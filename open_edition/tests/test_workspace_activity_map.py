"""The activity hook's --map mode (activity.py): each tool call, under the agent it was made in, with its name,
what it names as runtree keeps it, whether it failed and when; each sub-agent's start and end; never anything else of
a call's input, nor a sub-agent's last message; bounded files; and the session's state left alone."""

import json
import os
import subprocess
import sys

import pytest

from conftest import ROOT
from proofpane_workspace import activity, runtree

SCRIPT = os.path.join(ROOT, "proofpane_workspace", "activity.py")
SECRET = "sk-ant-api03-must-never-reach-the-map"


def _run(event, folder, shell=False, args=("--map",)):
    env = {"PATH": os.environ["PATH"], "HOME": str(folder), "PP_SESSION_DIR": str(folder)}
    if shell:
        env["PP_SHELL"] = "1"
    stdin = event if isinstance(event, str) else json.dumps(event)
    return subprocess.run([sys.executable, SCRIPT, *args], input=stdin, capture_output=True, text=True, env=env,
                          timeout=30)


def _calls(folder):
    path = folder / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_a_call_is_kept_with_its_target_and_nothing_else(tmp_path):
    event = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "s1", "transcript_path": "/t.jsonl",
             "tool_input": {"command": f"TOKEN={SECRET} curl -H 'Authorization: Bearer {SECRET}' https://example.test",
                            "description": "Fetch with the key"},
             "tool_response": {"stdout": SECRET, "stderr": ""}, "tool_use_id": "toolu_1"}
    done = _run(event, tmp_path)
    assert done.returncode == 0 and done.stdout == "" and done.stderr == ""
    (call,) = _calls(tmp_path)
    assert set(call) == {"at", "agent", "tool", "target", "failed"}
    assert call["agent"] is None and call["tool"] == "Bash" and call["target"] == "curl" and call["failed"] is False
    assert SECRET not in (tmp_path / "calls.jsonl").read_text()
    assert not (tmp_path / "activity.json").exists()           # the session's state is not this hook's


def test_a_command_whose_words_a_shell_may_split_otherwise_keeps_no_target(tmp_path):
    # through the hook, as Claude Code runs it: commands whose words could name an argument, a key included
    for command in (f'AUTH=$(printf "admin:{SECRET}" | base64); curl -H "Authorization: Basic $AUTH" https://x',
                    f"TOKEN=$(echo '{SECRET}') && gh api user",
                    f"HOOK=$(curl https://hooks.example.test/services/T000/B000/{SECRET}) && echo ok",
                    f"KEY=abc;mysql -p{SECRET} -u root", f"PASS=$'pa\\' {SECRET} x' mysql -u root",
                    f"A=${{B:- {SECRET} }} mysql -u root"):
        _run({"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": command}}, tmp_path)
    assert [c["target"] for c in _calls(tmp_path)] == ["", "", "", "", "", ""]
    assert SECRET not in (tmp_path / "calls.jsonl").read_text()


def test_a_sub_agent_s_call_is_kept_under_it_and_a_failure_as_one(tmp_path):
    _run({"hook_event_name": "PostToolUseFailure", "tool_name": "Read", "agent_id": "agent-a1",
          "agent_type": "Explore", "tool_input": {"file_path": "/w/src/a.py"}, "error": SECRET}, tmp_path)
    (call,) = _calls(tmp_path)
    assert call["agent"] == "agent-a1" and call["tool"] == "Read" and call["target"] == "/w/src/a.py"
    assert call["failed"] is True and SECRET not in (tmp_path / "calls.jsonl").read_text()


