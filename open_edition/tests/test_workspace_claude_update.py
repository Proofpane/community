"""Claude Code kept current inside the workspace (claude_update.py): a
stand-in claude (chat_stand_in.py) plays the image's own build, and its `install stable` a newer one on the home's
volume, which is used only once it passes its check. No network: what the real `install stable` does was seen in the
image (2.1.274 installed 2.1.277 at ~/.local/share/claude/versions/2.1.277, linked from ~/.local/bin/claude)."""

import json
import os
import subprocess
import sys
import threading
import time
import types

import pytest

from proofpane_workspace import chat as chats, claude_update, settings
from proofpane_workspace.workspace import PASS_ENV, Workspace, WorkspaceError
from workspace_fixtures import Agent, make_repo

pytestmark = pytest.mark.skipif(not chats.available(), reason="needs the Claude Agent SDK (requirements.txt)")
STAND_IN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_stand_in.py")


def fake(path, prelude="", **config):
    """The stand-in, as a build at CONFIG's version; `prelude`, code of a build of the test's own, runs before it."""
    path.write_text(f"#!{sys.executable}\nCONFIG = {config!r}\n" + prelude + open(STAND_IN).read())
    path.chmod(0o755)
    return str(path)


def native(home, prelude="", **config):
    """A build as Claude Code's install puts it on the volume: in its versions folder, named by its version."""
    versions(home).mkdir(parents=True, exist_ok=True)
    return fake(versions(home) / config["VERSION"], prelude=prelude, **config)


def link(home):
    return home / ".local" / "bin" / "claude"


def linked(home, build):
    """~/.local/bin/claude linked to `build`, as the install that put it there left it."""
    link(home).parent.mkdir(parents=True, exist_ok=True)
    link(home).symlink_to(build)


def put_state(root, **fields):
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, claude_update.STATE), "w") as fh:
        json.dump(fields, fh)


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    return home, bin_dir


def versions(home):
    return home / ".local" / "share" / "claude" / "versions"


@pytest.mark.parametrize("config, ok, version, why", [
    ({"VERSION": "2.1.280"}, True, "2.1.280", None),
    ({"VERSION": "2.1.274"}, True, "2.1.274", None),
    ({"VERSION": "2.1.100"}, False, "2.1.100", "it is older than the image's 2.1.274"),
    ({"VERSION": "latest"}, False, None, "its version could not be read"),
    ({"VERSION": "2.1.280", "HELP_OK": False}, False, "2.1.280",
     "its help does not say its efforts and permission modes"),
    ({"VERSION": "2.1.280", "HOOK_ARGS": False}, False, "2.1.280",
     "it did not start a hook given as a program and its arguments"),
    ({"VERSION": "2.1.280", "HOOK_PREFIX_WRAPS": True}, False, "2.1.280",
     "it did not start a hook given as a program and its arguments, outside any shell prefix"),
    ({"VERSION": "2.1.280", "HANDSHAKE_OK": False}, False, "2.1.280", "the Claude Agent SDK could not start it")])
def test_a_build_is_used_only_once_it_passes_its_check(world, tmp_path, config, ok, version, why):
    home, _ = world
    candidate = fake(tmp_path / "candidate", **config)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home)}
    got = claude_update.check(candidate, (2, 1, 274), env, str(tmp_path),
                              lambda path: chats.write_wrapper(str(tmp_path / "w"), PASS_ENV, claude=path))
    assert got[:2] == (ok, version)
    assert (got[2] or "").startswith(why or ""), got[2]
    assert (got[3] is not None) is ok             # what its handshake answered, when there was one


