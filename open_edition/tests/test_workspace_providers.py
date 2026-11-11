"""Other providers' models in Claude Code (providers.py): the person's own key kept by the workspace (0600) and never
sent back; a claude session on Kimi K3, MiniMax M3 or DeepSeek started with the vendor's endpoint and model names in
its environment, and its key only through Claude Code's apiKeyHelper, in a settings file of its own (with the local
provider's gate settings in it); DeepSeek's entry, a DeepSeek session and a task's attempt on a DeepSeek key held to
DeepSeek's own page; and the gate keeping the key file and the helper from a session."""

import json
import os
import shlex
import stat
import subprocess
import sys
import time

import pytest

from proofpane_workspace import gate, providers
from test_task_attempt import attempt, claude_starts, tws  # noqa: F401  (tws: a fixture)
from workspace_fixtures import CLAUDE_VERSION_PY, Agent, make_repo

HERE = os.path.dirname(os.path.abspath(__file__))
KEY = "sk-test-" + "k" * 24

# DeepSeek's Claude Code page (CATALOG["deepseek"]["docs"]), read in a browser on 2026-10-18: every variable it
# sets but the key, as it gives them; the page gives no date. These are written out here, not read from providers.py,
# so that a change to DeepSeek's entry is a change made on purpose: read the page again, and change both.
DEEPSEEK_PAGE = {
    "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
    "ANTHROPIC_MODEL": "deepseek-flash[1m]",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "deepseek-flash[1m]",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "deepseek-flash[1m]",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-flash",
    "CLAUDE_CODE_SUBAGENT_MODEL": "deepseek-flash",
    "CLAUDE_CODE_EFFORT_LEVEL": "max",
    "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "786432",
}


def _stand_in(bin_dir):
    """A claude that writes its arguments and environment into the session's folder, then waits like an agent."""
    path = bin_dir / "claude"
    path.write_text(f"#!{sys.executable}\n"
                    "import json, os, sys, time\n" + CLAUDE_VERSION_PY +
                    "out = os.path.join(os.environ['PP_SESSION_DIR'], 'seen.json')\n"
                    "json.dump({'argv': sys.argv[1:], 'env': dict(os.environ)}, open(out + '.tmp', 'w'))\n"
                    "os.replace(out + '.tmp', out)\n"
                    "time.sleep(600)\n")
    path.chmod(0o755)


def _seen(folder, wait=10):
    path = folder / "seen.json"
    end = time.time() + wait
    while not path.exists() and time.time() < end:
        time.sleep(0.05)
    return json.loads(path.read_text())


@pytest.fixture(params=["hosted", "local"])
def agent(request, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stand_in(bin_dir)
    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
    if request.param == "local":        # the local provider starts claude with its gate's settings file
        gate_settings = tmp_path / "gate-settings.json"
        gate_settings.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
            {"type": "command", "command": "gate"}]}]}, "disableAllHooks": False}))
        env["PP_WORKSPACE_CLAUDE_SETTINGS"] = str(gate_settings)
    a = Agent(str(tmp_path / "ws"), str(home), env)
    make_repo(str(tmp_path / "ws" / "repos" / "demo"))
    a.home, a.kind = home, request.param
    yield a
    a.close()


def test_the_key_is_kept_by_the_workspace_and_never_sent_back(agent):
    r = agent.http.put("/model-providers/deepseek", json={"key": KEY})
    assert r.status_code == 200 and KEY not in r.text
    assert {p["id"]: p["set"] for p in r.json()["providers"]} == {"kimi": False, "minimax": False, "deepseek": True}
    store = agent.home / ".proofpane" / "model-keys.json"
    assert stat.S_IMODE(store.stat().st_mode) == 0o600 and stat.S_IMODE(store.parent.stat().st_mode) == 0o700
    assert json.loads(store.read_text()) == {"deepseek": KEY}
    assert KEY not in agent.http.get("/model-providers").text
    for bad in ("short", "has a space in it", "x" * 401, 12345678, None):
        assert agent.http.put("/model-providers/kimi", json={"key": bad}).status_code == 400, bad
    assert agent.http.put("/model-providers/other", json={"key": KEY}).status_code == 400
    assert agent.http.delete("/model-providers/deepseek").json()["providers"][2]["set"] is False
    assert json.loads(store.read_text()) == {}


