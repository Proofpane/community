"""The Claude Code settings a workspace's sessions run with (proofpane_workspace/settings.py): one function makes both
the image's managed settings and the local provider's --settings file, so the gate and the status line cannot drift
apart between the two; each place gets the one difference its level needs."""

import json
import os
import shlex
import stat
import subprocess
import sys
import tomllib

from conftest import ROOT
from proofpane_workspace import providers, settings

PY, DIR = "/usr/bin/python3", "/opt/proofpane/proofpane_workspace"


def test_both_places_run_the_same_gate_on_every_tool_and_the_same_status_line():
    managed = settings.claude_settings(PY, DIR, managed=True)
    local = settings.claude_settings(PY, DIR, managed=False)
    assert managed["hooks"] == local["hooks"] and managed["statusLine"] == local["statusLine"]
    activity = lambda state: [{"type": "command", "command": PY,  # noqa: E731
                               "args": ["-E", "-s", "-X", "utf8", f"{DIR}/activity.py", state], "timeout": 5}]
    assert managed["hooks"] == {
        "PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": PY,
                                                   "args": ["-E", "-s", "-X", "utf8", f"{DIR}/gate.py"],
                                                   "timeout": 10}]}],
        "UserPromptSubmit": [{"hooks": activity("working")}],
        "PostToolUse": [{"matcher": "*", "hooks": activity("working")}],
        "Notification": [{"hooks": activity("waiting")}],
        "Stop": [{"hooks": activity("idle")}],
    }
    assert managed["statusLine"] == {"type": "command", "command": f"{PY} -E -s -X utf8 {DIR}/statusline.py"}


def _hooks(made):
    """Every hook of a Claude Code settings dict, with the event it is for."""
    return [(event, hook) for event, groups in made["hooks"].items() for group in groups for hook in group["hooks"]]


def test_no_hook_is_a_command_line_in_either_place():
    # Claude Code runs a hook given as a line through a shell, wrapped in CLAUDE_CODE_SHELL_PREFIX, which a settings
    # file a session loads may set to a program that does not run its argument; one given with `args` it starts as a
    # program, with no shell and no prefix. The interpreter is named in full (a settings file's env may set PATH), and
    # nothing in `command` is a word for a shell to split: a line put there whole is a program that does not exist,
    # which Claude Code cannot start, and the call would go on.
    for managed in (True, False):
        made = _hooks(settings.claude_settings(PY, DIR, managed))
        assert [event for event, _ in made] == ["PreToolUse", "UserPromptSubmit", "PostToolUse", "Notification", "Stop"]
        for event, hook in made:
            assert hook["type"] == "command" and isinstance(hook.get("args"), list), (managed, event, hook)
            assert hook["command"] == PY and os.path.isabs(hook["command"]), (managed, event, hook)
            assert all(isinstance(a, str) for a in hook["args"]), (managed, event, hook)
            assert hook["args"][:4] == list(settings.ISOLATED) == ["-E", "-s", "-X", "utf8"], (managed, event, hook)
            script = hook["args"][4]
            assert os.path.isabs(script) and os.path.dirname(script) == DIR, (managed, event, hook)
            assert os.path.basename(script) == ("gate.py" if event == "PreToolUse" else "activity.py")
        states = {event: hook["args"][5:] for event, hook in made}
        assert states == {"PreToolUse": [], "UserPromptSubmit": ["working"], "PostToolUse": ["working"],
                          "Notification": ["waiting"], "Stop": ["idle"]}


