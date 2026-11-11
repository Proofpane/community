"""The skills hook's settings: which hooks a claude of a given version is given (settings.skills_settings), each in
the background and from the build that has its event; how they join a claude terminal's own settings file at each start
(settings.with_map, workspace._with_map) and an owner's chat's settings after its start has read the claude's version
(mcp.with_hooks, workspace._chat_start, chat.Chat._connect); and who is given none (the controller's chat, an agent's
run)."""

import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from conftest import ROOT
from proofpane_workspace import chat as chats, mcp, settings, workspace as workspace_module
from skills_readme import stated
from proofpane_workspace.workspace import Workspace
from test_workspace_chat import _arg, _chat, _runs, _until, cagent  # noqa: F401  (cagent: a fixture)
from test_workspace_map_settings import PY, DIR, _first_start, _given, _less, _more, _start

SKILLS_HOOK = {"type": "command", "command": PY, "args": ["-E", "-s", "-X", "utf8", f"{DIR}/activity.py", "--skills"],
               "timeout": settings.ACTIVITY_TIMEOUT, "async": True}
EVENTS = ("PostToolUse", "PostToolUseFailure", "UserPromptExpansion")


def _expected(version):
    """The events a claude of `version` takes, by the dates the reading gave, each also needing a background hook."""
    given = set()
    if version >= max(settings.SKILL_CALLS_FROM, settings.ASYNC_HOOKS_FROM):
        given.add("PostToolUse")
    if version >= max(settings.SKILL_CALLS_FROM, settings.TOOL_FAILURES_FROM, settings.ASYNC_HOOKS_FROM):
        given.add("PostToolUseFailure")
    if version >= max(settings.SKILL_TYPED_FROM, settings.ASYNC_HOOKS_FROM):
        given.add("UserPromptExpansion")
    return given


def test_the_dates_are_the_ones_the_reading_gave():
    # as read in Claude Code's code and changelog: the `skill` field of a Skill call from 2.0.35, UserPromptExpansion
    # in the code from 2.1.116, PostToolUseFailure's first changelog naming 2.1.119, background hooks from 2.1.23
    assert settings.SKILL_CALLS_FROM == (2, 0, 35)
    assert settings.SKILL_TYPED_FROM == (2, 1, 116)
    assert settings.TOOL_FAILURES_FROM == (2, 1, 119)
    assert settings.ASYNC_HOOKS_FROM == (2, 1, 23)


def test_every_claude_the_workspace_starts_is_given_all_three():
    floor = workspace_module.CLAUDE_ARGS_FLOOR
    assert set(settings.skills_settings(PY, DIR, floor)["hooks"]) == set(EVENTS)
    assert _expected(floor) == set(EVENTS)


@pytest.mark.parametrize("version", [(2, 1, 139), (2, 1, 304), (3, 0, 0)])
def test_from_the_last_gate_every_hook_is_given_in_the_background(version):
    made = settings.skills_settings(PY, DIR, version)
    assert made == {"hooks": {
        "PostToolUse": [{"matcher": "Skill", "hooks": [SKILLS_HOOK]}],
        "PostToolUseFailure": [{"matcher": "Skill", "hooks": [SKILLS_HOOK]}],
        "UserPromptExpansion": [{"hooks": [SKILLS_HOOK]}]}}
    # a typed command's expansion is refused by a hook that exits 2 (Python ends 2 for a script that is not there):
    # none of them may hold or refuse a call or a command, so each is a background hook
    assert all(h["async"] is True for groups in made["hooks"].values() for g in groups for h in g["hooks"])


@pytest.mark.parametrize("version", sorted({
    (2, 0, 0), (2, 0, 34), (2, 0, 35), (2, 1, 22), (2, 1, 23), (2, 1, 24), _less(settings.SKILL_TYPED_FROM),
    settings.SKILL_TYPED_FROM, _more(settings.SKILL_TYPED_FROM), _less(settings.TOOL_FAILURES_FROM),
    settings.TOOL_FAILURES_FROM, _more(settings.TOOL_FAILURES_FROM), (1, 9, 9), (2, 1, 0)}))
def test_each_hook_is_given_from_its_own_version_and_not_before(version):
    # below, at and above each date: compared as numbers, never as text
    assert set(settings.skills_settings(PY, DIR, version).get("hooks", {})) == _expected(version)