def test_the_args_probe_runs_the_build_against_a_closed_port_and_tells_a_build_that_starts_the_program_alone(
        world, tmp_path, monkeypatch):
    # the probe's hook, given as a program
    # and its arguments, runs before any request to a model; a build that starts the program alone (one from before
    # 2.1.139) writes nothing, so it is never used, and the gate would not have run with it
    home, _ = world
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home)}
    assert claude_update.args_probe(fake(tmp_path / "new", VERSION="2.1.280"), env) is True
    started = time.monotonic()
    assert claude_update.args_probe(fake(tmp_path / "old", VERSION="2.1.280", HOOK_ARGS=False), env) is False
    assert time.monotonic() - started < 10       # a build that ends without the hook's word is not waited out
    assert claude_update.args_probe(str(tmp_path / "not-there"), env) is False
    assert claude_update.args_probe_result(str(tmp_path / "not-there"), env) == \
        (False, "it could not be started (FileNotFoundError)")
    # a build that wraps a hook given as a program in the shell prefix, as it wraps a command line, is told too
    assert claude_update.args_probe(fake(tmp_path / "wraps", VERSION="2.1.280", HOOK_PREFIX_WRAPS=True), env) is False
    # its command and environment: print mode, the probe's own settings and config folder, no MCP server, no
    # nonessential traffic, the API at the closed port, no real key, a shell prefix that runs nothing, and BASH_ENV and
    # ENV naming the probe's own shell file in its folder
    seen = {}
    real = claude_update.subprocess.Popen

    def popen(argv, **kw):
        seen.update(argv=argv, env=kw["env"], cwd=kw["cwd"])
        with open(argv[argv.index("--settings") + 1], encoding="utf-8") as fh:
            seen["settings"] = json.load(fh)
        return real(argv, **kw)
    monkeypatch.setattr(claude_update.subprocess, "Popen", popen)
    assert claude_update.args_probe(fake(tmp_path / "seen", VERSION="2.1.280"), dict(env, CLAUDE_CONFIG_DIR="/u")) \
        is True
    assert seen["argv"][1:3] == ["-p", "Say done."] and "--strict-mcp-config" in seen["argv"]
    assert seen["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9"
    assert seen["env"]["ANTHROPIC_API_KEY"] == "pp-probe-not-a-key" and seen["env"]["HOME"] == str(home)
    assert seen["env"]["CLAUDE_CONFIG_DIR"] == os.path.join(seen["cwd"], "config")
    assert seen["env"]["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert seen["env"]["CLAUDE_CODE_SHELL_PREFIX"] == "/usr/bin/false" == claude_update.PROBE_PREFIX
    shell_file = os.path.join(seen["cwd"], "shell-env")
    assert seen["env"]["BASH_ENV"] == seen["env"]["ENV"] == shell_file
    assert seen["settings"]["env"] == {"CLAUDE_CODE_SHELL_PREFIX": "/usr/bin/false", "BASH_ENV": shell_file,
                                       "ENV": shell_file}
    (group,) = seen["settings"]["hooks"]["UserPromptSubmit"]
    (hook,) = group["hooks"]
    assert hook["command"] == sys.executable and hook["args"][:2] == ["-I", "-c"]
    assert seen["settings"]["disableAllHooks"] is False
    assert not os.path.exists(seen["cwd"])        # its folder, removed after


def test_the_probe_s_mark_is_read_only_as_a_regular_file_and_never_waits(tmp_path):
    # nothing put where the hook writes its word can hold the probe up or pass for it: a FIFO with no writer is not
    # waited on, a FIFO holding the word does not pass for the file, and a link to a file holding it is not followed
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    got = []
    t = threading.Thread(target=lambda: got.append(claude_update._probe_written(str(fifo))), daemon=True)
    t.start()
    t.join(2)
    finished = not t.is_alive()
    if not finished:                                    # a reader waiting for a writer: one comes, so it ends
        os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        t.join(5)
    assert finished and got == [False], got
    held = os.open(fifo, os.O_RDWR)                     # the word in the FIFO, its writer still there
    try:
        os.write(held, claude_update.PROBE_WORD.encode())
        assert claude_update._probe_written(str(fifo)) is False
    finally:
        os.close(held)
    word = tmp_path / "word"
    word.write_text(claude_update.PROBE_WORD)
    assert claude_update._probe_written(str(word)) is True            # the control
    (tmp_path / "link").symlink_to(word)
    assert claude_update._probe_written(str(tmp_path / "link")) is False
    (tmp_path / "longer").write_text(claude_update.PROBE_WORD + "x")
    assert claude_update._probe_written(str(tmp_path / "longer")) is False
    assert claude_update._probe_written(str(tmp_path / "missing")) is False


def test_a_probe_whose_settings_cannot_be_written_leaves_no_folder(world, tmp_path, monkeypatch):
    home, _ = world
    candidate = fake(tmp_path / "candidate", VERSION="2.1.280")
    made = []
    real = claude_update.tempfile.mkdtemp

    def mkdtemp(**kw):
        made.append(real(dir=str(tmp_path), **kw))
        return made[-1]

    def full(*args, **kwargs):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(claude_update.tempfile, "mkdtemp", mkdtemp)
    monkeypatch.setattr(claude_update.json, "dump", full)
    assert claude_update.args_probe_result(candidate, {"PATH": "/usr/bin:/bin", "HOME": str(home)}) == \
        (False, "the probe could not be set up (OSError)")
    assert len(made) == 1 and not os.path.exists(made[0])


def gone(pid, within=5):
    """Whether a process has ended (or ended and waits to be reaped), within a few seconds."""
    deadline = time.monotonic() + within
    while True:
        if os.path.isdir("/proc/self"):
            try:
                with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
                    if fh.read().rsplit(")", 1)[1].split()[0] == "Z":
                        return True
            except OSError:
                return True
        else:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def test_a_build_that_never_ends_is_ended_at_the_probe_s_time_with_what_it_started(world, tmp_path, monkeypatch):
    # a build that hangs in print mode before its hooks, having started a program of its own: the probe gives up at
    # PROBE_SECONDS and ends both, the build's whole session, not the build alone
    home, _ = world
    hang, pids = tmp_path / "hang", tmp_path / "pids"
    hang.write_text(f"#!{sys.executable}\nimport os, subprocess, sys, time\nif '-p' in sys.argv:\n"
                    "    child = subprocess.Popen(['sleep', '600'])\n"
                    f"    open({str(pids)!r}, 'w').write(f'{{os.getpid()}} {{child.pid}}')\n"
                    "    time.sleep(600)\n")
    hang.chmod(0o755)
    monkeypatch.setattr(claude_update, "PROBE_SECONDS", 2)
    started = time.monotonic()
    assert claude_update.args_probe_result(str(hang), {"PATH": "/usr/bin:/bin", "HOME": str(home)}) == \
        (False, f"{claude_update.NO_HOOK} (it was still running after 2 seconds, writing nothing to its error output)")
    assert time.monotonic() - started < 7        # its time, and the ending, not a wait for the build to end
    build, child = (int(x) for x in pids.read_text().split())
    try:
        assert gone(build) and gone(child), (build, child)
    finally:
        for pid in (build, child):
            try:
                os.kill(pid, 9)
            except OSError:
                pass


def test_a_newer_build_is_installed_checked_and_used_and_the_old_ones_go(world, tmp_path):
    home, bin_dir = world
    floor = fake(bin_dir / "claude", INSTALLS={"VERSION": "2.1.280"})
    versions(home).mkdir(parents=True)
    (versions(home) / "2.1.100").write_text("an older build")
    root = str(tmp_path / "ws")
    ws = Workspace(root, claude_updates=True)
    assert ws.claude_path == floor and ws.argv("claude")[0] == floor
    assert ws.capabilities()["claude_version"] == "2.1.274"
    info = ws.update_claude()
    build = str(versions(home) / "2.1.280")
    assert ws.claude_path == build and ws.argv("claude")[0] == build and ws.argv("chat")[0] == build
    assert ws.argv("claude-sign-in") == [build, "auth", "login", "--claudeai"]
    assert (info["version"], info["floor_version"], info["updating"]) == ("2.1.280", "2.1.274", False)
    assert (info["last"]["ok"], info["last"]["version"], info["last"]["changed"]) == (True, "2.1.280", True)
    assert sorted(os.listdir(versions(home))) == ["2.1.280"]              # the older build is gone
    with open(os.path.join(root, claude_update.STATE)) as fh:
        saved = json.load(fh)
    assert (saved["active"], saved["checked"]) == (build, claude_update.CHECK_FORM)    # and the check it passed
    # the machine's next start takes up the build in use
    assert Workspace(root, claude_updates=True).claude_path == build
    # the same build again: nothing changes
    again = ws.update_claude()
    assert (again["last"]["ok"], again["last"]["changed"], ws.claude_path) == (True, False, build)


def test_a_build_that_fails_its_check_stays_unused_and_the_page_is_told_why(world, tmp_path):
    home, bin_dir = world
    floor = fake(bin_dir / "claude", INSTALLS={"VERSION": "2.1.280", "HANDSHAKE_OK": False})
    ws = Workspace(str(tmp_path / "ws"), claude_updates=True)
    info = ws.update_claude()
    assert ws.claude_path == floor and info["version"] == "2.1.274"
    assert (info["last"]["ok"], info["last"]["version"]) == (False, "2.1.280")
    assert info["last"]["why"].startswith("the Claude Agent SDK could not start it")
    # nor is it left where a shell would find it: the image's own is in use, so the link the install made goes, and
    # the build refused with it
    assert not os.path.lexists(link(home)) and os.listdir(versions(home)) == []


def test_a_chat_started_after_an_update_runs_the_new_build(world, tmp_path):
    home, bin_dir = world
    fake(bin_dir / "claude", INSTALLS={"VERSION": "2.1.280"})
    root = tmp_path / "ws"
    ws = Workspace(str(root), claude_updates=True)
    make_repo(str(root / "repos" / "demo"))
    record, _ = ws.prepare("demo", "chat", "hello")
    ws.chat(record["id"])                                  # its wrapper is written, naming the image's build
    before = open(os.path.join(str(root), "chat-claude")).read()
    ws.update_claude()
    after = open(os.path.join(str(root), "chat-claude")).read()
    assert str(bin_dir / "claude") in before and str(versions(home) / "2.1.280") in after


def test_what_claude_offers_read_while_an_update_changes_the_build_is_not_kept_as_the_new_build_s(world, tmp_path,
                                                                                                  monkeypatch):
    # a page asking what the installed claude offers (capabilities) as the start-time update finishes: the read of the
    # old build must not be stored over the new one's, where it would stay until the next update or restart
    home, bin_dir = world
    fake(bin_dir / "claude", INSTALLS={"VERSION": "2.1.280"})
    ws = Workspace(str(tmp_path / "ws"), claude_updates=True)
    from proofpane_workspace import workspace as module
    real = module.claude_cli.discover
    started, go = threading.Event(), threading.Event()

    def held(claude, *args, **kwargs):
        got = real(claude, *args, **kwargs)
        if not started.is_set():           # the first read, of the old build, is still on its way back
            started.set()
            go.wait(30)
        return got
    monkeypatch.setattr(module.claude_cli, "discover", held)
    reader = threading.Thread(target=ws.capabilities)
    reader.start()
    assert started.wait(30)
    # the old build's read comes back while the update, done, waits to read the new one (one read at a time)
    threading.Timer(0.5, go.set).start()
    assert ws.update_claude()["version"] == "2.1.280"
    reader.join(30)
    assert ws.capabilities()["claude_version"] == "2.1.280"


def test_two_first_reads_of_what_claude_offers_at_once_both_get_it(world, tmp_path, monkeypatch):
    # each read writes the same wrapper file: two at once used to trip over each other's (FileNotFoundError)
    home, bin_dir = world
    fake(bin_dir / "claude")
    ws = Workspace(str(tmp_path / "ws"), claude_updates=True)
    got, errors = [], []

    def read():
        try:
            got.append(ws.capabilities()["claude_version"])
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))
    readers = [threading.Thread(target=read) for _ in range(6)]
    for t in readers:
        t.start()
    for t in readers:
        t.join(60)
    assert errors == [] and got == ["2.1.274"] * 6


