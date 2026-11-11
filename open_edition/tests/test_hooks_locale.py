"""Every hook interpreter the owner's blocks and the workspace's settings start, run under a locale whose character set
Python has no codec for.

A settings file's `env` can name the locale (LC_ALL, LC_CTYPE, LANG), and Python then ends during its start, before
any of a hook's code runs, with "Fatal Python error: init_fs_encoding" and exit 1. A hook that exits 1 holds nothing:
the approval hook would let every gated command run, the stop hook would miss a stop the owner sets after that, and
the workspace's gate would not run. So each of them starts its interpreter with -X utf8, Python's UTF-8 mode, which
takes no character set from the locale: the owner's blocks after -I, and the workspace's settings in settings.ISOLATED.

The locale is hy_AM.ARMSCII-8 (Armenian in ARMSCII-8, one of glibc's supported locales whose character set Python has
no codec for), built with localedef into the test's own folder and named to the interpreter through LOCPATH, as on a
machine that has it installed in its system's own places: the blocks set LOCPATH empty, which keeps a repository's
own locale files out, not the system's. Each start is run twice: as the block or the settings give it, which answers,
and, as the control, the same start without -X utf8, which ends with Python's fatal error. The interpreter's own start
is also run under each of the three of glibc 2.39's supported locales whose character set Python has no codec for,
with and without -X utf8 (its list, /usr/share/i18n/SUPPORTED, has 509 entries and these three of that kind, by
codecs.lookup of each character set under Python 3.13.14). Where
localedef, a locale's source or its character map is missing (a Mac, a slim image), or the C library does not take
the locale from LOCPATH (musl reads MUSL_LOCPATH, a Mac PATH_LOCALE), the tests
here are skipped, saying why. Whether the locale is in effect is asked of the C library itself, through its own
`locale charmap`, never of Python's start, which is what the tests check: asked of an interpreter started in UTF-8
mode, a start that UTF-8 mode no longer kept would have been reported as a skip."""

import codecs
import json
import os
import shlex
import shutil
import subprocess
import sys

import pytest

import ade_link
import hook_templates
from conftest import ROOT
from proofpane_workspace import providers, settings

LOCALE = "hy_AM.ARMSCII-8"
# the three of glibc 2.39's supported locales whose character set Python has no codec for, as (source, character map)
LOCALES = (("hy_AM", "ARMSCII-8"), ("ka_GE", "GEORGIAN-PS"), ("zh_TW", "EUC-TW"))
FATAL = "Fatal Python error"
LINK_ID = "a" * 32
GATED = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git push origin main"}, "session_id": "s1",
                    "cwd": "/w"})
READ = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "/x"}})
CODESET = "import locale; print(locale.nl_langinfo(locale.CODESET))"


def _locale(tmp_path_factory, source, charmap):
    """`source` in `charmap`, built here with localedef into a folder of its own: (a LOCPATH for it, its name). Skipped,
    saying why, where this Python has a codec for the character set, localedef is missing or cannot build the locale,
    or the C library does not take the locale from that folder, which its own `locale charmap`, run in the environment
    the tests give the locale, shows by the character set it prints: a test whose locale is not in effect would see
    its control start, and fail, where it should say it cannot run here. No interpreter's start decides it, since that
    start is what the tests check."""
    name = f"{source}.{charmap}"
    try:
        codecs.lookup(charmap)
    except LookupError:
        pass
    else:
        pytest.skip(f"this Python has a codec for {charmap}, so {name} would not end its start")
    localedef = shutil.which("localedef")
    if localedef is None:
        pytest.skip("needs localedef to build a locale whose character set Python has no codec for")
    folder = tmp_path_factory.mktemp("locales")
    made = subprocess.run([localedef, "-i", source, "-f", charmap, str(folder / name)], capture_output=True, text=True,
                          timeout=120)
    if not (folder / name / "LC_CTYPE").exists():          # localedef also ends 1 for warnings: what it made decides
        pytest.skip(f"localedef could not build {name}, its source ({source}) or the {charmap} character map being "
                    f"missing here: {made.stderr.strip()[:300]}")
    program = shutil.which("locale")
    if program is None:
        pytest.skip(f"needs the C library's `locale` program to tell whether {name} is in effect")
    home = str(tmp_path_factory.mktemp("home"))
    taken = subprocess.run([program, "charmap"], capture_output=True, text=True,
                           env=_under(folder, {"PATH": "/usr/bin:/bin", "HOME": home}, name), timeout=60)
    if taken.stdout.strip() != charmap:
        pytest.skip(f"the C library here does not take {name} from LOCPATH (`locale charmap` prints "
                    f"{taken.stdout.strip()[:200]!r}), so the locale cannot be put in effect")
    return folder, name


