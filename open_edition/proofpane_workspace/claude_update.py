"""Claude Code kept current inside the workspace, without a new image, so that what the app offers follows the claude
in use.

The image's own claude is the floor. Claude Code's own `claude install stable` puts a native build on the workspace's
volume (~/.local/share/claude/versions/<v>, linked from ~/.local/bin/claude; about 224 MB, in 17 s, checked in the
image on 2026-09-29). A build is used only when it is in that versions folder and passes a check (check()): its version
is at least the floor's, its help parses (claude_cli.py), it starts a hook given as a program and its arguments, the
form the gate's hooks take, with no shell prefix and not through a shell that reads BASH_ENV or ENV first, in the
forms args_probe names, and the Claude Agent SDK completes its handshake with it (nothing signed in is needed for
that). The build in use is named by its versioned path, which a later install never changes; other versions are
removed once a new one is in use. A build that fails the check is not used: it is removed, ~/.local/bin/claude points
at the build in use again (or is removed while that is the image's own), and the page says why.

The state, in <root>/claude-update.json: the mode (`automatic`, the default, or `manual`: only when the page says), the
build in use, the form of the check it passed (`checked`, CHECK_FORM), and the last attempt. At the agent's start the
build the state names is taken up only when it is a file in the versions folder and the state says it passed the check
as it now is (in_use); otherwise the image's own is used, and the next update round (at the start when the mode is
automatic, else when the page asks) checks the build again. A round whose install finds the build in use still the
newest checks it too, unless it is the image's own or the state says it passed the check as it now is.

What this holds against: a build an earlier update installed before the check took its present form (checked again
before it is used), a build that fails the check (removed, and no longer linked), and a state naming a build outside
the versions folder (not used). Not a program of a session, which runs as the workspace's user: it can write the state
file and the versions folder, and a build it puts there can print any version, so against it neither this rule nor a
version read at a start shows anything.
"""

import json
import os
import secrets
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

from . import claude_cli, watch

STATE = "claude-update.json"
MODES = ("automatic", "manual")
EVERY = 24 * 3600
INSTALL_TIMEOUT = 600
# the form of the check (check()) a build passed, written beside it in the state: a build named there with another
# form, or with none (a state written before the form was kept), is checked again before it is used. Moved on whenever
# check() gains a test, so that the build in use is one that passed it.
CHECK_FORM = 1
PROBE_SECONDS = 60
PROBE_WORD = "started-with-its-arguments"       # what only a hook started with its arguments writes
PROBE_API = "http://127.0.0.1:9"                # a closed port on the machine's own loopback: no model is reached
# the probe's hook: Python, isolated, writing the word it is given to the file it is given
PROBE_CODE = "import sys; open(sys.argv[1], 'w').write(sys.argv[2])"
# a shell prefix that runs nothing: a build that wrapped a hook given as a program in it, as it wraps a command line,
# would never start the hook, so the probe would find no word
PROBE_PREFIX = "/usr/bin/false"
# what the probe's shell file (_shell_env) writes, in a shell about to run the probe's hook: the build started the hook
# through a shell that reads BASH_ENV or ENV before it, as a repository's settings may set them
SHELL_WORD = "started-through-a-shell"
TAIL = 4096         # the last bytes of the build's error output read for its last line
LINE = 200          # that line, at most, in the reason the page shows
NO_HOOK = ("it did not start a hook given as a program and its arguments, outside any shell prefix, the form the "
           "gate's hooks take, so the gate would not run")
THROUGH_A_SHELL = ("it started a hook given as a program and its arguments through a shell, which reads BASH_ENV or "
                   "ENV first, so a repository's settings could run code before the gate")
# why a build the state named is not taken up at the agent's start (Workspace), in the page's words for the last attempt
SET_ASIDE = ("the build named in use is not a build in Claude Code's versions folder that passed the check as it now "
             "is")
OUTSIDE = "Claude Code's install put its build outside its versions folder"


def native_dir(home):
    return os.path.join(home, ".local", "share", "claude", "versions")


def link_path(home):
    """Where Claude Code's install links the build it put on the volume, and a shell finds it once ~/.local/bin is on
    its PATH."""
    return os.path.join(home, ".local", "bin", "claude")