def test_updates_are_automatic_or_manual_and_only_in_a_workspace_machine(world, tmp_path, monkeypatch):
    home, bin_dir = world
    fake(bin_dir / "claude", INSTALLS={"VERSION": "2.1.280"})
    plain = Workspace(str(tmp_path / "plain"))
    assert plain.update_info() == {"enabled": False}
    with pytest.raises(WorkspaceError):
        plain.update_claude()
    ws = Workspace(str(tmp_path / "ws"), claude_updates=True)
    assert ws.update_info()["mode"] == "automatic"
    with pytest.raises(WorkspaceError):
        ws.update_mode("whenever")
    assert ws.update_mode("manual")["mode"] == "manual"
    assert claude_update.load(str(tmp_path / "ws"))["mode"] == "manual"
    # the round at the start and each day: nothing while manual, an update once automatic
    from proofpane_workspace import __main__ as entry

    class Done(Exception):
        pass

    def one_round(seconds):
        raise Done

    # the entry's own clock alone: entry.time is the time module, and its sleep set there would stop every sleep in the
    # process, a subprocess's wait for the update's child included (seen once under load)
    monkeypatch.setattr(entry, "time", types.SimpleNamespace(sleep=one_round, monotonic=time.monotonic, time=time.time))
    with pytest.raises(Done):
        entry.keep_claude_current(ws)
    assert ws.update_info()["last"] is None and not versions(home).exists()
    ws.update_mode("automatic")
    with pytest.raises(Done):
        entry.keep_claude_current(ws)
    assert ws.update_info()["version"] == "2.1.280"
    # once a day: not due again at once, due after a day
    assert claude_update.due(ws.update_state) is False
    assert claude_update.due(ws.update_state, now=time.time() + claude_update.EVERY) is True