def test_each_hook_runs_as_a_program_with_its_arguments_and_the_gate_refuses(tmp_path):
    # as Claude Code starts a hook given with args: the program and its arguments, no shell; the real gate and the real
    # activity hook, from this checkout, with the interpreter that runs the tests
    directory = os.path.join(ROOT, "proofpane_workspace")
    call = json.dumps({"tool_name": "Bash", "tool_input": {"command": "cat ~/.claude/.credentials.json"}})
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "PP_SESSION_DIR": str(tmp_path)}
    for managed in (True, False):
        made = dict(_hooks(settings.claude_settings(sys.executable, directory, managed)))
        gate = made["PreToolUse"]
        out = subprocess.run([gate["command"], *gate["args"]], input=call, capture_output=True, text=True, env=env,
                             timeout=30)
        assert json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny", (managed, out.stderr)
        stop, state = made["Stop"], tmp_path / "activity.json"
        state.unlink(missing_ok=True)
        done = subprocess.run([stop["command"], *stop["args"]], input=json.dumps({"session_id": "s1"}),
                              capture_output=True, text=True, env=env, timeout=30)
        assert done.returncode == 0 and json.loads(state.read_text())["state"] == "idle", (managed, done.stderr)


def test_managed_settings_never_name_disable_all_hooks():
    # set there, it is the one value that turns managed hooks off; false there would take away the user's own switch
    managed = settings.claude_settings(PY, DIR, managed=True)
    assert "disableAllHooks" not in managed
    assert managed["env"] == dict({name: "" for name in settings.LOADER_ENV}, DISABLE_AUTOUPDATER="1",
                                  CLAUDE_CODE_ENABLE_FUNCTION_HOOKS="0", CLAUDE_CODE_SIMPLE="0")


def test_the_local_file_holds_the_gate_on_against_a_repository_s_settings():
    local = settings.claude_settings(PY, DIR, managed=False)
    assert local["disableAllHooks"] is False
    assert local["env"] == dict({name: "" for name in settings.LOADER_ENV}, CLAUDE_CODE_SIMPLE="0")


def test_both_places_keep_bare_mode_off_as_a_task_s_attempt_does(tmp_path):
    # CLAUDE_CODE_SIMPLE at "1" is Claude Code's
    # bare mode, in which no hook a settings file gives runs, the gate's included. The user's own settings can set it, a
    # project's cannot; both places pin it at "0" a level above the user's, the value a task's attempt pins in its own
    # --settings. As the two files are written: the image's by --managed, the local provider's by --out.
    from proofpane_workspace import task_attempt
    out = tmp_path / "settings.json"
    subprocess.run([sys.executable, "-m", "proofpane_workspace.settings", "--out", str(out), "--python", PY,
                    "--dir", DIR], check=True, cwd=ROOT, timeout=60)
    printed = subprocess.run([sys.executable, "-m", "proofpane_workspace.settings", "--managed", "--python", PY,
                              "--dir", DIR], check=True, cwd=ROOT, capture_output=True, text=True, timeout=60).stdout
    for made in (json.loads(out.read_text()), json.loads(printed)):
        assert made["env"]["CLAUDE_CODE_SIMPLE"] == "0", made["env"]
    assert settings.SIMPLE_ENV == {"CLAUDE_CODE_SIMPLE": "0"}
    assert task_attempt.ATTEMPT_ENV["CLAUDE_CODE_SIMPLE"] == settings.HOOK_ENV["CLAUDE_CODE_SIMPLE"] == "0"


