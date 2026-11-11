"""The person's own agents (proofpane_open/ade/custom_agents.py): any command-line agent, kept by the app on the
account and never in the workspace; checked as they are written; one account's never another's."""

import pytest

from conftest import ADMIN, signed_up

OTHER = "second-admin@example.com"


@pytest.fixture
def local(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OPEN_EDITION_ADE", "local")
    monkeypatch.setenv("OPEN_EDITION_ADMINS", f"{ADMIN},{OTHER}")
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    from proofpane_open.ade import workspaces
    yield workspaces
    workspaces.shutdown()


A = "/api/ade/workspace/custom-agents"


def test_an_agent_of_one_s_own_is_kept_changed_and_forgotten(client, local):
    h = signed_up(client)
    assert client.get(A, headers=h).json() == {"agents": []}
    made = client.post(A, headers=h, json={"name": "  Aider  ", "command": " aider --model sonnet ", "task": "argument"})
    assert made.status_code == 200, made.text
    agent = made.json()
    assert (agent["name"], agent["command"], agent["task"]) == ("Aider", "aider --model sonnet", "argument")
    assert client.get(A, headers=h).json()["agents"] == [dict(agent, available=None)]      # no workspace running
    changed = client.put(f"{A}/{agent['id']}", headers=h, json={"name": "Aider", "command": "aider", "task": "typed"})
    assert changed.json()["task"] == "typed"
    assert client.delete(f"{A}/{agent['id']}", headers=h).json() == {"forgotten": agent["id"]}
    assert client.get(A, headers=h).json() == {"agents": []}
    assert client.delete(f"{A}/{agent['id']}", headers=h).status_code == 404


@pytest.mark.parametrize("body, says", [
    ({"name": "", "command": "aider", "task": "argument"}, "name is 1 to"),
    ({"name": "x" * 41, "command": "aider", "task": "argument"}, "name is 1 to"),
    ({"name": "Ai\u202eder", "command": "aider", "task": "argument"}, "plain text"),
    ({"name": "Aider", "command": "  ", "task": "argument"}, "one line"),
    ({"name": "Aider", "command": "aider\nrm -rf ~", "task": "argument"}, "one line"),
    ({"name": "Aider", "command": "aider 'unclosed", "task": "argument"}, "not closed"),
    ({"name": "Aider", "command": "--version", "task": "argument"}, "starts with the program"),
    ({"name": "Aider", "command": " ".join(["a"] * 40), "task": "argument"}, "at most"),
    ({"name": "Aider", "command": "aider", "task": "shell"}, "last argument, or is typed"),
])
def test_what_is_not_one_command_with_a_plain_name_is_refused(client, local, body, says):
    h = signed_up(client)
    r = client.post(A, headers=h, json=body)
    assert r.status_code == 400 and says in r.json()["detail"], r.text
    assert client.get(A, headers=h).json() == {"agents": []}


def test_one_account_s_agents_are_never_another_s(client, local):
    mine = signed_up(client)
    theirs = signed_up(client, email=OTHER)
    agent = client.post(A, headers=mine, json={"name": "Aider", "command": "aider", "task": "argument"}).json()
    assert client.get(A, headers=theirs).json() == {"agents": []}
    assert client.put(f"{A}/{agent['id']}", headers=theirs,
                      json={"name": "Mine now", "command": "evil", "task": "argument"}).status_code == 404
    assert client.delete(f"{A}/{agent['id']}", headers=theirs).status_code == 404
    assert client.get(A, headers=mine).json()["agents"] == [dict(agent, available=None)]


def test_at_most_twenty_and_a_sign_in_is_needed(client, local):
    h = signed_up(client)
    for n in range(20):
        assert client.post(A, headers=h, json={"name": f"agent {n}", "command": "aider", "task": "argument"}).status_code == 200
    r = client.post(A, headers=h, json={"name": "one more", "command": "aider", "task": "argument"})
    assert r.status_code == 400 and "At most 20" in r.json()["detail"]
    assert client.get(A).status_code == 401


def test_a_session_runs_the_command_the_app_keeps_never_one_the_page_sends(client, local, env, tmp_path, monkeypatch):
    import os
    import sys
    from workspace_fixtures import make_repo
    bin_dir = tmp_path / "agents-bin"
    bin_dir.mkdir()
    (bin_dir / "echoer").write_text(f"#!{sys.executable}\nimport sys, time\nprint('args:' + '|'.join(sys.argv[1:]), flush=True)\ntime.sleep(600)\n")
    (bin_dir / "echoer").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    h = signed_up(client)
    assert client.post("/api/ade/workspace/start", headers=h).json()["running"] is True
    me = client.get("/api/auth/me", headers=h).json()["id"]
    make_repo(str(env / "workspaces" / me / "repos" / "demo"))
    agent = client.post(A, headers=h, json={"name": "Echo", "command": "echoer --fast", "task": "argument"}).json()
    r = client.post("/api/ade/workspace/sessions", headers=h,
                    json={"repo": "demo", "program": "custom", "custom": agent["id"], "task": "Fix it"})
    assert r.status_code == 200, r.text
    assert r.json()["custom"] == {"name": "Echo", "argv": ["echoer", "--fast"], "task": "argument"}
    # an id the account does not have, another account's, or none: nothing starts
    other = signed_up(client, email=OTHER)
    theirs = client.post(A, headers=other, json={"name": "Theirs", "command": "echoer", "task": "argument"}).json()
    for body in ({"custom": "0123456789ab"}, {"custom": theirs["id"]}, {}):
        r = client.post("/api/ade/workspace/sessions", headers=h,
                        json={"repo": "demo", "program": "custom", "task": "x", **body})
        assert r.status_code == 404 and r.json()["detail"] == "No such agent of yours.", (body, r.text)
    # a command the page sends with a known agent is not taken: only the app's record of it
    r = client.post("/api/ade/workspace/sessions", headers=h,
                    json={"repo": "demo", "program": "shell", "task": "y", "custom": agent["id"]})
    assert r.status_code == 200 and not r.json().get("custom"), r.text


def test_each_one_says_whether_the_workspace_finds_its_program(client, local, tmp_path, monkeypatch):
    # while the workspace runs: its program found as its session would find it, so the page greys out one that cannot
    # start (an agent of one's own that is not installed, offered and then failing at Start, misled the person)
    import os
    bin_dir = tmp_path / "agentbin"
    bin_dir.mkdir()
    (bin_dir / "echoer").write_text("#!/bin/sh\nsleep 60\n")
    (bin_dir / "echoer").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    h = signed_up(client)
    there = client.post(A, headers=h, json={"name": "Echo", "command": "echoer --fast", "task": "argument"}).json()
    gone = client.post(A, headers=h, json={"name": "Aider", "command": "aider-not-here --yes", "task": "argument"}).json()
    assert {a["id"]: a["available"] for a in client.get(A, headers=h).json()["agents"]} == \
        {there["id"]: None, gone["id"]: None}
    assert client.post("/api/ade/workspace/start", headers=h).status_code == 200
    assert {a["id"]: a["available"] for a in client.get(A, headers=h).json()["agents"]} == \
        {there["id"]: True, gone["id"]: False}


def test_an_older_workspace_agent_that_cannot_say_leaves_each_one_not_known(client, local, monkeypatch):
    # a workspace machine runs its own image, updated when the provider sees a new one: its agent may come from before
    # `also`, and answer the programs alone; then nothing is known of the person's own, and nothing is greyed out
    from proofpane_open.ade import workspace_client, workspace_routes
    h = signed_up(client)
    agent = client.post(A, headers=h, json={"name": "Echo", "command": "echoer --fast", "task": "argument"}).json()
    monkeypatch.setattr(workspace_routes, "_running", lambda user: "http://127.0.0.1:1")
    monkeypatch.setattr(workspace_client, "call", lambda endpoint, method, path, *a, **k: {"claude": True, "codex": False})
    assert {a["id"]: a["available"] for a in client.get(A, headers=h).json()["agents"]} == {agent["id"]: None}