def test_the_models_are_read_from_the_handshake_and_the_aliases_stand_without_one(world, tmp_path):
    # 2.1.277 offers Fable, which 2.1.274 does not: the models come from what claude answers the SDK's handshake
    home, bin_dir = world
    models = [{"value": "default", "displayName": "Default (recommended)"},
              {"value": "opus[1m]", "displayName": "Opus (1M context)"},
              {"value": "claude-fable-5-1", "displayName": "Fable"},
              {"value": "--rm -rf", "displayName": "not a model"}, {"value": "haiku", "displayName": ""}]
    fake(bin_dir / "claude", MODELS=models)
    got = Workspace(str(tmp_path / "ws")).capabilities()
    assert got["models"] == [{"value": "opus[1m]", "name": "Opus (1M context)"},
                             {"value": "claude-fable-5-1", "name": "Fable"}, {"value": "haiku", "name": "haiku"}]
    assert got["model_aliases"] == ["opus[1m]", "claude-fable-5-1", "haiku"]
    fake(bin_dir / "claude")                     # a handshake that names no models: the aliases
    got = Workspace(str(tmp_path / "ws2")).capabilities()
    assert [m["value"] for m in got["models"]] == ["fable", "opus", "sonnet", "haiku"]


def test_an_agent_in_a_workspace_machine_updates_claude_at_its_start(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake(bin_dir / "claude", INSTALLS={"VERSION": "2.1.280"})
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": f"{bin_dir}:/usr/bin:/bin"}, args=["--claude-updates"])
    try:
        deadline = time.time() + 60             # an install, a check and a handshake: slow on a busy machine
        while (got := a.http.get("/claude/update").json())["version"] != "2.1.280":
            assert time.time() < deadline, got
            time.sleep(0.2)
        assert (got["mode"], got["last"]["ok"], got["last"]["changed"]) == ("automatic", True, True)
    finally:
        a.close()


def test_the_agent_s_routes_say_and_start_an_update(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake(bin_dir / "claude", INSTALLS={"VERSION": "2.1.280"})
    root = tmp_path / "ws"
    root.mkdir()
    (root / claude_update.STATE).write_text(json.dumps({"mode": "manual"}))     # nothing at the start
    a = Agent(str(root), str(home), {"PATH": f"{bin_dir}:/usr/bin:/bin"}, args=["--claude-updates"])
    try:
        got = a.http.get("/claude/update").json()
        assert (got["enabled"], got["mode"], got["version"], got["last"]) == (True, "manual", "2.1.274", None)
        assert a.http.post("/claude/update", json={"mode": "later"}).status_code == 400
        assert a.http.post("/claude/update", json={"now": True}).json()["updating"] is True
        deadline = time.time() + 30
        while (got := a.http.get("/claude/update").json())["updating"]:
            assert time.time() < deadline, got
            time.sleep(0.1)
        assert (got["version"], got["last"]["ok"]) == ("2.1.280", True)
        assert a.http.get("/capabilities").json()["claude_version"] == "2.1.280"   # the pages' choices follow it
        assert a.http.post("/claude/update", json={"mode": "automatic"}).json()["mode"] == "automatic"
    finally:
        a.close()


# A build of the test's own in print mode, as the probe starts it: each UserPromptSubmit hook of its --settings run
# through bash with no prefix, the settings' env over its own, given to bash as a line made from the program and its
# arguments (SHELL_FORM "line") or as bash's own arguments ("args"); then the request to its API fails.
THROUGH_BASH = '''
import json as _json, os as _os, shlex as _shlex, subprocess as _subprocess, sys as _sys
if "-p" in _sys.argv[1:]:
    with open(_sys.argv[_sys.argv.index("--settings") + 1]) as _fh:
        _given = _json.load(_fh)
    _env = dict(_os.environ, **{_k: str(_v) for _k, _v in (_given.get("env") or {}).items()})
    for _group in _given["hooks"]["UserPromptSubmit"]:
        for _hook in _group["hooks"]:
            _argv = [_hook["command"]] + [str(_a) for _a in _hook["args"]]
            _run = (["bash", "-c", _shlex.join(_argv)] if CONFIG["SHELL_FORM"] == "line"
                    else ["bash", "-c", '"$@"', "bash"] + _argv)
            _subprocess.run(_run, input="{}", text=True, timeout=30, env=_env)
    print("API Error: Connection error.", file=_sys.stderr)
    _sys.exit(1)
'''
# One that, in print mode, first starts a login bash of its own with the settings' env, as a build might to take a
# snapshot of the user's shell, and stops if that shell fails; then the stand-in's own print mode.
SHELL_OF_ITS_OWN = '''
import json as _json, os as _os, subprocess as _subprocess, sys as _sys
if "-p" in _sys.argv[1:]:
    with open(_sys.argv[_sys.argv.index("--settings") + 1]) as _fh:
        _env = dict(_os.environ, **{_k: str(_v) for _k, _v in (_json.load(_fh).get("env") or {}).items()})
    _own = _subprocess.run(["bash", "-l", "-c", "echo snapshot"], env=_env, capture_output=True, text=True)
    if _own.returncode != 0 or "snapshot" not in _own.stdout.split():
        print("its own shell failed", file=_sys.stderr)
        _sys.exit(5)
'''
# One whose `install stable` links ~/.local/bin/claude to CONFIG's ELSEWHERE, a build outside the versions folder.
LINKS_ELSEWHERE = '''
import os as _os, sys as _sys
if _sys.argv[1:3] == ["install", "stable"]:
    _link = _os.path.join(_os.environ["HOME"], ".local", "bin", "claude")
    _os.makedirs(_os.path.dirname(_link), exist_ok=True)
    if _os.path.lexists(_link):
        _os.remove(_link)
    _os.symlink(CONFIG["ELSEWHERE"], _link)
    _sys.exit(0)
'''
# One that notes each time it is run, in CONFIG's RAN.
RECORDS_A_RUN = 'open(CONFIG["RAN"], "a").close()\n'


def test_the_image_s_managed_settings_leave_the_probe_its_own_prefix_and_shell_file():
    # a workspace machine's probe runs under the image's managed settings, which outrank its --settings ("no user,
    # project, local, or --settings value overrides them", settings.py quoting Claude Code's documentation), and, not
    # measured here, its environment too: their `env` naming one
    # of these would take the place of the probe's own, and it would pass a build that wraps the gate's hook in the
    # prefix or starts it through a shell. The image's file is these settings (test_workspace_image_docker.py compares
    # the two); test_workspace_settings.py already pins their `env` whole, without saying this.
    managed = settings.claude_settings("/usr/bin/python3", "/opt/proofpane/proofpane_workspace", True)
    for name in ("CLAUDE_CODE_SHELL_PREFIX", "BASH_ENV", "ENV"):
        assert name not in managed["env"], (
            f"the image's managed settings set {name}: they outrank the updater's probe (claude_update.args_probe), "
            f"whose own {name} would not take effect, so it could pass a build that does not start the gate's hook as "
            "a program and its arguments")


def bash_sets_its_arguments_first(folder):
    """Whether the bash THROUGH_BASH starts, the first on the probe's PATH, has set its arguments when it reads
    BASH_ENV: macOS's /bin/bash, bash 3.2, has not (measured on macOS 15.7.9). Read by a BASH_ENV of its own, in
    `folder`, that writes them down, the bash's input a pipe, as THROUGH_BASH gives it: bash 3.2 given a socket for
    its input reads ~/.bashrc instead, as for a remote shell."""
    seen, env_file = folder / "seen", folder / "shows-its-arguments"
    env_file.write_text(f"printf %s \"$*\" > '{seen}'\n")
    subprocess.run(["bash", "-c", ":", "bash", "an-argument"], input="{}", text=True, capture_output=True, timeout=30,
                   env={"PATH": "/usr/bin:/bin", "HOME": str(folder), "BASH_ENV": str(env_file)}, check=True)
    return seen.read_text() == "an-argument"


@pytest.mark.parametrize("form", ["line", "args"])
def test_a_build_that_starts_the_hook_through_bash_is_refused(world, tmp_path, form):
    # a build that starts a hook given as a program and its arguments through bash,
    # with no prefix, must not pass the probe: a repository's BASH_ENV would then run before the gate. The probe's
    # BASH_ENV sees bash given the hook as a -c string or as its own arguments, and the hook does not run. A bash that
    # reads BASH_ENV before it sets its arguments, as macOS's bash 3.2 does, shows the file nothing of a hook given as
    # its own arguments; the probe runs only in a workspace machine, never on a Mac, so there alone such a bash skips
    # the case, and anywhere else the case runs, and fails if its bash is such a one
    if form == "args" and sys.platform == "darwin" and not bash_sets_its_arguments_first(tmp_path):
        pytest.skip("on a Mac, whose bash (3.2) has not set its arguments when it reads BASH_ENV, the probe's file can "
                    "see nothing of a hook given as bash's own arguments; and the product runs the probe only in a "
                    "workspace machine (--claude-updates, which only the image's start.sh gives), never on a Mac")
    home, _ = world
    candidate = fake(tmp_path / "candidate", prelude=THROUGH_BASH, VERSION="2.1.280", SHELL_FORM=form)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home)}
    assert claude_update.args_probe_result(candidate, env) == (False, claude_update.THROUGH_A_SHELL)
    got = claude_update.check(candidate, (2, 1, 274), env, str(tmp_path),
                              lambda path: chats.write_wrapper(str(tmp_path / "w"), PASS_ENV, claude=path))
    assert got == (False, "2.1.280", claude_update.THROUGH_A_SHELL, None)        # the page is told so