def is_native(home, path):
    """Whether `path` is a build Claude Code's install put on the volume: by its real path, a regular file this user may
    run, directly in the versions folder (native_dir). What the file system says, nothing run."""
    if not isinstance(path, str) or not path:
        return False
    real = os.path.realpath(path)
    return (os.path.dirname(real) == os.path.realpath(native_dir(home)) and os.path.isfile(real)
            and os.access(real, os.X_OK))


def _form(value):
    """A form of the check as the state may hold it: a whole number, never a JSON true, which Python takes for 1."""
    return value if type(value) is int else None


def load(root):
    try:
        with open(os.path.join(root, STATE), encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        state = {}
    state = state if isinstance(state, dict) else {}
    return {"mode": state.get("mode") if state.get("mode") in MODES else "automatic",
            "active": state.get("active") if isinstance(state.get("active"), str) else None,
            "checked": _form(state.get("checked")),
            "last_check": state.get("last_check") if isinstance(state.get("last_check"), (int, float)) else 0,
            "last": state.get("last") if isinstance(state.get("last"), dict) else None}


def in_use(state, home):
    """The build the state names as in use, by its real path, when it may be taken up: a native build (is_native) the
    state says passed the check as it now is (CHECK_FORM). None otherwise, and the image's own is used until the next
    update round checks the build again. Only file system calls: the agent asks it at its start."""
    active = state.get("active")
    if _form(state.get("checked")) != CHECK_FORM or not is_native(home, active):
        return None
    return os.path.realpath(active)


def save(root, state):
    path = os.path.join(root, STATE)
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1)
    os.replace(path + ".tmp", path)
    watch.wrote(path)


def version_text(v):
    return ".".join(str(x) for x in v) if v else None


