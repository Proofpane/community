"""A task's attempt in the workspace (proofpane_workspace/task_attempt.py), the workspace's routes
for tasks (agent.py), the gate's rules for an attempt (gate.py), and the workspace's parts of the task's acceptance
tests.

Every claude here is the tests' stand-in (chat_stand_in.py), taught what Claude Code 2.1.274 does that these rest on;
every gh the stand-in gh (fake_gh.py); every remote a bare repository in the test's folder, one served over
HTTPS on this machine's loopback as github.com. No real claude, codex or gh runs, and nothing reaches a network."""

import base64
import errno
import hashlib
import hmac
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler

import pytest

from conftest import ROOT
from proofpane_workspace import accounts as accounting, gate, gitplaces, providers, task_attempt, task_runs, task_shapes
from proofpane_workspace import settings as workspace_settings
from workspace_fixtures import git, make_repo

# APFS, a Mac's filesystem, refuses a name that is not UTF-8 (EILSEQ): a test that makes one makes it last, and where it
# is refused says so, every check before it having run
NOT_UTF8_REFUSED = "this filesystem refuses a name that is not UTF-8 (APFS, a Mac's, gives EILSEQ)"

TASK = "c0ffee" + "0" * 26
# each test process's own run id: a run's processes are found by its mark wherever they are on the machine
# (processes.marked), so with one id for every process, the end of a check in one run of this file ended a check of
# another run on the same machine at once (a check's `sleep 20`, which exited -15 where its time limit gives 124)
RUN = os.urandom(6).hex()
AGENT_ID = "0a0a0a0a0a0a"
FAKE_GH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_gh.py")


# ---- the gate's rules for a task's attempt (the account folders, the shell's words, the frozen start) --------------

@pytest.fixture
def folders(tmp_path):
    """A workspace's accounts as the workspace lays them out: the default's folder (~/.claude) with a skill and its
    history, an added account whose folder links the default's skills (accounts.py), its session's transcript and saved
    tool output, another session's, another account, and an agent whose task's attempt runs in its worktree."""
    home = tmp_path / "home"
    default = home / ".claude"
    (default / "skills" / "labels").mkdir(parents=True)
    (default / "skills" / "labels" / "SKILL.md").write_text("a skill")
    for name in ("history.jsonl", "settings.json"):
        (default / name).write_text("{}")
    root = tmp_path / "ws"
    root.mkdir()
    os.environ["HOME"], was = str(home), os.environ.get("HOME")
    try:
        accounts = accounting.Accounts(str(root))
        mine = accounts.add("claude", "Work")
        other = accounts.add("claude", "Spare")
    finally:
        if was is None:
            del os.environ["HOME"]
        else:
            os.environ["HOME"] = was
    own = root / accounting.FOLDER / mine["id"] / "claude"
    elsewhere = root / accounting.FOLDER / other["id"] / "claude"
    project = own / "projects" / "-ws-agents-x-work"
    for session in ("sess-mine", "sess-other"):
        (project / session / "tool-results").mkdir(parents=True)
        (project / session / "tool-results" / "out.txt").write_text("long output")
    (project / "sess-mine.jsonl").write_text("")
    for folder in (own, elsewhere, default):
        for name in ("history.jsonl", "paste-cache", "tasks", "shell-snapshots", ".credentials-hint"):
            path = folder / name
            if not path.exists():
                path.write_text("x") if "." in name else path.mkdir()
    agent = root / "agents" / "0a0a0a0a0a0a"
    work = agent / "tasks" / ("1" * 32) / "work"
    work.mkdir(parents=True)
    env = {"HOME": str(home), "PP_TASK_ATTEMPT": "1", "PP_AGENT_RUN": "1", "PP_AGENT_DIR": str(agent),
           "PP_SESSION_DIR": str(agent / "runs" / "0b0b0b0b0b0b"), "CLAUDE_CONFIG_DIR": str(own)}
    return {"home": str(home), "default": str(default), "own": str(own), "other": str(elsewhere), "work": str(work),
            "project": str(project), "env": env}


def _asked(f, tool, given, **env):
    payload = {"tool_name": tool, "tool_input": given, "cwd": f["work"], "session_id": "sess-mine",
               "transcript_path": os.path.join(f["project"], "sess-mine.jsonl")}
    return gate.refusal(payload, f["home"], dict(f["env"], **env))


def test_a_task_attempt_is_refused_every_path_in_any_account_folder(folders):
    f = folders
    for folder, var in ((f["own"], "$CLAUDE_CONFIG_DIR"), (f["other"], None), (f["default"], "~/.claude")):
        for name in ("history.jsonl", "paste-cache", "tasks", "shell-snapshots", ".credentials-hint"):
            path = os.path.join(folder, name)
            for tool, given in (("Read", {"file_path": path}), ("Grep", {"pattern": "x", "path": path}),
                                ("Glob", {"pattern": path + "/*"})):
                assert _asked(f, tool, given) == gate.ACCOUNT_FOLDER, (tool, path)
            # a command naming a .claude folder an agent's run is refused already (AGENT_SETTINGS); every other
            # account folder by the task's rule
            assert _asked(f, "Bash", {"command": f"cat {path}"}) in (gate.ACCOUNT_FOLDER, gate.AGENT_SETTINGS), path
            if folder != f["default"]:
                assert _asked(f, "Bash", {"command": f"cat {path}"}) == gate.ACCOUNT_FOLDER, path
            if var:
                spelled = f"{var}/{name}"
                assert _asked(f, "Read", {"file_path": spelled}) == gate.ACCOUNT_FOLDER, spelled
                assert _asked(f, "Bash", {"command": f"cat {spelled}"}) is not None, spelled
    # its own folder by its CLAUDE_CONFIG_DIR, also where the environment names no agent folder to find the others by
    own = os.path.join(f["own"], "history.jsonl")
    assert _asked(f, "Read", {"file_path": own}, PP_AGENT_DIR="") == gate.ACCOUNT_FOLDER
    # a quote put inside a word, as the shell reads it
    assert _asked(f, "Bash", {"command": "cat \"$CLAUDE_CONFIG_DIR\"/his''tory.jsonl"}) == gate.ACCOUNT_FOLDER
    assert _asked(f, "Bash", {"command": "cat ${CLAUDE_CONFIG_DIR}/hist\\ory.jsonl"}) == gate.ACCOUNT_FOLDER
    # the worktree is the attempt's own
    assert _asked(f, "Read", {"file_path": os.path.join(f["work"], "README.md")}) is None
    assert _asked(f, "Bash", {"command": "ls -la && git status"}) is None


def test_a_path_inside_a_word_or_in_an_mcp_server_s_input_is_refused_too(folders):
    # a path spelled inside a word (an option's value, a quoted string, a part after `=`), under a
    # key the gate does not know (an MCP server's tool), or climbing out of the worktree inside a word
    f = folders
    other = os.path.join(f["other"], "history.jsonl")
    climbing = os.path.relpath(other, f["work"])
    assert climbing.startswith("..")
    for command in ('dd if="$CLAUDE_CONFIG_DIR"/history.jsonl', 'grep -f"$CLAUDE_CONFIG_DIR"/history.jsonl x',
                    "python3 -c \"print(open('$CLAUDE_CONFIG_DIR/history.jsonl').read())\"",
                    f"dd if={other}", f"tar --file={other} -c x", f"grep -f{climbing} x", f"dd if={climbing}",
                    "dd if=~/.claude/history.jsonl", "cat {~/.claude/history.jsonl,x}",
                    'cat {"$CLAUDE_CONFIG_DIR"/history.jsonl,x}'):
        # the task's rule alone (no agent's run in the environment, whose rule could refuse a climbing path first)
        assert _asked(f, "Bash", {"command": command}, PP_AGENT_RUN="") == gate.ACCOUNT_FOLDER, command
        assert _asked(f, "Bash", {"command": command}) is not None, command
    for given in ({"file": other}, {"options": {"source": "~/.claude/history.jsonl"}}, {"files": ["x", other]},
                  {"url": "file://" + other}):
        assert _asked(f, "mcp__files__read", given) == gate.ACCOUNT_FOLDER, given
    # the controls: the same spellings naming the worktree's own files; the workspace's own tools, which open no
    # path; and a file's content that only mentions a folder
    readme = os.path.join(f["work"], "README.md")
    for command in (f"dd if={readme}", f'grep -f"{readme}" x', "python3 -c \"print(open('README.md').read())\"",
                    "grep -f../work/README.md x", "cat ~/.claude-notes.txt"):
        assert _asked(f, "Bash", {"command": command}) is None, command
    assert _asked(f, "mcp__files__read", {"file": readme}) is None
    assert _asked(f, "mcp__proofpane__ask_owner", {"question": "May I read ~/.claude/history.jsonl?"}) is None
    assert _asked(f, "Write", {"file_path": readme, "content": f"see ~/.claude and {other}"}) is None


def test_a_path_field_is_read_as_the_tool_opens_it_and_a_pattern_from_its_own_path(folders):
    # a path field as written (a link literally named $NOPE), and a Glob's or a Grep's pattern
    # or glob joined to the call's own path, not to the worktree
    f = folders
    planted = os.path.join(f["work"], "$NOPE")
    os.symlink(f["other"], planted)
    assert _asked(f, "Read", {"file_path": os.path.join(planted, "history.jsonl")}) == gate.ACCOUNT_FOLDER
    skills = os.path.join(f["own"], "skills")
    for tool, given in (("Glob", {"path": skills, "pattern": "../*.jsonl"}),
                        ("Glob", {"path": skills, "pattern": "../../*/claude/*.jsonl"}),
                        ("Grep", {"pattern": "x", "path": skills, "glob": "../history.jsonl"})):
        assert _asked(f, tool, given) == gate.ACCOUNT_FOLDER, (tool, given)
    # the controls: a link with a plain name; the skills read from their own path; a worktree glob from the worktree
    os.symlink(f["work"], os.path.join(f["work"], "plain"))
    assert _asked(f, "Read", {"file_path": os.path.join(f["work"], "plain", "README.md")}) is None
    assert _asked(f, "Glob", {"path": skills, "pattern": "*/SKILL.md"}) is None
    assert _asked(f, "Grep", {"pattern": "x", "path": skills, "glob": "*.md"}) is None
    assert _asked(f, "Glob", {"pattern": "src/../*.py"}) is None
    # a Grep's pattern is a regular expression matched in what the files hold, never a path: `..` is any two
    # characters, in the skills it may read
    for pattern in ("..", "\\.\\./x", "../history.jsonl"):
        assert _asked(f, "Grep", {"pattern": pattern, "path": skills}) is None, pattern


def test_past_its_part_budget_the_task_s_rule_refuses_the_call_unread(folders):
    # the reading of paths inside words stops after MAX_PARTS different parts that could be
    # paths, and past it the call is refused as one the gate cannot read (deny by default); a folder spelled anywhere,
    # as written or as the shell reads it, is found before any part is counted
    f = folders
    climbing = os.path.relpath(os.path.join(f["other"], "history.jsonl"), f["work"])
    assert climbing.startswith("..")

    def fill(n):
        return ": " + " ".join(f"a/{i}" for i in range(n))

    for command, why in ((fill(1000) + "; dd if=README.md", None), (fill(gate.MAX_PARTS) + "; dd if=README.md", None),
                         (fill(gate.MAX_PARTS + 1) + "; dd if=README.md", gate.UNREADABLE),
                         (fill(gate.MAX_PARTS + 1) + f"; dd if={climbing}", gate.UNREADABLE),
                         (fill(gate.MAX_PARTS + 1) + '; dd if="$CLAUDE_CONFIG_DIR"/history.jsonl', gate.ACCOUNT_FOLDER),
                         (fill(gate.MAX_PARTS - 1) + f"; dd if={climbing}", gate.ACCOUNT_FOLDER)):
        assert _asked(f, "Bash", {"command": command}, PP_AGENT_RUN="") == why, (command[-60:], why)


def test_a_command_s_words_are_read_whole_where_a_word_s_parts_name_no_folder(folders):
    # the reading of a path inside a word splits a word at `=`, `:`, `,` and `@`, so a link in the worktree whose name
    # has one of them (a repository can hold such a link, to the workspace's own fixed paths) is followed only by
    # reading each word whole, as the shell gives it: into another account's folder, by a word with a quote inside the
    # link's name, and into a readable place, which a shell may not read either
    f = folders
    os.symlink(f["other"], os.path.join(f["work"], "x,y"))
    os.symlink(os.path.join(f["project"], "sess-mine", "tool-results"), os.path.join(f["work"], "t,r"))
    for command in ("cat x,''y/history.jsonl", "cat t,r/out.txt", "cd t,r && cat out.txt"):
        assert _asked(f, "Bash", {"command": command}, PP_AGENT_RUN="") == gate.ACCOUNT_FOLDER, command
    # the control: a name with a comma in the worktree's own files
    os.mkdir(os.path.join(f["work"], "a,b"))
    assert _asked(f, "Bash", {"command": "cat a,''b/notes.txt"}, PP_AGENT_RUN="") is None


def test_the_two_readable_places_are_read_only_by_read_glob_and_grep_on_the_resolved_path(folders):
    f = folders
    saved = os.path.join(f["project"], "sess-mine", "tool-results", "out.txt")
    skill = os.path.join(f["own"], "skills", "labels", "SKILL.md")
    assert os.path.islink(os.path.join(f["own"], "skills"))      # the workspace's link to the default's skills
    assert _asked(f, "Read", {"file_path": saved}) is None
    assert _asked(f, "Read", {"file_path": skill}) is None
    assert _asked(f, "Glob", {"pattern": os.path.join(f["own"], "skills", "*", "SKILL.md")}) is None
    assert _asked(f, "Grep", {"pattern": "x", "path": os.path.join(f["own"], "skills")}) is None
    # the link's target, the default's skills, is allowed as its target
    assert _asked(f, "Read", {"file_path": os.path.join(f["default"], "skills", "labels", "SKILL.md")}) is None
    # a write to either, a shell command naming either, and a cd into one
    for tool, given in (("Write", {"file_path": skill, "content": "x"}), ("Edit", {"file_path": saved}),
                        ("Bash", {"command": f"cat {saved}"}),
                        ("Bash", {"command": 'cd "$CLAUDE_CONFIG_DIR/skills" && cat labels/SKILL.md'})):
        assert _asked(f, tool, given) == gate.ACCOUNT_FOLDER, (tool, given)
    # out of either: by .., another session's output, a link planted in skills
    planted = os.path.join(f["default"], "skills", "labels", "elsewhere")
    os.symlink(os.path.dirname(f["own"]), planted)
    for path in (os.path.join(f["own"], "skills", "..", "history.jsonl"),
                 os.path.join(f["project"], "sess-other", "tool-results", "out.txt"),
                 os.path.join(f["own"], "skills", "labels", "elsewhere", "claude", "history.jsonl")):
        assert _asked(f, "Read", {"file_path": path}) == gate.ACCOUNT_FOLDER, path


def test_the_output_place_is_the_call_s_own_session_s_and_in_its_own_folder(folders):
    f = folders
    saved = os.path.join(f["project"], "sess-other", "tool-results", "out.txt")
    payload = {"tool_name": "Read", "tool_input": {"file_path": saved}, "cwd": f["work"], "session_id": "sess-other",
               "transcript_path": os.path.join(f["project"], "sess-other.jsonl")}
    assert gate.refusal(payload, f["home"], f["env"]) is None       # its own session's, by the call's own transcript
    # a transcript the call names outside its own account's folder gives no readable place, there or here
    payload["transcript_path"] = os.path.join(f["other"], "projects", "p", "sess-other.jsonl")
    assert gate.refusal(payload, f["home"], f["env"]) == gate.ACCOUNT_FOLDER
    payload["tool_input"] = {"file_path": os.path.join(f["other"], "projects", "p", "sess-other", "tool-results",
                                                       "out.txt")}
    assert gate.refusal(payload, f["home"], f["env"]) == gate.ACCOUNT_FOLDER
    # nor a transcript that is not the session's own, which would name another session's output
    payload = {"tool_name": "Read", "tool_input": {"file_path": saved}, "cwd": f["work"], "session_id": "sess-other",
               "transcript_path": os.path.join(f["project"], "sess-mine.jsonl")}
    assert gate.refusal(payload, f["home"], f["env"]) == gate.ACCOUNT_FOLDER


def test_frozen_every_call_but_the_two_tools_is_refused(folders):
    f = folders
    assert gate.FROZEN_TOOLS == task_shapes.FROZEN_TOOLS
    for tool, given in (("Bash", {"command": "ls"}), ("Read", {"file_path": os.path.join(f["work"], "a")}),
                        ("mcp__proofpane__save_progress", {"note": "x"}), ("WebSearch", {"query": "x"})):
        assert _asked(f, tool, given, PP_FROZEN="1") == gate.FROZEN, tool
    for tool in task_shapes.FROZEN_TOOLS:
        assert _asked(f, tool, {}, PP_FROZEN="1") is None
    assert _asked(f, "Bash", {"command": "ls"}) is None                 # not frozen


def test_an_agent_s_own_run_keeps_release_59_s_rules(folders):
    # without PP_TASK_ATTEMPT the account folders are not a task's rule: a run of the agent's own reads as before
    f = folders
    env = {k: v for k, v in f["env"].items() if k != "PP_TASK_ATTEMPT"}
    payload = {"tool_name": "Read", "tool_input": {"file_path": os.path.join(f["own"], "history.jsonl")},
               "cwd": f["work"]}
    assert gate.refusal(payload, f["home"], env) is None
    payload["tool_input"] = {"file_path": "x"}
    assert gate.refusal(dict(payload, tool_name="Bash", tool_input={"command": "ls"}), f["home"],
                        dict(env, PP_FROZEN="1")) is None


# ---- where the workspace's own git and the owner's chats run, refused to a task's attempt ---------------------------

def _git_places_of(f, tmp_path):
    """The folders fixture's workspace with another repository, an owner's session and, as in the desktop app, a
    repository linked from the owner's own folder: where the workspace's own git and the owner's chats run."""
    root = os.path.dirname(os.path.dirname(f["env"]["PP_AGENT_DIR"]))
    other = os.path.join(root, "repos", "other")
    os.makedirs(os.path.join(other, ".git", "hooks"))
    session = os.path.join(root, "sessions", "d1d2d3d4d5d6")
    os.makedirs(os.path.join(session, "work"))
    mine = tmp_path / "Code" / "parser"
    (mine / ".git").mkdir(parents=True)
    os.symlink(str(mine), os.path.join(root, "repos", "parser"))
    return root, other, session, str(mine)


def test_another_repository_and_a_session_are_refused_to_a_run_and_to_a_task_s_attempt(folders, tmp_path):
    # five calls, from an agent's run and from a task's attempt, each in the agent's work folder and the attempt in
    # its own worktree too: the first four are refused to both
    f = folders
    root, other, session, _ = _git_places_of(f, tmp_path)
    agent = f["env"]["PP_AGENT_DIR"]
    os.makedirs(os.path.join(agent, "work"))
    run_env = {k: v for k, v in f["env"].items() if k != "PP_TASK_ATTEMPT"}
    for who, env, cwd in (("an agent's run", run_env, os.path.join(agent, "work")),
                          ("a task's attempt", f["env"], os.path.join(agent, "work")),
                          ("a task's attempt in its worktree", f["env"], f["work"])):
        hook = os.path.relpath(os.path.join(other, ".git", "hooks", "post-checkout"), cwd)
        for tool, given, why in (
                ("Write", {"file_path": os.path.join(other, ".git", "hooks", "pre-commit"), "content": "#!/bin/sh\n"},
                 gate.AGENT_REPOS),
                ("Write", {"file_path": os.path.join(other, ".git", "config"), "content": "[core]\n"}, gate.AGENT_REPOS),
                ("Bash", {"command": f"printf x > {hook}"}, gate.AGENT_REPOS),
                ("Edit", {"file_path": os.path.join(session, "README.md"), "old_string": "a", "new_string": "b"},
                 gate.AGENT_REPOS),
                ("Write", {"file_path": os.path.join(cwd, "ok.txt"), "content": "fine"}, None)):
            payload = {"tool_name": tool, "tool_input": given, "cwd": cwd}
            assert gate.refusal(payload, f["home"], env) == why, (who, tool, given)


def test_a_task_s_attempt_is_refused_another_repository_however_its_call_names_it(folders, tmp_path):
    # the rule for an agent's run reads a path field as written and a command's words; a task's attempt reads every
    # path as its rule for the account folders does: a Glob's pattern from its path, a Grep's glob, an
    # option's letters taken off, a part after `:`, a variable put in, an MCP server's input under a key the gate
    # does not know, and a linked repository's own folder; with the environment of an agent's run, and by the task's
    # rule alone (PP_AGENT_RUN empty)
    f = folders
    root, other, session, mine = _git_places_of(f, tmp_path)
    up = os.path.relpath(root, f["work"])
    hooks = os.path.join(other, ".git", "hooks")
    calls = [("Glob", {"pattern": f"{up}/repos/other/.git/hooks/*"}),
             ("Glob", {"path": f["work"], "pattern": f"{up}/sessions/*/work/*.py"}),
             ("Grep", {"pattern": "x", "path": f["work"], "glob": f"{up}/repos/other/.git/config"}),
             ("Bash", {"command": f"tar -C{up}/repos/other -xf x.tar"}),
             ("Bash", {"command": f"cp evil.sh -t{hooks}"}),
             ("Bash", {"command": f"rsync -a evil.sh localhost:{hooks}/pre-commit"}),
             ("Bash", {"command": "cat $PP_AGENT_DIR/../../repos/other/.git/config"}),
             ("Read", {"file_path": "$PP_AGENT_DIR/../../repos/other/.git/config"}),
             ("mcp__files__move", {"source": "evil.sh", "destination": os.path.join(hooks, "pre-commit")}),
             ("Read", {"file_path": os.path.join(mine, ".git", "config")}),
             ("Bash", {"command": f"cat {mine}/src/x.py"})]
    for tool, given in calls:
        assert _asked(f, tool, given, PP_AGENT_RUN="") == gate.AGENT_REPOS, (tool, given)
        # with an agent's run's environment too, where its rule may meet the call first (`-C..` read as a folder in
        # the agents' folder, say)
        assert _asked(f, tool, given) in (gate.AGENT_REPOS, gate.AGENT_FOLDER), (tool, given)
    # the controls: its own worktree, however named; a file's content or the workspace's own tool that only mention
    # the folders; a folder beside the linked one; and, with no agent folder to find the workspace by, nothing of it
    readme = os.path.join(f["work"], "README.md")
    for tool, given in (("Glob", {"pattern": "src/**/*.py"}), ("Bash", {"command": f"tar -C{f['work']} -xf x.tar"}),
                        ("Bash", {"command": "cat repos/other/notes.md"}),
                        ("Write", {"file_path": readme, "content": f"see {hooks}"}),
                        ("mcp__proofpane__ask_owner", {"question": f"May I write {hooks}/pre-commit?"}),
                        ("Read", {"file_path": str(tmp_path / "Code" / "notes.md")})):
        assert _asked(f, tool, given, PP_AGENT_RUN="") is None, (tool, given)
    assert _asked(f, "Bash", {"command": "ls ; pwd"}, PP_AGENT_RUN="", PP_AGENT_DIR="") is None


def test_the_record_of_accepted_git_settings_is_refused_as_the_shell_reads_its_name(folders):
    # a call that names git-places.json as written is refused, and every rule's command words are read as the
    # shell reads them, so a quote, a backslash or a variable inside the name is the record too, for every session
    f = folders
    for command in ("cat git-pla''ces.json", "cat git-places\\.json", 'cat "git-places".json', "cat $F"):
        for env in ({"F": "git-places.json"}, dict(f["env"], F="git-places.json")):
            payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": f["work"]}
            assert gate.refusal(payload, f["home"], env) == gate.PLACES_RECORD, (command, env)
    payload = {"tool_name": "Bash", "tool_input": {"command": "cat git-places-notes.json"}, "cwd": f["work"]}
    assert gate.refusal(payload, f["home"], {}) is None                 # a name beside it is not the record


def test_every_gate_rule_reads_a_command_s_words_as_the_shell_does(tmp_path):
    # a quote, a backslash or a variable inside a word spells the same name to the shell
    home = str(tmp_path)
    env = {"HOME": home, "C": ".credentials.json"}
    for command in ("cat ~/.claude/.cred''entials.json", "cat ~/.claude/.cred\\entials.json",
                    "cat ~/.claude/$C", "secur''ity find-generic-password -s x", "git push -''-force origin main",
                    "git push origin +ma''in"):
        assert gate.refusal({"tool_name": "Bash", "tool_input": {"command": command}}, home, env) is not None, command
    # and a quoted prompt is still one word: no flag, no second command
    assert gate.refusal({"tool_name": "Bash", "tool_input": {"command": "git commit -m 'git push -f'"}}, home,
                        env) is None
    assert gate.shell_words("qwen -p 'add a --bare flag'", env) == ["qwen", "-p", "add a --bare flag"]
    # an agent's run's rules too: ~/.claude.json, spelled with a quote inside its name
    agent = os.path.join(home, "ws", "agents", "0a0a0a0a0a0a")
    run = dict(env, PP_AGENT_RUN="1", PP_AGENT_DIR=agent)
    payload = {"tool_name": "Bash", "tool_input": {"command": "cat ~/.cl''aude.json"},
               "cwd": os.path.join(agent, "work")}
    assert gate.refusal(payload, home, run) == gate.AGENT_SETTINGS
    assert gate.refusal(dict(payload, tool_input={"command": "cat ~/.claude.json"}), home, run) == gate.AGENT_SETTINGS
    assert gate.refusal(dict(payload, tool_input={"command": "cat ~/.claude-notes.txt"}), home, run) is None


def test_every_gate_rule_reads_a_command_s_braces_as_bash_expands_them(tmp_path):
    # a brace's list or sequence spells a name the shell gives whole, so every rule reads
    # the words the braces make (a quoted brace too: the gate reads more, never fewer)
    home = str(tmp_path)
    env = {"HOME": home}
    assert gate.shell_words("x a{b,c}d {1..3} {c..a} {08..10} {1..7..3} {x}a{b,c} {a,{b,c}} {a,b", env) == \
        ["x", "abd", "acd", "1", "2", "3", "c", "b", "a", "08", "09", "10", "1", "4", "7", "{x}ab", "{x}ac", "a", "b",
         "c", "{a,b"]
    agent = os.path.join(home, "ws", "agents", "0a0a0a0a0a0a")
    run = dict(env, PP_AGENT_RUN="1", PP_AGENT_DIR=agent)
    payload = {"tool_name": "Bash", "cwd": os.path.join(agent, "work")}
    settings = gate.AGENT_SETTINGS
    for command, why in (("cat ~/.claude.{json,}", settings), ("cat ~/.claude.jso{n..n}", settings),
                         ("cat ~/.cla{u,x}de.json", settings), ("cat '~/.claude.{json,}'", settings),
                         ("cat ~/.claude/.cred{entials,x}.json", gate.SIGN_IN), ("ls {a,b} {1..3}", None),
                         ("echo " + "{a,b}" * 13, gate.UNREADABLE), ("echo {1..5000}", gate.UNREADABLE),
                         ("echo {1..4096}", None)):
        assert gate.refusal(dict(payload, tool_input={"command": command}), home, run) == why, command


# ---- the attempt's spec, checked before any claude starts -----------------------------------------------------------

def _packet(step=1, **information):
    info = dict({"attempt": {"run": RUN, "n": 1, "fence": 1}, "steps_done": [], "checkpoint": None, "expect": None,
                 "effects": {"done": [], "not_done": [], "unknown": [], "ran": []}}, **information)
    packet = {"schema": task_shapes.PACKET_SCHEMA, "nonce": "a-nonce-of-the-app-s",
              "instructions": {"by": "owner", "task": {"id": TASK, "title": "t", "goal": "g"},
                               "step": {"n": step, "of": step, "objective": "do the step"},
                               "steps": [{"n": n, "objective": "do the step"} for n in range(1, step + 1)],
                               "decisions": []},
              "information": info, "next": {"step": step, "action": f"begin step {step}"}}
    return json.dumps(packet, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pins():
    return dict(task_attempt.ATTEMPT_ENV, **{n: os.environ.get(n, "") for n in task_attempt.WORKSPACE_ENV})


def _spec(tmp_path, **over):
    work = tmp_path / "work"
    if not work.exists():
        make_repo(str(work))
    records = tmp_path / "records"
    records.mkdir(exist_ok=True)
    agent = tmp_path / "agent"          # the agent's folder, as the workspace names it (PP_AGENT_DIR)
    agent.mkdir(exist_ok=True)
    packet = over.pop("packet", None) or _packet()
    spec = {"run_id": RUN, "task_id": TASK, "agent_id": AGENT_ID, "step": 1, "attempt": 1, "fence": 1,
            "work": str(work), "branch": "pp/task-" + TASK, "records": str(records), "packet": packet,
            "packet_digest": task_shapes.tagged_digest(task_shapes.RESUME_TAG, packet),
            "backend": {"kind": "account", "id": "default"},
            "identity": {"email": "ana@example.org", "organization": None, "plan": "Claude Max"},
            "org_id": "org-stand-in", "claude": "/bin/true", "claude_version": task_shapes.TASK_CLAUDE_VERSION,
            "env": dict(_pins(), HOME=str(tmp_path), PP_AGENT_DIR=str(agent)), "settings": {"env": _pins()},
            "allow": [],
            "permission_mode": "default", "tools": [], "mcp_servers": {}, "model": None, "effort": None,
            "brief": "b", "objective": "o", "check": None, "protected": [], "external": [], "repo_slug": None,
            "repo_address": None, "watch_places": [], "watch_digest": task_attempt.watch_digest([]),
            "memory_dir": None, "expect": None, "checkpoint0": git("rev-parse", "HEAD", cwd=str(work)),
            "known_fence": None, "checkpoints_so_far": [], "max_turns": None, "max_seconds": None,
            "max_budget_usd": None, "unattended": False, "reserve_lifted": False}
    spec.update(over)
    return spec


def _refused(spec):
    with pytest.raises(task_attempt.SpecError) as e:
        task_attempt.check_spec(spec)
    return e.value


def test_a_stale_fence_or_a_changed_packet_is_refused_before_any_claude_starts(tmp_path):
    assert task_attempt.check_spec(_spec(tmp_path))
    assert task_attempt.check_spec(_spec(tmp_path, fence=4, known_fence=3))
    stale = _refused(_spec(tmp_path, fence=3, known_fence=3))
    assert stale.check == "fence" and stale.found == {"fence": 3, "known": 3}
    spec = _spec(tmp_path)
    changed = bytearray(spec["packet"])
    changed[10] ^= 1
    e = _refused(dict(spec, packet=bytes(changed)))
    assert e.check == "packet" and e.found["sent"] == spec["packet_digest"] and e.found["served"] != e.found["sent"]
    # a packet whose digest is right but that is not a resume packet
    other = json.dumps({"schema": "something.else"}).encode()
    assert _refused(_spec(tmp_path, packet=other)).check == "packet"


def test_each_pin_is_in_the_attempt_s_environment_and_its_own_settings(tmp_path):
    for name, value in task_attempt.ATTEMPT_ENV.items():
        spec = _spec(tmp_path)
        del spec["env"][name]
        assert _refused(spec).check is None, name
        spec = _spec(tmp_path)
        spec["settings"]["env"][name] = "1" if value != "1" else "0"
        assert _refused(spec).check is None, name
    for name in task_attempt.WORKSPACE_ENV:
        spec = _spec(tmp_path)
        del spec["env"][name]
        assert _refused(spec).check is None, name
        spec = _spec(tmp_path)
        spec["settings"]["env"][name] = "/somewhere/else"
        assert _refused(spec).check is None, name
    # the pins: auto memory, bare mode, background tasks and the advisor off, no prefix
    assert task_attempt.ATTEMPT_ENV == {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "CLAUDE_CODE_SHELL_PREFIX": "",
                                        "CLAUDE_CODE_SIMPLE": "0", "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
                                        "CLAUDE_CODE_DISABLE_ADVISOR_TOOL": "1"}
    assert task_attempt.WORKSPACE_ENV == ("LD_PRELOAD", "LD_AUDIT", "LD_LIBRARY_PATH", "SHELL", "PATH")
    for name in task_shapes.REFUSED_ENV:
        spec = _spec(tmp_path)
        spec["env"][name] = "x"
        e = _refused(spec)
        assert e.check == "start" and e.found == {"refused_env": [name]}, name


def test_a_backend_is_an_account_by_its_id_or_a_key_by_its_provider():
    for good in ({"kind": "account", "id": "default"}, {"kind": "account", "id": "0123456789ab"},
                 {"kind": "key", "provider": "anthropic"}, {"kind": "key", "provider": "kimi"}):
        assert task_attempt.is_backend(good), good
    for bad in ({"kind": "account"}, {"kind": "account", "id": None}, {"kind": "account", "id": "Default"},
                {"kind": "key", "id": "anthropic"}, {"kind": "key", "provider": "openai"},
                {"kind": "account", "id": "default", "provider": "anthropic"}, {"kind": "mac", "id": "x"}, None):
        assert not task_attempt.is_backend(bad), bad


# ---- a checkpoint's tree and a pending checkpoint after a machine stop ---------------------------------------------

@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repository with a tracked config file and a .gitignore that takes a .env back in, and a home whose git
    excludes name both (written after the first commit, as a person's would be). XDG_CONFIG_HOME, which git and
    exclude_files read before HOME, is cleared, so the machine's own configuration stays out (GitHub's runners set
    it)."""
    home = tmp_path / "home"
    (home / ".config" / "git").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    path = str(tmp_path / "r")
    make_repo(path)
    with open(os.path.join(path, "tracked.cfg"), "w") as fh:
        fh.write("one\n")
    with open(os.path.join(path, ".gitignore"), "w") as fh:
        fh.write("!.env\n")
    git("add", "-A", cwd=path)
    git("commit", "-qm", "base", cwd=path)
    (home / ".config" / "git" / "ignore").write_text(".env\n*.cfg\n")
    return path


def _tree_holds(path, tree):
    return git("ls-tree", "-r", "--name-only", tree, cwd=path).split()


def test_a_checkpoint_leaves_out_what_the_person_s_own_excludes_name_whatever_gitignore_says(repo):
    base = git("rev-parse", "HEAD", cwd=repo)
    with open(os.path.join(repo, "tracked.cfg"), "w") as fh:
        fh.write("two\n")
    with open(os.path.join(repo, ".env"), "w") as fh:
        fh.write("SECRET=1\n")
    with open(os.path.join(repo, "work.txt"), "w") as fh:
        fh.write("the work\n")
    excludes = task_attempt.exclude_files(repo)
    assert os.path.join(os.environ["HOME"], ".config", "git", "ignore") in excludes
    built = task_attempt.build_tree(task_attempt._git_dir(repo), repo, base, excludes=excludes)
    held = _tree_holds(repo, built["tree"])
    assert ".env" not in held and "work.txt" in held and "tracked.cfg" in held
    assert git("cat-file", "-p", f"{built['tree']}:tracked.cfg", cwd=repo) == "one"     # as HEAD has it
    assert built["left_out"] == [".env", "tracked.cfg"]
    # a tracked file the excludes name, deleted: kept as HEAD has it, and listed
    os.unlink(os.path.join(repo, "tracked.cfg"))
    built = task_attempt.build_tree(task_attempt._git_dir(repo), repo, base, excludes=excludes)
    assert "tracked.cfg" in _tree_holds(repo, built["tree"]) and "tracked.cfg" in built["left_out"]
    # an excludes file named elsewhere (core.excludesFile) is read, and .git/info/exclude too
    elsewhere = os.path.join(os.path.dirname(repo), "my-excludes")
    with open(elsewhere, "w") as fh:
        fh.write("work.txt\n")
    git("config", "core.excludesFile", elsewhere, cwd=repo)
    with open(os.path.join(repo, ".git", "info", "exclude"), "a") as fh:
        fh.write("notes.md\n")
    with open(os.path.join(repo, "notes.md"), "w") as fh:
        fh.write("x\n")
    with open(os.path.join(repo, ".gitignore"), "a") as fh:
        fh.write("!notes.md\n!work.txt\n")
    excludes = task_attempt.exclude_files(repo)
    assert elsewhere in excludes
    built = task_attempt.build_tree(task_attempt._git_dir(repo), repo, base, excludes=excludes)
    held = _tree_holds(repo, built["tree"])
    assert "work.txt" not in held and "notes.md" not in held


def test_a_pending_checkpoint_is_completed_from_the_branch_s_head_or_dropped(repo):
    base = git("rev-parse", "HEAD", cwd=repo)
    with open(os.path.join(repo, "line.txt"), "w") as fh:
        fh.write("step 1\n")
    built = task_attempt.build_tree(task_attempt._git_dir(repo), repo, base)
    pending = {"tree": built["tree"], "parent": base}
    # stopped before its commit: dropped, and no head
    assert task_attempt.complete_pending(repo, "main", pending) == {"state": "dropped", "head": None}
    commit = git("commit-tree", built["tree"], "-p", base, "-m", "checkpoint", cwd=repo)
    git("update-ref", "refs/heads/main", commit, cwd=repo)
    # stopped after its commit, before its record: completed from the branch's head, no second commit
    assert task_attempt.complete_pending(repo, "main", pending) == {"state": "complete", "head": commit}
    # the head's tree or parent not the pending one's: dropped
    assert task_attempt.complete_pending(repo, "main", dict(pending, parent=commit))["state"] == "dropped"
    assert task_attempt.complete_pending(repo, "main", {"tree": base, "parent": base})["state"] == "dropped"
    assert task_attempt.complete_pending(repo, "main", {})["state"] == "dropped"


def test_protected_paths_by_glob_and_by_folder():
    paths = ["tests/test_a.py", "src/a.py", "docs/x/y.md", "README.md", "testsx/a"]
    assert task_attempt.protected_paths(paths, ["tests/**"]) == ["tests/test_a.py"]
    assert task_attempt.protected_paths(paths, ["tests"]) == ["tests/test_a.py"]
    assert task_attempt.protected_paths(paths, ["*.md"]) == ["README.md"]
    assert task_attempt.protected_paths(paths, ["docs/**/*.md"]) == ["docs/x/y.md"]
    assert task_attempt.protected_paths(paths, ["**/a.py"]) == ["src/a.py"]


def test_the_files_a_next_run_loads_are_instruction_files_at_any_depth_and_what_they_import(repo):
    os.makedirs(os.path.join(repo, "src", ".claude"))
    os.makedirs(os.path.join(repo, "docs"))
    os.makedirs(os.path.join(repo, ".claude"))
    # an import is read from the folder of the file that holds it, as Claude Code reads it; a .claude folder counts at
    # any depth
    for path, text in (("CLAUDE.md", "see @docs/rules.md\n"), ("docs/rules.md", "and @more.md\n"),
                       ("docs/more.md", "x\n"), ("src/CLAUDE.md", "x\n"), (".claude/settings.json", "{}\n"),
                       ("src/.claude/settings.json", "{}\n"), ("src/code.py", "x\n"), ("src/x.claude", "x\n")):
        with open(os.path.join(repo, path), "w") as fh:
            fh.write(text)
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "rules", cwd=repo)
    loaded = task_attempt.loaded_files(repo, "HEAD")
    assert {"CLAUDE.md", "docs/rules.md", "docs/more.md", "src/CLAUDE.md", ".claude/settings.json",
            "src/.claude/settings.json"} <= loaded
    assert "src/code.py" not in loaded and "src/x.claude" not in loaded
    assert task_attempt.is_loaded("src/.claude/agents/a.md") and not task_attempt.is_loaded("src/x.claude")


def test_a_hand_off_lists_git_s_own_files_and_any_hooks_folder_among_the_files_a_next_run_would_load(repo):
    # a `.claude` folder and `.mcp.json`, an instruction file at any depth with what it imports, `.gitattributes`,
    # `.gitmodules` and any hooks folder, for an offer's view, an acceptance and a return (check_incoming)
    base = git("rev-parse", "HEAD", cwd=repo)
    listed = [".gitattributes", "src/.gitattributes", ".gitmodules", ".githooks/pre-commit", "tools/hooks/check.sh",
              ".husky/pre-push", "src/.claude/agents/a.md"]
    for path in listed + ["src/code.py", "hooks.txt", "src/hooks"]:
        os.makedirs(os.path.dirname(os.path.join(repo, path)) or repo, exist_ok=True)
        with open(os.path.join(repo, path), "w") as fh:
            fh.write("x\n")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "hand-off", cwd=repo)
    got = task_attempt.check_incoming(repo, "HEAD", since=base, protected=[], base=base)
    assert got["loaded_changed"] == sorted(listed)


# ---- each call's class, and settling --------------------------------------------------------------------------------

@pytest.mark.parametrize("command,kind", [
    ("ls -la", "local"), ("cat a.txt | grep x > out.txt", "local"), ("git status && git diff", "local"),
    ("git push origin pp/task-x", "git_remote"), ("git push -u origin HEAD", "git_remote"),
    ("g''it push origin main", "git_remote"),
    ("cd sub && git push", "external"), ("cd sub && git push origin pp/task-x", "external"),
    ("git push --delete origin pp/task-x", "external"),
    ("git push --force origin main", "external"), ("curl https://example.org", "external"),
    ("echo $(curl -s x)", "external"), ("make deploy", "external"), ("make deploy-prod", "unclassified"),
    ("pytest -q", "unclassified"), ("./run.sh", "unclassified"), ("/bin/ls", "unclassified"),
    ("find . -exec rm {} ;", "unclassified"), ("echo $(ls)", "unclassified"), ("ls; ./x", "unclassified"),
])
def test_a_command_s_class(command, kind):
    got = task_attempt.classify("Bash", {"command": command}, external=["make deploy"], repo_slug="o/n",
                                branch="pp/task-x")
    assert got["kind"] == kind, got
    if kind == "git_remote":
        assert got["push"]["remote"] == "origin"


def _owners(command, external=("make deploy",)):
    return task_attempt.classify("Bash", {"command": command}, external=list(external), repo_slug="o/n",
                                 branch="pp/task-x")


# an owner's external command is matched
# through a wrapper that runs the rest of its words as the command: `timeout` (with its options and its duration),
# `nohup`, `nice`, `env` (with its options and assignments), `time`, `stdbuf`, `setsid`, `command` and `exec`; and,
# inside `sh -c`, `bash -c` and `zsh -c`, the commands of the string. Each case is unclassified where no wrapper is read
@pytest.mark.parametrize("command", [
    pytest.param("timeout 900 make deploy", id="timeout"),
    pytest.param("timeout -k 5 -s KILL 10m make deploy", id="timeout-short-options"),
    pytest.param("timeout --kill-after=5 --signal KILL --foreground 9 make deploy", id="timeout-long-options"),
    pytest.param("timeout -vk5 -sKILL 9 make deploy", id="timeout-a-cluster-and-attached-values"),
    pytest.param("timeout --kill=5 9 make deploy", id="timeout-a-long-option-s-start"),
    pytest.param("nohup make deploy", id="nohup"),
    pytest.param("nohup -- make deploy", id="nohup-dashdash"),
    pytest.param("nice make deploy", id="nice"),
    pytest.param("nice -n 5 make deploy", id="nice-options"),
    pytest.param("nice -n5 make deploy", id="nice-an-attached-value"),
    pytest.param("nice -5 make deploy", id="nice-the-older-adjustment"),
    pytest.param("nice --adjustment=5 make deploy", id="nice-a-long-option"),
    pytest.param("nice --adj 5 make deploy", id="nice-a-long-option-s-start-and-its-value"),
    pytest.param("env make deploy", id="env"),
    pytest.param("env A=1 B=2 make deploy", id="env-assignments"),
    pytest.param("env -i -u HOME -C sub A=1 make deploy", id="env-options-and-assignments"),
    pytest.param("env A-B=x make deploy", id="env-takes-any-word-with-an-equals-sign"),
    pytest.param("env --unset=HOME --chdir sub make deploy", id="env-long-options"),
    pytest.param("env - A=1 make deploy", id="env-a-lone-dash"),
    pytest.param("env --ignore-signal=PIPE make deploy", id="env-an-optional-value"),
    pytest.param("/usr/bin/env make deploy", id="env-by-its-path"),
    pytest.param("time make deploy", id="time"),
    pytest.param("time -p make deploy", id="time-the-shell-s-option"),
    pytest.param("time -f %e -o out.txt -a make deploy", id="time-gnu-time-s-options"),
    pytest.param("time --format=%e make deploy", id="time-a-long-option"),
    pytest.param("stdbuf -oL -e0 make deploy", id="stdbuf"),
    pytest.param("stdbuf --output=L make deploy", id="stdbuf-a-long-option"),
    pytest.param("stdbuf -o L make deploy", id="stdbuf-a-separate-value"),
    pytest.param("setsid make deploy", id="setsid"),
    pytest.param("setsid -f -w make deploy", id="setsid-options"),
    pytest.param("setsid -fw make deploy", id="setsid-a-cluster"),
    pytest.param("setsid --wait make deploy", id="setsid-a-long-option"),
    pytest.param("command make deploy", id="command"),
    pytest.param("command -p make deploy", id="command-p"),
    pytest.param("exec make deploy", id="exec"),
    pytest.param("exec -a deployer -cl make deploy", id="exec-options"),
    pytest.param("exec -la deployer make deploy", id="exec-a-cluster-with-a-value"),
    pytest.param("nice -n 5 timeout 900 env A=1 make deploy", id="wrappers-in-wrappers"),
    pytest.param("A=1 timeout 900 make deploy", id="an-assignment-before-a-wrapper"),
    pytest.param("sh -c 'make deploy'", id="sh-c"),
    pytest.param("bash -c 'cd sub && make deploy'", id="bash-c-the-string-s-second-command"),
    pytest.param("zsh -c 'make deploy'", id="zsh-c"),
    pytest.param("bash -lc 'echo x; make deploy'", id="bash-lc"),
    pytest.param("bash -o pipefail -c 'make deploy | tee log'", id="bash-o-takes-a-word"),
    pytest.param("sh -ec 'make deploy'", id="sh-ec"),
    pytest.param("bash -c -e 'make deploy'", id="bash-reads-options-past-c"),
    pytest.param("bash --rcfile /dev/null -c 'make deploy'", id="bash-rcfile-takes-a-word"),
    pytest.param("bash --init-file /dev/null -c 'make deploy'", id="bash-init-file-takes-a-word"),
    # from zsh's manual (its --emulate takes the mode's word), zsh not being installed here to measure it
    pytest.param("zsh --emulate sh -c 'make deploy'", id="zsh-emulate-takes-a-word"),
    pytest.param("/bin/sh -c 'make deploy'", id="sh-by-its-path"),
    pytest.param("sh -c 'timeout 60 make deploy'", id="a-wrapper-in-a-shell-s-string"),
    pytest.param("timeout 60 sh -c 'make deploy'", id="a-shell-in-a-wrapper"),
    pytest.param("bash -c \"sh -c 'make deploy'\"", id="a-shell-in-a-shell-s-string"),
    pytest.param("sh -c 'make deploy \"$0\"' prod", id="the-string-before-its-arguments"),
    # bash's options, sh's read as
    # bash's, as measured here with bash and dash (a long option with one dash, at the start alone; a c after +; a
    # lone + as nothing and a lone - ending them; o and O taking the next word, in a cluster too), zsh's from its
    # manual (O takes no word; o takes its name from the rest of its word); bash's time keyword's assignments; nice's
    # older adjustments. Where a shell would run nothing (dash refusing a bash option, --help), the reading errs
    # toward a match
    pytest.param("bash -login -c 'make deploy'", id="bash-a-long-option-with-one-dash"),
    pytest.param("bash -noprofile -norc -c 'make deploy'", id="bash-long-options-with-one-dash"),
    pytest.param("bash -posix -c 'make deploy'", id="bash-posix-with-one-dash"),
    pytest.param("bash -rcfile /dev/null -c 'make deploy'", id="bash-rcfile-with-one-dash-takes-a-word"),
    pytest.param("bash -init-file /dev/null -c 'make deploy'", id="bash-init-file-with-one-dash-takes-a-word"),
    pytest.param("sh +c 'make deploy'", id="sh-a-c-after-plus"),
    pytest.param("bash +ec 'make deploy'", id="bash-a-c-after-plus-in-a-cluster"),
    pytest.param("bash -c + 'make deploy'", id="a-lone-plus-is-no-option"),
    pytest.param("sh + -c 'make deploy'", id="a-lone-plus-before-c"),
    pytest.param("bash -c - 'make deploy'", id="a-lone-dash-after-c-ends-the-options"),
    pytest.param("bash -O extglob -c 'make deploy'", id="bash-O-takes-a-word"),
    pytest.param("bash +O extglob -c 'make deploy'", id="bash-plus-O-takes-a-word"),
    pytest.param("bash +o posix -c 'make deploy'", id="bash-plus-o-takes-a-word"),
    pytest.param("bash -cO extglob 'make deploy'", id="bash-O-in-a-cluster-takes-the-next-word"),
    pytest.param("zsh -O -c 'make deploy'", id="zsh-O-takes-no-word"),
    pytest.param("zsh +O -c 'make deploy'", id="zsh-plus-O-takes-no-word"),
    pytest.param("zsh -o noglob -c 'make deploy'", id="zsh-o-takes-the-next-word"),
    pytest.param("zsh -onoglob -c 'make deploy'", id="zsh-o-takes-the-rest-of-its-word"),
    pytest.param("zsh +o noglob -c 'make deploy'", id="zsh-plus-o-takes-the-next-word"),
    pytest.param("zsh +c 'make deploy'", id="zsh-a-c-after-plus"),
    pytest.param("time FOO=1 make deploy", id="time-an-assignment-after-it"),
    pytest.param("time -p FOO=1 BAR=2 make deploy", id="time-an-option-then-assignments"),
    pytest.param("nice --5 make deploy", id="nice-a-dashdash-adjustment"),
    pytest.param("nice -+5 make deploy", id="nice-a-dashplus-adjustment"),
    # a wrapper's option this reader does not take (another platform's wrapper may have it) leaves it unread, and
    # then the pattern is matched by its text in the wrapper's words, or by its words anywhere among them
    pytest.param("env -S 'make deploy'", id="env-S-unread-matched-by-its-text"),
    pytest.param("timeout --no-such-option 9 make deploy", id="an-unknown-option-unread-matched-by-its-words"),
    pytest.param("env --ignore A=1 echo make deploy", id="a-long-option-s-start-two-share-unread"),
])
def test_an_owner_s_external_command_is_matched_through_a_wrapper_and_in_a_shell_s_string(command):
    got = _owners(command)
    assert (got["kind"], got["target"]) == ("external", "make deploy"), got


# what a wrapper or a shell does not run as its command is not the owner's command: these are not matched; they pin
# the reading's edges: each wrapper's options and operands as WRAPPERS lists them
# (where a reading that gave up would match by the words anywhere), command -v, and the string a shell runs
@pytest.mark.parametrize("command", [
    pytest.param("timeout 900 make deploy-prod", id="another-command"),
    pytest.param("timeout -k 5 10 echo make deploy", id="timeout-runs-echo"),
    pytest.param("timeout --kill=5 9 echo make deploy", id="timeout-a-long-option-s-start-runs-echo"),
    pytest.param("nohup -- echo make deploy", id="nohup-runs-echo"),
    pytest.param("nice -5 echo make deploy", id="nice-runs-echo"),
    # nice's older adjustments read as such, where a reading that gave up would match by the words anywhere (the
    # matched cases alone cannot tell them from an unread nice)
    pytest.param("nice --5 echo make deploy", id="nice-a-dashdash-adjustment-runs-echo"),
    pytest.param("nice -+5 echo make deploy", id="nice-a-dashplus-adjustment-runs-echo"),
    pytest.param("env -i A=1 echo make deploy", id="env-runs-echo"),
    pytest.param("time -f %e -o out.txt echo make deploy", id="time-runs-echo"),
    pytest.param("stdbuf -oL echo make deploy", id="stdbuf-runs-echo"),
    pytest.param("setsid -fw echo make deploy", id="setsid-runs-echo"),
    pytest.param("command -p echo make deploy", id="command-runs-echo"),
    pytest.param("exec -a x -cl echo make deploy", id="exec-runs-echo"),
    pytest.param("command -v make deploy", id="command-v-describes"),
    pytest.param("sh -c 'echo make deploy'", id="the-string-runs-echo"),
    pytest.param("sh -c 'echo x' make deploy", id="the-words-after-the-string-are-its-arguments"),
    pytest.param("sh -c make deploy", id="the-string-is-one-word"),
    pytest.param("bash deploy.sh make deploy", id="a-shell-given-a-file"),
    pytest.param("bash -- -c 'make deploy'", id="after-dashdash-c-is-a-file"),
    pytest.param("command -V make deploy", id="command-V-describes"),
    # the shells' readings: bash takes a long option with one dash at the start
    # alone, after which its letters are a cluster whose o takes -c; --rcfile takes the next word, -c too; bash's o in
    # a cluster takes the next word, not the rest of its own; zsh's O takes none, so the word after it is a file
    pytest.param("bash -l -login -c 'make deploy'", id="bash-a-long-option-after-a-short-one-is-letters"),
    pytest.param("bash -rcfile -c 'make deploy'", id="bash-rcfile-takes-the-next-word-even-c"),
    pytest.param("bash -onoglob -c 'make deploy'", id="bash-o-takes-the-next-word-not-the-rest-of-its-own"),
    pytest.param("zsh -O extglob -c 'make deploy'", id="zsh-O-takes-no-word-so-the-next-is-a-file"),
])
def test_what_a_wrapper_or_a_shell_does_not_run_is_not_the_owner_s_external_command(command):
    assert _owners(command)["kind"] == "unclassified"


def test_a_pattern_s_glob_is_matched_through_a_wrapper_and_in_a_shell_s_string():
    assert _owners("timeout -k 5 10 make deploy-prod", external=["make deploy-*"])["kind"] == "external"
    assert _owners("bash -c 'cd sub && make deploy-prod'", external=["make deploy-*"])["kind"] == "external"
    # an unread wrapper: its words anywhere among the wrapper's, which a text alone would not match
    assert _owners("timeout --no-such-option 9 make deploy-prod", external=["make deploy-*"])["kind"] == "external"
    assert _owners("timeout -k 5 10 make ship", external=["make deploy-*"])["kind"] == "unclassified"


def test_a_shell_s_string_past_the_depth_read_is_looked_in_by_its_text():
    def nested(command, times):
        for _ in range(times):
            command = "sh -c " + shlex.quote(command)
        return command
    deepest = task_attempt.SHELL_DEPTH
    assert _owners(nested("make deploy", deepest))["kind"] == "external"
    assert _owners(nested("echo make deploy", deepest))["kind"] == "unclassified"
    assert _owners(nested("echo make deploy", deepest + 1))["kind"] == "external"


# an owner's external command
# that itself starts with a wrapper is matched when the model runs it exactly as written, which a line read
# through its wrappers and a pattern not would miss. A pattern is read as a line's command is, and matched as written,
# under another wrapper, inside a -c string, and as the command it runs through its wrappers. The command env
# runs is run with env's assignments
_WRAPPED_PATTERNS = [
    ("timeout 600 ./scripts/deploy.sh", "./scripts/deploy.sh", "timeout"),
    ("nohup ./scripts/release.sh", "./scripts/release.sh", "nohup"),
    ("nice -n 5 ./deploy.sh", "./deploy.sh", "nice"),
    ("env DEPLOY_ENV=prod ./deploy.sh", "DEPLOY_ENV=prod ./deploy.sh", "env"),
    ("time ./deploy.sh", "./deploy.sh", "time"),
    ("stdbuf -oL ./deploy.sh", "./deploy.sh", "stdbuf"),
    ("setsid ./deploy.sh", "./deploy.sh", "setsid"),
    ("command ./deploy.sh", "./deploy.sh", "command"),
    ("exec ./deploy.sh", "./deploy.sh", "exec"),
    ("timeout 600 sh -c './deploy.sh'", "./deploy.sh", "a-wrapper-then-sh-c"),
]


def _wrapped_pattern_cases():
    for pattern, runs, name in _WRAPPED_PATTERNS:
        yield pytest.param(pattern, pattern, id=f"{name}-as-written")
        yield pytest.param(pattern, "timeout 9 " + pattern, id=f"{name}-under-another-wrapper")
        yield pytest.param(pattern, "bash -c " + shlex.quote(pattern), id=f"{name}-in-a-shell-s-string")
        yield pytest.param(pattern, runs, id=f"{name}-the-command-it-runs")
    for pattern, line, name in (
            ("sh -c './deploy.sh'", "./deploy.sh", "a-pattern-s-c-string-of-one-command"),
            ("sh -c './deploy.sh'", "nohup ./deploy.sh", "a-pattern-s-c-string-under-a-wrapper"),
            ("bash -lc 'make deploy'", "make deploy", "a-pattern-s-lc-string"),
            ("DEPLOY_ENV=prod ./deploy.sh", "DEPLOY_ENV=prod ./deploy.sh", "a-pattern-s-assignment-as-written"),
            ("timeout 600 ./deploy.sh", "timeout --no-such-option 9 ./deploy.sh", "an-unread-wrapper-in-the-line"),
            ("timeout 600 ./deploy.sh", 'echo "$(./deploy.sh)"', "a-line-that-cannot-be-split"),
            # a pattern that runs no command of its own, a wrapper alone or with its options alone, is matched as
            # written: what follows it on the line is its command; under another
            # wrapper, at a step of the line's reading between its first and its last
            ("nohup", "nohup make deploy", "a-pattern-that-is-a-wrapper-alone"),
            ("nice -n 19", "nice -n 19 ./build.sh", "a-pattern-that-is-a-wrapper-and-its-options"),
            ("nohup", "timeout 9 nohup make deploy", "a-wrapper-alone-under-another-wrapper"),
            ("nice -n 19", "timeout 60 nice -n 19 ./build.sh", "a-wrapper-and-its-options-under-another-wrapper"),
            # a pattern's string's command read as a line's is, through its wrappers, and a string in it too (the same)
            ("sh -c 'nohup ./deploy.sh'", "./deploy.sh", "a-pattern-s-string-read-through-its-wrapper"),
            ("sh -c \"bash -c './deploy.sh'\"", "./deploy.sh", "a-pattern-s-string-in-a-string"),
            # the assignments an owner's command names are its own: held by those
            # the line's command runs with, in either spelling, with more of its own, through a wrapper, into a
            # shell's string and after time; env's own as GNU env sets them (coreutils 9.4, measured): the last of a
            # name winning, and -u and -i before its NAME=VALUE words; a pattern's value is a glob, as its words are
            ("DEPLOY_ENV=prod ./deploy.sh", "env DEPLOY_ENV=prod ./deploy.sh", "a-pattern-s-assignment-as-env-s"),
            ("DEPLOY_ENV=prod ./deploy.sh", "DEPLOY_ENV=prod FOO=1 ./deploy.sh", "a-line-with-more-assignments"),
            ("DEPLOY_ENV=prod ./deploy.sh", "DEPLOY_ENV=prod nohup ./deploy.sh", "an-assignment-through-a-wrapper"),
            ("DEPLOY_ENV=prod ./deploy.sh", "DEPLOY_ENV=prod bash -c './deploy.sh'", "an-assignment-into-a-string"),
            ("DEPLOY_ENV=prod ./deploy.sh", "time DEPLOY_ENV=prod ./deploy.sh", "an-assignment-after-time"),
            ("DEPLOY_ENV=prod ./deploy.sh", "env DEPLOY_ENV=staging DEPLOY_ENV=prod ./deploy.sh",
             "the-last-of-a-name-wins"),
            ("DEPLOY_ENV=prod ./deploy.sh", "env -u DEPLOY_ENV DEPLOY_ENV=prod ./deploy.sh",
             "env-s-u-before-its-assignments"),
            ("DEPLOY_ENV=prod ./deploy.sh", "DEPLOY_ENV=staging env -i DEPLOY_ENV=prod ./deploy.sh",
             "env-s-i-before-its-assignments"),
            ("DEPLOY_ENV=pro* ./deploy.sh", "DEPLOY_ENV=prod ./deploy.sh", "a-pattern-s-value-is-a-glob"),
            ("DEPLOY_ENV=prod sh -c './deploy.sh'", "DEPLOY_ENV=prod ./deploy.sh",
             "a-pattern-s-assignment-before-its-string"),
            # a pattern with env's -C, -i or -u is matched as written, under a wrapper too
            ("env -C /srv/app ./deploy.sh", "nohup env -C /srv/app ./deploy.sh", "a-pattern-s-env-C-under-a-wrapper"),
            ("env -u HOME ./deploy.sh", "env -u HOME ./deploy.sh", "a-pattern-s-env-u-as-written"),
            # where the line cannot be split, or a wrapper's options cannot be read, by a form's text with its
            # assignments, or its words where the assignments before that wrapper hold its own
            ("env DEPLOY_ENV=staging ./deploy.sh", 'echo "$(true)"; DEPLOY_ENV=staging ./deploy.sh',
             "a-line-that-cannot-be-split-with-the-assignment"),
            ("DEPLOY_ENV=staging ./deploy.sh", "DEPLOY_ENV=staging timeout --no-such-option 9 ./deploy.sh",
             "an-unread-wrapper-after-the-assignment"),
    ):
        yield pytest.param(pattern, line, id=name)


@pytest.mark.parametrize("pattern,line", list(_wrapped_pattern_cases()))
def test_a_pattern_that_starts_with_a_wrapper_is_matched_as_written_and_as_the_command_it_runs(pattern, line):
    got = _owners(line, external=[pattern])
    assert (got["kind"], got["target"]) == ("external", pattern), got


# and not where the line runs another command: a pattern's wrapper alone, and one command of a pattern's string of
# several, are not the owner's command
@pytest.mark.parametrize("pattern,line", [
    pytest.param("timeout 600 ./deploy.sh", "./deploy-staging.sh", id="another-script"),
    pytest.param("timeout 600 ./deploy.sh", "timeout 600 ./other.sh", id="the-same-wrapper-another-command"),
    pytest.param("env DEPLOY_ENV=prod ./deploy.sh", "env ./other.sh", id="env-another-command"),
    pytest.param("command ./deploy.sh", "command -v ./deploy.sh", id="command-v-describes"),
    pytest.param("nohup", "pytest -q", id="a-wrapper-alone-runs-nothing"),
    pytest.param("sh -c 'make build && make deploy'", "make build", id="a-string-of-two-commands-is-not-split"),
    pytest.param("timeout 600 ./deploy.sh", 'echo "$(./other.sh)"', id="a-line-that-cannot-be-split-another"),
    # an owner's command runs with the assignments it names, so a line that
    # runs its program with another value, or with none, is not it, in either spelling and across them; nor where the
    # line's own env takes the name out (-u, by its long name and attached) or empties the environment (-i, a lone -),
    # nor where a later assignment of the name wins
    pytest.param("DEPLOY_ENV=prod ./deploy.sh", "./deploy.sh", id="a-pattern-s-assignment-is-its-own"),
    pytest.param("env DEPLOY_ENV=prod ./deploy.sh", "./deploy.sh", id="env-s-assignment-is-its-own"),
    pytest.param("DEPLOY_ENV=prod sh -c './deploy.sh'", "./deploy.sh", id="an-assignment-before-a-pattern-s-string"),
    pytest.param("env DEPLOY_ENV=prod ./deploy.sh", "env DEPLOY_ENV=staging ./deploy.sh", id="another-value-env-s"),
    pytest.param("DEPLOY_ENV=prod ./deploy.sh", "DEPLOY_ENV=staging ./deploy.sh", id="another-value-the-shell-s"),
    pytest.param("env DEPLOY_ENV=prod ./deploy.sh", "DEPLOY_ENV=staging ./deploy.sh", id="another-value-across"),
    pytest.param("DEPLOY_ENV=prod ./deploy.sh", "env DEPLOY_ENV=staging ./deploy.sh", id="another-value-across-back"),
    pytest.param("DEPLOY_ENV=prod ./deploy.sh", "DEPLOY_ENV=prod env -u DEPLOY_ENV ./deploy.sh",
                 id="a-line-s-env-u-takes-it-out"),
    pytest.param("DEPLOY_ENV=prod ./deploy.sh", "DEPLOY_ENV=prod env --unset=DEPLOY_ENV ./deploy.sh",
                 id="a-line-s-env-unset-takes-it-out"),
    pytest.param("DEPLOY_ENV=prod ./deploy.sh", "DEPLOY_ENV=prod env -uDEPLOY_ENV ./deploy.sh",
                 id="a-line-s-env-u-attached-takes-it-out"),
    pytest.param("DEPLOY_ENV=prod ./deploy.sh", "DEPLOY_ENV=prod env -i ./deploy.sh", id="a-line-s-env-i-empties-it"),
    pytest.param("DEPLOY_ENV=prod ./deploy.sh", "DEPLOY_ENV=prod env - ./deploy.sh", id="a-line-s-env-dash-empties-it"),
    pytest.param("DEPLOY_ENV=prod ./deploy.sh", "env DEPLOY_ENV=prod DEPLOY_ENV=staging ./deploy.sh",
                 id="a-later-assignment-wins"),
    # and a pattern is read through no env that runs its command in another folder or with names taken out (-C, -u,
    # -i): from that env on it is its command as written
    pytest.param("env -C /srv/app ./deploy.sh", "./deploy.sh", id="a-pattern-s-env-C-is-as-written"),
    pytest.param("env -u HOME ./deploy.sh", "./deploy.sh", id="a-pattern-s-env-u-is-as-written"),
    pytest.param("env -i PATH=/usr/bin ./deploy.sh", "PATH=/usr/bin ./deploy.sh", id="a-pattern-s-env-i-is-as-written"),
    # where the line cannot be split, or a wrapper's options cannot be read, a form's bare words do not stand for it
    pytest.param("env DEPLOY_ENV=staging ./deploy.sh", 'x="$(date)"; env DEPLOY_ENV=prod ./deploy.sh',
                 id="a-line-that-cannot-be-split-another-value"),
    pytest.param("env DEPLOY_ENV=staging ./deploy.sh", "timeout --no-such-option 9 env DEPLOY_ENV=prod ./deploy.sh",
                 id="an-unread-wrapper-another-value"),
])
def test_what_does_not_run_a_wrapped_pattern_s_command_is_not_matched(pattern, line):
    assert _owners(line, external=[pattern])["kind"] == "unclassified"


# two of the owner's commands that differ only in their environment are two,
# and a line is recorded under the one it runs, whichever is listed first, in either spelling; alone, neither takes a
# line that runs the other, nor does the first listed take both lines
@pytest.mark.parametrize("staging,prod", [
    pytest.param("env DEPLOY_ENV=staging ./deploy.sh", "env DEPLOY_ENV=prod ./deploy.sh", id="env-s"),
    pytest.param("DEPLOY_ENV=staging ./deploy.sh", "DEPLOY_ENV=prod ./deploy.sh", id="the-shell-s"),
])
def test_two_of_the_owner_s_commands_that_differ_only_in_their_environment_are_two(staging, prod):
    lines = {"env DEPLOY_ENV=staging ./deploy.sh": staging, "DEPLOY_ENV=staging ./deploy.sh": staging,
             "env DEPLOY_ENV=prod ./deploy.sh": prod, "DEPLOY_ENV=prod ./deploy.sh": prod}
    for listed in ([staging, prod], [prod, staging]):
        for line, own in lines.items():
            got = _owners(line, external=listed)
            assert (got["kind"], got["target"]) == ("external", own), (listed, line, got)
        assert _owners("./deploy.sh", external=listed)["kind"] == "unclassified"
    for line, own in lines.items():
        other = prod if own == staging else staging
        assert _owners(line, external=[other])["kind"] == "unclassified", (other, line)


def test_a_pull_request_is_github_only_for_the_task_s_repository_and_head_and_tools_by_their_class():
    pr = "gh pr create --repo o/n --head pp/task-x --title t --body b"
    got = task_attempt.classify("Bash", {"command": pr}, repo_slug="o/n", branch="pp/task-x")
    assert got["kind"] == "github" and got["pr"] == {"repo": "o/n", "head": "pp/task-x"}
    for other in (pr.replace("o/n", "o/other"), pr.replace("pp/task-x", "main"), "gh pr create --fill"):
        assert task_attempt.classify("Bash", {"command": other}, repo_slug="o/n", branch="pp/task-x")["kind"] \
            == "external", other
    assert task_attempt.classify("Read", {})["kind"] == "read"
    assert task_attempt.classify("EndConversation", {})["kind"] == "read"
    assert task_attempt.classify("Write", {})["kind"] == "local"
    assert task_attempt.classify("mcp__proofpane__ask_owner", {})["kind"] == "own_tool"
    assert task_attempt.classify("mcp__proofpane__task_state", {})["kind"] == "read"
    assert task_attempt.classify("mcp__issues__create", {})["kind"] == "external"
    assert task_attempt.classify("SomethingNew", {})["kind"] == "unclassified"


# a command is `local` only when the reader can show it is: GNU sed's
# e command and the s command's e flag start a program (--sandbox refuses both, measured with GNU sed 4.9); an
# assignment before a program can have it start another; an option of git's can start a pager, an editor or an
# external diff; and a word the shell gives otherwise than written (a variable, a pattern of names the attempt can
# make) can become an option that starts one, or sed's script
@pytest.mark.parametrize("command,kind", [
    ("sed -n '1,10p' a.txt", "local"), ("sed -i.bak 's/a/b/' a.txt", "local"), ("sed '$d' a.txt", "local"),
    ("sed 's/[0-9]*//' a.txt", "local"), ("sed -e 's/a/b/' --expression='s/c/d/' a.txt", "local"),
    ("sed --sandbox -n '1e ./deploy.sh' a.txt", "local"), ("sed -n '1e git push origin x' a.txt", "external"),
    ("sed -n '1e ./deploy.sh' a.txt", "unclassified"), ("sed 's/x/./e' a.txt", "unclassified"),
    ("sed --expression='1e ./deploy.sh' a.txt", "unclassified"), ("sed -- '1e ./deploy.sh' a.txt", "unclassified"),
    ("sed '1e ./deploy.sh' -- --sandbox", "unclassified"), ("sed -f script a.txt", "unclassified"),
    ("sed -nf script a.txt", "unclassified"), ("sed --fi=script a.txt", "unclassified"),
    ("sed -ne p a.txt", "unclassified"), ('sed "$S" a.txt', "unclassified"), ("sed -n p *.txt", "unclassified"),
    ("LD_PRELOAD=./x.so cat a.txt", "unclassified"), ("LD_PRELOAD=./x.so; cat a.txt", "unclassified"),
    ("GIT_EXTERNAL_DIFF=./x git diff", "unclassified"), ("git grep -O./x foo", "unclassified"),
    ("git grep -nO foo", "unclassified"), ("git grep --open=./x foo", "unclassified"), ("git grep -e Oops", "local"),
    ("git branch --edit-description", "unclassified"), ("git add -e", "unclassified"),
    ("git diff --ext-diff", "unclassified"), ("git diff --no-ext-diff", "local"),
    ("git cat-file --filters HEAD:a.txt", "unclassified"), ("rg foo *", "unclassified"), ("rg foo '*'", "local"),
    ("rg foo \\*", "local"), ("rg x {a,b}", "unclassified"),
    ("rg --pr=./x foo", "unclassified"), ("rg --pre-glob='*.gz' foo", "local"), ("sort --co=./x a.txt", "unclassified"),
    ("find . -name '*.py'", "local"), ("find . -name *.py", "unclassified"), ("cat *.txt", "local"),
    # --sandbox after sed's script (POSIXLY_CORRECT stops its options there);
    # ripgrep's and git's options that start a program, each entry of the lists; a pattern of file names ([ ?])
    ("sed '1e ./deploy.sh' --sandbox a.txt", "unclassified"),
    ("sed -e '1e ./deploy.sh' --sandbox a.txt", "unclassified"), ("sed -n --sandbox '1e ./deploy.sh' a.txt", "local"),
    ("sed -n '-e1e ./deploy.sh' -- --sandbox", "unclassified"),     # after --, a file's name
    ("rg -z foo", "unclassified"), ("rg -nz foo", "unclassified"), ("rg --search-zip foo", "unclassified"),
    ("rg --hostname-bin=./x --hyperlink-format=default foo", "unclassified"), ("rg -n foo", "local"),
    ("git log --show-signature -1", "unclassified"), ("git log -p --ext-diff", "unclassified"), ("git log -1", "local"),
    ("git show --show-signature HEAD", "unclassified"), ("git show --ext-diff HEAD", "unclassified"),
    ("git show --textconv HEAD:a.txt", "unclassified"), ("git show HEAD", "local"),
    ("git reflog -p --ext-diff", "unclassified"), ("git reflog --show-signature", "unclassified"),
    ("git reflog", "local"), ("git grep --textconv x", "unclassified"),
    ("git cat-file --textconv HEAD:a.txt", "unclassified"),
    ("sed [a-f] a.txt", "unclassified"), ("sed ? a.txt", "unclassified"), ("rg ?? foo", "unclassified"),
    ("rg [-]z foo", "unclassified"),
])
def test_a_program_that_can_start_another_is_local_only_when_its_words_show_it_does_not(command, kind):
    got = task_attempt.classify("Bash", {"command": command}, repo_slug="o/n", branch="pp/task-x")
    assert got["kind"] == kind, got


def _effect(call_id, kind, intent=None, end=None):
    return {"call_id": call_id, "tool": "Bash", "kind": kind, "target": "x", "intent": intent or {}, "end": end,
            "exit": None, "settled": None, "unseen": False, "at": 0}


def test_a_push_is_settled_by_the_remote_s_ref_against_its_intent(tmp_path):
    remote = str(tmp_path / "remote.git")
    git("init", "-q", "--bare", remote, cwd=str(tmp_path))
    work = str(tmp_path / "work")
    make_repo(work)
    head = git("rev-parse", "HEAD", cwd=work)
    ref = "refs/heads/pp/task-x"
    assert task_attempt.remote_ref(work, remote, ref) == ""
    intent = {"address": remote, "ref": ref, "commit": head, "before": ""}
    # in flight at a stop, the ref still as before: not done
    effects = [_effect("1", "git_remote", dict(intent))]
    task_attempt.settle_effects(effects, work=work)
    assert effects[0]["settled"] == "not_done"
    # at the pushed commit: done
    git("push", "-q", remote, f"{head}:{ref}", cwd=work)
    effects = [_effect("1", "git_remote", dict(intent))]
    task_attempt.settle_effects(effects, work=work)
    assert effects[0]["settled"] == "done"
    # moved by another push meanwhile: unknown
    with open(os.path.join(work, "x"), "w") as fh:
        fh.write("x")
    git("add", "-A", cwd=work)
    git("commit", "-qm", "other", cwd=work)
    git("push", "-q", remote, f"HEAD:{ref}", cwd=work)
    effects = [_effect("1", "git_remote", dict(intent))]
    task_attempt.settle_effects(effects, work=work)
    assert effects[0]["settled"] == "unknown"
    # an unreadable remote, a value before never read, or no git allowed: unknown
    for change in ({"address": str(tmp_path / "gone.git")}, {"before": "unread"}):
        effects = [_effect("1", "git_remote", dict(intent, **change))]
        task_attempt.settle_effects(effects, work=work)
        assert effects[0]["settled"] == "unknown", change
    effects = [_effect("1", "git_remote", dict(intent))]
    task_attempt.settle_effects(effects, work=work, git_ok=False)
    assert effects[0]["settled"] == "unknown"
    # one the turn's result lists as refused: not done, whatever the ref says
    effects = [_effect("1", "git_remote", dict(intent))]
    task_attempt.settle_effects(effects, work=work, denied={"1"})
    assert effects[0]["settled"] == "not_done"


def test_unclassified_external_and_own_tools_settle_by_their_class(tmp_path):
    effects = [_effect("a", "unclassified", end="error"), _effect("b", "unclassified"), _effect("c", "external"),
               _effect("d", "external", end="ok"), _effect("e", "own_tool"), _effect("f", "unclassified", end="ok")]
    task_attempt.settle_effects(effects, work=str(tmp_path))
    assert [e["settled"] for e in effects] == ["ran", "unknown", "unknown", "done", None, "ran"]


@pytest.fixture
def fake_gh(tmp_path, monkeypatch):
    bin_dir = tmp_path / "gh-bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(f"#!{sys.executable}\n" + open(FAKE_GH).read())
    (bin_dir / "gh").chmod(0o755)
    home = tmp_path / "gh-home"
    home.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("HOME", str(home))

    def state(prs):
        (home / "fake-gh.json").write_text(json.dumps({"repos": {"o/n": {"permission": "WRITE", "prs": prs}}}))
    return state


def _gh_workspace(tmp_path, monkeypatch):
    """A task's worktree in a workspace's folder whose record of git's places is kept, as the workspace process keeps
    it from its start: pull_requests runs gh there alone."""
    from proofpane_workspace import workspace as ws_module
    root = tmp_path / "ws"
    work = root / "agents" / AGENT_ID / "tasks" / TASK / "work"
    make_repo(str(work))
    checker = gitplaces.Checker(None, b"k" * 32, suspects=lambda since: ["a1"], hold=lambda *a: None,
                                folders=[str(root)])
    checker.first_look([str(work)])
    monkeypatch.setitem(ws_module._PLACES, str(root), checker)
    return root, work


def test_a_pull_request_is_settled_by_the_ones_from_its_head_against_those_before(tmp_path, fake_gh, monkeypatch):
    _, work = _gh_workspace(tmp_path, monkeypatch)
    work = str(work)
    earlier = {"number": 1, "head": "pp/task-x", "base": "main", "title": "before", "state": "CLOSED"}
    fake_gh([earlier])
    before = task_attempt.pull_requests("o/n", "pp/task-x", work)
    assert before == [1]
    intent = {"repo": "o/n", "head": "pp/task-x", "before": before}
    effects = [_effect("1", "github", dict(intent))]
    task_attempt.settle_effects(effects, work=work)
    assert effects[0]["settled"] == "not_done"          # the earlier one does not count
    fake_gh([earlier, {"number": 2, "head": "pp/task-x", "base": "main", "title": "new", "state": "OPEN"}])
    effects = [_effect("1", "github", dict(intent))]
    task_attempt.settle_effects(effects, work=work)
    assert effects[0]["settled"] == "done"
    effects = [_effect("1", "github", dict(intent, before="unread"))]
    task_attempt.settle_effects(effects, work=work)
    assert effects[0]["settled"] == "unknown"


def test_a_pull_request_s_gh_runs_as_the_workspace_s_own_and_not_where_git_s_places_are_held(tmp_path, fake_gh,
                                                                                               monkeypatch):
    # a pull request's intent and settling run gh as Workspace.github runs it (workspace._gh_env), with git's places
    # checked, in the folder
    # of the workspace the task's worktree is in, with no prompt, no colour, no lazy fetch and the three git
    # pins: listed while git's places are as recorded, and not run, None, once one of them is held (here the user's
    # own git settings, which every git reads), nor for a worktree no workspace's folder holds (gh never runs in that
    # worktree)
    from proofpane_workspace import workspace as ws_module
    fake_gh([{"number": 7, "head": "pp/task-x", "base": "main", "title": "t", "state": "OPEN"}])
    root, work = _gh_workspace(tmp_path, monkeypatch)
    envs = []
    real_run = subprocess.run

    def recording(args, *more, **kw):
        if isinstance(args, (list, tuple)) and args and os.path.basename(str(args[0])) == "gh":
            envs.append(dict(kw.get("env") or {}))
        return real_run(args, *more, **kw)
    monkeypatch.setattr(task_attempt.subprocess, "run", recording)
    assert task_attempt.pull_requests("o/n", "pp/task-x", str(work)) == [7]
    calls = jsonl_of(tmp_path / "gh-home" / "fake-gh-calls.jsonl")
    assert [os.path.realpath(c["cwd"]) for c in calls] == [os.path.realpath(root)]       # gh ran in the folder
    (env,) = envs
    assert (env.get("GH_PROMPT_DISABLED"), env.get("NO_COLOR"), env.get("GIT_NO_LAZY_FETCH")) == ("1", "1", "1"), env
    pins = gitplaces.config_env(ws_module._RUN_PAIRS)
    assert {k: env.get(k) for k in pins} == pins, env
    elsewhere = tmp_path / "elsewhere"
    make_repo(str(elsewhere))
    assert task_attempt.pull_requests("o/n", "pp/task-x", str(elsewhere)) is None
    git("config", "--global", "core.planted", "yes", cwd=str(tmp_path))
    assert task_attempt.pull_requests("o/n", "pp/task-x", str(work)) is None
    assert len(jsonl_of(tmp_path / "gh-home" / "fake-gh-calls.jsonl")) == 1               # and did not run again
    effects = [_effect("1", "github", {"repo": "o/n", "head": "pp/task-x", "before": []})]
    task_attempt.settle_effects(effects, work=str(work))
    assert effects[0]["settled"] == "unknown"


def jsonl_of(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# ---- a start's report -----------------------------------------------------------------------------------------------

def test_each_start_report_is_classed(tmp_path):
    identity = {"email": "ana@example.org", "organization": None, "plan": "Claude Max"}
    ok = task_attempt.report_of({"email": "ana@example.org", "subscriptionType": "Claude Max",
                                 "apiProvider": "firstParty"})
    account = {"kind": "account", "id": "default"}
    assert task_attempt.start_failure(account, ok, identity) == (None, None)

    def fails(report, ident=identity):
        return task_attempt.start_failure(account, report, ident)[0]
    # the account's own
    assert fails(dict(ok, email="bob@example.org")) == "account"
    assert fails(dict(ok, organization="Acme")) == "account"
    assert fails(ok, dict(identity, organization="Acme")) == "account"
    assert fails(dict(ok, plan="Claude Pro")) == "account"
    assert fails(dict(ok, api_key_source=task_attempt.MANAGED_KEY)) == "account"
    # the same on every account
    assert fails(None) == "same_everywhere"
    assert fails(dict(ok, token_source="CLAUDE_CODE_OAUTH_TOKEN")) == "same_everywhere"
    assert fails(dict(ok, api_key_source="ANTHROPIC_API_KEY")) == "same_everywhere"
    assert fails(dict(ok, api_provider="bedrock")) == "same_everywhere"
    assert fails(dict(ok, email=None)) == "same_everywhere"
    assert fails(dict(ok, plan=None)) == "same_everywhere"
    # a key: its helper, and Anthropic's own API for an Anthropic key
    key = {"kind": "key", "provider": "anthropic"}
    helper = task_attempt.report_of({"apiKeySource": "apiKeyHelper", "apiProvider": "firstParty"})
    assert task_attempt.start_failure(key, helper)[0] is None
    assert task_attempt.start_failure(key, dict(helper, api_key_source="ANTHROPIC_API_KEY"))[0] == "same_everywhere"
    assert task_attempt.start_failure(key, dict(helper, api_provider="vertex"))[0] == "same_everywhere"
    assert set(task_shapes.START_FAILURES) == {"account", "same_everywhere", "settings"}


def _auth_claude(tmp_path, answer):
    path = tmp_path / "auth-claude"
    path.write_text(f"#!{sys.executable}\nimport json, sys\n"
                    f"print(json.dumps({answer!r}) if sys.argv[1:3] == ['auth', 'status'] else '2.1.274 (Claude Code)')\n")
    path.chmod(0o755)
    return str(path)


def test_auth_status_s_org_id_is_read_and_can_only_refuse(tmp_path):
    env = {"HOME": str(tmp_path), "PATH": os.environ["PATH"]}
    signed = {"loggedIn": True, "email": "ana@example.org", "subscriptionType": "max", "orgId": "org-1"}
    assert task_attempt.org_id_now(_auth_claude(tmp_path, signed), env) == "org-1"
    assert task_attempt.org_id_now(_auth_claude(tmp_path, dict(signed, orgId=None)), env) is None
    assert task_attempt.org_id_now(_auth_claude(tmp_path, {"loggedIn": False}), env) is None
    assert task_attempt.org_id_now(_auth_claude(tmp_path, {"loggedIn": False, "orgId": "org-1"}), env) is None
    assert task_attempt.claude_version(_auth_claude(tmp_path, signed), env) == "2.1.274"


# ---- which claude a task's attempt runs -----------------------------------------------------------------------------

class _FakeWs:
    def __init__(self, updates, floor=None, path=None):
        self.claude_updates, self.claude_floor, self.claude_path = updates, floor, path


class _FakeAgents:
    def __init__(self, ws):
        self.ws = ws


def test_a_task_s_attempt_runs_the_image_s_claude_or_the_sdk_s_bundled_one_never_the_path_s(monkeypatch, tmp_path):
    from proofpane_workspace import workspace as workspace_module
    monkeypatch.delenv("PP_TASK_CLAUDE", raising=False)
    # in a workspace machine: the image's own, whatever newer build the updater keeps in use for the agent's runs,
    # which an agent's own run takes (Workspace._claude, agents.py)
    newer = "/home/ws/.local/share/claude/versions/2.1.300"
    fly_ws = _FakeWs(True, floor="/usr/local/bin/claude", path=newer)
    assert task_runs.TaskRuns(_FakeAgents(fly_ws)).claude() == "/usr/local/bin/claude"
    assert workspace_module.Workspace._claude(fly_ws) == newer
    # under the local provider: the Agent SDK's bundled one, at the workspace's pin, never a claude first on the PATH
    # (one is put there, and the SDK is required)
    (tmp_path / "claude").write_text("#!/bin/sh\nexit 0\n")
    (tmp_path / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    assert shutil.which("claude") == str(tmp_path / "claude")
    local = task_runs.TaskRuns(_FakeAgents(_FakeWs(False, path=shutil.which("claude"))))
    bundled = task_runs.bundled_claude()
    assert bundled is not None and bundled.endswith(os.path.join("claude_agent_sdk", "_bundled", "claude"))
    assert local.claude() == bundled != shutil.which("claude")
    with open(os.path.join(ROOT, "proofpane_workspace", "requirements.txt")) as fh:
        assert "claude-agent-sdk==0.2.154" in fh.read()
    # the tests' stand-in, by the workspace agent's own environment alone
    monkeypatch.setenv("PP_TASK_CLAUDE", "/tmp/stand-in")
    assert task_runs.TaskRuns(_FakeAgents(_FakeWs(True, floor="/usr/local/bin/claude"))).claude() == "/tmp/stand-in"


def test_the_spec_an_attempt_starts_from_names_task_runs_claude_not_a_session_s(monkeypatch, tmp_path, tws):
    """Through the spec itself: the spec TaskRuns.spec builds, with
    PP_TASK_CLAUDE unset, names TaskRuns.claude()'s claude and not the one a session starts (Workspace._claude). Under
    the local provider that is the Agent SDK's bundled one, never the one first on the PATH; in a workspace machine,
    the image's own, never the newer build the updater keeps in use for sessions. The spec is built in this process,
    from the task's record and message as a real attempt left them, by a Workspace over the stopped agent's folder; it
    is only read, so nothing it names is started."""
    from proofpane_workspace.agents import records_key
    from proofpane_workspace.workspace import Workspace
    put_agent(tws)
    run_id = queue(tws)["run_id"]
    until(tws, run_id)
    tws.stop()
    # a claude first on the PATH, the one a session would start, and no stand-in named by PP_TASK_CLAUDE
    path_bin = tmp_path / "path-bin"
    path_bin.mkdir()
    (path_bin / "claude").write_text("#!/bin/sh\nexit 0\n")
    (path_bin / "claude").chmod(0o755)
    monkeypatch.delenv("PP_TASK_CLAUDE", raising=False)
    monkeypatch.setenv("PATH", f"{path_bin}:{os.environ['PATH']}")
    monkeypatch.setenv("HOME", str(tws.home))
    ws = Workspace(tws.root, records_key=records_key(tws.secret))
    agents, runs = ws.agents, ws.agents.task_runs
    agent = agents.agent(agents._index[run_id])
    sent = task_runs.check_message(json.loads(agents._kept_file(run_id, task_runs.TASK_FILE, agents.read(run_id))))
    kept = runs._kept_record(agent, sent["id"])
    assert sent["backend"] == {"kind": "account", "id": "default"}         # so the start's account is None

    def spec():
        return runs.spec(run_id, agent, sent, kept, None)
    # under the local provider: the SDK's bundled claude, where a session's is the PATH's
    bundled = task_runs.bundled_claude()
    assert bundled is not None and runs.claude_path is None and not ws.claude_updates
    assert ws._claude() == shutil.which("claude") == str(path_bin / "claude")
    assert spec()["claude"] == runs.claude() == bundled != ws._claude()
    # in a workspace machine whose updater took a newer build for the sessions: the image's own, the floor, here at a
    # path of its own, so that neither the newer build nor the PATH's first claude passes for it
    floor, newer = str(tmp_path / "image" / "claude"), str(tmp_path / "versions" / "2.1.300")
    ws.claude_updates, ws.claude_floor, ws.claude_path = True, floor, newer
    assert ws._claude() == newer
    assert spec()["claude"] == runs.claude() == floor not in (newer, shutil.which("claude"))


def test_an_attempt_starts_the_claude_its_spec_names_never_the_one_first_on_the_path(tmp_path):
    """At the start: an attempt's version check, its auth status and its start each run the claude
    its spec names, never the one first on the PATH that a session would start (Workspace._claude). TaskWorkspace's
    stand-in is both PP_TASK_CLAUDE, the spec's claude, and first on its agent's PATH. Here a decoy `claude` is put
    before it there, which writes down any run of it; and the stand-in writes down each version asked of it with the
    folder it was asked in (VERSION_ASKS), and counts each auth status it answers (an orgId list). So the case shows
    that the stand-in answered the attempt's own version check and an auth status before each start, and made the
    start; and that none of the three ran the PATH's claude."""
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    ran = tmp_path / "decoy-ran"
    (decoy / "claude").write_text(f'#!/bin/sh\necho "$*" >> "{ran}"\nexit 3\n')
    (decoy / "claude").chmod(0o755)
    asks = tmp_path / "version-asks.jsonl"
    w = TaskWorkspace(tmp_path, config={"VERSION_ASKS": str(asks)},
                      env={"PATH": f"{decoy}:{tmp_path / 'bin'}:{os.environ['PATH']}"})
    try:
        account_file(w, org_id=["org-stand-in"])        # a list: the stand-in counts each auth status it answers
        put_agent(w, brief="complete done")
        done = until(w, queue(w, identity=CONFIRMED)["run_id"])
        assert done["state"] == "done", done
        starts = [s for s in claude_starts(w) if s["attempt"]]
        assert starts, starts_of(w)                     # the stand-in made the start
        # the attempt's own version check is asked in the account's home (the SDK asks too, through the wrapper, from
        # the agent's folder); its auth status is answered before each start
        asked = [json.loads(line) for line in asks.read_text().splitlines()] if asks.exists() else []
        assert [a for a in asked if a["cwd"] == os.path.realpath(w.home)], asked
        assert (w.home / ".claude" / "stand-in-auth-count").read_text() == str(len(starts))
        assert not ran.exists(), ran.read_text()
    finally:
        w.stop()


# ---- the attempt message, the push's secret shapes --------------------------------

def message(**over):
    packet = _packet()
    out = {"id": TASK, "step": 1, "attempt": 1, "fence": 1, "backend": {"kind": "account", "id": "default"},
           "identity": {"email": "ana@example.org", "organization": None, "plan": "Claude Max", "org_id": "org-1"},
           "chosen_by": "relay", "reserve": False, "cap_usd": None, "check": None, "protected": [], "external": [],
           "repo_slug": None, "repo_address": None, "packet_b64": base64.b64encode(packet).decode(),
           "packet_digest": task_shapes.tagged_digest(task_shapes.RESUME_TAG, packet)}
    out.update(over)
    return out


def test_an_attempt_message_is_held_to_its_shapes():
    assert task_runs.check_message(message())["id"] == TASK
    key = {"kind": "key", "provider": "anthropic"}
    assert task_runs.check_message(message(backend=key, identity=None, cap_usd=5))
    for bad in ({"backend": key, "identity": None}, {"id": "0123"}, {"fence": 0}, {"identity": None},
                {"backend": {"kind": "key", "id": "anthropic"}, "identity": None, "cap_usd": 5},
                {"packet_b64": base64.b64encode(b"x" * (task_shapes.PACKET_MAX + 1)).decode()},
                {"packet_b64": "not base64!"}, {"protected": ["x"] * 21}, {"check": ""}, {"repo_slug": "no-slash"},
                {"chosen_by": "Somebody Else"}, {"cap_usd": 0}, {"repo_address": "--upload-pack=x"}):
        with pytest.raises(Exception):
            task_runs.check_message(message(**bad))
    missing = message()
    del missing["packet_digest"]
    with pytest.raises(Exception):
        task_runs.check_message(missing)


def test_the_push_s_secret_shapes_are_the_session_channel_s():
    from proofpane_open.ade import link
    assert [(n, p.pattern, p.flags) for n, p in task_runs.SECRET_SHAPES] == \
        [(n, p.pattern, p.flags) for n, p in link.SECRET_SHAPES]
    assert task_runs.secret_shape("token=ghp_abc") == "a GitHub token (ghp_, gho_, ghu_, ghs_, ghr_)"
    assert task_runs.secret_shape("nothing here") is None


def test_an_anthropic_key_is_kept_for_a_task_and_is_not_among_a_session_s_providers(tmp_path):
    home = str(tmp_path)
    providers.save(home, "anthropic", "sk-ant-test-0000000000")
    assert providers.keys(home)["anthropic"] == "sk-ant-test-0000000000"
    assert [p["id"] for p in providers.listing(home)["providers"]] == ["kimi", "minimax", "deepseek"]
    assert providers.KEY_ONLY["anthropic"]["env"] == {"ANTHROPIC_BASE_URL": "https://api.anthropic.com"}
    providers.remove(home, "anthropic")
    assert "anthropic" not in providers.keys(home)


# ---- the workspace agent, with the stand-in as a task's claude and the stand-in gh --------------------------------

STAND_IN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_stand_in.py")


class TaskWorkspace:
    """A real workspace agent (workspace_fixtures.Agent) whose tasks run the stand-in claude (PP_TASK_CLAUDE), with
    the stand-in gh on its PATH, a repository `demo`, and a bare remote in the test's folder; `env`, more of the
    agent's environment (PP_WORKSPACE_CLAUDE_SETTINGS, the workspace's own claude settings, for one); `secret`, the one
    the agent is started with at each start (one of its own for each person's workspace, as the provider gives)."""

    def __init__(self, tmp_path, config=None, env=None, secret=None):
        from proofpane_workspace import chat as chats
        from workspace_fixtures import SECRET
        if not chats.available():
            pytest.skip("needs the Claude Agent SDK (requirements-dev.txt installs it)")
        self.env = dict(env or {})
        self.secret = secret or SECRET
        self.tmp = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir(exist_ok=True)
        head = f"#!{sys.executable}\n" + (f"CONFIG = {config!r}\n" if config else "")
        (self.bin / "claude").write_text(head + open(STAND_IN).read())
        (self.bin / "claude").chmod(0o755)
        (self.bin / "gh").write_text(f"#!{sys.executable}\n" + open(FAKE_GH).read())
        (self.bin / "gh").chmod(0o755)
        self.home = tmp_path / "home"
        self.home.mkdir(exist_ok=True)
        self.expected = {}              # run id -> what the task's next attempt must find after it (next_attempt)
        self.root = str(tmp_path / "ws")
        self.remote = str(tmp_path / "remote.git")
        if not os.path.isdir(os.path.join(self.root, "repos", "demo")):
            make_repo(os.path.join(self.root, "repos", "demo"), origin=self.remote)
            git("init", "-q", "--bare", "-b", "main", self.remote, cwd=str(tmp_path))
            git("push", "-q", self.remote, "main", cwd=os.path.join(self.root, "repos", "demo"))
        self.start()

    def start(self):
        from workspace_fixtures import Agent
        info = os.path.join(self.root, "agent.json")
        if os.path.exists(info):
            os.unlink(info)
        self.agent = Agent(self.root, str(self.home), {"PATH": f"{self.bin}:{os.environ['PATH']}",
                                                       "PP_TASK_CLAUDE": str(self.bin / "claude"), **self.env},
                           secret=self.secret)
        self.http = self.agent.http

    def stop(self):
        self.agent.close()

    def demo(self, *args):
        return git(*args, cwd=os.path.join(self.root, "repos", "demo"))


@pytest.fixture
def tws(tmp_path):
    w = TaskWorkspace(tmp_path)
    yield w
    w.stop()


def put_agent(w, agent_id=AGENT_ID, **over):
    """The agent, as the app keeps it here: its unattended runs in dontAsk, with allow rules for its work's tools."""
    body = dict({"id": agent_id, "name": "Builder", "brief": "work on the task", "repo": "demo",
                 "author": {"name": "Ana", "email": "ana@example.org"},
                 "chat_settings": {"allow": ["Write", "Edit", "Bash"]}}, **over)
    r = w.http.put(f"/agents/{agent_id}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def task_work(w, task=TASK, agent_id=AGENT_ID):
    return os.path.join(w.root, "agents", agent_id, "tasks", task, "work")


def commit_in(path, name, text, message="work"):
    with open(os.path.join(path, name), "w") as fh:
        fh.write(text)
    git("add", "-A", cwd=path)
    git("commit", "-qm", message, cwd=path)
    return git("rev-parse", "HEAD", cwd=path)


def offered(w, task_of_owner="ab" * 16, extra=None):
    """A commit an owner's workspace pushed to its task's branch on the bare remote: (commit, the base it is on)."""
    clone = str(w.tmp / "owner-clone")
    if not os.path.isdir(clone):
        git("clone", "-q", w.remote, clone, cwd=str(w.tmp))
    base = git("rev-parse", "HEAD", cwd=clone)
    for name, text in (extra or {"work.txt": "the owner's work\n"}).items():
        os.makedirs(os.path.dirname(os.path.join(clone, name)) or clone, exist_ok=True)
        with open(os.path.join(clone, name), "w") as fh:
            fh.write(text)
    git("add", "-A", cwd=clone)
    git("commit", "-qm", "offered", cwd=clone)
    commit = git("rev-parse", "HEAD", cwd=clone)
    git("push", "-q", w.remote, f"HEAD:refs/heads/pp/task-{task_of_owner}", cwd=clone)
    return commit, base


def accept(w, commit, base=None, task=TASK, agent_id=AGENT_ID):
    return w.http.post(f"/agents/tasks/{task}/fetch", json={"address": w.remote, "commit": commit,
                                                           "purpose": "accept", "agent": agent_id, "base": base,
                                                           "protected": []})


def test_an_acceptance_makes_the_task_s_worktree_at_the_offered_commit_by_its_hash(tws):
    # no git in a held or paused agent's repository before its owner resumes it
    put_agent(tws, paused=True)
    commit, base = offered(tws, extra={"work.txt": "x\n", ".claude/settings.json": '{"hooks": {}}\n'})
    r = accept(tws, commit, base)
    assert r.status_code == 409 and "paused or held" in r.json()["detail"], r.text
    assert not os.path.exists(task_work(tws))
    put_agent(tws)
    tws.demo("branch", f"pp/agent-{AGENT_ID}")      # the agent's own work: an acceptance starts at the offered commit
    (tws.home / ".claude").mkdir(exist_ok=True)
    (tws.home / ".claude" / "CLAUDE.md").write_text("@~/accepted.md\n")
    r = accept(tws, commit, base)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["commit"] == commit and got["descends"] is True
    assert got["loaded_files"] == [".claude/settings.json"]     # a file a next run would load, listed
    work = task_work(tws)
    assert git("rev-parse", "HEAD", cwd=work) == commit
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=work) == "pp/task-" + TASK
    # its checkpoint 0, in the task's own signed record, with the places its watch looks at from the acceptance on,
    # what Claude Code's own CLAUDE.md imports among them
    record = json.load(open(os.path.join(os.path.dirname(work), "task.json")))
    assert record["checkpoint0"] == commit and "mac" in record
    assert os.path.join(os.path.realpath(tws.home), "accepted.md") in record["places"]
    # a second acceptance of the same task is refused, and its attempts are none yet
    assert accept(tws, commit, base).status_code == 409
    assert tws.http.get(f"/agents/tasks/{TASK}/attempts").json() == {"attempts": []}


def test_a_commit_the_fetch_does_not_bring_and_an_unreadable_address_refuse_the_acceptance(tws):
    put_agent(tws)
    assert accept(tws, "1" * 40).status_code == 400
    # an address that cannot be read, the receiving repository's own origin (one that is not its origin is refused
    # before anything is fetched: the next test)
    nowhere = str(tws.tmp / "nowhere.git")
    tws.demo("remote", "set-url", "origin", nowhere)
    r = tws.http.post(f"/agents/tasks/{TASK}/fetch", json={"address": nowhere,
                                                           "commit": "1" * 40, "purpose": "accept",
                                                           "agent": AGENT_ID, "base": None, "protected": []})
    assert r.status_code == 400 and "cannot read" in r.json()["detail"]
    assert not os.path.exists(task_work(tws))


def test_an_acceptance_takes_the_offer_only_into_a_repository_whose_origin_is_its_address(tws):
    # the app finds the receiving agent's repository the task's by the name a clone gets
    # (task_offers._receiving), so an acceptance's fetch could fetch into whatever repository the agent had by that
    # name and make the task's worktree there, whatever its origin. The receiving repository's origin is compared with
    # the offer's address, however either is written, before anything is fetched: else 409, in the app's words
    put_agent(tws)
    commit, base = offered(tws, extra={"work.txt": "x\n"})
    demo = os.path.join(tws.root, "repos", "demo")

    def brought():
        return subprocess.run(["git", "cat-file", "-e", commit + "^{commit}"], cwd=demo,
                              capture_output=True).returncode == 0
    # another repository with the clone's name, demo, its origin a fork whose last part is the address's own
    # (remote.git): one by that name, not by its repository; and one with no origin at all
    for origin in (str(tws.tmp / "fork" / "remote.git"), None):
        if origin is None:
            tws.demo("remote", "remove", "origin")
        else:
            tws.demo("remote", "set-url", "origin", origin)
        r = accept(tws, commit, base)
        assert r.status_code == 409 and r.json()["detail"] == "That agent's repository is not this task's.", r.text
        assert not os.path.exists(task_work(tws)) and not brought()        # nothing fetched, no worktree
    # the task's own repository however its origin is written takes it
    tws.demo("remote", "add", "origin", "file://" + tws.remote + "/")
    r = accept(tws, commit, base)
    assert r.status_code == 200, r.text
    assert git("rev-parse", "HEAD", cwd=task_work(tws)) == commit


def test_the_workspace_s_refusal_of_another_repository_is_the_app_s_own_words():
    # the workspace refuses an acceptance into a repository whose
    # origin is not the task's address in the words the app refuses one that has another name (task_offers' accept),
    # a copy that this keeps equal to the app's own
    from proofpane_open.ade import task_offers
    with open(task_offers.__file__, encoding="utf-8") as fh:
        source = fh.read()
    assert f'AgentError(412, "{task_runs.NOT_THE_TASKS}")' in source


def test_a_worktree_no_task_came_of_goes_with_its_branch_and_the_owner_s_own_delete_keeps_his(tws):
    # the app removes the worktree an acceptance's fetch made when no task came of it
    # (task_offers._forget_worktree, for an offer's acceptance and for custody's hand-off), and its branch with it:
    # kept, as the owner's own deletion of a task keeps it on purpose, each such acceptance would leave a branch
    from proofpane_open.ade import task_offers, workspaces
    from workspace_fixtures import SECRET
    put_agent(tws)
    commit, base = offered(tws, extra={"work.txt": "x\n"})
    assert accept(tws, commit, base).status_code == 200
    demo = os.path.join(tws.root, "repos", "demo")

    def branch(task):
        p = subprocess.run(["git", "rev-parse", "--verify", "--quiet", "refs/heads/pp/task-" + task], cwd=demo,
                           capture_output=True, text=True)
        return p.stdout.strip() if p.returncode == 0 else None
    assert branch(TASK) == commit
    endpoint = workspaces.Endpoint(tws.agent.base, "ws" + tws.agent.base[len("http"):], SECRET)
    task_offers._forget_worktree(endpoint, TASK)
    assert not os.path.exists(os.path.dirname(task_work(tws))) and branch(TASK) is None
    # a branch that moved from where the acceptance made it is not one nothing worked on: it stays
    assert accept(tws, commit, base).status_code == 200
    moved = commit_in(task_work(tws), "more.txt", "work on it\n")
    r = tws.http.request("DELETE", f"/agents/tasks/{TASK}", json={"branch": True})
    assert r.status_code == 200 and r.json() == {"removed": True, "branch_removed": False}, r.text
    assert branch(TASK) == moved
    # the owner's own deletion of a task (the app's, which names no branch) keeps its branch where nothing moved it
    other = "5" * 32
    ended = until(tws, queue(tws, id=other)["run_id"])         # its worktree and branch made at its start
    start = branch(other)
    assert start is not None and os.path.isdir(task_work(tws, task=other)), ended
    # still at its checkpoint 0, where a delete asked for the branch would take it
    with open(os.path.join(os.path.dirname(task_work(tws, task=other)), "task.json")) as fh:
        assert start == json.load(fh)["checkpoint0"], ended
    r = tws.http.delete(f"/agents/tasks/{other}")
    assert r.status_code == 200 and r.json() == {"removed": True}, r.text
    assert not os.path.exists(task_work(tws, task=other)) and branch(other) == start


def _push(w, commit, since, branch_head, address=None, task=TASK, **more):
    return w.http.post(f"/agents/tasks/{task}/push", json=dict({"address": address or w.remote, "commit": commit,
                                                               "since": since, "call_id": "handover:offer-1",
                                                               "branch_head": branch_head}, **more))


def test_a_hand_off_pushes_the_recorded_commit_by_its_hash_after_its_secret_scan(tws):
    put_agent(tws)
    commit, base = offered(tws)
    assert accept(tws, commit, base).status_code == 200
    work = task_work(tws)
    done = commit_in(work, "more.txt", "more work\n")
    later = commit_in(work, "later.txt", "work after the recorded commit\n")
    # the branch moved since the app recorded it: nothing is pushed
    assert _push(tws, done, commit, commit).status_code == 409
    # what is pushed is the recorded commit, by its hash, not the branch's head
    r = _push(tws, done, commit, later)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["pushed"] is True and got["ref"] == "refs/heads/pp/task-" + TASK and got["after"] == done
    assert git("rev-parse", "refs/heads/pp/task-" + TASK, cwd=tws.remote) == done
    # a secret-shaped string in what the push would carry holds it, naming the shape and the file, never the value
    leaked = commit_in(work, "config.txt", "token = ghp_" + "a" * 36 + "\n")
    r = _push(tws, leaked, later, leaked)
    assert r.status_code == 200
    got = r.json()
    assert got["pushed"] is False and got["secret_shape"] == {"shape": "a GitHub token (ghp_, gho_, ghu_, ghs_, ghr_)",
                                                             "file": "config.txt"}
    assert "a" * 36 not in json.dumps(got)
    assert git("rev-parse", "refs/heads/pp/task-" + TASK, cwd=tws.remote) == done


def test_a_hand_off_s_push_carries_no_tag_whatever_the_owner_s_push_follow_tags_says(tws):
    # under the owner's push.followTags, a push would carry an annotated tag on the commit it pushed, and the tag's
    # message, which the scan does not read, would reach the remote. The setting is written before the task is made, so
    # it is in its watch's baseline, and refs/tags is no place the watch looks at
    (tws.home / ".gitconfig").write_text("[push]\n\tfollowTags = true\n")
    put_agent(tws)
    commit, base = offered(tws)
    assert accept(tws, commit, base).status_code == 200
    work = task_work(tws)
    done = commit_in(work, "more.txt", "more work\n")
    git("tag", "-a", "v-notes", "-m", "deploy key ghp_" + "t" * 36, done, cwd=work)
    # the control: git's own push from the worktree, with the owner's settings, carries the tag
    control = str(tws.tmp / "control.git")
    git("init", "-q", "--bare", control, cwd=str(tws.tmp))
    subprocess.run(["git", "push", "--quiet", "--no-verify", "--", control, f"{done}:refs/heads/pp/control"],
                   cwd=work, check=True, capture_output=True,
                   env={"PATH": os.environ["PATH"], "HOME": str(tws.home), "LANG": "C"})
    assert git("for-each-ref", "--format=%(refname)", "refs/tags", cwd=control) == "refs/tags/v-notes"
    r = _push(tws, done, commit, done)
    assert r.status_code == 200, r.text
    assert r.json()["pushed"] is True and r.json()["secret_shape"] is None
    assert git("rev-parse", "refs/heads/pp/task-" + TASK, cwd=tws.remote) == done
    assert git("for-each-ref", "--format=%(refname)", "refs/tags", cwd=tws.remote) == ""


GITHUB_TOKEN_SHAPE = "a GitHub token (ghp_, gho_, ghu_, ghs_, ghr_)"


def test_the_push_s_secret_scan_reads_every_commit_every_file_and_every_added_line(tmp_path):
    # the push carries every commit between `since` and `commit`: a scan that read the net change alone, passed over
    # a file git shows as binary, or took an added line beginning "++" for a file's header would let each of these
    # reach the remote
    repo = str(tmp_path / "repo")
    since = make_repo(repo)

    def scan(start, end):
        return task_runs.TaskRuns._scan(None, repo, start, end)

    def token(c):
        return "ghp_" + c * 36

    # a secret one commit wrote and the next took out
    commit_in(repo, "config.txt", f"token = {token('a')}\n")
    removed = commit_in(repo, "config.txt", "token = (none)\n")
    assert token("a") not in git("diff", since, removed, cwd=repo)          # the control: the net change holds none
    assert scan(since, removed) == {"shape": GITHUB_TOKEN_SHAPE, "file": "config.txt"}
    # a file the worktree's own attributes mark -diff, which git shows as binary
    marked = commit_in(repo, ".gitattributes", "secret.txt -diff\n")
    secret = commit_in(repo, "secret.txt", f"token = {token('b')}\n")
    assert "Binary files" in git("diff", marked, secret, cwd=repo)          # the control: git's own diff hides it
    assert scan(marked, secret) == {"shape": GITHUB_TOKEN_SHAPE, "file": "secret.txt"}
    # a file git shows as binary by its bytes
    with open(os.path.join(repo, "data.bin"), "wb") as fh:
        fh.write(b"x\0y " + token("c").encode() + b"\n")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "data", cwd=repo)
    data = git("rev-parse", "HEAD", cwd=repo)
    assert "Binary files" in git("diff", secret, data, cwd=repo)
    assert scan(secret, data) == {"shape": GITHUB_TOKEN_SHAPE, "file": "data.bin"}
    # added lines that begin with "++", with and without a space after them
    plus = commit_in(repo, "plus.txt", f"++{token('d')}\n")
    assert scan(data, plus) == {"shape": GITHUB_TOKEN_SHAPE, "file": "plus.txt"}
    spaced = commit_in(repo, "spaced.txt", f"++ {token('e')}\n")
    assert scan(plus, spaced) == {"shape": GITHUB_TOKEN_SHAPE, "file": "spaced.txt"}
    # a merge whose own resolution adds the secret, in neither of its parents
    git("checkout", "-qb", "side", cwd=repo)
    commit_in(repo, "side.txt", "side\n")
    git("checkout", "-q", "main", cwd=repo)
    commit_in(repo, "main.txt", "main\n")
    git("merge", "-q", "--no-commit", "--no-ff", "side", cwd=repo)
    with open(os.path.join(repo, "merged.txt"), "w") as fh:
        fh.write(f"token = {token('f')}\n")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "merge", cwd=repo)
    merge = git("rev-parse", "HEAD", cwd=repo)
    assert scan(spaced, merge) == {"shape": GITHUB_TOKEN_SHAPE, "file": "merged.txt"}
    # a side branch's secret, written and taken out before it is merged: its commits are read too
    git("checkout", "-qb", "aside", cwd=repo)
    commit_in(repo, "aside.txt", f"token = {token('g')}\n")
    commit_in(repo, "aside.txt", "token = (none)\n")
    git("checkout", "-q", "main", cwd=repo)
    git("merge", "-q", "--no-ff", "-m", "merge aside", "aside", cwd=repo)
    aside = git("rev-parse", "HEAD", cwd=repo)
    assert scan(merge, aside) == {"shape": GITHUB_TOKEN_SHAPE, "file": "aside.txt"}
    # a line break other than a newline earlier in the hunk (a form feed, a lone carriage return and their like must
    # not end the hunk's reading, every line it added after them going unread), and one on the secret's own line (lines
    # split as a text stream splits them, at a lone carriage return, would put the secret on a line of its own that
    # does not begin with "+")
    last = aside
    for n, text in enumerate(("import os\n\x0c# Section two\n", "first line\r\r\n", "page one\x0bpage two\n",
                              "page one\x85page two\n", "page one\u2028page two\n", "page one\x1cpage two\n",
                              "notes\x0c", "progress 10%\r", "page one\u2029")):
        name = f"break{n}.txt"
        with open(os.path.join(repo, name), "w", encoding="utf-8", newline="") as fh:
            fh.write(f"{text}token = {token('h')}\n")
        git("add", "-A", cwd=repo)
        git("commit", "-qm", name, cwd=repo)
        broken = git("rev-parse", "HEAD", cwd=repo)
        assert scan(last, broken) == {"shape": GITHUB_TOKEN_SHAPE, "file": name}, repr(text)
        last = broken
    # the control: what holds no secret shape passes, the commits before it not read again
    clean = commit_in(repo, "clean.txt", "nothing to see\n")
    assert scan(last, clean) is None


def test_the_push_s_secret_scan_rests_on_since_and_commit_never_on_a_ref_of_the_repository_s(tmp_path):
    # leaving out the commits refs/remotes/origin/HEAD holds would make the scan's range
    # rest on a ref nothing watches, and on the origin, which need not be the address the push goes to: a process the
    # attempt left behind could point the ref at the secret's commit, and the push would carry it
    repo = str(tmp_path / "repo")
    make_repo(repo)
    since = commit_in(repo, "shared.txt", "one\ntwo\nthree\n")
    leaked = commit_in(repo, "config.txt", 'TOKEN = "ghp_' + "j" * 36 + '"\n')
    git("update-ref", "refs/remotes/origin/main", leaked, cwd=repo)
    git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main", cwd=repo)
    assert task_runs.TaskRuns._scan(None, repo, since, leaked) == {"shape": GITHUB_TOKEN_SHAPE, "file": "config.txt"}
    # nor a replace ref (git log follows one, the push's packing does not): a commit of the secret's commit's tree on
    # `since` would stand in for it, and the push carry the real one
    tidied = commit_in(repo, "config.txt", "TOKEN = None\n")
    stand_in = git("commit-tree", f"{tidied}^{{tree}}", "-p", since, "-m", "tidied", cwd=repo)
    git("replace", tidied, stand_in, cwd=repo)
    assert task_runs.TaskRuns._scan(None, repo, since, tidied) == {"shape": GITHUB_TOKEN_SHAPE, "file": "config.txt"}
    git("replace", "-d", tidied, cwd=repo)
    # so the commits a task merges in from its default branch are read too: a secret shape that branch added and took
    # out holds the push, the remote's copy of the branch known here or not (its cost: the owner, told to remove it,
    # lifts it only with a task whose checkpoint 0 already holds that commit)
    git("checkout", "-qb", "work", since, cwd=repo)
    commit_in(repo, "task.txt", "the task's work\n")
    git("checkout", "-q", "-b", "default", since, cwd=repo)
    commit_in(repo, "fixture.py", 'FAKE = "ghp_' + "h" * 36 + '"\n')
    main = commit_in(repo, "fixture.py", "FAKE = None\n")
    git("update-ref", "refs/remotes/origin/main", main, cwd=repo)
    git("checkout", "-q", "work", cwd=repo)
    git("merge", "-q", "--no-ff", "-m", "merge the default branch", "default", cwd=repo)
    merged = git("rev-parse", "HEAD", cwd=repo)
    assert task_runs.TaskRuns._scan(None, repo, since, merged) == {"shape": GITHUB_TOKEN_SHAPE, "file": "fixture.py"}


def test_a_merge_s_file_git_counts_as_binary_is_read_as_text(tmp_path):
    # git's combined diff shows a merge's binary file as binary whatever --text
    # says, so read by it, a clean merge of a file the attributes mark -diff (a lockfile, changed on both sides) would
    # hold every later hand-off as a file the scan could not read, and nothing but rewriting the branch would lift it
    repo = str(tmp_path / "repo")
    make_repo(repo)
    commit_in(repo, ".gitattributes", "lock.txt -diff\n")
    lines = [f"line {i}\n" for i in range(1, 21)]
    since = commit_in(repo, "lock.txt", "".join(lines))
    git("checkout", "-qb", "side", cwd=repo)
    commit_in(repo, "lock.txt", "".join(lines[:2] + ["line 3, the side's\n"] + lines[3:]))
    git("checkout", "-q", "main", cwd=repo)
    commit_in(repo, "lock.txt", "".join(lines[:17] + ["line 18, main's\n"] + lines[18:]))
    git("merge", "-q", "--no-ff", "-m", "merge", "side", cwd=repo)                  # clean
    merged = git("rev-parse", "HEAD", cwd=repo)
    assert "Binary files differ" in git("log", "-1", "--cc", "-p", "--text", "--format=", merged, cwd=repo)
    assert task_runs.TaskRuns._scan(None, repo, since, merged) is None
    # and a merge's own bytes in a file git shows as binary by them: read as text, its secret found
    git("checkout", "-qb", "other", since, cwd=repo)
    commit_in(repo, "other.txt", "the other side\n")
    git("checkout", "-q", "main", cwd=repo)
    git("merge", "-q", "--no-commit", "--no-ff", "other", cwd=repo)
    with open(os.path.join(repo, "data.bin"), "wb") as fh:
        fh.write(b"x\0the merge's own " + ("ghp_" + "n" * 36).encode() + b"\n")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "merge other", cwd=repo)
    own = git("rev-parse", "HEAD", cwd=repo)
    assert task_runs.TaskRuns._scan(None, repo, merged, own) == {"shape": GITHUB_TOKEN_SHAPE, "file": "data.bin"}


@pytest.mark.parametrize("diff_merges", [None, "combined", "dense-combined"])
def test_the_push_s_secret_scan_reads_merges_and_root_commits_whatever_git_s_log_settings_say(tmp_path,
                                                                                                    diff_merges):
    # -m reads log.diffMerges, and under combined a merge is git's combined
    # diff, which shows a binary file as binary whatever --text says: after a text file's hunk, the "Binary files"
    # line would be read as one of its lines, and a merge's own secret pushed; log.showRoot=false would hide a root
    # commit's diff
    repo = str(tmp_path / "repo")
    since = make_repo(repo)
    if diff_merges:
        git("config", "log.diffMerges", diff_merges, cwd=repo)
    git("config", "log.showRoot", "false", cwd=repo)
    git("checkout", "-qb", "other", cwd=repo)
    commit_in(repo, "other.txt", "the other side\n")
    git("checkout", "-q", "main", cwd=repo)
    commit_in(repo, "main.txt", "main's own\n")
    git("merge", "-q", "--no-commit", "--no-ff", "other", cwd=repo)
    with open(os.path.join(repo, "a-first.txt"), "w") as fh:          # a text file of the merge's own, before it
        fh.write("the merge's own text\n")
    with open(os.path.join(repo, "data.bin"), "wb") as fh:
        fh.write(b"x\0the merge's own " + ("ghp_" + "p" * 36).encode() + b"\n")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "merge other", cwd=repo)
    merged = git("rev-parse", "HEAD", cwd=repo)
    assert task_runs.TaskRuns._scan(None, repo, since, merged) == {"shape": GITHUB_TOKEN_SHAPE, "file": "data.bin"}
    # a clean merge of a file the attributes mark -diff, which holds no secret, is not held
    commit_in(repo, ".gitattributes", "lock.txt -diff\n")
    lines = [f"line {i}\n" for i in range(1, 21)]
    base = commit_in(repo, "lock.txt", "".join(lines))
    git("checkout", "-qb", "side", cwd=repo)
    commit_in(repo, "lock.txt", "".join(lines[:2] + ["line 3, the side's\n"] + lines[3:]))
    git("checkout", "-q", "main", cwd=repo)
    commit_in(repo, "lock.txt", "".join(lines[:17] + ["line 18, main's\n"] + lines[18:]))
    git("merge", "-q", "--no-ff", "-m", "merge side", "side", cwd=repo)
    assert task_runs.TaskRuns._scan(None, repo, base, git("rev-parse", "HEAD", cwd=repo)) is None
    # a root commit, read against nothing
    git("checkout", "-q", "--orphan", "lone", cwd=repo)
    git("rm", "-rqf", ".", cwd=repo)
    root = commit_in(repo, "lone.txt", "TOKEN = ghp_" + "r" * 36 + "\n")
    assert task_runs.TaskRuns._scan(None, repo, since, root) == {"shape": GITHUB_TOKEN_SHAPE, "file": "lone.txt"}


def test_a_scan_that_runs_out_of_time_holds_the_push(tmp_path, monkeypatch):
    repo = str(tmp_path / "repo")
    since = make_repo(repo)
    commit = commit_in(repo, "clean.txt", "nothing to see\n")
    assert task_runs.TaskRuns._scan(None, repo, since, commit) is None        # the control

    def slow(args, **kw):
        raise subprocess.TimeoutExpired(args, kw.get("timeout"))

    monkeypatch.setattr(task_runs, "_git_output", slow)
    assert task_runs.TaskRuns._scan(None, repo, since, commit) == {"shape": "a change the scan could not read",
                                                                     "file": None}


@pytest.mark.parametrize("program", ["textconv", "command", "external"])
def test_the_push_s_secret_scan_reads_a_submodule_as_the_commit_id_the_push_carries(tmp_path, monkeypatch,
                                                                                         program):
    # under the owner's diff.submodule=diff, a scan's git log would
    # show a submodule's change as that repository's own diff, read with its own settings, so its diff driver's
    # textconv or command, or its external diff, would run in the scan's git, and its files, a binary one and a secret
    # shape among them, would hold a push that carries the submodule's commit id and none of its files
    repo = str(tmp_path / "repo")
    since_first = make_repo(repo)
    sub = os.path.join(repo, "sub")
    os.makedirs(sub)
    git("init", "-q", "-b", "main", cwd=sub)
    commit_in(sub, "a.txt", "one\n")
    git("add", "sub", cwd=repo)
    git("commit", "-qm", "a submodule", cwd=repo)
    since = git("rev-parse", "HEAD", cwd=repo)
    assert since != since_first
    mark = tmp_path / f"{program}-ran"
    script = tmp_path / f"{program}.sh"
    script.write_text(f"#!/bin/sh\ntouch {mark}\ncat \"$1\" 2>/dev/null\nexit 0\n")
    script.chmod(0o755)
    if program == "external":
        git("config", "diff.external", str(script), cwd=sub)
    else:
        git("config", f"diff.sub.{program}", str(script), cwd=sub)
        with open(os.path.join(sub, ".git", "info", "attributes"), "w") as fh:
            fh.write("* diff=sub\n")
    commit_in(sub, "a.txt", "TOKEN = ghp_" + "s" * 36 + "\n")
    with open(os.path.join(sub, "b.bin"), "wb") as fh:
        fh.write(b"x\0y\n")
    git("add", "-A", cwd=sub)
    git("commit", "-qm", "more", cwd=sub)
    git("add", "sub", cwd=repo)
    git("commit", "-qm", "the submodule moved", cwd=repo)
    commit = git("rev-parse", "HEAD", cwd=repo)
    git("config", "diff.submodule", "diff", cwd=repo)
    real, scans = task_runs._git_output, []

    def seen(args, **kw):
        if list(args[:3]) == ["git", "--no-replace-objects", "log"]:
            scans.append((list(args), kw))
        return real(args, **kw)

    monkeypatch.setattr(task_runs, "_git_output", seen)
    assert task_runs.TaskRuns._scan(None, repo, since, commit) is None and not mark.exists()
    # the control: the scan's own git log without its --submodule option runs the submodule's program, and holds
    [(args, kw)] = scans
    old = [a for a in args if not a.startswith("--submodule")]
    assert len(old) == len(args) - 1
    p = real(old, **kw)
    assert p.returncode == 0 and mark.exists()
    assert b"Binary files " in p.stdout or b"ghp_" in p.stdout


def test_a_secret_a_checkpoint_wrote_and_a_later_one_took_out_holds_the_hand_off(tws):
    put_agent(tws)
    commit, base = offered(tws)
    assert accept(tws, commit, base).status_code == 200
    work = task_work(tws)
    commit_in(work, "config.txt", "token = ghp_" + "a" * 36 + "\n")
    removed = commit_in(work, "config.txt", "token = (none)\n")
    r = _push(tws, removed, commit, removed)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["pushed"] is False and got["secret_shape"] == {"shape": GITHUB_TOKEN_SHAPE, "file": "config.txt"}
    assert "a" * 36 not in json.dumps(got)
    assert "a" * 36 not in git("log", "--all", "-p", cwd=tws.remote)


# ---- the scan's commit objects and paths, its findings' ids and the lifts the push is given -------------------------

KEY = b"k" * 32


def scan_all(repo, since, commit, lifted=(), key=KEY):
    return task_runs.TaskRuns._scan_all(repo, since, commit, lifted, key)


def token(c, n=36):
    return "ghp_" + c * n


def fid(kind, path, text, key=KEY, shape=None, column=None):
    """The id a scan gives the one place `text` holds a GitHub token, by default: finding_id's kind, path, text, shape
    and column."""
    return task_runs.finding_id(key, kind, path, text, shape or GITHUB_TOKEN_SHAPE,
                                column or text.index("ghp_") + 1)


def test_the_push_s_scan_reads_each_commit_object_whole_as_git_keeps_it(tmp_path):
    # the scan reads the commits' messages as well as the lines they add, since the push carries them
    repo = str(tmp_path / "repo")
    since = make_repo(repo)
    said = commit_in(repo, "a.txt", "one\n", message="use the deploy key " + token("m"))
    got = scan_all(repo, since, said)
    assert got["unread"] is None and got["passed"] == []
    [f] = got["findings"]
    assert f["kind"] == "commit" and f["shape"] == GITHUB_TOKEN_SHAPE and f["file"] is None and f["commit"] == said
    assert f["column"] == len("use the deploy key ") + 1
    assert f["finding"] == fid("commit", None, "use the deploy key " + token("m"))
    assert "m" * 36 not in json.dumps(got)
    # a name, which the push carries in the commit object's author and committer lines: two lines, two findings
    git("-c", f"user.name={token('n')}", "commit", "-q", "--allow-empty", "-m", "nothing", cwd=repo)
    named = git("rev-parse", "HEAD", cwd=repo)
    found = scan_all(repo, said, named)["findings"]
    assert [(f["kind"], f["commit"]) for f in found] == [("commit", named)] * 2
    assert found[0]["finding"] != found[1]["finding"]
    # a header of the commit's own, which git log's %an, %ae, %cn, %ce and %B do not show (its raw format does): the
    # commit object read whole, as git keeps it
    tree = git("rev-parse", f"{named}^{{tree}}", cwd=repo)
    raw = (f"tree {tree}\nparent {named}\nauthor A <a@example.com> 1700000000 +0000\n"
           f"committer A <a@example.com> 1700000000 +0000\nx-note {token('x')}\n\nplain\n")
    crafted = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--stdin", "--literally"], cwd=repo,
                             input=raw.encode(), capture_output=True, check=True).stdout.decode().strip()
    shown = git("log", "-1", "--format=%an%n%ae%n%cn%n%ce%n%B", crafted, cwd=repo)
    assert "ghp_" not in shown                          # the control: git log's own fields leave the header out
    [f] = scan_all(repo, named, crafted)["findings"]
    assert f["kind"] == "commit" and f["commit"] == crafted
    # a replace ref, which the push's packing does not follow, does not stand in for a commit
    stand_in = git("commit-tree", f"{said}^{{tree}}", "-p", since, "-m", "clean", cwd=repo)
    git("replace", said, stand_in, cwd=repo)
    assert "ghp_" not in git("cat-file", "commit", said, cwd=repo)       # the control: git follows the replace ref
    assert scan_all(repo, since, said)["findings"][0]["commit"] == said
    git("replace", "-d", said, cwd=repo)
    # the control: a range of commits whose objects hold no shape is not held
    first = commit_in(repo, "b.txt", "two\n")
    clean = commit_in(repo, "c.txt", "three\n")
    assert scan_all(repo, first, clean) == {"unread": None, "findings": [], "passed": []}


def test_the_push_s_scan_reads_each_path_a_commit_adds_as_its_bytes(tmp_path):
    # a file's name, read from git's list of the paths each commit adds. Two cases below are ones
    # a scan of the patch's own "+++" lines alone would miss: an empty new file has none, and a path with a tab is
    # quoted there, where "\tghp_" has no word boundary before the token; the others show what is hidden of a path
    repo = str(tmp_path / "repo")
    since = make_repo(repo)

    def adds(name, text="content\n"):
        os.makedirs(os.path.dirname(os.path.join(repo, name)) or repo, exist_ok=True)
        with open(os.path.join(repo, name), "w") as fh:
            fh.write(text)
        before = git("rev-parse", "HEAD", cwd=repo)
        git("add", "-A", cwd=repo)
        git("commit", "-qm", "work", cwd=repo)
        return before, git("rev-parse", "HEAD", cwd=repo)

    empty = token("e")
    start, made = adds(empty, "")
    assert "+++" not in git("log", "-1", "-p", "--format=", made, cwd=repo)      # the control: no "+++" line
    [f] = scan_all(repo, start, made)["findings"]
    assert f == {"kind": "name", "shape": GITHUB_TOKEN_SHAPE, "file": "\u2026", "line": None, "place": 1,
                 "column": 1, "commit": made, "finding": fid("name", empty, empty)}
    tabbed = "notes\t" + token("t") + ".txt"
    start, made = adds(tabbed)
    header = [line for line in git("log", "-1", "-p", "--format=", made, cwd=repo).splitlines()
              if line.startswith("+++ ")]
    assert header and task_runs.secret_shape(header[0]) is None       # the control: the quoted path hides the token
    [f] = scan_all(repo, start, made)["findings"]
    assert f["kind"] == "name" and f["file"] == "\u2026"
    # a value runs to the first ASCII white space after its match, as the scan reads it: each part of the path it
    # reaches is hidden, the folder's file after it among them, and a part after the white space that ends it is not
    hidden = chr(0x2026)
    start, made = adds("dir/" + token("d") + "/inner.txt")
    [f] = scan_all(repo, start, made)["findings"]
    assert f["file"] == "dir/" + hidden + "/" + hidden
    start, made = adds("dir/" + token("w") + " notes/inner.txt")
    [f] = scan_all(repo, start, made)["findings"]
    assert f["file"] == "dir/" + hidden + "/inner.txt"
    # two values in one part, white space between and after them: the part is hidden once, and the next is said
    start, made = adds("dir/" + token("p") + " and " + token("k") + " x/b.txt")
    assert [f["file"] for f in scan_all(repo, start, made)["findings"]] == ["dir/" + hidden + "/b.txt"] * 2
    # a value glued to one in an earlier part of the path: hiding only
    # a part that held a shape by its own pattern would name the glued token in the finding's file
    glued = "sk-" + "f" * 16 + "/x" + token("g") + ".txt"
    start, made = adds(glued)
    got = scan_all(repo, start, made)
    assert [(f["kind"], f["column"], f["file"]) for f in got["findings"]] == [
        ("name", 1, hidden + "/" + hidden), ("name", 22, hidden + "/" + hidden)]
    assert "g" * 36 not in json.dumps(got)
    # a Fly token's characters can hold a "/": the part after it is the token's own, and hidden too
    start, made = adds("FlyV1 fm2_" + "q" * 20 + "/" + "q" * 20 + "+q==")
    got = scan_all(repo, start, made)
    assert [f["file"] for f in got["findings"]] == [hidden + "/" + hidden] and "q" * 20 not in json.dumps(got)
    # a shape that spans the path's parts hides each part it reaches
    start, made = adds("-----BEGIN RSA/PRIVATE KEY", "x\n")
    [f] = scan_all(repo, start, made)["findings"]
    assert f["shape"] == "a private key (-----BEGIN ... PRIVATE KEY)" and f["file"] == hidden + "/" + hidden
    # a rename adds its new path, here the one shaped like a secret, the old one not
    start, plain = adds("plain.txt", "the same text, which a rename keeps\n")
    git("mv", "plain.txt", "renamed-" + token("r") + ".txt", cwd=repo)
    git("commit", "-qm", "rename", cwd=repo)
    moved = git("rev-parse", "HEAD", cwd=repo)
    assert git("diff", "-M", "--name-status", plain, moved, cwd=repo).startswith("R100")     # the control: a rename
    [f] = scan_all(repo, plain, moved)["findings"]
    assert f["kind"] == "name" and f["file"] == "\u2026" and f["commit"] == moved
    # a path the range does not add is not read for its name: changed or deleted, it was in `since` already
    old = git("rev-parse", "HEAD", cwd=repo)
    with open(os.path.join(repo, empty), "w") as fh:
        fh.write("changed\n")
    os.remove(os.path.join(repo, tabbed))
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "work", cwd=repo)
    assert scan_all(repo, old, git("rev-parse", "HEAD", cwd=repo)) == {"unread": None, "findings": [], "passed": []}
    # a replace ref does not stand in for a commit here either
    start, named = adds("plain/" + token("p") + ".txt")
    stand_in = git("commit-tree", f"{start}^{{tree}}", "-p", start, "-m", "work", cwd=repo)
    git("replace", named, stand_in, cwd=repo)
    assert scan_all(repo, start, named)["findings"][0]["kind"] == "name"
    git("replace", "-d", named, cwd=repo)
    assert since


def test_a_finding_s_id_names_its_kind_path_and_text_and_a_lift_covers_that_and_nothing_else(tmp_path):
    # a lift answers a hold that no change of the task can clear, and must answer that one alone
    repo = str(tmp_path / "repo")
    since = make_repo(repo)
    line = 'FAKE = "' + token("f") + '"'
    first = commit_in(repo, "fixture.py", line + "\n")
    [f] = scan_all(repo, since, first)["findings"]
    assert f["finding"] == fid("line", "fixture.py", line)
    assert fid("line", "fixture.py", line, key=b"o" * 32) != f["finding"]                  # another key
    assert scan_all(repo, since, first, [f["finding"]]) == {"unread": None, "findings": [],
                                                            "passed": [f["finding"]]}
    # the same text taken out and added again by a later commit is the same finding, met once
    commit_in(repo, "fixture.py", "FAKE = None\n")
    again = commit_in(repo, "fixture.py", line + "\n")
    assert scan_all(repo, since, again, [f["finding"]])["passed"] == [f["finding"]]
    # the same text in another file, and another text in the same file, are other findings
    other = commit_in(repo, "other.py", line + "\n")
    [g] = scan_all(repo, since, other, [f["finding"]])["findings"]
    assert g["file"] == "other.py" and g["finding"] != f["finding"]
    changed = commit_in(repo, "fixture.py", line + "  # made up\n")
    found = scan_all(repo, since, changed, [f["finding"]])["findings"]
    assert sorted(h["file"] for h in found) == ["fixture.py", "other.py"]
    # a byte past UTF-8 is its own: two lines that differ in that byte alone have two ids
    with open(os.path.join(repo, "bytes.txt"), "wb") as fh:
        fh.write(b"x\xff " + token("b").encode() + b"\nx\xfe " + token("b").encode() + b"\n")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "work", cwd=repo)
    two = scan_all(repo, changed, git("rev-parse", "HEAD", cwd=repo))["findings"]
    assert len(two) == 2 and two[0]["finding"] != two[1]["finding"]
    # a lifted id the range does not meet is not said to have passed
    assert scan_all(repo, since, first, [f["finding"], "0" * 32])["passed"] == [f["finding"]]


def test_a_scan_reports_up_to_twenty_findings_each_once(tmp_path):
    repo = str(tmp_path / "repo")
    since = make_repo(repo)
    lines = [f"key{n} = {token(chr(ord('a') + n % 26), 36 + n)}" for n in range(25)]
    many = commit_in(repo, "keys.txt", "\n".join(lines + lines[:3]) + "\n")
    got = scan_all(repo, since, many)
    assert len(got["findings"]) == task_runs.FINDINGS_MAX == 20
    assert len({f["finding"] for f in got["findings"]}) == 20
    # with the first twenty lifted, the other five are what holds
    rest = scan_all(repo, since, many, [f["finding"] for f in got["findings"]])
    assert len(rest["findings"]) == 5 and len(rest["passed"]) == 20
    # a line with more values than the findings left to report stops at twenty all the same: eighteen lines of one
    # value, then one of three
    crowded = commit_in(repo, "crowded.txt", "\n".join(lines[:18] + [" ".join(token(c) for c in "xyz")]) + "\n")
    found = scan_all(repo, many, crowded)["findings"]
    assert len(found) == 20 and [f["column"] for f in found[18:]] == [1, len(token("x")) + 2]


def test_what_the_scan_could_not_read_has_no_id_and_no_lift_reaches_it(tmp_path, monkeypatch):
    repo = str(tmp_path / "repo")
    since = make_repo(repo)
    leaked = commit_in(repo, "config.txt", "token = " + token("u") + "\n")
    [f] = scan_all(repo, since, leaked)["findings"]
    monkeypatch.setattr(task_runs, "MAX_SCAN", 10)
    assert scan_all(repo, since, leaked, [f["finding"]]) == {
        "unread": {"shape": "a change too large to scan", "file": None}, "findings": [], "passed": []}
    monkeypatch.setattr(task_runs, "MAX_SCAN", 64 * 1024 * 1024)
    assert scan_all(repo, "f" * 40, leaked, [f["finding"]]) == {
        "unread": {"shape": "a change the scan could not read", "file": None}, "findings": [], "passed": []}
    # each of the scan's git commands that fails holds the push, the first or a later one
    real = task_runs._git_output
    for verb in ("log", "rev-list", "diff-tree", "cat-file"):
        def failing(args, _verb=verb, **kw):
            if list(args[:3]) == ["git", "--no-replace-objects", _verb]:
                return subprocess.CompletedProcess(args, 1, b"", b"")
            return real(args, **kw)
        monkeypatch.setattr(task_runs, "_git_output", failing)
        assert scan_all(repo, since, leaked, [f["finding"]])["unread"] == {
            "shape": "a change the scan could not read", "file": None}, verb
    monkeypatch.setattr(task_runs, "_git_output", real)


def test_the_scan_s_git_commands_share_its_one_time_limit(tmp_path, monkeypatch):
    # four git commands: their time together is the one the app's wait was made for (task_offers'
    # PUSH_WAIT, a scan and a push), so each is given what the others left of SCAN_SECONDS
    repo = str(tmp_path / "repo")
    since = make_repo(repo)
    commit = commit_in(repo, "clean.txt", "nothing to see\n")
    now, given = [1000.0], []
    real = task_runs._git_output

    def slow(args, **kw):
        given.append((args[2], kw["timeout"]))
        now[0] += 50                                    # each command takes 50 seconds
        return real(args, **kw)

    monkeypatch.setattr(task_runs, "time", type("Clock", (), {"monotonic": staticmethod(lambda: now[0])}))
    monkeypatch.setattr(task_runs, "_git_output", slow)
    assert scan_all(repo, since, commit)["unread"] == {"shape": "a change the scan could not read", "file": None}
    assert given == [("log", 120.0), ("rev-list", 70.0), ("diff-tree", 20.0)]
    now[0], given[:] = 1000.0, []
    monkeypatch.setattr(task_runs, "SCAN_SECONDS", 1000)       # the control: with time left, it reads all four
    assert scan_all(repo, since, commit) == {"unread": None, "findings": [], "passed": []}
    assert [verb for verb, _ in given] == ["log", "rev-list", "diff-tree", "cat-file"]


# ---- each of the scan's git commands read through a pipe, MAX_SCAN and one byte at most, and ended there ------------

# A git for the scan's tests, in a folder of the test's: for the command git.json names ("verb"), the real git's own
# output for it, and `more` bytes after it, written to the scan one piece a pipe takes whole (PIPE_BUF) at a time, then
# `wait` seconds before it ends. The folder's "written" says its pid, its piece and the whole output's size, then each
# piece's size once it is written, then "done" once it ends. Every other command is the real git's own.
_STRETCHED_GIT = r"""
import json, os, select, subprocess, sys, time
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "git.json")) as fh:
    told = json.load(fh)
if sys.argv[2:3] != [told["verb"]]:
    os.execv(told["git"], [told["git"], *sys.argv[1:]])
given = sys.stdin.buffer.read() if told["verb"] in ("diff-tree", "cat-file") else None
out = subprocess.run([told["git"], *sys.argv[1:]], input=given, stdout=subprocess.PIPE, check=True).stdout
out += b"x" * told["more"]
record, piece = os.open(os.path.join(here, "written"), os.O_WRONLY | os.O_CREAT | os.O_APPEND), select.PIPE_BUF
os.write(record, f"pid {os.getpid()}\npiece {piece}\ntotal {len(out)}\n".encode())
for at in range(0, len(out), piece):
    os.write(record, f"{os.write(1, out[at:at + piece])}\n".encode())
time.sleep(told.get("wait", 0))
os.write(record, b"done\n")
"""


def _stretched_git(tmp_path, monkeypatch, verb, more=0, wait=0):
    """_STRETCHED_GIT first on the PATH, for `verb`: its folder, where it says what it wrote."""
    folder = tmp_path / "bin"
    folder.mkdir()
    (folder / "git.json").write_text(json.dumps({"git": shutil.which("git"), "verb": verb, "more": more,
                                                 "wait": wait}))
    (folder / "git").write_text(f"#!{sys.executable}\n" + _STRETCHED_GIT)
    (folder / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{folder}:{os.environ['PATH']}")
    return folder


def _written(folder):
    """What _STRETCHED_GIT says it wrote: (its pid, its piece, the whole output's size, the bytes it wrote, done)."""
    lines = (folder / "written").read_text().splitlines()
    told = dict(line.split(" ") for line in lines if " " in line)
    return (int(told["pid"]), int(told["piece"]), int(told["total"]), sum(int(line) for line in lines if line.isdigit()),
            "done" in lines)


def _watched(monkeypatch, call, seconds=60):
    """call() in a thread, each process started meanwhile (subprocess.Popen) killed if it has not returned in
    `seconds`, which fails the test, so that a read that waits for ever fails instead: ({"value": what it returned} or
    {"error": what it raised}, those processes)."""
    started, real = [], subprocess.Popen

    class Started(real):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            started.append(self)

    got = {}

    def run():
        try:
            got["value"] = call()
        except BaseException as e:
            got["error"] = e

    monkeypatch.setattr(subprocess, "Popen", Started)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(seconds)
    late = thread.is_alive()
    if late:
        for p in started:
            p.kill()
        thread.join(30)
    monkeypatch.setattr(subprocess, "Popen", real)
    assert not late, f"not done in {seconds} s; ended: {[p.args for p in started]}"
    return got, started


def _pipe_buffer():
    """What a pipe holds before its writer waits: Linux's says (F_GETPIPE_SZ); macOS's grows to 64 KiB at most."""
    import fcntl
    r, w = os.pipe()
    try:
        return fcntl.fcntl(w, fcntl.F_GETPIPE_SZ) if hasattr(fcntl, "F_GETPIPE_SZ") else 64 * 1024
    finally:
        os.close(r)
        os.close(w)


@pytest.mark.parametrize("verb", ["log", "rev-list", "diff-tree", "cat-file"])
def test_each_git_command_of_the_scan_is_read_to_max_scan_and_one_byte_and_ended_there(tmp_path, monkeypatch,
                                                                                            verb):
    # under subprocess.run, a command's whole output would be in memory before the check against MAX_SCAN
    # refused it, and only SCAN_SECONDS would bound what was read
    repo = str(tmp_path / "repo")
    since = make_repo(repo)
    if verb == "log":                   # a change whose log, as git gives it, is far longer than MAX_SCAN
        commit = commit_in(repo, "big.txt", "".join(f"line {n:06}, nothing to see here\n" for n in range(40000)))
        more = 0
    else:                               # the command's own output, and more after it
        commit = commit_in(repo, "clean.txt", "nothing to see\n")
        more = 2 * 1024 * 1024
    folder = _stretched_git(tmp_path, monkeypatch, verb, more)
    monkeypatch.setattr(task_runs, "MAX_SCAN", 256 * 1024)
    got, _ = _watched(monkeypatch, lambda: scan_all(repo, since, commit))
    assert got == {"value": {"unread": {"shape": task_runs.TOO_LARGE, "file": None}, "findings": [], "passed": []}}
    pid, piece, total, written, done = _written(folder)
    assert total > 4 * task_runs.MAX_SCAN                       # the control: the command had far more to say
    # the amount read, measured where git writes it: what the scan read, and at most a pipe's buffer more, which the
    # stand-in was waiting to write when it was ended; less one piece written that it had not yet recorded
    assert task_runs.MAX_SCAN + 1 - piece <= written <= task_runs.MAX_SCAN + 1 + _pipe_buffer()
    assert not done                                             # ended there, never read to its end
    with pytest.raises(ProcessLookupError):                     # and waited for
        os.kill(pid, 0)


def test_a_scan_whose_git_stalls_holds_the_push_as_unread_at_its_deadline(tmp_path, monkeypatch):
    # the reader inside the real scan, to the scan's deadline as before: a git command that stalls, its output given
    # but not ended, is ended at SCAN_SECONDS and waited for, and the push is held as unread
    repo = str(tmp_path / "repo")
    since = make_repo(repo)
    commit = commit_in(repo, "clean.txt", "nothing to see\n")
    folder = _stretched_git(tmp_path, monkeypatch, "log", wait=120)
    monkeypatch.setattr(task_runs, "SCAN_SECONDS", 4)
    start = time.monotonic()
    got, _ = _watched(monkeypatch, lambda: scan_all(repo, since, commit), seconds=40)
    assert got == {"value": {"unread": {"shape": task_runs.UNREAD, "file": None}, "findings": [], "passed": []}}
    assert time.monotonic() - start < 20
    pid, _, total, written, done = _written(folder)
    assert written == total > 0 and not done                    # its whole log given, and it never ended by itself
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def _program(tmp_path, name, code):
    """A program for _git_output, written in the test's folder, so that one left running is named by the run's
    guard (conftest)."""
    path = tmp_path / name
    path.write_text(code)
    return [sys.executable, str(path)]


def test_a_git_command_s_output_is_read_to_its_limit_and_one_byte_and_no_further(tmp_path, monkeypatch, capfd):
    # one byte past the limit is enough to know the output is too large. The program writes its output in one piece a
    # pipe takes whole (PIPE_BUF), so a read that asked for more than that byte would have more of it, whatever it
    # then kept: what is read is counted where it is read (os.read), not only what is given back
    say = _program(tmp_path, "say.py", "import os, select, sys, time\n"
                                       "n = select.PIPE_BUF if sys.argv[1] == 'piece' else int(sys.argv[1])\n"
                                       "os.write(1, b'a' * n)\n"
                                       "os.write(2, b'e' * int(sys.argv[3]))\n"
                                       "time.sleep(float(sys.argv[2]))\n")
    real_read, read = os.read, []

    def counted(fd, n):
        got = real_read(fd, n)
        read.append(len(got))
        return got

    def output(n, wait, errors=0):
        read.clear()
        monkeypatch.setattr(os, "read", counted)
        try:
            got, _ = _watched(monkeypatch, lambda: task_runs._git_output(
                say + [str(n), str(wait), str(errors)], cwd=str(tmp_path), env=None, timeout=60, limit=100),
                seconds=20)
        finally:
            monkeypatch.setattr(os, "read", real_read)
        return got["value"], sum(read)
    done, taken = output("piece", 120)
    assert (done.stdout, done.returncode, taken) == (b"a" * 101, -signal.SIGKILL, 101)
    # one byte past the limit and nothing more to come: ended at that byte all the same, not waited on for more
    done, taken = output(101, 120)
    assert (done.stdout, done.returncode, taken) == (b"a" * 101, -signal.SIGKILL, 101)
    done, taken = output(100, 0)
    assert (done.stdout, done.returncode, taken) == (b"a" * 100, 0, 100)      # the limit itself is read whole
    # what it says on its standard error goes to /dev/null: so much of it does not stop it, and none of it reaches the
    # workspace's own standard error
    capfd.readouterr()
    done, taken = output(100, 0, errors=1024 * 1024)
    assert (done.stdout, done.returncode, taken) == (b"a" * 100, 0, 100)
    assert "eeee" not in capfd.readouterr().err


def test_a_git_command_s_input_is_written_as_its_output_is_read(tmp_path, monkeypatch):
    # diff-tree --stdin and cat-file --batch answer each line as they read it, and their input is rev-list's output,
    # MAX_SCAN at most: written whole before their output is read, each would wait on the other once a pipe was full
    echo = _program(tmp_path, "echo.py", "import sys\n"
                                         "while piece := sys.stdin.buffer.read1(512):\n"
                                         "    sys.stdout.buffer.write(piece)\n"
                                         "    sys.stdout.buffer.flush()\n")
    given = bytes(range(256)) * 4096                                # 1 MiB, far more than two pipes hold
    got, _ = _watched(monkeypatch, lambda: task_runs._git_output(echo, cwd=str(tmp_path), env=None, timeout=60,
                                                                 limit=2 * len(given), input=given), seconds=30)
    assert (got["value"].stdout, got["value"].returncode) == (given, 0)
    # and as git does, at many times its length (a 41-byte commit id answered by a whole commit object): an echo drains
    # its input as fast as its output fills, so only this shows a write that waits on a full pipe, as one in pieces
    # larger than a pipe takes whole would. The program says 16 bytes for each it reads, then
    # the digest of all it read
    grow = _program(tmp_path, "grow.py", "import hashlib, sys\n"
                                         "seen = hashlib.sha256()\n"
                                         "while piece := sys.stdin.buffer.read1(512):\n"
                                         "    seen.update(piece)\n"
                                         "    sys.stdout.buffer.write(b'x' * (16 * len(piece)))\n"
                                         "    sys.stdout.buffer.flush()\n"
                                         "sys.stdout.buffer.write(seen.hexdigest().encode())\n")
    given = bytes(range(256)) * 1024                                # 256 KiB, four pipes' worth
    answer = b"x" * (16 * len(given)) + hashlib.sha256(given).hexdigest().encode()
    got, _ = _watched(monkeypatch, lambda: task_runs._git_output(grow, cwd=str(tmp_path), env=None, timeout=60,
                                                                 limit=len(answer), input=given), seconds=30)
    assert (got["value"].stdout == answer, got["value"].returncode) == (True, 0)
    # one that ends, or shuts its input, before it has read all of it is read to its end all the same
    early = _program(tmp_path, "early.py", "import os\nos.write(1, b'read none')\nos._exit(3)\n")
    got, _ = _watched(monkeypatch, lambda: task_runs._git_output(early, cwd=str(tmp_path), env=None, timeout=60,
                                                                 limit=1024, input=given), seconds=30)
    assert (got["value"].stdout, got["value"].returncode) == (b"read none", 3)
    # one whose output ends before all its input is written has its input closed, so it is not left waiting for the
    # rest: this one closes its output first, then reads its input to its end
    first = _program(tmp_path, "first.py", "import os, sys\nos.close(1)\nsys.stdin.buffer.read()\nos._exit(3)\n")
    got, _ = _watched(monkeypatch, lambda: task_runs._git_output(first, cwd=str(tmp_path), env=None, timeout=10,
                                                                 limit=1024, input=bytes(1024 * 1024)), seconds=30)
    assert (got.get("value") and (got["value"].stdout, got["value"].returncode)) == (b"", 3), got
    # with no input, its input is /dev/null, which a program that reads it to its end finds empty
    reads = _program(tmp_path, "reads.py", "import os, sys\nos.write(1, b'%d' % len(sys.stdin.buffer.read()))\n")
    got, _ = _watched(monkeypatch, lambda: task_runs._git_output(reads, cwd=str(tmp_path), env=None, timeout=10,
                                                                 limit=1024), seconds=30)
    assert (got["value"].stdout, got["value"].returncode) == (b"0", 0)


@pytest.mark.parametrize("stall", ["its output unfinished", "its output closed", "its input unread"])
def test_a_git_command_still_running_at_the_scan_s_deadline_is_ended(tmp_path, monkeypatch, stall):
    # _git_output alone, given the time the scan has left: a command that stalls, whichever way, is ended once that
    # time is out and waited for, and TimeoutExpired raised, which the scan answers as unread (the scan itself to its
    # deadline: test_a_scan_whose_git_stalls_holds_the_push_as_unread_at_its_deadline)
    code = {"its output unfinished": "import os, time\nos.write(1, b'started')\ntime.sleep(120)\n",
            "its output closed": "import os, time\nos.write(1, b'started')\nos.close(1)\ntime.sleep(120)\n",
            "its input unread": "import time\ntime.sleep(120)\n"}[stall]
    program = _program(tmp_path, "stall.py", code)
    given = bytes(1024 * 1024) if stall == "its input unread" else None
    start = time.monotonic()
    got, [p] = _watched(monkeypatch, lambda: task_runs._git_output(program, cwd=str(tmp_path), env=None, timeout=1,
                                                                   limit=1024, input=given), seconds=20)
    assert isinstance(got.get("error"), subprocess.TimeoutExpired), got
    assert time.monotonic() - start < 10 and p.returncode == -signal.SIGKILL


def test_the_push_takes_the_lifted_ids_and_pushes_past_them_alone(tws):
    put_agent(tws)
    commit, base = offered(tws)
    assert accept(tws, commit, base).status_code == 200
    work = task_work(tws)
    leaked = commit_in(work, "fixture.py", 'FAKE = "' + token("l") + '"\n', message="add a fixture")
    r = _push(tws, leaked, commit, leaked)
    assert r.status_code == 200, r.text
    got = r.json()
    [f] = got["secret_findings"]
    assert got["pushed"] is False and got["lifted_passed"] == []
    assert got["secret_shape"] == {"shape": GITHUB_TOKEN_SHAPE, "file": "fixture.py"}
    assert f["kind"] == "line" and f["file"] == "fixture.py" and f["commit"] == leaked
    assert task_shapes.COMMIT.fullmatch(f["commit"]) and len(f["finding"]) == 32
    # the same scan again gives the same id: the key is the workspace's own, the same at each of its scans
    assert _push(tws, leaked, commit, leaked).json()["secret_findings"][0]["finding"] == f["finding"]
    # a lifted id that is not one is refused, and so is a list past the most; a list of the most is taken
    for bad in (["x"], ["A" * 32], ["0" * 33], "0" * 32, ["0" * 32] * (task_runs.LIFTED_MAX + 1), [None]):
        assert _push(tws, leaked, commit, leaked, lifted=bad).status_code == 400, bad
    assert _push(tws, leaked, commit, leaked, lifted=["0" * 32] * task_runs.LIFTED_MAX).json()["pushed"] is False
    # the app's key for the person pushing, which names a finding the same in any workspace: 64 hex or refused
    for bad in ("x", "AB" * 32, "ab" * 31, 7):
        assert _push(tws, leaked, commit, leaked, findings_key=bad).status_code == 400, bad
    theirs = _push(tws, leaked, commit, leaked, findings_key="ab" * 32).json()["secret_findings"]
    assert [g["finding"] for g in theirs] == [fid("line", "fixture.py", 'FAKE = "' + token("l") + '"',
                                                  key=bytes.fromhex("ab" * 32))] != [f["finding"]]
    # another id lifted holds it still
    assert _push(tws, leaked, commit, leaked, lifted=["0" * 32]).json()["pushed"] is False
    r = _push(tws, leaked, commit, leaked, lifted=[f["finding"]])
    got = r.json()
    assert got["pushed"] is True and got["secret_findings"] == [] and got["lifted_passed"] == [f["finding"]]
    assert git("rev-parse", "refs/heads/pp/task-" + TASK, cwd=tws.remote) == leaked
    # what a later commit adds is scanned again: the lift covers the line it named, not the file
    more = commit_in(work, "fixture.py", 'FAKE = "' + token("l") + '"\nOTHER = "' + token("q") + '"\n')
    got = _push(tws, more, leaked, more, lifted=[f["finding"]]).json()
    assert got["pushed"] is False and [g["file"] for g in got["secret_findings"]] == ["fixture.py"]
    assert got["secret_findings"][0]["finding"] != f["finding"]
    # a path byte that is not UTF-8 reaches the app as \xNN, and the route answers
    try:
        os.makedirs(os.path.join(work.encode(), b"dir\xff"))
    except OSError as e:
        if e.errno != errno.EILSEQ:
            raise
        pytest.skip(f"{NOT_UTF8_REFUSED}: the push of a path holding such a byte, this test's last part, is not run "
                    "here; every check before it was")
    with open(os.path.join(work.encode(), b"dir\xff", b"plain.txt"), "w") as fh:
        fh.write('X = "' + token("z") + '"\n')
    git("add", "-A", cwd=work)
    git("commit", "-qm", "odd", cwd=work)
    odd = git("rev-parse", "HEAD", cwd=work)
    r = _push(tws, odd, more, odd)
    assert r.status_code == 200, r.text
    assert [g["file"] for g in r.json()["secret_findings"]] == ["dir\\xff/plain.txt"]


def test_each_value_on_a_line_is_its_own_finding_and_a_lift_of_one_lifts_no_other(tmp_path):
    # were a finding a line, named by its first shape, with an id covering
    # the whole line, a lift of a made-up token would let a real one beside it go out
    repo = str(tmp_path / "repo")
    since = make_repo(repo)
    line = 'fake = "sk-ant-test-0000"; real = "' + token("r") + '"'
    both = commit_in(repo, "two.txt", line + "\n")
    found = scan_all(repo, since, both)["findings"]
    assert [(f["shape"], f["column"]) for f in found] == [
        ("an Anthropic key (sk-ant-)", line.index("sk-ant-") + 1), (GITHUB_TOKEN_SHAPE, line.index("ghp_") + 1)]
    got = scan_all(repo, since, both, [found[0]["finding"]])
    assert [f["shape"] for f in got["findings"]] == [GITHUB_TOKEN_SHAPE] and got["passed"] == [found[0]["finding"]]
    # two values of one shape on one line are two findings too, each lifted alone
    pair = "a = " + token("a") + ", b = " + token("b")
    two = commit_in(repo, "pair.txt", pair + "\n")
    found = scan_all(repo, both, two)["findings"]
    assert [f["column"] for f in found] == [5, pair.index(token("b")) + 1]
    left = scan_all(repo, both, two, [found[0]["finding"]])["findings"]
    assert [f["finding"] for f in left] == [found[1]["finding"]]
    # one value that two shapes begin at (an Anthropic key is also a key of sk- and 16 characters) is one finding
    one = commit_in(repo, "one.txt", "sk-ant-api03-" + "k" * 20 + "\n")
    assert [(f["shape"], f["column"]) for f in scan_all(repo, two, one)["findings"]] == [
        ("an Anthropic key (sk-ant-)", 1)]


def test_a_value_that_begins_inside_another_match_of_its_shape_is_a_finding_of_its_own(tmp_path):
    # finditer does not overlap, so with each shape's matches found by it,
    # a value that began inside another match of its shape (a pattern that takes on through it) would be no finding,
    # and a lift of a made-up value would let a real one beside it out; measured for these four shapes
    repo = str(tmp_path / "repo")
    at = make_repo(repo)
    pem = "-----BEGIN RSA PRIVATE KEY-----\\n%s\\n-----END RSA PRIVATE KEY-----"
    for line in ('{"fake": "%s", "real": "%s"}' % (pem % "FAKEFAKE", pem % "MIIEREAL"),
                 "OPENAI=sk-" + "f" * 16 + "-sk-" + "r" * 20,
                 "k=AIzaFAKE-AIza" + "x" * 35,
                 "t=eyJfakefakefake-eyJ" + "a" * 12 + ".eyJ" + "b" * 12 + ".sig"):
        [(name, pattern)] = [(n, p) for n, p in task_runs.SECRET_SHAPES if p.search(line)]
        assert len(list(pattern.finditer(line))) == 1          # the control: one match takes the second value in
        # where the shape begins, read by a lookahead at every place: an instrument of its own, not the scan's
        begins = [m.start() + 1 for m in re.finditer("(?=" + pattern.pattern + ")", line)]
        assert len(begins) == 2
        made = commit_in(repo, "values.txt", line + "\n")
        found = scan_all(repo, at, made)["findings"]
        assert [(f["shape"], f["column"], f["line"]) for f in found] == [(name, c, 1) for c in begins], line
        got = scan_all(repo, at, made, [found[0]["finding"]])       # the made-up one lifted: the real one holds
        assert got["passed"] == [found[0]["finding"]] and [f["column"] for f in got["findings"]] == begins[1:]
        at = made


def test_two_findings_of_one_shape_in_one_file_and_commit_say_their_lines_apart(tmp_path):
    # a finding that said only its kind, shape, file, column and commit would read the same for two
    # values of one shape at one column on two lines of one file, or in two lines of one message, or in two paths one
    # commit adds, and the page would ask which of them to lift
    repo = str(tmp_path / "repo")
    make_repo(repo)
    lines = [f"line {n}" for n in range(1, 11)]
    first = commit_in(repo, "settings.py", "\n".join(lines) + "\n")
    lines[3], lines[6] = 'FAKE_TOKEN = "' + token("f") + '"', 'REAL_TOKEN = "' + token("r") + '"'
    lines += ["plain", "OTHER = " + token("o"), "LAST = " + token("l")]
    two = commit_in(repo, "settings.py", "\n".join(lines) + "\n",
                    message="one\n\nsecond " + token("m") + "\nthird " + token("n"))
    hunks = [h[:h.index(" @@", 2) + 3] for h in git("log", "-1", "-p", "--unified=0", "--format=", two,
                                                     cwd=repo).splitlines() if h.startswith("@@")]
    assert hunks == ["@@ -4 +4 @@", "@@ -7 +7 @@", "@@ -10,0 +11,3 @@"]        # the control: three hunks
    found = scan_all(repo, first, two)["findings"]
    in_file = [(f["line"], f["column"], f["place"]) for f in found if f["kind"] == "line"]
    assert in_file == [(n + 1, lines[n].index("ghp_") + 1, 1) for n in (3, 6, 11, 12)]
    assert in_file[:2] == [(4, 15, 1), (7, 15, 1)]         # one column, two lines
    raw = git("cat-file", "commit", two, cwd=repo).split("\n")
    assert [(f["line"], f["place"]) for f in found if f["kind"] == "commit"] == [
        (n + 1, None) for n, row in enumerate(raw) if "ghp_" in row]
    # two paths one commit adds, each said as "\u2026": their places among the paths it changes, as git diff-tree lists
    # them, tell them apart; a path it changes counts too, and a later commit's, which git lists first, does not
    aa = commit_in(repo, "aa.txt", "before\n")

    def commits(*names):
        for name in names:
            with open(os.path.join(repo, name), "w") as fh:
                fh.write("clean\n")
        git("add", "-A", cwd=repo)
        git("commit", "-qm", "paths", cwd=repo)
        return git("rev-parse", "HEAD", cwd=repo)
    paths = commits("a.txt", "aa.txt", token("a") + ".txt", token("b") + ".txt")
    later = commits("z.txt")
    listed = git("diff-tree", "-r", "--no-renames", "--no-commit-id", "--name-status", paths, cwd=repo).splitlines()
    assert [row.split("\t")[0] for row in listed] == ["A", "M", "A", "A"]           # the control: a changed path
    names = [f for f in scan_all(repo, aa, later)["findings"] if f["kind"] == "name"]
    assert [(f["file"], f["line"], f["place"], f["commit"]) for f in names] == [
        ("\u2026", None, n + 1, paths) for n, row in enumerate(listed) if "ghp_" in row]
    assert [f["place"] for f in names] == [3, 4]


def test_a_line_s_digest_is_made_once_and_its_values_are_read_only_as_far_as_the_scan_goes(monkeypatch):
    # each value's id made over its whole line would have one long line of
    # a thousand values, most of them lifted, take 53 seconds; and every value of a line found before the
    # twentieth finding stopped the scan would take 3.34 s and 430 MiB for a line of 1.68 million values
    digests, looked = [], []
    real_digest, real_first = task_runs.line_digest, task_runs._Pattern.first

    def digest(text, start=0, end=None):
        digests.append((len(text) if end is None else end) - start)
        return real_digest(text, start, end)

    def first(self, pos):
        looked.append(pos)
        return real_first(self, pos)
    monkeypatch.setattr(task_runs, "line_digest", digest)
    monkeypatch.setattr(task_runs._Pattern, "first", first)
    line = " ".join([token("v")] * 200)
    ids = [task_runs.finding_id(KEY, "line", "a.txt", line, name, column)
           for column, name in task_runs.secret_matches(line)]
    assert len(ids) == len(set(ids)) == 200
    # an id, written from finding_id's words: HMAC-SHA256 of its kind, path, its text's SHA-256, shape and column
    words = json.dumps(["line", "a.txt", hashlib.sha256(line.encode()).hexdigest(), GITHUB_TOKEN_SHAPE, 1],
                       separators=(",", ":")).encode()
    assert ids[0] == hmac.new(KEY, words, hashlib.sha256).hexdigest()[:32]
    found = task_runs._Findings(ids[:180], KEY)
    digests.clear()
    assert found.add("line", "a.txt", line, "c" * 40, 7) is True                   # twenty not lifted: it stops
    assert digests == [len(line)] and len(found.passed) == 180 and len(found.found) == 20
    assert found.found[0]["line"] == 7 and found.found[0]["finding"] == ids[180]
    # a line of five thousand values, none lifted: it reads twenty of them, and looks no further than the next one's
    # place (each value is 40 characters and a space)
    looked.clear()
    many = task_runs._Findings((), KEY)
    assert many.add("line", "b.txt", " ".join([token("w")] * 5000), "c" * 40) is True and len(many.found) == 20
    assert len(looked) > 0 and max(looked) <= 41 * 20
    # past the scan's time, a line's reading stops, and what holds the push is what the scan could not read
    late = task_runs._Findings((), KEY, deadline=time.monotonic() - 1)
    assert late.add("line", "c.txt", token("x"), "c" * 40) is True and late.found == []
    assert late.result() == {"unread": {"shape": task_runs.UNREAD, "file": None}, "findings": [], "passed": []}
    assert task_runs._Findings((), KEY, deadline=time.monotonic() + 60).add("line", "c.txt", token("x"),
                                                                            "c" * 40) is False   # the control


def test_the_scan_reads_its_values_under_its_own_time_limit(tmp_path, monkeypatch):
    # a line's reading is under SCAN_SECONDS too, not git's commands alone: with no bound, it would add to the time the
    # app waits for the push
    repo = str(tmp_path / "repo")
    since = make_repo(repo)
    leaked = commit_in(repo, "config.txt", "token = " + token("u") + "\n")
    given = []

    class Kept(task_runs._Findings):
        def __init__(self, lifted, key, deadline=None):
            given.append(deadline)
            super().__init__(lifted, key, deadline)
    monkeypatch.setattr(task_runs, "_Findings", Kept)
    monkeypatch.setattr(task_runs, "time", type("Clock", (), {"monotonic": staticmethod(lambda: 1000.0)}))
    assert len(scan_all(repo, since, leaked)["findings"]) == 1
    assert given == [1000.0 + task_runs.SCAN_SECONDS]


# ---- the scan's findings' places, its time, and git's settings it does not follow -----------------------------------

def _said(f):
    """All the page shows of a finding, by which a person tells it from another (TaskHandOff's findingWords)."""
    return (f["kind"], f["shape"], f["file"], f["line"], f["place"], f["column"], f["commit"])


def _entry_places(repo, commit):
    """Each path's place among those `commit` changes, read as a person reads `git diff-tree -r -m --no-renames
    --ignore-submodules=none --root <commit>`: its entries counted from 1, on through a merge's blocks, at the first
    that names the path."""
    out = subprocess.run(["git", "diff-tree", "-r", "-m", "--no-renames", "--ignore-submodules=none", "--root", "-z",
                          commit], cwd=repo, capture_output=True, check=True).stdout.split(b"\0")
    places, entry = {}, 0
    for n, part in enumerate(out):
        if part.startswith(b":"):
            entry += 1
            places.setdefault(out[n + 1].decode("utf-8", "surrogateescape"), entry)
    return places


def _clock(monkeypatch, tick):
    """task_runs' clock made a counter: each look at it is `tick` seconds after the last, from 0."""
    now = [0.0]

    def monotonic():
        now[0] += tick
        return now[0]
    monkeypatch.setattr(task_runs, "time", type("Clock", (), {"monotonic": staticmethod(monotonic)}))
    return now


def test_two_findings_whose_paths_read_the_same_or_in_two_parents_of_a_merge_say_their_places(tmp_path):
    # a line's finding that said its file only as shown, which hides a part shaped
    # like a secret, would read the same for two such files of one commit, or two folders so named; a path's place is
    # not counted again for each parent of a merge; and a path holding a control byte does not read as one that
    # spells it out
    repo = str(tmp_path / "repo")
    since = make_repo(repo)

    def files(*pairs, message="work"):
        for name, text in pairs:
            full = os.path.join(repo.encode(), name if isinstance(name, bytes) else name.encode())
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as fh:
                fh.write(text.encode())
        git("add", "-A", cwd=repo)
        git("commit", "-qm", message, cwd=repo)
        return git("rev-parse", "HEAD", cwd=repo)

    named = [token("a") + ".txt", token("b") + ".txt", token("c") + "/config.py", token("d") + "/config.py"]
    both = files((named[0], "token " + token("x") + "\n"), (named[1], "token " + token("y") + "\n"),
                 (named[2], "one\ntwo\nKEY = '" + token("u") + "'\n"), (named[3], "one\ntwo\nKEY = '" + token("v") + "'\n"))
    found = scan_all(repo, since, both)["findings"]
    lines = [f for f in found if f["kind"] == "line"]
    # the control: without their places, the two files' lines and the two folders' read the same
    hidden = chr(0x2026)
    assert [(f["file"], f["line"], f["column"]) for f in lines] == [
        (hidden, 1, 7), (hidden, 1, 7), (hidden + "/" + hidden, 3, 8), (hidden + "/" + hidden, 3, 8)]
    listed = _entry_places(repo, both)
    assert sorted((f["kind"], f["place"]) for f in found) == sorted(
        (kind, listed[p]) for p in named for kind in ("line", "name"))
    assert len({_said(f) for f in found}) == len(found) == 8
    # a merge: each parent's block lists a path it adds, and a place counts on through them
    git("checkout", "-qb", "side", cwd=repo)
    files(("s1.txt", "clean\n"), (token("e") + ".txt", "clean\n"), message="side")
    git("checkout", "-q", "-", cwd=repo)
    main = files(("m1.txt", "clean\n"), (token("f") + ".txt", "clean\n"), message="main")
    git("merge", "-q", "--no-ff", "-m", "merge", "side", cwd=repo)
    merge = git("rev-parse", "HEAD", cwd=repo)
    listed = _entry_places(repo, merge)
    blocks = git("diff-tree", "-r", "-m", "--no-renames", merge, cwd=repo).splitlines()
    assert blocks.count(merge) == 2 and len(listed) == 4        # the control: two blocks, a path each of them adds
    names = [f for f in scan_all(repo, main, merge)["findings"] if f["kind"] == "name" and f["commit"] == merge]
    assert sorted(f["place"] for f in names) == sorted(listed[token(c) + ".txt"] for c in "ef") == [1, 3]
    assert len({_said(f) for f in names}) == 2
    # a path the merge changes against its first parent and adds against its second: the place is where it is first
    # listed, in the first block, though the second block's entry is the one that adds it
    git("checkout", "-qb", "side2", "HEAD~1", cwd=repo)
    side2 = files(("t1.txt", "clean\n"), message="side2")
    git("checkout", "-q", "-", cwd=repo)
    own = files((token("k") + ".txt", "first\n"), message="main again")
    git("merge", "-q", "--no-ff", "--no-commit", "side2", cwd=repo)
    with open(os.path.join(repo, token("k") + ".txt"), "w") as fh:
        fh.write("changed in the merge\n")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "an evil merge", cwd=repo)
    evil = git("rev-parse", "HEAD", cwd=repo)
    statuses = git("diff-tree", "-r", "-m", "--no-renames", "--name-status", evil, cwd=repo).splitlines()
    assert [row for row in statuses if token("k") in row] == ["M\t" + token("k") + ".txt",
                                                               "A\t" + token("k") + ".txt"]     # the control
    names = [f for f in scan_all(repo, own, evil)["findings"] if f["kind"] == "name" and f["commit"] == evil]
    listed = _entry_places(repo, evil)          # its second block adds side's two paths, which side2 holds none of
    assert sorted(f["place"] for f in names) == sorted(listed[token(c) + ".txt"] for c in "ek") == [1, 3] and side2
    # one path changed by two commits of a range, at a place of its own in each
    files(("a.txt", "x = " + token("m") + "\n"), ("b.txt", "clean\n"), message="one")
    two = files(("0.txt", "clean\n"), ("a.txt", "x = " + token("m") + "\ny = " + token("n") + "\n"), message="two")
    found = scan_all(repo, evil, two)["findings"]
    assert [(f["commit"] == two, f["line"], f["place"]) for f in found] == [(True, 2, 2), (False, 1, 1)]
    # a rename beside a new path: --no-renames lists the rename as the new path and the old, which a place counts
    files(("b_old.txt", "kept\n"))
    before = git("rev-parse", "HEAD", cwd=repo)
    git("mv", "b_old.txt", "b_new.txt", cwd=repo)
    moved = files((token("g") + ".txt", "clean\n"))
    [f] = scan_all(repo, before, moved)["findings"]
    assert f["kind"] == "name" and f["place"] == _entry_places(repo, moved)[token("g") + ".txt"] == 3
    # a path with a control byte, and one that spells that byte out as \x01: shown alike, their places apart
    odd = files((b"a\x01.txt", "t=" + token("h") + "\n"), (b"a\\x01.txt", "t=" + token("i") + "\n"))
    found = [f for f in scan_all(repo, moved, odd)["findings"] if f["kind"] == "line"]
    assert [f["file"] for f in found] == ["a\\x01.txt", "a\\x01.txt"]
    assert len({_said(f) for f in found}) == 2 and sorted(f["place"] for f in found) == [1, 2]


def test_a_long_line_of_the_two_slow_shapes_is_read_in_time_linear_in_its_length(monkeypatch):
    # the private key's and the signed web token's patterns try each place
    # they could begin at on to the line's end, so one 1 MiB line of "-eyJ" and ten letters takes 81 s by the pattern,
    # and 200 KiB of "-----BEGIN " 2.18 s; each is read once, where its parts meet
    jwt, pem = task_runs.SECRET_SHAPES[14][1], task_runs.SECRET_SHAPES[6][1]
    assert jwt.pattern == task_runs._JWT and pem.pattern == task_runs._PEM        # the control: the shapes meant
    for line in (("-eyJ" + "a" * 10) * 75000, "-----BEGIN " * 95000, "eyJ." * 262144, "sk-" * 350000):
        began = time.monotonic()
        assert task_runs.secret_shape(line) in (None, "a key (sk- and 16 or more characters)")
        assert len(list(zip(range(25), task_runs.secret_matches(line)))) in (0, 25)
        assert time.monotonic() - began < 5, line[:20]
    # a reading that runs long between two places looks at the scan's clock as it goes
    for line in ("eyJ." * 262144, "-----BEGIN\n" * 100000):
        _clock(monkeypatch, 1.0)
        with pytest.raises(task_runs._OutOfTime):
            list(task_runs.secret_matches(line, deadline=10.0))
    monkeypatch.undo()
    # where they are found, they are found as their patterns find them (the fuller check: the next test)
    real = "x " + pem.pattern.replace("[^\\n]*", " RSA ") + " " + "eyJ" + "a" * 10 + ".eyJ" + "b" * 10 + ".sig"
    assert [c for c, _ in task_runs.secret_matches(real)] == [3, real.index("eyJ") + 1]


def test_each_shape_is_read_where_its_pattern_finds_it_and_a_glued_value_where_one_the_scan_found_runs(
        monkeypatch):
    # against an instrument of its own: at each place of a line, the
    # first of SECRET_SHAPES whose pattern matches there (re's match at a place reads the character before it for \b);
    # every other place the scan gives is a shape's pattern without its \b, after a place and before white space
    import random
    rnd = random.Random(20261014)
    parts = ["sk-", "sk-ant-", "ghp_", "gho_", "github_pat_", "FlyV1 ", "AKIA", "-----BEGIN", " RSA ", "PRIVATE KEY",
             "adeh_", "PP-", "pp-ABCDEFGH", "ABCDEFGH", "rk_live_", "xoxb-", "AIza", "glpat-", "npm_", "eyJ", ".eyJ",
             ".", "-", "_", " ", "\t", "a", "Z9", "f" * 10, "x" * 16, "q" * 30, "w" * 36, chr(0xE9), ";", '"', "=", "/"]
    patterns = [(name, p) for name, p in task_runs.SECRET_SHAPES]
    glued = [re.compile(p.pattern[2:] if p.pattern.startswith(r"\b") else p.pattern, p.flags) for _, p in patterns]
    space = re.compile(r"[ \t\n\r\f\v]")

    def places(line):
        """The places by their definition: at each place, the first shape whose pattern matches there, or, within a
        value found before it (from its place to the first white space after its match), whose pattern without its
        \\b does."""
        out, value_end = [], 0
        for at in range(len(line)):
            for order, (name, p) in enumerate(patterns):
                m = p.match(line, at) or (at < value_end and glued[order].match(line, at))
                if m:
                    out.append((at + 1, name))
                    after = space.search(line, m.end())
                    value_end = max(value_end, after.start() if after else len(line))
                    break
        return out

    # each shape's edges, which random lines meet seldom: one character short of a match, and a match
    edges = ["eyJ" + "a" * 9 + ".eyJ" + "b" * 10 + ".", "eyJ" + "a" * 10 + ".eyJ" + "b" * 10 + ".",
             "eyJ" + "a" * 10 + ".eyJ" + "b" * 9 + ".", "x eyJ" + "a" * 10 + ".eyJ" + "b" * 10,
             "eyJ" + "a" * 13 + "eyJ" + "c" * 10 + ".eyJ" + "b" * 10 + ".",
             "eyJ" + "a" * 13 + "eyJ" + "c" * 9 + ".eyJ" + "b" * 10 + ".", "-----BEGINPRIVATE KEY",
             "-----BEGIN\nPRIVATE KEY", "-----BEGIN a PRIVATE KEY b PRIVATE KEYxghp_", "sk-" + "a" * 15,
             "sk-" + "a" * 16, "AIza" + "a" * 29, "AIza" + "a" * 30, "npm_" + "a" * 35, "npm_" + "a" * 36,
             "PP-ABCDEFGH", "PP-ABCDEFGHI", "pp-abcdefgh", "sk-ant-xghp_" + "a" * 36, "sk-ant-x ghp_" + "a" * 36,
             "FlyV1 xghp_" + "a" * 36, "FlyV1\txghp_" + "a" * 36]
    for line in edges:
        assert list(task_runs.secret_matches(line)) == places(line), line
    glued_seen = 0
    for _ in range(3000):
        line = "".join(rnd.choice(parts) for _ in range(rnd.randint(1, 40)))
        mine, theirs = list(task_runs.secret_matches(line)), places(line)
        assert mine == theirs, line
        bare = [at + 1 for at in range(len(line)) if any(p.match(line, at) for _, p in patterns)]
        assert set(bare) <= {c for c, _ in mine}, line          # every place a pattern finds, as before
        glued_seen += len(mine) - len(bare)
        assert task_runs.secret_shape(line) == next((n for n, p in patterns if p.search(line)), None), line
    assert glued_seen > 0                       # the control: some lines held a glued value
    # each shape has its reading, and a pattern with another unbounded repeat is refused, not read slowly
    assert [how[0] for how in task_runs._READINGS].count("pattern") == len(patterns) - 2
    with pytest.raises(ValueError):
        task_runs._shape_reading(re.compile(r"\bfoo[a-z]+bar", re.ASCII))


def test_the_scan_s_time_is_looked_at_between_places_of_a_line_and_after_its_last_git(tmp_path, monkeypatch):
    # a scan that reads a line's places past its time, or that runs out of it while reading the commit objects or with a
    # finding already made, answering as if it had read everything, fails here
    many = " ".join([token("v")] * 1000)
    lifted = [task_runs.finding_id(KEY, "line", "a.txt", many, n, c) for c, n in task_runs.secret_matches(many)]
    assert len(lifted) == 1000
    _clock(monkeypatch, 1.0)
    found = task_runs._Findings(lifted, KEY, deadline=50.0)         # out of time after some fifty looks
    assert found.add("line", "a.txt", many, "c" * 40) is True and found.out_of_time
    assert 0 < len(found.passed) < 100
    assert found.result()["unread"] == {"shape": task_runs.UNREAD, "file": None}
    # out of time after one finding: the push is held as unread, never asked to lift what a whole scan did not find
    two = token("a") + " " + token("b")
    now = _clock(monkeypatch, 0.0)
    real_shown = task_runs.shown

    def late_shown(path, deadline=None):
        now[0] = 10.0 ** 9                          # the time runs out as the first finding's path is said
        return real_shown(path, None)
    monkeypatch.setattr(task_runs, "shown", late_shown)
    found = task_runs._Findings((), KEY, deadline=2.5)
    assert found.add("line", "b.txt", two, "c" * 40) is True and len(found.found) == 1
    assert found.result() == {"unread": {"shape": task_runs.UNREAD, "file": None}, "findings": [], "passed": []}
    monkeypatch.setattr(task_runs, "shown", real_shown)
    # a scan whose time runs out once its last git command has run, as it reads the commit objects: unread, not clean
    repo = str(tmp_path / "repo")
    since = make_repo(repo)
    clean = commit_in(repo, "a.txt", "nothing here\n", message="a plain message")
    real_run, ran = task_runs._git_output, []

    def run(args, *a, **k):
        ran.append(args[2] if args[:1] == ["git"] else None)
        return real_run(args, *a, **k)
    now = _clock(monkeypatch, 0.0)
    monkeypatch.setattr(task_runs, "_git_output", run)
    assert scan_all(repo, since, clean) == {"unread": None, "findings": [], "passed": []}     # the control
    ran.clear()

    def late_after(args, *a, **k):
        done = run(args, *a, **k)
        if "cat-file" in args:
            now[0] = 10.0 ** 9                      # the time runs out while the git command runs
        return done
    monkeypatch.setattr(task_runs, "_git_output", late_after)
    real_add, read = task_runs._Findings.add, []

    def add(self, kind, *a, **k):
        read.append(kind)
        return real_add(self, kind, *a, **k)
    monkeypatch.setattr(task_runs._Findings, "add", add)
    assert scan_all(repo, since, clean)["unread"] == {"shape": task_runs.UNREAD, "file": None}
    assert ran[-1] == "cat-file" and "commit" not in read      # it read no line of the objects once out of time
    # out of time while it reads the last line of the last commit object, which holds no shape: still unread
    monkeypatch.setattr(task_runs, "_git_output", run)
    # the object's lines as the scan reads them, the empty one after its last "\n" among them (git() strips its output)
    raw = subprocess.run(["git", "cat-file", "commit", clean], cwd=repo, capture_output=True, check=True).stdout
    now[0], lines = 0.0, len(raw.split(b"\n"))

    def late_last(self, kind, *a, **k):
        read.append(kind)
        done = real_add(self, kind, *a, **k)
        if read.count("commit") == lines:
            now[0] = 10.0 ** 9
        return done
    read.clear()
    monkeypatch.setattr(task_runs._Findings, "add", late_last)
    assert scan_all(repo, since, clean)["unread"] == {"shape": task_runs.UNREAD, "file": None}
    assert read.count("commit") == lines


def test_a_line_s_number_is_its_own_whatever_git_s_settings_say_of_a_hunk_s_lines_or_a_rename(tmp_path):
    # diff.interHunkContext fuses two hunks into one with the lines
    # between shown as kept ones, so a scan that did not count them would say a token on line 5 is on line 3; and
    # diff.renames shows a renamed file's lines only where they changed
    repo = str(tmp_path / "repo")
    make_repo(repo)
    lines = [f"line {n}" for n in range(1, 8)]
    first = commit_in(repo, "settings.py", "\n".join(lines) + "\n")
    lines[1], lines[4] = "A = " + token("a"), "B = " + token("b")
    two = commit_in(repo, "settings.py", "\n".join(lines) + "\n")
    for key, value in (("diff.interHunkContext", "3"), ("diff.context", "5")):
        git("config", key, value, cwd=repo)
    shown = git("log", "-1", "-p", "--unified=0", "--format=", two, cwd=repo)
    assert len([h for h in shown.splitlines() if h.startswith("@@")]) == 1         # the control: one fused hunk
    assert [(f["line"], f["place"]) for f in scan_all(repo, first, two)["findings"]] == [(2, 1), (5, 1)]
    # a renamed file's lines are read as a new file's, a token among them, under a lift of its own (its path is new)
    git("config", "diff.renames", "copies", cwd=repo)
    git("mv", "settings.py", "moved.py", cwd=repo)
    git("commit", "-qm", "rename", cwd=repo)
    moved = git("rev-parse", "HEAD", cwd=repo)
    assert "+A = " not in git("log", "-1", "-p", "-M", "--format=", moved, cwd=repo)   # the control: git shows none
    assert [(f["kind"], f["line"]) for f in scan_all(repo, two, moved)["findings"]] == [("line", 2), ("line", 5)]


def test_a_value_glued_to_one_the_scan_found_is_a_finding_of_its_own_and_its_lift_lifts_no_other(tmp_path):
    # a shape's \b passes over a value glued to another, so read by the pattern alone a made-up key
    # with a real token after it would be one finding, and its lift would let the real token out
    repo = str(tmp_path / "repo")
    at = make_repo(repo)
    for line in ("FAKE=sk-" + "f" * 16 + "ghp_" + "r" * 36,
                 "x=ghp_" + "f" * 36 + "sk-ant-api03-" + "r" * 20,
                 "-----BEGIN RSA PRIVATE KEYxghp_" + "r" * 36,
                 "FlyV1 fm2_abcxghp_" + "r" * 36):
        # the control: the shapes' patterns find one place, the made-up value's; the real one has a letter before it
        assert len([at for at in range(len(line)) if any(p.match(line, at) for _, p in task_runs.SECRET_SHAPES)]) == 1
        made = commit_in(repo, "values.txt", line + "\n")
        found = scan_all(repo, at, made)["findings"]
        assert len(found) == 2, line
        got = scan_all(repo, at, made, [found[0]["finding"]])       # the made-up one lifted: the real one holds
        assert got["passed"] == [found[0]["finding"]] and [f["finding"] for f in got["findings"]] == [
            found[1]["finding"]]
        at = made
    # a value glued to what is no value begins no place, as its pattern has it
    made = commit_in(repo, "values.txt", "plain xghp_" + "r" * 36 + "\n")
    assert scan_all(repo, at, made)["findings"] == []


def test_the_place_order_and_name_of_each_shape_on_a_line(monkeypatch):
    # places taken shape by shape rather than in the order they stand
    # give a later shape's first, and one place twice under two names
    line = "a=" + token("x") + " k1=sk-ant-api03-" + "k" * 20 + " k2=sk-ant-api03-" + "j" * 20
    assert list(task_runs.secret_matches(line)) == [
        (3, GITHUB_TOKEN_SHAPE), (line.index("sk-ant") + 1, "an Anthropic key (sk-ant-)"),
        (line.rindex("sk-ant") + 1, "an Anthropic key (sk-ant-)")]
    # a line read in place: its columns count from its start, and it is read no further than its end
    text = "zz\n+" + line + "\nghp_" + "n" * 36
    begin = text.index("+") + 1
    assert list(task_runs.secret_matches(text, begin, begin + len(line))) == list(task_runs.secret_matches(line))
    # and a long text after the line costs its reading nothing
    text = line + "\n" + "z" * 20000000
    began = time.monotonic()
    for _ in range(20):
        assert len(list(task_runs.secret_matches(text, 0, len(line)))) == 3
    assert time.monotonic() - began < 2


def test_a_line_s_digest_is_made_in_parts_and_is_its_bytes(monkeypatch):
    # a digest that encoded the whole line at once would make a copy as long as it
    monkeypatch.setattr(task_runs, "_DIGEST_PART", 3)
    text = ("ab" + chr(0x1F600) + "c" + chr(0xDCFF) + "de" + chr(0xE9) + "fg") * 3 + "tail"
    for start, end in ((0, len(text)), (2, len(text) - 4), (5, 6), (4, 4)):
        assert task_runs.line_digest(text, start, end) == hashlib.sha256(
            text[start:end].encode("utf-8", "surrogateescape")).hexdigest(), (start, end)


def test_a_path_git_quotes_is_read_back_to_its_bytes_and_said_with_none_of_a_value(tmp_path):
    # a line's file read as the path the patch's header quotes,
    # where "\\t" or an octal escape before a token leaves no word boundary, would name the value; and a byte that is
    # not UTF-8 would reach the answer as a lone surrogate, which no JSON encoder writes as UTF-8
    repo = str(tmp_path / "repo")
    since = make_repo(repo)

    def adds(name, data):
        full = os.path.join(repo.encode(), name)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(data)
        before = git("rev-parse", "HEAD", cwd=repo)
        git("add", "-A", cwd=repo)
        git("commit", "-qm", "work", cwd=repo)
        return before, git("rev-parse", "HEAD", cwd=repo)

    line = ("key = " + token("v") + "\n").encode()
    # a path git quotes for its '"', read back to its bytes as diff-tree gives them, its place among them
    start, made = adds(b'say "hi".txt', line)
    [f] = scan_all(repo, start, made)["findings"]
    assert (f["file"], f["place"]) == ('say "hi".txt', _entry_places(repo, made)['say "hi".txt'])
    for name in (b"notes\t" + token("a").encode() + b".txt", "caf\u00e9".encode() + token("e").encode() + b".txt"):
        start, made = adds(name, line)
        header = [h for h in git("log", "-1", "-p", "--format=", made, cwd=repo).splitlines() if h.startswith("+++ ")]
        assert header[0].startswith('+++ "b/') and task_runs.secret_shape(header[0]) is None    # the control: quoted
        got = scan_all(repo, start, made)
        assert [(f["kind"], f["file"]) for f in got["findings"]] == [("line", "\u2026"), ("name", "\u2026")]
        assert "a" * 36 not in json.dumps(got) and "e" * 36 not in json.dumps(got)
    # git's letter escapes for BEL, BS, VT, FF and CR, read back as a tab's and a newline's are (a reading that left
    # them as two characters would name the value that followed)
    escapes = {"a": b"\a", "b": b"\b", "v": b"\v", "f": b"\f", "r": b"\r"}
    for c in "abvfr":
        name = b"notes" + escapes[c] + token("z").encode() + b".txt"
        start, made = adds(name, line)
        header = [h for h in git("log", "-1", "-p", "--format=", made, cwd=repo).splitlines() if h.startswith("+++ ")]
        assert header[0].startswith('+++ "b/notes\\' + c)                    # the control: git's letter escape
        got = scan_all(repo, start, made)
        assert [(f["kind"], f["file"]) for f in got["findings"]] == [("line", "\u2026"), ("name", "\u2026")], c
        assert "z" * 36 not in json.dumps(got), c
    # a newline between "-----BEGIN" and "PRIVATE KEY" leaves the path no shape as git keeps it, and one once the
    # newline is written as \x0a: such a path is said as "\u2026" whole
    odd = "-----BEGIN\nPRIVATE KEY.txt"
    assert task_runs.masked(odd) == odd and task_runs.secret_shape(odd) is None            # the control
    start, made = adds(odd.encode(), line)
    assert [(f["kind"], f["file"]) for f in scan_all(repo, start, made)["findings"]] == [("line", "\u2026")]
    # a path that holds no shape keeps its tab and its byte that is not UTF-8, each written as \xNN: the tab first on a
    # path of its own, which APFS takes (measured on a Mac)
    start, made = adds(b"tabs/odd\tname.txt", line)
    got = scan_all(repo, start, made)
    assert [f["file"] for f in got["findings"]] == ["tabs/odd\\x09name.txt"]
    assert json.loads(json.dumps(got, ensure_ascii=False).encode("utf-8")) == got
    try:
        start, made = adds(b"dir\xff/odd\tname.txt", line)
    except OSError as e:
        if e.errno != errno.EILSEQ:
            raise
        pytest.skip(f"{NOT_UTF8_REFUSED}: the paths holding such a byte, this test's last part, are not read here; "
                    "every check before them was, the tab's on a path of its own among them")
    got = scan_all(repo, start, made)
    assert [f["file"] for f in got["findings"]] == ["dir\\xff/odd\\x09name.txt"]
    assert json.loads(json.dumps(got, ensure_ascii=False).encode("utf-8")) == got        # UTF-8 carries it
    start, made = adds(b"dir\xfe/" + token("n").encode() + b".txt", b"clean\n")
    assert [(f["kind"], f["file"]) for f in scan_all(repo, start, made)["findings"]] == [("name", "dir\\xfe/\u2026")]


def test_a_path_with_a_space_is_read_as_it_is_whatever_diff_noprefix_says(tmp_path):
    # git ends a patch's "+++" header with a tab where its path holds a space, so such a path would be read with that
    # tab, and a quoted one, not read back at all, would have its value named; and under diff.noprefix the header has no
    # "b/", so a path under a directory b/ would be read without its own b/
    repo = str(tmp_path / "repo")
    since = make_repo(repo)
    line = "key = " + token("v") + "\n"
    plain = commit_in(repo, "my notes.txt", line)
    header = [h for h in git("log", "-1", "-p", "--format=", plain, cwd=repo).splitlines() if h.startswith("+++ ")]
    assert header == ["+++ b/my notes.txt\t"]                                       # the control: git's tab
    assert [f["file"] for f in scan_all(repo, since, plain)["findings"]] == ["my notes.txt"]
    quoted = commit_in(repo, "my notes\t" + token("q") + ".txt", line)
    got = scan_all(repo, plain, quoted)
    assert [(f["kind"], f["file"]) for f in got["findings"]] == [("line", chr(0x2026)), ("name", chr(0x2026))]
    assert "q" * 36 not in json.dumps(got)
    git("config", "diff.noprefix", "true", cwd=repo)
    os.makedirs(os.path.join(repo, "b"))
    under = commit_in(repo, os.path.join("b", "inner.txt"), line)
    header = [h for h in git("log", "-1", "-p", "--format=", under, cwd=repo).splitlines() if h.startswith("+++ ")]
    assert header == ["+++ b/inner.txt"]                                            # the control: no prefix
    assert [f["file"] for f in scan_all(repo, quoted, under)["findings"]] == ["b/inner.txt"]


def test_the_scan_verifies_no_signature_whatever_log_show_signature_says(tmp_path):
    # git log's verification of a commit's signature runs gpg.program,
    # and log.showSignature turns it on for every git log
    repo = str(tmp_path / "repo")
    since = make_repo(repo)
    ran, program = tmp_path / "ran", tmp_path / "gpg.sh"
    program.write_text(f"#!/bin/sh\necho ran >> {ran}\nexit 1\n")
    program.chmod(0o755)
    tree = git("rev-parse", f"{since}^{{tree}}", cwd=repo)
    raw = (f"tree {tree}\nparent {since}\nauthor A <a@example.com> 1700000000 +0000\n"
           f"committer A <a@example.com> 1700000000 +0000\ngpgsig -----BEGIN PGP SIGNATURE-----\n \n"
           f" iQEzBAABCAAdFiEE\n -----END PGP SIGNATURE-----\n\nsigned\n")
    signed = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--stdin"], cwd=repo, input=raw.encode(),
                            capture_output=True, check=True).stdout.decode().strip()
    git("config", "log.showSignature", "true", cwd=repo)
    git("config", "gpg.program", str(program), cwd=repo)
    subprocess.run(["git", "log", "-1", "--format=%H", signed], cwd=repo, capture_output=True)
    assert ran.exists()                                     # the control: a git log runs it
    ran.unlink()
    assert scan_all(repo, since, signed) == {"unread": None, "findings": [], "passed": []}
    assert not ran.exists()


# a control's git reads the repository's settings and none of the test machine's own: not its user's or its system's
# files (the workspace has a home of its own, with none), nor settings given in the test process's environment
REPOSITORY_ALONE = dict({k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG")},
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")


def failing_program(w, name):
    """A program that git might run, which says so in the file it returns and fails: (the file, the program)."""
    ran, program = w.tmp / f"ran-{name}", w.tmp / f"{name}.sh"
    program.write_text(f"#!/bin/sh\necho ran >> {ran}\ncat > /dev/null\nexit 1\n")
    program.chmod(0o755)
    return ran, program


@pytest.mark.parametrize("value", ["true", "if-asked"])
@pytest.mark.parametrize("form", ["openpgp", "ssh"])
def test_a_hand_off_s_push_signs_nothing_whatever_push_gpg_sign_says(tws, form, value):
    # with commit.gpgSign alone pinned in RUN_GIT, a push the repository's settings set to sign, to a remote that takes
    # signed pushes, would run gpg.program, and one whose signatures are SSH's gpg.ssh.defaultKeyCommand for its key;
    # push.gpgSign's two values that sign there, each
    ran, program = failing_program(tws, "signer")
    settings = [("push.gpgSign", value), ("user.name", "Ana"), ("user.email", "ana@example.org")]
    settings += [("gpg.program", str(program))] if form == "openpgp" else \
        [("gpg.format", "ssh"), ("gpg.ssh.defaultKeyCommand", str(program))]
    for key, value in settings:
        tws.demo("config", key, value)
    git("config", "receive.certNonceSeed", "a seed", cwd=tws.remote)          # the remote takes signed pushes
    put_agent(tws)
    commit, base = offered(tws)
    assert accept(tws, commit, base).status_code == 200
    work = task_work(tws)
    done = commit_in(work, "more.txt", "more work\n")
    r = _push(tws, done, commit, done)
    assert r.status_code == 200 and r.json()["pushed"] is True, r.text
    assert not ran.exists()
    # the control: the same push, as git runs it with the repository's settings alone, runs it and fails
    control = subprocess.run(["git", "push", "--no-verify", "--", tws.remote, f"{done}:refs/heads/control"],
                             cwd=work, capture_output=True, env=REPOSITORY_ALONE)
    assert ran.exists() and control.returncode != 0, control.stderr


SIGNATURE_FORMS = {"openpgp": ("gpg.program", "PGP SIGNATURE"), "x509": ("gpg.x509.program", "SIGNED MESSAGE"),
                   "ssh": ("gpg.ssh.program", "SSH SIGNATURE")}


@pytest.mark.parametrize("form", sorted(SIGNATURE_FORMS))
def test_a_return_reads_its_steps_with_no_signature_checked_whatever_log_show_signature_says(tws, form):
    # a log the repository's settings
    # set to check signatures (log.showSignature) runs the program a commit's signature's form names, whatever the
    # log's format, and a return reads the steps its commits completed with one (_steps_done); an SSH signature is
    # checked only where an allowed signers file is named
    key, armor = SIGNATURE_FORMS[form]
    ran, program = failing_program(tws, "verifier")
    allowed = tws.tmp / "allowed_signers"
    allowed.write_text("")
    for name, value in (("log.showSignature", "true"), (key, str(program)),
                        ("gpg.ssh.allowedSignersFile", str(allowed))):
        tws.demo("config", name, value)
    put_agent(tws)
    handed, base = offered(tws)
    assert accept(tws, handed, base).status_code == 200
    clone = str(tws.tmp / "owner-clone")
    tree = git("rev-parse", f"{handed}^{{tree}}", cwd=clone)
    raw = (f"tree {tree}\nparent {handed}\nauthor A <a@example.com> 1700000000 +0000\n"
           f"committer A <a@example.com> 1700000000 +0000\ngpgsig -----BEGIN {armor}-----\n \n"
           f" iQEzBAABCAAdFiEE\n -----END {armor}-----\n\nTask {TASK}: step 1, checkpoint 1 (step)\n")
    returned = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--stdin"], cwd=clone, input=raw.encode(),
                              capture_output=True, check=True).stdout.decode().strip()
    git("push", "-q", tws.remote, f"{returned}:refs/heads/pp/task-{'ef' * 16}", cwd=clone)
    r = tws.http.post(f"/agents/tasks/{TASK}/fetch", json={"address": tws.remote, "commit": returned,
                                                           "purpose": "return", "base": handed, "protected": []})
    assert r.status_code == 200 and r.json()["steps"] == [1], r.text
    assert not ran.exists()
    subprocess.run(["git", "log", "--format=%s", f"{handed}..{returned}"], cwd=task_work(tws), capture_output=True,
                   env=REPOSITORY_ALONE)
    assert ran.exists()                                     # the control: a git log there runs it


def test_a_header_s_continuation_lines_are_read_as_any_line_of_the_commit(tmp_path):
    # a merged signed tag's message is carried in the commit's mergetag
    # header, each of its lines after the first beginning with a space
    repo = str(tmp_path / "repo")
    since = make_repo(repo)
    tree = git("rev-parse", f"{since}^{{tree}}", cwd=repo)
    raw = (f"tree {tree}\nparent {since}\nauthor A <a@example.com> 1700000000 +0000\n"
           f"committer A <a@example.com> 1700000000 +0000\nmergetag object {since}\n type commit\n tag v1\n"
           f" tagger A <a@example.com> 1700000000 +0000\n \n release notes: rotate {token('g')}\n\nmerge v1\n")
    crafted = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--stdin", "--literally"], cwd=repo,
                             input=raw.encode(), capture_output=True, check=True).stdout.decode().strip()
    [f] = scan_all(repo, since, crafted)["findings"]
    assert (f["kind"], f["commit"], f["column"]) == ("commit", crafted, len(" release notes: rotate ") + 1)


def test_a_merge_s_own_new_path_and_an_unrelated_root_s_are_read(tmp_path):
    # diff-tree's -m reads a merge against each parent, so a path the
    # merge itself adds, in neither, is read; and --root reads a root commit's paths, one an unrelated history brings
    repo = str(tmp_path / "repo")
    since = make_repo(repo)
    home = git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo)
    git("checkout", "-qb", "side", cwd=repo)
    commit_in(repo, "side.txt", "side\n")
    git("checkout", "-q", home, cwd=repo)
    main = commit_in(repo, "main.txt", "main\n")
    git("merge", "-q", "--no-commit", "--no-ff", "side", cwd=repo)
    with open(os.path.join(repo, token("m") + ".txt"), "w") as fh:
        fh.write("clean\n")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "merge side", cwd=repo)
    merged = git("rev-parse", "HEAD", cwd=repo)
    [f] = scan_all(repo, main, merged)["findings"]
    assert (f["kind"], f["commit"]) == ("name", merged)
    # an unrelated history whose root adds a shaped path that its next commit deletes, merged in
    git("checkout", "-q", "--orphan", "other", cwd=repo)
    git("rm", "-rqf", ".", cwd=repo)
    root = commit_in(repo, token("o") + ".txt", "clean\n")
    os.remove(os.path.join(repo, token("o") + ".txt"))
    commit_in(repo, "kept.txt", "kept\n")
    git("checkout", "-q", home, cwd=repo)
    git("merge", "-q", "--allow-unrelated-histories", "-m", "join", "other", cwd=repo)
    joined = git("rev-parse", "HEAD", cwd=repo)
    # the merge against the unrelated side adds the main side's paths, the one lifted above among them
    got = scan_all(repo, merged, joined, [f["finding"]])
    assert [(g["kind"], g["commit"]) for g in got["findings"]] == [("name", root)] and got["passed"] == [f["finding"]]
    assert since


def test_a_failing_git_holds_the_push_whatever_it_wrote(tmp_path, monkeypatch):
    # a failing diff-tree or cat-file is caught by its failure, not by an empty output alone: one that fails after
    # writing what it read holds the push all the same
    repo = str(tmp_path / "repo")
    since = make_repo(repo)
    clean = commit_in(repo, "clean.txt", "nothing to see\n")
    real = task_runs._git_output
    for verb in ("diff-tree", "cat-file"):
        def failing(args, _verb=verb, **kw):
            done = real(args, **kw)
            if list(args[:3]) == ["git", "--no-replace-objects", _verb]:
                return subprocess.CompletedProcess(args, 128, done.stdout, done.stderr)
            return done
        monkeypatch.setattr(task_runs, "_git_output", failing)
        assert scan_all(repo, since, clean)["unread"] == {"shape": "a change the scan could not read", "file": None}
    monkeypatch.setattr(task_runs, "_git_output", real)
    assert scan_all(repo, since, clean) == {"unread": None, "findings": [], "passed": []}       # the control


def test_a_submodule_whose_path_is_a_token_is_read_whatever_says_to_leave_submodules_out(tmp_path):
    # under ignore = all, in the .gitmodules the worktree holds or in the
    # repository's config, git leaves out of its list of a commit's paths a submodule whose path is a token, so read by
    # that list the push would go out with no finding, and each path listed after it take a place one less than
    # without the setting
    head = make_repo(str(tmp_path / "sub"))
    path = token("s")
    for case in ("gitmodules", "config"):
        repo = str(tmp_path / case)
        since = make_repo(repo)
        # the path written across a line continuation, so no line the commit adds holds the token whole
        ignore = "\tignore = all\n" if case == "gitmodules" else ""
        with open(os.path.join(repo, ".gitmodules"), "w") as fh:
            fh.write('[submodule "s"]\n\tpath = gh\\\n' + path[2:] + "\n\turl = ./x\n" + ignore)
        if case == "config":
            git("config", "submodule.s.ignore", "all", cwd=repo)
        git("update-index", "--add", "--cacheinfo", f"160000,{head},{path}", cwd=repo)
        git("add", ".gitmodules", cwd=repo)
        git("commit", "-qm", "a submodule", cwd=repo)
        made = git("rev-parse", "HEAD", cwd=repo)
        # the control: git's own list of the commit's paths leaves the submodule out under the setting
        listed = git("diff-tree", "-r", "--root", "--no-renames", "--name-only", made, cwd=repo).splitlines()
        assert path not in listed and ".gitmodules" in listed, case
        got = scan_all(repo, since, made)
        assert [(f["kind"], f["place"]) for f in got["findings"]] == [("name", _entry_places(repo, made)[path])], case
        assert "s" * 36 not in json.dumps(got), case
    # a path git lists after a submodule keeps its place under the setting
    repo = str(tmp_path / "places")
    since = make_repo(repo)
    with open(os.path.join(repo, ".gitmodules"), "w") as fh:
        fh.write('[submodule "sub"]\n\tpath = sub\n\turl = ./x\n\tignore = all\n')
    named = "z-" + token("z") + ".txt"
    with open(os.path.join(repo, named), "w") as fh:
        fh.write("clean\n")
    git("update-index", "--add", "--cacheinfo", f"160000,{head},sub", cwd=repo)
    git("add", ".gitmodules", named, cwd=repo)
    git("commit", "-qm", "three paths", cwd=repo)
    made = git("rev-parse", "HEAD", cwd=repo)
    listed = git("diff-tree", "-r", "--root", "--no-renames", "--name-only", made, cwd=repo).splitlines()
    assert listed[1:] == [".gitmodules", named]                       # the control: git's list skips the submodule
    [f] = scan_all(repo, since, made)["findings"]
    assert f["kind"] == "name" and f["place"] == _entry_places(repo, made)[named] == 3


def test_a_path_is_said_once_for_its_findings_and_under_the_scan_s_time(tmp_path, monkeypatch):
    # a finding that said its path again, with no look at the scan's
    # time, would make a path of 16 MiB take 14 to 17 s for each finding in it, past SCAN_SECONDS
    repo = str(tmp_path / "repo")
    since = make_repo(repo)
    named = token("n") + ".txt"
    made = commit_in(repo, named, "".join(f"KEY{n} = {token(chr(97 + n))}\n" for n in range(5)))
    real_shown, said = task_runs.shown, []

    def counted(path, deadline=None):
        said.append(path)
        return real_shown(path, deadline)
    monkeypatch.setattr(task_runs, "shown", counted)
    got = scan_all(repo, since, made)
    assert [f["kind"] for f in got["findings"]] == ["line"] * 5 + ["name"]
    assert said == [named]
    monkeypatch.undo()
    # a path of many parts, each a token: the scan's time runs out while it says the path, which it then reads no
    # further, and the push is held as unread
    long = "/".join(token(chr(97 + n % 26)) for n in range(300))
    blob = git("hash-object", "-w", "README.md", cwd=repo)
    subprocess.run(["git", "update-index", "--add", "--index-info"], cwd=repo, check=True,
                   input=f"100644 {blob}\t{long}\n".encode())
    git("commit", "-qm", "a long path", cwd=repo)
    deep = git("rev-parse", "HEAD", cwd=repo)
    now = _clock(monkeypatch, 0.0)
    real_places, calls = task_runs._places, []

    def places(text, start, end, deadline):
        calls.append([])
        for got in real_places(text, start, end, deadline):
            calls[-1].append(got)
            if text == long and len(calls[-1]) == 3:
                now[0] = 10.0 ** 9                  # the time runs out at the third place of a reading of the path
            yield got
    monkeypatch.setattr(task_runs, "_places", places)
    assert scan_all(repo, made, deep)["unread"] == {"shape": task_runs.UNREAD, "file": None}
    assert max(len(c) for c in calls) == 3                  # no reading went on past the place the time ran out at
    monkeypatch.undo()
    assert len(list(task_runs.secret_matches(long))) == 300                  # the control: 300 places in the path
    # a path is said cut at SHOWN_MOST characters once its values are hidden, the last of them an ellipsis, and what
    # the cut leaves is read again: here the cut makes "PP-" and eight letters a shape, which the path as git gave it
    # does not hold, so the whole is hidden
    deep = "/".join(["dir"] * 2000) + "/" + token("c") + ".txt"
    assert task_runs.shown(deep) == ("/".join(["dir"] * 2000))[:task_runs.SHOWN_MOST - 1] + chr(0x2026)
    cut = "a" * 1011 + "/PP-ABCDEFGHIJ"
    assert len(cut) > task_runs.SHOWN_MOST and task_runs.masked(cut) == cut          # the control: no shape in it
    assert task_runs.shown(cut) == chr(0x2026)


def test_saying_a_path_and_reading_its_quoting_look_at_the_scan_s_time(tmp_path, monkeypatch):
    # a path's hidden parts are not found from a list of every part (74
    # bytes of memory for each character of a path of many parts), git's quoting of a path is not read back by a loop
    # over its characters, and a reading of a line looks at the clock between the searches of every shape's reading
    path = token("q") + "/" + "/".join(["a"] * 100000)
    import tracemalloc
    tracemalloc.start()
    said = task_runs.masked(path)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert said == chr(0x2026) + ("/" + chr(0x2026)) * 100000 and peak < 16 * len(path)
    quoted = '"' + "\\001" * 5000 + '"'
    assert task_runs._unquoted(quoted) == "\x01" * 5000                             # the control
    _clock(monkeypatch, 1.0)
    with pytest.raises(task_runs._OutOfTime):
        task_runs._unquoted(quoted, deadline=3.0)
    # the first look at a text tries each place a shape without its \b begins, and looks at the clock as it goes
    _clock(monkeypatch, 1.0)
    with pytest.raises(task_runs._OutOfTime):
        task_runs._holds_place("xghp_" * 5000, 0, 25000, 3.0)
    # a part hidden between "-----BEGIN" and "PRIVATE KEY" takes its line break with it: what is said is looked at
    # again, and hidden whole
    made = "-----BEGIN/\n" + token("h") + " /PRIVATE KEY"
    assert task_runs.masked(made) == "-----BEGIN/" + chr(0x2026) + "/PRIVATE KEY"
    assert task_runs.secret_shape(made) == GITHUB_TOKEN_SHAPE                  # the control: none such before
    assert task_runs.secret_shape(task_runs.masked(made)) == "a private key (-----BEGIN ... PRIVATE KEY)"
    assert task_runs.shown(made) == chr(0x2026)
    # a private key's body follows its header's line, and is hidden to the path's end (its base64 holds "/"s, and the
    # parts after the line break would be said)
    hidden = chr(0x2026)
    assert task_runs.shown("-----BEGIN RSA/PRIVATE KEY-----\nMIIEpAIBAAKCAQEA1x/abc+def/ghiQ==") == "/".join([hidden] * 4)
    assert task_runs.shown("keys/-----BEGIN EC/PRIVATE KEY-----\nMHcCAQEEIBk3/9eRmZ+Qz/Lk7.pem") == "keys/" + "/".join(
        [hidden] * 4)
    # a body of more lines than one: a value run on only to the white space after its first line said the rest
    assert task_runs.shown("k/-----BEGIN RSA PRIVATE KEY-----\nMIIE/abc\ndef/ghi==\n-----END RSA PRIVATE KEY-----") \
        == "k/" + "/".join([hidden] * 3)
    # values with white space and a part with none between them, and one that ends at a "/" white space follows
    assert task_runs.masked(token("r") + " /clean/" + token("s")) == hidden + "/clean/" + hidden
    assert task_runs.masked(token("r") + "/ notes/x.txt") == hidden + "/ notes/x.txt"
    # a "-----BEGIN" with no "PRIVATE KEY" after it, and an "eyJ" with no token, are no shape: said as they are
    for plain in ("docs/-----BEGIN-notes.txt", "eyJ-notes/plan.txt"):
        assert task_runs.shown(plain) == plain
    # a path is said SHOWN_MOST characters at most once its bytes are written out, each \xNN four of them
    for odd in ("d/" + "\x01" * 2000, "d/" + chr(0xDCFF) * 2000):
        said = task_runs.shown(odd)
        assert len(said) == task_runs.SHOWN_MOST and said.endswith(hidden) and said.startswith("d/\\x")
    # and it is cut before its bytes are written out, so a long path is never written out whole: a cut after it alone
    # says the same, but writes a mebibyte of control characters out as four
    odd = "d/" + "\x01" * (1 << 20)
    tracemalloc.start()
    said = task_runs.shown(odd)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert len(said) == task_runs.SHOWN_MOST and peak < (1 << 20), peak
    # the clock is looked at before each search a shape's reading makes: once the time is out, none is made
    now = _clock(monkeypatch, 0.0)
    real_first, searched = task_runs._Pattern.first, []

    def first(self, pos):
        searched.append(pos)
        now[0] = 10.0 ** 9                          # the time runs out in the first search
        return real_first(self, pos)
    monkeypatch.setattr(task_runs._Pattern, "first", first)
    with pytest.raises(task_runs._OutOfTime):
        list(task_runs.secret_matches("x sk-" + "a" * 16 + " " + token("t"), deadline=10.0))
    assert len(searched) == 1
    # and before each search for a value glued to one the scan found
    now[0], glued = 0.0, []

    def first_glued(self, pos):
        if not self.pattern.pattern.startswith("\\b"):
            glued.append(pos)
            now[0] = 10.0 ** 9                      # the time runs out in the first search for a glued value
        return real_first(self, pos)
    monkeypatch.setattr(task_runs._Pattern, "first", first_glued)
    with pytest.raises(task_runs._OutOfTime):
        list(task_runs.secret_matches("x sk-" + "a" * 16 + "x" + token("t"), deadline=10.0))
    assert len(glued) == 1
    monkeypatch.undo()
    # the scan reads a path git quotes under its own time, and a path it could not say in time holds the push as unread
    repo = str(tmp_path / "repo")
    since = make_repo(repo)
    quoted_path = commit_in(repo, "notes\t" + token("u") + ".txt", "clean\n")
    real_unquoted, given = task_runs._unquoted, []

    def unquoted(text, deadline=None):
        given.append(deadline)
        return real_unquoted(text, deadline)
    monkeypatch.setattr(task_runs, "_unquoted", unquoted)
    assert len(scan_all(repo, since, quoted_path)["findings"]) == 1
    assert given and all(isinstance(d, float) for d in given)
    monkeypatch.undo()
    real_run = task_runs._git_output

    def binary_log(args, **kw):
        if list(args[:3]) == ["git", "--no-replace-objects", "log"]:
            out = b"\x00" + quoted_path.encode() + b"\ndiff --git a/x.bin b/x.bin\nBinary files a/x.bin and b/x.bin differ\n"
            return subprocess.CompletedProcess(args, 0, out, b"")
        return real_run(args, **kw)
    monkeypatch.setattr(task_runs, "_git_output", binary_log)
    assert scan_all(repo, since, quoted_path)["unread"] == {"shape": task_runs.BINARY,
                                                            "file": "a/x.bin and b/x.bin differ"}      # the control

    def no_time(path, deadline=None):
        raise task_runs._OutOfTime
    monkeypatch.setattr(task_runs, "shown", no_time)
    assert scan_all(repo, since, quoted_path)["unread"] == {"shape": task_runs.UNREAD, "file": None}


def test_a_scan_that_has_all_it_reports_as_its_time_runs_out_holds_the_push(tmp_path, monkeypatch):
    # the scan answers as unread, not with its findings, when it has
    # all it reports, the twentieth found in the reading that ran past its time, in a commit's message or among the
    # paths a commit adds
    repo = str(tmp_path / "repo")
    since = make_repo(repo)
    commit_in(repo, "a.txt", "plain\n")
    git("commit", "--amend", "-qm", "\n".join("t " + token(chr(97 + n)) for n in range(25)), cwd=repo)
    message = git("rev-parse", "HEAD", cwd=repo)
    paths = repo + "-paths"
    paths_since = make_repo(paths)
    for n in range(20):
        with open(os.path.join(paths, token(chr(97 + n)) + ".txt"), "w") as fh:
            fh.write("clean\n")
    git("add", "-A", cwd=paths)
    git("commit", "-qm", "twenty", cwd=paths)
    names = git("rev-parse", "HEAD", cwd=paths)
    for where, start, end in ((repo, since, message), (paths, paths_since, names)):
        assert len(scan_all(where, start, end)["findings"]) == 20               # the control: all it reports
        now = _clock(monkeypatch, 0.0)
        real_first, met = task_runs._Pattern.first, []

        def first(self, pos):
            got = real_first(self, pos)
            if got is not None and self.pattern.pattern.startswith(r"\bgh"):
                met.append(got)
                if len(met) == 20:
                    now[0] = 10.0 ** 9           # the time runs out in the search that finds the twentieth
            return got
        monkeypatch.setattr(task_runs._Pattern, "first", first)
        assert scan_all(where, start, end) == {"unread": {"shape": task_runs.UNREAD, "file": None}, "findings": [],
                                               "passed": []}
        monkeypatch.undo()


def test_the_scan_reads_no_further_line_or_path_once_its_time_is_out(tmp_path, monkeypatch):
    # with no look at the clock for each line git log gives, or for each
    # of diff-tree's entries, a scan past its time would go on reading every line and path of a change before it stopped
    repo = str(tmp_path / "repo")
    since = make_repo(repo)
    lines = commit_in(repo, "many.txt", "".join(f"plain line {n}\n" for n in range(1000)))
    files = repo + "-files"
    files_since = make_repo(files)
    for n in range(300):
        with open(os.path.join(files, f"plain-{n}.txt"), "w") as fh:
            fh.write("x\n")
    git("add", "-A", cwd=files)
    git("commit", "-qm", "many paths", cwd=files)
    many = git("rev-parse", "HEAD", cwd=files)
    # and each line of a commit object (with a look once a commit, a scan read on through 2,000,000)
    told = repo + "-message"
    told_since = make_repo(told)
    commit_in(told, "a.txt", "x\n", message="\n".join(f"line {n} of the message" for n in range(1000)))
    long_message = git("rev-parse", "HEAD", cwd=told)
    real_add = task_runs._Findings.add
    for where, start, end, kind, total in ((repo, since, lines, "line", 1000), (files, files_since, many, "name", 300),
                                           (told, told_since, long_message, "commit", 1000)):
        now = _clock(monkeypatch, 0.0)
        read = []

        def add(self, what, *a, **k):
            read.append(what)
            done = real_add(self, what, *a, **k)
            if read.count(kind) == 10:
                now[0] = 10.0 ** 9                  # the time runs out as the tenth is read
            return done
        monkeypatch.setattr(task_runs._Findings, "add", add)
        assert scan_all(where, start, end)["unread"] == {"shape": task_runs.UNREAD, "file": None}
        assert read.count(kind) == 10, kind
        monkeypatch.undo()
        assert scan_all(where, start, end)["unread"] is None                     # the control: a clean change
    # a commit object with no "\n" at its end, its last line the one read as the time runs out: still unread
    tree = git("rev-parse", f"{since}^{{tree}}", cwd=repo)
    raw = (f"tree {tree}\nparent {since}\nauthor A <a@b> 1700000000 +0000\ncommitter A <a@b> 1700000000 +0000\n\n"
           "no final newline").encode()
    bare = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--stdin"], cwd=repo, input=raw,
                          capture_output=True, check=True).stdout.decode().strip()
    assert scan_all(repo, since, bare)["unread"] is None and not raw.endswith(b"\n")    # the control
    now = _clock(monkeypatch, 0.0)
    read = []

    def late_last(self, what, *a, **k):
        read.append(what)
        done = real_add(self, what, *a, **k)
        if read.count("commit") == len(raw.split(b"\n")):
            now[0] = 10.0 ** 9
        return done
    monkeypatch.setattr(task_runs._Findings, "add", late_last)
    assert scan_all(repo, since, bare)["unread"] == {"shape": task_runs.UNREAD, "file": None}
    assert read.count("commit") == len(raw.split(b"\n"))


def test_a_value_ends_at_ascii_white_space_alone(tmp_path):
    # the region a found value covers ends at the first ASCII white space
    # after its match; a reading of every white space Python knows would end it at a no-break space, so a real token
    # glued to a made-up key across one would go out once the key was lifted
    made_up = "sk-" + "f" * 16
    for gap in " \t\n\r\f\v":
        assert [c for c, _ in task_runs.secret_matches(made_up + gap + "x" + token("r"))] == [1], repr(gap)
    for gap in (chr(0xA0), chr(0x1C), chr(0x85), chr(0x2028), chr(0x3000)):
        assert gap.isspace()                                    # the control: white space, though not ASCII's
        line = made_up + gap + "x" + token("r")
        assert [c for c, _ in task_runs.secret_matches(line)] == [1, len(made_up) + 3], repr(gap)
    repo = str(tmp_path / "repo")
    since = make_repo(repo)
    with open(os.path.join(repo, "a.txt"), "w", encoding="utf-8") as fh:
        fh.write("FAKE=" + made_up + chr(0xA0) + "x" + token("r") + "\n")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "work", cwd=repo)
    made = git("rev-parse", "HEAD", cwd=repo)
    first = scan_all(repo, since, made)["findings"]
    assert [f["column"] for f in first] == [6, 27]
    got = scan_all(repo, since, made, [first[0]["finding"]])
    assert [f["column"] for f in got["findings"]] == [27] and got["passed"] == [first[0]["finding"]]


def test_a_line_with_no_shape_where_its_pattern_finds_it_runs_no_reader(monkeypatch):
    # a first look at a line built from the shapes without
    # their \b would let words such as "risk-assessment-framework" pass it and every reader run, and a scan of ordinary
    # text take about twice as long; a glued value counts only after one a pattern finds, so a line with none has none
    real, built = task_runs._reader, []

    def counted(*a):
        built.append(a[0])
        return real(*a)
    monkeypatch.setattr(task_runs, "_reader", counted)
    for words in ("  webapp-frontend-1: risk-assessment-framework", "task-description-heading", "app-database",
                  "mask-image-gradient-top", "xsk-" + "a" * 20 + " xghp_" + "b" * 36,
                  "payload = 'xeyJhbGciOiJIUzI1NiJ9'"):
        assert task_runs._ANY_GLUED.search(words), words           # the control: each is a value without its \b
        assert list(task_runs.secret_matches(words)) == [] and task_runs.secret_shape(words) is None, words
    assert built == []
    assert list(task_runs.secret_matches("x sk-" + "a" * 16)) and built         # the control: a value builds them
    monkeypatch.undo()
    # lines read in place, each holding a value without its \b: the first look reads each to its own end, never on
    # through the text after it (8,000 such lines read so took 117 s)
    text = "".join(f"xghp_{n} xsk-{n}\n" for n in range(4000))
    began, at = time.monotonic(), 0
    while at < len(text):
        stop = text.index("\n", at)
        assert list(task_runs.secret_matches(text, at, stop)) == []
        at = stop + 1
    assert time.monotonic() - began < 2
    # and the first look looks at the scan's clock as secret_matches gives it
    _clock(monkeypatch, 1.0)
    with pytest.raises(task_runs._OutOfTime):
        list(task_runs.secret_matches("xghp_" * 5000, deadline=3.0))
    # a shape whose pattern repeats with no fixed count has no reading here, and is refused
    for pattern in (r"\bfoo[a-z]+", r"\bfoo[a-z]*", r"\bfoo[a-z]?", r"\bfoo[a-z]{3,}", r"\bfoo[a-z]{3,5}"):
        with pytest.raises(ValueError):
            task_runs._shape_reading(re.compile(pattern, re.ASCII))
    assert task_runs._shape_reading(re.compile(r"\bfoo[a-z]{3}", re.ASCII))[0] == "pattern"
    assert task_runs._shape_reading(re.compile(r"\bfoo[a-z]{16,}", re.ASCII))[1].pattern == r"\bfoo[a-z]{16}"


def test_a_hand_off_pushes_to_the_address_the_app_recorded_not_the_repository_s_origin(tws):
    # a hand-off pushes to the address recorded when the task was made; the loopback HTTPS test's is the
    # only other address, and it skips where 127.0.0.1:443 cannot be bound: here the recorded address is a second bare
    # repository on this machine, and the repository's origin another
    put_agent(tws)
    commit, base = offered(tws)
    assert accept(tws, commit, base).status_code == 200
    done = commit_in(task_work(tws), "more.txt", "more work\n")
    recorded = str(tws.tmp / "recorded.git")
    git("init", "-q", "--bare", "-b", "main", recorded, cwd=str(tws.tmp))
    r = _push(tws, done, commit, done, address=recorded)
    assert r.status_code == 200 and r.json()["pushed"] is True, r.text
    assert git("rev-parse", "refs/heads/pp/task-" + TASK, cwd=recorded) == done
    assert git("for-each-ref", "refs/heads/pp/task-" + TASK, cwd=tws.remote) == ""


def test_a_received_task_s_work_goes_to_the_owner_s_task_branch_the_push_names(tws):
    # what the teammate's side pushes: a received task's work, by its hash, to the owner's task's branch,
    # which `ref` names, not to the received task's own; its own branch's head is still the one checked; a ref that is
    # not a task's branch is refused, and nothing is pushed
    put_agent(tws)
    commit, base = offered(tws)
    assert accept(tws, commit, base).status_code == 200
    done = commit_in(task_work(tws), "more.txt", "more work\n")
    owners = "refs/heads/pp/task-" + "c" * 32
    assert _push(tws, done, commit, commit, ref=owners).status_code == 409        # the received task's branch moved
    r = _push(tws, done, commit, done, call_id="handover:offer-1:1", ref=owners)
    assert r.status_code == 200, r.text
    assert r.json()["pushed"] is True and r.json()["ref"] == owners and r.json()["after"] == done
    assert git("rev-parse", owners, cwd=tws.remote) == done
    assert git("for-each-ref", "refs/heads/pp/task-" + TASK, cwd=tws.remote) == ""
    main = git("rev-parse", "refs/heads/main", cwd=tws.remote)
    for ref in ("refs/heads/main", "refs/heads/pp/task-" + "c" * 31, "refs/heads/pp/task-" + "C" * 32,
                "refs/tags/pp/task-" + "c" * 32, "pp/task-" + "c" * 32, "refs/heads/pp/task-" + "c" * 32 + "x", 7):
        r = _push(tws, done, commit, done, ref=ref)
        assert r.status_code == 400 and r.json()["detail"] == "A push goes to a task's branch.", ref
    assert git("rev-parse", "refs/heads/main", cwd=tws.remote) == main


def test_watch_a_changed_git_setting_refuses_the_task_s_git_with_409(tws):
    put_agent(tws)
    commit, base = offered(tws)
    assert accept(tws, commit, base).status_code == 200
    with open(os.path.join(tws.root, "repos", "demo", ".git", "config"), "a") as fh:
        fh.write("[core]\n\tfsmonitor = /tmp/planted\n")
    r = _push(tws, commit, base, commit)
    assert r.status_code == 409 and "watched places changed" in r.json()["detail"]


def test_a_return_descends_from_the_handed_commit_or_is_refused(tws):
    put_agent(tws)
    handed, base = offered(tws)
    assert accept(tws, handed, base).status_code == 200
    # the teammate's side: a step checkpoint on top of the handed commit, and a protected path touched
    clone = str(tws.tmp / "owner-clone")
    with open(os.path.join(clone, "tests_x.py"), "w") as fh:
        fh.write("x\n")
    git("add", "-A", cwd=clone)
    git("commit", "-qm", f"Task {'ef' * 16}: step 1, checkpoint 1 (step)", cwd=clone)
    returned = git("rev-parse", "HEAD", cwd=clone)
    git("push", "-q", tws.remote, f"HEAD:refs/heads/pp/task-{'ef' * 16}", cwd=clone)
    r = tws.http.post(f"/agents/tasks/{TASK}/fetch", json={"address": tws.remote, "commit": returned,
                                                           "purpose": "return", "base": handed,
                                                           "protected": ["tests_*.py"]})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["descends"] is True and got["protected_touched"] == ["tests_x.py"] and got["steps"] == [1]
    assert git("rev-parse", "HEAD", cwd=task_work(tws)) == returned       # the owner's task goes on from it
    # and the tree the worktree is left at, the end state the app keeps with the commit, which the next attempt's
    # resume check expects
    assert got["tree"] == git("rev-parse", f"{returned}^{{tree}}", cwd=clone)
    assert got["tree"] == git("write-tree", cwd=task_work(tws))
    # a commit that does not descend from the handed one (here, one from before it) is refused
    r = tws.http.post(f"/agents/tasks/{TASK}/fetch", json={"address": tws.remote, "commit": base,
                                                           "purpose": "return", "base": returned, "protected": []})
    assert r.status_code == 409 and "does not descend" in r.json()["detail"]
    assert git("rev-parse", "HEAD", cwd=task_work(tws)) == returned


@pytest.mark.parametrize("by_hash", [True, False])
def test_a_return_s_fetch_starts_no_automatic_gc_whose_repack_would_write_a_watched_place(tws, by_hash):
    # a route keeps the look its watch compared before its git ran (_keep_seen),
    # and a return's git fetch could start git's automatic gc, whose repack writes the common folder's info, a watched
    # place: the next route, or the next attempt's start, would find it changed. The thresholds made 1 and the gc not
    # detached, before the task was made, so that a gc would run with the fetch. And its second fetch, of the task
    # branches, which runs where the commit cannot be fetched by its hash:
    # protocol 0, a remote that gives no commit by its hash but a branch's tip, the returned one behind its tip
    for key, value in (("gc.autoPackLimit", "1"), ("fetch.unpackLimit", "1"), ("gc.autoDetach", "false")):
        tws.demo("config", key, value)
    if not by_hash:
        tws.demo("config", "protocol.version", "0")
        git("config", "uploadpack.allowReachableSHA1InWant", "false", cwd=tws.remote)
    put_agent(tws)
    handed, base = offered(tws)
    assert accept(tws, handed, base).status_code == 200
    clone = str(tws.tmp / "owner-clone")
    returned = commit_in(clone, "returned.txt", "the teammate's work\n")
    if not by_hash:
        commit_in(clone, "later.txt", "a later commit on the branch\n")
    git("push", "-q", tws.remote, f"HEAD:refs/heads/pp/task-{'ef' * 16}", cwd=clone)
    r = tws.http.post(f"/agents/tasks/{TASK}/fetch", json={"address": tws.remote, "commit": returned,
                                                           "purpose": "return", "base": handed, "protected": []})
    assert r.status_code == 200, r.text
    assert not os.path.exists(os.path.join(tws.root, "repos", "demo", ".git", "info", "refs"))
    pushed = _push(tws, returned, handed, returned)                 # the next route's watch finds nothing changed
    assert pushed.status_code == 200 and pushed.json()["pushed"] is True, pushed.text


@pytest.mark.parametrize("route", ["return", "discard", "keep", "keep nothing"])
def test_a_write_while_a_route_s_git_runs_is_found_by_the_next_route(tws, route):
    # a route that kept a look taken after its own git as the next baseline would let a process the last attempt left
    # behind that wrote a watched place while that git ran become the baseline unseen; a git on the workspace's PATH
    # that writes one while the route's git runs (a return's fetch, a discard's reset) puts the write in that window
    # with no timing. A keep's too (its write-tree; a version keeping a look taken after its git for a keep alone fails
    # here). A keep that commits and one that commits nothing, each, the place the next route finds changed named, and
    # for a keep that commits a control first: two such keeps in turn find nothing changed, so its own git after
    # write-tree (commit-tree, update-ref, read-tree) writes no watched place
    real = shutil.which("git")
    marker = tws.tmp / "plant"
    info = os.path.join(tws.root, "repos", "demo", ".git", "info")
    action = "keep" if route.startswith("keep") else route
    verb = {"return": "fetch", "discard": "reset", "keep": "write-tree"}[action]
    shim = tws.bin / "git"
    shim.write_text(f'#!/bin/sh\ncase " $* " in *" {verb} "*) if [ -e "{marker}" ]; then rm -f "{marker}"; '
                    f'echo planted > "{info}/planted"; fi ;; esac\nexec "{real}" "$@"\n')
    shim.chmod(0o755)
    put_agent(tws)
    if route == "return":
        handed, base = offered(tws)
        assert accept(tws, handed, base).status_code == 200
        clone = str(tws.tmp / "owner-clone")
        returned = commit_in(clone, "returned.txt", "the teammate's work\n")
        git("push", "-q", tws.remote, f"HEAD:refs/heads/pp/task-{'ef' * 16}", cwd=clone)
        marker.write_text("")
        r = tws.http.post(f"/agents/tasks/{TASK}/fetch", json={"address": tws.remote, "commit": returned,
                                                               "purpose": "return", "base": handed, "protected": []})
        assert r.status_code == 200, r.text
        assert not marker.exists() and os.path.exists(os.path.join(info, "planted"))      # written while it ran
        after = _push(tws, returned, handed, returned)
    else:
        until(tws, queue(tws)["run_id"])
        work = task_work(tws)
        head = git("rev-parse", "HEAD", cwd=work)

        def resolve():
            return tws.http.post(f"/agents/tasks/{TASK}/worktree", json={"fence": 1, "action": action, "head": head})

        def leave(name):
            with open(os.path.join(work, name), "w") as fh:
                fh.write("left by a stop\n")
        if route == "keep":
            leave("kept.txt")
            control = resolve()
            assert control.status_code == 200 and control.json()["head"] != head, control.text    # it committed
            again = resolve()
            assert again.status_code == 200, again.text                     # the control: nothing changed by it
        if route != "keep nothing":
            leave("left.txt")
        before = git("rev-parse", "HEAD", cwd=work)
        marker.write_text("")
        r = resolve()
        assert r.status_code == 200, r.text
        assert not marker.exists() and os.path.exists(os.path.join(info, "planted"))      # written while it ran
        if action == "keep":
            assert (r.json()["head"] != before) == (route == "keep"), r.text      # committed, or committed nothing
        after = resolve()
    detail = after.json().get("detail", "")
    assert after.status_code == 409 and "watched places changed" in detail and f"({info})" in detail, after.text


@pytest.mark.parametrize("route", ["return", "discard", "keep"])
def test_an_account_added_before_a_route_is_compared_from_the_look_that_route_kept(tws, route):
    # a place absent from the kept look is taken as it is at the next check (TaskRuns.expected), so it is the keep
    # (_keep_seen) of a route (a return, the worktree route) that pins the folder of an account added since the last
    # attempt; such a route keeping nothing, reasoning that its watch's look is the one already kept, would leave a
    # later write to that folder, by a process the last attempt left behind, to be found by no later route or start. The
    # worktree route's keep as well as its discard (a version keeping on a discard alone, as the one that resets and
    # cleans the worktree, fails here). What decides a keep is the route that ran, as built: a return taken keeps, and
    # the worktree route for either action once its git has run, whatever it commits (this keep commits nothing); a
    # push, the checks and a view of a task held here each run the watch and keep nothing, and a route refused keeps
    # nothing
    put_agent(tws)
    if route == "return":
        handed, base = offered(tws)
        assert accept(tws, handed, base).status_code == 200
    else:
        done = attempt(tws, "complete done")
        assert done["state"] == "done", done
        resolve = {"fence": done["task"]["fence"], "action": route, "head": expect_of(done)["head"]}
    later = tws.http.post("/accounts", json={"kind": "claude", "name": "Later"}).json()["id"]
    if route == "return":
        clone = str(tws.tmp / "owner-clone")
        returned = commit_in(clone, "returned.txt", "the teammate's work\n")
        git("push", "-q", tws.remote, f"HEAD:refs/heads/pp/task-{'ef' * 16}", cwd=clone)
        r = tws.http.post(f"/agents/tasks/{TASK}/fetch", json={"address": tws.remote, "commit": returned,
                                                               "purpose": "return", "base": handed, "protected": []})
    else:
        r = tws.http.post(f"/agents/tasks/{TASK}/worktree", json=resolve)
    assert r.status_code == 200, r.text
    if route == "keep":
        assert r.json()["head"] == resolve["head"], r.text                  # the keep committed nothing
    with open(os.path.join(tws.root, "agent-accounts", later, "claude", ".claude.json"), "w") as fh:
        json.dump({"mcpServers": {"planted": {"command": "true"}}}, fh)
    if route == "return":
        after = _push(tws, returned, handed, returned)
    else:
        after = tws.http.post(f"/agents/tasks/{TASK}/worktree", json=resolve)
    assert after.status_code == 409 and "watched places changed" in after.json()["detail"], after.text


OFFERED = "9" * 32          # the owner's task, which the teammate's workspace does not hold


def view(w, commit, address=None, agent=None, base=None, task=OFFERED, protected=()):
    return w.http.post(f"/agents/tasks/{task}/fetch", json={"address": address or w.remote, "commit": commit,
                                                           "purpose": "view", "agent": agent, "base": base,
                                                           "protected": list(protected)})


def test_an_acceptance_as_the_app_sends_it_lists_the_files_its_view_showed(tws):
    # the app's acceptance names no base (task_offers.accept); were the fetch
    # to take the offered commit for its own base, the files a next run would load that it changes would always be
    # none, and the app's check that the teammate had been shown them would compare with an empty list
    put_agent(tws)
    commit, _ = offered(tws, extra={"work.txt": "x\n", ".claude/settings.json": '{"hooks": {}}\n'})
    shown = view(tws, commit)                       # no agent and no base, as the app sends it
    assert shown.status_code == 200, shown.text
    assert shown.json()["loaded_files"] == [".claude/settings.json"]
    r = accept(tws, commit)                         # base None, as the app sends it
    assert r.status_code == 200, r.text
    assert r.json()["commit"] == commit and r.json()["loaded_files"] == shown.json()["loaded_files"]


def test_an_acceptance_lists_the_loaded_files_of_every_commit_offered_and_keeps_what_no_base_gives_it(tws):
    # an offer of one commit cannot tell a list against the default branch from one
    # against the offered commit's parent; and with the default branch as the acceptance's base for what it descends
    # from and the protected paths it touches too, which no base names, a default branch past the offer's base would
    # make it not descend, and list that branch's own change as one the offer makes
    put_agent(tws)
    clone = str(tws.tmp / "owner-clone")
    git("clone", "-q", tws.remote, clone, cwd=str(tws.tmp))
    os.makedirs(os.path.join(clone, ".claude"))
    commit_in(clone, ".claude/settings.json", '{"hooks": {}}\n')         # in the offer's first commit
    commit = commit_in(clone, "work.txt", "the owner's work\n")
    git("push", "-q", tws.remote, "HEAD:refs/heads/pp/task-" + "ab" * 16, cwd=clone)
    git("checkout", "-q", "-b", "later", "HEAD~2", cwd=clone)             # the default branch moves on past it
    commit_in(clone, "secret.txt", "the default branch's own\n")
    # and changes a file a next run loads (against the merge base, "what the offer
    # changes", the acceptance's list would be narrower than the view's, and the teammate would never see this file)
    commit_in(clone, "CLAUDE.md", "the default branch's own instructions\n")
    git("push", "-q", tws.remote, "HEAD:refs/heads/main", cwd=clone)
    tws.demo("fetch", "-q", "origin")
    tws.demo("remote", "set-head", "origin", "main")
    # the view the app makes names no agent, and compares with the remote's default branch as last fetched
    shown = view(tws, commit)
    assert shown.status_code == 200 and shown.json()["loaded_files"] == [".claude/settings.json", "CLAUDE.md"], \
        shown.text
    # the acceptance names its agent, and compares with that agent's own start:
    # here the checkout's HEAD, which never had the default branch's CLAUDE.md, nor has the offered commit; not
    # with the parent, which lists nothing. The app's check then refuses once and shows the teammate this list
    # (task_offers' SHOWN_OTHERS)
    r = tws.http.post(f"/agents/tasks/{TASK}/fetch", json={"address": tws.remote, "commit": commit,
                                                            "purpose": "accept", "agent": AGENT_ID, "base": None,
                                                            "protected": ["secret.txt"]})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["loaded_files"] == [".claude/settings.json"]
    assert got["descends"] is True and got["protected_touched"] == []     # as no base gives them


def test_with_no_default_branch_a_view_and_an_acceptance_list_every_loaded_file_of_the_offered_commit(tws):
    # a repository cloned while its remote was empty has no default branch here (no origin HEAD, and its
    # own HEAD on no commit); a view and an acceptance with no base that compared the offered commit with itself would
    # list none of the files a next run would load, and the app's check that the teammate saw them would pass on two
    # empty lists. They compare against git's empty tree. The offer is made elsewhere and pushed to the remote the
    # receiving repository was cloned from, which is the offer's address, since an acceptance takes only into a
    # repository whose origin is its address
    fresh = str(tws.tmp / "fresh.git")
    git("init", "-q", "--bare", "-b", "main", fresh, cwd=str(tws.tmp))
    git("clone", "-q", fresh, os.path.join(tws.root, "repos", "fresh"), cwd=str(tws.tmp))
    owners = str(tws.tmp / "fresh-owner")
    git("clone", "-q", fresh, owners, cwd=str(tws.tmp))
    os.makedirs(os.path.join(owners, ".claude"))
    with open(os.path.join(owners, ".claude", "settings.json"), "w") as fh:
        fh.write('{"hooks": {"PreToolUse": []}}\n')
    commit = commit_in(owners, "work.txt", "the owner's work\n")
    git("push", "-q", fresh, "HEAD:refs/heads/pp/task-" + "ab" * 16, cwd=owners)
    put_agent(tws, repo="fresh")
    shown = view(tws, commit, address=fresh)              # no agent and no base, as the app sends it
    assert shown.status_code == 200, shown.text
    assert shown.json()["loaded_files"] == [".claude/settings.json"]
    r = tws.http.post(f"/agents/tasks/{TASK}/fetch", json={"address": fresh, "commit": commit, "purpose": "accept",
                                                           "agent": AGENT_ID, "base": None, "protected": ["work.txt"]})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["loaded_files"] == [".claude/settings.json"]
    # what it descends from and the protected paths it touches stay as no base gives them
    assert got["descends"] is True and got["protected_touched"] == []
    assert git("rev-parse", "HEAD", cwd=task_work(tws)) == commit


def _four_bases(w):
    """The offered commit, in a repository where each base a list with no base may be compared with
    gives a list of its own: the checkout's HEAD (demo's main: README.md and AGENTS.md), the
    remote's default branch as last fetched (origin's HEAD: those and CLAUDE.md), a branch `base` (README.md, AGENTS.md
    and .claude/settings.json), and nothing. The offered commit holds all four files, and work.txt."""
    demo = os.path.join(w.root, "repos", "demo")
    commit_in(demo, "AGENTS.md", "the repository's own agents file\n")
    git("push", "-q", w.remote, "main", cwd=demo)
    owners = str(w.tmp / "owner-clone")
    git("clone", "-q", w.remote, owners, cwd=str(w.tmp))
    commit_in(owners, "CLAUDE.md", "the default branch's own instructions\n")
    git("push", "-q", w.remote, "HEAD:refs/heads/main", cwd=owners)
    git("fetch", "-q", "origin", cwd=demo)
    git("remote", "set-head", "origin", "main", cwd=demo)
    git("checkout", "-q", "-b", "base", cwd=demo)
    os.makedirs(os.path.join(demo, ".claude"))
    commit_in(demo, ".claude/settings.json", '{"hooks": {}}\n')
    git("checkout", "-q", "main", cwd=demo)
    os.makedirs(os.path.join(owners, ".claude"))
    with open(os.path.join(owners, ".claude", "settings.json"), "w") as fh:
        fh.write('{"hooks": {}}\n')
    commit = commit_in(owners, "work.txt", "the owner's work\n")
    git("push", "-q", w.remote, "HEAD:refs/heads/pp/task-" + "ab" * 16, cwd=owners)
    return commit


def test_a_hand_off_s_list_is_compared_with_the_named_agent_s_base_ref(tws):
    # with no base, a view naming an agent and an
    # acceptance compare with the teammate's agent's own start, its base_ref where it has one, as its own runs start:
    # neither with the remote's default branch (which listed .claude/settings.json) nor with the checkout's HEAD
    commit = _four_bases(tws)
    put_agent(tws, base_ref="base")
    r = view(tws, commit, agent=AGENT_ID, protected=["CLAUDE.md", ".claude/settings.json"])
    assert r.status_code == 200 and r.json()["loaded_files"] == ["CLAUDE.md"], r.text
    # what the commit descends from and the protected paths it touches are measured against the default branch, the
    # agent's start deciding the list alone: it descends from origin's HEAD, not
    # from the agent's base_ref, and changes .claude/settings.json since origin's HEAD (CLAUDE.md since the base_ref)
    assert r.json()["descends"] is True and r.json()["protected_touched"] == [".claude/settings.json"], r.text
    r = accept(tws, commit)
    assert r.status_code == 200 and r.json()["loaded_files"] == ["CLAUDE.md"], r.text


def test_a_hand_off_s_list_is_compared_with_the_checkout_s_head_for_an_agent_with_no_base_ref(tws):
    # an agent with no base_ref starts from the checkout's HEAD, and its list is compared
    # with that, not with the remote's default branch, which listed CLAUDE.md as no change
    commit = _four_bases(tws)
    put_agent(tws)
    r = view(tws, commit, agent=AGENT_ID)
    assert r.status_code == 200 and r.json()["loaded_files"] == [".claude/settings.json", "CLAUDE.md"], r.text
    r = accept(tws, commit)
    assert r.status_code == 200 and r.json()["loaded_files"] == [".claude/settings.json", "CLAUDE.md"], r.text
    # but a view where the task is, naming its agent as custody's own view does (task_offers), compares with the
    # repository's default branch as this workspace has it (origin's HEAD), as custody's view does, not with
    # that agent's start: the agent's start decides the teammate's list alone
    r = view(tws, commit, agent=AGENT_ID, task=TASK)
    assert r.status_code == 200 and r.json()["loaded_files"] == [".claude/settings.json"], r.text


def test_a_view_naming_no_agent_compares_with_the_remote_s_default_branch_as_last_fetched(tws):
    # the view the app makes names no agent, and compares with the remote's default
    # branch as the workspace last fetched it, whichever agent is on the repository and wherever its checkout is
    commit = _four_bases(tws)
    put_agent(tws, base_ref="base")
    r = view(tws, commit)
    assert r.status_code == 200 and r.json()["loaded_files"] == [".claude/settings.json"], r.text


def test_with_nothing_to_compare_with_a_hand_off_s_list_holds_every_loaded_file_of_the_commit(tws):
    # where there is neither the named agent's start nor, with no agent named, the
    # remote's default branch, the list is compared with nothing, so every file a next run would load that the commit
    # holds is listed. An agent whose base_ref names no commit has no start: the remote's default branch is not taken
    # in its place; and a view naming no agent never falls back on the checkout's HEAD
    commit = _four_bases(tws)
    every = [".claude/settings.json", "AGENTS.md", "CLAUDE.md"]
    put_agent(tws, base_ref="nowhere")
    r = view(tws, commit, agent=AGENT_ID)
    assert r.status_code == 200 and r.json()["loaded_files"] == every, r.text
    r = accept(tws, commit)
    assert r.status_code == 200 and r.json()["loaded_files"] == every, r.text
    git("remote", "set-head", "origin", "--delete", cwd=os.path.join(tws.root, "repos", "demo"))
    r = view(tws, commit)
    assert r.status_code == 200 and r.json()["loaded_files"] == every, r.text
    # while what it descends from is still measured against the checkout's HEAD there:
    # a commit of the checkout's own, which the offered commit does not hold, makes it not descend
    commit_in(os.path.join(tws.root, "repos", "demo"), "local.txt", "the teammate's own\n")
    r = view(tws, commit)
    assert r.status_code == 200 and r.json()["loaded_files"] == every and r.json()["descends"] is False, r.text


def test_custody_s_view_compares_with_the_default_branch_whichever_agent_it_names(tws):
    # custody's view of the owner's task (task_offers names the task's
    # own agent) compares the recorded commit with the repository's default branch at the recorded address:
    # its origin's HEAD as last fetched, else the checkout's HEAD, and does not read the agent it names. So
    # the agent's base_ref, here a feature branch adding a hook, which the task's checkpoint 0 inherits and a comparison
    # with which would show nothing, is not what the commit is compared with: the agent's start decides the teammate's
    # list alone. (Where origin's HEAD is absent, the checkout's HEAD stands in, which is also the start
    # of an agent with no base_ref: the next test.)
    demo = os.path.join(tws.root, "repos", "demo")
    commit_in(demo, "CLAUDE.md", "the default branch's own instructions\n")
    git("push", "-q", tws.remote, "main", cwd=demo)
    git("fetch", "-q", "origin", cwd=demo)
    git("remote", "set-head", "origin", "main", cwd=demo)
    git("checkout", "-q", "-b", "feature", cwd=demo)
    os.makedirs(os.path.join(demo, ".claude"))
    commit_in(demo, ".claude/settings.json", '{"hooks": {"PreToolUse": []}}\n')
    git("checkout", "-q", "main", cwd=demo)
    put_agent(tws, base_ref="feature")
    until(tws, queue(tws)["run_id"])                    # the task's worktree, made at its agent's start
    commit = git("rev-parse", "HEAD", cwd=task_work(tws))
    assert commit == git("rev-parse", "feature", cwd=demo)     # its checkpoint 0, which holds the hook
    hook = [".claude/settings.json"]
    for agent in (AGENT_ID, None, "0e0e0e0e0e0e"):      # its own, as task_offers names it; none; one not here
        r = view(tws, commit, agent=agent, task=TASK, protected=hook)
        assert r.status_code == 200 and r.json()["loaded_files"] == hook, (agent, r.text)
        # and the protected paths it touches are measured against that default branch too, not against the agent's
        # start nor with no base, which would touch none
        assert r.json()["protected_touched"] == hook, (agent, r.text)
    # with no origin HEAD, the checkout's HEAD where it names a commit, as here (demo's main, which holds CLAUDE.md as
    # the commit does), whether or not the view names an agent: not nothing, which would list CLAUDE.md too and so end
    # such a hand-off
    git("remote", "set-head", "origin", "--delete", cwd=demo)
    for agent in (AGENT_ID, None):
        r = view(tws, commit, agent=agent, task=TASK, protected=hook)
        assert r.status_code == 200 and r.json()["loaded_files"] == hook, (agent, r.text)
        assert r.json()["protected_touched"] == hook, (agent, r.text)


def test_custody_s_view_lists_a_hook_the_checkout_s_branch_adds_while_origin_s_head_is_there(tws):
    # an owner's agent with no base_ref starts from the checkout's HEAD, here a local
    # branch whose .claude/settings.json adds a hook, which the task's checkpoint 0 inherits. Custody's view compares
    # with origin's HEAD and lists the hook. Where origin's HEAD is absent, the checkout's HEAD stands in for the
    # default branch, and it is that agent's own start, so the hook is not listed: the fallback's limit, which the
    # manual names
    demo = os.path.join(tws.root, "repos", "demo")
    git("fetch", "-q", "origin", cwd=demo)
    git("remote", "set-head", "origin", "main", cwd=demo)
    git("checkout", "-q", "-b", "local", cwd=demo)
    os.makedirs(os.path.join(demo, ".claude"))
    commit_in(demo, ".claude/settings.json", '{"hooks": {"PreToolUse": []}}\n')
    put_agent(tws)
    until(tws, queue(tws)["run_id"])                    # the task's worktree, made at the checkout's HEAD
    commit = git("rev-parse", "HEAD", cwd=task_work(tws))
    assert commit == git("rev-parse", "local", cwd=demo)
    r = view(tws, commit, agent=AGENT_ID, task=TASK)
    assert r.status_code == 200 and r.json()["loaded_files"] == [".claude/settings.json"], r.text
    git("remote", "set-head", "origin", "--delete", cwd=demo)
    r = view(tws, commit, agent=AGENT_ID, task=TASK)
    assert r.status_code == 200 and r.json()["loaded_files"] == [], r.text


def test_an_offer_is_viewed_where_its_task_is_not_against_the_default_branch_as_the_workspace_has_it(tws):
    # the teammate sees every change the offered commit makes to the files a
    # next run would load, against the repository's default branch as the teammate's workspace has it; the
    # teammate's workspace does not hold the task, and the view names no base
    commit, base = offered(tws, extra={"work.txt": "x\n", ".claude/settings.json": '{"hooks": {}}\n',
                                       ".githooks/pre-commit": "exit 0\n"})
    loads = [".claude/settings.json", ".githooks/pre-commit"]
    r = view(tws, commit, address=str(tws.tmp / "another.git"))
    assert r.status_code == 404 and "No repository here has the task's address" in r.json()["detail"], r.text
    # the repository here whose origin is the address, however the address is written; a paused agent on another
    # repository holds nothing up
    put_agent(tws, agent_id="0f0f0f0f0f0f", repo=None, paused=True)
    for address in (tws.remote, tws.remote + "/", tws.remote[:-len(".git")], "file://" + tws.remote):
        r = view(tws, commit, address=address)
        assert r.status_code == 200, r.text
        got = r.json()
        assert got["commit"] == commit and got["descends"] is True and got["loaded_files"] == loads, address
        assert got["protected_touched"] == [] and got["steps"] == []
    # in the repository of the agent named, whatever the address's repository is here, and none while an agent on it
    # is paused or held (the view's git runs there)
    put_agent(tws, paused=True)
    for agent in (None, AGENT_ID):
        r = view(tws, commit, agent=agent)
        assert r.status_code == 409 and "paused or held" in r.json()["detail"], r.text
    put_agent(tws)
    owners = str(tws.tmp / "owner-clone")         # a repository that holds the commit and is no one's origin here
    assert view(tws, commit, address=owners).status_code == 404
    r = view(tws, commit, address=owners, agent=AGENT_ID)
    assert r.status_code == 200 and r.json()["loaded_files"] == loads, r.text
    # the default branch as this workspace has it is its origin's, as last fetched: a commit of its own on its main
    # (here, one adding a CLAUDE.md) is not a change the offer makes
    tws.demo("fetch", "-q", "origin")
    tws.demo("remote", "set-head", "origin", "main")
    commit_in(os.path.join(tws.root, "repos", "demo"), "CLAUDE.md", "the teammate's own\n")
    r = view(tws, commit)
    assert r.status_code == 200 and r.json()["loaded_files"] == loads, r.text
    assert not os.path.exists(os.path.join(tws.root, "agents", AGENT_ID, "tasks", OFFERED))


def test_a_view_or_an_acceptance_waits_for_every_agent_on_the_repository(tws):
    # the agents on a repository share it, and a view's or an acceptance's git runs there:
    # none while any of them is paused or held, or has a run working there, whichever agent the call names
    commit, base = offered(tws, extra={"work.txt": "x\n"})
    put_agent(tws)
    other = "0f0f0f0f0f0f"
    put_agent(tws, agent_id=other, paused=True)            # on the same repository, demo
    for agent in (None, AGENT_ID):
        r = view(tws, commit, agent=agent)
        assert r.status_code == 409 and "paused or held" in r.json()["detail"], r.text
    assert accept(tws, commit, base).status_code == 409
    put_agent(tws, agent_id=other, brief="sleep 3")
    r = tws.http.post(f"/agents/{other}/runs", json={"trigger": {"kind": "manual", "prompt": None}})
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    until(tws, run_id, states=("working",))
    for agent in (None, AGENT_ID):
        r = view(tws, commit, agent=agent)
        assert r.status_code == 409 and "working" in r.json()["detail"], r.text
    r = accept(tws, commit, base)
    assert r.status_code == 409 and "working" in r.json()["detail"], r.text
    assert until(tws, run_id)["state"] == "done"
    assert view(tws, commit, agent=AGENT_ID).status_code == 200
    assert accept(tws, commit, base).status_code == 200


def test_the_owner_s_checks_wait_for_a_running_attempt_and_check_out_where_nothing_was_planted(tws):
    # an attempt's processes live until its end, so the owner's checks are refused while
    # one runs; and their checkout is under a folder made new, as an attempt's own check's is, so a link planted at a
    # name known before (here the fixed one they used) takes nothing elsewhere
    account_file(tws)
    put_agent(tws, brief="complete done")
    done = until(tws, queue(tws, identity=CONFIRMED, check="test -f README.md")["run_id"])
    assert done["state"] == "done", done
    head = expect_of(done)["head"]
    decoy = tws.tmp / "decoy"
    decoy.mkdir()
    os.symlink(str(decoy), os.path.join(os.path.dirname(task_work(tws)), "owner-check"))
    r = tws.http.post(f"/agents/tasks/{TASK}/checks", json={"commit": head, "steps": [1]})
    assert r.status_code == 200 and r.json()["results"][0]["exit"] == 0, r.text
    assert decoy.is_dir() and list(decoy.iterdir()) == []      # never checked out into, nor removed after
    put_agent(tws, brief="sleep 30 ;; complete done")
    run_id = queue(tws, identity=CONFIRMED, fence=2, attempt=2, check="test -f README.md")["run_id"]
    until(tws, run_id, states=("working",))
    r = tws.http.post(f"/agents/tasks/{TASK}/checks", json={"commit": head, "steps": [1]})
    assert r.status_code == 409 and "running" in r.json()["detail"], r.text
    assert tws.http.post(f"/agents/runs/{run_id}/stop").status_code == 200


def test_an_offer_s_view_where_the_task_is_compares_with_the_repository_s_default_branch_not_the_task_s(tws):
    # with no base, never the commit against itself, nor against the task's own branch
    put_agent(tws)
    handed, base = offered(tws, extra={".claude/settings.json": '{"hooks": {}}\n'})
    assert accept(tws, handed, base).status_code == 200
    later, _ = offered(tws, extra={"later.txt": "more\n"})
    r = view(tws, later, task=TASK)
    assert r.status_code == 200, r.text
    assert r.json()["loaded_files"] == [".claude/settings.json"] and r.json()["descends"] is True
    # and, the task here, after the task's watch, as its other fetches
    with open(os.path.join(tws.root, "repos", "demo", ".git", "config"), "a") as fh:
        fh.write("[core]\n\tfsmonitor = /tmp/planted\n")
    r = view(tws, later, task=TASK)
    assert r.status_code == 409 and "watched places changed" in r.json()["detail"], r.text


def test_a_repository_s_address_is_one_key_however_it_is_written():
    key = task_runs._repo_key
    one = key("https://github.com/o/n")
    for other in ("https://github.com/o/n.git", "https://github.com/o/n/", "git@github.com:o/n.git",
                  "ssh://git@github.com/o/n", "https://x-access-token@GitHub.com/o/n", "github.com:o/n"):
        assert key(other) == one, other
    for other in ("https://github.com/o/m", "https://gitlab.com/o/n", "https://github.com/p/n"):
        assert key(other) != one, other
    assert key("/srv/r.git") == key("/srv/r/") == key("file:///srv/r") != key("/srv/s")


def queue(w, run_id=None, agent_id=AGENT_ID, **over):
    """An attempt of the task, as the app queues it. Its identity by default is not
    the account's (nobody@example.org), so that a start ends it at once, blocked, unless a test confirms the stand-in's
    own: the stand-in, taught or not, then ends no other way."""
    over.setdefault("identity", {"email": "nobody@example.org", "organization": None, "plan": "Claude Max",
                                 "org_id": "org-stand-in"})
    task = message(**over)
    body = {"trigger": {"kind": "task", "prompt": None, "id": task["id"]}, "task": task}
    if run_id:
        body["run_id"] = run_id
    r = w.http.post(f"/agents/{agent_id}/runs", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def run_view(w, run_id):
    (got,) = [r for r in w.http.get("/agents/runs?since=0").json()["runs"] if r["run_id"] == run_id]
    return got


def until(w, run_id, states=("done", "failed", "blocked", "stopped", "skipped"), timeout=60):
    import time
    deadline = time.time() + timeout
    while True:
        got = run_view(w, run_id)
        if got["state"] in states:
            return got
        assert time.time() < deadline, got
        time.sleep(0.1)


def until_view(w, run_id, test, timeout=60):
    """The run's view once `test` holds of it."""
    import time
    deadline = time.time() + timeout
    while True:
        got = run_view(w, run_id)
        if test(got):
            return got
        assert time.time() < deadline, got
        time.sleep(0.1)


def starts_of(w):
    path = w.home / "stand-in-runs.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_the_frozen_start_s_arguments_and_environment(tws):
    account_file(tws)                       # its orgId passes check 3; its address is not the one queued
    put_agent(tws)
    queued = queue(tws)
    assert queued["state"] == "queued" and queued["trigger"]["kind"] == "task"
    assert queued["task"]["id"] == TASK and queued["backend"] == {"kind": "account", "id": "default"}
    ended = until(tws, queued["run_id"])
    assert ended["state"] == "blocked"                      # the start reported another account
    assert ended["task"]["claude_version"] == task_shapes.TASK_CLAUDE_VERSION      # recorded
    (start,) = [s for s in starts_of(tws) if "--version" not in s["args"] and s["args"][:2] != ["auth", "status"]]
    args = start["args"]
    # a new SDK session, no --resume; no setting sources, strict MCP, the two tools in dontAsk
    assert not any(a.startswith("--resume") for a in args)
    assert "--setting-sources=" in args or ("--setting-sources" in args
                                            and args[args.index("--setting-sources") + 1] == "")
    assert "--strict-mcp-config" in args
    assert args[args.index("--permission-mode") + 1] == "dontAsk"
    allowed = args[args.index("--allowedTools") + 1].split(",")
    assert sorted(allowed) == sorted(task_shapes.FROZEN_TOOLS)
    # and no built-in tool (the SDK's tools=[], given as --tools "")
    at = [i for i, a in enumerate(args) if a == "--tools"]
    assert len(at) == 1 and args[at[0] + 1:at[0] + 2] == [""], args
    # the sub-agent tool off
    disallowed = args[args.index("--disallowedTools") + 1].split(",")
    assert {"Agent", "Task"} <= set(disallowed)
    # the default account asked for by name, so no CLAUDE_CONFIG_DIR; PP_FROZEN at the frozen start
    env = set(start["env"])
    assert "CLAUDE_CONFIG_DIR" not in env and {"PP_FROZEN", "PP_TASK_ATTEMPT", "PP_AGENT_RUN"} <= env
    # both pins in its environment and in its own --settings env, and no name of REFUSED_ENV
    assert set(task_attempt.ATTEMPT_ENV) | set(task_attempt.WORKSPACE_ENV) <= env
    assert not env & set(task_shapes.REFUSED_ENV)
    settings = json.loads(args[args.index("--settings") + 1])
    assert {k: settings["env"][k] for k in task_attempt.ATTEMPT_ENV} == task_attempt.ATTEMPT_ENV
    assert set(task_attempt.WORKSPACE_ENV) <= set(settings["env"])
    # the task's worktree, on its branch, cut from the agent's repository's head: checkpoint 0
    work = task_work(tws)
    assert os.path.realpath(start["cwd"]) == os.path.realpath(work)
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=work) == "pp/task-" + TASK
    assert git("rev-parse", "HEAD", cwd=work) == tws.demo("rev-parse", "HEAD")
    attempts = tws.http.get(f"/agents/tasks/{TASK}/attempts").json()["attempts"]
    assert attempts == [{"run_id": queued["run_id"], "fence": 1, "state": "blocked"}]


def test_a_task_is_cut_from_the_agent_s_branch_head_or_without_a_kept_worktree_from_its_base(tws):
    # the task's worktree is cut from the agent's branch head when the task starts;
    # an agent that keeps its worktree works on pp/agent-<id> (agents.py), so its task starts where that work stands,
    # not at the repository's head; one that does not keep it starts each run at its base, and so does its task
    demo = os.path.join(tws.root, "repos", "demo")
    base = tws.demo("rev-parse", "HEAD")
    tws.demo("checkout", "-q", "-b", f"pp/agent-{AGENT_ID}")
    kept = commit_in(demo, "agent.txt", "the agent's work so far\n")
    tws.demo("checkout", "-q", "main")
    put_agent(tws)
    until(tws, queue(tws)["run_id"])
    work = task_work(tws)
    assert git("rev-parse", "HEAD", cwd=work) == kept
    record = json.load(open(os.path.join(os.path.dirname(work), "task.json")))
    assert record["checkpoint0"] == kept
    other, other_task = "0e0e0e0e0e0e", "d0d0" + "0" * 28
    tws.demo("branch", f"pp/agent-{other}", kept)
    put_agent(tws, agent_id=other, keep_worktree=False)
    until(tws, queue(tws, agent_id=other, id=other_task)["run_id"])
    assert git("rev-parse", "HEAD", cwd=task_work(tws, other_task, other)) == base


def test_an_attempt_runs_on_the_account_it_names_and_the_default_is_asked_for_by_name(tws):
    r = tws.http.post("/accounts", json={"kind": "claude", "name": "Work"})
    assert r.status_code == 200, r.text
    account = r.json()["id"]
    # only Work's folder holds the stand-in's account (with its orgId): an attempt run anywhere else is refused
    account_file(tws, folder=os.path.join(tws.root, "agent-accounts", account, "claude"))
    put_agent(tws, brief="complete done")
    done = until(tws, queue(tws, identity=CONFIRMED, backend={"kind": "account", "id": account})["run_id"])
    assert done["state"] == "done" and done["task"]["backend"] == {"kind": "account", "id": account}, done
    assert claude_starts(tws) and all("CLAUDE_CONFIG_DIR" in s["env"] for s in claude_starts(tws))
    # with Work the account new sessions start on, an attempt on the default account asks for it by name: it runs
    # there, with no CLAUDE_CONFIG_DIR, and its own address is reported, not Work's
    assert tws.http.post("/accounts/choose", json={"kind": "claude", "account": account}).status_code == 200
    account_file(tws, email="default@example.com")
    before = len(claude_starts(tws))
    done = next_attempt(tws, done, backend={"kind": "account", "id": "default"},
                        identity=dict(CONFIRMED, email="default@example.com"))
    assert done["state"] == "done", done
    assert claude_starts(tws)[before:] and not any("CLAUDE_CONFIG_DIR" in s["env"]
                                                   for s in claude_starts(tws)[before:])


def test_an_agent_s_own_run_s_view_names_the_account_it_started_on(tws):
    # the view's top-level backend is for every
    # run, and for an agent's own run it is the account the run ran on, by its id: its agent's, or, for an agent with
    # none, the one new sessions start on when it starts, under which the app keeps the run's windows
    def agent_with(brief="hello", **agent):
        put_agent(tws, brief=brief, **agent)

    def queue_own():
        r = tws.http.post(f"/agents/{AGENT_ID}/runs", json={"trigger": {"kind": "manual", "prompt": None}})
        assert r.status_code == 200, r.text
        return r.json()["run_id"]

    def ran_on(run_id):
        done = until(tws, run_id)
        assert done["state"] == "done", done
        return done["backend"]

    def account(name):
        return {"kind": "account", "id": name}

    agent_with()
    assert ran_on(queue_own()) == account("default")
    work = tws.http.post("/accounts", json={"kind": "claude", "name": "Work"}).json()["id"]
    spare = tws.http.post("/accounts", json={"kind": "claude", "name": "Spare"}).json()["id"]
    assert tws.http.post("/accounts/choose", json={"kind": "claude", "account": work}).status_code == 200
    assert ran_on(queue_own()) == account(work)
    agent_with(account=spare)
    assert ran_on(queue_own()) == account(spare)
    agent_with(account="default")
    assert ran_on(queue_own()) == account("default")
    # the account it started on, not the one new sessions started on when it was queued: a run queued behind its
    # agent's working one starts after new sessions moved to Spare, and names Spare
    agent_with(brief="sleep 2")
    first = queue_own()
    assert until(tws, first, states=("working",))["backend"] == account(work)
    second = queue_own()
    waiting = run_view(tws, second)
    assert waiting["state"] == "queued" and waiting["backend"] is None, waiting
    assert tws.http.post("/accounts/choose", json={"kind": "claude", "account": spare}).status_code == 200
    assert ran_on(first) == account(work)
    assert ran_on(second) == account(spare)


NOTES = {"name": "notes", "kind": "address", "transport": "http", "url": "https://notes.example/mcp",
         "headers": {"X-Team": "docs"}, "secret_headers": ["Authorization"]}


def test_the_attempt_s_mcp_servers_are_written_in_the_agent_s_folder_and_gone_at_its_end(tws):
    # their secret headers are written for claude where the gate refuses a run, as an agent's run's are
    # written, never in the run's own folder, which the run may read; and removed at the attempt's end
    account_file(tws)
    put_agent(tws, brief="complete done", mcp={"servers": [NOTES]})
    r = tws.http.put(f"/agents/{AGENT_ID}/mcp-secrets", json={"secrets": {"notes": {"Authorization": "Bearer s3cr3t"}}})
    assert r.status_code == 200, r.text
    done = until(tws, queue(tws, identity=CONFIRMED)["run_id"])
    assert done["state"] == "done", done
    agent_dir = os.path.join(tws.root, "agents", AGENT_ID)
    run_dir = os.path.join(agent_dir, "runs", done["run_id"])
    args = claude_starts(tws)[-1]["args"]
    given = [a for i, a in enumerate(args) if i and args[i - 1] == "--mcp-config"]
    (path,) = [g for g in given if not g.lstrip().startswith("{")]     # the SDK's own servers come inline
    assert path == os.path.join(agent_dir, f"servers-{done['run_id']}.json")
    at_work = claude_starts(tws)[-1]
    assert at_work["mcp"]["notes"]["headers"]["Authorization"] == "Bearer s3cr3t"      # it was there for claude
    assert not os.path.exists(path)                                                    # and is gone
    for folder, _, names in os.walk(run_dir):
        for name in names:
            with open(os.path.join(folder, name), "rb") as fh:
                assert b"s3cr3t" not in fh.read(), name
    # the gate refuses a run that place; the run's own folder it lets the run read
    env = {"HOME": str(tws.home), "PP_TASK_ATTEMPT": "1", "PP_AGENT_RUN": "1", "PP_AGENT_DIR": agent_dir,
           "PP_SESSION_DIR": run_dir}
    call = {"tool_name": "Read", "tool_input": {"file_path": path}, "cwd": task_work(tws)}
    assert gate.refusal(call, str(tws.home), env) == gate.AGENT_FOLDER
    mine = dict(call, tool_input={"file_path": os.path.join(run_dir, "attempt-mcp.json")})
    assert gate.refusal(mine, str(tws.home), env) is None


def test_a_step_s_check_runs_in_a_fresh_checkout_outside_the_run_s_folder(tws):
    # a checkout at a name nothing knows before, in the agent's folder (which the gate refuses
    # a run), not in the run's own folder, which the run may write and plant in; gone after
    done = attempt(tws, "append line.txt=one ;; complete step one",
                   check='pwd -P > "$HOME/check-at.txt" && test "$(cat line.txt)" = one')
    assert done["state"] == "done", done
    at = (tws.home / "check-at.txt").read_text().strip()
    agent_dir = os.path.realpath(os.path.join(tws.root, "agents", AGENT_ID))
    holder = os.path.dirname(at)
    assert os.path.dirname(holder) == agent_dir and os.path.basename(holder).startswith("check-1-"), at
    assert not at.startswith(os.path.join(agent_dir, "runs") + os.sep)
    assert not os.path.exists(holder)


def test_queueing_a_task_s_attempt_is_held_to_its_shapes(tws):
    put_agent(tws)
    bad = message()
    r = tws.http.post(f"/agents/{AGENT_ID}/runs", json={"trigger": {"kind": "task", "id": "f" * 32}, "task": bad})
    assert r.status_code == 400                         # the trigger's task is not the message's
    r = tws.http.post(f"/agents/{AGENT_ID}/runs", json={"trigger": {"kind": "manual"}, "task": bad})
    assert r.status_code == 400                         # only a task's attempt carries a task
    r = tws.http.post(f"/agents/{AGENT_ID}/runs", json={"trigger": {"kind": "task", "id": TASK}})
    assert r.status_code == 400                         # nor without one


def test_a_stale_fence_ends_the_attempt_blocked_before_any_claude_starts(tws):
    put_agent(tws)
    until(tws, queue(tws, fence=5)["run_id"])
    before = len(starts_of(tws))
    stale = until(tws, queue(tws, fence=5)["run_id"])
    assert stale["state"] == "blocked" and stale["cause"] == "resume"
    assert stale["task"]["resume"]["check"] == "fence"
    assert len(starts_of(tws)) == before               # no claude started for it


def test_keep_and_discard_carry_out_the_owner_s_resolve(tws):
    put_agent(tws)
    first = until(tws, queue(tws)["run_id"])
    work = task_work(tws)
    head = git("rev-parse", "HEAD", cwd=work)
    with open(os.path.join(work, "left.txt"), "w") as fh:
        fh.write("left by a stop\n")
    # an old fence is refused (412); the owner keeps the worktree: its files committed as the new end state
    assert tws.http.post(f"/agents/tasks/{TASK}/worktree", json={"fence": 2, "action": "keep"}).status_code == 412
    r = tws.http.post(f"/agents/tasks/{TASK}/worktree", json={"fence": 1, "action": "keep"})
    assert r.status_code == 200, r.text
    kept = r.json()
    assert kept["head"] != head and git("rev-parse", "HEAD", cwd=work) == kept["head"]
    assert "left.txt" in git("ls-tree", "--name-only", kept["head"], cwd=work).split()
    assert run_view(tws, first["run_id"])["task"]["end_state"]["head"] == kept["head"]
    # then drops what was left after it: back to the head the app recorded, the leftover gone
    with open(os.path.join(work, "more.txt"), "w") as fh:
        fh.write("more\n")
    r = tws.http.post(f"/agents/tasks/{TASK}/worktree", json={"fence": 1, "action": "discard", "head": head})
    assert r.status_code == 200, r.text
    assert r.json()["head"] == head and git("rev-parse", "HEAD", cwd=work) == head
    assert not os.path.exists(os.path.join(work, "more.txt")) and not os.path.exists(os.path.join(work, "left.txt"))
    assert tws.http.post(f"/agents/tasks/{TASK}/worktree",
                         json={"fence": 1, "action": "discard", "head": "1" * 40}).status_code in (400, 409)


WORKTREE_MOVES = ["reset_behind", "another_branch", "detached"]


def _move_worktree(work, moved):
    """The task's worktree left otherwise than its end state, as check 5 then finds it: its branch reset behind the
    recorded head; HEAD on another branch made by `git checkout -b`, which moved on; or HEAD on no branch, at an earlier
    commit, with a file left there."""
    if moved == "reset_behind":
        git("reset", "-q", "--hard", "HEAD~1", cwd=work)
    elif moved == "another_branch":
        git("checkout", "-q", "-b", "other", cwd=work)
        commit_in(work, "other.txt", "on another branch\n")
    else:
        git("checkout", "-q", "--detach", "HEAD~1", cwd=work)
        with open(os.path.join(work, "loose.txt"), "w") as fh:
            fh.write("left on no branch\n")


@pytest.mark.parametrize("moved", WORKTREE_MOVES)
def test_discard_puts_the_task_s_branch_and_worktree_at_the_recorded_head_wherever_check_5_found_them(tws, moved):
    # check 5 sends the owner to Resolve whenever the worktree is on another branch or none, at another head, or holds
    # other files, and Resolve offers him "reset it to the recorded head"; a route that refused a discard off the task's
    # branch, or to a head the worktree's was not past, would have each Continue block there again
    done = attempt(tws, "append line.txt=one ;; complete step one")
    assert done["state"] == "done", done
    work, recorded, ref = task_work(tws), expect_of(done), "refs/heads/pp/task-" + TASK
    _move_worktree(work, moved)
    held = next_attempt(tws, done, expect=recorded)
    assert held["state"] == "blocked" and held["task"]["resume"]["check"] == "worktree", held
    resolve = {"fence": held["task"]["fence"], "action": "discard"}
    # a commit that does not descend from the task's checkpoint 0 (here, one with no parent) is no head to discard to:
    # nothing moves
    before = git("rev-parse", "HEAD", cwd=work)
    stray = git("commit-tree", git("rev-parse", "HEAD^{tree}", cwd=work), "-m", "not the task's", cwd=work)
    r = tws.http.post(f"/agents/tasks/{TASK}/worktree", json=dict(resolve, head=stray))
    assert r.status_code == 409 and r.json()["detail"] == "That head does not descend from the task's checkpoint 0.", \
        r.text
    assert git("rev-parse", "HEAD", cwd=work) == before
    r = tws.http.post(f"/agents/tasks/{TASK}/worktree", json=dict(resolve, head=recorded["head"]))
    assert r.status_code == 200, r.text
    assert (r.json()["head"], r.json()["tree"]) == (recorded["head"], recorded["tree"])
    assert git("symbolic-ref", "HEAD", cwd=work) == ref and git("rev-parse", ref, cwd=work) == recorded["head"]
    assert git("status", "--porcelain", "--untracked-files=all", cwd=work) == ""
    # the next attempt, expecting the end state the route wrote, passes check 5
    after = next_attempt(tws, run_view(tws, held["run_id"]))
    assert after["state"] == "done", after


@pytest.mark.parametrize("moved", WORKTREE_MOVES)
def test_keep_puts_the_worktree_as_it_is_on_the_task_s_branch_wherever_check_5_found_it(tws, moved):
    # Resolve's other choice, "keep the worktree as it is (its files recorded as the new end state)": off the task's
    # branch the files are committed onto the branch's own head, never onto another branch's
    # commit, and the worktree is put on the task's branch. On the branch reset behind, the
    # worktree's head, with what it holds, is the end state
    done = attempt(tws, "append line.txt=one ;; complete step one")
    assert done["state"] == "done", done
    work, recorded, ref = task_work(tws), expect_of(done), "refs/heads/pp/task-" + TASK
    _move_worktree(work, moved)
    held = next_attempt(tws, done, expect=recorded)
    assert held["state"] == "blocked" and held["task"]["resume"]["check"] == "worktree", held
    r = tws.http.post(f"/agents/tasks/{TASK}/worktree", json={"fence": held["task"]["fence"], "action": "keep"})
    assert r.status_code == 200, r.text
    kept = r.json()
    assert git("symbolic-ref", "HEAD", cwd=work) == ref and git("rev-parse", "HEAD", cwd=work) == kept["head"]
    files = set(git("ls-tree", "-r", "--name-only", kept["head"], cwd=work).split())
    if moved == "reset_behind":
        assert kept["head"] == git("rev-parse", recorded["head"] + "~1", cwd=work) and "line.txt" not in files
    else:
        assert git("rev-parse", kept["head"] + "~1", cwd=work) == recorded["head"]     # on the branch's own head
        assert files == ({"README.md", "line.txt", "other.txt"} if moved == "another_branch"
                         else {"README.md", "loose.txt"})
    assert git("status", "--porcelain", "--untracked-files=all", cwd=work) == ""
    after = next_attempt(tws, run_view(tws, held["run_id"]))
    assert after["state"] == "done", after


def test_with_the_task_s_branch_gone_keep_refuses_and_discard_puts_it_back(tws):
    # Keep commits the files onto the task's branch's own head, so with that branch deleted there is
    # nothing to keep them on, wherever the worktree's HEAD is (on another branch, or then on no commit): Keep refuses,
    # saying to discard, and Discard puts the branch back at the recorded head with the worktree on it
    done = attempt(tws, "append line.txt=one ;; complete step one")
    assert done["state"] == "done", done
    work, recorded, ref = task_work(tws), expect_of(done), "refs/heads/pp/task-" + TASK
    git("checkout", "-q", "-b", "other", cwd=work)
    git("update-ref", "-d", ref, cwd=work)             # not `branch -D`, which writes the config, a watched place
    held = next_attempt(tws, done, expect=recorded)
    assert held["state"] == "blocked" and held["task"]["resume"]["check"] == "worktree", held
    resolve = {"fence": held["task"]["fence"]}
    for leave in ("HEAD on another branch", "HEAD on no commit"):
        if leave == "HEAD on no commit":
            git("checkout", "-q", "--orphan", "stray", cwd=work)
        r = tws.http.post(f"/agents/tasks/{TASK}/worktree", json=dict(resolve, action="keep"))
        assert r.status_code == 409 and r.json()["detail"] == \
            "The task's branch is gone: discard it to the head the app recorded.", (leave, r.text)
    r = tws.http.post(f"/agents/tasks/{TASK}/worktree", json=dict(resolve, action="discard", head=recorded["head"]))
    assert r.status_code == 200 and r.json()["head"] == recorded["head"], r.text
    assert git("symbolic-ref", "HEAD", cwd=work) == ref and git("rev-parse", ref, cwd=work) == recorded["head"]
    assert git("status", "--porcelain", "--untracked-files=all", cwd=work) == ""


def test_keep_with_the_worktree_s_head_on_no_commit_puts_its_files_on_the_task_s_branch(tws):
    # with the task's branch there and the worktree's HEAD on a branch
    # with no commit (`git checkout --orphan`), Keep keeps the files on the task's branch, as for any other
    # place check 5 finds the worktree in
    done = attempt(tws, "append line.txt=one ;; complete step one")
    assert done["state"] == "done", done
    work, recorded, ref = task_work(tws), expect_of(done), "refs/heads/pp/task-" + TASK
    git("checkout", "-q", "--orphan", "stray", cwd=work)
    with open(os.path.join(work, "loose.txt"), "w") as fh:
        fh.write("left with no commit under it\n")
    held = next_attempt(tws, done, expect=recorded)
    assert held["state"] == "blocked" and held["task"]["resume"]["check"] == "worktree", held
    r = tws.http.post(f"/agents/tasks/{TASK}/worktree", json={"fence": held["task"]["fence"], "action": "keep"})
    assert r.status_code == 200, r.text
    kept = r.json()["head"]
    assert git("rev-parse", kept + "~1", cwd=work) == recorded["head"]           # onto the branch's own head
    assert set(git("ls-tree", "-r", "--name-only", kept, cwd=work).split()) == {"README.md", "line.txt", "loose.txt"}
    assert git("symbolic-ref", "HEAD", cwd=work) == ref and git("rev-parse", "HEAD", cwd=work) == kept
    assert git("status", "--porcelain", "--untracked-files=all", cwd=work) == ""
    after = next_attempt(tws, run_view(tws, held["run_id"]))
    assert after["state"] == "done", after


def test_keep_off_the_task_s_branch_takes_an_excluded_path_as_the_task_s_branch_has_it(tws):
    # Keep builds the files against the task's branch's own head, as a
    # checkpoint builds them, never against another branch's commit, which would take a path the person's excludes
    # match as that commit has it: here one an agent's git force-added on another branch, kept out of the task's. A
    # file the repository's own ignore rules keep out (debug.log, which .gitignore matches), force-added there too, is
    # left out as well, as a checkpoint leaves it out; neither is named in the end state's left_out, since git never
    # took either into the files it built
    demo = os.path.join(tws.root, "repos", "demo")
    with open(os.path.join(demo, ".git", "info", "exclude"), "a") as fh:
        fh.write("secret.txt\n")
    done = attempt(tws, "append line.txt=one ;; complete step one")
    assert done["state"] == "done", done
    work, recorded, ref = task_work(tws), expect_of(done), "refs/heads/pp/task-" + TASK
    git("checkout", "-q", "-b", "other", cwd=work)
    for name, text in (("secret.txt", "not for the task's branch\n"), (".gitignore", "*.log\n"),
                       ("debug.log", "an agent's log\n")):
        with open(os.path.join(work, name), "w") as fh:
            fh.write(text)
    git("add", "-f", "secret.txt", "debug.log", cwd=work)
    commit_in(work, "other.txt", "on another branch\n")
    held = next_attempt(tws, done, expect=recorded)
    assert held["state"] == "blocked" and held["task"]["resume"]["check"] == "worktree", held
    r = tws.http.post(f"/agents/tasks/{TASK}/worktree", json={"fence": held["task"]["fence"], "action": "keep"})
    assert r.status_code == 200, r.text
    kept = r.json()["head"]
    assert git("rev-parse", kept + "~1", cwd=work) == recorded["head"]
    assert set(git("ls-tree", "-r", "--name-only", kept, cwd=work).split()) == \
        {"README.md", "line.txt", "other.txt", ".gitignore"}
    assert git("symbolic-ref", "HEAD", cwd=work) == ref
    assert run_view(tws, held["run_id"])["task"]["end_state"]["left_out"] == []


def test_the_attempts_route_lists_a_first_attempt_queued_behind_another_run_and_none_for_a_task_it_never_had(tws):
    # a route that found a task's attempts through its folder, which a first attempt makes only at its
    # start, would answer 404 without one; the app's poll (tasks.check_attempts) passes over an error, so an attempt
    # lost before it started would never free its lease. The runs this workspace knows for the task are listed, folder
    # or not:
    # a first attempt queued behind another run of its agent is listed and kept, and a task none of them carries is []
    put_agent(tws, brief="sleep 60")
    r = tws.http.post(f"/agents/{AGENT_ID}/runs", json={"trigger": {"kind": "manual", "prompt": None}})
    assert r.status_code == 200, r.text
    busy = r.json()["run_id"]
    until(tws, busy, states=("working",))
    queued = queue(tws)
    try:
        assert not os.path.exists(os.path.dirname(task_work(tws)))          # no folder before its start
        got = tws.http.get(f"/agents/tasks/{TASK}/attempts")
        assert got.status_code == 200, got.text
        assert got.json() == {"attempts": [{"run_id": queued["run_id"], "fence": 1, "state": "queued"}]}
        assert tws.http.get(f"/agents/tasks/{'7' * 32}/attempts").json() == {"attempts": []}
        assert tws.http.get("/agents/tasks/not-a-task/attempts").status_code == 404
    finally:
        assert tws.http.post(f"/agents/runs/{busy}/stop").status_code == 200
    until(tws, busy)
    ended = until(tws, queued["run_id"])
    assert tws.http.get(f"/agents/tasks/{TASK}/attempts").json() == \
        {"attempts": [{"run_id": queued["run_id"], "fence": 1, "state": ended["state"]}]}
    # a run whose record is gone is left out, as the agent's own list of runs leaves it out
    shutil.rmtree(os.path.join(tws.root, "agents", AGENT_ID, "runs", queued["run_id"]))
    assert tws.http.get(f"/agents/tasks/{TASK}/attempts").json() == {"attempts": []}


def test_the_attempts_route_lists_a_task_s_ended_and_queued_attempts_after_a_restart(tmp_path):
    # the runs task_of holds for the task are listed after the
    # workspace stops and starts again, task_of being filled again from their records at the start: one that ended
    # before the stop, whose end the app may not have copied yet (the poll ends one not listed failed), and one queued
    # behind another run of its agent, in the order they were queued, their ids chosen so that that order is not theirs
    w = TaskWorkspace(tmp_path)
    try:
        put_agent(w, brief="sleep 60")
        ended = until(w, queue(w, run_id="f0f0f0f0f0f0")["run_id"])     # not the account's identity: ended at start
        r = w.http.post(f"/agents/{AGENT_ID}/runs", json={"trigger": {"kind": "manual", "prompt": None}})
        assert r.status_code == 200, r.text
        until(w, r.json()["run_id"], states=("working",))
        queue(w, run_id="0f0f0f0f0f0f", fence=2, attempt=2)
        listed = [{"run_id": "f0f0f0f0f0f0", "fence": 1, "state": ended["state"]},
                  {"run_id": "0f0f0f0f0f0f", "fence": 2, "state": "queued"}]
        assert w.http.get(f"/agents/tasks/{TASK}/attempts").json() == {"attempts": listed}
        w.stop()
        w.start()
        # both listed, in the order they were queued, each in the state its record now holds (the start ends what was
        # working at the stop, and what waited behind it)
        got = w.http.get(f"/agents/tasks/{TASK}/attempts").json()["attempts"]
        assert [(a["run_id"], a["fence"], a["state"]) for a in got] == \
            [(r, n, run_view(w, r)["state"]) for r, n in (("f0f0f0f0f0f0", 1), ("0f0f0f0f0f0f", 2))], got
        assert got[0]["state"] == ended["state"], got
    finally:
        w.stop()


def test_two_attempts_queued_in_one_second_are_listed_and_started_in_the_order_they_were_queued(tmp_path, monkeypatch):
    # a run's queued_at is a second: two runs queued in one second, which the case above meets on a machine as fast as
    # GitHub's runners, were ordered by their ids, in the attempts route and in the runs the pump starts (runs, queued).
    # Read in this process with the clock held, after a later change to the first's record, which moves its change
    # number past the second's, and again after a restart over the same records
    from proofpane_workspace import workspace as ws_module
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(time, "time", lambda: 1_800_000_000.25)
    root = str(tmp_path / "ws")
    first = ws_module.Workspace(root)
    first.agents.put({"id": AGENT_ID, "name": "Builder", "brief": "work", "repo": None})
    queued = ["f0f0f0f0f0f0", "0f0f0f0f0f0f"]       # their ids' order is not theirs
    for n, run_id in enumerate(queued, 1):
        first.agents.queue(AGENT_ID, trigger={"kind": "task", "id": TASK}, run_id=run_id,
                           task=message(fence=n, attempt=n))
    first.agents._change(queued[0], lambda record: None)
    for ws in (first, ws_module.Workspace(root)):
        assert [a["run_id"] for a in ws.agents.task_runs.attempts_view(TASK)["attempts"]] == queued
        assert [r["id"] for r in ws.agents.runs(states=("queued",))] == queued


def test_the_owner_s_checks_run_on_a_returned_commit_in_a_fresh_checkout(tws):
    put_agent(tws)
    until(tws, queue(tws, check="test -f work.txt")["run_id"])
    work = task_work(tws)
    head = git("rev-parse", "HEAD", cwd=work)
    clone = str(tws.tmp / "teammate")
    git("clone", "-q", tws.remote, clone, cwd=str(tws.tmp))
    returned = commit_in(clone, "work.txt", "done\n")
    git("push", "-q", tws.remote, "HEAD:refs/heads/pp/task-" + TASK, cwd=clone)
    r = tws.http.post(f"/agents/tasks/{TASK}/fetch", json={"address": tws.remote, "commit": returned,
                                                           "purpose": "return", "base": head, "protected": []})
    assert r.status_code == 200, r.text
    # a file left in the worktree that would sway it is not in the fresh checkout
    r = tws.http.post(f"/agents/tasks/{TASK}/checks", json={"commit": returned, "steps": [1]})
    assert r.status_code == 200, r.text
    assert r.json()["results"][0]["n"] == 1 and r.json()["results"][0]["exit"] == 0
    r = tws.http.post(f"/agents/tasks/{TASK}/checks", json={"commit": head, "steps": [1]})
    assert r.json()["results"][0]["exit"] == 1
    # a step this workspace was given no check for
    assert tws.http.post(f"/agents/tasks/{TASK}/checks", json={"commit": head, "steps": [2]}).status_code == 400


def test_the_owner_s_check_of_a_step_its_attempts_never_ran_is_the_one_the_app_gives(tws):
    # the way back's fifth result: a step done on the teammate's side that the owner never ran an attempt
    # of has no check known here, so the app gives the owner's with the request, and it runs; given for a step known,
    # the given one runs (the owner's as the app keeps it); a step with neither is refused, and so is a `checks` out of
    # shape, nothing run
    put_agent(tws)
    until(tws, queue(tws, check="test -f work.txt")["run_id"])
    work = task_work(tws)
    head = git("rev-parse", "HEAD", cwd=work)
    clone = str(tws.tmp / "teammate")
    git("clone", "-q", tws.remote, clone, cwd=str(tws.tmp))
    commit_in(clone, "work.txt", "done\n")
    returned = commit_in(clone, "docs.txt", "the docs\n")
    git("push", "-q", tws.remote, "HEAD:refs/heads/pp/task-" + TASK, cwd=clone)
    r = tws.http.post(f"/agents/tasks/{TASK}/fetch", json={"address": tws.remote, "commit": returned,
                                                           "purpose": "return", "base": head, "protected": []})
    assert r.status_code == 200, r.text

    def checks(commit, steps, given=None):
        body = {"commit": commit, "steps": steps}
        if given is not None:
            body["checks"] = given
        return tws.http.post(f"/agents/tasks/{TASK}/checks", json=body)
    docs = {"2": "test -f docs.txt"}
    r = checks(returned, [1, 2], docs)
    assert r.status_code == 200, r.text
    assert [(x["n"], x["exit"]) for x in r.json()["results"]] == [(1, 0), (2, 0)]
    assert [x["exit"] for x in checks(head, [2], docs).json()["results"]] == [1]
    assert [x["exit"] for x in checks(returned, [1], {"1": "false"}).json()["results"]] == [1]
    assert checks(returned, [2]).status_code == 400
    # each out of shape beside a request that holds otherwise (step 1's check is known here): only its shape refuses it
    assert checks(returned, [1], {"1": "true"}).status_code == 200
    for given in ({"0": "true"}, {"01": "true"}, {"x": "true"}, {"²": "true"}, {"2": ""}, {"2": 5}, {"2": "a\x00b"},
                  {"2": "x" * 2001}, {str(n): "true" for n in range(2, 22)}):
        r = checks(returned, [1], dict({"1": "true"}, **given))
        assert r.status_code == 400 and "a step's number and its command" in r.text, (given, r.text)
    r = checks(returned, [1], ["true"])
    assert r.status_code == 400 and "a step's number and its command" in r.text, r.text


def test_a_task_is_removed_once_no_attempt_of_it_is_queued_or_running(tws):
    account_file(tws)
    put_agent(tws, brief="sleep 30 ;; complete done")
    run_id = queue(tws, identity=CONFIRMED)["run_id"]
    until(tws, run_id, states=("working",))
    assert tws.http.delete(f"/agents/tasks/{TASK}").status_code == 409        # while its attempt works
    assert tws.http.post(f"/agents/runs/{run_id}/stop").status_code == 200
    assert until(tws, run_id)["state"] == "stopped"
    folder = os.path.dirname(task_work(tws))
    assert tws.http.delete(f"/agents/tasks/{TASK}").json() == {"removed": True}
    assert not os.path.exists(folder)
    assert tws.demo("rev-parse", "--verify", "--quiet", "refs/heads/pp/task-" + TASK)     # its branch stays
    assert tws.http.delete(f"/agents/tasks/{TASK}").status_code == 404


# a test that asks the workspace for its accounts with their status has
# `codex login status` run wherever a codex is on its agent's PATH (Workspace.codex_status), so each such test puts a
# codex of its own, signed in, in tws.bin, which begins that PATH, and no person's own Codex is asked
SIGNED_IN_CODEX = """#!/bin/sh
echo "codex $*" >> '{calls}'
if [ "$#" -eq 2 ] && [ "$1" = login ] && [ "$2" = status ]; then
    echo 'Logged in using ChatGPT' >&2
    exit 0
fi
exit 97
"""


def signed_in_codex(folder, calls):
    """A codex in `folder` that answers `login status` signed in with ChatGPT, in the words that
    tests/test_workspace_accounts.py's `CODEX` gives for a sign-in, writes each call it gets as a line of the file
    `calls`, and exits 97 on any other call."""
    path = os.path.join(folder, "codex")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(SIGNED_IN_CODEX.format(calls=calls))
    os.chmod(path, 0o755)


def codex_calls(calls):
    """What a signed_in_codex was asked, one line for each call: none where it was asked nothing."""
    return open(calls, encoding="utf-8").read().splitlines() if os.path.exists(calls) else []


def test_backends_list_each_account_and_each_key_s_provider(tws):
    calls = tws.tmp / "codex-calls"
    signed_in_codex(tws.bin, calls)
    assert shutil.which("codex", path=f"{tws.bin}:{os.environ['PATH']}") == str(tws.bin / "codex")
    got = tws.http.get("/agents/backends").json()["backends"]
    refs = [b["ref"] for b in got]
    assert refs[0] == {"kind": "account", "id": "default"}
    assert [r for r in refs if r["kind"] == "key"] == [{"kind": "key", "provider": p}
                                                       for p in task_shapes.KEY_PROVIDERS]
    assert all(set(b) == {"ref", "label", "ready", "email", "plan", "organization", "windows"} for b in got)
    assert not any(b["ready"] for b in got if b["ref"]["kind"] == "key")
    tws.http.put("/model-providers/anthropic", json={"key": "sk-ant-test-0000000000"})
    got = tws.http.get("/agents/backends").json()["backends"]
    assert [b["ready"] for b in got if b["ref"] == {"kind": "key", "provider": "anthropic"}] == [True]
    assert "sk-ant" not in json.dumps(got)
    # each account's organisation as auth status last gave it
    account_file(tws, org_name="Acme Ltd")
    got = tws.http.get("/agents/backends").json()["backends"]
    assert got[0]["ref"] == {"kind": "account", "id": "default"} and got[0]["organization"] == "Acme Ltd"
    account_file(tws, org_name="Acme" + chr(0x202E) + "Ltd")      # a name shown otherwise than it reads: none given
    assert tws.http.get("/agents/backends").json()["backends"][0]["organization"] is None
    account_file(tws)
    assert tws.http.get("/agents/backends").json()["backends"][0]["organization"] is None
    asked = codex_calls(calls)
    assert all(c == "codex login status" for c in asked), asked


def test_a_codex_account_is_never_a_task_s_backend_nor_read_for_a_confirmation(tws):
    # Continue in a new session may start a session on a Codex account, by the owner's press alone; a task
    # never runs there and the relay never moves one there (Codex is not an agent backend). No
    # backend is a Codex account, and a Codex account's identity is not read, which is how an account is confirmed for
    # a task, the relay's way onto it.
    calls = tws.tmp / "codex-calls"
    signed_in_codex(tws.bin, calls)
    assert shutil.which("codex", path=f"{tws.bin}:{os.environ['PATH']}") == str(tws.bin / "codex")
    codex = tws.http.post("/accounts", json={"kind": "codex", "name": "Team"})
    assert codex.status_code == 200, codex.text
    codex = codex.json()["id"]
    claude = tws.http.post("/accounts", json={"kind": "claude", "name": "Work"}).json()["id"]
    refs = [b["ref"] for b in tws.http.get("/agents/backends").json()["backends"] if b["ref"]["kind"] == "account"]
    assert refs == [{"kind": "account", "id": "default"}, {"kind": "account", "id": claude}]
    r = tws.http.post("/agents/identity", json={"account": codex})
    assert r.status_code == 404 and r.json()["detail"] == "No such account.", r.text
    asked = codex_calls(calls)
    assert all(c == "codex login status" for c in asked), asked


# ---- an attempt's own rules, driven in this process: its callbacks, its stream, its acknowledgement. No claude:
# each callback and each message is given to it as the SDK would give it.

class _Live:
    """An Attempt made from a spec, with its loop's parts made as run() makes them, and every record it emits kept."""

    def __init__(self, tmp_path, fail_durable=False, **over):
        self.records = []

        def emit(record, durable):
            if durable and fail_durable:
                raise OSError("the disk is full")
            self.records.append((record, durable))
        self.a = task_attempt.Attempt(_spec(tmp_path, **over), emit)

    async def up(self):
        import asyncio
        a = self.a
        a._loop = asyncio.get_running_loop()
        a._changed = asyncio.Event()
        a._emit_lock = asyncio.Lock()
        a._ck_lock = asyncio.Lock()
        return a


def _in_loop(coro_fn):
    import asyncio
    return asyncio.run(coro_fn())


def _at_work(a):
    """The attempt at work with a claude started at work, as run() leaves it after the unfreezing restart."""
    a.phase = a.connected_phase = "work"


def _pre(call_id, tool, given):
    return {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": given, "tool_use_id": call_id}


def _turn(a, *calls, message_id="msg-1"):
    from claude_agent_sdk import AssistantMessage, ToolUseBlock
    a._message(AssistantMessage(content=[ToolUseBlock(id=c, name=n, input=i) for c, n, i in calls], model="m",
                                message_id=message_id))


def _result(a, call_id, error=False):
    from claude_agent_sdk import ToolResultBlock, UserMessage
    a._message(UserMessage(content=[ToolResultBlock(tool_use_id=call_id, content="out", is_error=error)]))


def _unseen(a):
    return {e["call_id"]: (e["tool"], e["target"]) for e in a.view["task"]["effects"] if e["unseen"]}


def test_each_callback_fails_closed_on_its_own_error(tmp_path, monkeypatch):
    live = _Live(tmp_path)

    async def body():
        a = await live.up()
        _at_work(a)
        push = {"command": "git push origin pp/task-" + TASK}

        def boom(*args, **kwargs):
            raise RuntimeError("the gate broke")
        monkeypatch.setattr(gate, "refusal", boom)
        got = await a._gate_cb(_pre("t1", "Bash", push), "t1", None)
        assert got == {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                              "permissionDecisionReason": task_attempt.GATE_FAILED}}
        monkeypatch.undo()
        # the gate's own refusal is a deny too, its reason given
        got = await a._gate_cb(_pre("t2", "Read", {"file_path": os.path.join(str(tmp_path), ".claude",
                                                                             ".credentials.json")}), "t2", None)
        assert got["hookSpecificOutput"]["permissionDecision"] == "deny"
        # an allowed call: no permission decision
        assert await a._gate_cb(_pre("t3", "Bash", {"command": "ls"}), "t3", None) == {}
        return a
    _in_loop(body)
    # the intent hook, made to fail at its write: the call refused, and no intent left for it
    live = _Live(tmp_path, fail_durable=True)

    async def body2():
        a = await live.up()
        _at_work(a)
        got = await a._intent_cb(_pre("t4", "Bash", {"command": "git push origin pp/task-" + TASK}), "t4", None)
        assert got["hookSpecificOutput"]["permissionDecisionReason"] == task_attempt.INTENT_FAILED
        assert a.view["task"]["effects"] == []
        # a read or a local call writes none and is not refused
        assert await a._intent_cb(_pre("t5", "Bash", {"command": "ls"}), "t5", None) == {}
    _in_loop(body2)


def test_the_gate_decides_a_call_before_its_intent_callback_runs_when_both_come_at_once(tmp_path, monkeypatch):
    # measured, 2.1.274 sends a call's two
    # PreToolUse requests at once, in the order their matchers are registered; read in the SDK 0.2.154's source, it
    # runs each as a task of its own, made in the order they arrive, awaiting nothing before its callback. The intent
    # callback writes no intent for a call already denied, so the attempt's guarantee is two things: the gate's
    # matcher registered before the intent's, and the gate's callback deciding before it awaits anything. Were either
    # broken, the intent callback could write an intent, and an effect the attempt's end must settle, for a call the
    # gate refuses
    import ast
    import inspect
    import textwrap
    live = _Live(tmp_path)

    async def body():
        import asyncio
        a = await live.up()
        _at_work(a)
        matchers = a._hooks()["PreToolUse"]
        # the order the requests come in: the gate's matcher first, then the intent's, one callback each
        assert [m.hooks for m in matchers] == [[a._gate_cb], [a._intent_cb]]
        push = {"command": "git push origin pp/task-" + TASK}
        loop = asyncio.get_running_loop()

        async def at_once(call_id):
            # the two control requests as the SDK takes them: a task each, made in the order of the registered matchers
            tasks = [loop.create_task(h(_pre(call_id, "Bash", push), call_id, None)) for m in matchers for h in m.hooks]
            return await asyncio.gather(*tasks)

        def boom(payload, home, env):
            raise RuntimeError("the gate broke")
        # the gate's two refusals of a call that reaches its rules: by those rules, and on its own error
        for call_id, refusal in (("t1", lambda payload, home, env: "refused here"), ("t3", boom)):
            monkeypatch.setattr(gate, "refusal", refusal)
            denied, _ = await at_once(call_id)
            assert denied["hookSpecificOutput"]["permissionDecision"] == "deny", call_id
            assert a.calls[call_id].denied and call_id not in a.effects, call_id
        assert a.view["task"]["effects"] == []
        # the same command in a call the gate allows: its intent is written, so the two above are the gate's doing
        monkeypatch.setattr(gate, "refusal", lambda payload, home, env: None)
        assert await at_once("t2") == [{}, {}]
        assert "t2" in a.effects
    _in_loop(body)

    # and the gate's callback holds no wait of any kind, its branch for a decided end and its error's among it, and is
    # no wrapper of another, so that a change which makes it wait fails here before it reaches 2.1.274
    def waits(fn):
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        return [n for n in ast.walk(tree) if isinstance(n, (ast.Await, ast.AsyncFor, ast.AsyncWith))
                or isinstance(n, ast.comprehension) and n.is_async]
    gate_cb = task_attempt.Attempt.__dict__["_gate_cb"]
    b1_9 = "so the intent callback can run first"
    assert not hasattr(gate_cb, "__wrapped__"), "the gate's callback is a wrapper, " + b1_9
    # the walk's own control: it finds the waits of the intent callback, which awaits
    assert waits(task_attempt.Attempt.__dict__["_intent_cb"]), "the walk finds no wait in the intent callback"
    assert not waits(gate_cb), "the gate's callback waits, " + b1_9


def test_a_claude_is_started_only_by_the_attempt_s_start_with_its_checks(tmp_path, monkeypatch):
    # a reminder or a message that finds the claude gone (its channel closed) starts none, since
    # only _start checks what a new claude reports before any message
    live = _Live(tmp_path)

    async def body():
        a = await live.up()
        a.phase = "frozen"
        sent, prepared = [], []

        async def send(text):
            sent.append(text)
        monkeypatch.setattr(a.chat, "send", send)
        assert a.chat.client is None
        await a._send(task_attempt.ACK_AGAIN)          # what a turn's end does while frozen
        assert sent == []
        monkeypatch.undo()
        monkeypatch.setattr(a, "_options", lambda chat: prepared.append(chat) or {})
        with pytest.raises(task_attempt.chats.ChatError):
            await a.chat.send("the owner's message")    # the chat's own send, which would start one
        assert prepared == [] and a.chat.client is None
        return a
    _in_loop(body)


def test_the_gate_judges_a_call_by_the_claude_that_made_it_frozen_or_not(tmp_path):
    # the phase moves on (unfreezing, ending) before the frozen claude is stopped; a call it
    # makes meanwhile is still a frozen claude's
    live = _Live(tmp_path)

    async def body():
        a = await live.up()
        a.connected_phase = "frozen"
        for phase in ("frozen", "unfreezing", "ending"):
            a.phase = phase
            got = await a._gate_cb(_pre(f"f-{phase}", "Bash", {"command": "ls"}), f"f-{phase}", None)
            assert got["hookSpecificOutput"]["permissionDecisionReason"].endswith(gate.FROZEN), phase
        assert await a._gate_cb(_pre("f2", "mcp__proofpane__task_state", {}), "f2", None) == {}
        # a claude started at work: its calls are judged at work, whatever the phase says by then
        a.connected_phase, a.phase = "work", "ending"
        assert await a._gate_cb(_pre("w1", "Bash", {"command": "ls"}), "w1", None) == {}
        return a
    _in_loop(body)


def test_a_call_asked_after_the_attempt_s_end_was_decided_is_refused(tmp_path):
    # an end reaches the claude by an interrupt, and a call it asks before that is refused
    # at the gate (nothing after the end runs in that attempt), whatever the call
    live = _Live(tmp_path)

    async def body():
        a = await live.up()
        _at_work(a)
        assert await a._gate_cb(_pre("b1", "Bash", {"command": "ls"}), "b1", None) == {}
        a.in_flight.discard("b1")
        for end in (("blocked", "watch", "the watched places changed while the attempt worked"), ("done", None, None)):
            a.ending = end
            for n, (tool, given) in enumerate((("Bash", {"command": "ls"}), ("mcp__proofpane__task_state", {}))):
                got = await a._gate_cb(_pre(f"b{n}-{end[0]}", tool, given), f"b{n}-{end[0]}", None)
                assert got["hookSpecificOutput"]["permissionDecisionReason"] == task_attempt.ENDED, (end, tool)
                assert a.calls[f"b{n}-{end[0]}"].denied and not a.in_flight
        return a
    _in_loop(body)


def test_an_attempt_is_not_given_wait_run_whose_answer_could_not_reach_it(tmp_path, monkeypatch):
    # an agent that may dispatch gives its runs
    # list_agents, run_agent, wait_run and send_to_run (agents.py); wait_run tells the model to end its turn and answers
    # as its next message, and at work a turn's end ends the attempt done, so an attempt is given the others alone
    import claude_agent_sdk
    from claude_agent_sdk import tool

    def own(name):
        @tool(name, "a run's own tool", {})
        async def handler(args):
            return {}
        return handler
    served = []
    monkeypatch.setattr(claude_agent_sdk, "create_sdk_mcp_server",
                        lambda name, tools: served.append([t.name for t in tools]) or {"type": "sdk", "name": name})
    live = _Live(tmp_path, tools=[own(n) for n in ("list_agents", "run_agent", "wait_run", "send_to_run")])

    async def body():
        a = await live.up()
        _at_work(a)
        allowed = a._options(a.chat)["allowed_tools"]
        assert served[-1] == ["task_state", "resume_ack", "save_progress", "complete_step", "ask_owner",
                              "ask_grant", "list_agents", "run_agent", "send_to_run"]
        assert "mcp__proofpane__wait_run" not in allowed
        assert {"mcp__proofpane__list_agents", "mcp__proofpane__run_agent",
                "mcp__proofpane__send_to_run"} <= set(allowed)
    _in_loop(body)


def test_a_stream_message_or_a_post_callback_that_cannot_be_read_ends_it_ungated(tmp_path, monkeypatch):
    # what the ungated rules and the start's check read is never dropped quietly
    from claude_agent_sdk import SystemMessage
    live = _Live(tmp_path)

    async def body():
        a = await live.up()
        _at_work(a)
        a._message(SystemMessage(subtype="init", data={"apiKeySource": "none"}))     # read: nothing ends
        assert a.ending is None

        def broken(data):
            raise KeyError("an init the reader did not expect")
        monkeypatch.setattr(a, "_init", broken)
        a._message(SystemMessage(subtype="init", data={"apiKeySource": "none"}))
        assert a.ending == ("blocked", task_attempt.UNGATED, "a message of the stream could not be read")
        return a
    _in_loop(body)
    monkeypatch.undo()
    live = _Live(tmp_path)

    async def body2():
        a = await live.up()
        _at_work(a)

        def broken(call_id):
            raise RuntimeError("a call the reader did not expect")
        monkeypatch.setattr(a, "_call", broken)
        assert await a._post_cb({"tool_name": "Bash", "tool_input": {"command": "ls"}}, "p1", None) == {}
        assert a.ending == ("blocked", task_attempt.UNGATED, "a call's post callback could not be read")
    _in_loop(body2)


def test_what_cannot_be_read_names_its_calls_and_ends_it_ungated_after_any_end_but_stop(tmp_path):
    # a message or a post callback this cannot read records the calls it names as
    # unknown effects, for the owner to resolve before Continue, and ends the attempt ungated in place of an earlier
    # end, while the workspace closes the channel too; after Stop the end stays Stop's and the calls are recorded
    from claude_agent_sdk import AssistantMessage, SystemMessage, ToolUseBlock
    ungated = ("blocked", task_attempt.UNGATED, "a message of the stream could not be read")

    class NoMessageId(AssistantMessage):
        # an SDK whose assistant message has no message_id
        def __getattribute__(self, name):
            if name == "message_id":
                raise AttributeError(name)
            return super().__getattribute__(name)

    def message(call_id):
        return NoMessageId(content=[ToolUseBlock(id=call_id, name="Bash", input={"command": "rm -rf x"})], model="m",
                           message_id="msg-1")

    def unseen(a):
        return [(e["call_id"], e["tool"], e["target"], e["unseen"], e["settled"]) for e in a.view["task"]["effects"]]

    async def body():
        a = await _Live(tmp_path).up()
        _at_work(a)
        a._message(message("u1"))
        assert a.ending == ungated
        assert unseen(a) == [("u1", "Bash", "rm", True, "unknown")]
        # after the end the step's checkpoint decided, a post callback it cannot read
        b = await _Live(tmp_path).up()
        _at_work(b)
        b._end("done", None, None)

        class Stuck(set):
            def discard(self, item):
                raise RuntimeError("a set the reader did not expect")
        b.in_flight = Stuck()
        assert await b._post_cb({"tool_name": "Bash", "tool_input": {"command": "rm -rf x"}}, "p2", None) == {}
        assert b.ending == ("blocked", task_attempt.UNGATED, "a call's post callback could not be read")
        assert unseen(b) == [("p2", "Bash", "rm", True, "unknown")]
        # while the workspace closes the channel (the frozen restart's stop, or the end's)
        c = await _Live(tmp_path).up()
        _at_work(c)
        c.closing = True
        c._message(message("u3"))
        assert c.ending == ungated and unseen(c) == [("u3", "Bash", "rm", True, "unknown")]
        # after Stop
        d = await _Live(tmp_path).up()
        _at_work(d)
        d._end("stopped", None, "the owner stopped it")
        d._message(message("u4"))
        assert d.ending == ("stopped", None, "the owner stopped it")
        assert unseen(d) == [("u4", "Bash", "rm", True, "unknown")]

        # a message that names no call (a start's): the same after an end, and while the channel closes
        def broken(data):
            raise KeyError("an init the reader did not expect")
        for at in ("done", "closing"):
            e = await _Live(tmp_path).up()
            _at_work(e)
            if at == "done":
                e._end("done", None, None)
            else:
                e.closing = True
            e._init = broken
            e._message(SystemMessage(subtype="init", data={"apiKeySource": "none"}))
            assert e.ending == ungated and e.view["task"]["effects"] == [], at
    _in_loop(body)


def test_the_intent_is_written_durably_before_the_call_and_names_its_push(tmp_path):
    # the intent holds the address, the ref, the commit and the ref's value before, read from the
    # remote: the work repository has an origin, and the remote the task's branch
    remote = str(tmp_path / "remote.git")
    git("init", "-q", "--bare", "-b", "main", remote, cwd=str(tmp_path))
    work = str(tmp_path / "work")
    make_repo(work, origin=remote)
    before = git("rev-parse", "HEAD", cwd=work)
    git("push", "-q", remote, f"HEAD:refs/heads/pp/task-{TASK}", cwd=work)
    git("checkout", "-q", "-b", "pp/task-" + TASK, cwd=work)
    commit = commit_in(work, "work.txt", "x\n")
    live = _Live(tmp_path)

    async def body():
        a = await live.up()
        _at_work(a)
        got = await a._intent_cb(_pre("t1", "Bash", {"command": "git push origin pp/task-" + TASK}), "t1", None)
        assert got == {}                            # no permission decision: the agent's rules still ask
        (effect,) = a.view["task"]["effects"]
        assert effect["kind"] == "git_remote"
        assert effect["intent"] == {"address": remote, "ref": "refs/heads/pp/task-" + TASK, "commit": commit,
                                    "before": before}
        assert live.records[-1][1] is True and live.records[-1][0]["task"]["effects"][0]["call_id"] == "t1"
        # an address with a user and a token in it is recorded without them, and a remote it cannot read is unread
        # (the ssh command here fails at once: nothing reaches a network)
        git("remote", "add", "tok", "ssh://x-access-token:s3cr3t@example.invalid/o/n.git", cwd=work)
        git("config", "core.sshCommand", "false", cwd=work)
        assert await a._intent_cb(_pre("t2", "Bash", {"command": "git push tok pp/task-" + TASK}), "t2", None) == {}
        assert a.effects["t2"]["intent"] == {"address": "ssh://example.invalid/o/n.git",
                                             "ref": "refs/heads/pp/task-" + TASK, "commit": commit,
                                             "before": "unread"}
        assert "s3cr3t" not in json.dumps([r for r, _ in live.records])
    _in_loop(body)


def test_a_push_of_head_names_the_branch_head_is_on_and_no_ref_where_head_is_detached(tmp_path):
    # `git push <remote> HEAD` (or @) pushes the branch HEAD is on, by that branch's
    # name, and git refuses it where HEAD is detached; an intent that named the task's branch whatever branch HEAD was
    # on would have such a push in flight at a machine stop settled against a ref it never touched
    remote = str(tmp_path / "remote.git")
    git("init", "-q", "--bare", "-b", "main", remote, cwd=str(tmp_path))
    work = str(tmp_path / "work")
    make_repo(work, origin=remote)
    before = git("rev-parse", "HEAD", cwd=work)
    git("push", "-q", remote, "HEAD:refs/heads/other", cwd=work)
    git("checkout", "-q", "-b", "other", cwd=work)
    commit = commit_in(work, "work.txt", "x\n")
    live = _Live(tmp_path)

    async def body():
        a = await live.up()
        _at_work(a)
        for n, command in enumerate(("git push origin HEAD", "git push origin @")):
            await a._intent_cb(_pre(f"h{n}", "Bash", {"command": command}), f"h{n}", None)
            assert a.effects[f"h{n}"]["intent"] == {"address": remote, "ref": "refs/heads/other", "commit": commit,
                                                    "before": before}, command
        # a destination written as HEAD is a branch of that name on the remote, as git 2.43 makes it
        await a._intent_cb(_pre("h2", "Bash", {"command": "git push origin HEAD:HEAD"}), "h2", None)
        assert a.effects["h2"]["intent"]["ref"] == "refs/heads/HEAD"
        git("checkout", "-q", "--detach", cwd=work)
        await a._intent_cb(_pre("h3", "Bash", {"command": "git push origin HEAD"}), "h3", None)
        assert a.effects["h3"]["intent"] == {"address": remote, "ref": None, "commit": commit, "before": "unread",
                                             "detached": True}
        # settled by the branch it named: done once the remote's branch is at the commit; with no ref, not done, git
        # refusing such a push every time, in flight at a machine stop too; with
        # git's places changed, where no git runs, unknown as any push is
        git("push", "-q", remote, f"{commit}:refs/heads/other", cwd=work)
        effects = [dict(a.effects["h0"], end="error"), dict(a.effects["h3"], end="error"), dict(a.effects["h3"])]
        task_attempt.settle_effects(effects, work=work)
        assert [e["settled"] for e in effects] == ["done", "not_done", "not_done"]
        effects = [dict(a.effects["h3"], end="error"), dict(a.effects["h3"])]
        task_attempt.settle_effects(effects, work=work, git_ok=False)
        assert [e["settled"] for e in effects] == ["unknown", "unknown"]
        # the control: git refuses it, as the effect says, but only after it ran the remote's receive-pack, which a
        # changed place can name (a program of the attempt's choosing, run before the refusal)
        marker = tmp_path / "receive-pack-ran"
        planted = tmp_path / "receive-pack"
        planted.write_text(f"#!/bin/sh\necho ran >> {shlex.quote(str(marker))}\nexec git receive-pack \"$@\"\n")
        planted.chmod(0o755)
        refused = subprocess.run(["git", "-c", f"remote.origin.receivepack={planted}", "push", "origin", "HEAD"],
                                 cwd=work, capture_output=True)
        assert refused.returncode != 0 and marker.read_text() == "ran\n", refused
    _in_loop(body)


def test_a_call_run_outside_the_gate_ends_the_attempt_ungated(tmp_path):
    cases = {
        # its post callback without its PreToolUse callbacks
        "nopre": lambda a: a._post_cb({"tool_name": "Bash", "tool_input": {"command": "ls"}}, "c1", None),
    }

    async def nopre():
        a = await live.up()
        _at_work(a)
        a.turn_ups = True
        _turn(a, ("c1", "Bash", {"command": "rm -rf build"}))
        await cases["nopre"](a)
        assert a.ending[0:2] == ("blocked", task_attempt.UNGATED)
        assert _unseen(a) == {"c1": ("Bash", "rm")}

    live = _Live(tmp_path)
    _in_loop(nopre)

    async def lost_deny():
        a = await live.up()
        _at_work(a)
        a.turn_ups = True
        secret = {"file_path": os.path.join(str(tmp_path), ".claude", ".credentials.json")}
        _turn(a, ("c2", "Read", secret))
        denied = await a._gate_cb(_pre("c2", "Read", secret), "c2", None)
        assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
        await a._intent_cb(_pre("c2", "Read", secret), "c2", None)
        await a._post_cb({"tool_name": "Read", "tool_input": secret}, "c2", None)      # it ran anyway
        assert a.ending[1] == task_attempt.UNGATED and "c2" in _unseen(a)

    live = _Live(tmp_path)
    _in_loop(lost_deny)

    async def lost_deny_no_post():
        # the gate's refusal lost on its way back, the call run with no post callback: its result in the stream, not
        # an error, ends the attempt ungated, as for a cancelled intent callback (_result's rule), and an error result
        # ends nothing
        a = await live.up()
        _at_work(a)
        a.turn_ups = True
        secret = {"file_path": os.path.join(str(tmp_path), ".claude", ".credentials.json")}
        _turn(a, ("c6", "Read", secret), ("c7", "Read", secret))
        for call_id in ("c6", "c7"):
            denied = await a._gate_cb(_pre(call_id, "Read", secret), call_id, None)
            assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
            await a._intent_cb(_pre(call_id, "Read", secret), call_id, None)
        _result(a, "c7", error=True)                # refused, as Claude Code took it: nothing
        assert a.ending is None
        _result(a, "c6")                            # it ran anyway
        assert a.ending[1] == task_attempt.UNGATED and "c6" in _unseen(a) and "c7" not in _unseen(a)

    live = _Live(tmp_path)
    _in_loop(lost_deny_no_post)

    async def no_callback():
        a = await live.up()
        _at_work(a)
        a.turn_ups = True
        _turn(a, ("c0", "Read", {"file_path": "a"}))
        await a._gate_cb(_pre("c0", "Read", {"file_path": "a"}), "c0", None)
        _turn(a, ("c3", "Bash", {"command": "curl https://example.org"}), message_id="msg-2")
        _result(a, "c3", error=True)                # refused before its hooks: not counted
        assert a.ending is None
        _turn(a, ("c4", "EndConversation", {}), message_id="msg-3")
        _result(a, "c4")                            # exempt from hooks: not counted
        assert a.ending is None
        _turn(a, ("c5", "Bash", {"command": "curl https://example.org"}), message_id="msg-4")
        _result(a, "c5")                            # ran, and no callback saw it
        assert a.ending[1] == task_attempt.UNGATED and _unseen(a) == {"c5": ("Bash", "curl")}

    live = _Live(tmp_path)
    _in_loop(no_callback)


@pytest.mark.parametrize("seen_by", ["post", "result"])
def test_a_call_whose_intent_callback_was_cancelled_and_which_then_ran_ends_it_ungated(tmp_path, monkeypatch,
                                                                                          seen_by):
    # Claude Code gives up on a PreToolUse callback at its timeout and cancels it
    # (a control_cancel_request, which the SDK turns into a cancel of the callback's task). The intent callback,
    # cancelled inside its to_thread, takes its call as refused and raises the cancel again, so a call that runs after
    # that is a call run after a deny: ungated at its post callback, or, where none comes, at its result in the stream
    # that is not an error
    import asyncio
    import threading
    started, release = threading.Event(), threading.Event()

    def slow_intent(self, got):                     # the intent's own git, still reading when the cancel comes
        started.set()
        release.wait(10)
        return {}
    monkeypatch.setattr(task_attempt.Attempt, "_intent", slow_intent)
    live = _Live(tmp_path)

    async def body():
        a = await live.up()
        _at_work(a)
        a.turn_ups = True
        push = {"command": "git push origin pp/task-" + TASK}
        _turn(a, ("c1", "Bash", push))
        assert await a._gate_cb(_pre("c1", "Bash", push), "c1", None) == {}
        task = asyncio.ensure_future(a._intent_cb(_pre("c1", "Bash", push), "c1", None))
        while not started.is_set():
            await asyncio.sleep(0.01)
        task.cancel()                               # as the SDK does on Claude Code's control_cancel_request
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        assert a.calls["c1"].denied and "c1" not in a.in_flight and a.ending is None
        if seen_by == "post":
            await a._post_cb({"tool_name": "Bash", "tool_input": push}, "c1", None)       # it ran all the same
        else:
            _result(a, "c1")                        # its result says it ran, and no post callback came
        assert a.ending[0:2] == ("blocked", task_attempt.UNGATED)
        assert _unseen(a) == {"c1": ("Bash", "git push")}
        # the control: a refused call's error result ends nothing
        _turn(a, ("c2", "Bash", push), message_id="msg-2")
        a.ending = None
        a.calls["c2"].gate = a.calls["c2"].denied = True
        _result(a, "c2", error=True)
        assert a.ending is None
    _in_loop(body)


@pytest.mark.parametrize("then", ["refused", "ran"])
def test_an_intent_callback_cancelled_in_its_flush_takes_its_call_s_effect_out_again(tmp_path, monkeypatch, then):
    # the cancel lands in the intent's durable flush, after the call's effect was
    # registered. The effect is taken out of the ledger again, so that a call Claude Code then refuses leaves nothing
    # for the attempt's end to settle (it was held as unknown, for the owner to resolve a call that never ran), and one
    # it runs anyway is an unseen call, its intent {}. The flush goes on, as its thread would anyway,
    # and the next record is written after it, without the effect
    import asyncio
    import threading
    intent = {"address": "a", "ref": "refs/heads/pp/task-" + TASK, "commit": None, "before": "unread"}
    monkeypatch.setattr(task_attempt.Attempt, "_intent", lambda self, got: dict(intent))
    flushing, release = threading.Event(), threading.Event()
    live = _Live(tmp_path)
    kept = live.a.emit

    def slow(record, durable):                      # the durable write of the record that holds the effect, held
        if durable and not release.is_set() and any(e["call_id"] == "c1" for e in record["task"]["effects"]):
            flushing.set()
            release.wait(10)
        kept(record, durable)
    live.a.emit = slow

    async def body():
        a = await live.up()
        _at_work(a)
        a.turn_ups = True
        push = {"command": "git push origin pp/task-" + TASK}
        _turn(a, ("c1", "Bash", push))
        assert await a._gate_cb(_pre("c1", "Bash", push), "c1", None) == {}
        task = asyncio.ensure_future(a._intent_cb(_pre("c1", "Bash", push), "c1", None))
        while not flushing.is_set():
            await asyncio.sleep(0.01)
        assert a.effects["c1"]["intent"] == intent      # registered, its flush under way
        task.cancel()                               # as the SDK does on Claude Code's control_cancel_request
        with pytest.raises(asyncio.CancelledError):
            await task
        assert a.calls["c1"].denied and "c1" not in a.in_flight
        assert "c1" not in a.effects and all(e["call_id"] != "c1" for e in a.view["task"]["effects"])
        written = asyncio.ensure_future(a._emit(durable=True))
        await asyncio.sleep(0.2)
        assert not written.done()                   # it waits for the flush, still going
        release.set()
        await written
        durable = [r for r, d in live.records if d]
        assert any(e["call_id"] == "c1" for e in durable[-2]["task"]["effects"])      # the flush, written first
        assert all(e["call_id"] != "c1" for e in durable[-1]["task"]["effects"])      # then the record without it
        if then == "refused":
            _result(a, "c1", error=True)            # Claude Code took the cancel as a refusal: nothing ran
            assert a.ending is None
            task_attempt.settle_effects(a.view["task"]["effects"], work=a.spec["work"])
            assert all(e["call_id"] != "c1" for e in a.view["task"]["effects"])
        else:
            await a._post_cb({"tool_name": "Bash", "tool_input": push}, "c1", None)       # it ran all the same
            assert a.ending[0:2] == ("blocked", task_attempt.UNGATED)
            assert [(e["intent"], e["unseen"]) for e in a.view["task"]["effects"] if e["call_id"] == "c1"] == \
                [({}, True)]
    _in_loop(body)


@pytest.mark.parametrize("before_the_cancel", ["ran", "unseen"])
def test_a_cancel_after_its_call_ran_or_proved_unseen_leaves_its_effect(tmp_path, monkeypatch, before_the_cancel):
    # the cancel takes out only an effect still open and gated. Where the call's
    # post callback came first (it ran while its intent callback had not returned), its
    # effect has its end and stays, to be settled done; where the call proved unseen first (its post callback with no
    # gate callback), its entry is the unseen call's and stays, unknown, the attempt ungated
    import asyncio
    import threading
    intent = {"address": "a", "ref": "refs/heads/pp/task-" + TASK, "commit": None, "before": "unread"}
    monkeypatch.setattr(task_attempt.Attempt, "_intent", lambda self, got: dict(intent))
    flushing, release = threading.Event(), threading.Event()
    live = _Live(tmp_path)
    kept = live.a.emit

    def slow(record, durable):                      # the durable write of the record that holds the effect, held
        if durable and not release.is_set() and any(e["call_id"] == "c1" for e in record["task"]["effects"]):
            flushing.set()
            release.wait(10)
        kept(record, durable)
    live.a.emit = slow

    async def body():
        a = await live.up()
        _at_work(a)
        a.turn_ups = True
        push = {"command": "git push origin pp/task-" + TASK}
        _turn(a, ("c1", "Bash", push))
        if before_the_cancel == "ran":
            assert await a._gate_cb(_pre("c1", "Bash", push), "c1", None) == {}
        task = asyncio.ensure_future(a._intent_cb(_pre("c1", "Bash", push), "c1", None))
        while not flushing.is_set():
            await asyncio.sleep(0.01)
        await a._post_cb({"tool_name": "Bash", "tool_input": push}, "c1", None)       # before the cancel
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        await a._emit(durable=True)
        (entry,) = [e for e in a.view["task"]["effects"] if e["call_id"] == "c1"]
        assert a.effects["c1"] is entry
        if before_the_cancel == "ran":
            assert a.ending is None and (entry["intent"], entry["end"], entry["unseen"]) == (intent, "ok", False)
            task_attempt.settle_effects(a.view["task"]["effects"], work=a.spec["work"])
            assert entry["settled"] == "done"
        else:
            assert a.ending[0:2] == ("blocked", task_attempt.UNGATED)
            assert (entry["intent"], entry["unseen"], entry["settled"]) == ({}, True, "unknown")
    _in_loop(body)


@pytest.mark.parametrize("seen_by", ["post", "result"])
def test_a_call_whose_intent_was_written_and_whose_gate_deny_was_lost_is_an_unseen_call(tmp_path, monkeypatch,
                                                                                            seen_by):
    # the cancel's taking out rests on _unseen replacing a registered
    # effect's fields. A call's intent is written first, the gate's deny of it is lost on its way back, and the call
    # runs: its one entry is then an unseen call's, its intent {}, settled unknown, and the attempt ends ungated, seen
    # by its post callback or, with none, by its result
    intent = {"address": "a", "ref": "refs/heads/pp/task-" + TASK, "commit": None, "before": "unread"}
    monkeypatch.setattr(task_attempt.Attempt, "_intent", lambda self, got: dict(intent))
    monkeypatch.setattr(task_attempt.gate, "refusal", lambda payload, home, env: "refused here")
    live = _Live(tmp_path)

    async def body():
        a = await live.up()
        _at_work(a)
        a.turn_ups = True
        push = {"command": "git push origin pp/task-" + TASK}
        _turn(a, ("c8", "Bash", push))
        assert await a._intent_cb(_pre("c8", "Bash", push), "c8", None) == {}
        assert a.effects["c8"]["intent"] == intent
        denied = await a._gate_cb(_pre("c8", "Bash", push), "c8", None)
        assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
        if seen_by == "post":
            await a._post_cb({"tool_name": "Bash", "tool_input": push}, "c8", None)       # it ran anyway
        else:
            _result(a, "c8")                        # it ran anyway, with no post callback
        assert a.ending[0:2] == ("blocked", task_attempt.UNGATED)
        assert [(e["intent"], e["unseen"], e["settled"]) for e in a.view["task"]["effects"]
                if e["call_id"] == "c8"] == [({}, True, "unknown")]
    _in_loop(body)


def test_bare_mode_ends_the_attempt_and_a_turn_with_its_submission_hooks_skipped_does_not(tmp_path):
    async def bare():
        a = await live.up()
        _at_work(a)
        _turn(a, ("b1", "Bash", {"command": "ls"}))      # no UserPromptSubmit, no PreToolUse
        _result(a, "b1", error=True)
        assert a.ending[1] == task_attempt.UNGATED and "b1" in _unseen(a)

    live = _Live(tmp_path)
    _in_loop(bare)

    async def skipped():
        # a turn with no UserPromptSubmit whose call still calls its PreToolUse callbacks, as one dispatched with its
        # submission hooks skipped would: no turn of 2.1.274 was measured coming so, a turn sent as a command having
        # come with UserPromptSubmit, so the rule this tests stays a defence
        #
        a = await live.up()
        _at_work(a)
        _turn(a, ("s1", "Bash", {"command": "ls"}))      # no UserPromptSubmit, but its PreToolUse callbacks
        await a._gate_cb(_pre("s1", "Bash", {"command": "ls"}), "s1", None)
        await a._intent_cb(_pre("s1", "Bash", {"command": "ls"}), "s1", None)
        await a._post_cb({"tool_name": "Bash", "tool_input": {"command": "ls"}}, "s1", None)
        _result(a, "s1")
        assert a.ending is None

    live = _Live(tmp_path)
    _in_loop(skipped)


def test_a_server_tool_s_block_is_not_counted_with_its_result_or_beside_an_unseen_call(tmp_path):
    from claude_agent_sdk import AssistantMessage, ServerToolResultBlock, ServerToolUseBlock, ToolUseBlock
    live = _Live(tmp_path)

    async def body():
        a = await live.up()
        _at_work(a)
        a.turn_ups = True
        # an advisor's call and its result (the server blocks the SDK gives) beside a client call, in one message
        a._message(AssistantMessage(content=[ServerToolUseBlock(id="srv1", name="advisor", input={}),
                                             ServerToolResultBlock(tool_use_id="srv1", content={"type": "advice"}),
                                             ToolUseBlock(id="c1", name="Bash", input={"command": "ls"})],
                                    model="m", message_id="msg-1"))
        assert a.ending is None and a.view["task"]["effects"] == []
        _result(a, "c1")                            # the client call ran, and no callback saw it
        a._ungated_batches()
        assert a.ending[1] == task_attempt.UNGATED and _unseen(a) == {"c1": ("Bash", "ls")}
    _in_loop(body)


def test_after_the_interrupt_the_batch_s_calls_no_pretooluse_callback_saw_are_unknown(tmp_path):
    async def body():
        a = await live.up()
        _at_work(a)
        a.turn_ups = True
        _turn(a, ("x1", "Bash", {"command": "echo a"}), ("x2", "Bash", {"command": "git push origin main"}),
              ("x3", "Write", {"file_path": "f", "content": "c"}), message_id="batch-1")
        await a._post_cb({"tool_name": "Bash", "tool_input": {"command": "echo a"}}, "x1", None)
        a._ungated_batches()
        assert a.ending[1] == task_attempt.UNGATED
        assert _unseen(a) == {"x1": ("Bash", "echo"), "x2": ("Bash", "git push"), "x3": ("Write", "Write")}
        assert all(e["settled"] == "unknown" and e["intent"] == {} for e in a.view["task"]["effects"])

    live = _Live(tmp_path)
    _in_loop(body)


def test_the_batch_rule_takes_each_other_call_no_pretooluse_callback_saw_before_the_ungated_one_too(tmp_path):
    # the rule takes every other call of
    # the batch that no PreToolUse callback saw, one before the ungated call among them, and leaves a call that either
    # PreToolUse callback saw, and EndConversation, which no hook sees and which reaches nothing outside the turn
    async def body():
        a = await live.up()
        _at_work(a)
        a.turn_ups = True
        _turn(a, *((f"y{n}", "Bash", {"command": f"echo {n}"}) for n in range(1, 6)), ("y6", "EndConversation", {}),
              message_id="batch-2")
        await a._gate_cb(_pre("y3", "Bash", {"command": "echo 3"}), "y3", None)        # the gate's callback alone
        await a._intent_cb(_pre("y4", "Bash", {"command": "echo 4"}), "y4", None)      # the intent's alone
        await a._post_cb({"tool_name": "Bash", "tool_input": {"command": "echo 2"}}, "y2", None)
        a._ungated_batches()
        assert a.ending[1] == task_attempt.UNGATED
        assert set(_unseen(a)) == {"y1", "y2", "y5"}

    live = _Live(tmp_path)
    _in_loop(body)


def test_the_control_channel_closed_by_claude_is_ungated_and_stop_s_close_is_not(tmp_path):
    async def body():
        a = await live.up()
        _at_work(a)
        a.closing = True
        a._closed(None, True)                       # the workspace's own close
        assert a.ending is None
        a.closing = False
        a._closed(None, False)                      # the chat stopping its claude
        assert a.ending is None
        a._closed(None, True)                       # claude's channel closed under it
        assert a.ending[1] == task_attempt.UNGATED

    live = _Live(tmp_path)
    _in_loop(body)

    async def frozen():
        # the frozen claude's own close before it acknowledged, an exit or not: blocked by the resume check, cause
        # resume, check ack, found {closed: true}, never ungated; a close after that changes nothing
        a = await live.up()
        a.phase = "frozen"
        a._closed(None, True)
        assert a.ending[0:2] == ("blocked", "resume") and a.view["task"]["resume"]["check"] == "ack"
        assert a.view["task"]["resume"]["found"] == {"closed": True}
        assert a.view["task"]["resume"]["found"]["closed"] is True     # JSON's true
        decided = a.ending
        a._closed(None, True)
        assert a.ending == decided

    live = _Live(tmp_path)
    _in_loop(frozen)

    async def after_an_end():
        # at work, a close after an end was decided (a step's checkpoint, a refusal, a watch change) changes nothing:
        # the end decided first stands, where a close alone would end the attempt ungated
        a = await live.up()
        _at_work(a)
        a._end("done", None, "the step's checkpoint was taken")
        a._closed(None, True)
        assert a.ending == ("done", None, "the step's checkpoint was taken")

    live = _Live(tmp_path)
    _in_loop(after_an_end)

    async def stopped():
        import asyncio
        a = await live.up()
        _at_work(a)
        a.stop(None, "stopped by you")
        await asyncio.sleep(0)
        assert a.ending == ("stopped", None, "stopped by you")
        a._closed(None, True)                       # the close Stop's own end brings
        assert a.ending == ("stopped", None, "stopped by you")
        a.turn_ups = True
        _turn(a, ("y1", "Bash", {"command": "ls"}))
        _result(a, "y1")                            # a call no callback saw: an unknown effect, the end Stop's
        assert a.ending[0] == "stopped" and "y1" in _unseen(a)
        a.stop(task_attempt.UNGATED, "a stop named so is still a stop")
        await asyncio.sleep(0)
        assert a.ending[0] == "stopped"

    live = _Live(tmp_path)
    _in_loop(stopped)


def test_each_field_of_the_acknowledgement_is_checked_and_a_second_miss_ends_it(tmp_path):
    right = {"nonce": "a-nonce-of-the-app-s", "task": TASK, "step": 1, "checkpoint": None, "done_steps": [],
             "done_effects": [], "next_step": 1}
    wrong = {"nonce": "x", "task": "x", "step": 101, "checkpoint": "x", "done_steps": [999], "done_effects": ["x"],
             "next_step": 101}
    for field in task_attempt.ACK_FIELDS:
        live = _Live(tmp_path)

        async def body(field=field):
            a = await live.up()
            a.phase = "frozen"
            got = await a._resume_ack(dict(right, **{field: wrong[field]}))
            assert got.get("is_error") and f"The field {field} does not match" in got["content"][0]["text"], field
            assert a.ending is None and not a.acked
            got = await a._resume_ack(right)
            assert not got.get("is_error") and a.acked
            assert a.view["task"]["resume"] == {"outcome": "passed", "check": None, "found": None, "acks": 2}
        _in_loop(body)
    live = _Live(tmp_path)

    async def twice():
        a = await live.up()
        a.phase = "frozen"
        await a._resume_ack(dict(right, nonce="x"))
        await a._resume_ack(dict(right, step=7))
        assert a.ending[0] == "blocked" and a.view["task"]["resume"]["check"] == "ack"
        assert a.view["task"]["resume"]["found"] == {"field": "step", "tries": 2}
    _in_loop(twice)
    live = _Live(tmp_path)

    async def bool_for_int():
        a = await live.up()
        a.phase = "frozen"
        got = await a._resume_ack(dict(right, step=True))    # True == 1 in Python: not the step
        assert got.get("is_error")
    _in_loop(bool_for_int)


def test_no_acknowledgement_within_its_turns_ends_it_and_nothing_is_sent_after(tmp_path):
    live = _Live(tmp_path)
    sent = []

    async def body():
        a = await live.up()
        a.phase = "frozen"

        async def send(text):
            sent.append(text)
        a._send = send
        for _ in range(task_shapes.ACK_TURNS - 1):
            assert a._turn_end({"subtype": "success"}, False) == "hold"
            assert a.ending is None
        assert a._turn_end({"subtype": "success"}, False) == "hold"
        assert a.ending[0] == "blocked" and a.view["task"]["resume"]["found"] == {"turns": task_shapes.ACK_TURNS}
        import asyncio
        await asyncio.sleep(0)
    _in_loop(body)
    assert sent == [task_attempt.ACK_AGAIN] * (task_shapes.ACK_TURNS - 1)


def test_no_acknowledgement_within_its_time_ends_it(tmp_path, monkeypatch):
    monkeypatch.setattr(task_shapes, "ACK_SECONDS", 0.2)
    live = _Live(tmp_path)

    async def body():
        import asyncio
        a = await live.up()

        async def passed(*args):
            return True
        a._checks = a._start = a._say = passed      # the frozen start up, its first message sent, nothing acked
        await asyncio.wait_for(a._course(), 10)
        assert a.ending[0] == "blocked" and a.view["task"]["resume"]["check"] == "ack"
        assert a.view["task"]["resume"]["found"] == {"seconds": 0.2}
        for timer in a._timers:
            timer.cancel()
    _in_loop(body)


def test_a_message_while_frozen_is_kept_and_given_after_the_restart(tmp_path):
    live = _Live(tmp_path)

    async def body():
        import asyncio
        a = await live.up()
        a.phase = "frozen"
        a.message("from another run")
        await asyncio.sleep(0)
        assert a.kept == ["from another run"]
    _in_loop(body)


def test_task_state_serves_the_packet_only_as_the_app_sent_it(tmp_path):
    live = _Live(tmp_path)
    path = os.path.join(live.a.spec["records"], "packet.json")

    async def body():
        a = await live.up()
        task_attempt._write_new(path, a.packet_bytes)
        got = await a._task_state()
        assert got["content"][0]["text"] == a.packet_bytes.decode() and a.ending is None
        task_attempt._write_new(path, a.packet_bytes.replace(b"a-nonce", b"b-nonce"))
        got = await a._task_state()
        assert got.get("is_error") and a.ending[0] == "blocked"
        assert a.view["task"]["resume"]["check"] == "packet"
    _in_loop(body)
    live = _Live(tmp_path)

    async def link():
        a = await live.up()
        elsewhere = os.path.join(str(tmp_path), "elsewhere.json")
        with open(elsewhere, "wb") as fh:
            fh.write(a.packet_bytes)
        os.unlink(path)
        os.symlink(elsewhere, path)
        got = await a._task_state()                 # the same bytes, through a link: not followed
        assert got.get("is_error") and a.view["task"]["resume"]["check"] == "packet"
    _in_loop(link)


def test_the_first_message_carries_no_packet_id_no_nonce_and_no_model_written_text(tmp_path):
    live = _Live(tmp_path, brief="The owner's brief.", objective="Step one, the owner's words.")
    first = live.a._first_message()
    assert first.startswith("The owner's brief.\n\nThe current step: Step one, the owner's words.")
    assert "a-nonce-of-the-app-s" not in first and TASK not in first and RUN not in first
    assert len(first.encode()) < 32 * 1024


def test_the_first_message_fits_the_chat_s_send_with_the_longest_brief_and_a_long_objective(tmp_path):
    # an attempt message is under 32 KB with the longest brief; the chat's send refuses more, and the
    # objective is bounded only with the rest of the owner's instructions, so the longest brief beside a long objective
    # would make a message the send refused, and the attempt fail to start: the objective is cut to fit, and marked
    from proofpane_workspace import agents as agentry, chat as chats
    brief = chr(0x10348) * agentry.MAX_BRIEF               # four bytes each in UTF-8
    assert agentry.check_agent({"id": AGENT_ID, "name": "n", "brief": brief})["brief"] == brief
    for objective in ("o" * 24_000, chr(0x10348) * 6_000):
        first = _Live(tmp_path, brief=brief, objective=objective).a._first_message()
        assert len(first.encode()) <= chats.MAX_SEND
        assert first.startswith(brief + "\n\nThe current step: " + objective[:1000])
        assert task_attempt.OBJECTIVE_CUT + "\n\n" + task_attempt.FIRST in first
    # an objective that fits is whole and not marked
    first = _Live(tmp_path, brief=brief, objective="Step one.").a._first_message()
    assert "The current step: Step one.\n\n" in first and task_attempt.OBJECTIVE_CUT not in first


def test_rate_limits_end_the_attempt_as_the_window_says(tmp_path):
    import time as clock
    later = int(clock.time()) + 3600

    def end_of(info, **over):
        live = _Live(tmp_path, **over)

        async def body():
            a = await live.up()
            _at_work(a)
            a._rate_limit(info)
            return a
        return _in_loop(body)
    a = end_of({"status": "rejected", "rate_limit_type": "five_hour", "resets_at": later, "overage_status": "allowed"})
    assert a.ending == ("blocked", "rate_limit", task_attempt.PAID) and a.view["task"]["paid_usage"] is True
    assert a.view["task"]["overage"]["status"] == "allowed"
    a = end_of({"status": "allowed", "rate_limit_type": "five_hour", "is_using_overage": True})
    assert a.ending[2] == task_attempt.PAID
    a = end_of({"status": "rejected", "rate_limit_type": "five_hour", "resets_at": later,
                "overage_status": "rejected", "overage_disabled_reason": "org_level_disabled"})
    assert a.ending[1] == "rate_limit" and a.ending[2].startswith("plan window exhausted")
    assert a.view["rate_limits"]["five_hour"]["overage_disabled_reason"] == "org_level_disabled"
    assert set(a.view["rate_limits"]["five_hour"]) >= {"at", "overage_status", "overage_resets_at",
                                                       "overage_disabled_reason"}
    # an unattended attempt hearing a weekly window near its limit ends before its work; a press, or the reserve
    # lifted, goes on; a five-hour warning is not the weekly reserve
    warning = {"status": "allowed_warning", "rate_limit_type": "seven_day_opus", "resets_at": later}
    assert end_of(warning, unattended=True).ending[2].startswith("weekly reserve")
    assert end_of(warning, unattended=False).ending is None
    assert end_of(warning, unattended=True, reserve_lifted=True).ending is None
    assert end_of(dict(warning, rate_limit_type="five_hour"), unattended=True).ending is None


def test_a_start_message_whose_credential_source_is_not_the_backend_s_ends_it(tmp_path):
    live = _Live(tmp_path)

    async def body():
        a = await live.up()
        a.phase = "frozen"
        a._init({"apiKeySource": "none", "model": "claude-x"})
        assert a.ending is None and a.view["task"]["model"] == "claude-x"
        a.start_checked = False
        a._init({"apiKeySource": "ANTHROPIC_API_KEY"})
        assert a.ending[0] == "blocked" and a.view["task"]["start"]["failure"] == "same_everywhere"
    _in_loop(body)
    live = _Live(tmp_path)

    async def restart():
        a = await live.up()
        _at_work(a)
        a._init({})                                 # no credential source at the restart: the settings'
        assert a.view["task"]["start"]["failure"] == "settings" and a.view["task"]["resume"]["check"] == "settings"
    _in_loop(restart)


def _checkpointing(tmp_path, **over):
    live = _Live(tmp_path, **over)

    async def up():
        a = await live.up()
        _at_work(a)
        a.start_look = task_attempt.look([])
        a.start_head = git("rev-parse", "HEAD", cwd=a.spec["work"])
        git("checkout", "-q", "-b", "pp/task-" + TASK, cwd=a.spec["work"])
        return a
    return live, up


def test_a_step_s_check_runs_in_a_fresh_checkout_and_its_protected_paths_are_checked(tmp_path):
    live, up = _checkpointing(tmp_path, check="test -f planted.txt && test -f work.txt", protected=["tests/**"])

    async def body():
        a = await up()
        work = a.spec["work"]
        with open(os.path.join(work, ".gitignore"), "w") as fh:
            fh.write("planted.txt\n")
        with open(os.path.join(work, "planted.txt"), "w") as fh:
            fh.write("would pass the check here\n")
        with open(os.path.join(work, "work.txt"), "w") as fh:
            fh.write("the work\n")
        got = await a._checkpoint("step", {"note": "done"})
        assert got.get("is_error") and "its check exited 1" in got["content"][0]["text"]
        (entry,) = a.view["task"]["checkpoints"]
        assert entry["kind"] == "progress" and entry["state"] == "complete" and entry["check"] == 1
        # the branch's commit says progress, so a hand-off reads no step done from it
        assert git("log", "-1", "--format=%s", cwd=work).endswith("checkpoint 1 (progress)")
        assert task_runs._steps_done(work, a.start_head, entry["head"]) == []
        # its pending entry was written, durably, before its commit, and completed after
        states = [(r["task"]["checkpoints"][-1]["state"], durable) for r, durable in live.records
                  if r["task"]["checkpoints"]]
        assert states[0] == ("pending", True) and states[-1] == ("complete", True)
        assert not a.completed and a.ending is None
        # a passing check, but a protected path touched: not done either
        a.spec["check"] = "test -f work.txt"
        os.makedirs(os.path.join(work, "tests"))
        with open(os.path.join(work, "tests", "t.py"), "w") as fh:
            fh.write("x\n")
        got = await a._checkpoint("step", {"note": "done"})
        assert got.get("is_error") and "protected" in got["content"][0]["text"]
        assert a.view["task"]["checkpoints"][-1]["protected"] == ["tests/t.py"]
        assert not a.completed
        # the commit carries its trailers, and the branch moved to it
        head = git("rev-parse", "HEAD", cwd=work)
        assert head == a.view["task"]["checkpoints"][-1]["head"]
        body_text = git("log", "-1", "--format=%B", cwd=work)
        assert f"Proofpane-Run: {RUN}" in body_text and "Proofpane-Checkpoint: 2" in body_text
        assert body_text.startswith(f"Task {TASK}: step 1, checkpoint 2 (progress)")
        assert task_runs._steps_done(work, a.start_head, head) == []
    _in_loop(body)


def test_a_checkpoint_descends_from_the_task_s_last_checkpoint_not_only_from_where_the_attempt_began(tmp_path):
    # An attempt that saved a checkpoint, then moved its branch back past it to where the attempt began, is refused its
    # next checkpoint, which would have taken the first one off the task's branch; from the first one, the next is taken
    live, up = _checkpointing(tmp_path)

    async def body():
        a = await up()
        work = a.spec["work"]
        with open(os.path.join(work, "one.txt"), "w") as fh:
            fh.write("one\n")
        got = await a._checkpoint("progress", {"note": "one"})
        assert not got.get("is_error"), got
        first = a.view["task"]["checkpoints"][-1]["head"]
        git("reset", "-q", "--hard", a.start_head, cwd=work)
        with open(os.path.join(work, "two.txt"), "w") as fh:
            fh.write("two\n")
        got = await a._checkpoint("progress", {"note": "two"})
        assert got.get("is_error") and "does not descend from this attempt's last checkpoint" in \
            got["content"][0]["text"]
        assert [e["state"] for e in a.view["task"]["checkpoints"]] == ["complete", "dropped"]
        assert git("rev-parse", "pp/task-" + TASK, cwd=work) == a.start_head
        git("reset", "-q", "--hard", first, cwd=work)
        got = await a._checkpoint("progress", {"note": "three"})
        assert not got.get("is_error"), got
        third = a.view["task"]["checkpoints"][-1]
        assert third["state"] == "complete" and third["parent"] == first
        assert git("rev-parse", "pp/task-" + TASK, cwd=work) == third["head"]
        # the last checkpoint is the newest: back at the first, the next is refused again
        git("reset", "-q", "--hard", first, cwd=work)
        got = await a._checkpoint("progress", {"note": "four"})
        assert got.get("is_error") and "does not descend from this attempt's last checkpoint" in \
            got["content"][0]["text"]
        assert git("rev-parse", "pp/task-" + TASK, cwd=work) == first
    _in_loop(body)


def test_a_checkpoint_s_refusal_names_where_the_attempt_began_when_it_has_no_checkpoint_of_its_own(tmp_path):
    # with no checkpoint of its own, the head a checkpoint must descend from is where the attempt began, the head the
    # task recorded for it (which may be an earlier attempt's end state), and the refusal names that, not the task's
    # last checkpoint
    live, up = _checkpointing(tmp_path)

    async def body():
        a = await up()
        work = a.spec["work"]
        git("checkout", "-q", "--orphan", "elsewhere", cwd=work)        # a history of its own on the task's branch
        commit_in(work, "other.txt", "x\n")
        git("branch", "-f", "pp/task-" + TASK, "elsewhere", cwd=work)
        git("checkout", "-q", "pp/task-" + TASK, cwd=work)
        with open(os.path.join(work, "zero.txt"), "w") as fh:
            fh.write("zero\n")
        got = await a._checkpoint("progress", {"note": "zero"})
        assert got.get("is_error") and "does not descend from where this attempt began" in got["content"][0]["text"]
    _in_loop(body)


@pytest.mark.parametrize("left", ["reset", "branch"])
@pytest.mark.parametrize("files", [True, False], ids=["files-to-commit", "nothing-to-commit"])
def test_an_end_state_is_on_the_task_s_branch_and_descends_from_its_last_checkpoint(tmp_path, left, files):
    # A checkpoint's rule for an end state too (an end commits the files as a checkpoint's steps 1 to 5 do, the same
    # ancestry): an attempt that saved a checkpoint, then reset its branch back past it, or went to another branch, has
    # no end state, whether or not it left files to commit (with none, HEAD itself would have been taken, and check 5,
    # comparing the next start with that head, would have passed); from its last checkpoint, it has one
    live, up = _checkpointing(tmp_path)

    async def body():
        a = await up()
        work = a.spec["work"]
        with open(os.path.join(work, "one.txt"), "w") as fh:
            fh.write("one\n")
        got = await a._checkpoint("progress", {"note": "one"})
        assert not got.get("is_error"), got
        first = a.view["task"]["checkpoints"][-1]["head"]
        if left == "reset":
            git("reset", "-q", "--hard", a.start_head, cwd=work)
        else:
            git("checkout", "-q", "-b", "elsewhere", cwd=work)
        if files:
            with open(os.path.join(work, "two.txt"), "w") as fh:
                fh.write("two\n")
        with pytest.raises(ValueError, match="left the task's branch or its history"):
            await a._end_state()
        assert a.view["task"]["end_state"] is None
        assert git("rev-parse", "pp/task-" + TASK, cwd=work) == (a.start_head if left == "reset" else first)
        # back on the task's branch at its last checkpoint, the end state is taken
        if left == "reset":
            git("reset", "-q", "--hard", first, cwd=work)
        else:
            git("checkout", "-q", "pp/task-" + TASK, cwd=work)
        await a._end_state()
        end = a.view["task"]["end_state"]
        if files:
            assert end["committed"] and git("rev-parse", end["head"] + "^", cwd=work) == first, end
            assert git("rev-parse", "pp/task-" + TASK, cwd=work) == end["head"]
        else:
            assert (end["committed"], end["head"]) == (False, first), end
    _in_loop(body)


@pytest.mark.parametrize("before", ["none", "stopped", "rate_limit"])
def test_an_end_state_off_the_task_s_history_ends_the_attempt_blocked_by_the_machine(tmp_path, before):
    # through _finish: an attempt that saved a checkpoint and then reset its branch back past it, nothing left to
    # commit, ends as any other failure of its end state's commit does: blocked, cause machine, the reason naming it, in
    # place of the end decided before it, the owner's Stop among them; an end already blocked (here a rate limit) keeps
    # its own cause and reason
    import asyncio
    live, up = _checkpointing(tmp_path)

    async def body():
        a = await up()
        work = a.spec["work"]
        with open(os.path.join(work, "one.txt"), "w") as fh:
            fh.write("one\n")
        got = await a._checkpoint("progress", {"note": "one"})
        assert not got.get("is_error"), got
        git("reset", "-q", "--hard", a.start_head, cwd=work)       # back past the checkpoint, nothing to commit
        if before == "stopped":
            a.stop(None, "stopped by you")
            await asyncio.sleep(0)
            assert a.ending[0] == "stopped"
        elif before == "rate_limit":
            a._end("blocked", "rate_limit", "plan window exhausted")
        await a._finish()
        if before == "rate_limit":
            assert (a.view["state"], a.view["cause"], a.view["reason"]) == \
                ("blocked", "rate_limit", "plan window exhausted"), a.view
        else:
            assert (a.view["state"], a.view["cause"], a.view["reason"]) == \
                ("blocked", "machine", "the attempt's end state could not be committed: the worktree left the "
                                       "task's branch or its history"), a.view
        assert a.view["task"]["end_state"] is None
    _in_loop(body)


def test_a_checkpoint_is_refused_while_another_call_runs_and_a_late_write_is_not_in_it(tmp_path):
    live, up = _checkpointing(tmp_path, check="true")

    async def body():
        import asyncio
        a = await up()
        work = a.spec["work"]
        a.in_flight.add("other")
        a.calls["other"] = task_attempt._Call("other")
        a.calls["other"].name = "Bash"
        got = await a._checkpoint("step", {"note": "x"})
        assert got["content"][0]["text"] == task_attempt.NOT_WHILE and not a.view["task"]["checkpoints"]
        a.in_flight.clear()
        # a background command still writing after complete_step: its late write is not in the commit
        import subprocess as sp
        sp.Popen(["/bin/sh", "-c", "sleep 1; echo late > late.txt"], cwd=work)
        got = await a._checkpoint("step", {"note": "x"})
        assert not got.get("is_error") and a.completed and a.ending[0] == "done"
        await asyncio.sleep(1.5)
        assert os.path.exists(os.path.join(work, "late.txt"))
        assert "late.txt" not in git("ls-tree", "--name-only", "HEAD", cwd=work).split()
    _in_loop(body)


def test_a_step_s_check_is_ended_at_its_time_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(task_shapes, "CHECK_SECONDS", 1)
    live, up = _checkpointing(tmp_path, check="sleep 20")

    async def body():
        import time as clock
        a = await up()
        began = clock.monotonic()
        got = await a._checkpoint("step", {"note": "x"})
        assert got.get("is_error") and "its check exited 124" in got["content"][0]["text"]
        assert clock.monotonic() - began < 10 and a.view["task"]["checkpoints"][-1]["check"] == 124
    _in_loop(body)


def test_the_bounds_no_checkpoint_past_them_while_the_check_fails_and_an_eleventh_check_refused(tmp_path):
    live, up = _checkpointing(tmp_path, check="false")

    async def body():
        a = await up()
        a.attempt_checkpoints = task_shapes.CHECKPOINTS_PER_ATTEMPT
        got = await a._checkpoint("progress", {"note": "x"})
        assert got.get("is_error") and "No more checkpoints" in got["content"][0]["text"]
        got = await a._checkpoint("step", {"note": "x"})           # past the bounds, its check failing
        assert got.get("is_error") and a.view["task"]["checkpoints"][-1]["state"] == "dropped"
        assert git("rev-parse", "HEAD", cwd=a.spec["work"]) == a.start_head
        a.check_runs = task_shapes.CHECK_RUNS_PER_ATTEMPT
        got = await a._checkpoint("step", {"note": "x"})
        assert got.get("is_error") and "its most" in got["content"][0]["text"]
    _in_loop(body)
    live, up = _checkpointing(tmp_path / "nocheck", check=None)

    async def no_check():
        a = await up()
        a.attempt_checkpoints = task_shapes.CHECKPOINTS_PER_ATTEMPT
        with open(os.path.join(a.spec["work"], "w.txt"), "w") as fh:
            fh.write("w\n")
        got = await a._checkpoint("step", {"note": "x"})           # no check: the step completes, once
        assert not got.get("is_error") and a.completed and a.ending[0] == "done"
        # the control: a step's commit says step, and a hand-off reads the step done from it
        head = git("rev-parse", "HEAD", cwd=a.spec["work"])
        assert git("log", "-1", "--format=%s", cwd=a.spec["work"]).endswith("(step)")
        assert task_runs._steps_done(a.spec["work"], a.start_head, head) == [1]
        got = await a._checkpoint("step", {"note": "again"})
        assert got.get("is_error")
    _in_loop(no_check)


def test_a_checkpoint_cut_off_under_its_check_is_dropped_when_the_attempt_finishes(tmp_path):
    # a step's checkpoint whose task is cancelled under its check (the workspace shutting down) is still pending when
    # the attempt finishes; its end state, the same tree on the same parent, would be taken at the next start for the
    # checkpoint's own commit, the step done without its check, so the attempt drops it
    import asyncio
    live, up = _checkpointing(tmp_path, check="sleep 3")

    async def body():
        a = await up()
        work = a.spec["work"]
        with open(os.path.join(work, "work.txt"), "w") as fh:
            fh.write("the work\n")
        call = asyncio.ensure_future(a._checkpoint("step", {"note": "done"}))
        while a.check_runs < 1:
            await asyncio.sleep(0.05)
        for task in list(a._ck_tasks):
            task.cancel()
        await a._finish()
        (entry,) = a.view["task"]["checkpoints"]
        assert entry["state"] == "dropped" and entry["head"] is None, entry
        end = a.view["task"]["end_state"]
        assert end["committed"] and end["tree"] == entry["tree"], end
        # what the next start would have done with it left pending: the end state's commit taken for its own
        left = dict(entry, state="pending")
        assert task_attempt.complete_pending(work, a.spec["branch"], left)["state"] == "complete"
        assert call.done()
    _in_loop(body)


@pytest.mark.parametrize("case", ["check_failed", "protected", "check_passed"])
def test_a_checkpoint_completed_after_a_machine_stop_says_what_its_branch_holds(tmp_path, case):
    # The machine stops after a step's checkpoint moved its branch and before its record was completed. The next start
    # completes the pending entry the last durable record holds from the branch's head (TaskRuns.recover, through
    # complete_pending, which writes its state and head alone), and the entry then says what the branch holds: a step
    # whose check failed, or that touched a protected path, a progress checkpoint with the check's exit and the paths;
    # one whose check passed, the step with its exit; each with the check's tail
    from types import SimpleNamespace
    check, protected, path, want = {
        "check_failed": ("echo check-said-3; exit 3", [], "work.txt", ("progress", 3, [])),
        "protected": ("echo check-said-0", ["tests/**"], "tests/t.py", ("progress", 0, ["tests/t.py"])),
        "check_passed": ("echo check-said-0", [], "work.txt", ("step", 0, []))}[case]
    live, up = _checkpointing(tmp_path, check=check, protected=protected)
    on_disk = []

    async def body():
        a = await up()
        work = a.spec["work"]
        os.makedirs(os.path.dirname(os.path.join(work, path)), exist_ok=True)
        with open(os.path.join(work, path), "w") as fh:
            fh.write("the work\n")
        move = a._move_branch

        def moved(commit, parent):
            move(commit, parent)
            # the machine stops here: what is on disk is the last durable record
            on_disk.append(json.loads(json.dumps([r for r, durable in live.records if durable][-1])))
        a._move_branch = moved
        await a._checkpoint("step", {"note": "done"})
        return a
    a = _in_loop(body)
    (record,) = on_disk
    records = [{"id": RUN, "agent_id": AGENT_ID,
                "run": {"trigger": {"kind": "task", "id": TASK}, "task": record["task"]}}]
    runs = task_runs.TaskRuns(SimpleNamespace(ws=None, runs=lambda: records, agent=lambda agent_id: {"id": agent_id},
                                              _change=lambda run_id, change: None))
    runs._read_record = lambda agent_id, task_id: {"look": {}}
    runs._current_places = lambda agent, task_id, kept: []
    runs.work = lambda agent_id, task_id: a.spec["work"]
    runs.recover()
    (entry,) = records[0]["run"]["task"]["checkpoints"]
    head = git("rev-parse", a.spec["branch"], cwd=a.spec["work"])
    assert (entry["state"], entry["head"]) == ("complete", head), entry
    assert (entry["kind"], entry["check"], entry["protected"]) == want, entry
    assert f"check-said-{want[1]}" in (entry["check_tail"] or ""), entry
    assert git("log", "-1", "--format=%s", head, cwd=a.spec["work"]).endswith(f"({want[0]})")


# ---- whole attempts, through the workspace agent and the stand-in claude taught 2.1.274's behaviour ----------------
# The stand-in's default account is stand-in@example.com on Claude Max, no organisation, orgId org-stand-in; an
# attempt's brief is the first paragraph of its first message, which the stand-in takes, after the unfreezing restart,
# as its script of work actions (chat_stand_in.py).

CONFIRMED = {"email": "stand-in@example.com", "organization": None, "plan": "Claude Max", "org_id": "org-stand-in"}


def attempt(w, brief, agent=None, **over):
    """An attempt of the task with the confirmed identity, run to its end: its run's view. The default account's
    stand-in file is written first unless a test wrote one, so that `auth status` gives the confirmed orgId."""
    if not os.path.exists(os.path.join(str(w.home / ".claude"), "stand-in-account.json")):
        account_file(w)
    put_agent(w, brief=brief, **(agent or {}))
    over.setdefault("identity", CONFIRMED)
    ended = until(w, queue(w, **over)["run_id"])
    w.expected[ended["run_id"]] = expect_of(ended)
    return ended


def account_file(w, folder=None, **values):
    path = os.path.join(folder or str(w.home / ".claude"), "stand-in-account.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(values, fh)


def jsonl(w, name):
    path = w.home / name
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def claude_starts(w):
    return [s for s in starts_of(w) if "--version" not in s["args"] and s["args"][:2] != ["auth", "status"]]


def resume_agent(w, **over):
    """The owner's resume, in the app, of an agent the workspace held (agents.put: the app mirrors it paused once, then
    not)."""
    put_agent(w, paused=True, **over)
    put_agent(w, **over)


def expect_of(view):
    """What the task's next attempt must find, as the app's packet says it: the end state the attempt before left,
    else its last checkpoint, else None (nothing moved)."""
    task = view["task"]
    end = task.get("end_state") or {}
    if end.get("head"):
        return {"head": end["head"], "tree": end["tree"]}
    done = [c for c in task.get("checkpoints") or [] if c["state"] == "complete"]
    return {"head": done[-1]["head"], "tree": done[-1]["tree"]} if done else None


def next_attempt(w, previous, expect="previous", **over):
    """The task's next attempt after `previous` (its run's view), as the app queues it: one fence and one attempt
    more, with the confirmed identity, its packet expecting what `previous` left, or, where it left nothing (it ended
    before its end state), what the attempt before it left; run to its end. A `step` past the first has its packet
    name it and the steps before it done."""
    if expect == "previous":
        expect = expect_of(previous) or w.expected.get(previous["run_id"])
    step = over.get("step", 1)
    packet = _packet(step=step, expect=expect, steps_done=[{"n": n} for n in range(1, step)])
    over.setdefault("identity", CONFIRMED)
    queued = queue(w, fence=previous["task"]["fence"] + 1, attempt=previous["task"]["attempt"] + 1,
                   packet_b64=base64.b64encode(packet).decode(),
                   packet_digest=task_shapes.tagged_digest(task_shapes.RESUME_TAG, packet), **over)
    ended = until(w, queued["run_id"])
    w.expected[ended["run_id"]] = expect_of(ended) or expect
    return ended


def test_a_step_s_attempt_is_frozen_then_restarted_and_completes_with_its_check(tws):
    done = attempt(tws, "append line.txt=one ;; complete step one", check='test "$(cat line.txt)" = one')
    assert done["state"] == "done", done
    task = done["task"]
    assert task["resume"]["outcome"] == "passed"
    (checkpoint,) = task["checkpoints"]
    assert checkpoint["kind"] == "step" and checkpoint["state"] == "complete" and checkpoint["check"] == 0
    work = task_work(tws)
    assert git("show", f"{checkpoint['head']}:line.txt", cwd=work) == "one"
    frozen, resumed = claude_starts(tws)
    # the frozen start made a new session; the only --resume is its own unfreezing restart, naming that session
    assert not any(a.startswith("--resume") for a in frozen["args"])
    resumes = [a for a in resumed["args"] if a.startswith("--resume")]
    assert len(resumes) == 1
    (first,) = [r for r in jsonl(tws, "stand-in-received.jsonl") if r["frozen"]][:1]
    sid = resumes[0].split("=", 1)[1]
    assert (tws.home / f"stand-in-first-{sid}.txt").exists()
    # its first message: no packet id, no nonce; the packet in the run's folder, never in the worktree
    assert "a-nonce-of-the-app-s" not in first["text"] and TASK not in first["text"]
    assert os.path.exists(os.path.join(tws.root, "agents", AGENT_ID, "runs", done["run_id"], "packet.json"))
    assert "packet.json" not in os.listdir(work)
    assert resumed["setting_sources"] == ["user", "project", "local"] and frozen["setting_sources"] == []
    # The frozen start is offered no built-in tool (the SDK's tools=[], given as --tools ""), so that a Read, which
    # dontAsk lets run, is no tool there; the restart's options leave tools as they are
    at = [i for i, a in enumerate(frozen["args"]) if a == "--tools"]
    assert len(at) == 1 and frozen["args"][at[0] + 1:at[0] + 2] == [""], frozen["args"]
    assert not any(a.startswith("--tools") for a in resumed["args"]), resumed["args"]
    # resume_ack's acknowledgement came from the packet the stand-in read through task_state
    assert any(c["name"] == "mcp__proofpane__resume_ack" for c in jsonl(tws, "stand-in-calls.jsonl"))


def test_a_task_s_second_step_runs_on_another_account_from_where_the_first_step_ended(tws):
    # step 2 on account B: each start's folder, no --resume but the unfreezing restart, the file's two lines; and the
    # second step's protected paths read against where it began, the first step's checkpoint, not the task's checkpoint
    # 0 (the first step wrote a test file)
    r = tws.http.post("/accounts", json={"kind": "claude", "name": "B"})
    assert r.status_code == 200, r.text
    b = r.json()["id"]
    account_file(tws, folder=os.path.join(tws.root, "agent-accounts", b, "claude"), email="b@example.com")
    one = attempt(tws, "bash echo one >> lines.txt ;; bash mkdir -p tests && echo x > tests/t.py ;; complete step one",
                  check='test "$(wc -l < lines.txt)" -eq 1')
    assert one["state"] == "done", one
    a_frozen, a_resumed = claude_starts(tws)
    put_agent(tws, brief="bash echo two >> lines.txt ;; complete step two")
    two = next_attempt(tws, one, step=2, backend={"kind": "account", "id": b},
                       identity=dict(CONFIRMED, email="b@example.com"), protected=["tests/**"],
                       check='test "$(wc -l < lines.txt)" -eq 2 && test "$(grep -c one lines.txt)" -eq 1')
    assert two["state"] == "done", two
    (checkpoint,) = two["task"]["checkpoints"]
    assert (checkpoint["step"], checkpoint["kind"], checkpoint["check"], checkpoint["protected"]) == (2, "step", 0, [])
    assert git("show", f"{checkpoint['head']}:lines.txt", cwd=task_work(tws)) == "one\ntwo"
    b_frozen, b_resumed = claude_starts(tws)[2:]
    # A's starts on the default account, with no CLAUDE_CONFIG_DIR; B's in B's folder, the only one whose claude
    # answers as b@example.com (another folder's would have ended the attempt at its start)
    assert not any("CLAUDE_CONFIG_DIR" in s["env"] for s in (a_frozen, a_resumed))
    assert all("CLAUDE_CONFIG_DIR" in s["env"] for s in (b_frozen, b_resumed))
    # B's frozen start is a new session, and its only --resume is its own restart's, naming that session, not A's
    assert not any(a.startswith("--resume") for a in b_frozen["args"])
    (b_resume,) = [a for a in b_resumed["args"] if a.startswith("--resume")]
    (a_resume,) = [a for a in a_resumed["args"] if a.startswith("--resume")]
    assert b_resume != a_resume and (tws.home / f"stand-in-first-{b_resume.split('=', 1)[1]}.txt").exists()


def test_a_machine_stop_leaves_a_call_in_flight_settled_at_the_next_start(tws):
    # TaskRuns.recover at the workspace's own start (agents.py, startup), reached with a call left in flight: an
    # unclassified call in flight is unknown, its record settled and written again
    account_file(tws)
    # a call this reader cannot place, so unclassified, still running when the machine stops: nice's wrapper, which a
    # Mac has as Linux has, where GNU's timeout, which macOS does not ship, ended the call at once there (exit 127)
    put_agent(tws, brief="bash nice -n 1 sleep 60")
    run_id = queue(tws, identity=CONFIRMED)["run_id"]
    until_view(tws, run_id, lambda v: (v.get("task") or {}).get("effects"))
    tws.stop()
    tws.start()
    stopped = until_view(tws, run_id, lambda v: v["state"] == "blocked" and all(
        e["settled"] for e in v["task"]["effects"]), timeout=20)
    assert stopped["cause"] == "machine", stopped
    (effect,) = stopped["task"]["effects"]
    assert (effect["kind"], effect["end"], effect["settled"]) == ("unclassified", None, "unknown"), effect


def test_a_machine_stop_during_a_step_s_check_leaves_its_checkpoint_dropped_at_the_next_start(tws):
    # a checkpoint recorded pending (its commit made, the branch not moved while its check runs) when the machine stops,
    # resolved by TaskRuns.recover at the next start: the branch's head is not its commit, so it is dropped, and the
    # branch is where it was
    account_file(tws)
    put_agent(tws, brief="append line.txt=one ;; complete step one")
    start = tws.demo("rev-parse", "HEAD")
    run_id = queue(tws, identity=CONFIRMED, check="sleep 60")["run_id"]
    until_view(tws, run_id, lambda v: any(c["state"] == "pending" for c in (v.get("task") or {}).get(
        "checkpoints") or []))
    tws.stop()
    tws.start()
    stopped = until_view(tws, run_id, lambda v: v["state"] == "blocked" and all(
        c["state"] != "pending" for c in v["task"]["checkpoints"]), timeout=20)
    assert stopped["cause"] == "machine", stopped
    assert [(c["state"], c["head"]) for c in stopped["task"]["checkpoints"]] == [("dropped", None)]
    assert git("rev-parse", "pp/task-" + TASK, cwd=task_work(tws)) == start


@pytest.mark.parametrize("watch_changed", [False, True])
def test_a_pending_checkpoint_the_branch_reached_is_completed_at_the_next_start_unless_the_watch_changed(
        tws, watch_changed):
    # end to end: the machine stops after the checkpoint's branch moved and before its record was completed (here the
    # branch moved as the commit made it, its tree and its parent, while the workspace was stopped): TaskRuns.recover
    # completes it from the branch's head at the next start, and, with a watched place changed meanwhile, runs no git
    # and drops it
    account_file(tws)
    put_agent(tws, brief="append line.txt=one ;; complete step one")
    run_id = queue(tws, identity=CONFIRMED, check="sleep 60")["run_id"]
    view = until_view(tws, run_id, lambda v: any(c["state"] == "pending" for c in (v.get("task") or {}).get(
        "checkpoints") or []))
    (entry,) = [c for c in view["task"]["checkpoints"] if c["state"] == "pending"]
    tws.stop()
    work = task_work(tws)
    made = git("commit-tree", entry["tree"], "-p", entry["parent"], "-m", "the checkpoint", cwd=work)
    git("update-ref", "refs/heads/pp/task-" + TASK, made, cwd=work)
    if watch_changed:
        git("config", "core.planted", "yes", cwd=work)
    tws.start()
    stopped = until_view(tws, run_id, lambda v: v["state"] == "blocked" and all(
        c["state"] != "pending" for c in v["task"]["checkpoints"]), timeout=20)
    assert stopped["cause"] == "machine", stopped
    assert [(c["state"], c["head"]) for c in stopped["task"]["checkpoints"]] == \
        ([("dropped", None)] if watch_changed else [("complete", made)])


@pytest.mark.parametrize("watch_changed", [False, True])
def test_a_push_in_flight_at_a_machine_stop_is_settled_by_its_ref_unless_the_watch_changed(tws, watch_changed):
    # a push whose remote took the ref and still runs its post-receive hook when the machine stops; TaskRuns.recover
    # settles it by the remote's ref (done), and, with a watched place changed meanwhile, runs no git and leaves it
    # unknown
    import time
    hook = os.path.join(tws.remote, "hooks", "post-receive")
    with open(hook, "w") as fh:
        fh.write("#!/bin/sh\nsleep 60\n")
    os.chmod(hook, 0o755)
    account_file(tws)
    put_agent(tws, brief=f"bash git push origin pp/task-{TASK}")
    run_id = queue(tws, identity=CONFIRMED)["run_id"]
    view = until_view(tws, run_id, lambda v: any(e["kind"] == "git_remote" for e in (v.get("task") or {}).get(
        "effects") or []))
    (effect,) = [e for e in view["task"]["effects"] if e["kind"] == "git_remote"]
    assert effect["intent"]["before"] == "" and effect["end"] is None, effect
    ref, deadline = "refs/heads/pp/task-" + TASK, time.time() + 30
    while not git("ls-remote", tws.remote, ref, cwd=str(tws.tmp)).startswith(effect["intent"]["commit"]):
        assert time.time() < deadline
        time.sleep(0.1)
    tws.stop()
    if watch_changed:
        git("config", "core.planted", "yes", cwd=task_work(tws))
    tws.start()
    stopped = until_view(tws, run_id, lambda v: v["state"] == "blocked" and all(
        e["settled"] for e in v["task"]["effects"]), timeout=20)
    assert stopped["cause"] == "machine", stopped
    (effect,) = stopped["task"]["effects"]
    assert (effect["end"], effect["settled"]) == (None, "unknown" if watch_changed else "done"), effect


def test_a_message_sent_while_the_attempt_is_frozen_arrives_only_after_the_restart(tws):
    # the message arrives only after the restart, end to end: the owner's message posted while the frozen start works
    # (the stand-in slow to read the task's state)
    import time
    account_file(tws, frozen={"delay": 3})
    put_agent(tws, brief="bash echo first >> log.txt")
    run_id = queue(tws, identity=CONFIRMED)["run_id"]
    deadline = time.time() + 30
    while not list(tws.home.glob("stand-in-first-*.txt")):
        assert time.time() < deadline
        time.sleep(0.05)
    owners = "bash echo second >> log.txt ;; complete done"
    # posted while frozen: the frozen claude is slow to read the task's state, so it has
    # not acknowledged, and no restart has started; a message posted after the restart's turn would be said after it
    assert len(claude_starts(tws)) == 1 and all(m["frozen"] for m in jsonl(tws, "stand-in-received.jsonl"))
    r = tws.http.post(f"/agents/runs/{run_id}/message", json={"text": owners})
    assert r.status_code == 200, r.text
    done = until(tws, run_id)
    assert done["state"] == "done", done
    received = jsonl(tws, "stand-in-received.jsonl")
    assert not any(owners in m["text"] for m in received if m["frozen"])
    resumed = [m["text"] for m in received if not m["frozen"]]
    assert resumed == [task_attempt.RESUMED, owners], resumed
    head = done["task"]["checkpoints"][-1]["head"]
    assert git("show", f"{head}:log.txt", cwd=task_work(tws)) == "first\nsecond"


def test_a_message_sent_while_the_restart_starts_waits_for_its_checks(tmp_path):
    # a message posted while the unfreezing restart's claude is still starting (its initialize slow, as a real one can
    # be) is kept too, until the restart's checks have passed: it reaches claude after "Resume check passed", never
    # before the account it runs on was checked
    import time
    w = TaskWorkspace(tmp_path, config={"INIT_DELAY": 2})
    try:
        account_file(w)
        put_agent(w, brief="bash echo first >> log.txt")
        run_id = queue(w, identity=CONFIRMED)["run_id"]
        deadline = time.time() + 60
        while len(claude_starts(w)) < 2:        # the restart's claude has started; its initialize answers 2 s later
            assert time.time() < deadline
            time.sleep(0.05)
        owners = "bash echo second >> log.txt ;; complete done"
        r = w.http.post(f"/agents/runs/{run_id}/message", json={"text": owners})
        assert r.status_code == 200, r.text
        done = until(w, run_id)
        assert done["state"] == "done", done
        resumed = [m["text"] for m in jsonl(w, "stand-in-received.jsonl") if not m["frozen"]]
        assert resumed == [task_attempt.RESUMED, owners], resumed
        head = done["task"]["checkpoints"][-1]["head"]
        assert git("show", f"{head}:log.txt", cwd=task_work(w)) == "first\nsecond"
    finally:
        w.stop()


def test_a_message_sent_while_the_attempt_works_goes_on_after_its_turn(tws):
    # once the restart's checks have passed, a message is said, not kept: posted while the resumed turn works, it is
    # given at the turn's end, and the attempt goes on with it
    import time
    account_file(tws)
    put_agent(tws, brief="bash echo first >> log.txt ;; sleep 3")
    run_id = queue(tws, identity=CONFIRMED)["run_id"]
    deadline = time.time() + 60
    while not any(not m["frozen"] for m in jsonl(tws, "stand-in-received.jsonl")):
        assert time.time() < deadline
        time.sleep(0.05)
    owners = "bash echo second >> log.txt ;; complete done"
    r = tws.http.post(f"/agents/runs/{run_id}/message", json={"text": owners})
    assert r.status_code == 200, r.text
    done = until(tws, run_id)
    assert done["state"] == "done", done
    resumed = [m["text"] for m in jsonl(tws, "stand-in-received.jsonl") if not m["frozen"]]
    assert resumed == [task_attempt.RESUMED, owners], resumed


def test_a_key_s_attempt_runs_on_its_helper_with_anthropic_s_address_pinned_and_its_cap(tws):
    key = "sk-ant-test-" + "k" * 30
    assert tws.http.put("/model-providers/anthropic", json={"key": key}).status_code == 200
    done = attempt(tws, "complete done", backend={"kind": "key", "provider": "anthropic"}, identity=None, cap_usd=5)
    assert done["state"] == "done", done
    for start in claude_starts(tws):
        args = start["args"]
        settings = json.loads(args[args.index("--settings") + 1])
        assert settings["apiKeyHelper"] and settings["env"]["ANTHROPIC_BASE_URL"] == "https://api.anthropic.com"
        assert args[args.index("--max-budget-usd") + 1] == "5"
        assert "ANTHROPIC_API_KEY" not in start["env"]
    assert key not in (tws.home / "stand-in-runs.jsonl").read_text()
    assert done["task"]["start"]["api_key_source"] == "apiKeyHelper"
    assert key not in json.dumps(run_view(tws, done["run_id"]))
    # a Claude Code folder of the key's own, never signed in, not the default account's
    folder = os.path.join(tws.root, "agent-accounts", "keys", "anthropic", "claude")
    assert all(start["effective"].get("CLAUDE_CONFIG_DIR") == folder for start in claude_starts(tws))
    assert os.path.isdir(folder) and (os.stat(folder).st_mode & 0o777) == 0o700
    assert not os.path.exists(os.path.join(folder, ".credentials.json"))


def test_an_account_s_attempt_names_anthropic_s_address_over_a_repository_s(tws):
    # on an account as on Anthropic's key, so that a repository's settings, loaded after the unfreezing restart, cannot
    # send a sign-in's token elsewhere
    demo = os.path.join(tws.root, "repos", "demo")
    os.makedirs(os.path.join(demo, ".claude"), exist_ok=True)
    commit_in(demo, os.path.join(".claude", "settings.json"),
              json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://elsewhere.example"}}), "point claude elsewhere")
    done = attempt(tws, "complete done")
    assert done["state"] == "done", done
    frozen, at_work = claude_starts(tws)
    for start in (frozen, at_work):
        settings = json.loads(start["args"][start["args"].index("--settings") + 1])
        assert settings["env"]["ANTHROPIC_BASE_URL"] == "https://api.anthropic.com"
    assert at_work["setting_sources"] == ["user", "project", "local"]       # the repository's own were read
    assert at_work["effective"]["ANTHROPIC_BASE_URL"] == "https://api.anthropic.com"


def test_another_vendor_s_key_runs_its_own_model_not_the_agent_s(tws):
    # on another vendor's key the model is that vendor's, pinned by its entry
    assert tws.http.put("/model-providers/kimi", json={"key": "sk-kimi-test-" + "k" * 30}).status_code == 200
    model = {"chat_settings": {"allow": ["Write", "Edit", "Bash"], "model": "claude-fable-5"}}
    done = attempt(tws, "complete done", agent=model, backend={"kind": "key", "provider": "kimi"}, identity=None,
                   cap_usd=5)
    assert done["state"] == "done", done
    for start in claude_starts(tws):
        assert "--model" not in start["args"], start["args"]
        settings = json.loads(start["args"][start["args"].index("--settings") + 1])
        assert settings["env"]["ANTHROPIC_MODEL"] == providers.CATALOG["kimi"]["env"]["ANTHROPIC_MODEL"]
    # and on Anthropic's own key, and an account, the agent's model is passed
    assert tws.http.put("/model-providers/anthropic", json={"key": "sk-ant-test-" + "k" * 30}).status_code == 200
    before = len(claude_starts(tws))
    done = next_attempt(tws, done, backend={"kind": "key", "provider": "anthropic"}, identity=None, cap_usd=5)
    assert done["state"] == "done", done
    assert all(s["args"][s["args"].index("--model") + 1] == "claude-fable-5" for s in claude_starts(tws)[before:])


def test_a_refused_window_ends_the_attempt_blocked_with_its_partial_change_as_its_end_state(tws):
    blocked = attempt(tws, "append line.txt=partial ;; ratelimit rejected five_hour")
    assert blocked["state"] == "blocked" and blocked["cause"] == "rate_limit", blocked
    end = blocked["task"]["end_state"]
    assert end["committed"] is True and git("show", f"{end['head']}:line.txt", cwd=task_work(tws)) == "partial"
    assert git("rev-parse", "HEAD", cwd=task_work(tws)) == end["head"]
    assert blocked["rate_limits"]["five_hour"]["status"] == "rejected"


def test_a_signed_out_backend_another_account_or_another_org_id_ends_it_at_check_3_or_the_start(tws):
    account_file(tws, signed_out=True)
    blocked = attempt(tws, "complete done")
    assert blocked["state"] == "blocked" and blocked["task"]["resume"]["check"] == "backend"
    assert blocked["task"]["resume"]["found"] == {"signed_in": False}      # refused as signed out, not by its orgId
    assert blocked["task"]["start"]["failure"] == "account"                # found signed out: the account's own
    assert claude_starts(tws) == []                     # no claude started for it
    # an answer that cannot be read is an unclear cause, which holds the task: not the account's
    account_file(tws, auth_unreadable=True)
    blocked = next_attempt(tws, blocked)
    assert blocked["task"]["resume"]["check"] == "backend" and blocked["task"]["resume"]["found"] == {"signed_in": None}
    assert blocked["task"]["start"]["failure"] == "same_everywhere"
    assert claude_starts(tws) == []
    # an account whose email changed: its frozen start's report says so, before its first message
    account_file(tws, email="someone-else@example.com", auth_email="someone-else@example.com")
    blocked = next_attempt(tws, blocked)
    assert blocked["task"]["resume"]["check"] == "start" and blocked["task"]["start"]["failure"] == "account"
    assert jsonl(tws, "stand-in-received.jsonl") == []    # ended before its first message
    # the folder signed in again to another organisation of the same name and plan: the report matches,
    # auth status's orgId does not, and the account refuses at check 3, as an identity change, before any start
    before = len(claude_starts(tws))
    account_file(tws, org_id="org-other")
    blocked = next_attempt(tws, blocked)
    assert blocked["task"]["resume"]["check"] == "backend" and blocked["task"]["start"]["failure"] == "account"
    assert blocked["task"]["resume"]["found"] == {"org_id": "org-other", "confirmed": "org-stand-in"}
    assert len(claude_starts(tws)) == before
    # and before every start: an orgId that changes after the frozen start refuses the unfreezing restart
    account_file(tws, org_id=["org-stand-in", "org-other"])
    blocked = next_attempt(tws, blocked)
    assert blocked["task"]["resume"]["check"] == "backend" and blocked["task"]["start"]["failure"] == "account"
    assert len(claude_starts(tws)) == before + 1        # the frozen start alone
    assert not any(m["text"] == task_attempt.RESUMED for m in jsonl(tws, "stand-in-received.jsonl"))


def test_the_frozen_claude_runs_nothing_but_its_two_tools(tws):
    account_file(tws, frozen={"try": ["Bash", "Read"], "ack": "right"})
    done = attempt(tws, "complete done")
    assert done["state"] == "done", done
    frozen_calls = [c for c in jsonl(tws, "stand-in-calls.jsonl") if c["name"] in ("Bash", "Read")]
    assert [c["name"] for c in frozen_calls] == ["Bash", "Read"]
    assert all(c["denied"] and not c["ran"] for c in frozen_calls)


def test_a_watched_change_at_an_ungated_end_holds_the_agent_too(tws):
    # the attempt keeps its ungated cause, and the change it made to a watched place still holds the agent, as an end by
    # the watch does
    held = attempt(tws, "bash git config core.watched yes ;; unseen bash echo ran unseen")
    assert held["state"] == "blocked" and held["cause"] == task_attempt.UNGATED, held
    assert held["task"]["watch"]["changed"], held["task"]["watch"]
    assert tws.http.get(f"/agents/{AGENT_ID}").json()["paused"] is True


@pytest.mark.parametrize("way", ["changed", "loaded", "ungated", "between", "precheck", "none"])
def test_each_hold_of_a_task_s_agent_is_on_its_run_s_record_for_the_app(tws, way):
    # the app pauses its agent from a run's `hold`, as an agent's own run's, counted as not yet mirrored paused, so that
    # the owner's resume lifts the hold here (agents.put); a task's attempt that held its agent with no hold on its
    # record would leave it held here while the app showed it never paused, and every Continue would be refused here.
    # Each way an attempt holds its agent (a watched place changed while it worked, a file a next run loads changed, the
    # first at an ungated end, a change between two attempts found by the start's look, one the agent's precheck made
    # after that look, found by the attempt's own check 4) puts the hold on its record, naming why; the control, an
    # attempt that holds nothing, has none
    briefs = {"changed": "bash git config core.watched yes ;; complete done",
              "loaded": "bash d=.cla && mkdir -p ${d}ude && echo {} > ${d}ude/settings.json ;; complete done",
              "ungated": "bash git config core.watched yes ;; unseen bash echo ran unseen"}
    ended = attempt(tws, briefs.get(way, "complete done"))
    repo = os.path.join(tws.root, "repos", "demo")
    if way in ("between", "precheck"):
        assert ended["state"] == "done" and ended["hold"] is None, ended
        if way == "between":
            git("config", "core.between", "yes", cwd=repo)
        else:
            put_agent(tws, brief="complete done", precheck=f"git -C {repo} config core.prechecked yes")
        ended = next_attempt(tws, ended)
        assert ended["task"]["resume"]["check"] == "watch", ended
    paused = tws.http.get(f"/agents/{AGENT_ID}").json()["paused"]
    if way == "none":
        assert (ended["state"], ended["hold"], paused) == ("done", None, False), ended
        return
    assert ended["state"] == "blocked" and paused is True, ended
    assert isinstance(ended["hold"], str) and ended["hold"].endswith(task_runs.WATCH_HOLD), ended["hold"]
    if way == "loaded":
        # the attempt's own why, then what changed
        assert ended["cause"] == "watch" and ended["hold"].startswith("The attempt changed files a next run loads (")
        assert ".claude/settings.json" in ended["hold"], ended["hold"]
    elif way == "ungated":
        assert ended["cause"] == task_attempt.UNGATED, ended


def test_an_intent_s_git_runs_only_after_the_watched_places_were_found_unchanged(tws):
    # the intent hook reads the remote with git in the workspace's own process; a watched place changed earlier in the
    # attempt means no git, the call refused, and the task held for the owner; the attempt ends there, so a call after
    # it never runs
    held = attempt(tws, f"bash git config core.planted yes ;; bash git push origin pp/task-{TASK} ;; "
                        "bash echo after-the-push")

    def command(c):
        return str((c["input"] or {}).get("command"))
    pushes = [c for c in jsonl(tws, "stand-in-calls.jsonl") if "git push" in command(c)]
    assert held["state"] == "blocked" and held["cause"] == "watch", held
    assert len(pushes) == 1 and pushes[0]["denied"] and not pushes[0]["ran"], pushes
    assert not [c for c in jsonl(tws, "stand-in-calls.jsonl") if "after-the-push" in command(c) and c["ran"]]
    assert not [e for e in held["task"]["effects"] if e["kind"] == "git_remote"]
    assert git("ls-remote", tws.remote, f"refs/heads/pp/task-{TASK}", cwd=str(tws.tmp)) == ""
    assert tws.http.get(f"/agents/{AGENT_ID}").json()["paused"] is True
    # a pull request's intent, which reads with gh: the same, once the owner resumed the agent
    resume_agent(tws, brief="bash git config core.planted2 yes ;; "
                            f"bash gh pr create --repo o/n --head pp/task-{TASK} --title t --body b ;; "
                            "bash echo after-the-pr")
    held = next_attempt(tws, held, repo_slug="o/n")
    assert held["state"] == "blocked" and held["cause"] == "watch", held
    prs = [c for c in jsonl(tws, "stand-in-calls.jsonl") if "gh pr create" in command(c)]
    assert len(prs) == 1 and prs[0]["denied"] and not prs[0]["ran"], prs
    assert not [c for c in jsonl(tws, "stand-in-calls.jsonl") if "after-the-pr" in command(c) and c["ran"]]
    assert not [e for e in held["task"]["effects"] if e["kind"] == "github"]


def test_the_watched_places_follow_the_accounts_there_are_at_each_attempt(tws):
    # an account added after the task's first attempt is watched from then on, and one removed since is no longer
    # compared
    spare = tws.http.post("/accounts", json={"kind": "claude", "name": "Spare"}).json()["id"]
    gone = tws.http.post("/accounts", json={"kind": "claude", "name": "Gone"}).json()["id"]
    done = attempt(tws, "complete done")
    assert done["state"] == "done", done

    def remove(account):
        account_file(tws, folder=os.path.join(tws.root, "agent-accounts", account, "claude"), signed_out=True)
        r = tws.http.delete(f"/accounts/{account}")     # signed out first, as the workspace removes one
        assert r.status_code == 200, r.text
    remove(spare)
    # the task's routes compare the places as they are now too: a Resolve after the
    # removal is not refused for the removed account's folder
    r = tws.http.post(f"/agents/tasks/{TASK}/worktree",
                      json={"fence": done["task"]["fence"], "action": "discard", "head": expect_of(done)["head"]})
    assert r.status_code == 200, r.text
    later = tws.http.post("/accounts", json={"kind": "claude", "name": "Later"}).json()["id"]
    remove(gone)                                    # and one removed with no route between it and the next start
    done = next_attempt(tws, done)
    assert done["state"] == "done", done            # neither a removed account nor the added one is a change
    with open(os.path.join(tws.root, "agent-accounts", later, "claude", ".claude.json"), "w") as fh:
        json.dump({"oauthAccount": {"emailAddress": "someone@example.org"}}, fh)
    held = next_attempt(tws, done)
    assert held["state"] == "blocked" and held["task"]["resume"]["check"] == "watch", held


def test_an_account_taken_out_of_the_record_is_compared_with_how_it_was_last_seen_when_it_comes_back(tws):
    # which accounts are compared follows the accounts' record, which a task's watch does not watch; an account taken
    # out of it for an attempt and put back is compared with how its folder was last seen, never taken as it is then, so
    # a change made while it was out is found
    spare = tws.http.post("/accounts", json={"kind": "claude", "name": "Spare"}).json()["id"]
    done = attempt(tws, "complete done")
    assert done["state"] == "done", done
    record = os.path.join(tws.root, "agent-accounts.json")
    with open(record, encoding="utf-8") as fh:
        whole = fh.read()
    data = json.loads(whole)
    data["accounts"] = [a for a in data["accounts"] if a["id"] != spare]
    with open(record, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    done = next_attempt(tws, done)
    assert done["state"] == "done", done            # out of the record: not compared
    with open(os.path.join(tws.root, "agent-accounts", spare, "claude", ".claude.json"), "w") as fh:
        json.dump({"mcpServers": {"planted": {"command": "true"}}}, fh)
    with open(record, "w", encoding="utf-8") as fh:
        fh.write(whole)
    held = next_attempt(tws, done)
    assert held["state"] == "blocked" and held["task"]["resume"]["check"] == "watch", held


def test_a_key_backend_s_folder_is_watched_between_attempts_and_by_an_agent_s_own_run(tws):
    # a key's attempt loads its own folder's settings and instructions, so the folder is a watched place: a change to it
    # between two attempts holds the task, and an agent's own run that writes there is held at its end, as for any
    # account's folder
    done = attempt(tws, "complete done")
    assert done["state"] == "done", done
    folder = os.path.join(tws.root, "agent-accounts", "keys", "anthropic", "claude")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "settings.json"), "w") as fh:
        json.dump({"hooks": {}}, fh)
    held = next_attempt(tws, done)
    assert held["state"] == "blocked" and held["task"]["resume"]["check"] == "watch", held
    resume_agent(tws, brief="complete done")
    instructions = os.path.join(tws.root, "agent-accounts", "keys", "deepseek", "claude", "CLAUDE.md")
    # a process the run starts writes it, as one a Bash call leaves behind would; the run ends a second later
    put_agent(tws, brief=f"[mkdir -p {os.path.dirname(instructions)} && echo planted > {instructions}] later 1 hello")
    r = tws.http.post(f"/agents/{AGENT_ID}/runs", json={"trigger": {"kind": "manual", "prompt": None}})
    assert r.status_code == 200, r.text
    own = until(tws, r.json()["run_id"])
    assert os.path.exists(instructions), own
    assert own["state"] == "blocked" and own["cause"] == "watch", own
    assert tws.http.get(f"/agents/{AGENT_ID}").json()["paused"] is True


def test_outside_imports_reads_imports_as_claude_code_follows_them(tmp_path):
    # the reader alone: an import by ~/, by an absolute path, or climbing out of the tree is outside it, and one inside
    # the tree is followed on; ~user and an address are not paths; a link is kept with where it leads; a pipe is never
    # read, which would wait; a fifth hop is not followed; and the places are bounded
    import pathlib
    tmp = pathlib.Path(os.path.realpath(tmp_path))
    home, work, out = tmp / "home", tmp / "work", tmp / "out"
    for folder in (home, work / "docs", out):
        folder.mkdir(parents=True)
    (work / "CLAUDE.md").write_text(f"@docs/a.md @../out/climb.md @{out}/abs.md @~/h.md @~other/x.md "
                                    "@https://example.org/x.md @link.md @~/c1.md\n")
    (work / "docs" / "a.md").write_text("@~/from-tree.md\n")
    (out / "climb.md").write_text("")
    (out / "target.md").write_text("")
    os.symlink(out / "target.md", work / "link.md")
    (home / "h.md").write_text("@~/pipe.md\n")
    os.mkfifo(home / "pipe.md")
    for n in range(1, 6):
        (home / f"c{n}.md").write_text(f"@~/c{n + 1}.md\n")
    import threading
    got = []
    reader = threading.Thread(target=lambda: got.append(task_attempt.outside_imports(
        [str(work / "CLAUDE.md")], str(work), str(home))), daemon=True)
    reader.start()
    reader.join(10)
    assert got, "the reader waited on the pipe"
    found = got[0]
    assert sorted(found) == sorted(str(p) for p in (
        out / "climb.md", out / "abs.md", home / "h.md", work / "link.md", out / "target.md", home / "from-tree.md",
        home / "pipe.md", home / "c1.md", home / "c2.md", home / "c3.md", home / "c4.md")), found
    (work / "CLAUDE.md").write_text(" ".join(f"@~/f{n}.md" for n in range(task_attempt.MAX_IMPORT_PLACES + 1)))
    assert len(task_attempt.outside_imports([str(work / "CLAUDE.md")], str(work), str(home))) == \
        task_attempt.MAX_IMPORT_PLACES


def test_a_file_an_instruction_file_imports_from_outside_the_worktree_is_watched_for_four_hops(tws):
    # any file the instruction files import with @path, which Claude Code follows for up to four hops, is watched; one
    # outside the worktree is a watched place of the task, imported through Claude Code's own CLAUDE.md, through a file
    # inside the tree or by an instruction file in a subfolder, and a fifth hop, which is not loaded, is not; an import
    # added later is watched once the owner has looked at its adding
    chain = tws.home / "chain"
    chain.mkdir()
    for n in range(1, 6):
        (chain / f"i{n}.md").write_text(f"hop {n}\n" + (f"@~/chain/i{n + 1}.md\n" if n < 5 else ""))
    own = tws.home / ".claude" / "CLAUDE.md"
    own.parent.mkdir(exist_ok=True)
    own.write_text("See @~/chain/i1.md\n")
    for name in ("via-tree.md", "nested.md", "later.md"):
        (tws.home / name).write_text("as it was\n")
    repo = os.path.join(tws.root, "repos", "demo")
    for path, text in (("CLAUDE.md", "@docs/notes.md\n"), ("docs/notes.md", "@~/via-tree.md\n"),
                       ("sub/deeper/AGENTS.md", "@~/nested.md\n")):
        os.makedirs(os.path.dirname(os.path.join(repo, path)), exist_ok=True)
        with open(os.path.join(repo, path), "w") as fh:
            fh.write(text)
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "instructions", cwd=repo)
    previous = attempt(tws, "complete done")
    assert previous["state"] == "done", previous
    for path, watched in ((chain / "i5.md", False), (chain / "i4.md", True), (tws.home / "via-tree.md", True),
                          (tws.home / "nested.md", True), (own, True), (tws.home / "later.md", True)):
        with open(path, "a") as fh:
            fh.write("@~/later.md\n" if path == own else "changed between attempts\n")
        ended = next_attempt(tws, previous)
        if watched:
            assert ended["state"] == "blocked" and ended["task"]["resume"]["check"] == "watch", (path, ended)
            resume_agent(tws, brief="complete done")
            if path == own:
                # the owner looked at the import's adding: the next attempt finds the file as it is, and watches it
                ended = next_attempt(tws, ended)
                assert ended["state"] == "done", ended
        else:
            assert ended["state"] == "done", (path, ended)
        previous = ended


def test_the_start_s_look_comes_before_any_git_and_finds_a_changed_git_link(tws):
    # the places git's settings name are kept from the task's first attempt, so no git runs before the start's look; a
    # worktree whose .git now names a folder that is not there is found by the look (the attempt ends blocked, its agent
    # held), not by a git that fails first
    done = attempt(tws, "complete done")
    assert done["state"] == "done", done
    with open(os.path.join(task_work(tws), ".git"), "w") as fh:
        fh.write("gitdir: /nowhere/at/all\n")
    held = next_attempt(tws, done)
    assert held["state"] == "blocked" and held["task"]["resume"]["check"] == "watch", held
    assert tws.http.get(f"/agents/{AGENT_ID}").json()["paused"] is True


def test_a_file_git_s_settings_newly_include_is_watched_once_the_owner_has_looked(tws):
    # git's own places are found again with git once the start's look has found the places as kept: a file the
    # repository's settings come to include (a watched change the owner has looked at) is watched from that attempt on
    done = attempt(tws, "complete done")
    assert done["state"] == "done", done
    extra = tws.tmp / "extra.gitconfig"
    extra.write_text("[user]\n\tname = Ana\n")
    git("config", "include.path", str(extra), cwd=os.path.join(tws.root, "repos", "demo"))
    held = next_attempt(tws, done)
    assert held["state"] == "blocked" and held["task"]["resume"]["check"] == "watch", held
    resume_agent(tws, brief="complete done")
    again = next_attempt(tws, held)
    assert again["state"] == "done", again
    extra.write_text("[user]\n\tname = Ana\n[core]\n\tpager = ./x\n")
    held = next_attempt(tws, again)
    assert held["state"] == "blocked" and held["task"]["resume"]["check"] == "watch", held


def test_check_4_looks_before_the_agent_s_precheck_runs(tws):
    # the agent's precheck, a command that may run git in the repository, runs only after the watched places were looked
    # at and found unchanged
    done = attempt(tws, "complete done", agent={"precheck": 'touch "$HOME/precheck-ran"'})
    assert done["state"] == "done" and (tws.home / "precheck-ran").exists(), done
    (tws.home / "precheck-ran").unlink()
    git("config", "core.planted", "yes", cwd=os.path.join(tws.root, "repos", "demo"))
    held = next_attempt(tws, done)
    assert held["state"] == "blocked" and held["task"]["resume"]["check"] == "watch", held
    assert not (tws.home / "precheck-ran").exists()
    assert tws.http.get(f"/agents/{AGENT_ID}").json()["paused"] is True


@pytest.mark.parametrize("refused", ["stale_fence", "another_packet", "not_a_resume_packet"])
def test_a_stale_fence_or_another_packet_is_refused_before_the_start_s_look_and_its_precheck(tws, refused):
    # Were the fence and the packet checked only once the attempt's spec was made, after its look at the watched places,
    # its git, a write of the task's record and its agent's precheck, such a message would run the precheck, and where a
    # watched place had changed its look would hold the agent for it and keep the change as found. Both are refused
    # first, as check_spec refuses them, and nothing of the attempt's runs
    done = attempt(tws, "complete done", agent={"precheck": 'touch "$HOME/precheck-ran"'})
    assert done["state"] == "done" and (tws.home / "precheck-ran").exists(), done
    (tws.home / "precheck-ran").unlink()
    git("config", "core.planted", "yes", cwd=os.path.join(tws.root, "repos", "demo"))     # a watched place changed
    packet = _packet(expect=expect_of(done))
    fence = done["task"]["fence"] + 1
    digest = task_shapes.tagged_digest(task_shapes.RESUME_TAG, packet)
    if refused == "stale_fence":
        fence = done["task"]["fence"]                   # not higher than the one the workspace started
    elif refused == "another_packet":
        digest = "0" * 64
    else:
        packet = json.dumps({"schema": "not a resume packet"}).encode("utf-8")
        digest = task_shapes.tagged_digest(task_shapes.RESUME_TAG, packet)
    ended = until(tws, queue(tws, identity=CONFIRMED, fence=fence, attempt=2, packet_digest=digest,
                             packet_b64=base64.b64encode(packet).decode())["run_id"])
    check = "fence" if refused == "stale_fence" else "packet"
    assert (ended["state"], ended["cause"], ended["task"]["resume"]["check"]) == ("blocked", "resume", check), ended
    assert not (tws.home / "precheck-ran").exists()                         # its precheck never ran
    assert tws.http.get(f"/agents/{AGENT_ID}").json()["paused"] is False    # nor its look, which would hold the agent
    # the change is found by the next attempt, at its own start's look
    held = next_attempt(tws, done)
    assert held["state"] == "blocked" and held["task"]["resume"]["check"] == "watch", held


def test_a_stale_attempt_of_a_task_removed_here_is_refused_before_anything_is_made_for_it(tws):
    # The refusal's place: before the task's worktree is made at an attempt's start, so a stale
    # message for a task whose folder was removed here makes nothing (and is not refused for the branch the removal
    # kept, which would make it fail instead)
    done = attempt(tws, "complete done")
    assert done["state"] == "done", done
    assert tws.http.delete(f"/agents/tasks/{TASK}").status_code == 200
    stale = until(tws, queue(tws, identity=CONFIRMED)["run_id"])       # fence 1, the one the workspace started
    assert (stale["state"], stale["cause"], stale["task"]["resume"]["check"]) == ("blocked", "resume", "fence"), stale
    assert not os.path.exists(os.path.dirname(task_work(tws)))


@pytest.mark.parametrize("case", ["stale_fence", "equal_fence", "another_digest", "not_utf8", "not_json", "a_list",
                                  "another_schema", "no_schema", "a_resume_packet"])
def test_a_start_s_early_refusal_is_the_spec_s_own_word_for_word(tmp_path, case):
    # a start refuses a stale fence and a packet that is not the one sent or not a resume packet before anything of the
    # attempt's (task_runs._resume_refusal), a copy of the attempt's own check (task_attempt.check_spec), which runs
    # them again: the two refuse the same messages with the same words, check and findings, and pass the same
    packet, known, digest = _packet(), 1, None
    if case in ("stale_fence", "equal_fence"):
        known = 3 if case == "stale_fence" else 2
    elif case == "another_digest":
        digest = task_shapes.tagged_digest(task_shapes.RESUME_TAG, _packet(step=2))
    elif case != "a_resume_packet":
        packet = {"not_utf8": b"\xff\xfe not text", "not_json": b"not json", "a_list": b"[]", "no_schema": b"{}",
                  "another_schema": json.dumps({"schema": "not a resume packet"}).encode("utf-8")}[case]
    digest = digest or task_shapes.tagged_digest(task_shapes.RESUME_TAG, packet)
    sent = task_runs.check_message(message(fence=2, attempt=2, packet_b64=base64.b64encode(packet).decode(),
                                           packet_digest=digest))
    early = task_runs._resume_refusal(sent, known)
    spec = _spec(tmp_path, packet=packet, packet_digest=digest, fence=2, attempt=2, known_fence=known)
    if case == "a_resume_packet":
        assert early is None and task_attempt.check_spec(spec) is spec
        return
    later = _refused(spec)
    assert early is not None and (str(early), early.check, early.found) == (str(later), later.check, later.found), \
        (case, early, later)


def test_what_the_start_s_look_ended_an_attempt_on_is_the_next_attempt_s_baseline(tws):
    # the resume check's check 4 compares with what "the previous attempt's end found", and an attempt the start's look
    # refused ended there: once the owner has resumed the agent its change held, the next attempt runs, its precheck
    # with it, and a change after that is found again
    agent = {"precheck": 'touch "$HOME/precheck-ran"'}
    done = attempt(tws, "complete done", agent=agent)
    assert done["state"] == "done", done
    repo = os.path.join(tws.root, "repos", "demo")
    git("config", "core.planted", "yes", cwd=repo)
    held = next_attempt(tws, done)
    assert held["state"] == "blocked" and held["task"]["resume"]["check"] == "watch", held
    resume_agent(tws, brief="complete done", **agent)
    (tws.home / "precheck-ran").unlink()
    again = next_attempt(tws, held)
    assert again["state"] == "done" and (tws.home / "precheck-ran").exists(), again
    git("config", "core.planted", "no", cwd=repo)
    held = next_attempt(tws, again)
    assert held["state"] == "blocked" and held["task"]["resume"]["check"] == "watch", held


# ---- a task's attempt and the record of git's places (gitplaces.py, agents.suspects) --------------------------------

OTHER_AGENT = "0b0b0b0b0b0b"


def _paused_within(w, seconds, agent_id=AGENT_ID):
    """Whether the agent is held (paused) at any time within `seconds`."""
    deadline = time.time() + seconds
    while True:
        if w.http.get(f"/agents/{agent_id}").json()["paused"]:
            return True
        if time.time() > deadline:
            return False
        time.sleep(0.2)


def _listed(w, name):
    (repo,) = [r for r in w.http.get("/repos").json()["repos"] if r["name"] == name]
    return repo


def _with_other(tmp_path):
    """A task's workspace with another repository, there before its first start, so its places are accepted then."""
    make_repo(os.path.join(str(tmp_path), "ws", "repos", "other"))
    return TaskWorkspace(tmp_path)


def test_a_task_s_attempt_that_changes_another_repository_s_git_settings_holds_its_agent(tmp_path):
    # a task's attempt ends as an agent's run does, git's places looked at (TaskRuns._places_at_end): the task's own
    # watch covers its repository and the user's git settings, not another repository of the workspace, which a program
    # the attempt runs can still write (the gate refuses only what a call names). That change holds the agent at the
    # attempt's end, named in the hold on its run's record, and no git of the workspace's runs there until the owner has
    # resumed it; meanwhile an acceptance into another agent on that repository is refused alone (409: git should not
    # run there now), not failed as unreadable by a 400
    w = _with_other(tmp_path)
    try:
        other = os.path.join(w.root, "repos", "other")
        done = attempt(w, "complete done")
        assert done["state"] == "done" and done.get("hold") is None, done
        assert not _paused_within(w, 3)     # an attempt that changed nothing there holds nothing
        plant = ('cd "$(git rev-parse --path-format=absolute --git-common-dir)/../../other" && '
                 'git config core.planted yes')
        put_agent(w, brief=f"write plant.sh={plant} ;; bash sh plant.sh ;; complete done")
        planted = next_attempt(w, done)
        assert git("config", "--get", "core.planted", cwd=other) == "yes"
        assert planted["state"] == "done", planted      # the task's own watch did not cover it
        assert (planted.get("hold") or "").startswith(
            "The git settings of the repository other changed while an agent's run worked (config core.planted), so "
            "no git of the workspace's runs there until you resume an agent held for it."), planted
        assert _paused_within(w, 10)
        listed = _listed(w, "other")
        assert "changed while an agent's run worked" in (listed.get("held") or "")
        # its slug and address in the listing as in every entry, null while no git may
        # read its origin
        assert listed["slug"] is None and listed["address"] is None, listed
        put_agent(w, agent_id=OTHER_AGENT, repo="other")
        head = git("rev-parse", "HEAD", cwd=other)
        accepted = w.http.post(f"/agents/tasks/{'d' * 32}/fetch", json={
            "address": w.remote, "commit": head, "purpose": "accept", "agent": OTHER_AGENT, "base": None,
            "protected": []})
        assert accepted.status_code == 409 and accepted.json()["detail"].startswith(
            "The git settings of the repository other changed while an agent's run worked"), accepted.text
        # a view where the task is not looks for the repository whose origin is the task's address: the held one, whose
        # origin no git may read now, is passed over, and with no other having it the view is that 409 too, not a 400
        viewed = w.http.post(f"/agents/tasks/{'e' * 32}/fetch", json={
            "address": "https://example.invalid/o/n.git", "commit": head, "purpose": "view", "agent": None,
            "base": None, "protected": []})
        assert viewed.status_code == 409 and "repository other changed" in viewed.json()["detail"], viewed.text
        resume_agent(w, brief="complete done")          # the owner looked: what is there now is accepted
        assert not _listed(w, "other").get("held")
        viewed = w.http.post(f"/agents/tasks/{'e' * 32}/fetch", json={
            "address": "https://example.invalid/o/n.git", "commit": head, "purpose": "view", "agent": None,
            "base": None, "protected": []})
        assert viewed.status_code == 404, viewed.text   # no repository here has that address
    finally:
        w.stop()


def test_a_change_found_while_a_task_s_precheck_runs_is_held_with_its_agent(tmp_path):
    # a task's attempt is a suspect from its precheck on, as an agent's run is (agents._acting): its precheck runs the
    # repository's code, outside claude and the gate, so a change of another repository's git settings found while it
    # runs (by the repositories' listing here) is held with its agent, never accepted as the owner's. The precheck
    # waits until the test lets it go, so the listing is read while it runs
    w = _with_other(tmp_path)
    try:
        other = os.path.join(w.root, "repos", "other")
        planted, go = tmp_path / "planted", tmp_path / "go"
        account_file(w)
        put_agent(w, brief="complete done", precheck=f"git -C {other} config core.planted yes && touch {planted} && "
                                                     f"while [ ! -e {go} ]; do sleep 0.1; done")
        run_id = queue(w, identity=CONFIRMED)["run_id"]
        deadline = time.time() + 60
        while not planted.exists():
            assert time.time() < deadline
            time.sleep(0.05)
        assert "changed while an agent's run worked" in (_listed(w, "other").get("held") or "")
        go.write_text("")
        ended = until(w, run_id)
        assert ended["state"] == "done" and "The git settings of the repository other changed" in ended["hold"], ended
    finally:
        w.stop()


def test_the_owner_s_checks_are_his_own_action_and_what_they_change_is_taken_as_his(tmp_path):
    # Run checks is the owner's own action, not the task's agent acting. So a change his checks make to git's places
    # outside the task's repository (here another repository's git settings) is taken as his at the next look, as any
    # change the workspace finds while no agent's run worked; the same change made while an attempt's precheck runs is
    # held with its agent (the test above). The check's code is the returned commit's, the teammate's, and it still runs
    # as the owner's action: he chose to run it.
    w = _with_other(tmp_path)
    try:
        other = os.path.join(w.root, "repos", "other")
        ended = attempt(w, "complete done")
        assert ended["state"] == "done", ended
        head = git("rev-parse", "HEAD", cwd=task_work(w))
        r = w.http.post(f"/agents/tasks/{TASK}/checks",
                        json={"commit": head, "steps": [1], "checks": {"1": f"git -C {other} config core.planted yes"}})
        assert r.status_code == 200 and r.json()["results"][0]["exit"] == 0, r.text
        assert git("config", "core.planted", cwd=other) == "yes"            # the check changed it
        listed = _listed(w, "other")
        assert listed.get("held") is None, listed                          # taken as the owner's, nothing held
        assert w.http.get(f"/agents/{AGENT_ID}").json()["paused"] is False     # its agent is not held for it
    finally:
        w.stop()


def _plants_other(w, tail=""):
    """A precheck that changes the repository `other`'s git settings (and then `tail`, a command's text)."""
    other = os.path.join(w.root, "repos", "other")
    return f"git -C {other} config core.planted yes" + (f" && {tail}" if tail else "")


PLANTED = "The git settings of the repository other changed while an agent's run worked (config core.planted)"


def _refused_by_the_spec():
    """A message's packet that a start's own checks pass (the digest the app sent, a resume packet, the task's first
    fence) and the attempt's spec refuses once its agent's precheck has run: its expect is not a commit's head and
    tree (task_attempt.check_spec). A packet whose digest is not the one sent is refused before the precheck,
    so it does not reach it."""
    packet = _packet(expect={"head": "x", "tree": "y"})
    return {"packet_b64": base64.b64encode(packet).decode(),
            "packet_digest": task_shapes.tagged_digest(task_shapes.RESUME_TAG, packet)}


def test_an_attempt_stopped_while_its_precheck_runs_is_blocked_by_what_the_precheck_changed(tmp_path):
    # the owner's stop while an attempt starts: the app has the run stopped at once. The end of its precheck (cut short
    # where the stop can end it, else run to its end) looks at git's places, and a change of them it made blocks the
    # run (`watch`), the hold's words its reason and its hold, once: the app takes the hold of a run it has as stopped
    # only when it turns blocked, `watch` (proofpane_open/ade/agents.py), as an agent's own run's does
    w = _with_other(tmp_path)
    try:
        planted, go = tmp_path / "planted", tmp_path / "go"
        account_file(w)
        put_agent(w, brief="complete done",
                  precheck=_plants_other(w, f"touch {planted} && while [ ! -e {go} ]; do sleep 0.1; done"))
        run_id = queue(w, identity=CONFIRMED)["run_id"]
        deadline = time.time() + 60
        while not planted.exists():
            assert time.time() < deadline
            time.sleep(0.05)
        stopped = w.http.post(f"/agents/runs/{run_id}/stop")
        assert stopped.status_code == 200 and stopped.json()["state"] == "stopped", stopped.text
        go.write_text("")
        ended = until(w, run_id, ("blocked",))
        assert ended["cause"] == "watch" and ended["reason"].startswith(PLANTED), ended
        assert ended["hold"].startswith(PLANTED) and ended["hold"].count(PLANTED) == 1, ended
        assert _paused_within(w, 10)
    finally:
        w.stop()


def test_an_attempt_refused_after_its_precheck_is_blocked_by_what_the_precheck_changed(tmp_path):
    # its spec refused (a packet whose expect is no commit's, which only the attempt's own check refuses) after its
    # precheck changed another repository's git settings: blocked by them (`watch`), as an agent's own run that did
    # not start is, not refused by its check with the hold beside it, so the app holds the task and pauses the agent
    w = _with_other(tmp_path)
    try:
        account_file(w)
        put_agent(w, brief="complete done", precheck=_plants_other(w))
        ended = until(w, queue(w, identity=CONFIRMED, **_refused_by_the_spec())["run_id"])
        assert ended["state"] == "blocked" and ended["cause"] == "watch", ended
        assert ended["hold"].startswith(PLANTED) and ended["hold"].count(PLANTED) == 1, ended
        assert not claude_starts(w)                     # refused before any claude, after the precheck
        assert _paused_within(w, 10)
    finally:
        w.stop()


def test_an_attempt_whose_spec_cannot_be_made_after_its_precheck_is_blocked_by_what_it_changed(tmp_path):
    # its spec failing to be made (the workspace's own claude settings no longer read) after its precheck changed
    # another repository's git settings: looked at as any end is, and blocked by them
    made = tmp_path / "claude-settings.json"
    made.write_text("{}")
    make_repo(os.path.join(str(tmp_path), "ws", "repos", "other"))
    w = TaskWorkspace(tmp_path, env={"PP_WORKSPACE_CLAUDE_SETTINGS": str(made)})
    try:
        account_file(w)
        put_agent(w, brief="complete done", precheck=_plants_other(w))
        made.write_text("{ no longer settings")
        ended = until(w, queue(w, identity=CONFIRMED)["run_id"])
        assert ended["state"] == "blocked" and ended["cause"] == "watch", ended
        assert ended["hold"].startswith(PLANTED), ended
        assert _paused_within(w, 10)
    finally:
        w.stop()


def _makes_evil(w, marker, tail=""):
    """A precheck that makes a repository `evil` in the workspace's repositories' folder, a hook in it that touches
    `marker` (and then `tail`, a command's text)."""
    evil = os.path.join(w.root, "repos", "evil")
    return (f"git init -q -b main {evil} && git -C {evil} -c user.name=x -c user.email=x@x commit -q --allow-empty -m x"
            f" && printf '#!/bin/sh\\ntouch {marker}\\n' > {evil}/.git/hooks/post-checkout && "
            f"chmod +x {evil}/.git/hooks/post-checkout" + (f" && {tail}" if tail else ""))


EVIL = "The git settings of the repository evil changed while an agent's run worked (all of it: never accepted)"


def test_a_repository_an_attempt_s_precheck_made_is_held_at_its_end_and_after_a_restart(tmp_path):
    # the precheck runs the repository's code: a repository it made in the repositories' folder, which the record of
    # git's places did not know of, is looked at at the attempt's end (every folder git runs in there, _look_at_folders),
    # while the attempt is a suspect, and held with its agent, the attempt blocked by it (here its spec refused after
    # the precheck). The hold is kept in the record, so after the workspace stops and starts again, which counts its
    # suspects from that start, the repository is still held, and the hook planted there never runs
    w = TaskWorkspace(tmp_path)
    try:
        marker = tmp_path / "hook-ran"
        account_file(w)
        put_agent(w, brief="complete done", precheck=_makes_evil(w, marker))
        ended = until(w, queue(w, identity=CONFIRMED, **_refused_by_the_spec())["run_id"])
        assert ended["state"] == "blocked" and ended["cause"] == "watch" and ended["hold"].startswith(EVIL), ended
        w.stop()
        w.start()
        assert "never accepted" in (_listed(w, "evil").get("held") or "")
        made = w.http.post("/sessions", json={"repo": "evil", "program": "shell", "task": "x"})
        assert made.status_code == 400 and not marker.exists(), made.text
    finally:
        w.stop()


def test_a_repository_made_by_a_precheck_the_owner_stopped_is_held_at_the_attempt_s_end(tmp_path):
    # the owner's stop while the precheck runs: whichever way the precheck ends (the stop ending it, or run to its end
    # where the stop cannot), the attempt's end looks at every folder git runs in there, and the repository the precheck
    # made is held, the run blocked by it, so the app takes the hold of a run it has as stopped
    w = TaskWorkspace(tmp_path)
    try:
        marker, made_it, go = tmp_path / "hook-ran", tmp_path / "made", tmp_path / "go"
        account_file(w)
        put_agent(w, brief="complete done",
                  precheck=_makes_evil(w, marker, f"touch {made_it} && while [ ! -e {go} ]; do sleep 0.1; done"))
        run_id = queue(w, identity=CONFIRMED)["run_id"]
        deadline = time.time() + 60
        while not made_it.exists():
            assert time.time() < deadline
            time.sleep(0.05)
        assert w.http.post(f"/agents/runs/{run_id}/stop").json()["state"] == "stopped"
        go.write_text("")
        ended = until(w, run_id, ("blocked",))
        assert ended["cause"] == "watch" and ended["hold"].startswith(EVIL), ended
        made = w.http.post("/sessions", json={"repo": "evil", "program": "shell", "task": "x"})
        assert made.status_code == 400 and not marker.exists(), made.text
    finally:
        w.stop()


def test_a_task_s_precheck_that_says_no_is_looked_at_as_any_end_is(tmp_path):
    # the precheck's end is the attempt's end when it says no: what it started is ended, git's places looked at, and a
    # change of them it made blocks the run (`watch`) and holds its agent, the hold on its record, where it would have
    # been skipped, as an agent's own run's is; with nothing changed it is skipped as before
    w = _with_other(tmp_path)
    try:
        account_file(w)
        put_agent(w, brief="complete done", precheck="exit 3")
        ended = until(w, queue(w, identity=CONFIRMED)["run_id"])
        assert ended["state"] == "skipped" and ended["reason"].startswith("its precheck exited 3"), ended
        assert not ended.get("hold") and not _paused_within(w, 2), ended
        put_agent(w, brief="complete done", precheck=_plants_other(w, "exit 3"))
        ended = until(w, queue(w, identity=CONFIRMED)["run_id"])
        assert ended["state"] == "blocked" and ended["cause"] == "watch", ended
        assert ended["hold"].startswith(PLANTED) and ended["hold"].count(PLANTED) == 1, ended
        assert _paused_within(w, 10)
    finally:
        w.stop()


def _tops(w):
    """The folders git runs in that the record of git's places knows (its sealed file, read as written)."""
    with open(os.path.join(w.root, gitplaces.RECORD), encoding="utf-8") as fh:
        return json.load(fh)["tops"]


def test_the_owner_s_change_before_a_task_s_first_attempt_holds_nothing(tws):
    # the attempt is a suspect only once its own code may run: its start's own git (its worktree's making, the look)
    # runs nothing of the run's, so the owner's own change of the repository's settings just before it is his. Its
    # worktree is known to the record once its first attempt has run (made known when made; the record also takes a
    # folder of the workspace's on first sight, at the git that follows at once, so this does not tell the two apart),
    # and forgotten when the task is removed
    git("config", "core.owner", "yes", cwd=os.path.join(tws.root, "repos", "demo"))
    done = attempt(tws, "complete done")
    assert done["state"] == "done" and done.get("hold") is None, done
    assert tws.http.get(f"/agents/{AGENT_ID}").json()["paused"] is False
    assert task_work(tws) in _tops(tws)
    assert tws.http.delete(f"/agents/tasks/{TASK}").status_code == 200
    assert task_work(tws) not in _tops(tws)


def test_a_route_s_git_where_the_record_holds_the_repository_answers_409_with_its_words(tws):
    # the task's routes run git only where the record of git's places lets it (TaskRuns._places_or_409): a place of
    # its own repository that the task's watch does not look at (objects/info/alternates), changed by a program its
    # attempt ran, holds the repository at that attempt's end; the owner's push then answers 409 with the hold's words,
    # as a changed watched place does, and no git runs (it was a WorkspaceError's 400, and a git of the push's)
    plant = ('printf "/nowhere\\n" > "$(git rev-parse --path-format=absolute --git-common-dir)/objects/info/'
             'alternates"')
    held = attempt(tws, f"write plant.sh={plant} ;; bash sh plant.sh")
    assert "The git settings of the repository demo changed while an agent's run worked (objects/info/alternates)" \
        in (held.get("hold") or ""), held
    head = tws.demo("rev-parse", "HEAD")
    pushed = tws.http.post(f"/agents/tasks/{TASK}/push", json={"address": tws.remote, "commit": head, "since": head,
                                                                "call_id": "c1", "branch_head": head})
    assert pushed.status_code == 409 and pushed.json()["detail"].startswith(
        "The git settings of the repository demo changed while an agent's run worked"), pushed.text
    assert git("ls-remote", tws.remote, f"refs/heads/pp/task-{TASK}", cwd=str(tws.tmp)) == ""


def test_a_task_s_attempt_s_end_is_kept_to_the_moment_and_its_look_named_on_its_record(tmp_path):
    # TaskRuns._run with a stand-in attempt, in this process: what the attempt started is ended and its end kept to
    # the moment (agents._ended_now), so a place looked at after it is not for it, where its record's whole second
    # would count it for most of a second more; and git's places looked at, each hold naming its agent put on the run's
    # record after the task's own watch's words, one closing sentence between them, and the agent held
    import asyncio
    from types import SimpleNamespace
    from test_agents_runs import AGENT, _in_process
    agents, r = _in_process(tmp_path)
    agents._change(r, lambda rec: rec["run"].update(started_at=int(time.time()) - 5))
    looked = []
    agents.ws.places = SimpleNamespace(
        look=lambda agent_id: looked.append(agent_id) or [("/x/.git", "other", ["config core.planted"])])
    view = {"state": "blocked", "cause": "watch", "reason": "the watched places changed while the attempt worked",
            "task": {"watch": {"changed": ["config core.mine"]}, "end_state": None}, "backend": None,
            "rate_limits": None, "question": None}

    class Attempt:
        chat = SimpleNamespace(cost=0.0, last_text="")

        async def run(self):
            return view
    while time.time() % 1 > 0.5:            # early in a second, so the record's end second counts it for the rest
        time.sleep(0.05)
    before = time.time()
    agents.working.add(r)
    asyncio.run(agents.task_runs._run(r, Attempt(), AGENT, TASK))
    after = time.time()
    assert looked == [AGENT]
    assert agents.suspects(before) == [AGENT]           # it worked until its end
    assert agents.suspects(after) == []                 # a place looked at after its end: not for it
    assert agents.read(r)["run"]["hold"] == (
        "The watched places changed while the attempt worked (config core.mine). The git settings of the repository "
        "other changed while an agent's run worked (config core.planted), so no git of the workspace's runs there "
        "until you resume an agent held for it. Look at what changed, then resume the agent.")
    assert agents._held.get(AGENT) == "set"


def test_an_attempt_refused_or_failed_once_running_is_looked_at_and_its_hold_named(tmp_path):
    # TaskRuns._run in this process, its stand-in attempt raising: refused (a check of its own, SpecError) and failed
    # (any other error). Each end looks at git's places, as every end does, its state as before (the app copies the
    # hold of a run that ends, whatever its state) and the hold on its record, its agent held
    import asyncio
    from types import SimpleNamespace
    from test_agents_runs import AGENT, _in_process
    words = (f"{PLANTED}, so no git of the workspace's runs there until you resume an agent held for it. Look at what "
             "changed, then resume the agent.")
    for raised, state, cause in ((task_attempt.SpecError("the packet changed", "packet", {}), "blocked", "resume"),
                                 (RuntimeError("it broke"), "failed", "error")):
        agents, r = _in_process(tmp_path / state)
        agents.ws.places = SimpleNamespace(look=lambda agent_id: [("/x/.git", "other", ["config core.planted"])])

        class Attempt:
            chat = SimpleNamespace(cost=0.0, last_text="")

            async def run(self, raised=raised):
                raise raised
        agents.working.add(r)
        asyncio.run(agents.task_runs._run(r, Attempt(), AGENT, TASK))
        run = agents.read(r)["run"]
        assert (run["state"], run["cause"], run["hold"]) == (state, cause, words), run
        assert agents._held.get(AGENT) == "set"


def test_a_task_s_attempt_working_when_the_machine_stopped_is_a_suspect_at_the_next_start(tmp_path):
    # its record's end second is the next start's scan's, which can be before the record of git's places starts: the
    # scan lists it among the runs that were working (stopped_at_start), a suspect for every place not looked at
    # since that start, as an agent's own run is
    from proofpane_workspace import agents as agentry
    from test_agents_runs import AGENT, _in_process
    agents, r = _in_process(tmp_path)
    agents._change(r, lambda rec: rec["run"].update(state="working", started_at=int(time.time()) - 60,
                                                    trigger={"kind": "task", "id": TASK}))
    again = agentry.Agents(agents.ws)                   # the next start, over the same records
    assert again.task_runs.is_task(r) and again.read(r)["run"]["state"] == "blocked"
    later = time.time() + 10                            # the record of git's places starts after the scan's second
    assert again.suspects(later) == []                  # by its record's second alone, not a suspect
    again.stopped_since(later)
    assert again.suspects(later) == [AGENT]


def test_a_start_gives_the_attempts_working_at_the_stop_the_moment_its_record_began(tmp_path, monkeypatch):
    # the workspace's own start (Workspace.__init__) gives every run that was working when the machine stopped, a task's
    # attempt among them, the moment its record of git's places began (agents.stopped_since). Through a real workspace
    # that start comes within a second of the scan, so a run counted by its record's second alone passes for one
    # there; this start, read in this process, tells the two apart
    from proofpane_workspace import workspace as ws_module
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = str(tmp_path / "ws")
    first = ws_module.Workspace(root)
    first.agents.put({"id": AGENT_ID, "name": "Builder", "brief": "work", "repo": None})
    run_id = first.agents.queue(AGENT_ID)["run_id"]
    first.agents._change(run_id, lambda rec: rec["run"].update(state="working", started_at=int(time.time()) - 60,
                                                              trigger={"kind": "task", "id": TASK}))
    again = ws_module.Workspace(root)                   # the next start, over the same records
    assert run_id in again.agents.stopped_at_start and again.agents.task_runs.is_task(run_id)
    assert again.agents._stopped.get(run_id) == again.places.started


def test_a_task_s_attempt_planting_when_the_machine_stopped_is_held_for_at_the_next_start(tmp_path):
    # an attempt planting in another repository while it works, the machine stopped before its end, and the record of
    # git's places taken away: at the next start nothing is accepted for being there, since an attempt was working
    # when it stopped. With no record nothing is known from before, so every place is held as never accepted, the
    # user's settings first, what was planted among them (so this does not tell the planted hook from the rest, as
    # 59a's own test of a run does not), for that attempt's agent, until the owner resumes it
    w = _with_other(tmp_path)
    try:
        marker = tmp_path / "planted-done"
        plant = ('cd "$(git rev-parse --path-format=absolute --git-common-dir)/../../other" && '
                 f'printf "#!/bin/sh\\n" > .git/hooks/post-checkout && touch {marker}')
        account_file(w)
        put_agent(w, brief=f"write plant.sh={plant} ;; bash sh plant.sh ;; sleep 60")
        run_id = queue(w, identity=CONFIRMED)["run_id"]
        deadline = time.time() + 60
        while not marker.exists():
            assert time.time() < deadline
            time.sleep(0.05)
        w.stop()
        os.unlink(os.path.join(w.root, gitplaces.RECORD))
        w.start()
        until(w, run_id, ("blocked",))
        assert task_work(w) in _tops(w)                 # the start's first look lists the tasks' worktrees
        made = w.http.post("/sessions", json={"repo": "other", "program": "shell", "task": "x"})
        assert made.status_code == 400 and "no record of them from before" in made.json()["detail"], made.text
        resume_agent(w, brief="complete done")
        assert w.http.post("/sessions", json={"repo": "other", "program": "shell", "task": "x"}).status_code == 200
    finally:
        w.stop()


def test_a_recovery_after_a_machine_stop_runs_no_git_where_the_record_holds_the_repository(monkeypatch):
    # TaskRuns.recover settles a stopped attempt's pending checkpoint with git only where git's places let git run
    # there (_places_allow): where the record holds the repository the checkpoint is dropped, as where the task's
    # watched places changed, and no git runs; and the next task's recovery goes on, where a GitHeld from its git had
    # ended the whole recovery. In this process, with stand-ins for the records and for git
    from types import SimpleNamespace
    from proofpane_workspace import workspace as ws_module
    held_task, free_task = "1" * 32, "2" * 32
    records = [{"id": run_id, "agent_id": AGENT_ID,
                "run": {"trigger": {"kind": "task", "id": task_id},
                        "task": {"checkpoints": [{"state": "pending", "tree": "t", "parent": "p"}], "effects": []}}}
               for run_id, task_id in (("r1", held_task), ("r2", free_task))]
    runs = task_runs.TaskRuns(SimpleNamespace(ws=None, runs=lambda: records, agent=lambda agent_id: {"id": agent_id},
                                              _change=lambda run_id, change: change({"run": {}})))
    runs._read_record = lambda agent_id, task_id: {"look": {}}
    runs._current_places = lambda agent, task_id, kept: []
    runs.work = lambda agent_id, task_id: f"/nowhere/{task_id}/work"

    def check_places(where):
        if held_task in where:
            raise ws_module.GitHeld("The git settings of the repository demo changed while an agent's run worked")
    completed = []
    monkeypatch.setattr(ws_module, "check_places", check_places)
    monkeypatch.setattr(task_runs.attempts, "complete_pending",
                        lambda work, branch, entry: completed.append(work) or {"state": "complete", "head": "h"})
    runs.recover()
    assert completed == [f"/nowhere/{free_task}/work"]
    assert [r["run"]["task"]["checkpoints"][0]["state"] for r in records] == ["dropped", "complete"]


def test_the_push_s_secret_scan_runs_no_textconv_and_its_git_is_checked_first(tmp_path, monkeypatch):
    # the scan's git runs as the workspace's own reads do (workspace._git_env), with --no-textconv: a diff driver's
    # textconv the repository's settings give starts nothing, and where the record of git's places holds the
    # repository the scan's git does not run at all
    from proofpane_workspace import workspace as ws_module
    repo = str(tmp_path / "repo")
    since = make_repo(repo)
    mark = tmp_path / "textconv-ran"
    git("config", "diff.x.textconv", f"touch {mark}; cat", cwd=repo)
    with open(os.path.join(repo, ".git", "info", "attributes"), "w") as fh:
        fh.write("* diff=x\n")
    commit_in(repo, "f.txt", "hello\n")
    commit = git("rev-parse", "HEAD", cwd=repo)
    assert git("diff", since, commit, cwd=repo) and mark.exists()   # the control: git's own diff runs it
    mark.unlink()
    assert task_runs.TaskRuns._scan(None, repo, since, commit) is None and not mark.exists()
    checker = gitplaces.Checker(None, b"k" * 32, suspects=lambda since: ["a1"], hold=lambda *a: None,
                                folders=[str(tmp_path)])
    checker.first_look([repo])
    monkeypatch.setitem(ws_module._PLACES, str(tmp_path), checker)
    git("config", "core.planted", "yes", cwd=repo)
    with pytest.raises(ws_module.GitHeld):
        task_runs.TaskRuns._scan(None, repo, since, commit)


def test_a_place_first_seen_at_an_attempt_that_ends_before_its_claude_is_kept_as_it_was_then(tws):
    # an account added between two attempts is watched from the next start on, even where that attempt ends before
    # its end's own look (here its agent's precheck says no): a change to the new account's places after it is found
    done = attempt(tws, "complete done")
    assert done["state"] == "done", done
    spare = tws.http.post("/accounts", json={"kind": "claude", "name": "Spare"}).json()["id"]
    put_agent(tws, brief="complete done", precheck="exit 3")
    skipped = next_attempt(tws, done)
    assert skipped["state"] == "skipped", skipped
    with open(os.path.join(tws.root, "agent-accounts", spare, "claude", ".claude.json"), "w") as fh:
        json.dump({"mcpServers": {"planted": {"command": "true"}}}, fh)
    put_agent(tws, brief="complete done")
    held = next_attempt(tws, skipped)
    assert held["state"] == "blocked" and held["task"]["resume"]["check"] == "watch", held


def test_a_change_the_precheck_makes_is_found_by_the_attempt_s_own_check_4_before_any_git(tws):
    # the start's look comes before the agent's precheck, which may change a watched place itself: the attempt's own
    # check 4 finds what the precheck planted before any git of the attempt's (here git's clean filter, which a tree
    # built from the files would run), so the planted program never runs and no claude starts
    done = attempt(tws, "complete done")
    assert done["state"] == "done", done
    before = len(claude_starts(tws))
    repo = os.path.join(tws.root, "repos", "demo")
    marker = tws.tmp / "filter-ran"
    planted = tws.tmp / "filter.sh"
    planted.write_text(f"#!/bin/sh\ntouch {marker}\ncat\n")
    planted.chmod(0o755)
    attributes = os.path.join(repo, ".git", "info", "attributes")
    put_agent(tws, brief="complete done",
              precheck=f"git -C {repo} config filter.planted.clean {planted} && echo '* filter=planted' > {attributes}")
    held = next_attempt(tws, done)
    assert held["state"] == "blocked" and held["task"]["resume"]["check"] == "watch", held
    assert git("config", "--get", "filter.planted.clean", cwd=repo) == str(planted)     # the precheck ran
    assert not marker.exists()
    assert len(claude_starts(tws)) == before


def test_a_change_its_precheck_made_is_found_by_the_next_start_when_check_3_ended_the_attempt(tws):
    # an attempt check 3 refuses (its account signed out, the relay's ordinary way on) ends before its own check 4, so
    # nothing compares after its precheck; a look its end kept that took in what the precheck had changed would let the
    # next attempt's checks pass, and it would run with what the precheck wrote (here an instruction file a next run
    # loads, which no record of git's places looks at); the precheck plants once, as a precheck that planted at every
    # attempt would make a fresh change each time
    done = attempt(tws, "complete done")
    assert done["state"] == "done", done
    target = tws.home / ".claude" / "CLAUDE.md"
    put_agent(tws, brief="complete done", precheck=f"grep -q planted {target} 2>/dev/null || echo planted >> {target}")
    sign_in = tws.home / ".claude" / "stand-in-account.json"
    signed_in = sign_in.read_bytes() if sign_in.exists() else None
    account_file(tws, signed_out=True)
    refused = next_attempt(tws, done)
    assert refused["state"] == "blocked" and refused["task"]["resume"]["check"] == "backend", refused
    assert "planted" in target.read_text()                                   # the precheck ran
    if signed_in is None:
        sign_in.unlink()
    else:
        sign_in.write_bytes(signed_in)
    held = next_attempt(tws, refused)
    assert held["state"] == "blocked" and held["task"]["resume"]["check"] == "watch", held
    assert tws.http.get(f"/agents/{AGENT_ID}").json()["paused"] is True


def test_the_look_an_attempt_s_end_found_a_change_in_is_the_next_baseline_once_the_owner_resumes(tws):
    # the look an attempt's end took is the next baseline (_keep_end): the owner's one resume after the change it found
    # lets the next attempt run, the change not found again
    held = attempt(tws, "bash d=cla; echo be-quick > ~/.${d}ude/CLAUDE.md ;; complete done")
    assert held["state"] == "blocked" and held["cause"] == "watch", held
    resume_agent(tws, brief="complete done")
    again = next_attempt(tws, held)
    assert again["state"] == "done", again


def test_an_attempt_s_end_keeps_the_look_it_compared_not_one_taken_after_it(tmp_path):
    # with the next baseline a look taken after the attempt's end (after its end state, its effects settled and its
    # memory), a change made in between, by a process the attempt left behind, would be the baseline unseen; a version
    # taking the look again at _finish's own end fails here
    watched = tmp_path / "CLAUDE.md"
    watched.write_text("before\n")
    places = [str(watched)]
    live, up = _checkpointing(tmp_path, watch_places=places, watch_digest=task_attempt.watch_digest(places))

    async def body():
        a = await up()
        a.start_look = task_attempt.look(places)
        compared = task_runs._plain(a.start_look)
        end_state = a._end_state

        async def planted():        # a process the attempt left behind, after the end's look, before its last record
            watched.write_text("planted after the end's look\n")
            return await end_state()
        a._end_state = planted
        await a._finish()
        assert a.view["task"]["watch"]["changed"] == []                  # its end found nothing changed
        assert task_runs._plain(a.end_look) == compared                    # and keeps the look it compared
        assert task_runs._plain(task_attempt.look(places)) != compared     # the control: the file as it is now
    _in_loop(body)


@pytest.mark.parametrize("when", ["check_3", "look"])
def test_an_end_decided_during_check_3_or_check_4_s_look_keeps_no_look(tmp_path, monkeypatch, when):
    # the owner's Stop while check 3 reads auth status, or while check 4's look runs (the look made to wait for it),
    # ends the attempt before its own check 4: it keeps no look, and its end compares none and keeps none, so the next
    # start finds what the precheck changed against the baseline as kept. Here the precheck's change is already in the
    # watched file, and the baseline as kept is another digest
    import asyncio
    watched = tmp_path / "CLAUDE.md"
    watched.write_text("planted by the precheck\n")
    live = _Live(tmp_path, watch_places=[str(watched)], watch_digest="0" * 64)
    real_look = task_attempt.look

    async def body():
        a = await live.up()
        loop = asyncio.get_running_loop()

        def stop_now():
            a.stop(None, "stopped by you")
            asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop).result(5)     # its end decided before this goes on

        def auth_status(claude, env, timeout):
            if when == "check_3":
                stop_now()
            return {"loggedIn": True, "orgId": "org-stand-in"}

        def look(places):
            if when == "look" and a.start_look is None and a.ending is None:
                stop_now()
            return real_look(places)
        monkeypatch.setattr(task_attempt, "_auth_status", auth_status)
        monkeypatch.setattr(task_attempt, "look", look)
        assert await a._checks() is False
        assert a.ending[0] == "stopped" and a.start_look is None
        await a._finish()
        assert a.end_look is None and a.view["task"]["watch"] is None and a.view["state"] == "stopped"
    _in_loop(body)


def test_an_attempt_stopped_during_check_3_leaves_what_its_precheck_did_for_the_next_start_to_find(tws):
    # end to end: the agent's precheck writes a file a next run loads, once; the owner's Stop lands while check 3 reads
    # auth status. With check 3 still passing, check 4 would take its look with the change in it, and the attempt's end
    # would keep that look as the baseline, so the next attempt would run with what the precheck wrote. Here the task's
    # claude is a wrapper whose next auth status, once armed, waits for the stop
    import threading
    done = attempt(tws, "complete done")
    assert done["state"] == "done", done
    real, arm, waiting, go = tws.bin / "claude-stand-in", tws.tmp / "arm", tws.tmp / "waiting", tws.tmp / "go"
    os.rename(tws.bin / "claude", real)
    (tws.bin / "claude").write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = auth ] && [ "$2" = status ] && mv {arm} {arm}.taken 2>/dev/null; then\n'
        f"  : > {waiting}\n"
        f"  i=0; while [ ! -e {go} ] && [ $i -lt 250 ]; do sleep 0.1; i=$((i+1)); done\n"
        "fi\n"
        f'exec {real} "$@"\n')
    (tws.bin / "claude").chmod(0o755)
    target = tws.home / ".claude" / "CLAUDE.md"
    put_agent(tws, brief="complete done", precheck=f"grep -q planted {target} 2>/dev/null || echo planted >> {target}")
    arm.write_text("")
    expect = expect_of(done)
    packet = _packet(expect=expect)
    run_id = queue(tws, fence=done["task"]["fence"] + 1, attempt=done["task"]["attempt"] + 1, identity=CONFIRMED,
                   packet_b64=base64.b64encode(packet).decode(),
                   packet_digest=task_shapes.tagged_digest(task_shapes.RESUME_TAG, packet))["run_id"]
    tws.expected[run_id] = expect
    deadline = time.time() + 60
    while not waiting.exists():
        assert time.time() < deadline, run_view(tws, run_id)
        time.sleep(0.05)
    stopped = []
    stopper = threading.Thread(target=lambda: stopped.append(tws.http.post(f"/agents/runs/{run_id}/stop")))
    stopper.start()
    time.sleep(2)                   # the stop reaches the attempt before check 3's auth status answers
    go.write_text("")
    stopper.join(60)
    assert stopped and stopped[0].status_code == 200, stopped
    ended = until(tws, run_id)
    assert ended["state"] == "stopped", ended
    assert "planted" in target.read_text()                                   # the precheck ran
    held = next_attempt(tws, ended)
    assert held["state"] == "blocked" and held["task"]["resume"]["check"] == "watch", held
    assert tws.http.get(f"/agents/{AGENT_ID}").json()["paused"] is True


@pytest.mark.parametrize("held", [True, False])
def test_an_end_state_a_held_repository_refuses_ends_the_attempt_by_the_watch(tmp_path, held):
    # the end state's commit refused by the record of git's places (GitHeld) ends the attempt as a run that changed
    # git's places does, by the watch, its hold saying why, not blocked with cause "machine", which the app tells the
    # owner as a machine stop to Resolve, while the agent is held and only the owner's resume lifts it. Anything else
    # that stops the commit is still the machine's
    from proofpane_workspace import workspace as ws_module
    words = "The git settings of the repository demo changed while an agent's run worked"
    other = ws_module.WorkspaceError("git commit failed: no space left on device")
    raised = ws_module.GitHeld(words) if held else other
    live, up = _checkpointing(tmp_path)

    async def body():
        a = await up()

        async def refused():
            raise raised
        a._end_state = refused
        await a._finish()
        assert a.view["state"] == "blocked" and a.view["cause"] == ("watch" if held else "machine"), a.view
        assert a.view["reason"] == f"the attempt's end state could not be committed: {raised}"
        hold = task_runs._watch_hold(a.view)
        if held:
            assert hold.startswith(f"The attempt's end state could not be committed: {words}") and \
                hold.endswith(task_runs.WATCH_HOLD), hold
        else:
            assert hold is None
    _in_loop(body)


def test_a_task_s_attempt_s_end_keeps_the_look_its_end_compared_and_nothing_where_it_took_none(tmp_path):
    # TaskRuns._run with a stand-in attempt, in this process: the look the attempt's end compared is the task's next
    # baseline, never one taken when _run keeps it; an attempt that took no look, or a stand-in with no end look at all,
    # keeps nothing
    import asyncio
    from types import SimpleNamespace
    from test_agents_runs import AGENT, _in_process
    watched = tmp_path / "CLAUDE.md"
    watched.write_text("before\n")
    places = [str(watched)]
    view = {"state": "done", "cause": None, "reason": None, "task": {"watch": {"changed": []}, "end_state": None},
            "backend": None, "rate_limits": None, "question": None}

    def ended_with(name, **attempt_has):
        agents, r = _in_process(tmp_path / name)
        agents.ws.places = SimpleNamespace(look=lambda agent_id: [])
        runs = agents.task_runs
        os.makedirs(runs.folder(AGENT, TASK))
        runs._write_record({"agent_id": AGENT, "task_id": TASK, "places": places, "look": {"kept": "before it"}})

        class Attempt:
            chat = SimpleNamespace(cost=0.0, last_text="")

            async def run(self):
                return view
        attempt = Attempt()
        for key, value in attempt_has.items():
            setattr(attempt, key, value)
        agents.working.add(r)
        asyncio.run(runs._run(r, attempt, AGENT, TASK))
        return runs._read_record(AGENT, TASK)["look"]

    end_look = task_attempt.look(places)
    watched.write_text("planted after the end's look\n")         # by a process the attempt left behind
    assert ended_with("compared", end_look=end_look) == dict({"kept": "before it"}, **task_runs._plain(end_look))
    assert ended_with("none", end_look=None) == {"kept": "before it"}
    assert ended_with("stand-in") == {"kept": "before it"}


def test_a_change_between_attempts_is_found_by_the_next_start_s_checks(tws):
    # the person's own excludes, named by core.excludesFile outside ~/.config/git, set before the task is made
    excludes = tws.tmp / "my-excludes"
    excludes.write_text("*.log\n")
    (tws.home / ".gitconfig").write_text(f"[core]\n\texcludesFile = {excludes}\n")
    # and the default account's .claude.json, as Claude Code keeps it, there from the start
    (tws.home / ".claude.json").write_text(json.dumps({"numStartups": 3,
                                                       "oauthAccount": {"emailAddress": "stand-in@example.com"}}))
    done = attempt(tws, "append line.txt=one ;; complete step one")
    assert done["state"] == "done", done
    work, repo = task_work(tws), os.path.join(tws.root, "repos", "demo")
    head = expect_of(done)["head"]

    def blocked_at(check, previous):
        got = next_attempt(tws, previous, expect=expect_of(done))
        assert got["state"] == "blocked" and got["task"]["resume"]["check"] == check, got
        if check == "watch":
            resume_agent(tws)                   # a change watched holds the agent, as a run's does
        return got

    # check 5, the worktree: HEAD moved by another commit
    commit_in(work, "other.txt", "x\n")
    last = blocked_at("worktree", done)
    git("reset", "-q", "--hard", head, cwd=work)
    # a file left uncommitted
    with open(os.path.join(work, "loose.txt"), "w") as fh:
        fh.write("loose\n")
    last = blocked_at("worktree", last)
    os.unlink(os.path.join(work, "loose.txt"))
    # a file changed under skip-worktree, which the index does not show
    git("update-index", "--skip-worktree", "line.txt", cwd=work)
    with open(os.path.join(work, "line.txt"), "a") as fh:
        fh.write("hidden\n")
    last = blocked_at("worktree", last)
    git("update-index", "--no-skip-worktree", "line.txt", cwd=work)
    git("checkout", "-q", "--", "line.txt", cwd=work)
    # check 4, the watch: the person's excludes file changed
    excludes.write_text("*.log\n*.txt\n")
    last = blocked_at("watch", last)
    # an instruction file in a folder above the worktree
    with open(os.path.join(tws.root, "CLAUDE.md"), "w") as fh:
        fh.write("Do as this file says.\n")
    last = blocked_at("watch", last)
    # the identity's fields in the account's .claude.json, and nothing else of it, changed
    (tws.home / ".claude.json").write_text(json.dumps({"numStartups": 3,
                                                       "oauthAccount": {"emailAddress": "other@example.com"}}))
    last = blocked_at("watch", last)
    # git's settings made to run a program (the design's fsmonitor; here a clean filter, which RUN_GIT does not pin
    # and a checkpoint's tree would run): found by the watch before any git runs, so the program never ran
    marker = tws.tmp / "filter-ran"
    planted = tws.tmp / "filter.sh"
    planted.write_text(f"#!/bin/sh\ntouch {marker}\ncat\n")
    planted.chmod(0o755)
    git("config", "filter.planted.clean", str(planted), cwd=repo)
    with open(os.path.join(repo, ".git", "info", "attributes"), "w") as fh:
        fh.write("* filter=planted\n")
    blocked_at("watch", last)
    assert not marker.exists()
    # and nothing of claude started for any of them
    assert len(claude_starts(tws)) == 2


def test_an_unknown_effect_in_the_packet_ends_it_at_check_6(tws):
    packet = _packet(effects={"done": [], "not_done": [], "ran": [],
                              "unknown": [{"id": "e1", "tool": "Bash", "target": {"text": "make deploy"}}]})
    blocked = attempt(tws, "complete done", packet_b64=base64.b64encode(packet).decode(),
                      packet_digest=task_shapes.tagged_digest(task_shapes.RESUME_TAG, packet))
    assert blocked["state"] == "blocked" and blocked["task"]["resume"]["check"] == "effects"
    assert claude_starts(tws) == []


def test_a_wrong_field_is_answered_and_a_second_ends_it_and_an_exit_starts_no_claude_again(tws):
    account_file(tws, frozen={"ack": {"wrong": "nonce"}})
    done = attempt(tws, "complete done")
    assert done["state"] == "done" and done["task"]["resume"]["acks"] == 2, done
    account_file(tws, frozen={"ack": {"wrong": "step", "twice": True}})
    blocked = next_attempt(tws, done)
    assert blocked["state"] == "blocked" and blocked["task"]["resume"]["check"] == "ack"
    assert blocked["task"]["resume"]["found"] == {"field": "step", "tries": 2}
    before, heard = len(claude_starts(tws)), len(jsonl(tws, "stand-in-received.jsonl"))
    account_file(tws, frozen={"ack": "exit"})
    blocked = next_attempt(tws, blocked)
    assert blocked["task"]["resume"]["check"] == "ack"
    assert len(claude_starts(tws)) == before + 1        # its frozen claude, and none again
    assert not any(m["text"] == task_attempt.RESUMED for m in jsonl(tws, "stand-in-received.jsonl")[heard:])


def test_a_start_report_the_same_everywhere_or_named_by_settings_holds_it_for_the_owner(tws):
    account_file(tws, token_source="CLAUDE_CODE_OAUTH_TOKEN")
    blocked = attempt(tws, "complete done")
    assert blocked["task"]["start"]["failure"] == "same_everywhere" and blocked["task"]["resume"]["check"] == "start"
    assert jsonl(tws, "stand-in-received.jsonl") == []
    # each report at the frozen start, classed: the account's own, or what every account would meet
    for values, failure in (({"organization": "Acme", "org_name": "Acme"}, "account"),
                            ({"api_key_source": task_attempt.MANAGED_KEY}, "account"),
                            ({"api_provider": "bedrock"}, "same_everywhere"),
                            ({"no_account": True}, "same_everywhere"),
                            ({"no_email": True}, "same_everywhere")):
        account_file(tws, **values)
        blocked = next_attempt(tws, blocked)
        assert (blocked["task"]["start"]["failure"], blocked["task"]["resume"]["check"]) == (failure, "start"), values
    assert jsonl(tws, "stand-in-received.jsonl") == []       # each before its first message
    # a start message with no credential source, read at the first turn's start
    account_file(tws, init_api_key_source=None)
    blocked = next_attempt(tws, blocked)
    assert (blocked["task"]["start"]["failure"], blocked["task"]["resume"]["check"]) == ("same_everywhere", "start")
    # at the unfreezing restart only: the settings' failure, before "Resume check passed" is sent, for each report of
    # the restart's initialize answer
    for values in ({"api_key_source": "ANTHROPIC_API_KEY"}, {"email": "other@example.com"},
                   {"organization": "Acme", "org_name": "Acme"}, {"api_key_source": task_attempt.MANAGED_KEY},
                   {"token_source": "CLAUDE_CODE_OAUTH_TOKEN"}, {"api_provider": "bedrock"}, {"no_account": True},
                   {"no_email": True}):
        account_file(tws, restart=values)
        blocked = next_attempt(tws, blocked)
        assert (blocked["task"]["start"]["failure"], blocked["task"]["resume"]["check"]) == ("settings", "settings"), \
            values
        assert not any(r["text"] == task_attempt.RESUMED for r in jsonl(tws, "stand-in-received.jsonl")), values
    # and its start message with another credential source: the settings' failure too, read from the first turn's start
    # message, which Claude Code sends only with that turn, so after "Resume check passed"
    account_file(tws, restart={"init_api_key_source": "ANTHROPIC_API_KEY"})
    blocked = next_attempt(tws, blocked)
    assert (blocked["task"]["start"]["failure"], blocked["task"]["resume"]["check"]) == ("settings", "settings")
    assert blocked["task"]["start"]["api_key_source"] == "ANTHROPIC_API_KEY"


def test_an_account_s_identity_is_read_by_a_frozen_start_that_sends_no_message(tws):
    account_file(tws, organization="Acme", org_name="Acme", plan="Claude Team", auth_plan="team", org_id="org-acme")
    got = tws.http.post("/agents/identity", json={"account": "default"}).json()
    assert got["report"]["email"] == "stand-in@example.com" and got["report"]["organization"] == "Acme"
    assert got["org_id"] == "org-acme" and got["mismatch"] is None
    assert jsonl(tws, "stand-in-received.jsonl") == []
    account_file(tws, organization="Acme", org_name="Other", plan="Claude Team", auth_plan="team")
    assert tws.http.post("/agents/identity", json={"account": "default"}).json()["mismatch"] == "organization"
    # no orgId, and the confirmation fails saying so
    account_file(tws, organization="Acme", org_name="Acme", plan="Claude Team", auth_plan="team", org_id=None)
    got = tws.http.post("/agents/identity", json={"account": "default"}).json()
    assert got["mismatch"] == "orgId" and got["org_id"] is None
    assert tws.http.post("/agents/identity", json={"account": "0123456789ab"}).status_code == 404
    account_file(tws, signed_out=True)
    assert tws.http.post("/agents/identity", json={"account": "default"}).status_code == 400


def test_a_file_a_next_run_loads_written_by_a_built_name_holds_the_task_at_the_end(tws):
    held = attempt(tws, "write .claude/settings.local.json={} ;; "
                        "bash d=cla; mkdir -p .${d}ude && echo {} > .${d}ude/settings.local.json ;; complete done")
    assert held["state"] == "blocked" and held["cause"] == "watch", held
    assert ".claude/settings.local.json" in held["task"]["watch"]["changed"]
    calls = jsonl(tws, "stand-in-calls.jsonl")
    assert [c["denied"] for c in calls if c["name"] == "Write"] == [True]      # the named write: refused by the gate
    # ~/.claude/CLAUDE.md, a place watched outside the worktree, written by a name the shell builds: found by
    # complete_step's own look before any git, so no checkpoint is made
    resume_agent(tws, brief="bash d=cla; echo be-quick > ~/.${d}ude/CLAUDE.md ;; complete done")
    held = next_attempt(tws, held)
    assert held["state"] == "blocked" and held["cause"] == "watch", held
    assert (tws.home / ".claude" / "CLAUDE.md").read_text() == "be-quick\n"
    assert any(p.endswith("CLAUDE.md") for p in held["task"]["watch"]["changed"]), held["task"]["watch"]
    assert held["task"]["checkpoints"] == []
    # and by the look at the attempt's end, when no checkpoint was asked for
    resume_agent(tws, brief="bash d=cla; echo be-slow > ~/.${d}ude/CLAUDE.md")
    held = next_attempt(tws, held)
    assert held["state"] == "blocked" and held["cause"] == "watch", held
    assert (tws.home / ".claude" / "CLAUDE.md").read_text() == "be-slow\n"


def test_an_instruction_file_below_the_root_or_one_the_root_s_imports_changed_holds_the_task_at_the_end(tws):
    # an attempt writing src/CLAUDE.md or a file the root CLAUDE.md imports, each holding the task at that attempt's
    # end; the imported one is no instruction file by its name, only by the import
    demo = os.path.join(tws.root, "repos", "demo")
    os.makedirs(os.path.join(demo, "docs"))
    with open(os.path.join(demo, "docs", "rules.md"), "w") as fh:
        fh.write("be careful\n")
    commit_in(demo, "CLAUDE.md", "see @docs/rules.md\n", message="instructions")
    held = attempt(tws, "bash echo be-careless >> docs/rules.md ;; complete done")
    assert held["state"] == "blocked" and held["cause"] == "watch", held
    assert held["task"]["watch"]["changed"] == ["docs/rules.md"], held["task"]["watch"]
    resume_agent(tws, brief="bash mkdir -p src && echo be-quick > src/CLAUDE.md ;; complete done")
    held = next_attempt(tws, held)
    assert held["state"] == "blocked" and held["cause"] == "watch", held
    assert held["task"]["watch"]["changed"] == ["src/CLAUDE.md"], held["task"]["watch"]


def test_an_unattended_attempt_hearing_a_weekly_warning_ends_before_its_work(tws):
    account_file(tws, first_answer="ratelimit allowed_warning seven_day_opus")
    blocked = attempt(tws, "append line.txt=one ;; complete done", chosen_by="relay")
    assert blocked["state"] == "blocked" and blocked["reason"].startswith("weekly reserve"), blocked
    assert not os.path.exists(os.path.join(task_work(tws), "line.txt"))


def test_a_press_goes_through_check_3_and_its_weekly_warning(tws):
    account_file(tws, first_answer="ratelimit allowed_warning seven_day")
    done = attempt(tws, "complete done", chosen_by="owner")            # the owner's press: not unattended
    assert done["state"] == "done", done
    # and the relay's attempt once the owner lifted the reserve
    done = next_attempt(tws, done, chosen_by="relay", reserve=True)
    assert done["state"] == "done", done


def test_a_claude_that_is_not_the_version_ends_the_attempt_blocked_naming_it(tmp_path):
    w = TaskWorkspace(tmp_path, config={"VERSION": "2.1.300"})
    try:
        blocked = attempt(w, "complete done")
        assert blocked["state"] == "blocked" and blocked["task"]["resume"]["check"] == "start"
        assert blocked["task"]["resume"]["found"] == {"claude_version": "2.1.300"}
        assert claude_starts(w) == []
    finally:
        w.stop()


def test_a_repository_s_settings_cannot_unpin_the_attempt_and_the_gate_still_refuses(tws):
    work_settings = {"env": {"CLAUDE_CODE_SHELL_PREFIX": "/bin/true", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "0",
                             "CLAUDE_CODE_SIMPLE": "1", "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "0",
                             "LD_PRELOAD": "./carried.so", "SHELL": "/bin/repository-shell",
                             "PATH": "/repository/bin"},
                     "advisorModel": "opus"}
    tws.demo("checkout", "-q", "main")
    os.makedirs(os.path.join(tws.root, "repos", "demo", ".claude"), exist_ok=True)
    with open(os.path.join(tws.root, "repos", "demo", ".claude", "settings.json"), "w") as fh:
        json.dump(work_settings, fh)
    tws.demo("add", "-A")
    tws.demo("commit", "-qm", "a repository's settings")
    (tws.home / ".claude").mkdir(exist_ok=True)
    (tws.home / ".claude" / "settings.json").write_text(json.dumps({"env": {"CLAUDE_CODE_SIMPLE": "1"}}))
    done = attempt(tws, "read ~/.claude/.credentials.json ;; complete done")
    assert done["state"] == "done", done
    resumed = claude_starts(tws)[-1]
    effective = resumed["effective"]
    for name, value in task_attempt.ATTEMPT_ENV.items():
        assert effective.get(name) == value, name
    # each to the workspace process's own value, which the repository's settings name otherwise: SHELL and PATH as
    # TaskWorkspace starts the workspace agent, the loader's names unset there
    own = {"SHELL": "/bin/sh", "PATH": f"{tws.bin}:{os.environ['PATH']}", "LD_PRELOAD": "", "LD_AUDIT": "",
           "LD_LIBRARY_PATH": ""}
    for name, value in own.items():
        assert effective.get(name) == value, name
    assert resumed["bare"] is False and resumed["advisor_on"] is False and resumed["auto_memory"] is False
    reads = [c for c in jsonl(tws, "stand-in-calls.jsonl") if c["name"] == "Read"]
    assert reads and all(c["denied"] for c in reads)


def test_an_agent_s_own_run_s_environment_is_release_59_s(tws):
    # an agent's own run's environment as it was before tasks: none of a task's attempt's names or pins in an agent's
    # own run, and the ones it had then
    put_agent(tws, brief="hello")
    r = tws.http.post(f"/agents/{AGENT_ID}/runs", json={"trigger": {"kind": "manual", "prompt": None}})
    assert r.status_code == 200, r.text
    assert until(tws, r.json()["run_id"])["state"] == "done"
    (start,) = claude_starts(tws)
    assert start["attempt"] is False and start["frozen"] is False
    assert not {"PP_TASK_ATTEMPT", "PP_FROZEN"} & set(start["env"])
    assert {"PP_AGENT_RUN", "PP_AGENT_ID", "PP_AGENT_DIR"} <= set(start["env"])
    assert start["effective"].get("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS") == "1"
    assert not set(start["effective"]) & (set(task_attempt.ATTEMPT_ENV) - {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"}
                                          | {"LD_PRELOAD", "LD_AUDIT", "LD_LIBRARY_PATH"})


def test_a_server_managed_source_replacing_the_managed_settings_leaves_the_callbacks_refusing(tmp_path):
    # the workspace's own claude settings as the local provider makes them, the gate in exec form, which the attempt's
    # own --settings carry; the stand-in runs its settings' PreToolUse command hooks after the callbacks
    made = tmp_path / "claude-settings.json"
    made.write_text(json.dumps(workspace_settings.claude_settings(sys.executable, workspace_settings.HERE,
                                                                  managed=False)))
    w = TaskWorkspace(tmp_path, env={"PP_WORKSPACE_CLAUDE_SETTINGS": str(made)})
    try:
        (w.home / "stand-in-command-hooks").write_text("")
        account_file(w, frozen={"try": ["Read"], "ack": "right"})
        # the control: with no server-managed source the gate there runs for each Read, at the frozen start and after
        # the restart, and refuses it as the callbacks do
        first = attempt(w, "read ~/.claude/.credentials.json ;; complete done")
        assert first["state"] == "done", first
        reads = [c for c in jsonl(w, "stand-in-calls.jsonl") if c["name"] == "Read"]
        assert len(reads) == 2 and all(c["denied"] and not c["ran"] for c in reads), reads
        assert all(len(c["command_hooks"]) == 1 and c["command_hooks"][0][-1].endswith("gate.py") for c in reads)
        ran = jsonl(w, "stand-in-hooks.jsonl")
        assert ran and {h["source"] for h in ran} == {"flag"}
        assert {s["managed"] for s in claude_starts(w)} == {None}
        # a seat's server-managed settings replace the managed file, and with allowManagedHooksOnly no other source's
        # command hook runs: the gate in the run's --settings is gone, and the attempt's own callbacks still refuse
        (w.home / "stand-in-server-managed.json").write_text(json.dumps({"allowManagedHooksOnly": True}))
        for name in ("stand-in-calls.jsonl", "stand-in-hooks.jsonl", "stand-in-runs.jsonl"):
            (w.home / name).unlink()
        second = next_attempt(w, first)
        assert second["state"] == "done", second
        reads = [c for c in jsonl(w, "stand-in-calls.jsonl") if c["name"] == "Read"]
        assert len(reads) == 2 and all(c["denied"] and not c["ran"] and c["command_hooks"] == [] for c in reads), reads
        assert jsonl(w, "stand-in-hooks.jsonl") == []
        assert {s["managed"] for s in claude_starts(w)} == {"server"}
    finally:
        w.stop()


def test_the_stand_in_s_refusals_before_hooks_end_nothing_and_its_unseen_calls_end_it(tws):
    # an Edit whose old string is not in the file and an unknown tool are refused before their hooks; the one measured
    # Write over an existing file not read first was no such refusal: 2.1.274 ran it with both PreToolUse callbacks and
    # a PostToolUse, and the stand-in runs such a Write so: here a local write, which ends nothing
    done = attempt(tws, "edit README.md=nothing-like-this=>x ;; write README.md=over ;; tool NoSuchTool {} ;; "
                        "endconv")
    assert done["state"] == "done" and not done["task"]["effects"], done
    calls = {c["name"]: c for c in jsonl(tws, "stand-in-calls.jsonl")}
    for refused in ("Edit", "NoSuchTool"):
        assert calls[refused]["refused"] == "before_hooks" and calls[refused]["pre"] == [], calls[refused]
    write = calls["Write"]
    assert write["ran"] and not write["error"] and len(write["pre"]) == 2 and write["post"] == "PostToolUse", write
    # each way a call runs outside the gate ends it ungated, the call an unknown effect by its tool and target: run
    # with no callback at all; with its post callback but not its PreToolUse ones (here failing, which only the post
    # callback shows), or with one of the two only, the gate's or the intent's; after a deny lost on its way back
    previous = done
    for brief, tool, target in (("unseen bash echo ran unseen", "Bash", "echo"),
                                ("nopre bash exit 3", "Bash", "exit"),
                                ("onepre bash echo half-seen", "Bash", "echo"),
                                ("lastpre bash echo half-seen", "Bash", "echo"),
                                ("lostdeny read ~/.claude/.credentials.json", "Read", "Read")):
        put_agent(tws, brief=brief)
        ungated = next_attempt(tws, previous)
        assert ungated["state"] == "blocked" and ungated["cause"] == task_attempt.UNGATED, (brief, ungated)
        assert [(e["tool"], e["target"], e["unseen"], e["settled"]) for e in ungated["task"]["effects"]] == \
            [(tool, target, True, "unknown")], brief
        previous = ungated


def test_bare_mode_by_the_stand_in_ends_it_and_skipped_submission_hooks_do_not(tws):
    # bare mode's first call fails, so its result alone would read as a refusal before hooks: the turn ends it
    blocked = attempt(tws, "noups ;; bash exit 3")
    assert blocked["cause"] == task_attempt.UNGATED, blocked
    assert [(e["tool"], e["unseen"]) for e in blocked["task"]["effects"]] == [("Bash", True)]
    # a turn whose submission hooks were skipped, its later call refused before any hook (an Edit of a string the file
    # does not hold, an error result no callback saw): not the turn's first call, so it ends nothing. The stand-in's
    # skiphooks models a turn no run of 2.1.274 was measured making: a turn sent as a command came with
    # UserPromptSubmit, so the rule this tests stays a defence
    put_agent(tws, brief="skiphooks ;; bash echo fine ;; edit README.md=nothing-like-this=>x ;; complete done")
    done = next_attempt(tws, blocked)
    assert done["state"] == "done" and not done["task"]["effects"], done


def test_a_batch_the_interrupt_cuts_through_makes_each_call_no_pretooluse_callback_saw_unknown(tws):
    # a batch of three calls the workspace's interrupt cuts through, end to end: the first runs unseen and ends the
    # attempt; the workspace interrupts at its result, and the stand-in runs none of the batch's later calls, each an
    # error result with no hook. 2.1.274 was measured doing that for an interrupt sent from the first call's PreToolUse
    # callback, the one moment measured, not for this later one. The batch rule still makes each other call no
    # PreToolUse callback saw an unknown effect, a defence whose cost is an unknown effect the owner resolves for a call
    # that may not have run
    blocked = attempt(tws, "batch unseen bash echo one || bash echo two || bash exit 3")
    assert blocked["state"] == "blocked" and blocked["cause"] == task_attempt.UNGATED, blocked
    assert [(e["tool"], e["target"], e["unseen"], e["settled"]) for e in blocked["task"]["effects"]] == \
        [("Bash", "echo", True, "unknown"), ("Bash", "echo", True, "unknown"), ("Bash", "exit", True, "unknown")]
    calls = [c for c in jsonl(tws, "stand-in-calls.jsonl") if c["name"] == "Bash"]
    assert [(c["ran"], c["pre"], c["post"], c["error"], c["refused"]) for c in calls] == \
        [(True, [], None, False, None), (False, [], None, True, "interrupt"), (False, [], None, True, "interrupt")]


def test_a_batch_s_call_whose_pretooluse_callbacks_came_after_the_interrupt_ran_not_and_is_left(tws):
    # A measured batch's later Reads had their PreToolUse callbacks between the interrupt's sending and its end, and did
    # not run. Such a call is one a PreToolUse callback saw, so the batch rule leaves it, and the stand-in cuts it; it
    # is the one case that tells reading the batch at the turn's end from reading it at the ungated call, where it had
    # no callback yet
    blocked = attempt(tws, "batch unseen bash echo one || inflight bash echo two || bash exit 3")
    assert blocked["state"] == "blocked" and blocked["cause"] == task_attempt.UNGATED, blocked
    assert [(e["tool"], e["target"], e["unseen"], e["settled"]) for e in blocked["task"]["effects"]] == \
        [("Bash", "echo", True, "unknown"), ("Bash", "exit", True, "unknown")]
    calls = [c for c in jsonl(tws, "stand-in-calls.jsonl") if c["name"] == "Bash"]
    assert [(c["ran"], len(c["pre"]), c["post"], c["refused"]) for c in calls] == \
        [(True, 0, None, None), (False, 2, None, "interrupt"), (False, 0, None, "interrupt")]


def test_in_a_turn_with_every_hook_off_a_batch_s_inflight_call_calls_no_callback_and_is_counted(tws):
    # In a turn whose hooks are all off (noups, as bare mode), the stand-in calls no callback of an inflight call
    # either, so the batch rule counts it as it counts the others no callback saw
    blocked = attempt(tws, "noups ;; batch unseen bash echo one || inflight bash echo two || bash exit 3")
    assert blocked["state"] == "blocked" and blocked["cause"] == task_attempt.UNGATED, blocked
    assert [(e["tool"], e["target"], e["unseen"]) for e in blocked["task"]["effects"]] == \
        [("Bash", "echo", True), ("Bash", "echo", True), ("Bash", "exit", True)]
    calls = [c for c in jsonl(tws, "stand-in-calls.jsonl") if c["name"] == "Bash"]
    assert [(c["ran"], c["pre"], c["refused"]) for c in calls] == \
        [(True, [], None), (False, [], "interrupt"), (False, [], "interrupt")]


def test_a_batch_s_call_a_pretooluse_callback_saw_is_not_made_unknown_with_its_unseen_neighbour(tws):
    # the batch rule takes only the calls no PreToolUse callback saw; the first call here went through both callbacks
    # and ran, the second ran unseen
    blocked = attempt(tws, "batch bash echo seen || unseen bash echo unseen")
    assert blocked["state"] == "blocked" and blocked["cause"] == task_attempt.UNGATED, blocked
    assert [(e["tool"], e["unseen"]) for e in blocked["task"]["effects"]] == [("Bash", True)]
    seen, unseen = [c for c in jsonl(tws, "stand-in-calls.jsonl") if c["name"] == "Bash"]
    assert len(seen["pre"]) == 2 and seen["ran"] and unseen["pre"] == [] and unseen["ran"]


def test_a_server_tool_s_block_is_not_counted(tws):
    done = attempt(tws, "servertool ;; complete done")
    assert done["state"] == "done" and done["task"]["effects"] == [], done


def test_claude_closing_its_channel_is_ungated(tws):
    blocked = attempt(tws, "close")
    assert blocked["state"] == "blocked" and blocked["cause"] == task_attempt.UNGATED, blocked
    assert blocked["reason"] == "the control channel closed other than by the workspace"


def test_a_call_the_agent_s_rules_leave_to_ask_still_asks(tws):
    # the owner's press runs in the agent's own permission mode, here one that asks: the intent hook, which writes an
    # intent for this call (no class reads it as local), decides nothing
    put_agent(tws, brief="bash make asked ;; complete done", chat_settings={"permission_mode": "default"})
    account_file(tws)
    run_id = queue(tws, identity=CONFIRMED, chosen_by="owner")["run_id"]
    waiting = until(tws, run_id, states=("waiting", "done", "failed", "blocked", "stopped"))
    assert waiting["state"] == "waiting", waiting
    assert [(e["tool"], e["kind"]) for e in waiting["task"]["effects"]] == [("Bash", "unclassified")]
    assert not any(c["ran"] for c in jsonl(tws, "stand-in-calls.jsonl") if c["name"] == "Bash")
    assert tws.http.post(f"/agents/runs/{run_id}/stop").status_code == 200
    assert until(tws, run_id)["state"] == "stopped"


def test_the_account_a_working_attempt_runs_on_is_not_removed_while_it_works(tws):
    import time
    calls = tws.tmp / "codex-calls"
    signed_in_codex(tws.bin, calls)
    assert shutil.which("codex", path=f"{tws.bin}:{os.environ['PATH']}") == str(tws.bin / "codex")
    r = tws.http.post("/accounts", json={"kind": "claude", "name": "Work"})
    assert r.status_code == 200, r.text
    account = r.json()["id"]
    account_file(tws, folder=os.path.join(tws.root, "agent-accounts", account, "claude"))
    put_agent(tws, brief="sleep 30 ;; complete done")
    run_id = queue(tws, identity=CONFIRMED, backend={"kind": "account", "id": account})["run_id"]
    deadline = time.time() + 60
    while not any(m["text"] == task_attempt.RESUMED for m in jsonl(tws, "stand-in-received.jsonl")):
        assert time.time() < deadline, run_view(tws, run_id)
        time.sleep(0.1)
    assert run_view(tws, run_id)["state"] == "working"
    # refused while the attempt works on it, in the words a session's gets
    r = tws.http.delete(f"/accounts/{account}")
    assert r.status_code == 400 and r.json()["detail"] == "1 session runs on Work: remove it first."
    view = tws.http.get("/accounts").json()
    assert view["claude"]["accounts"][-1]["id"] == account
    # each Codex row says what this test's codex answered
    signed = {"installed": True, "signed_in": True, "method": "chatgpt"}
    rows = view["codex"]["accounts"]
    assert rows and all(row["status"] == signed for row in rows), [row["status"] for row in rows]
    assert tws.http.post(f"/agents/runs/{run_id}/stop").status_code == 200
    assert until(tws, run_id)["state"] == "stopped"
    # once it has ended, the account is no longer held by it
    r = tws.http.delete(f"/accounts/{account}")
    assert r.json().get("detail") != "1 session runs on Work: remove it first.", r.text
    asked = codex_calls(calls)
    assert asked and all(c == "codex login status" for c in asked), asked


def test_a_working_attempt_s_session_is_not_stopped_by_the_sessions_stop(tws):
    # An attempt's chat is the run's session, and the sessions' Stop would stop its claude while the attempt stayed
    # working with none until its time limit; ending the attempt there instead would not be the owner's Stop, so its
    # task would go on to a Next on... pressed before. It answers 409, naming the task's own Stop, and stops nothing
    from proofpane_workspace import workspace as ws_module
    account_file(tws)
    put_agent(tws, brief="sleep 120")
    run_id = queue(tws, identity=CONFIRMED)["run_id"]
    deadline = time.time() + 60
    while not any(m["text"] == task_attempt.RESUMED for m in jsonl(tws, "stand-in-received.jsonl")):
        assert time.time() < deadline, run_view(tws, run_id)
        time.sleep(0.1)
    assert len(claude_starts(tws)) == 2                     # the frozen start, and the restart at work
    r = tws.http.post(f"/sessions/{run_id}/stop")
    assert r.status_code == 409 and r.json()["detail"] == ws_module.ATTEMPT_STOP, r.text
    time.sleep(2)
    assert run_view(tws, run_id)["state"] == "working"
    assert tws.http.get(f"/sessions/{run_id}").json()["chat"]["state"] == "running"     # its claude still at work
    assert len(claude_starts(tws)) == 2
    stopped = tws.http.post(f"/agents/runs/{run_id}/stop")
    assert stopped.status_code == 200, stopped.text
    ended = until(tws, run_id)
    assert (ended["state"], ended["reason"]) == ("stopped", "stopped by you"), ended
    # once the attempt has ended its session is one like any other, and the sessions' Stop answers it: the refusal is
    # for a live attempt alone, not for any run of a task. The run's id leaves task_runs.live a moment after its record
    # says it ended, so the Stop may still answer 409 meanwhile
    deadline = time.time() + 30
    while True:
        after = tws.http.post(f"/sessions/{run_id}/stop")
        if after.status_code != 409 or time.time() > deadline:
            break
        time.sleep(0.2)
    assert after.status_code == 200, after.text


# ---- two users' workspaces, each pushing and fetching with its own sign-in ------------------------------------------
# GitHub is played on this machine: each workspace user's own git settings resolve github.com to the loopback for git
# alone (http.curloptResolve) and trust a test CA's certificate for it, and name gh as the credential helper, as `gh
# auth setup-git` does; the bare repositories are served by git's own http-backend, each reachable with one user's
# token alone (another user's gets GitHub's 404). Each workspace's gh is the stand-in, signed in as its own user.
# Where 127.0.0.1:443 cannot be bound, the test on that port skips; its case through a tunnel needs no port 443:
# the played GitHub listens on a free port, and each user's git settings name a proxy on the loopback
# (http.proxy) that tunnels CONNECT github.com:443 there, so git's address, the host its credential helper is asked
# for and gh's answer stay as they are on port 443. A url.<base>.insteadOf to the free port would not do: git then asks
# its credential helper for host=github.com:<port> (measured with git 2.43), for which gh, as the stand-in does, keeps
# no token, so the push would no longer be made with the user's own sign-in.

class _GitHub:
    """github.com on 127.0.0.1:443, for git; or, with `port` 0, on a free port of the loopback, reached through
    `tunnel` (_Tunnel), the address git is given unchanged. `access` maps a token to (login, the owner/name
    repositories it reaches). Each request is written down in `seen`: {method, path, user, status}."""

    def __init__(self, folder, access, port=443):
        import ssl
        import threading
        from http.server import ThreadingHTTPServer
        self.folder = folder
        (folder / "repos").mkdir(parents=True)
        self.access = access
        self.seen = []
        self.ca, cert, key = self._certificates()
        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", port), _GitHandler)
        except OSError as e:
            if port != 443:
                raise
            pytest.skip(f"github.com is played on 127.0.0.1:443, where this test cannot listen: {e}")
        self.server.github = self
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.tunnel = None if port == 443 else _Tunnel(self.server.server_address[1])

    def _certificates(self):
        import datetime
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
        now = datetime.datetime.now(datetime.timezone.utc)
        ca_key, key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
        ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "The tests' own CA")])
        usage = dict(digital_signature=False, content_commitment=False, key_encipherment=False,
                     data_encipherment=False, key_agreement=False, encipher_only=False, decipher_only=False)
        ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
              .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
              .not_valid_after(now + datetime.timedelta(days=1))
              .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
              .add_extension(x509.KeyUsage(key_cert_sign=True, crl_sign=True, **usage), critical=True)
              .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
              .sign(ca_key, hashes.SHA256()))
        leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,
                                                                                     "github.com")]))
                .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(minutes=5)).not_valid_after(now + datetime.timedelta(days=1))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName("github.com")]), critical=False)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                               critical=False)
                .sign(ca_key, hashes.SHA256()))
        paths = [str(self.folder / name) for name in ("ca.pem", "github.pem", "github-key.pem")]
        for path, data in zip(paths, (ca.public_bytes(serialization.Encoding.PEM),
                                      leaf.public_bytes(serialization.Encoding.PEM),
                                      key.private_bytes(serialization.Encoding.PEM,
                                                        serialization.PrivateFormat.PKCS8,
                                                        serialization.NoEncryption()))):
            with open(path, "wb") as fh:
                fh.write(data)
        return paths

    def repository(self, name, source):
        """A bare repository `owner/name` here, holding `source`'s main."""
        bare = str(self.folder / "repos" / f"{name}.git")
        git("init", "-q", "--bare", "-b", "main", bare, cwd=str(self.folder))
        git("push", "-q", bare, "main", cwd=source)
        return f"https://github.com/{name}.git", bare

    def who(self, authorization):
        if not (authorization or "").startswith("Basic "):
            return None, ()
        try:
            _, _, token = base64.b64decode(authorization[6:]).decode().partition(":")
        except ValueError:
            return None, ()
        return self.access.get(token, (None, ()))

    def close(self):
        if self.tunnel is not None:
            self.tunnel.close()
        self.server.shutdown()
        self.server.server_close()


class _Tunnel:
    """An HTTP proxy on a free port of the loopback, as git's http.proxy names one: it tunnels CONNECT github.com:443
    to the played GitHub's port and refuses any other. Each request line it was given is written down in `seen`."""

    def __init__(self, target):
        import socket
        import socketserver
        import threading
        seen = self.seen = []

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                head = b""
                while b"\r\n\r\n" not in head:
                    chunk = self.request.recv(4096)
                    if not chunk:
                        return
                    head += chunk
                head, _, early = head.partition(b"\r\n\r\n")
                line = head.split(b"\r\n", 1)[0].decode("latin-1")
                seen.append(line)
                if line.split()[:2] != ["CONNECT", "github.com:443"]:
                    self.request.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                    return
                with socket.create_connection(("127.0.0.1", target), timeout=60) as upstream:
                    self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                    if early:
                        upstream.sendall(early)
                    _relay(self.request, upstream)

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _relay(a, b, timeout=60):
    """Bytes both ways between two sockets until either side ends, or nothing moves for `timeout` seconds. A selector,
    not select.select, which refuses a descriptor numbered 1024 or more, as a long test run's can be."""
    import selectors
    with selectors.DefaultSelector() as sel:
        sel.register(a, selectors.EVENT_READ, b)
        sel.register(b, selectors.EVENT_READ, a)
        while True:
            ready = sel.select(timeout)
            if not ready:
                return
            for key, _ in ready:
                data = key.fileobj.recv(65536)
                if not data:
                    return
                key.data.sendall(data)


class _GitHandler(BaseHTTPRequestHandler):
    """git's smart HTTP, as GitHub answers it: a request without a token is asked for one (401), one whose token does
    not reach the repository is not found (404), and the rest go to `git http-backend` as CGI."""

    def log_message(self, *args):
        pass

    def do_GET(self):
        self._git()

    def do_POST(self):
        self._git()

    def _git(self):
        github = self.server.github
        path, _, query = self.path.partition("?")
        repo = "/".join(path.strip("/").split("/")[:2]).removesuffix(".git")
        login, reach = github.who(self.headers.get("Authorization"))
        if login is None:
            return self._answer(401, [("WWW-Authenticate", 'Basic realm="GitHub"')], b"", path, login)
        if repo not in reach:
            return self._answer(404, [], b"Repository not found.\n", path, login)
        body = self._body()
        env = {"PATH": os.environ["PATH"], "HOME": str(github.folder), "GIT_PROJECT_ROOT": str(github.folder / "repos"),
               "GIT_HTTP_EXPORT_ALL": "1", "PATH_INFO": path, "QUERY_STRING": query, "REQUEST_METHOD": self.command,
               "CONTENT_TYPE": self.headers.get("Content-Type", ""), "CONTENT_LENGTH": str(len(body)),
               "REMOTE_USER": login, "REMOTE_ADDR": "127.0.0.1", "GATEWAY_INTERFACE": "CGI/1.1"}
        for header, name in (("Git-Protocol", "HTTP_GIT_PROTOCOL"), ("Content-Encoding", "HTTP_CONTENT_ENCODING")):
            if self.headers.get(header):
                env[name] = self.headers[header]
        p = subprocess.run(["git", "http-backend"], input=body, env=env, capture_output=True, timeout=60)
        head, _, out = p.stdout.partition(b"\r\n\r\n")
        status, headers = 200, []
        for line in head.decode("latin-1").split("\r\n"):
            name, _, value = line.partition(":")
            if name.lower() == "status":
                status = int(value.split()[0])
            elif name:
                headers.append((name, value.strip()))
        self._answer(status, headers, out, path, login)

    def _body(self):
        if self.headers.get("Transfer-Encoding", "").lower() != "chunked":
            return self.rfile.read(int(self.headers.get("Content-Length") or 0))
        out = b""
        while True:
            size = int(self.rfile.readline().split(b";")[0], 16)
            if size == 0:
                self.rfile.readline()
                return out
            out += self.rfile.read(size)
            self.rfile.readline()

    def _answer(self, status, headers, body, path, login):
        self.server.github.seen.append({"method": self.command, "path": path, "user": login, "status": status})
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def signed_in_to(w, github, user, token):
    """The workspace user's own sign-in at the played github.com: gh's state, and git's settings as `gh auth setup-git`
    leaves them, with github.com resolved to the loopback for git (or, for a GitHub on a free port, reached through its
    tunnel) and the tests' CA trusted."""
    (w.home / "fake-gh.json").write_text(json.dumps({"user": user, "token": token, "logged_in": True}))
    reach = f"\tproxy = http://127.0.0.1:{github.tunnel.port}\n" if github.tunnel is not None else \
        "\tcurloptResolve = github.com:443:127.0.0.1\n"
    (w.home / ".gitconfig").write_text('[credential "https://github.com"]\n\thelper =\n'
                                       '\thelper = !gh auth git-credential\n'
                                       f"[http]\n{reach}\tsslCAInfo = {github.ca}\n")


def _gh_credential_asks(w):
    return [c for c in jsonl(w, "fake-gh-calls.jsonl") if c["args"][:3] == ["auth", "git-credential", "get"]]


def _files_holding(root, needle):
    """The files under `root` whose bytes hold `needle`."""
    found = []
    for folder, _, names in os.walk(root):
        for name in names:
            path = os.path.join(folder, name)
            try:
                with open(path, "rb") as fh:
                    if needle.encode() in fh.read():
                        found.append(path)
            except OSError:
                continue
    return found


def test_two_users_workspaces_push_and_fetch_each_with_its_own_sign_in_and_share_nothing(tmp_path):
    _two_users_push_and_fetch(tmp_path, 443)


def test_the_same_through_a_tunnel_to_a_free_port_where_port_443_is_not_needed(tmp_path):
    """The same where 127.0.0.1:443 cannot be bound: the played GitHub on a free port, each
    user's git reaching it through the tunnel its http.proxy names; the test above keeps its own skip."""
    _two_users_push_and_fetch(tmp_path, 0)


def _two_users_push_and_fetch(tmp_path, port):
    token_a, token_b = "gho_" + "1" * 36, "gho_" + "2" * 36
    github = _GitHub(tmp_path / "github", {token_a: ("ana", ("ana/widgets",)), token_b: ("bo", ("bo/gadgets",))},
                     port=port)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a, b = TaskWorkspace(tmp_path / "a"), TaskWorkspace(tmp_path / "b")
    try:
        signed_in_to(a, github, "ana", token_a)
        signed_in_to(b, github, "bo", token_b)
        url_a, _ = github.repository("ana/widgets", os.path.join(a.root, "repos", "demo"))
        url_b, bare_b = github.repository("bo/gadgets", os.path.join(b.root, "repos", "demo"))
        # each user's own account and key, and an attempt in each workspace
        keys = {}
        for w, name in ((a, "ana"), (b, "bo")):
            account = w.http.post("/accounts", json={"kind": "claude", "name": f"{name}'s"}).json()["id"]
            keys[name] = (account, f"sk-ant-test-{name}-" + "k" * 30)
            assert w.http.put("/model-providers/anthropic", json={"key": keys[name][1]}).status_code == 200
            put_agent(w)
            until(w, queue(w)["run_id"])
        # each pushes its own task's commit to its own repository, with its own sign-in
        heads = {}
        for w, name, url in ((a, "ana", url_a), (b, "bo", url_b)):
            commit, base = offered(w, task_of_owner="ab" * 16)
            other = "ef" * 16
            assert accept(w, commit, base, task=other).status_code == 200
            work = task_work(w, task=other)
            done = commit_in(work, f"{name}.txt", f"{name}'s work\n")
            r = _push(w, done, commit, done, address=url, task=other)
            assert r.status_code == 200 and r.json()["pushed"] is True, r.text
            heads[name] = (other, done)
        pushes = [s for s in github.seen if s["path"].endswith("/git-receive-pack") and s["status"] == 200]
        assert [(s["path"], s["user"]) for s in pushes] == [("/ana/widgets.git/git-receive-pack", "ana"),
                                                            ("/bo/gadgets.git/git-receive-pack", "bo")]
        # and fetches with it: a commit another pushed to bo's repository, brought into bo's task by its hash
        clone = str(tmp_path / "someone")
        git("clone", "-q", bare_b, clone, cwd=str(tmp_path))
        git("checkout", "-q", "-b", "pp/task-" + heads["bo"][0], heads["bo"][1], cwd=clone)
        later = commit_in(clone, "later.txt", "later\n")
        git("push", "-q", bare_b, "HEAD:refs/heads/pp/task-" + heads["bo"][0], cwd=clone)
        before = len(github.seen)
        r = b.http.post(f"/agents/tasks/{heads['bo'][0]}/fetch", json={"address": url_b, "commit": later,
                                                                     "purpose": "view", "base": heads["bo"][1],
                                                                     "protected": []})
        assert r.status_code == 200 and r.json()["descends"] is True, r.text
        fetched = [s for s in github.seen[before:] if s["path"].endswith("/git-upload-pack") and s["status"] == 200]
        assert fetched and {s["user"] for s in fetched} == {"bo"}
        # bo's sign-in does not reach ana's repository, and nothing of ana's sign-in is borrowed for it
        r = b.http.post(f"/agents/tasks/{heads['bo'][0]}/fetch", json={"address": url_a, "commit": heads["ana"][1],
                                                                     "purpose": "view", "base": None,
                                                                     "protected": []})
        assert r.status_code == 400 and "cannot read" in r.json()["detail"]
        assert {s["user"] for s in github.seen if s["path"].startswith("/ana/") and s["user"]} == {"ana", "bo"}
        assert all(s["status"] == 404 for s in github.seen if s["path"].startswith("/ana/") and s["user"] == "bo")
        # the stand-in gh records whose: each workspace's git asked its own user's gh, in its own home
        for w, name in ((a, "ana"), (b, "bo")):
            asks = _gh_credential_asks(w)
            assert asks and {(c["home"], c["user"]) for c in asks} == {(str(w.home), name)}
        # through the tunnel: git asked it for github.com's own address, and for nothing else
        if github.tunnel is not None:
            assert github.tunnel.seen and {line.split()[:2] == ["CONNECT", "github.com:443"]
                                           for line in github.tunnel.seen} == {True}, github.tunnel.seen
        # nothing of either user's account, key, token or folders is in the other's workspace or home
        for w, other_w, other in ((a, b, "bo"), (b, a, "ana")):
            for needle in (keys[other][0], keys[other][1], token_a if other == "ana" else token_b,
                           str(other_w.tmp)):
                assert _files_holding(str(w.tmp), needle) == [], (other, needle)
    finally:
        a.stop()
        b.stop()
        github.close()