def test_a_shell_the_build_starts_for_itself_reads_the_probe_s_file_and_goes_on(world, tmp_path):
    # a login bash reads BASH_ENV too (bash 5.2, measured): the probe's file acts only in a shell about to run the
    # probe's hook, so a build that starts one of its own, then the hook as a program, still passes. A file that acts in
    # every shell would refuse this build
    home, _ = world
    candidate = fake(tmp_path / "candidate", prelude=SHELL_OF_ITS_OWN, VERSION="2.1.280")
    assert claude_update.args_probe_result(candidate, {"PATH": "/usr/bin:/bin", "HOME": str(home)}) == (True, None)


@pytest.mark.parametrize("prelude, config, how", [
    ("", {"HOOK_ARGS": False}, "it exited with status 1, its last line: API Error: Connection error."),
    ("import sys as _s\nif '-p' in _s.argv[1:]:\n    _s.exit(4)\n", {},
     "it exited with status 4, writing nothing to its error output"),
    ("import os as _o, signal as _g, sys as _s\nif '-p' in _s.argv[1:]:\n    _o.kill(_o.getpid(), _g.SIGTERM)\n", {},
     "it was ended by signal 15, writing nothing to its error output"),
    ("import sys as _s\nif '-p' in _s.argv[1:]:\n"
     "    _s.stderr.write('an earlier line\\n\\x1b[31m' + 'y' * 300 + '\\n\\n')\n    _s.exit(3)\n", {},
     "it exited with status 3, its last line: [31m" + "y" * 196 + "...")])
