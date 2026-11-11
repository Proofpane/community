"""The README's section "What a linked session gets, client by client" says, client by client, what a session the
person runs in its vendor's own program gets once it is linked to the ADE, Claude Code first, and where Proofpane's
daemon's fixed baseline stops: a check on a call's arguments, not a sandbox.

Each client's paragraph must hold its words (WORDS) and not the words it once held wrongly (WRONG). Each fact a
paragraph states is checked below where this repository can check it, by the fact itself: the link's setup run from its
entry point, the approval hook's gate run on the acts the paragraph names and on the other words it says are not held,
the hook's settings block, the hosted workspace's settings, and the daemon beside this folder. The daemon's rows speak
of 1.5.26 and later, so they are checked only where the daemon beside is that version or later, and skipped, saying why,
where it is older or is not there, as in the open part published alone. What only a run of a client's own program would
show, this repository cannot, so the README says it as measured, with the versions, or as not measured: whether Grok
Build and the five other clients run hooks from Claude Code's settings files.
"""

import ast
import hashlib
import json
import os
import re
import shutil

import pytest

import ade_hook
import ade_link
from conftest import ROOT

HEADING = "## What a linked session gets, client by client"
REPO = os.path.dirname(ROOT)
DAEMON = os.path.join(REPO, "daemon")
OTHERS = "Gemini CLI, OpenCode, Qwen Code, Kimi Code and MiniMax Code"

# The clients the section names, in the order it names them: Claude Code first, built and checked first, then every
# other coding agent the README's opening names, and the daemon.
CLIENTS = ["Claude Code", "Codex", "Grok Build", OTHERS, "Proofpane's daemon"]

# (client, a pattern its paragraph must match)
WORDS = [
    ("Claude Code", r"`ade_link\.py setup` writes"),
    ("Claude Code", r"`\.claude/settings\.local\.json`"),
    ("Claude Code", r"merged into the same file as one block"),
    ("Claude Code", r"`ade_hook\.GATE`"),
    ("Claude Code", r"same act in other words"),
    ("Claude Code", r"every MCP tool"),
    ("Claude Code", r"in the cloud reads no `\.claude/settings\.local\.json`"),
    ("Codex", r"neither the approval hook nor the link's stop hook holds a Codex session's commands"),
    ("Codex", r"not for want of hooks in Codex"),
    ("Codex", r"Codex 0\.163\.0"),
    ("Grok Build", r"not measured"),
    ("Grok Build", r"a repository's `\.claude/settings\.json`"),
    ("Grok Build", r"`\.claude/settings\.local\.json`"),
    ("Grok Build", r"1\.0\.46 and 1\.0\.62"),
    ("Grok Build", r"`/etc/grok/requirements\.toml`"),
    ("Grok Build", r"fails open"),
    (OTHERS, r"writes no block for theirs"),
    (OTHERS, r"not measured"),
    (OTHERS, r"log and (?:its|their) commits"),
    ("Proofpane's daemon", r"not a sandbox"),
    ("Proofpane's daemon", r"the words of a shell command"),
    ("Proofpane's daemon", r"the programs `app_cli` runs"),
    ("Proofpane's daemon", r"a routed tool whose arguments the daemon does not know"),
    ("Proofpane's daemon", r"monitoring off or the daemon unpaired"),
    ("Proofpane's daemon", r"1\.5\.26"),
]

# Words the section must not say: Codex runs hooks (`ADE_FOR_SESSIONS.md`, section 4), what it lacks is a block of
# ours; whether Grok Build runs the link's hooks was not measured; and a second block beside the first drops one
# block's hooks with nothing said (`ADE_FOR_SESSIONS.md`, section 2).
WRONG = [r"no hooks API", r"Codex has no hooks", r"Codex (?:does not|cannot) run hooks",
         r"a Grok Build session's commands are not held", r"added beside them"]

# Commands the approval hook holds by `ade_hook.GATE`, one for each act the Claude Code paragraph names, and the same
# acts in the other words it says are not held.
HELD = ["git push origin main", "gh pr merge 12", "fly deploy", "npm publish", "rm -rf build"]
NOT_HELD = ["git -C dir push", "rm -r -f build"]

# The README's version of the daemon: its rows speak of this version and later.
DAEMON_VERSION = (1, 5, 26)


