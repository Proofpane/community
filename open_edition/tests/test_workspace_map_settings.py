"""The control window's map sources: the sub-agent status line and the activity hook on each tool call and each
sub-agent's start and end, given to a claude terminal at each start, only where the version that start read is known to
take each (settings.map_settings), in a settings file of the session's own that holds what its --settings held besides
(workspace._with_map). The workspace-wide settings (the image's managed ones, the local provider's file) never carry
them: they are written once, for whatever claude a session later runs."""

import json
import os
import stat
import subprocess
import sys
import time

import pytest

from conftest import ROOT
from proofpane_workspace import settings
from workspace_fixtures import Agent, make_repo

PY, DIR = "/usr/bin/python3", "/opt/proofpane/proofpane_workspace"
STATUS_LINE = {"type": "command", "command": f"{PY} -E -s -X utf8 {DIR}/subagentline.py"}


def _map_hook(background=True):
    hook = {"type": "command", "command": PY, "args": ["-E", "-s", "-X", "utf8", f"{DIR}/activity.py", "--map"],
            "timeout": settings.ACTIVITY_TIMEOUT}
    return dict(hook, **{"async": True}) if background else hook


def _less(version):
    major, minor, patch = version
    return (major, minor, patch - 1) if patch else (major, minor - 1, 999)


def _more(version):
    return (version[0], version[1], version[2] + 1)


@pytest.mark.parametrize("version", [settings.SUBAGENT_STATUS_LINE_FROM, _more(settings.SUBAGENT_STATUS_LINE_FROM),
                                     (3, 0, 0)])
def test_from_the_status_line_s_version_every_part_is_given(version):
    made = settings.map_settings(PY, DIR, version)
    assert made == {"subagentStatusLine": STATUS_LINE,
                    "hooks": {"PostToolUse": [{"matcher": "*", "hooks": [_map_hook()]}],
                              "PostToolUseFailure": [{"matcher": "*", "hooks": [_map_hook()]}],
                              "SubagentStart": [{"hooks": [_map_hook(background=False)]}],
                              "SubagentStop": [{"hooks": [_map_hook()]}]}}


@pytest.mark.parametrize("name, key", [
    ("SUBAGENT_STATUS_LINE_FROM", "subagentStatusLine"),
    ("TOOL_FAILURES_FROM", "PostToolUseFailure"),
    ("TOOL_CALLS_FROM", "PostToolUse"),
    ("SUBAGENT_HOOKS_FROM", "SubagentStart"),
    ("SUBAGENT_HOOKS_FROM", "SubagentStop"),
])
def test_each_part_is_given_from_its_own_version_and_not_before(name, key):
    # below, at and above each version: compared as numbers, never as text ("2.1.99" sorts after "2.1.214" as text)
    at = getattr(settings, name)

    def has(version):
        made = settings.map_settings(PY, DIR, version)
        return key in made or key in made.get("hooks", {})
    assert not has(_less(at)) and has(at) and has(_more(at)) and has((at[0], at[1] + 1, 0))
    # a version that sorts after it as text and before it as a number: 2.1.3 against 2.1.214
    text_after = (at[0], at[1], int(str(at[2])[0]) + 1)
    assert text_after < at and ".".join(map(str, text_after)) > ".".join(map(str, at))
    assert not has(text_after) and not has((at[0], at[1] - 1, 999)) if at[1] else not has(text_after)


def test_the_versions_are_the_changelog_s_first_naming():
    assert settings.SUBAGENT_STATUS_LINE_FROM == (2, 1, 214)
    assert settings.SUBAGENT_HOOKS_FROM == (2, 0, 43)
    assert settings.TOOL_CALLS_FROM == (2, 1, 69)
    assert settings.TOOL_FAILURES_FROM == (2, 1, 119)
    assert settings.ASYNC_HOOKS_FROM == (2, 1, 23)


def test_the_map_s_hooks_run_in_the_background_from_the_version_that_takes_it():
    # async: they never hold a turn, and a missing script's exit 2 never keeps a sub-agent from stopping
    at = settings.ASYNC_HOOKS_FROM
    for version, background in ((_less(at), False), (at, True), (_more(at), True), ((2, 1, 274), True)):
        made = settings.map_settings(PY, DIR, version)
        hooks = [h for event, groups in made.get("hooks", {}).items() if event != "SubagentStart"
                 for g in groups for h in g["hooks"]]
        assert hooks and all(h.get("async", False) is background for h in hooks), (version, hooks)


