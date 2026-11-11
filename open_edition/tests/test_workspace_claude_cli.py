"""What the installed Claude Code offers a chat, read from it (claude_cli.py), so that the app's choices follow it.
The fixtures are the image's own claude 2.1.274's `--help` and `--version`, as it printed them."""

import os
import shlex

from proofpane_workspace import claude_cli

HERE = os.path.dirname(os.path.abspath(__file__))
HELP = open(os.path.join(HERE, "fixtures", "claude-2.1.274-help.txt"), encoding="utf-8").read()
VERSION = open(os.path.join(HERE, "fixtures", "claude-2.1.274-version.txt"), encoding="utf-8").read()


def test_the_image_s_claude_offers_five_levels_and_ultracode_and_five_modes():
    got = claude_cli.capabilities(VERSION, HELP, now=1)
    assert got == {"claude_version": "2.1.274", "efforts": ["low", "medium", "high", "xhigh", "max", "ultracode"],
                   "permission_modes": ["default", "acceptEdits", "auto", "bypassPermissions", "plan"],
                   "model_aliases": ["fable", "opus", "sonnet", "haiku"], "floor": False, "discovered_at": 1}


def test_a_newer_claude_s_lists_are_taken_as_they_are():
    # one more level, one mode fewer, one mode more: the page offers what the binary says
    help_text = HELP.replace("(low, medium, high, xhigh, max)", "(low, medium, high, xhigh, max,\n   ultra)").replace(
        '"auto",\n', '"review",\n')
    got = claude_cli.capabilities("2.2.0 (Claude Code)", help_text)
    assert got["claude_version"] == "2.2.0" and got["floor"] is False
    assert got["efforts"] == ["low", "medium", "high", "xhigh", "max", "ultra", "ultracode"]
    assert got["permission_modes"] == ["default", "acceptEdits", "review", "bypassPermissions", "plan"]


def test_ultracode_only_from_the_version_that_takes_it():
    assert "ultracode" not in claude_cli.capabilities("2.1.202 (Claude Code)", HELP)["efforts"]
    assert "ultracode" in claude_cli.capabilities("2.1.203 (Claude Code)", HELP)["efforts"]
    listed = HELP.replace("(low, medium, high, xhigh, max)", "(low, ultracode, max)")
    assert claude_cli.capabilities("2.9.0 (Claude Code)", listed)["efforts"] == ["low", "ultracode", "max"]


def test_a_help_that_does_not_parse_keeps_the_floor():
    for text in ("", "Usage: claude [options]", HELP.replace("--effort <level>", "--effort-level <level>"),
                 HELP.replace('"acceptEdits"', '"accept edits; rm -rf"')):
        got = claude_cli.capabilities(VERSION, text)
        assert got["floor"] is True, text[:40]
        assert set(got["efforts"]) <= set(claude_cli.FLOOR_EFFORTS) or got["efforts"][:5] == [
            "low", "medium", "high", "xhigh", "max"]
    got = claude_cli.capabilities("", "")
    assert got == dict(got, claude_version=None, efforts=list(claude_cli.FLOOR_EFFORTS),
                       permission_modes=list(claude_cli.FLOOR_MODES), floor=True)


def test_an_option_s_list_is_its_own_never_the_next_one_s():
    # an --effort line that gives no list, and a next option whose own list is plain words: not taken as the levels
    no_list = HELP.replace("Effort level for the current session\n                                        (low, medium, high, xhigh, max)",
                           "Effort level for the current session").replace(
        "Create a new cloud session that runs on", "Create a new cloud session (json, text) that runs on")
    assert "(json, text)" in no_list and claude_cli.parse_help(no_list)[0] is None


def test_a_claude_that_cannot_run_gives_the_floor(tmp_path):
    got = claude_cli.discover(str(tmp_path / "no-such-claude"), {"PATH": "/usr/bin:/bin"}, str(tmp_path))
    assert got["floor"] is True and got["claude_version"] is None


def script(tmp_path, name, body):
    path = tmp_path / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return str(path)