def _text(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as fh:
        return fh.read()


def section():
    readme = re.sub(r"\s+", " ", _text(ROOT, "README.md"))
    assert readme.count(HEADING) == 1, "the README should hold the section's heading once"
    start = readme.index(HEADING)
    end = readme.find(" ## ", start + len(HEADING))
    return readme[start:] if end < 0 else readme[start:end]


def paragraphs():
    """The section's bullets, by the bold name each opens with, in order."""
    found = []
    for part in section().split(" - **")[1:]:
        name, _, rest = part.partition("**")
        found.append((name, rest))
    return found


def test_the_section_names_each_client_claude_code_first():
    assert [name for name, _ in paragraphs()] == CLIENTS


@pytest.mark.parametrize("client, words", WORDS, ids=["%s: %s" % w for w in WORDS])
def test_each_client_s_paragraph_says_what_it_gets(client, words):
    text = dict(paragraphs()).get(client)
    assert text is not None, "the section has no paragraph for %s" % client
    assert re.search(words, text), "the paragraph for %s should match %r, and reads:\n%s" % (client, words, text)


@pytest.mark.parametrize("words", WRONG)
def test_the_section_does_not_say_what_was_wrong_or_not_measured(words):
    assert not re.search(words, section()), words


# ---- what the rows rest on: each checks the fact itself (they are the controls) ----------------------------------

def test_the_link_names_the_cloud_s_settings_it_does_not_write():
    """That a cloud session's setup writes no hook is run from setup's entry point, against the app served on a local
    port, by `test_ade_link_setup.py`'s `test_a_cloud_session_is_linked_and_no_hook_is_written`; here, the words the
    client says it with."""
    link = _text(ROOT, "ade_link.py")
    assert "a cloud session reads no .claude/settings.local.json, so no hook was written" in link


def _files_under(top):
    """Each file under `top`, by its path relative to `top`, with the digest of its bytes."""
    found = {}
    for dirpath, _dirnames, filenames in os.walk(top):
        for name in filenames:
            path = os.path.join(dirpath, name)
            with open(path, "rb") as fh:
                found[os.path.relpath(path, top)] = hashlib.sha256(fh.read()).hexdigest()
    return found


@pytest.mark.parametrize("mirror", [False, True], ids=["block", "mirror"])
def test_the_link_s_setup_writes_claude_code_s_settings_file_and_no_other(tmp_path, monkeypatch, capsys, mirror):
    """Run from its entry point as SETUP_A.md's line runs it, `ade_link.py setup` for a directory linked already (the
    hooks alone, so nothing is sent) writes the session's `.claude/settings.local.json` and no other file outside the
    link's own directory: no block for Codex, Grok Build or any of the five others, whatever their settings files are
    called and however a path to one is spelled."""
    home = tmp_path / "home"
    link_dir = home / ".proofpane-ade" / "mac"
    link_dir.mkdir(parents=True)
    shutil.copy(ade_link.__file__, home / ".proofpane-ade" / "ade_link.py")
    (link_dir / "config.json").write_text(json.dumps({"url": "https://ade.invalid", "link_id": "0123456789abcdef" * 2}))
    project = tmp_path / "project"
    project.mkdir()
    for name in ("CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "ADE_OWNER_KEYS", "ADE_LINK_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)
    before = _files_under(str(tmp_path))
    try:
        code = ade_link.main(["setup", "--name", "mac", "--kind", "local"] + (["--mirror"] if mirror else []))
    except SystemExit as e:
        code = e.code
    out, err = capsys.readouterr()
    assert code == 0, out + err
    after = _files_under(str(tmp_path))
    inside = os.path.relpath(str(link_dir), str(tmp_path)) + os.sep
    written = sorted(path for path in set(before) | set(after)
                     if before.get(path) != after.get(path) and not path.startswith(inside))
    assert written == [os.path.join("project", ".claude", "settings.local.json")], written


def test_the_approval_hook_holds_the_named_acts_and_not_their_other_words():
    for command in HELD:
        assert ade_hook.gated(command, {}) == (True, None), "the hook should hold %r" % command
    for command in NOT_HELD:
        assert ade_hook.gated(command, {}) == (False, None), "the README says %r is not held" % command


def test_the_approval_hook_s_block_is_one_block_for_bash_alone():
    """The block holds Bash commands alone, so every MCP tool is not held; it is one `env` and one `hooks`, to be merged
    into the settings file as one block."""
    block = json.loads(_text(ROOT, "ade_hook.settings.json"))
    assert sorted(block) == ["disableAllHooks", "env", "hooks"], sorted(block)
    assert sorted(block["hooks"]) == ["PreToolUse"], sorted(block["hooks"])
    assert [entry["matcher"] for entry in block["hooks"]["PreToolUse"]] == ["Bash"]
    guide = re.sub(r"\s+", " ", _text(ROOT, "ADE_FOR_SESSIONS.md"))
    assert ("a settings file takes one `env` and one `hooks`, and a second pasted beside the first drops one block's "
            "hooks with nothing said") in guide


def test_the_hosted_workspace_gives_codex_and_grok_build_their_hooks():
    settings = _text(ROOT, "proofpane_workspace", "settings.py")
    for fact in ("/etc/codex/requirements.toml", "/etc/grok/requirements.toml", "0.163.0"):
        assert fact in settings, fact


def test_grok_build_s_measurement_says_what_the_readme_says_it_measured():
    """The Grok Build paragraph rests on the measurement of 2026-10-18, its record read where it is beside this
    folder: both versions, in a git
    repository whose own `.claude/settings.json` holds a hook, ask whether to trust it before any hook runs. Nothing
    there runs a hook from `.claude/settings.local.json`, which the README says was not measured."""
    path = os.path.join(REPO, "docs", "measurements", "grok-build", "measure-grok.md")
    if not os.path.isfile(path):
        pytest.skip("the measurement record is not beside open_edition/ here")
    record = re.sub(r"\s+", " ", _text(path))
    assert "whose own `.claude/settings.json`, or whose own `.grok/hooks/`, holds a hook" in record
    assert "both versions ask \"Do you trust the contents of this directory?\"" in record
    assert "settings.local.json" not in record


def _daemon_version():
    path = os.path.join(DAEMON, "mcp_server.py")
    if not os.path.isfile(path):
        pytest.skip("daemon/ is not beside open_edition/ here")
    source = _text(path)
    found = re.search(r'^SERVER_VERSION = "(\d+)\.(\d+)\.(\d+)"$', source, re.M)
    assert found, "daemon/mcp_server.py states no SERVER_VERSION"
    version = tuple(int(n) for n in found.groups())
    if version < DAEMON_VERSION:
        pytest.skip("the daemon beside open_edition/ here is %s, older than the %s the README's daemon row speaks of"
                    % (".".join(map(str, version)), ".".join(map(str, DAEMON_VERSION))))
    return source


def _daemon(name):
    _daemon_version()
    return _text(os.path.join(DAEMON, name))


# The names whose reading would make the baseline depend on the client's monitoring switch or on the pairing; the two
# calls that read them, which alone may come before the baseline, each as `name = call()`; and the names a tool is run
# by, which the refusal must not name.
SWITCH_OR_PAIRING = {"monitoring_off", "_monitoring_disabled", "_load_pairing"}
READ_BEFORE = {"_monitoring_disabled", "_load_pairing"}
RUNS_THE_TOOL = {"handler", "TOOL_HANDLERS", "CLOUD_TOOL_HANDLERS", "get_proxy"}


def _named(node):
    """Each name a node reads, and each attribute's name (`ask_policy.monitoring_off`)."""
    found = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
            found.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            found.add(sub.attr)
    return found


def _reaching(tree, names):
    """`names`, and each function of the module that reads one of them or calls a function that does."""
    functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    reach = set(names)
    grew = True
    while grew:
        grew = False
        for fn in functions:
            if fn.name not in reach and _named(fn) & reach:
                reach.add(fn.name)
                grew = True
    return reach


def _returns(node):
    return [sub for sub in ast.walk(node) if isinstance(sub, ast.Return)]


def _is_error_result(node):
    """Whether a return's value is `_result(<id>, {..., "isError": True})`, the refusal's answer."""
    if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_result" and len(node.args) == 2
            and isinstance(node.args[1], ast.Dict)):
        return False
    return any(isinstance(key, ast.Constant) and key.value == "isError" and isinstance(value, ast.Constant)
               and value.value is True for key, value in zip(node.args[1].keys, node.args[1].values))