@pytest.mark.parametrize("event", [
    {"hook_event_name": "PostToolUse", "tool_name": "cat ~/.ssh/id_rsa", "tool_input": {}},
    {"hook_event_name": "PostToolUse", "tool_name": 5},
    {"hook_event_name": "PostToolUse"},
    {"hook_event_name": "PostToolUse", "tool_name": "Read", "agent_id": "../../x", "tool_input": {}},
    {"hook_event_name": "PostToolUse", "tool_name": "Read", "agent_id": "", "tool_input": {}},
    {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {}},
    {"hook_event_name": "Stop"},
])
def test_what_is_no_call_of_a_known_agent_is_not_kept(tmp_path, event):
    assert _run(event, tmp_path).returncode == 0
    assert _calls(tmp_path) == []


@pytest.mark.parametrize("name, given", [
    ("Read", {"file_path": "/w/a b/c.py"}), ("Edit", {"file_path": "  /w/x\n.py  ", "old_string": SECRET}),
    ("NotebookEdit", {"notebook_path": "/w/n.ipynb"}), ("Glob", {"path": "/w", "pattern": "**/*.py"}),
    ("Grep", {"pattern": SECRET, "path": "/w/src"}), ("Grep", {"pattern": SECRET}),
    ("Bash", {"command": "A=1 B=2 /usr/bin/env python -c 'print(1)'"}), ("Bash", {"command": "'./run me.sh' --x"}),
    ("Bash", {"command": "   "}), ("Bash", {"command": ["ls", "-la"]}), ("Bash", {"command": "X=1"}),
    ("WebFetch", {"url": f"https://example.test/?key={SECRET}", "prompt": SECRET}),
    ("Agent", {"prompt": SECRET, "description": "Look", "subagent_type": "Explore"}),
    ("Write", {"file_path": "/w/" + "d" * 300, "content": SECRET}), ("Write", "not a dict"), ("Read", None),
    ("mcp__fs__read_file", {"path": "/w/m.md"}), ("Bash", {"command": "(" + "x" * 100 + ")"}),
    ("Bash", {"command": f'TOKEN="{SECRET} part-two" ./deploy.sh'}), ("Bash", {"command": f"echo '{SECRET}"}),
    ("Bash", {"command": f'"curl -H Authorization:{SECRET}" https://example.test'}),
    ("Bash", {"command": f"'python3 -c \"print({SECRET})\"'"}), ("Bash", {"command": f'TOKEN="{SECRET} ./deploy.sh'}),
    ("Bash", {"command": "cat > notes.md <<'EOF'\nIt's done\nEOF"}), ("Bash", {"command": "ls -la # don't care"}),
    # where shlex and a shell split the words differently
    ("Bash", {"command": f'AUTH=$(printf "admin:{SECRET}" | base64); curl -H "Authorization: Basic $AUTH" https://x'}),
    ("Bash", {"command": f"TOKEN=$(echo '{SECRET}') && gh api user"}),
    ("Bash", {"command": f"HOOK=$(curl https://hooks.example.test/services/T000/B000/{SECRET}) && echo ok"}),
    ("Bash", {"command": f"KEY=abc;mysql -p{SECRET} -u root"}), ("Bash", {"command": f"KEY=abc ; mysql -p{SECRET}"}),
    ("Bash", {"command": f"A=1 2>/dev/{SECRET} ls"}), ("Bash", {"command": f"A=x#y cat <<EOF\n{SECRET}\nEOF"}),
    ("Bash", {"command": f"A=(1 {SECRET} 3) ls"}), ("Bash", {"command": f"A=`echo {SECRET}` ls"}),
    ("Bash", {"command": f"A=1 -p{SECRET}"}), ("Bash", {"command": f"admin:{SECRET} x"}),
    ("Bash", {"command": '"$VENV/bin/python" -m pytest'}), ("Bash", {"command": "g++ -o x x.cc"}),
    ("Bash", {"command": "PAYLOAD='" + "x" * 200_000 + f"' curl -d {SECRET}"}),
    ("Bash", {"command": "A=" + "v" * (activity.SCAN - 6) + " program-name-long " + "t" * 5000}),
    ("Bash", {"command": f"# {SECRET}\ngit status"}), ("Bash", {"command": "# only a comment"}),
    ("Bash", {"command": f"PASS=$'pa\\' {SECRET} x' mysql -u root"}), ("Bash", {"command": f"A=${{B:- {SECRET} }} x"}),
    ("Bash", {"command": f"TOKEN=abc\r{SECRET} more curl"}), ("Bash", {"command": "A=$[1 + 2] mysql"}),
    ("Bash", {"command": f"A=}}${{B:-a {SECRET} }} mysql"}), ("Bash", {"command": f'"A"=b {SECRET} more'}),
    ("Bash", {"command": "A=1 \\\n  python run.py"}), ("Bash", {"command": f"A=1 # {SECRET}\nmake"}),
])
def test_the_target_is_runtree_s(name, given):
    # written out in activity.py, which runs alone: the same answers as runtree._target, which the run tree keeps
    assert activity.target(name, given) == runtree._target(name, given)
    assert SECRET not in activity.target(name, given)