def test_the_probe_says_how_a_build_that_wrote_no_word_ended(world, tmp_path, prelude, config, how):
    # each way a build fails the probe is told apart: the page is told its exit status,
    # or the signal that ended it, and the last line of its error output, printable characters only, cut to LINE
    home, _ = world
    candidate = fake(tmp_path / "candidate", prelude=prelude, VERSION="2.1.280", **config)
    assert claude_update.args_probe_result(candidate, {"PATH": "/usr/bin:/bin", "HOME": str(home)}) == \
        (False, f"{claude_update.NO_HOOK} ({how})")


@pytest.mark.parametrize("given, kept", [(1, 1), (7, 7), (True, None), ("1", None), (1.0, None), (None, None)])
def test_the_state_keeps_the_form_of_the_check_only_as_a_whole_number(tmp_path, given, kept):
    # a JSON true is 1 to Python's ==: kept as a form, it would pass for the check's first
    put_state(str(tmp_path), active="/x", checked=given)
    assert repr(claude_update.load(str(tmp_path))["checked"]) == repr(kept)


@pytest.mark.parametrize("case", ["marked", "no mark", "an older form", "outside the versions folder", "not runnable",
                                  "gone"])
def test_the_build_the_state_names_is_taken_up_at_the_start_only_from_the_versions_folder_and_checked(
        world, tmp_path, case):
    # the start takes up only a build in Claude Code's versions folder that the state says passed the check as it now
    # is; else the image's own, the page told why, and nothing of the build's is run at the start. A start that took up
    # whatever build the state named, checked only for being a file it could run, would leave every machine updated
    # before the check took its present form a build that never passed it
    home, bin_dir = world
    floor = fake(bin_dir / "claude")
    ran = tmp_path / "ran"
    build = (fake(tmp_path / "2.1.280", prelude=RECORDS_A_RUN, VERSION="2.1.280", RAN=str(ran))
             if case == "outside the versions folder"
             else native(home, prelude=RECORDS_A_RUN, VERSION="2.1.280", RAN=str(ran)))
    if case == "not runnable":
        os.chmod(build, 0o644)
    elif case == "gone":
        os.remove(build)
    mark = {"no mark": {}, "an older form": {"checked": claude_update.CHECK_FORM - 1}}.get(
        case, {"checked": claude_update.CHECK_FORM})
    root = str(tmp_path / "ws")
    put_state(root, mode="manual", active=build, **mark)
    ws = Workspace(root, claude_updates=True)
    assert not ran.exists()
    if case == "marked":
        assert (ws.claude_path, ws.update_state["last"]) == (build, None)
        return
    assert ws.claude_path == floor and (ws.update_state["active"], ws.update_state["checked"]) == (None, None)
    last = ws.update_state["last"]
    assert (last["ok"], last["version"], last["why"]) == (False, None, claude_update.SET_ASIDE)