@pytest.fixture(scope="module")
def no_codec(tmp_path_factory):
    """The folder holding hy_AM.ARMSCII-8, built here with localedef: a LOCPATH for it (_locale)."""
    return _locale(tmp_path_factory, "hy_AM", "ARMSCII-8")[0]


def _under(folder, env, name=LOCALE):
    """`env` with the locale named and its files found where a machine that has it installed would find them."""
    return dict(env, LOCPATH=str(folder), LC_ALL=name)


def _without_utf8(argv):
    """The control's start: the same program and arguments with -X utf8 taken out."""
    at = argv.index("-X")
    assert argv[at + 1] == "utf8", argv
    return argv[:at] + argv[at + 2:]


def _line_without_utf8(line):
    assert line.count(" -X utf8 ") == 1, line
    return line.replace(" -X utf8 ", " ")


def _ended(r, charmap="ARMSCII-8"):
    """The control: Python's fatal error during its start, the character set named, exit 1, nothing on standard
    output."""
    return r.returncode == 1 and FATAL in r.stderr and charmap in r.stderr and r.stdout == ""


@pytest.mark.parametrize("source, charmap", LOCALES)
def test_the_interpreter_s_start_ends_under_each_such_locale_and_not_in_utf8_mode(tmp_path_factory, source, charmap,
                                                                                   own_home):
    """Under each of glibc's supported locales whose character set Python has no codec for, the interpreter's start ends
    with Python's fatal error and exit 1, and in UTF-8 mode it starts, the locale in effect, its character set the one
    the C library reports (what the documents say of the locale rests on these three, and on the hooks run below under
    the first). The start in UTF-8 mode is this assertion alone, never also the fixture's reason to skip, so it can
    fail."""
    folder, name = _locale(tmp_path_factory, source, charmap)
    env = _under(folder, {"PATH": "/usr/bin:/bin", "HOME": own_home}, name)
    started = subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", CODESET], capture_output=True, text=True,
                             env=env, timeout=60)
    assert (started.returncode, started.stdout) == (0, charmap + "\n"), started.stderr
    control = subprocess.run([sys.executable, "-I", "-c", "print('started')"], capture_output=True, text=True, env=env,
                             timeout=60)
    assert _ended(control, charmap), (control.returncode, control.stdout, control.stderr)


def _decision(r):
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["hookSpecificOutput"]


def _approval_argv(hook):
    """The approval hook's program as Claude Code starts it, its two paths filled: this interpreter and this file."""
    paths = {"/absolute/path/to/python3": sys.executable,
             "/absolute/path/to/open_edition/ade_hook.py": os.path.join(hook_templates.HERE, "ade_hook.py")}
    return [paths.get(part, part) for part in hook_templates.program(hook)]


@pytest.mark.parametrize("name", ["ade_hook.settings.json", "ade_link_and_hook.settings.json"])
def test_the_approval_hook_denies_a_gated_command_under_a_locale_python_has_no_codec_for(no_codec, tmp_path, name):
    """The block's `env` as the template gives it (no ADE address filled), so a gated command is denied: the hook
    deciding, not failing. Without -X utf8 the same start ends before the hook runs, and its exit 1 holds nothing."""
    template = hook_templates.template(name)
    (block,) = [b for b in template["hooks"]["PreToolUse"] if b["matcher"] == "Bash"]
    argv = _approval_argv(block["hooks"][0])
    assert argv[1:4] == ["-I", "-X", "utf8"], argv
    env = _under(no_codec, dict(template["env"], PATH="/usr/bin:/bin", HOME=str(tmp_path)))
    r = subprocess.run(argv, input=GATED, capture_output=True, text=True, env=env, timeout=60)
    out = _decision(r)
    assert out["permissionDecision"] == "deny" and "no ADE address" in out["permissionDecisionReason"], out
    control = subprocess.run(_without_utf8(argv), input=GATED, capture_output=True, text=True, env=env, timeout=60)
    assert _ended(control), (control.returncode, control.stdout, control.stderr)