def test_a_sub_agent_s_start_and_stop_are_kept_and_its_last_message_never(tmp_path):
    _run({"hook_event_name": "SubagentStart", "agent_id": "def456", "agent_type": "Explore", "session_id": "s"},
         tmp_path)
    _run({"hook_event_name": "SubagentStop", "agent_id": "def456", "agent_type": "Explore",
          "agent_transcript_path": "/x/subagents/agent-def456.jsonl", "last_assistant_message": SECRET,
          "background_tasks": [], "stop_hook_active": False}, tmp_path)
    lines = [json.loads(x) for x in (tmp_path / "subagents.jsonl").read_text().splitlines()]
    assert [(e["event"], e["id"], e["type"]) for e in lines] == [("start", "def456", "Explore"),
                                                                ("stop", "def456", "Explore")]
    assert all(set(e) == {"at", "event", "id", "type"} for e in lines)
    assert SECRET not in (tmp_path / "subagents.jsonl").read_text()


def test_a_sub_agent_event_with_no_id_is_not_kept(tmp_path):
    _run({"hook_event_name": "SubagentStop", "agent_type": ""}, tmp_path)
    _run({"hook_event_name": "SubagentStart", "agent_id": 7}, tmp_path)
    assert not (tmp_path / "subagents.jsonl").exists()


def test_a_call_whose_event_is_past_the_state_s_64_kb_is_still_kept(tmp_path):
    big = {"hook_event_name": "PostToolUse", "tool_name": "Read", "tool_input": {"file_path": "/w/big.txt"},
           "tool_response": {"content": "y" * (2 * 1024 * 1024)}}
    assert _run(big, tmp_path).returncode == 0
    assert [c["target"] for c in _calls(tmp_path)] == ["/w/big.txt"]


def test_the_calls_file_is_cut_to_its_newest_half_past_its_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(activity, "MAX_CALLS", 2000)
    for n in range(60):
        activity.map_record({"hook_event_name": "PostToolUse", "tool_name": "Read",
                             "tool_input": {"file_path": f"/w/{n:03d}.py"}}, directory=str(tmp_path))
    text = (tmp_path / "calls.jsonl").read_text()
    assert len(text.encode()) <= 2000 + 200
    kept = [json.loads(x)["target"] for x in text.splitlines()]
    assert kept[-1] == "/w/059.py" and "/w/000.py" not in kept and kept == sorted(kept)