def _probe_written(mark, word=PROBE_WORD):
    """Whether `word` was written to `mark`: read only as a regular file, through no link, and never waiting, so that
    nothing put in its place (a FIFO, say) can hold the probe up."""
    try:
        fd = os.open(mark, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return False
    try:
        return stat.S_ISREG(os.fstat(fd).st_mode) and os.read(fd, len(word) + 1) == word.encode()
    except OSError:
        return False
    finally:
        os.close(fd)


def _shell_env(mark):
    """The file the probe names in BASH_ENV and ENV: in a shell about to run the probe's hook, one whose command (its
    -c string, BASH_EXECUTION_STRING) or arguments carry PROBE_WORD, it writes SHELL_WORD to `mark` and ends the shell,
    so the hook does not run. Any other shell the build starts reads it and goes on, so no build is refused for a shell
    of its own, such as one to take a snapshot of the user's shell (2.1.274 keeps a shell-snapshots store): a
    non-interactive bash reads BASH_ENV, a login one included, unless it is in POSIX mode or named sh (bash 5.2,
    measured)."""
    return (f'case "${{BASH_EXECUTION_STRING-}} $0 $*" in *{PROBE_WORD}*) printf %s {SHELL_WORD} > '
            f"{shlex.quote(mark)}; exit 97;; esac\n")


def _last_line(fd):
    """The last line the build wrote to its error output, from the probe's own descriptor of the file (never its path,
    so nothing put in its place is read) and never waiting: of its last TAIL bytes, printable characters only, cut to
    LINE. Empty when it wrote nothing."""
    try:
        size = os.fstat(fd).st_size
        tail = os.pread(fd, TAIL, max(0, size - TAIL))
    except OSError:
        return ""
    lines = [line for line in tail.decode("utf-8", "replace").splitlines() if line.strip()]
    if not lines:
        return ""
    line = "".join(ch for ch in lines[-1] if ch.isprintable()).strip()
    return line if len(line) <= LINE else line[:LINE] + "..."


def _what_happened(code, err):
    """How a build that wrote no word ended, for the page: its exit status, the signal that ended it, or that it was
    still running at PROBE_SECONDS; and the last line of its error output."""
    if code is None:
        how = f"it was still running after {PROBE_SECONDS} seconds"
    elif code < 0:
        how = f"it was ended by signal {-code}"
    else:
        how = f"it exited with status {code}"
    said = _last_line(err)
    return f"{how}, its last line: {said}" if said else f"{how}, writing nothing to its error output"


def _end(p):
    """The build's process group ended: the build, which leads it (it was started in a session of its own), and what
    it started that stayed in the group; then the build waited for, at most 10 seconds."""
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def args_probe_result(candidate, env):
    """(True, None) when `candidate` starts a hook given as a program and its arguments (`args`), the form the
    workspace's gate takes (settings.py), as that: outside any shell prefix and through no shell. (False, why) when it
    does not: a build that wrapped the hook in a prefix, or started its interpreter alone, would not run the gate, and
    one that started it through a shell would run a repository's BASH_ENV or ENV before it.

    The build runs once in print mode, in a folder of its own that is also its config folder (CLAUDE_CONFIG_DIR: no
    settings, hook or MCP server of the user's is loaded, and nothing it writes stays), with no MCP server
    (--strict-mcp-config), no nonessential traffic, and its API pointed at a closed port on the machine's own loopback,
    so it reaches no model and spends nothing. Its settings file holds one UserPromptSubmit hook in that form, which
    runs before any request to a model; in its `env` and in the build's own: a shell prefix that runs nothing
    (PROBE_PREFIX), and BASH_ENV and ENV naming a file that, in a shell about to run the probe's hook, writes its own
    mark and ends the shell (_shell_env). Measured with bash 5.2 and dash, that sees a bash that is not interactive (and
    not in POSIX mode or named sh), which reads BASH_ENV, given the hook as a -c string or as its arguments; and an
    interactive POSIX sh, which reads ENV, given the hook as its arguments, or as a -c string when the sh is bash (in
    POSIX mode, or named sh). It does not see an interactive dash given the hook as a -c string, which shows the file
    nothing of it, a shell given the hook on its input, or zsh, which reads files of its own. A shell that reads neither
    variable's file (dash, or bash in POSIX mode or named sh, when not interactive) runs no repository's BASH_ENV or ENV
    either.

    What it measures is how a build starts a hook given so, on UserPromptSubmit from flag settings (--settings). The
    gate itself is a PreToolUse hook from the image's managed settings; that it holds with a build the updater installs
    is measured with the real claude by the image's Docker update check, which has that build try to read the sign-in
    against a stand-in Messages API and sees the gate refuse it (test_workspace_image_docker.py). The image's managed
    settings set none of CLAUDE_CODE_SHELL_PREFIX, BASH_ENV and ENV: they outrank the probe's --settings (settings.py
    quotes Claude Code's documentation), so a value there would take the place of the probe's own; a test pins that.

    Passed once the hook has written its word (the probe does not wait for the build to end). Otherwise the reason says
    how the build ended: its exit status, or the signal, or that it was still running at PROBE_SECONDS, and the last
    line of its error output. A build that starts the program alone writes nothing: the interpreter reads the hook's
    input as its code and exits. The build's process group is ended when the probe is done."""
    folder = p = err = None
    try:
        folder = tempfile.mkdtemp(prefix="pp-args-probe-")
        config = os.path.join(folder, "config")
        os.mkdir(config, 0o700)
        mark, shell_mark = os.path.join(folder, "mark"), os.path.join(folder, "shell")
        shell_env = os.path.join(folder, "shell-env")
        with open(shell_env, "w", encoding="utf-8") as fh:
            fh.write(_shell_env(shell_mark))
        variables = {"CLAUDE_CODE_SHELL_PREFIX": PROBE_PREFIX, "BASH_ENV": shell_env, "ENV": shell_env}
        settings = os.path.join(folder, "settings.json")
        hook = {"type": "command", "command": sys.executable, "args": ["-I", "-c", PROBE_CODE, mark, PROBE_WORD],
                "timeout": PROBE_SECONDS}
        with open(settings, "w", encoding="utf-8") as fh:
            json.dump({"disableAllHooks": False, "env": variables,
                       "hooks": {"UserPromptSubmit": [{"hooks": [hook]}]}}, fh)
        err = os.open(os.path.join(folder, "stderr"), os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as e:
        if folder is not None:
            shutil.rmtree(folder, ignore_errors=True)
        return False, f"the probe could not be set up ({type(e).__name__})"
    try:
        try:
            p = subprocess.Popen([candidate, "-p", "Say done.", "--settings", settings, "--strict-mcp-config"],
                                 cwd=folder, env=dict(env, ANTHROPIC_API_KEY="pp-probe-not-a-key",
                                                      ANTHROPIC_BASE_URL=PROBE_API, CLAUDE_CONFIG_DIR=config,
                                                      CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", **variables),
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err,
                                 start_new_session=True)
        except OSError as e:
            return False, f"it could not be started ({type(e).__name__})"
        deadline = time.monotonic() + PROBE_SECONDS
        while time.monotonic() < deadline and p.poll() is None and not _probe_written(mark) \
                and not _probe_written(shell_mark, SHELL_WORD):
            time.sleep(0.1)
        code = p.poll()
        _end(p)
        if _probe_written(shell_mark, SHELL_WORD):
            return False, THROUGH_A_SHELL
        if _probe_written(mark):
            return True, None
        return False, f"{NO_HOOK} ({_what_happened(code, err)})"
    except OSError as e:
        return False, f"the probe failed ({type(e).__name__})"
    finally:
        if p is not None:
            _end(p)
        os.close(err)
        shutil.rmtree(folder, ignore_errors=True)


def args_probe(candidate, env):
    """args_probe_result's answer alone: whether `candidate` starts a hook given as a program and its arguments as
    that."""
    return args_probe_result(candidate, env)[0]


def check(candidate, floor_version, env, cwd, make_wrapper):
    """Whether a build may be used: (True, its version, None, what its handshake answered), or (False, its version or
    None, why not, None). `make_wrapper(path)` writes the wrapper the SDK starts in place of the build."""
    version = claude_cli.parse_version(claude_cli.run_text(candidate, env, cwd, "--version"))
    if version is None:
        return False, None, "its version could not be read", None
    if floor_version is not None and version < floor_version:
        return False, version_text(version), f"it is older than the image's {version_text(floor_version)}", None
    efforts, modes = claude_cli.parse_help(claude_cli.run_text(candidate, env, cwd, "--help"))
    if efforts is None or modes is None:
        return False, version_text(version), "its help does not say its efforts and permission modes", None
    passed, why = args_probe_result(candidate, env)
    if not passed:
        return False, version_text(version), why, None
    try:
        info = claude_cli.handshake(make_wrapper(candidate), cwd)
    except Exception as e:  # noqa: BLE001  any way it fails, it is not used
        return False, version_text(version), f"the Claude Agent SDK could not start it ({type(e).__name__})", None
    return True, version_text(version), None, info


def install(claude, env, cwd, home):
    """Claude Code's own `install stable`, run with the claude in use; the versioned path it links to, or None."""
    try:
        subprocess.run([claude, "install", "stable"], cwd=cwd, env=env, capture_output=True, timeout=INSTALL_TIMEOUT,
                       stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    link = link_path(home)
    target = os.path.realpath(link) if os.path.lexists(link) else None
    if target is None or not os.path.isfile(target) or not os.access(target, os.X_OK):
        return None
    return target


def relink(home, build):
    """~/.local/bin/claude pointed at `build` when that is a native build (is_native), else removed: after a build is
    refused, so that a shell whose PATH puts ~/.local/bin first runs the build in use and not the one refused. The new
    link is made beside the old one and put in its place whole. Best effort: what cannot be done is left as it was."""
    link = link_path(home)
    try:
        if is_native(home, build):
            tmp = f"{link}.{secrets.token_hex(8)}.tmp"
            os.symlink(os.path.realpath(build), tmp)
            try:
                os.replace(tmp, link)
            except OSError:
                os.unlink(tmp)
                raise
        else:
            os.remove(link)
    except OSError:
        pass


def remove_others(home, keep):
    """Every version but the one in use, `keep` (all of them when it is None): a running program keeps the file it
    opened."""
    folder = native_dir(home)
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    kept = os.path.realpath(keep) if keep else None
    gone = []
    for name in names:
        path = os.path.join(folder, name)
        if os.path.realpath(path) != kept:
            try:
                (shutil.rmtree if os.path.isdir(path) and not os.path.islink(path) else os.remove)(path)
                gone.append(name)
            except OSError:
                pass
    return sorted(gone)


def restore(home, keep):
    """After a build is refused: ~/.local/bin/claude points at the build in use, `keep`, again (relink), and every other
    version in the versions folder goes (remove_others), the refused one too when it is there. The versions removed."""
    relink(home, keep)
    return remove_others(home, keep)


def due(state, now=None):
    now = time.time() if now is None else now
    return state["mode"] == "automatic" and now - (state["last_check"] or 0) >= EVERY