def test_a_build_with_no_background_hook_is_given_none_of_them():
    for version in (_less(settings.ASYNC_HOOKS_FROM), (2, 0, 100), (1, 0, 0)):
        assert settings.skills_settings(PY, DIR, version) == {}
    assert settings.skills_settings(PY, DIR, settings.ASYNC_HOOKS_FROM)["hooks"].keys() == {"PostToolUse"}


@pytest.mark.parametrize("version", [None, "2.1.274", (2, 1), (2, 1, 274, 0), (2, 1, "x"), (2, 1, 274.0), [2, 1, 274],
                                     (2, True, 274), 0])
def test_a_version_not_read_is_given_nothing(version):
    assert settings.skills_settings(PY, DIR, version) == {}


def test_the_failure_hook_is_given_with_the_same_matcher_as_the_call_s():
    made = settings.skills_settings(PY, DIR, (2, 1, 300))["hooks"]
    assert made["PostToolUse"][0]["matcher"] == made["PostToolUseFailure"][0]["matcher"] == "Skill"
    assert "matcher" not in made["UserPromptExpansion"][0]


def test_the_workspace_wide_settings_never_carry_it():
    for managed in (True, False):
        made = settings.claude_settings(PY, DIR, managed)
        assert not any("--skills" in h.get("args", []) for groups in made["hooks"].values() for g in groups
                       for h in g["hooks"])


# how the hooks join a terminal's own settings


def test_with_map_gives_the_skills_hook_only_where_asked_and_after_what_is_there():
    base = settings.claude_settings(PY, DIR, managed=False)
    plain = settings.with_map(base, PY, DIR, (2, 1, 274))
    assert not any("--skills" in h.get("args", []) for groups in plain["hooks"].values() for g in groups
                   for h in g["hooks"])
    made = settings.with_map(base, PY, DIR, (2, 1, 274), skills=True)
    for event, groups in base["hooks"].items():
        assert made["hooks"][event][:len(groups)] == groups                 # the gate and the activity hooks first
    assert made["hooks"]["PostToolUse"][-1] == {"matcher": "Skill", "hooks": [SKILLS_HOOK]}
    assert made["hooks"]["PostToolUse"][-2] == {"matcher": "*", "hooks": [{
        "type": "command", "command": PY, "args": ["-E", "-s", "-X", "utf8", f"{DIR}/activity.py", "--map"],
        "timeout": settings.ACTIVITY_TIMEOUT, "async": True}]}            # the map's own hook, before it
    assert made["hooks"]["UserPromptExpansion"] == [{"hooks": [SKILLS_HOOK]}]
    assert made["hooks"]["PostToolUseFailure"][-1] == {"matcher": "Skill", "hooks": [SKILLS_HOOK]}


def test_with_map_with_the_controller_s_hooks_gives_the_skills_hook_after_them_too():
    made = settings.with_map({}, PY, DIR, (2, 1, 300), controller=True, skills=True)
    assert made["hooks"]["StopFailure"] and made["hooks"]["Notification"]
    assert made["hooks"]["PostToolUse"][-1] == {"matcher": "Skill", "hooks": [SKILLS_HOOK]}
    assert made["autoContinueAtUsageLimit"] is False


def test_with_map_on_nothing_given_is_the_map_s_and_the_skills_hook():
    got = settings.with_map({}, PY, DIR, (2, 1, 274), skills=True)
    want = settings.map_settings(PY, DIR, (2, 1, 274))
    for event, groups in settings.skills_settings(PY, DIR, (2, 1, 274))["hooks"].items():
        want["hooks"][event] = want["hooks"].get(event, []) + groups
    assert got == want


@pytest.mark.parametrize("given", [None, [], "x", {"hooks": []}, {"hooks": {"PostToolUse": {"matcher": "*"}}},
                                   {"hooks": {"UserPromptExpansion": "x"}}])
def test_with_map_refuses_what_it_cannot_add_the_skills_hook_to(given):
    assert settings.with_map(given, PY, DIR, (2, 1, 274), skills=True) is None


def test_with_map_at_a_version_not_read_adds_nothing_even_for_skills():
    assert settings.with_map({"x": 1}, PY, DIR, None, skills=True) == {"x": 1}


# how they join a chat's settings


def _text(**given):
    return json.dumps(dict(given))