def test_an_agent_s_own_run_keeps_bare_mode_off_against_the_user_s_settings_as_the_stand_in_reads_them(tmp_path):
    # the pin shown failing where a stand-in can. The stand-in claude
    # (tests/chat_stand_in.py) reads its settings in 2.1.274's order: the user's file, the project's and the local
    # one, then its --settings, then a policy tier, each one's env over the one before; bare mode is
    # CLAUDE_CODE_SIMPLE at 1 in the result (or --bare), and then it calls no hook. An agent's own run gets the
    # workspace's --settings file under the local provider (PP_WORKSPACE_CLAUDE_SETTINGS), and in the image the
    # managed settings, which the stand-in reads as its policy tier. With the user's own settings turning bare mode
    # on, each file's pin keeps it off; with neither pinned (the controls, run first) it is on. What the real claude
    # does is measured only by the image's Docker check, test_bare_mode_in_the_user_s_own_settings_leaves_the_gate_
    # running in test_workspace_image_docker.py.
    from test_agents_runs import Workspace, put, run, starts, until
    from workspace_fixtures import Agent
    made = tmp_path / "claude-settings.json"

    class Local(Workspace):
        """The test's workspace agent, started as the local provider starts it, with its --settings file."""

        def start(self):
            info = os.path.join(self.root, "agent.json")
            if os.path.exists(info):
                os.unlink(info)
            self.agent = Agent(self.root, str(self.home), {"PATH": f"{self.bin}:{os.environ['PATH']}",
                                                           "PP_WORKSPACE_CLAUDE_SETTINGS": str(made)})
            self.http = self.agent.http

    def unpinned(given):
        return dict(given, env={k: v for k, v in given["env"].items() if k != "CLAUDE_CODE_SIMPLE"})
    local = settings.claude_settings(sys.executable, settings.HERE, managed=False)
    managed = settings.claude_settings(sys.executable, settings.HERE, managed=True)
    made.write_text(json.dumps(unpinned(local)))
    ws = Local(tmp_path)
    policy = ws.home / "stand-in-managed.json"

    def bare(given, policy_given):
        made.write_text(json.dumps(given))              # read again at each chat start (mcp.settings_value)
        if policy_given is None:
            policy.unlink(missing_ok=True)
        else:
            policy.write_text(json.dumps(policy_given))
        # a bound for a loaded machine, not a wait: a run that takes seconds here took more than until's 30 on a Mac
        # busy with a whole suite's run (a load up to 55.5 on 12 cores)
        until(ws, run(ws)["run_id"], ("done",), timeout=120)
        start = starts(ws)[-1]
        return start["bare"], start["effective"].get("CLAUDE_CODE_SIMPLE")
    try:
        (ws.home / ".claude").mkdir(exist_ok=True)
        (ws.home / ".claude" / "settings.json").write_text(json.dumps({"env": {"CLAUDE_CODE_SIMPLE": "1"}}))
        put(ws, "hello")
        assert bare(unpinned(local), None) == (True, "1")                   # the user's own 1: bare mode
        assert bare(unpinned(local), unpinned(managed)) == (True, "1")
        assert bare(local, None) == (False, "0")                            # the local provider's --settings file
        assert bare(unpinned(local), managed) == (False, "0")               # the image's managed settings
    finally:
        ws.stop()


def test_both_places_set_every_loader_variable_empty_and_the_workspace_passes_none_on():
    # a settings file's env could set a
    # loader variable, which reaches the gate started directly, and on a Mac newly so with the exec form; both files
    # set each one empty at the level that outranks a repository's. Empty takes nothing from a session: neither the
    # workspace nor the app that starts it passes any of them on. Every name the module pins is named here, so leaving
    # one out of LOADER_ENV fails this test; a further LD_ or DYLD_ name may be added there without changing it.
    # glibc's GCONV_PATH and LOCPATH are named too: where it loads its
    # character-conversion modules, which are libraries, and its locale files from
    from proofpane_open.ade import workspaces
    from proofpane_workspace import workspace
    named = {"LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH",
             "DYLD_FRAMEWORK_PATH", "DYLD_FALLBACK_LIBRARY_PATH", "DYLD_FALLBACK_FRAMEWORK_PATH",
             "DYLD_VERSIONED_LIBRARY_PATH", "DYLD_VERSIONED_FRAMEWORK_PATH", "DYLD_IMAGE_SUFFIX", "GCONV_PATH",
             "LOCPATH"}
    assert named <= set(settings.LOADER_ENV)
    assert all(name.startswith(("LD_", "DYLD_")) or name in ("GCONV_PATH", "LOCPATH") for name in settings.LOADER_ENV)
    for managed in (True, False):
        env = settings.claude_settings(PY, DIR, managed)["env"]
        assert all(env.get(name) == "" for name in settings.LOADER_ENV), (managed, env)
    assert not set(settings.LOADER_ENV) & set(workspace.PASS_ENV)
    assert not set(settings.LOADER_ENV) & set(workspaces.AGENT_PASS_ENV)