def test_the_daemon_s_baseline_holds_whatever_the_monitoring_switch_and_the_pairing_say():
    """The README says the daemon's fixed baseline holds whatever the client's monitoring switch says and whether or
    not the daemon is paired. Run from `_handle_tools_call` in both states, unpaired and with the switch off by a
    statement the product key signed, by `backend/tests/test_daemon_mcp_baseline.py`, beside this folder where the
    repository is whole; here, read as control flow from the server's method tables. Each table's `tools/call` is
    `_handle_tools_call`, or a function whose body, a docstring aside, is one return of `_handle_tools_call` on its own
    request that reads nothing else reaching the switch or the pairing. `_handle_tools_call` takes the baseline's
    decision, and each function that takes it does so in a statement of its own body, so no branch skips it, by
    `_system_protected_decision`, which reaches neither, on arguments that read neither. Before it, the switch and the
    pairing are read only as `name = _monitoring_disabled()` or `name = _load_pairing()`, and no statement reads the
    names those give, a function of the module that reaches them, or an attribute so named. The statement after it is
    the refusal: an `if` on the baseline that reads none of these, with no `else`, whose body names none of
    `handler`, `TOOL_HANDLERS`, `CLOUD_TOOL_HANDLERS` and `get_proxy` and ends in its one return, an error result."""
    tree = ast.parse(_daemon_version())
    reach = _reaching(tree, SWITCH_OR_PAIRING)
    functions = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    served = [value for node in ast.walk(tree) if isinstance(node, ast.Dict)
              for key, value in zip(node.keys, node.values)
              if isinstance(key, ast.Constant) and key.value == "tools/call"]
    assert served, "no method table of the daemon's names tools/call"
    for value in served:
        name = getattr(value, "id", None)
        if name == "_handle_tools_call":
            continue
        fn = functions.get(name)
        body = list(fn.body) if fn else []
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            body = body[1:]
        request = fn.args.args[0].arg if fn and fn.args.args else None
        answer = body[0].value if len(body) == 1 and isinstance(body[0], ast.Return) else None
        assert answer is not None and any(
            isinstance(sub, ast.Call) and getattr(sub.func, "id", None) == "_handle_tools_call"
            and [ast.unparse(arg) for arg in sub.args] == [request] and not sub.keywords for sub in ast.walk(answer)), (
            "tools/call is served by %s, which is not one return of _handle_tools_call on its request"
            % ast.unparse(value))
        assert not (_named(answer) & reach) - {"_handle_tools_call"}, (
            "%s reads the switch or the pairing beside _handle_tools_call" % name)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and getattr(node.func, "id", None) == "_system_protected_decision"]
    assert calls, "no function of the daemon's takes the baseline's decision"
    assert "_system_protected_decision" not in reach, "the baseline's decision reads the switch or the pairing"
    taken = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for i, stmt in enumerate(fn.body):
            if (isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call)
                    and getattr(stmt.value.func, "id", None) == "_system_protected_decision"
                    and [ast.unparse(target) for target in stmt.targets] == ["baseline"]):
                taken.append((fn, i))
    assert len(taken) == len(calls), (
        "the baseline's decision is taken in %d place(s), %d of them a statement of its function's own body"
        % (len(calls), len(taken)))
    assert "_handle_tools_call" in {fn.name for fn, _ in taken}, "_handle_tools_call does not take the baseline"
    for fn, i in taken:
        decision = fn.body[i]
        given = list(decision.value.args) + [keyword.value for keyword in decision.value.keywords]
        assert not any(_named(arg) & reach for arg in given), (
            "%s's baseline at line %d is given the switch or the pairing" % (fn.name, decision.lineno))
        read = set(reach)
        for stmt in fn.body[:i]:
            if not _named(stmt) & read:
                continue
            assert (isinstance(stmt, ast.Assign) and all(isinstance(t, ast.Name) for t in stmt.targets)
                    and isinstance(stmt.value, ast.Call) and getattr(stmt.value.func, "id", None) in READ_BEFORE
                    and not stmt.value.args and not stmt.value.keywords), (
                "%s reads the switch or the pairing at line %d, before the baseline at line %d, other than as "
                "name = _monitoring_disabled() or name = _load_pairing()" % (fn.name, stmt.lineno, decision.lineno))
            read.update(target.id for target in stmt.targets)
        refusal = fn.body[i + 1] if i + 1 < len(fn.body) else None
        assert isinstance(refusal, ast.If) and "baseline" in _named(refusal.test), (
            "%s does not refuse on the baseline right after taking it" % fn.name)
        assert not _named(refusal.test) & read, "%s refuses on the switch or the pairing too" % fn.name
        assert isinstance(refusal.body[-1], ast.Return) and not refusal.orelse, (
            "%s's refusal does not end in a return" % fn.name)
        assert len(_returns(refusal)) == 1, "%s's refusal holds another return" % fn.name
        assert not _named(refusal) & RUNS_THE_TOOL, (
            "%s's refusal names %s" % (fn.name, ", ".join(sorted(_named(refusal) & RUNS_THE_TOOL))))
        assert _is_error_result(refusal.body[-1].value), "%s's refusal does not answer with an error" % fn.name


def test_the_daemon_s_baseline_judges_a_shell_command_by_its_dangerous_patterns_alone():
    policy = _daemon("policy.py")
    assert 'hit = _check_dangerous_bash((args.get("command") or "").strip())' in policy
    keys = policy.split("_PATH_ARG_KEYS: Dict[str, List[str]] = {", 1)[1].split("}", 1)[0]
    for tool in ('"bash"', '"app_cli"'):
        assert tool not in keys, "the baseline reads %s's arguments as paths now: the README's daemon row" % tool


def test_the_daemon_runs_app_cli_s_programs():
    handlers = _daemon("tool_handlers.py")
    assert '"app_cli": tool_app_cli' in handlers
    allow = handlers.split("APP_CLI_ALLOWLIST = {", 1)[1].split("}", 1)[0]
    for program in ('"python3"', '"node"', '"curl"'):
        assert program in allow, program