def test_hooks_are_added_to_a_chat_s_settings_given_as_none_as_text_and_as_a_file(tmp_path):
    extra = settings.skills_settings(PY, DIR, (2, 1, 300))
    only = json.loads(mcp.with_hooks(None, extra))
    assert only == extra and json.loads(mcp.with_hooks("", extra)) == extra
    given = {"env": {"A": "1"}, "disabledMcpjsonServers": ["x"],
             "hooks": {"PostToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "gate"}]}],
                       "Stop": [{"hooks": [{"type": "command", "command": "idle"}]}]}}
    for value in (json.dumps(given), "  " + json.dumps(given)):
        got = json.loads(mcp.with_hooks(value, extra))
        assert got["env"] == given["env"] and got["disabledMcpjsonServers"] == ["x"]
        assert got["hooks"]["Stop"] == given["hooks"]["Stop"]
        assert got["hooks"]["PostToolUse"] == given["hooks"]["PostToolUse"] + extra["hooks"]["PostToolUse"]
        assert got["hooks"]["UserPromptExpansion"] == extra["hooks"]["UserPromptExpansion"]
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(given))
    assert json.loads(mcp.with_hooks(str(path), extra)) == json.loads(mcp.with_hooks(json.dumps(given), extra))
    assert json.loads(path.read_text()) == given                   # the file is read, never changed


@pytest.mark.parametrize("value", ["/no/such/file.json", "{not json", "[1]", '"x"', '{"hooks": []}',
                                   '{"hooks": {"PostToolUse": {"matcher": "*"}}}'])
def test_settings_that_cannot_be_added_to_are_given_as_they_were(value):
    extra = settings.skills_settings(PY, DIR, (2, 1, 300))
    assert mcp.with_hooks(value, extra) == value


@pytest.mark.parametrize("extra", [None, {}, {"hooks": {}}, {"hooks": None}, {"hooks": []}, "x"])
def test_no_hooks_to_add_leaves_the_settings_as_they_were(extra):
    assert mcp.with_hooks('{"a": 1}', extra) == '{"a": 1}'
    assert mcp.with_hooks("/some/file", extra) == "/some/file"
    assert mcp.with_hooks(None, extra) is None


def test_a_directory_or_a_directory_named_as_settings_is_given_as_it_was(tmp_path):
    extra = settings.skills_settings(PY, DIR, (2, 1, 300))
    assert mcp.with_hooks(str(tmp_path), extra) == str(tmp_path)


def test_a_fifo_where_the_settings_file_was_is_given_as_it_was_and_never_waited_on(tmp_path):
    """A program of a session can replace the workspace's settings file with a FIFO: a read that waits for a writer would
    hold the agent's loop at the next chat start. The settings are given as they were, at once."""
    import threading
    extra = settings.skills_settings(PY, DIR, (2, 1, 300))
    fifo = tmp_path / "gate.json"
    os.mkfifo(fifo)
    got = []
    reader = threading.Thread(target=lambda: got.append(mcp.with_hooks(str(fifo), extra)), daemon=True)
    reader.start()
    reader.join(3)
    alive = reader.is_alive()
    if alive:                                           # a read waiting for a writer: let it go, then fail
        fd = os.open(fifo, os.O_WRONLY)
        os.close(fd)
        reader.join(3)
    assert not alive and got == [str(fifo)]


def test_a_fifo_holding_settings_is_never_read_as_them(tmp_path):
    """A program of a session can hold valid settings in a FIFO put where the file was: a FIFO is no settings file. (Run
    in a thread with a limit: a read that waits for the end of what a FIFO holds would otherwise hold this test.)"""
    import threading
    extra = settings.skills_settings(PY, DIR, (2, 1, 300))
    fifo = tmp_path / "gate.json"
    os.mkfifo(fifo)
    keep = os.open(fifo, os.O_RDWR)                     # a writer, so that a read finds what it wrote
    got = []
    try:
        os.write(keep, b'{"a": 1}')
        reader = threading.Thread(target=lambda: got.append(mcp.with_hooks(str(fifo), extra)), daemon=True)
        reader.start()
        reader.join(3)
        alive = reader.is_alive()
    finally:
        os.close(keep)                                  # lets a read that waits for the end of the file go
    reader.join(3)
    assert not alive and got == [str(fifo)]