def test_gemini_s_settings_exclude_every_loader_variable_from_a_project_env():
    # Gemini CLI loads a trusted folder's project .env into its own process and hands its hooks that environment; its
    # default exclusion list does not name a loader variable, so without this a trusted folder's .env could set
    # LD_PRELOAD and reach the gate's python3 -E -s, which the dynamic linker preloads before the gate runs. The list
    # is named under advanced.excludedEnvVars; its merge strategy is union, so a repository can only add to it.
    got = settings.gemini_settings(PY, DIR)
    excluded = got["advanced"]["excludedEnvVars"]
    # the list is LOADER_ENV and Gemini's defaults; the subset checks read both sides from the same constant, so they
    # pin the shape, not the list's completeness (dropping a name shrinks both sides together). The master list's
    # completeness is pinned by test_both_places_set_every_loader_variable_empty_and_the_workspace_passes_none_on, which
    # names every one of them; the image's Docker check has no case of Gemini's list. The literal names below pin that
    # the most dangerous ones, and glibc's pair, reach this list
    assert set(settings.LOADER_ENV) <= set(excluded)
    # Gemini's own defaults are kept besides them (so the list does not narrow what Gemini already excludes). Gemini's
    # merged settings carry DEBUG and DEBUG_MODE from its schema default anyway, so naming them here adds the other two
    assert set(settings.GEMINI_DEFAULT_EXCLUDED_ENV_VARS) <= set(excluded)
    assert "LD_PRELOAD" in excluded and "LD_AUDIT" in excluded and "LD_LIBRARY_PATH" in excluded
    assert "GCONV_PATH" in excluded and "LOCPATH" in excluded
    # the gate's hooks are still there and still run with -E -s -X utf8 (the exclusion did not displace them)
    (gate_hook,) = got["hooks"]["BeforeTool"][0]["hooks"]
    assert gate_hook["name"] == "proofpane-gate"
    assert gate_hook["command"] == f"{PY} -E -s -X utf8 {DIR}/gate.py --gemini"


def test_every_gemini_hook_sets_the_loader_variables_empty_in_its_own_env():
    # the exclusion list reaches a project .env only, not a .gemini/.env, which Gemini also loads in a trusted folder;
    # Gemini's hook runner puts a hook's own env over the process environment (0.62.0, read), so each hook the
    # workspace gives Gemini sets every loader variable empty there, whatever file gave the process one
    got = settings.gemini_settings(PY, DIR)
    hooks = [h for groups in got["hooks"].values() for group in groups for h in group["hooks"]]
    assert {h["name"] for h in hooks} == {"proofpane-gate", "proofpane-working", "proofpane-waiting", "proofpane-idle"}
    for h in hooks:
        assert h["env"] == {variable: "" for variable in settings.LOADER_ENV}, h["name"]
    assert "LD_PRELOAD" in settings.LOADER_ENV and "DYLD_INSERT_LIBRARIES" in settings.LOADER_ENV
    assert all(h["env"].get("GCONV_PATH") == "" and h["env"].get("LOCPATH") == "" for h in hooks)


def test_a_path_with_a_space_is_one_argument_and_quoted_in_the_status_line():
    # every hook, in both places: the local provider's folder and interpreter are wherever the app is installed, and a
    # path quoted for a shell, as script_line quotes it, names a program that does not exist or a script the
    # interpreter cannot open (it then ends with 2)
    states = {"PreToolUse": [], "UserPromptSubmit": ["working"], "PostToolUse": ["working"],
              "Notification": ["waiting"], "Stop": ["idle"]}
    for managed in (True, False):
        made = settings.claude_settings("/a b/python", "/c d", managed)
        hooks = _hooks(made)
        assert [event for event, _ in hooks] == list(states), managed
        for event, hook in hooks:
            script = "gate.py" if event == "PreToolUse" else "activity.py"
            assert (hook["command"], hook["args"]) == (
                "/a b/python", ["-E", "-s", "-X", "utf8", f"/c d/{script}", *states[event]]), (managed, event, hook)
        assert made["statusLine"]["command"] == "'/a b/python' -E -s -X utf8 '/c d/statusline.py'"