def test_a_sub_agent_s_start_is_written_before_it_runs():
    # background hooks are not ordered: a short sub-agent's stop could be written before its start, leaving it running
    # Claude Code waits for a SubagentStart hook, whose exit 2 cannot block the sub-agent
    for version in (settings.ASYNC_HOOKS_FROM, (2, 1, 274), (3, 0, 0)):
        (group,) = settings.map_settings(PY, DIR, version)["hooks"]["SubagentStart"]
        assert [h.get("async", False) for h in group["hooks"]] == [False]
        assert all(h["timeout"] == settings.ACTIVITY_TIMEOUT for h in group["hooks"])


@pytest.mark.parametrize("version", [None, "2.1.274", (2, 1), (2, 1, 274, 0), (2, "1", 274), [2, 1, 274], (2, 1, True)])
def test_a_version_not_known_gives_nothing(version):
    assert settings.map_settings(PY, DIR, version) == {}


def test_a_version_between_the_floor_and_the_status_line_gets_the_hooks_alone():
    made = settings.map_settings(PY, DIR, (2, 1, 213))
    assert "subagentStatusLine" not in made
    assert set(made["hooks"]) == {"PostToolUse", "PostToolUseFailure", "SubagentStart", "SubagentStop"}


def test_the_workspace_wide_settings_never_carry_them():
    # managed and not: the image's and the local provider's, written once for whatever claude later runs
    for managed in (True, False):
        made = settings.claude_settings(PY, DIR, managed)
        assert "subagentStatusLine" not in made
        assert set(made["hooks"]) == {"PreToolUse", "UserPromptSubmit", "PostToolUse", "Notification", "Stop"}
        assert not any("--map" in h.get("args", []) for groups in made["hooks"].values() for g in groups
                       for h in g["hooks"])


def test_with_map_keeps_what_was_given_and_adds_after_it():
    base = settings.claude_settings(PY, DIR, managed=False)
    before = json.loads(json.dumps(base))
    made = settings.with_map(base, PY, DIR, (2, 1, 274))
    assert base == before                                           # the given settings are not changed in place
    for key in base:
        if key != "hooks":
            assert made[key] == base[key]
    assert made["subagentStatusLine"] == STATUS_LINE
    for event, groups in base["hooks"].items():
        assert made["hooks"][event][:len(groups)] == groups         # the gate and the activity hooks first, unchanged
    assert made["hooks"]["PostToolUse"][1:] == [{"matcher": "*", "hooks": [_map_hook()]}]
    assert made["hooks"]["SubagentStart"] == [{"hooks": [_map_hook(background=False)]}]


def test_with_map_on_nothing_given_is_the_map_s_alone():
    assert settings.with_map({}, PY, DIR, (2, 1, 274)) == settings.map_settings(PY, DIR, (2, 1, 274))


@pytest.mark.parametrize("given", [None, [], "x", {"hooks": []}, {"hooks": {"PostToolUse": {"matcher": "*"}}}])
def test_with_map_refuses_what_it_cannot_add_to(given):
    assert settings.with_map(given, PY, DIR, (2, 1, 274)) is None


def test_each_map_hook_runs_as_a_program_and_writes_the_call(tmp_path):
    # the real activity hook, given as the settings give it: no shell, its arguments, the call written
    directory = os.path.join(ROOT, "proofpane_workspace")
    made = settings.map_settings(sys.executable, directory, (2, 1, 274))
    hook = made["hooks"]["PostToolUse"][0]["hooks"][0]
    event = {"hook_event_name": "PostToolUse", "tool_name": "Read", "tool_input": {"file_path": "/w/a.py"}}
    done = subprocess.run([hook["command"], *hook["args"]], input=json.dumps(event), capture_output=True, text=True,
                          env={"PATH": os.environ["PATH"], "HOME": str(tmp_path), "PP_SESSION_DIR": str(tmp_path)},
                          timeout=30)
    assert done.returncode == 0 and done.stdout == ""
    line = json.loads((tmp_path / "calls.jsonl").read_text())
    assert {k: line[k] for k in ("agent", "tool", "target", "failed")} == {"agent": None, "tool": "Read",
                                                                          "target": "/w/a.py", "failed": False}