def test_where_a_decision_rests_on_it_only_claude_code_s_own_line_is_read(tmp_path):
    # the workspace's floor reads `claude --version` with cli_version: the version on a line of its own followed by
    # "(Claude Code)", as the image's claude prints it, so nothing a wrapper or a shim prints first is taken for it
    env, cwd = {"PATH": "/usr/bin:/bin"}, str(tmp_path)
    printed = os.path.join(HERE, "fixtures", "claude-2.1.274-version.txt")
    real = script(tmp_path, "real", f"cat {shlex.quote(printed)}\n")
    assert claude_cli.cli_version(real, env, cwd) == ((2, 1, 274), None)
    proxy = script(tmp_path, "proxy", "echo 'proxy 10.0.0.1'; echo '2.1.100 (Claude Code)'\n")
    assert claude_cli.cli_version(proxy, env, cwd) == ((2, 1, 100), None)
    wrapped = script(tmp_path, "wrapped", "echo 'wrapper 1.0.0'; echo '2.1.274 (Claude Code)'\n")
    assert claude_cli.cli_version(wrapped, env, cwd) == ((2, 1, 274), None)
    # no line of its own: not read as a version, where the reader for the page's choices takes the address
    proxy_only = script(tmp_path, "proxy-only", "echo 'proxy 10.0.0.1'\n")
    assert claude_cli.cli_version(proxy_only, env, cwd) == (None, "unreadable")
    assert claude_cli.parse_version(claude_cli.run_text(proxy_only, env, cwd, "--version")) == (10, 0, 0)
    within = script(tmp_path, "within", "echo 'not 2.1.274 (Claude Code) but something else'\n")
    assert claude_cli.cli_version(within, env, cwd) == (None, "unreadable")
    on_error = script(tmp_path, "on-error", "echo '2.1.274 (Claude Code)' >&2\n")
    assert claude_cli.cli_version(on_error, env, cwd) == (None, "unreadable")
    assert claude_cli.cli_version(str(tmp_path / "none"), env, cwd) == (None, "unreadable")


def _says(tmp_path, version_lines):
    """A claude that says to --version what the shell commands `version_lines` print, and the image's help to --help,
    noting each time its help is read in `help-read`."""
    printed = os.path.join(HERE, "fixtures", "claude-2.1.274-help.txt")
    return script(tmp_path, "claude", 'case "$1" in\n'
                                      f"  --version) {version_lines} ;;\n"
                                      f"  --help) echo read >> {shlex.quote(str(tmp_path / 'help-read'))}; "
                                      f"cat {shlex.quote(printed)} ;;\n"
                                      "esac\n")


def test_the_workspace_s_own_read_of_its_claude_gives_what_it_gave_for_claude_code_s_own_version_line(tmp_path):
    # a control: the workspace's own read of its claude (discover) reads the version as the
    # gate's floor does (cli_version), and for a claude that prints Claude Code's own line, as the image's does, it
    # gives what capabilities gives of its --version and --help
    printed = os.path.join(HERE, "fixtures", "claude-2.1.274-version.txt")
    got = claude_cli.discover(_says(tmp_path, f"cat {shlex.quote(printed)}"), {"PATH": "/usr/bin:/bin"},
                              str(tmp_path))
    before = claude_cli.capabilities(VERSION, HELP)
    for key in ("claude_version", "efforts", "permission_modes", "floor"):
        assert got[key] == before[key], key


def test_the_workspace_s_own_read_of_its_claude_takes_claude_code_s_line_and_no_version_read_is_the_floor(tmp_path):
    # discover reads the version with cli_version, as the gate's
    # floor does, not the first x.y.z anywhere: a wrapper's own version printed first is not Claude Code's,
    # and a version it cannot read is the floor, its help not read; the help is read once the version is
    env = {"PATH": "/usr/bin:/bin"}
    got = claude_cli.discover(_says(tmp_path, "echo 'wrapper 3.0.0'; echo '2.1.150 (Claude Code)'"), env,
                              str(tmp_path))
    assert (got["claude_version"], got["floor"]) == ("2.1.150", False) and "ultracode" not in got["efforts"]
    assert (tmp_path / "help-read").read_text() == "read\n"
    (tmp_path / "help-read").unlink()
    got = claude_cli.discover(_says(tmp_path, "echo 'proxy 10.0.0.1'"), env, str(tmp_path))
    assert (got["claude_version"], got["floor"]) == (None, True) and not (tmp_path / "help-read").exists()


def test_where_a_decision_rests_on_it_a_version_not_said_in_time_is_told_apart(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_cli, "TIMEOUT", 1)
    slow = script(tmp_path, "slow", "exec sleep 5\n")
    assert claude_cli.cli_version(slow, {"PATH": "/usr/bin:/bin"}, str(tmp_path)) == (None, "timeout")