def test_the_module_prints_the_managed_settings_and_writes_a_private_file(tmp_path):
    args = [sys.executable, "-m", "proofpane_workspace.settings", "--python", PY, "--dir", DIR]
    out = subprocess.run(args + ["--managed"], cwd=ROOT, capture_output=True, text=True, check=True, timeout=30)
    assert json.loads(out.stdout) == settings.claude_settings(PY, DIR, managed=True)
    path = tmp_path / "gate-settings.json"
    leftover = tmp_path / "gate-settings.json.tmp"
    leftover.write_text("left by a run that stopped halfway")
    os.chmod(leftover, 0o644)
    subprocess.run(args + ["--out", str(path)], cwd=ROOT, check=True, timeout=30)
    assert json.loads(path.read_text()) == settings.claude_settings(PY, DIR, managed=False)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600 and not leftover.exists()


def _commands(value):
    """Every program a settings value runs, as its words: a command line split as the shell splits it, or a program
    and its arguments (`command` and `args`) as they are given."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "command" and isinstance(item, str):
                yield [item, *value["args"]] if "args" in value else shlex.split(item)
            yield from _commands(item)
    elif isinstance(value, list):
        for item in value:
            yield from _commands(item)


def test_every_script_a_program_runs_for_the_workspace_starts_python_isolated():
    # a file a session wrote in the user's home (usercustomize.py, a .pth file), or a PYTHON* variable a repository's
    # settings set, would otherwise run inside every hook, and could end the gate before it answers; and a locale whose
    # character set Python has no codec for, which a settings file's env can name, would end the interpreter's start
    # without -X utf8 (test_hooks_locale.py runs them under one)
    made = [settings.claude_settings(PY, DIR, managed=True), settings.claude_settings(PY, DIR, managed=False),
            settings.gemini_settings(PY, DIR), settings.qwen_settings(PY, DIR),
            tomllib.loads(settings.codex_requirements(PY, DIR)), tomllib.loads(settings.grok_requirements(PY, DIR))]
    words = [w for each in made for w in _commands(each)] + [shlex.split(providers.helper_command(PY, DIR, "kimi"))]
    assert len(words) > 25
    for argv in words:
        assert argv[:5] == [PY, *settings.ISOLATED] == [PY, "-E", "-s", "-X", "utf8"], argv


def test_a_variable_a_repository_s_settings_set_does_not_run_inside_the_gate(tmp_path):
    # a repository's .claude/settings.json `env` reaches the hooks Claude Code starts: PYTHONPATH naming a folder with a
    # json.py of its own would run it inside the gate, which could end there before it answers
    planted = tmp_path / "planted"
    planted.mkdir()
    (planted / "json.py").write_text("import os\nos._exit(0)\n")
    hook = settings.claude_settings(sys.executable, os.path.join(ROOT, "proofpane_workspace"), managed=False)[
        "hooks"]["PreToolUse"][0]["hooks"][0]
    call = json.dumps({"tool_name": "Bash", "tool_input": {"command": "cat ~/.claude/.credentials.json"}})
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "PYTHONPATH": str(planted)}
    plain = subprocess.run([sys.executable, os.path.join(ROOT, "proofpane_workspace", "gate.py")], input=call,
                           capture_output=True, text=True, env=env, timeout=30)
    assert plain.returncode == 0 and plain.stdout == ""          # the control: the planted module ended the gate
    gated = subprocess.run([hook["command"], *hook["args"]], input=call, capture_output=True, text=True, env=env,
                           timeout=30)
    assert json.loads(gated.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