def test_a_settings_file_is_read_to_one_byte_past_the_most_and_no_farther(tmp_path, monkeypatch):
    """What is read of a settings file is bounded by what it is asked for, not by what the file holds: a program of a
    session can make the file any size, and a read of all of it holds all of it in memory before the size is looked at
    (a read with no bound, which no test of the answer can tell from the code)."""
    asked, real = [], os.fdopen

    class Spy:
        def __init__(self, fh):
            self.fh = fh

        def __enter__(self):
            self.fh.__enter__()
            return self

        def __exit__(self, *given):
            return self.fh.__exit__(*given)

        def read(self, n=-1):
            asked.append(n)
            return self.fh.read(n)

    monkeypatch.setattr(mcp, "MAX_FILE", 10)
    monkeypatch.setattr(mcp.os, "fdopen", lambda fd, *a, **kw: Spy(real(fd, *a, **kw)))
    small = tmp_path / "small.json"
    small.write_text('{"a": 1}')
    assert mcp._read_json(str(small)) == {"a": 1} and asked == [11]
    asked.clear()
    exact = tmp_path / "exact.json"
    exact.write_text('{"a": 123}')                                      # ten bytes: exactly the most
    assert exact.stat().st_size == 10 and mcp._read_json(str(exact)) == {"a": 123} and asked == [11]
    asked.clear()
    over = tmp_path / "over.json"
    over.write_text('{"a": 1234}')
    with pytest.raises(ValueError):
        mcp._read_json(str(over))
    assert asked == [11]                                                # one byte past the most, and no more


def test_a_settings_file_past_the_most_is_given_as_it_was(tmp_path):
    extra = settings.skills_settings(PY, DIR, (2, 1, 300))
    big = tmp_path / "big.json"
    big.write_text('{"a": "' + "x" * (mcp.MAX_FILE + 10) + '"}')
    assert mcp.with_hooks(str(big), extra) == str(big)
    padded = tmp_path / "padded.json"
    padded.write_text('{"a": 1}' + " " * (mcp.MAX_FILE + 10))          # whole JSON within the most, then past it
    assert mcp.with_hooks(str(padded), extra) == str(padded)
    exact = tmp_path / "exact.json"
    exact.write_text('{"a": "' + "x" * (mcp.MERGED_MOST - 10_000) + '"}')
    assert json.loads(mcp.with_hooks(str(exact), extra))["hooks"] == extra["hooks"]     # within the most: read and merged


def test_merged_settings_that_are_too_large_to_be_one_argument_are_not_given_and_the_value_is_as_it_was(tmp_path):
    """Linux refuses one argument of 128 KB: a settings file inside what is read (1 MB) whose merged text would be past
    MERGED_MOST bytes is given as the path it was, so the chat starts, without the hooks."""
    extra = settings.skills_settings(PY, DIR, (2, 1, 300))

    def merged_of(filler):
        path = tmp_path / f"s{filler}.json"
        path.write_text(json.dumps({"env": {"X": "x" * filler}}))
        return str(path), mcp.with_hooks(str(path), extra)

    path, base = merged_of(0)
    assert base != path                                         # a small file is merged
    room = mcp.MERGED_MOST - len(base.encode("utf-8"))
    path, at = merged_of(room)                                  # the merged text is exactly MERGED_MOST bytes
    assert len(at.encode("utf-8")) == mcp.MERGED_MOST and json.loads(at)["hooks"] == extra["hooks"]
    path, over = merged_of(room + 1)                            # one byte past: given as it was
    assert over == path
    big = tmp_path / "big2.json"
    big.write_text(json.dumps({"env": {"X": "x" * 200_000}}))   # the case measured: 200 KB of settings, read, not given
    assert mcp.with_hooks(str(big), extra) == str(big)
    text = json.dumps({"env": {"X": "é" * 60_000}}, ensure_ascii=False)     # bytes, not characters: 120,000 of them
    assert len(text) < mcp.MERGED_MOST < len(text.encode("utf-8"))
    assert mcp.with_hooks(text, extra) == text                  # JSON text given as it was past the most too


@pytest.mark.skipif(sys.platform != "linux", reason="one argument of 128 KB is refused by Linux")
def test_the_most_the_merged_settings_may_be_is_an_argument_the_system_starts_a_program_with():
    """The merged settings are one argument of the start, which Linux refuses at 128 KB (131,072 bytes).
    The bound is one a program is started with, and the control is that the system's own limit is refused."""
    true = shutil.which("true")
    assert subprocess.run([true, "x" * mcp.MERGED_MOST]).returncode == 0
    with pytest.raises(OSError):
        subprocess.run([true, "x" * 131072])