def test_a_session_on_another_model_takes_its_key_from_the_helper_and_never_from_its_environment(agent, tmp_path):
    agent.http.put("/model-providers/kimi", json={"key": KEY})
    r = agent.http.post("/sessions", json={"repo": "demo", "program": "claude", "task": "Tidy the parser",
                                           "model_provider": "kimi"})
    assert r.status_code == 200 and r.json()["model_provider"] == "kimi", r.text
    folder = tmp_path / "ws" / "sessions" / r.json()["id"]
    seen = _seen(folder)
    env, argv = seen["env"], seen["argv"]
    for name, value in providers.CATALOG["kimi"]["env"].items():
        assert env[name] == value, name
    assert KEY not in json.dumps(env)                       # nothing a command the agent runs inherits names the key
    assert not any(k in env for k in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"))
    assert argv[0] == "--settings" and argv[1] == str(folder / "model-settings.json") and argv[-1] == "Tidy the parser"
    assert argv.count("--settings") == 1
    settings_file = folder / "model-settings.json"
    assert stat.S_IMODE(settings_file.stat().st_mode) == 0o600
    settings = json.loads(settings_file.read_text())
    if agent.kind == "local":
        assert settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "gate"    # the gate's settings, kept
    # the endpoint pinned where a repository's settings cannot move it: a --settings file outranks them
    assert settings["env"] == providers.CATALOG["kimi"]["env"]
    # what Claude Code runs for the key, as it runs it (/bin/sh), as the session's user
    helper = subprocess.run(["/bin/sh", "-c", settings["apiKeyHelper"]], capture_output=True, text=True, timeout=30,
                            env={"PATH": os.environ["PATH"], "HOME": str(agent.home)})
    assert helper.returncode == 0 and helper.stdout == KEY


def test_deepseek_s_entry_is_what_its_claude_code_page_gives():
    # the page's own address, its variables and nothing else. A model name DeepSeek does not serve, unless it starts
    # with "claude", fails a session at its first call (its Anthropic API guide: such a name answers 400, and a claude
    # name is mapped, claude-opus ones to deepseek-v4-pro, every other to deepseek-flash); one it serves that the page
    # does not give, deepseek-v4-pro say, changes the model and its price with no error
    entry = providers.CATALOG["deepseek"]
    assert entry["env"] == DEEPSEEK_PAGE
    assert entry["docs"] == "https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code/"
    # the name the app shows: the vendor's, as the page names the model the entry uses only by its id, deepseek-flash,
    # and as the session list, a task's records and the progress board call this provider
    assert (entry["label"], entry["vendor"]) == ("DeepSeek", "DeepSeek")
    listed = {p["id"]: p for p in providers.listing("/nonexistent")["providers"]}
    assert listed["deepseek"]["model"] == "deepseek-flash[1m]"


def test_a_deepseek_session_is_started_with_what_its_page_gives(agent, tmp_path):
    # in the environment Claude Code is started with, and in the session's own settings file, which outranks a
    # repository's settings: each of the page's variables as it gives it, and no model name the page does not give
    # (one more, a Fable slot say, would otherwise pass)
    gate_env = {}
    if agent.kind == "local":
        # the gate's settings given an env, as the workspace's own file has one (settings.py), with three of the page's
        # names set otherwise: the page's values must win in the session's file (the gate's winning would otherwise
        # pass)
        gate_env = {"CLAUDE_CODE_SIMPLE": "0", "ANTHROPIC_BASE_URL": "https://gate.invalid",
                    "ANTHROPIC_MODEL": "gate-model", "CLAUDE_CODE_EFFORT_LEVEL": "low"}
        gate_settings = tmp_path / "gate-settings.json"
        gate_settings.write_text(json.dumps(dict(json.loads(gate_settings.read_text()), env=gate_env)))
    # DeepSeek's key kept among others, Anthropic's, whose name sorts first, and Kimi's before it, MiniMax's after: a
    # helper giving the first key kept, the last, or the first by its provider's name is wrong
    others = [p for p in sorted(providers.CATALOG) if p != "deepseek"]
    keys = {p: KEY if p == "deepseek" else f"sk-test-{p}-" + "o" * 24
            for p in ["anthropic"] + others[:1] + ["deepseek"] + others[1:]}
    for provider, key in keys.items():
        assert agent.http.put(f"/model-providers/{provider}", json={"key": key}).status_code == 200
    r = agent.http.post("/sessions", json={"repo": "demo", "program": "claude", "task": "Tidy the parser",
                                           "model_provider": "deepseek"})
    assert r.status_code == 200 and r.json()["model_provider"] == "deepseek", r.text
    folder = tmp_path / "ws" / "sessions" / r.json()["id"]
    seen = _seen(folder)
    env, argv = seen["env"], seen["argv"]
    assert {name: env.get(name) for name in DEEPSEEK_PAGE} == DEEPSEEK_PAGE
    assert sorted(n for n in env if "MODEL" in n) == sorted(n for n in DEEPSEEK_PAGE if "MODEL" in n)
    # the claude given the session's own settings file, once, the session's user's alone, as Kimi's is (a DeepSeek
    # session started without it, or with it open to every user, would otherwise pass)
    settings_file = folder / "model-settings.json"
    assert argv[0] == "--settings" and argv[1] == str(settings_file) and argv.count("--settings") == 1
    assert stat.S_IMODE(settings_file.stat().st_mode) == 0o600
    settings = json.loads(settings_file.read_text())
    assert settings["env"] == dict(gate_env, **DEEPSEEK_PAGE)
    if agent.kind == "local":
        # the gate's settings kept in it, as in Kimi's, since it takes the gate's file's place (a
        # DeepSeek session's file without them would otherwise pass)
        assert settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "gate"
    # but for the page's ninth variable, the key: never in what every command the session runs inherits (Claude Code
    # takes it from the apiKeyHelper), and the helper gives DeepSeek's, not another provider's kept beside it (Kimi's
    # given would otherwise pass)
    assert not any(k in json.dumps(env) for k in keys.values())
    assert not any(k in env for k in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"))
    helper = subprocess.run(["/bin/sh", "-c", settings["apiKeyHelper"]], capture_output=True, text=True, timeout=30,
                            env={"PATH": os.environ["PATH"], "HOME": str(agent.home)})
    assert helper.returncode == 0 and helper.stdout == KEY


def test_a_deepseek_session_s_claude_is_read_with_what_its_page_gives(tmp_path, monkeypatch):
    # the floor's read of the claude a DeepSeek session starts (its version and its help) runs in the environment that
    # start gives (_claude_start_env), the page's variables in it. The floor's own test holds this for the catalog's
    # first provider alone (sorted(CATALOG)[0]), which is DeepSeek only while no provider's id sorts before it
    from proofpane_workspace.workspace import Workspace
    (tmp_path / "ws").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    ws = Workspace(str(tmp_path / "ws"))
    monkeypatch.setattr(ws, "_account_env", lambda record: {})
    env = ws._claude_start_env("b" * 12, "claude", "deepseek")
    assert {name: env.get(name) for name in DEEPSEEK_PAGE} == DEEPSEEK_PAGE


def test_a_deepseek_attempt_s_settings_are_what_its_page_gives():
    # a task's attempt pressed onto a DeepSeek key (task_runs.py) has its own settings, with the page's values in its
    # env; its effort there is what an attempt was measured to run at, given low, high or no effort
    from types import SimpleNamespace

    from proofpane_workspace import task_runs
    me = SimpleNamespace(ws=SimpleNamespace(claude_settings=None))
    settings = task_runs.TaskRuns._settings(me, {"kind": "key", "provider": "deepseek"}, {})
    assert settings["env"] == DEEPSEEK_PAGE
    here = os.path.dirname(os.path.abspath(providers.__file__))
    assert shlex.split(settings["apiKeyHelper"])[-2:] == [os.path.join(here, "model_key.py"), "deepseek"]


def test_a_deepseek_attempt_runs_what_its_page_gives_and_not_the_agent_s_model(tws):
    # the attempt as it runs (the test above calls _settings alone, with no pins, and the model is
    # chosen outside it): no --model, which outranks the page's ANTHROPIC_MODEL, where the agent's own, a claude-opus
    # name, would run deepseek-v4-pro at its price by DeepSeek's mapping and a name not starting with claude would
    # answer 400; the page's values in its --settings env beside the attempt's pins, the page's max over the effort the
    # agent chose and its window over the agent's compaction point, and no other model name; a helper giving DeepSeek's
    # key, kept among others as in the session's test; and no key in a start's arguments or in its environment (the
    # key in --settings, on the claude's command line, would otherwise pass, and so would the agent's own effort
    # put there, the agent having chosen none, the key in a value of the environment, and the
    # agent's compaction point put in --settings)
    keys = {"anthropic": "sk-ant-test-" + "a" * 30, "kimi": "sk-kimi-test-" + "o" * 30,
            "deepseek": "sk-deepseek-test-" + "k" * 30, "minimax": "sk-minimax-test-" + "m" * 30}
    for provider, key in keys.items():
        assert tws.http.put(f"/model-providers/{provider}", json={"key": key}).status_code == 200
    (tws.home / "stand-in-env-values").write_text("")             # each start's whole environment, values too
    model = {"chat_settings": {"allow": ["Write", "Edit", "Bash"], "model": "claude-opus-5", "effort": "low"},
             "autocompact_window": 300000}
    done = attempt(tws, "complete done", agent=model, backend={"kind": "key", "provider": "deepseek"}, identity=None,
                   cap_usd=5)
    assert done["state"] == "done", done
    starts = [s for s in claude_starts(tws) if s["attempt"]]    # an effort chosen adds a start of no attempt's
    assert [s["frozen"] for s in starts] == [True, False]
    for start in starts:
        args = start["args"]
        assert "--model" not in args, args
        assert args[args.index("--effort") + 1] == "low"           # the agent's effort, given to the attempt
        settings = json.loads(args[args.index("--settings") + 1])
        env = settings["env"]
        assert {name: env.get(name) for name in DEEPSEEK_PAGE} == DEEPSEEK_PAGE
        assert sorted(n for n in env if "MODEL" in n) == sorted(n for n in DEEPSEEK_PAGE if "MODEL" in n)
        assert not {"ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"} & set(start["env"])
        helper = subprocess.run(["/bin/sh", "-c", settings["apiKeyHelper"]], capture_output=True, text=True,
                                timeout=30, env={"PATH": os.environ["PATH"], "HOME": str(tws.home)})
        assert helper.returncode == 0 and helper.stdout == keys["deepseek"]
    recorded = (tws.home / "stand-in-runs.jsonl").read_text()
    assert not any(key in recorded for key in keys.values())      # in no start's arguments or names
    environments = [json.loads(line) for line in (tws.home / "stand-in-env.jsonl").read_text().splitlines()]
    assert environments and not any(key in json.dumps(environments) for key in keys.values())
    # the agent's compaction point given, in the environment the attempt is started with, which --settings outranks
    attempt_envs = [e for e in environments if e.get("PP_TASK_ATTEMPT") == "1"]
    assert len(attempt_envs) == len(starts)
    assert all(e.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW") == "300000" for e in attempt_envs)


def test_the_workspace_s_own_settings_are_read_as_before_a_link_and_past_the_map_s_bound(tmp_path):
    # the operator's file, which the workspace always read so; read as a session's own file is (no link, bounded), it
    # refused a session that started before. Past the bound the map is not given, since the
    # session's own map file is read back no larger, by the start and by the map, whose bound is the start's (a file
    # between the map's 256 KB and the start's 1 MB was once given, and the map said "missing"); the
    # session starts all the same, with the gate
    bin_dir, home = tmp_path / "bin", tmp_path / "home"
    bin_dir.mkdir()
    home.mkdir()
    _stand_in(bin_dir)
    gate_hooks = {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "gate"}]}]}
    small, medium, large = tmp_path / "small.json", tmp_path / "medium.json", tmp_path / "large.json"
    named = tmp_path / "gate-settings.json"
    small.write_text(json.dumps({"hooks": gate_hooks, "disableAllHooks": False}))
    medium.write_text(json.dumps({"hooks": gate_hooks, "disableAllHooks": False, "padding": "p" * (400 * 1024)}))
    large.write_text(json.dumps({"hooks": gate_hooks, "disableAllHooks": False, "padding": "p" * (1024 * 1024)}))
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": f"{bin_dir}:{os.environ['PATH']}",
                                                "PP_WORKSPACE_CLAUDE_SETTINGS": str(named)})
    try:
        make_repo(str(tmp_path / "ws" / "repos" / "demo"))
        a.http.put("/model-providers/kimi", json={"key": KEY})
        for target, mapped in ((small, True), (medium, False), (large, False)):
            if named.is_symlink():
                named.unlink()
            os.symlink(target, named)
            for body, own in (({"model_provider": "kimi"}, "model-settings.json"), ({}, "map-settings.json")):
                r = a.http.post("/sessions", json=dict({"repo": "demo", "program": "claude", "task": "t"}, **body))
                assert r.status_code == 200, (target.name, r.text)
                folder = tmp_path / "ws" / "sessions" / r.json()["id"]
                kept = own if (body or mapped) else None            # no map file: the workspace's own, as named
                assert _seen(folder)["argv"][:2] == ["--settings", str(folder / kept if kept else named)]
                settings = json.loads((folder / kept).read_text() if kept else named.read_text())
                assert settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "gate"    # the gate's, kept
                hooks = [h for g in settings["hooks"].get("PostToolUse", []) for h in g["hooks"]]
                assert any("--map" in h.get("args", []) for h in hooks) is mapped, target.name
                assert (folder / "map-settings.json").exists() is mapped
    finally:
        a.close()


def test_a_session_on_another_model_needs_its_key_and_a_claude(agent):
    r = agent.http.post("/sessions", json={"repo": "demo", "program": "claude", "task": "t", "model_provider": "minimax"})
    assert r.status_code == 400 and "MiniMax M3 API key" in r.json()["detail"]
    for body in ({"program": "shell", "model_provider": "kimi"}, {"program": "claude", "model_provider": "openai"}):
        r = agent.http.post("/sessions", json=dict({"repo": "demo", "task": "t"}, **body))
        assert r.status_code == 400 and "Claude Code" in r.json()["detail"], body


def test_the_gate_keeps_the_keys_and_the_helper_from_a_session(tmp_path):
    home = str(tmp_path)
    for tool_input in ({"command": "cat ~/.proofpane/model-keys.json"},
                       {"file_path": f"{home}/.proofpane/model-keys.json"},
                       {"command": "python3 /opt/proofpane/proofpane_workspace/model_key.py kimi"}):
        why = gate.refusal({"tool_name": "Bash" if "command" in tool_input else "Read", "tool_input": tool_input}, home)
        assert why == gate.SIGN_IN, tool_input