# a stand-in claude that answers --version with the version in VERSION beside it, writes the arguments it was started
# with, and, given a --settings file, runs what a real one would: the sub-agent status line with the agents in
# AGENTS beside it, and each map hook of the file with each event in EVENTS beside it
STAND_IN = r'''#!{python}
import json, os, subprocess, sys, time
here = os.path.dirname(os.path.abspath(__file__))
if sys.argv[1:2] in (["-v"], ["--version"]):
    print(open(os.path.join(here, "VERSION")).read().strip() + " (Claude Code)")
    sys.exit(0)
folder = os.environ.get("PP_SESSION_DIR", "")
starts = os.path.join(folder, "starts.jsonl")
with open(starts, "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\n")
try:
    made = json.load(open(sys.argv[sys.argv.index("--settings") + 1])) if "--settings" in sys.argv else None
except ValueError:
    made = None
if isinstance(made, dict):
    line = (made.get("subagentStatusLine") or {}).get("command")
    agents = os.path.join(here, "AGENTS")
    if line and os.path.exists(agents):
        subprocess.run(line, shell=True, input=open(agents).read(), capture_output=True, text=True, timeout=30)
    events = os.path.join(here, "EVENTS")
    if os.path.exists(events):
        for event in json.load(open(events)):
            for group in (made.get("hooks") or {}).get(event["hook_event_name"], []):
                for hook in group["hooks"]:
                    if "--map" in hook.get("args", []):
                        subprocess.run([hook["command"], *hook["args"]], input=json.dumps(event), text=True,
                                       capture_output=True, timeout=30)
with open(os.path.join(folder, "done"), "w") as fh:
    fh.write("1")
time.sleep(600)
'''