def test_each_skills_hook_runs_as_a_program_the_way_its_settings_give_it(tmp_path):
    """The hook as `skills_settings` gives it (the interpreter, its isolating flags and arguments, no shell, the
    environment a hook has), once for each of its three events: a line is written for each. Every other test of the hook
    starts activity.py itself, so without this one a change to the settings' command or flags would leave them all green
    while no line was written (the map has the same test for its own hooks)."""
    directory = os.path.join(ROOT, "proofpane_workspace")
    made = settings.skills_settings(sys.executable, directory, (2, 1, 304))["hooks"]
    skill = "proofpane-core:ai-risk-assessment"
    events = {
        "PostToolUse": {"hook_event_name": "PostToolUse", "tool_name": "Skill", "tool_input": {"skill": skill}},
        "PostToolUseFailure": {"hook_event_name": "PostToolUseFailure", "tool_name": "Skill", "tool_input": {"skill": skill}},
        "UserPromptExpansion": {"hook_event_name": "UserPromptExpansion", "expansion_type": "slash_command",
                                "command_name": skill}}
    assert set(made) == set(events)
    for name, event in events.items():
        (hook,) = [h for group in made[name] for h in group["hooks"]]
        done = subprocess.run([hook["command"], *hook["args"]], input=json.dumps(event), capture_output=True, text=True,
                              env={"PATH": os.environ["PATH"], "HOME": str(tmp_path), "PP_SESSION_DIR": str(tmp_path)},
                              timeout=30)
        assert done.returncode == 0 and done.stdout == "", name
    lines = [json.loads(line) for line in (tmp_path / "skills.jsonl").read_text().splitlines()]
    assert [(r["skill"], r["kind"], r["path"], r["failed"]) for r in lines] == [
        (skill, "core", "claude", False), (skill, "core", "claude", True), (skill, "core", "typed", None)]


# what a chat's start does with them


def _chat_for(ws, tmp_path, sid="0123456789ab"):
    c = chats.Chat(sid, str(tmp_path / "s"), str(tmp_path), {}, ws._chat_wrapper)
    return c