@pytest.mark.parametrize("passes", [True, False])
def test_a_build_set_aside_at_the_start_is_checked_by_the_next_round(world, tmp_path, passes):
    # the state names a build with no mark, as an update before the check's form was kept left it, and install stable,
    # run now by the image's own, finds that same build the newest: it is checked as a new build is, then used, or
    # removed with its link
    home, bin_dir = world
    config = {"VERSION": "2.1.280"} if passes else {"VERSION": "2.1.280", "HOOK_ARGS": False}
    floor = fake(bin_dir / "claude", INSTALLS=config)
    build = native(home, **config)
    linked(home, build)
    root = str(tmp_path / "ws")
    put_state(root, mode="automatic", active=build)
    ws = Workspace(root, claude_updates=True)
    assert ws.claude_path == floor
    info = ws.update_claude()
    state = claude_update.load(root)
    if passes:
        assert (ws.claude_path, info["last"]["ok"], info["last"]["changed"]) == (build, True, True)
        assert (state["active"], state["checked"]) == (build, claude_update.CHECK_FORM)
        assert Workspace(root, claude_updates=True).claude_path == build        # and the next start takes it up
    else:
        assert (ws.claude_path, info["last"]["ok"], info["last"]["version"]) == (floor, False, "2.1.280")
        assert info["last"]["why"].startswith(claude_update.NO_HOOK)
        assert (state["active"], state["checked"]) == (None, None)
        assert not os.path.lexists(link(home)) and not os.path.exists(build)


@pytest.mark.parametrize("passes", [True, False])
def test_the_build_in_use_found_the_newest_is_checked_unless_it_passed_the_check_as_it_now_is(
        world, tmp_path, monkeypatch, passes):
    # the build in use passed the check before the check gained a test, and install stable finds it still the newest:
    # reported unchanged only for a build that passed the check as it now is, so this one is checked, and kept, or set
    # aside for the image's own and removed with its link
    home, bin_dir = world
    config = {"VERSION": "2.1.280"} if passes else {"VERSION": "2.1.280", "HOOK_ARGS": False}
    floor = fake(bin_dir / "claude")
    build = native(home, **config)
    linked(home, build)
    root = str(tmp_path / "ws")
    put_state(root, mode="manual", active=build, checked=claude_update.CHECK_FORM)
    ws = Workspace(root, claude_updates=True)
    assert ws.claude_path == build
    monkeypatch.setattr(claude_update, "CHECK_FORM", claude_update.CHECK_FORM + 1)      # the check gains a test
    info = ws.update_claude()
    state = claude_update.load(root)
    if passes:
        assert (ws.claude_path, info["last"]["ok"], info["last"]["changed"]) == (build, True, False)
        assert (state["active"], state["checked"]) == (build, claude_update.CHECK_FORM)
        assert os.path.realpath(link(home)) == build
    else:
        assert (ws.claude_path, info["last"]["ok"], info["last"]["changed"]) == (floor, False, True)
        assert info["last"]["why"].startswith(claude_update.NO_HOOK)
        assert (state["active"], state["checked"]) == (None, None)
        assert not os.path.lexists(link(home)) and not os.path.exists(build)
        assert info["version"] == "2.1.274"                     # what the page offers, read again from the image's