def _owner_home(tmp_path):
    """The owner's home folder as the link block names it: the client copied to ~/.proofpane-ade, and no link directory
    for the link the block names, so the stop hook holds every tool and resume says it has no link."""
    home = tmp_path / "home"
    (home / ".proofpane-ade").mkdir(parents=True)
    shutil.copy(ade_link.__file__, home / ".proofpane-ade" / "ade_link.py")
    return home


@pytest.mark.parametrize("name", ["ade_link.settings.json", "ade_link_and_hook.settings.json"])
def test_the_link_block_s_hooks_answer_under_a_locale_python_has_no_codec_for(no_codec, tmp_path, name):
    """Each of the link block's four starts of the client, filled as SETUP_A.md says, with the block's `env`: the stop
    hook as a program, and the command line beside it, each deny every tool while the link's directory is missing; the
    SessionStart line says resume has no link; the Stop line ends 0, its interpreter having started. Each control, the
    same start without -X utf8, ends with Python's fatal error, which the line beside the stop hook answers with its own
    hold (exit 2) and the Stop line with its `|| true`."""
    home = _owner_home(tmp_path)
    template = hook_templates.template(name)
    env = _under(no_codec, dict(template["env"], PATH="/usr/bin:/bin", HOME=str(home)))
    (pre,) = [b for b in template["hooks"]["PreToolUse"] if b["matcher"] == "*"]
    program, line = pre["hooks"]

    argv = hook_templates.argv(program, sys.executable, str(home), "mac", LINK_ID)
    assert argv[1:4] == ["-I", "-X", "utf8"], argv
    r = subprocess.run(argv, input=READ, capture_output=True, text=True, env=env, timeout=60)
    assert _decision(r)["permissionDecision"] == "deny", r.stdout
    control = subprocess.run(_without_utf8(argv), input=READ, capture_output=True, text=True, env=env, timeout=60)
    assert _ended(control), (control.returncode, control.stdout, control.stderr)

    def shell(hook, stdin, plain=False):
        text = hook_templates.line(hook, sys.executable, str(home), "mac", LINK_ID)
        return subprocess.run(["/bin/sh", "-c", _line_without_utf8(text) if plain else text], input=stdin,
                              capture_output=True, text=True, env=env, timeout=60)

    r = shell(line, READ)
    assert _decision(r)["permissionDecision"] == "deny" and FATAL not in r.stderr, (r.stdout, r.stderr)
    control = shell(line, READ, plain=True)
    assert control.returncode == 2 and FATAL in control.stderr and "could not run (exit 1)" in control.stderr, (
        control.stdout, control.stderr)

    (start,) = template["hooks"]["SessionStart"][0]["hooks"]
    r = shell(start, "{}")
    assert r.returncode == 0 and r.stdout.startswith("ADE: resume failed (no link in "), (r.stdout, r.stderr)
    control = shell(start, "{}", plain=True)
    assert _ended(control), (control.returncode, control.stdout, control.stderr)

    stop, prompt = template["hooks"]["Stop"][0]["hooks"]
    r = shell(stop, json.dumps({"session_id": "s1"}))
    assert (r.returncode, r.stdout, r.stderr) == (0, "", ""), r.stderr
    control = shell(stop, json.dumps({"session_id": "s1"}), plain=True)
    assert control.returncode == 0 and FATAL in control.stderr, control.stderr

    # the prompt hook, as a program on each of its events: it starts, finds no link's
    # directory and prints nothing; without -X utf8 its start ends as every other one does
    for event in ("PermissionRequest", "Stop", "UserPromptSubmit"):
        (hook,) = [h for b in template["hooks"][event] for h in b["hooks"] if h.get("args", [""])[-1] == "permission"]
        argv = hook_templates.argv(hook, sys.executable, str(home), "mac", LINK_ID)
        assert argv[1:4] == ["-I", "-X", "utf8"], argv
        given = json.dumps({"hook_event_name": event, "session_id": "s1", "tool_name": "Bash",
                            "tool_input": {"command": "ls"}})
        r = subprocess.run(argv, input=given, capture_output=True, text=True, env=env, timeout=60)
        assert (r.returncode, r.stdout, r.stderr) == (0, "", ""), (event, r.stderr)
        control = subprocess.run(_without_utf8(argv), input=given, capture_output=True, text=True, env=env,
                                 timeout=60)
        assert _ended(control), (event, control.returncode, control.stdout, control.stderr)