def stand_in(bin_dir, version):
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "claude"
    fake.write_text(STAND_IN.replace("{python}", sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    (bin_dir / "VERSION").write_text(version)
    return fake


def wait_for(path, timeout=20):
    deadline = time.time() + timeout
    while not os.path.exists(path):
        if time.time() > deadline:
            raise AssertionError(f"{path} did not appear")
        time.sleep(0.05)


def _start(tmp_path, version, local=False):
    bin_dir, home = tmp_path / "bin", tmp_path / "home"
    home.mkdir()
    stand_in(bin_dir, version)
    extra = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
    gate = None
    if local:
        gate = tmp_path / "gate-settings.json"
        gate.write_text(json.dumps(settings.claude_settings(sys.executable, os.path.join(ROOT, "proofpane_workspace"),
                                                            managed=False)))
        extra["PP_WORKSPACE_CLAUDE_SETTINGS"] = str(gate)
    a = Agent(str(tmp_path / "ws"), str(home), extra)
    make_repo(str(tmp_path / "ws" / "repos" / "demo"))
    return a, bin_dir, gate


def _first_start(a, tmp_path):
    r = a.http.post("/sessions", json={"repo": "demo", "program": "claude", "task": "Map it"})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    folder = tmp_path / "ws" / "sessions" / sid
    wait_for(folder / "done")
    return sid, folder


def _given(folder):
    """The arguments the stand-in was last started with, and the settings file they named."""
    argv = json.loads((folder / "starts.jsonl").read_text().splitlines()[-1])
    path = argv[argv.index("--settings") + 1] if "--settings" in argv else None
    return argv, path


@pytest.mark.parametrize("version, line", [("2.1.213", False), ("2.1.214", True), ("2.1.274", True)])
@pytest.mark.parametrize("local", [False, True])
def test_a_terminal_start_gives_the_parts_its_claude_takes(tmp_path, version, line, local):
    a, _, gate = _start(tmp_path, version, local)
    try:
        sid, folder = _first_start(a, tmp_path)
        argv, path = _given(folder)
        assert path == str(folder / "map-settings.json")
        assert argv.count("--settings") == 1
        made = json.loads(open(path).read())
        assert ("subagentStatusLine" in made) is line
        assert set(made["hooks"]) >= {"PostToolUse", "PostToolUseFailure", "SubagentStart", "SubagentStop"}
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        if local:
            # the local provider's gate is still in the file the session runs with, first on every tool call
            base = json.loads(gate.read_text())
            for key in base:
                if key != "hooks":
                    assert made[key] == base[key]
            for event, groups in base["hooks"].items():
                assert made["hooks"][event][:len(groups)] == groups
        else:
            # a workspace machine's gates are its managed settings: the file holds the map's parts alone
            assert "PreToolUse" not in made["hooks"] and "env" not in made
    finally:
        a.close()


def test_a_start_after_an_update_gets_what_the_new_claude_takes(tmp_path):
    a, bin_dir, _ = _start(tmp_path, "2.1.213")
    try:
        sid, folder = _first_start(a, tmp_path)
        assert "subagentStatusLine" not in json.loads((folder / "map-settings.json").read_text())
        assert a.http.post(f"/sessions/{sid}/stop").status_code == 200
        (bin_dir / "VERSION").write_text("2.1.290")
        os.unlink(folder / "done")
        assert a.http.post(f"/sessions/{sid}/start", json={}).status_code == 200
        wait_for(folder / "done")
        assert json.loads((folder / "map-settings.json").read_text())["subagentStatusLine"]["command"].endswith(
            "subagentline.py")
        (bin_dir / "VERSION").write_text("2.1.213")
        assert a.http.post(f"/sessions/{sid}/stop").status_code == 200
        os.unlink(folder / "done")
        assert a.http.post(f"/sessions/{sid}/start", json={}).status_code == 200
        wait_for(folder / "done")
        # and an older one, at the next start, nothing it would refuse
        assert "subagentStatusLine" not in json.loads((folder / "map-settings.json").read_text())
    finally:
        a.close()


def test_a_start_whose_settings_cannot_be_read_gives_nothing_and_keeps_its_own(tmp_path):
    a, _, gate = _start(tmp_path, "2.1.274", local=True)
    try:
        gate.write_text("{not json")
        sid, folder = _first_start(a, tmp_path)
        argv, path = _given(folder)
        # never a file that holds less than the settings it was given: the session is started with the same file
        assert path == str(gate) and not (folder / "map-settings.json").exists()
    finally:
        a.close()


def test_a_start_whose_settings_hold_a_number_json_has_no_name_for_keeps_its_own(tmp_path):
    # 1e400 is a number Python reads as infinity, which it would write back as Infinity, no JSON a strict reader takes:
    # the session's file would hold less than the operator's, the gate included
    a, _, gate = _start(tmp_path, "2.1.274", local=True)
    try:
        gate.write_text(gate.read_text()[:-1] + ', "cleanupPeriodDays": 1e400}')
        sid, folder = _first_start(a, tmp_path)
        argv, path = _given(folder)
        assert path == str(gate) and not (folder / "map-settings.json").exists()
    finally:
        a.close()


def test_the_version_read_for_one_start_is_not_used_by_another(tmp_path, monkeypatch):
    # the version is the floor's read for this start: taken once, at its launch; a second launch with no read of its
    # own gives nothing
    from proofpane_workspace.workspace import Workspace as Space
    home, root = tmp_path / "home", tmp_path / "ws"
    home.mkdir()
    root.mkdir()
    monkeypatch.setenv("HOME", str(home))
    ws = Space(str(root))
    fake = stand_in(tmp_path / "bin", "2.1.274")
    folder = tmp_path / "s"
    folder.mkdir()
    ws._dir = lambda sid: str(folder)
    ws._read_versions[(str(fake), str(tmp_path))] = ((2, 1, 274), time.monotonic())
    first = ws._with_map("a" * 12, [str(fake)], str(tmp_path))
    assert first == [str(fake), "--settings", str(folder / "map-settings.json")]
    assert ws._read_versions == {}
    assert ws._with_map("a" * 12, [str(fake)], str(tmp_path)) == [str(fake)]
    assert not (folder / "map-settings.json").exists()


def test_the_floor_s_read_is_kept_for_the_start_it_is_read_for(tmp_path, monkeypatch):
    from proofpane_workspace.workspace import Workspace as Space
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    ws = Space(str(root))
    fake = stand_in(tmp_path / "bin", "2.1.274")
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path)}
    assert ws.claude_floor_refusal(str(fake), str(tmp_path), env) is None
    assert list(ws._read_versions) == [(str(fake), str(tmp_path))]
    assert ws._read_versions[(str(fake), str(tmp_path))][0] == (2, 1, 274)
    (tmp_path / "bin" / "VERSION").write_text("not a version")
    assert ws.claude_floor_refusal(str(fake), str(root), env) is not None
    assert (str(fake), str(root)) not in ws._read_versions