def test_an_agent_whose_state_names_an_unchecked_build_checks_it_at_its_start(tmp_path):
    # the agent's own start: the build its state names with no mark is not used, and the round at the start checks it
    # (here it fails, so it is removed with its link and the image's own stays in use)
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    config = {"VERSION": "2.1.280", "HOOK_ARGS": False}
    fake(bin_dir / "claude", INSTALLS=config)
    build = native(home, **config)
    linked(home, build)
    root = tmp_path / "ws"
    put_state(str(root), mode="automatic", active=build)
    a = Agent(str(root), str(home), {"PATH": f"{bin_dir}:/usr/bin:/bin"}, args=["--claude-updates"])
    try:
        deadline = time.time() + 60
        while (got := a.http.get("/claude/update").json())["updating"] or \
                (got["last"] or {}).get("why") == claude_update.SET_ASIDE:
            assert time.time() < deadline, got
            time.sleep(0.2)
        assert (got["version"], got["last"]["ok"], got["last"]["version"]) == ("2.1.274", False, "2.1.280"), got
        assert got["last"]["why"].startswith(claude_update.NO_HOOK)
        assert not os.path.exists(build) and not os.path.lexists(link(home))
    finally:
        a.close()


def test_a_build_the_install_puts_outside_the_versions_folder_is_not_used(world, tmp_path):
    # the start takes up only a build in the versions folder (claude_update.in_use), so an update uses none from
    # elsewhere either, nor leaves it linked; the file itself is not the updater's to remove
    home, bin_dir = world
    elsewhere = fake(tmp_path / "elsewhere", VERSION="2.1.280")
    floor = fake(bin_dir / "claude", prelude=LINKS_ELSEWHERE, ELSEWHERE=elsewhere)
    ws = Workspace(str(tmp_path / "ws"), claude_updates=True)
    info = ws.update_claude()
    assert (ws.claude_path, info["last"]["ok"], info["last"]["why"]) == (floor, False, claude_update.OUTSIDE)
    assert os.path.exists(elsewhere) and not os.path.lexists(link(home))


def test_a_newer_build_that_fails_its_check_goes_and_the_link_points_at_the_build_in_use_again(world, tmp_path):
    # the install links a new build before the check, so a build refused would stay linked, and
    # a shell whose PATH puts ~/.local/bin first (which Claude Code's own setup note is said to tell users to do)
    # would run it
    home, bin_dir = world
    fake(bin_dir / "claude", INSTALLS={"VERSION": "2.1.280", "INSTALLS": {"VERSION": "2.1.290", "HOOK_ARGS": False}})
    ws = Workspace(str(tmp_path / "ws"), claude_updates=True)
    assert ws.update_claude()["last"]["ok"] is True
    build = str(versions(home) / "2.1.280")
    info = ws.update_claude()                 # 2.1.280's own install puts 2.1.290 on the volume and links it
    assert (info["last"]["ok"], info["last"]["version"], ws.claude_path) == (False, "2.1.290", build)
    assert info["last"]["why"].startswith(claude_update.NO_HOOK)
    assert os.path.islink(link(home)) and os.readlink(link(home)) == build
    assert os.listdir(versions(home)) == ["2.1.280"]


def test_an_install_that_does_not_finish_leaves_the_link_at_the_build_in_use(world, tmp_path, monkeypatch):
    # the restore followed a refusal only, so an install that linked its build and then did not finish (its time ran
    # out) left that build linked, never checked, for a shell whose PATH puts ~/.local/bin first
    home, bin_dir = world
    fake(bin_dir / "claude", INSTALLS={"VERSION": "2.1.280", "INSTALLS": {"VERSION": "2.1.290"}})
    ws = Workspace(str(tmp_path / "ws"), claude_updates=True)
    assert ws.update_claude()["last"]["ok"] is True
    build = str(versions(home) / "2.1.280")
    real = claude_update.install

    def unfinished(*args):
        real(*args)             # 2.1.280's install puts 2.1.290 on the volume and links it,
        assert os.readlink(link(home)) != build
        return None             # and then, as far as the updater can tell, does not finish
    monkeypatch.setattr(claude_update, "install", unfinished)
    info = ws.update_claude()
    assert (info["last"]["ok"], info["last"]["why"], ws.claude_path) == (False, "Claude Code's install did not finish",
                                                                        build)
    assert os.path.islink(link(home)) and os.readlink(link(home)) == build
    assert os.listdir(versions(home)) == ["2.1.280"]


def test_relink_puts_a_new_link_in_place_whole_and_leaves_what_it_cannot_change(world, tmp_path, monkeypatch):
    home, bin_dir = world
    build = native(home, VERSION="2.1.280")
    linked(home, str(tmp_path / "refused"))
    moved = []
    real = os.replace

    def replace(src, dst):
        moved.append((src, dst, os.readlink(src)))
        return real(src, dst)
    monkeypatch.setattr(claude_update.os, "replace", replace)
    claude_update.relink(str(home), build)
    assert os.readlink(link(home)) == build
    ((src, dst, to),) = moved                     # a new link made beside the old one, then put in its place
    assert (os.path.dirname(src), dst, to) == (str(link(home).parent), str(link(home)), build)
    assert os.listdir(link(home).parent) == ["claude"]
    # the image's own in use, not a build in the versions folder: the link goes
    image = fake(bin_dir / "claude")
    claude_update.relink(str(home), image)
    assert not os.path.lexists(link(home))
    # a folder where the link would be is left as it is
    link(home).mkdir()
    claude_update.relink(str(home), image)
    claude_update.relink(str(home), build)
    assert link(home).is_dir() and os.listdir(link(home).parent) == ["claude"]
