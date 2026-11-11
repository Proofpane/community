"""The owner's hook templates (ade_link.settings.json and ade_hook.settings.json, and the two in one,
ade_link_and_hook.settings.json), read as Claude Code starts their hooks: one given as a program and its arguments
(exec form, which Claude Code's settings declare from 2.1.139) with no shell and no CLAUDE_CODE_SHELL_PREFIX, as
2.1.274 starts it, one given as a command line by the shell. Their placeholders are filled as SETUP_A.md and
ADE_FOR_SESSIONS.md say."""

import json
import os
import re

HERE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PYTHON = "/absolute/path/to/venv/bin/python"       # the product venv's interpreter
HOME = "/absolute/path/to/home"                     # the owner's home folder, written out: no hook reads $HOME


def template(name):
    """The template `name`, read whole."""
    with open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return json.load(fh)


def blocks(name, event):
    """The blocks of `event` in the template `name`."""
    return template(name)["hooks"][event]


def start_check(hook):
    """For the command line beside the approval hook's program, which only checks that its interpreter and its file
    are there, the two paths it checks, as (interpreter, hook file); None for any other hook."""
    found = re.match(r"p='([^']*)'; h='([^']*)'; ", hook.get("command", "")) if "args" not in hook else None
    return found.groups() if found else None


def program(hook):
    """The hook's program and its arguments, checked to be the exec form: a command with `args`, no shell between."""
    assert hook["type"] == "command" and isinstance(hook.get("args"), list), hook
    assert all(isinstance(a, str) for a in hook["args"]), hook
    return [hook["command"], *hook["args"]]


def _fill(text, python, home, name, link):
    """`text` with the placeholders filled as SETUP_A.md says: the interpreter, the home folder, the link's directory
    name and the link's id."""
    return text.replace(PYTHON, python).replace(HOME, home).replace("NAME", name).replace("LINK_ID", link)


def argv(hook, python, home, name="mac", link="a" * 32):
    """The hook as Claude Code starts it, its placeholders filled: the interpreter, the home folder, the link's
    directory name and the link's id."""
    return [_fill(part, python, home, name, link) for part in program(hook)]


def line(hook, python, home, name="mac", link="a" * 32):
    """A hook given as a command line, as Claude Code hands it to the shell, its placeholders filled as argv fills a
    program's: every path in it written out, so the shell's HOME names none of them."""
    assert hook["type"] == "command" and "args" not in hook, hook
    return _fill(hook["command"], python, home, name, link)