@pytest.mark.parametrize("local", [False, True])
def test_a_session_on_another_provider_s_model_gets_the_map_beside_its_key_helper(tmp_path, local):
    # the map's file is made first, from the settings the start gives; the provider's file, the one the session runs
    # with, is made from it with the key helper and the address added (workspace._on_provider)
    a, _, gate = _start(tmp_path, "2.1.274", local)
    try:
        key = "sk-test-" + "k" * 24
        assert a.http.put("/model-providers/kimi", json={"key": key}).status_code == 200
        r = a.http.post("/sessions", json={"repo": "demo", "program": "claude", "task": "Map it", "model_provider": "kimi"})
        assert r.status_code == 200, r.text
        folder = tmp_path / "ws" / "sessions" / r.json()["id"]
        wait_for(folder / "done")
        argv, path = _given(folder)
        assert path == str(folder / "model-settings.json") and argv.count("--settings") == 1
        made = json.loads(open(path).read())
        mapped = json.loads((folder / "map-settings.json").read_text())
        assert made["subagentStatusLine"] == mapped["subagentStatusLine"]
        assert made["hooks"] == mapped["hooks"] and "apiKeyHelper" in made and "apiKeyHelper" not in mapped
        assert key not in json.dumps(made) and key not in json.dumps(mapped)
        if local:
            assert made["hooks"]["PreToolUse"] == json.loads(gate.read_text())["hooks"]["PreToolUse"]
    finally:
        a.close()


def test_a_command_that_names_the_map_s_file_already_keeps_it(tmp_path, monkeypatch):
    # a start's command given the map's file (prepare's) is never given it again, nor the file taken from under it
    from proofpane_workspace.workspace import Workspace as Space
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    ws = Space(str(root))
    fake = stand_in(tmp_path / "bin", "2.1.274")
    folder = tmp_path / "s"
    folder.mkdir()
    ws._dir = lambda sid: str(folder)
    ws._read_versions[(str(fake), str(tmp_path))] = ((2, 1, 274), time.monotonic())
    argv = ws._with_map("a" * 12, [str(fake)], str(tmp_path))
    kept = (folder / "map-settings.json").read_text()
    assert ws._with_map("a" * 12, argv, str(tmp_path)) == argv          # no version read for it: still there
    assert (folder / "map-settings.json").read_text() == kept


@pytest.mark.parametrize("planted", ["map-settings.json.tmp", "map-settings.json"])
def test_a_start_whose_map_file_cannot_be_written_goes_on_as_it_was(tmp_path, monkeypatch, planted):
    # a full disk, or a folder the session's programs put where the file goes: the start is not refused for the map
    from proofpane_workspace.workspace import Workspace as Space
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    ws = Space(str(root))
    fake = stand_in(tmp_path / "bin", "2.1.274")
    folder = tmp_path / "s"
    (folder / planted).mkdir(parents=True)
    ws._dir = lambda sid: str(folder)
    ws._read_versions[(str(fake), str(tmp_path))] = ((2, 1, 274), time.monotonic())
    argv = ws._with_map("a" * 12, [str(fake)], str(tmp_path))
    assert argv == [str(fake)]
    assert not (folder / planted).is_file()


def test_a_version_read_long_before_its_start_is_not_used(tmp_path, monkeypatch):
    # a read never taken (a chat's, an agent run's) is dropped once old, and none older is given to a start
    from proofpane_workspace import workspace as space
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    ws = space.Workspace(str(root))
    fake = stand_in(tmp_path / "bin", "2.1.274")
    folder = tmp_path / "s"
    folder.mkdir()
    ws._dir = lambda sid: str(folder)
    old = time.monotonic() - space.READ_VERSION_FOR - 1
    ws._read_versions[(str(fake), str(tmp_path))] = ((2, 1, 274), old)
    ws._read_versions[("/other/claude", "/other")] = ((2, 1, 274), old)
    assert ws._with_map("a" * 12, [str(fake)], str(tmp_path)) == [str(fake)]
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path)}
    assert ws.claude_floor_refusal(str(fake), str(root), env) is None
    assert ("/other/claude", "/other") not in ws._read_versions and (str(fake), str(root)) in ws._read_versions