def test_a_link_where_the_file_goes_is_not_followed(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_text("")
    os.symlink(elsewhere, tmp_path / "calls.jsonl")
    _run({"hook_event_name": "PostToolUse", "tool_name": "Read", "tool_input": {"file_path": "/w/a"}}, tmp_path)
    assert elsewhere.read_text() == ""


def test_a_program_in_one_of_the_session_s_shells_keeps_nothing(tmp_path):
    _run({"hook_event_name": "PostToolUse", "tool_name": "Read", "tool_input": {}}, tmp_path, shell=True)
    _run({"hook_event_name": "SubagentStart", "agent_id": "a1"}, tmp_path, shell=True)
    assert os.listdir(tmp_path) == []


@pytest.mark.parametrize("stdin", ["", "not json", "[1, 2]", "{"])
def test_any_input_is_taken_and_nothing_blocks(tmp_path, stdin):
    done = _run(stdin, tmp_path)
    assert done.returncode == 0 and done.stdout == "" and os.listdir(tmp_path) == []


def test_the_state_hooks_still_keep_no_calls(tmp_path):
    # the hooks every program runs (working, waiting, idle) are as they were: they keep the state, not the map's calls
    _run({"hook_event_name": "PostToolUse", "tool_name": "Read", "tool_input": {"file_path": "/w/a"}}, tmp_path,
         args=("working",))
    assert json.loads((tmp_path / "activity.json").read_text())["state"] == "working"
    assert not (tmp_path / "calls.jsonl").exists()


def test_a_value_quoted_in_an_assignment_is_never_taken_for_the_program():
    # split on spaces, the second half of the value would be the target: read as a shell reads it
    for target in (activity.target, runtree._target):
        assert target("Bash", {"command": 'TOKEN="part-one part-two" ./deploy.sh --now'}) == "deploy.sh"
        assert target("Bash", {"command": "A='x y' B=\"z w\" python3 run.py"}) == "python3"
        # a quote that does not close before the program's word names nothing; after it, it is never read
        assert target("Bash", {"command": 'TOKEN="unclosed ./deploy.sh'}) == ""
        assert target("Bash", {"command": "echo 'unclosed"}) == "echo"
        # a quoted first word brings none of its arguments
        assert target("Bash", {"command": f'"curl -H Authorization:{SECRET}" https://example.test'}) == "curl"
        assert target("Bash", {"command": f"'python3 -c \"print({SECRET})\"'"}) == "python3"
        # nothing after the program is read: a heredoc or a comment with an apostrophe in it
        assert target("Bash", {"command": "cat > notes.md <<'EOF'\nIt's done\nEOF"}) == "cat"
        assert target("Bash", {"command": "ls -la # don't care"}) == "ls"


@pytest.mark.parametrize("command", [
    f'AUTH=$(printf "admin:{SECRET}" | base64); curl -H "Authorization: Basic $AUTH" https://example.test',
    f"TOKEN=$(echo '{SECRET}') && gh api user",
    f"HOOK=$(curl https://hooks.example.test/services/T000/B000/{SECRET}) && echo ok",
    f"KEY=abc;mysql -p{SECRET} -u root", f"KEY=abc ; mysql -p{SECRET}", f"KEY=abc && mysql -p{SECRET}",
    f"A=x|tee {SECRET}", f"A=x&{SECRET}", f"A=x<{SECRET} ls", f"A=x>{SECRET} ls", f"A=(1 {SECRET} 3) ls",
    f"A=`echo {SECRET}` ls", f"A=1 2>/dev/null {SECRET}", f"A=1 -p{SECRET}", f"admin:{SECRET} x",
    f"user@{SECRET} x", f"1A={SECRET} x", f"a+={SECRET} x", "$EDITOR notes.md", "[ -f x ]", "# only a comment",
    # each of the assignment's characters on its own, with an argument after it that is a plain word: the plain
    # program's rule alone would name it
    f"KEY=abc;echo {SECRET}", f"KEY=abc&&echo {SECRET}", f"A=`echo {SECRET} ` ls", f"KEY=abc< {SECRET} cat",
    f"KEY=abc> {SECRET} cat", f"A=x) {SECRET} ls",
    # a colon in a path's folders: a URL in the program's place would name its last part, a key included
    f"https://hooks.example.test/services/{SECRET} --x",
    # quoting a shell reads otherwise than shlex: $'...' with \' inside, ${...} or $[...] holding spaces (each would
    # name a word from inside the value)
    "MSG=$'It\\'s ready now' git commit -m \"$MSG\"",
    "BODY=$'Fixes the parser\\'s crash' gh pr create --body \"$BODY\"",
    f"PASS=$'pa\\' {SECRET} x' mysql -u root", "GREETING=${NAME:-my default message} ./notify.sh",
    f"A=${{B:- {SECRET} }} mysql -u root", "A=$[1 + 2] mysql -u root",
    # quoted, a substitution splits nothing to a shell either, but its characters are refused all the same (the cost
    # of reading the words with shlex, which keeps no quotes: said in the manual)
    'A="$(cat x) y" make',
    # a bracket closed before it opens, a program word holding an open one, a name a shell reads as no assignment, and
    # a comment whose quote shlex reads past its line (each would name a word of the value)
    f"A=}}${{B:-a {SECRET} }} mysql", f"A=${{B:-x}}}}${{C:-y {SECRET} }} mysql",
    f"${{TOOL-/opt/{SECRET} more}}/bin/run x",
    f'"A"=b {SECRET} more', f"A\\=b {SECRET} more", f"A=1 #'{SECRET}\nmake",
    f"A=1 #'x\ny' cat <<EOF\n{SECRET}\nEOF",          # the comment's quote closes on the next line: shlex reads past it
])
def test_a_word_a_shell_may_split_otherwise_names_nothing(command):
    # an assignment holding a substitution or another command, or a word that is no plain program: the word shlex
    # comes to can be an argument, a key included (`KEY=abc;mysql -pS3cret` would give `-pS3cret`)
    for target in (activity.target, runtree._target):
        assert target("Bash", {"command": command}) == ""


@pytest.mark.parametrize("command, program", [
    ("g++ -o x x.cc", "g++"), ("docker-compose up", "docker-compose"), ("~/bin/tool x", "tool"),
    ("./run.sh", "run.sh"), ("python3.12 -m x", "python3.12"), ('"$VENV/bin/python" -m pytest', "python"),
    ("${HOME}/bin/x --y", "x"), ("(cd x && make)", "cd"), ("A=1 B='x y' make all", "make"),
    (f"A=x#y cat <<EOF\n{SECRET}\nEOF", "cat"),        # a `#` inside a word hides no line: the heredoc is never read
    ("A=1\nls -la", "ls"), ("if true; then x; fi", "if"),
    # the comment lines a command begins with are passed over, as a shell passes them over; their words never named
    ("# a note\nls", "ls"), ("#!/bin/sh\n  # two\r\n  ls -la", "ls"), (f"# {SECRET}\ngit status", "git"),
    # a carriage return is part of a word to a shell, as here: the assignment holds the key, and `more` is what runs
    (f"TOKEN=abc\r{SECRET} more curl https://example.test", "more"), ("git status\r\n", "git"),
    ("A=${HOME} make", "make"),
    # a line continued after an assignment, a comment after one, and a comment line before the program, which a shell
    # passes over: each still names its program
    ("OPENAI_API_KEY=sk-x \\\n  python script.py", "python"), ("OPENAI_API_KEY=sk-x \\\npython script.py", "python"),
    (f"A=1 # {SECRET}\nmake", "make"), (f"A=1\n# {SECRET}\nmake all", "make"), ("A=1 #note\nmake", "make"),
    ("A=${A:-${B}} make", "make"),
])
def test_a_plain_program_is_still_named(command, program):
    for target in (activity.target, runtree._target):
        assert target("Bash", {"command": command}) == program


# each built where it is read, so that no test id or argument carries megabytes
_LONG = [
    ("quoted-assignment", "\"PAYLOAD='\" + 'x' * 200_000 + \"' curl -d @- https://example.test\"", ""),
    ("quoted-first-word", "\"'\" + 'y' * 200_000 + \"' --x\"", ""),
    ("unclosed-quote", "\"'\" + 'y' * 200_000", ""),
    ("long-assignment", "'TOKEN=' + 'z' * 200_000 + ' curl'", ""),
    ("short-program-first", "'make ' + 'x' * (4 * 1024 * 1024)", "make"),
    ("long-first-word", "'make' + 'x' * (4 * 1024 * 1024)", ""),
    ("open-quote-after", "\"python3 '\" + 'z' * 200_000 + \"'\"", "python3"),   # its quote closes past what is read
]


@pytest.mark.parametrize("built, program", [(b, p) for _, b, p in _LONG], ids=[n for n, _, _ in _LONG])
def test_a_long_command_is_read_only_at_its_start(built, program):
    # shlex grows a word a character at a time: a 160 KB first word took 14 s, and the map's
    # hooks run in the background, where Claude Code times out none. In a process of its own with a time limit: a
    # reader that took the square of a word's length again would hold a run for hours on the 4 MB first word, and a
    # thread could not be stopped
    code = ("import json, sys, time; sys.path.insert(0, sys.argv[1]); "
            "from proofpane_workspace import activity, runtree; "
            "command = " + built + "; started = time.monotonic(); "
            "got = [activity.target('Bash', {'command': command}), runtree._target('Bash', {'command': command})]; "
            "print(json.dumps({'got': got, 'seconds': time.monotonic() - started}))")
    done = subprocess.run([sys.executable, "-I", "-c", code, ROOT], capture_output=True, text=True, timeout=20)
    got = json.loads(done.stdout)
    assert got["got"] == [program, program] and got["seconds"] < 1, (got, done.stderr[-300:])


def test_a_command_is_read_as_far_as_4_kb():
    # the manual's bound, as a number (the test below reads it from the code, so it holds the rule at any bound): a
    # program within the first 4096 characters is named, one past them is not
    for target in (activity.target, runtree._target):
        assert target("Bash", {"command": "A=" + "v" * 4080 + " prog " + "y" * 9000}) == "prog"
        assert target("Bash", {"command": "A=" + "v" * 4100 + " prog " + "y" * 9000}) == ""


def test_a_word_the_cut_may_have_shortened_names_nothing():
    scan = activity.SCAN
    assert runtree._SCAN == scan
    for target in (activity.target, runtree._target):
        # within what is read, and past it only when another word follows
        assert target("Bash", {"command": "A=" + "v" * (scan - 10) + " prog"}) == "prog"
        assert target("Bash", {"command": "A=" + "v" * (scan - 10) + " prog --flag " + "y" * scan}) == "prog"
        assert target("Bash", {"command": "A=" + "v" * (scan - 12) + " prog " + "y" * scan}) == "prog"
        # the cut leaves `pro` of `program-name-long`, and nothing after it: never a part of a name
        assert target("Bash", {"command": "A=" + "v" * (scan - 6) + " program-name-long " + "t" * 5000}) == ""
        # an assignment longer than what is read
        assert target("Bash", {"command": "A=" + "v" * scan + " prog"}) == ""


def test_a_fifo_where_the_calls_file_goes_holds_up_no_call(tmp_path):
    import time as _time
    os.mkfifo(tmp_path / "calls.jsonl")
    started = _time.monotonic()
    done = _run({"hook_event_name": "PostToolUse", "tool_name": "Read", "tool_input": {"file_path": "/w/a"}}, tmp_path)
    assert done.returncode == 0 and _time.monotonic() - started < 5


def test_a_trim_takes_away_what_an_ended_one_left_and_keeps_another_s_in_hand(tmp_path, monkeypatch):
    import time as _time
    stale, fresh = tmp_path / "calls.jsonl.111.tmp", tmp_path / "calls.jsonl.222.tmp"
    stale.write_text("x")
    fresh.write_text("y")
    os.utime(stale, (_time.time() - 3600, _time.time() - 3600))
    monkeypatch.setattr(activity, "MAX_CALLS", 300)
    for n in range(10):
        activity.map_record({"hook_event_name": "PostToolUse", "tool_name": "Read",
                             "tool_input": {"file_path": f"/w/{n}.py"}}, directory=str(tmp_path))
    assert not stale.exists() and fresh.exists()
    assert not [p for p in os.listdir(tmp_path) if p.startswith("calls.jsonl.") and p != fresh.name]