@pytest.mark.parametrize("managed", [True, False])
def test_the_workspace_s_hooks_answer_under_a_locale_python_has_no_codec_for(no_codec, tmp_path, managed):
    """The gate refuses a call it refuses, an activity hook records its state, the status line prints its line, and
    the key helper prints the key kept, each started as the settings start it (settings.ISOLATED, with -X utf8);
    without -X utf8 each ends with Python's fatal error, the gate's call let through by its exit 1."""
    directory = os.path.join(ROOT, "proofpane_workspace")
    made = settings.claude_settings(sys.executable, directory, managed)
    env = _under(no_codec, {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PP_SESSION_DIR": str(tmp_path)})
    credentials = json.dumps({"tool_name": "Bash", "tool_input": {"command": "cat ~/.claude/.credentials.json"}})

    gate = made["hooks"]["PreToolUse"][0]["hooks"][0]
    argv = [gate["command"], *gate["args"]]
    assert argv[1:5] == ["-E", "-s", "-X", "utf8"], argv
    r = subprocess.run(argv, input=credentials, capture_output=True, text=True, env=env, timeout=60)
    assert _decision(r)["permissionDecision"] == "deny", r.stdout
    control = subprocess.run(_without_utf8(argv), input=credentials, capture_output=True, text=True, env=env,
                             timeout=60)
    assert _ended(control), (control.returncode, control.stdout, control.stderr)

    stop = made["hooks"]["Stop"][0]["hooks"][0]
    state = tmp_path / "activity.json"
    argv = [stop["command"], *stop["args"]]
    control = subprocess.run(_without_utf8(argv), input=json.dumps({"session_id": "s1"}), capture_output=True,
                             text=True, env=env, timeout=60)
    assert _ended(control) and not state.exists(), (control.returncode, control.stderr)
    r = subprocess.run(argv, input=json.dumps({"session_id": "s1"}), capture_output=True, text=True, env=env,
                       timeout=60)
    assert r.returncode == 0 and json.loads(state.read_text())["state"] == "idle", r.stderr

    status = made["statusLine"]["command"]
    r = subprocess.run(["/bin/sh", "-c", status], input=json.dumps({"model": {"display_name": "Some model"}}),
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0 and "Some model" in r.stdout and FATAL not in r.stderr, (r.stdout, r.stderr)
    control = subprocess.run(["/bin/sh", "-c", _line_without_utf8(status)],
                             input=json.dumps({"model": {"display_name": "Some model"}}), capture_output=True,
                             text=True, env=env, timeout=60)
    assert _ended(control), (control.returncode, control.stdout, control.stderr)

    providers.save(str(tmp_path), "kimi", "test-only-key-0123456789")
    helper = providers.helper_command(sys.executable, directory, "kimi")
    assert shlex.split(helper)[1:5] == ["-E", "-s", "-X", "utf8"], helper
    r = subprocess.run(["/bin/sh", "-c", helper], capture_output=True, text=True, env=env, timeout=60)
    assert (r.returncode, r.stdout) == (0, "test-only-key-0123456789"), r.stderr
    control = subprocess.run(["/bin/sh", "-c", _line_without_utf8(helper)], capture_output=True, text=True, env=env,
                             timeout=60)
    assert _ended(control), (control.returncode, control.stdout, control.stderr)