@pytest.fixture
def ws(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    w = Workspace(str(tmp_path / "ws"))
    w._chat_wrapper = str(tmp_path / "chat-claude")
    monkeypatch.setattr(w, "_claude", lambda: "/no/such/claude")
    monkeypatch.setattr(w, "claude_floor_refusal", lambda claude, cwd, env: None)
    return w


def test_an_owner_s_chat_start_takes_the_version_its_check_read_and_gives_the_hook_for_it(ws, tmp_path):
    version = (2, 1, 274)
    ws._read_versions[("/no/such/claude", str(tmp_path))] = (version, time.monotonic())
    c = _chat_for(ws, tmp_path)
    assert ws._chat_start(c) is None
    assert c.skills == settings.skills_settings(sys.executable, os.path.join(ROOT, "proofpane_workspace"), version)
    assert set(c.skills["hooks"]) == set(EVENTS)
    assert ("/no/such/claude", str(tmp_path)) not in ws._read_versions      # taken, as a terminal's start takes its own


def test_a_version_read_for_another_folder_is_not_taken_for_this_chat(ws, tmp_path):
    ws._read_versions[("/no/such/claude", str(tmp_path / "elsewhere"))] = ((2, 1, 274), time.monotonic())
    c = _chat_for(ws, tmp_path)
    assert ws._chat_start(c) is None and c.skills == {}
    assert ("/no/such/claude", str(tmp_path / "elsewhere")) in ws._read_versions


def test_a_version_read_long_ago_is_not_taken(ws, tmp_path):
    ws._read_versions[("/no/such/claude", str(tmp_path))] = ((2, 1, 274),
                                                              time.monotonic() - workspace_module.READ_VERSION_FOR - 1)
    c = _chat_for(ws, tmp_path)
    assert ws._chat_start(c) is None and c.skills == {}


def test_no_version_read_gives_no_hook(ws, tmp_path):
    c = _chat_for(ws, tmp_path)
    assert ws._chat_start(c) is None and c.skills == {}


def test_an_agent_s_run_is_given_no_skills_hook(ws, tmp_path):
    ws._read_versions[("/no/such/claude", str(tmp_path))] = ((2, 1, 274), time.monotonic())
    c = _chat_for(ws, tmp_path)
    c.is_run = True
    assert ws._chat_start(c) is None and c.skills == {}


def test_the_controller_s_chat_is_given_no_skills_hook(ws, tmp_path, monkeypatch):
    monkeypatch.setattr(ws.controller, "is_chat", lambda sid: True)
    monkeypatch.setattr(ws.controller, "start_refusal", lambda sid: None)
    ws._read_versions[("/no/such/claude", str(tmp_path))] = ((2, 1, 274), time.monotonic())
    c = _chat_for(ws, tmp_path)
    assert ws._chat_start(c) is None and c.skills == {}


def test_a_task_s_attempt_is_given_no_skills_hook(tmp_path):
    """A control that guards the manual's "no task's attempt (counted nowhere)": an attempt's chat makes its own options
    (task_attempt.AttemptChat._connect, which never reaches the chat's with_hooks), so whatever its `skills` hold, the
    settings its claude is given are the attempt's own."""
    pytest.importorskip("claude_agent_sdk")
    import asyncio
    from types import SimpleNamespace

    from proofpane_workspace import task_attempt

    seen = {}

    class Options:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class Client:
        def __init__(self, options):
            self.options = options

        async def connect(self):
            seen["settings"] = self.options.settings
            raise RuntimeError("stop here")

    fake = type(sys)("claude_agent_sdk")
    fake.ClaudeAgentOptions, fake.ClaudeSDKClient = Options, Client
    saved = sys.modules.get("claude_agent_sdk")
    sys.modules["claude_agent_sdk"] = fake
    attempt = SimpleNamespace(
        spec={"run_id": "0123456789ab", "records": str(tmp_path), "work": str(tmp_path), "env": {}, "model": None,
              "effort": None, "permission_mode": "default"},
        _turn_end=None, _chat_state=None, _rate_limit=None, _message=None, _closed=None,
        _options=lambda chat: {"settings": '{"attempt": "its own"}'})
    try:
        c = task_attempt.AttemptChat(attempt)
        c.skills = settings.skills_settings(PY, DIR, (2, 1, 300))
        assert c.skills["hooks"]                                         # there is a hook to give, and it is not given
        with pytest.raises(chats.ChatError):
            asyncio.run(c._connect(checked=True))
    finally:
        if saved is not None:
            sys.modules["claude_agent_sdk"] = saved
        else:
            del sys.modules["claude_agent_sdk"]
    assert seen["settings"] == '{"attempt": "its own"}'


def test_the_hook_is_given_to_the_chat_s_settings_after_its_start_has_answered(tmp_path):
    # Chat._connect: the SDK's options are made before before_start reads the version, so the hook is set after it
    pytest.importorskip("claude_agent_sdk")
    import asyncio

    seen = {}

    class Options:
        def __init__(self, **kw):
            self.__dict__.update(kw)
            seen["at_creation"] = kw.get("settings")

    class Client:
        def __init__(self, options):
            self.options = options

        async def connect(self):
            seen["at_connect"] = self.options.settings
            raise RuntimeError("stop here")

    fake = type(sys)("claude_agent_sdk")
    fake.ClaudeAgentOptions, fake.ClaudeSDKClient = Options, Client
    saved = sys.modules.get("claude_agent_sdk")
    sys.modules["claude_agent_sdk"] = fake
    try:
        c = chats.Chat("0123456789ab", str(tmp_path), str(tmp_path), {}, "/no/claude", settings=None,
                       before_start=lambda: None)
        c.skills = settings.skills_settings(PY, DIR, (2, 1, 300))
        with pytest.raises(chats.ChatError):
            asyncio.run(c._connect())
    finally:
        if saved is not None:
            sys.modules["claude_agent_sdk"] = saved
        else:
            del sys.modules["claude_agent_sdk"]
    assert seen["at_creation"] is None                                  # not there when the options were made
    assert json.loads(seen["at_connect"]) == c.skills                  # there when the SDK connects


def test_a_refused_start_gives_no_hook_and_a_chat_with_none_leaves_its_settings_alone(tmp_path):
    pytest.importorskip("claude_agent_sdk")
    import asyncio

    seen = {}

    class Options:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class Client:
        def __init__(self, options):
            self.options = options

        async def connect(self):
            seen["settings"] = self.options.settings
            raise RuntimeError("stop here")

    fake = type(sys)("claude_agent_sdk")
    fake.ClaudeAgentOptions, fake.ClaudeSDKClient = Options, Client
    saved = sys.modules.get("claude_agent_sdk")
    sys.modules["claude_agent_sdk"] = fake
    try:
        c = chats.Chat("0123456789ab", str(tmp_path), str(tmp_path), {}, "/no/claude", settings="/some/settings.json",
                       before_start=lambda: None)
        with pytest.raises(chats.ChatError):
            asyncio.run(c._connect())
        assert seen["settings"] == "/some/settings.json"                # no skills: the value as it was
    finally:
        if saved is not None:
            sys.modules["claude_agent_sdk"] = saved
        else:
            del sys.modules["claude_agent_sdk"]


# through a real agent and a stand-in claude


@pytest.mark.skipif(not chats.available(), reason="needs the Claude Agent SDK (requirements.txt)")
def test_a_chat_s_claude_is_started_with_the_skills_hook_in_its_settings(cagent):  # noqa: F811
    s = _chat(cagent)
    _until(cagent, s["id"], lambda log: log["state"] == "idle")
    run = _runs(cagent)[-1]
    given = json.loads(_arg(run, "--settings"))
    assert set(given["hooks"]) == set(EVENTS)
    assert given["hooks"]["PostToolUse"][0]["matcher"] == "Skill"
    assert given["hooks"]["PostToolUse"][0]["hooks"][0]["args"][-1] == "--skills"
    # and again when it is stopped and its next message starts it again: the hook is read at each start
    assert cagent.http.post(f"/sessions/{s['id']}/stop").status_code == 200
    seq = cagent.http.get(f"/sessions/{s['id']}/chat").json()["seq"]
    assert cagent.http.post(f"/sessions/{s['id']}/chat", json={"text": "more"}).status_code == 200
    _until(cagent, s["id"], lambda log: log["state"] == "idle" and log["seq"] > seq)
    again = _runs(cagent)[-1]
    assert again is not run and set(json.loads(_arg(again, "--settings"))["hooks"]) == set(EVENTS)


# a claude terminal's own settings file


@pytest.mark.parametrize("local", [False, True])
def test_a_terminal_start_gives_the_skills_hook_in_the_session_s_own_settings(tmp_path, local):
    a, _, gate = _start(tmp_path, "2.1.274", local)
    try:
        sid, folder = _first_start(a, tmp_path)
        argv, path = _given(folder)
        assert path == str(folder / "map-settings.json")
        made = json.loads(open(path).read())
        skills = {e: [g for g in groups if any("--skills" in h.get("args", []) for h in g["hooks"])]
                  for e, groups in made["hooks"].items()}
        assert {e for e, g in skills.items() if g} == set(EVENTS)
        assert skills["PostToolUse"][0]["matcher"] == "Skill" and "matcher" not in skills["UserPromptExpansion"][0]
        if local:
            base = json.loads(gate.read_text())
            for event, groups in base["hooks"].items():
                assert made["hooks"][event][:len(groups)] == groups     # the gate first, still
    finally:
        a.close()


def test_a_terminal_start_at_the_floor_is_given_all_three(tmp_path):
    version = ".".join(str(n) for n in workspace_module.CLAUDE_ARGS_FLOOR)
    a, _, _ = _start(tmp_path, version)
    try:
        sid, folder = _first_start(a, tmp_path)
        made = json.loads((folder / "map-settings.json").read_text())
        have = {e for e, groups in made["hooks"].items()
                if any("--skills" in h.get("args", []) for g in groups for h in g["hooks"])}
        assert have == set(EVENTS)
    finally:
        a.close()


def test_the_numbers_the_manual_states_of_a_chat_s_merged_settings_are_the_code_s():
    """The manual says a chat whose settings would pass 100 KB with the hook in them is given them as they were (Linux
    refuses one argument of 128 KB): the numbers are read back from the code, as a bound a test holds only relative to
    itself can move by a byte or by a megabyte."""
    assert stated(r"would pass ([0-9]+) KB with the hook") * 1024 == mcp.MERGED_MOST
    assert mcp.MERGED_MOST < stated(r"Linux refuses one argument of ([0-9]+) KB") * 1024
