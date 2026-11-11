"""An agent of the person's own, started by the workspace (workspace.prepare with program "custom"): its command as
written, split as a shell would but never run by one, with the task as its last argument or typed at its prompt by the
page; started again with its command alone; refused when it is not one program the workspace can find."""

import json
import os
import sys

import pytest

from workspace_fixtures import Agent, make_repo, read_until

# prints the arguments it was given, one a line between markers, then waits as an agent would
ECHOER = f"""#!{sys.executable}
import sys, time
print("args:" + "|".join(sys.argv[1:]) + ":end", flush=True)
time.sleep(600)
"""


@pytest.fixture
def agent(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "echoer").write_text(ECHOER)
    (bin_dir / "echoer").chmod(0o755)
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": f"{bin_dir}:{os.environ['PATH']}"})
    make_repo(str(tmp_path / "ws" / "repos" / "demo"))
    a.bin = bin_dir
    yield a
    a.close()


def _start(agent, command="echoer --model fast", task="argument", text="Fix the parser"):
    return agent.http.post("/sessions", json={"repo": "demo", "program": "custom", "task": text,
                                              "custom": {"name": "Echo agent", "command": command, "task": task}})


def test_its_command_runs_as_written_with_the_task_as_its_last_argument(agent):
    r = _start(agent, command="echoer --model fast 'two words'")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["custom"] == {"name": "Echo agent", "argv": ["echoer", "--model", "fast", "two words"], "task": "argument"}
    with agent.terminal(s["id"]) as ws:
        out = read_until(ws, b":end")
    assert b"args:--model|fast|two words|Fix the parser:end" in out


def test_one_that_takes_its_task_at_its_prompt_starts_without_it_and_again_with_its_command_alone(agent):
    s = _start(agent, task="typed").json()
    with agent.terminal(s["id"]) as ws:
        out = read_until(ws, b":end")
    assert b"args:--model|fast:end" in out
    record = json.load(open(os.path.join(agent.root, "sessions", s["id"], "session.json")))
    assert record["custom"]["task"] == "typed"
    # started once more, after it ended: the same command, no task
    assert agent.http.post(f"/sessions/{s['id']}/stop").status_code == 200
    r = agent.http.post(f"/sessions/{s['id']}/start", json={})
    assert r.status_code == 200, r.text
    with agent.terminal(s["id"]) as ws:
        assert b"args:--model|fast:end" in read_until(ws, b":end")


@pytest.mark.parametrize("command, task, says", [
    ("nowhere-to-be-found", "argument", "nowhere-to-be-found is not installed"),
    ("./bin/echoer", "argument", "by name, as the workspace's PATH finds it, or by its full path"),
    ("--version", "argument", "a program and at most"),
    ("echoer 'unclosed", "argument", "not closed"),
    ("echoer", "shell", "last argument, or is typed"),
    ("echoer\nrm -rf ~", "argument", "one line"),
])
def test_what_is_not_one_program_it_can_find_is_refused(agent, command, task, says):
    r = _start(agent, command=command, task=task)
    assert r.status_code == 400 and says in r.json()["detail"], r.text
    assert agent.http.get("/sessions").json()["sessions"] == []


def test_its_full_path_is_taken_and_a_session_names_it(agent):
    s = _start(agent, command=str(agent.bin / "echoer")).json()
    assert s["custom"]["argv"] == [str(agent.bin / "echoer")]
    listed = agent.http.get("/sessions").json()["sessions"]
    assert [(x["program"], x["custom"]["name"]) for x in listed] == [("custom", "Echo agent")]


def test_without_its_command_it_is_not_started(agent):
    r = agent.http.post("/sessions", json={"repo": "demo", "program": "custom", "task": "x"})
    assert r.status_code == 400 and "Name the agent of your own" in r.json()["detail"]
    r = agent.http.post("/sessions", json={"repo": "demo", "program": "custom", "task": "x",
                                           "custom": {"name": "Ec\u202eho", "command": "echoer", "task": "argument"}})
    assert r.status_code == 400 and "plain text" in r.json()["detail"]


def test_the_workspace_says_whether_an_agent_s_program_is_there(agent):
    r = agent.http.get("/programs", params=[("also", "echoer"), ("also", "nowhere-to-be-found"),
                                            ("also", "./bin/echoer"), ("also", str(agent.bin / "echoer"))])
    assert r.json()["also"] == {"echoer": True, "nowhere-to-be-found": False, "./bin/echoer": False,
                                str(agent.bin / "echoer"): True}
    assert "also" not in agent.http.get("/programs").json()
