"""The workspace agent, run as a real process: its secret, repositories, sessions, terminals and finish."""

import json
import os
import shutil
import subprocess
import sys
import time

import pytest
from websockets.exceptions import InvalidStatus

from proofpane_workspace import ports, terminals
from workspace_fixtures import LEAK, SECRET, Agent, git, make_repo, next_control, read_until


@pytest.fixture
def agent(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home))
    base = make_repo(str(tmp_path / "ws" / "repos" / "demo"), origin="https://github.com/acme/demo.git")
    a.first_commit = base
    yield a
    a.close()


def _session(agent, program="shell", task="Fix the thing\n\nIn more words.", **kw):
    r = agent.http.post("/sessions", json=dict(repo="demo", program=program, task=task, **kw))
    assert r.status_code == 200, r.text
    return r.json()


def test_health_needs_no_secret_and_everything_else_does(agent):
    import httpx
    assert httpx.get(agent.base + "/health").json()["ok"] is True
    for method, path in (("GET", "/repos"), ("POST", "/repos"), ("GET", "/sessions"), ("POST", "/sessions"),
                         ("GET", "/sessions/aaaaaaaaaaaa"), ("POST", "/sessions/aaaaaaaaaaaa/finish"),
                         ("GET", "/repos/demo/issues"), ("GET", "/sessions/aaaaaaaaaaaa/pr"),
                         ("POST", "/sessions/aaaaaaaaaaaa/pr/merge")):
        for headers in ({}, {"Authorization": "Bearer " + "w" * 39}, {"Authorization": "Bearer " + SECRET + "x"},
                        {"Authorization": SECRET}):
            r = httpx.request(method, agent.base + path, headers=headers)
            assert r.status_code == 401, (method, path, headers, r.status_code)


def test_a_terminal_without_the_secret_is_refused_before_it_opens(agent):
    s = _session(agent)
    for secret in (None, "w" * 40 + "x"):
        with pytest.raises(InvalidStatus):
            agent.terminal(s["id"], secret=secret)


def test_repositories_are_listed_with_their_label(agent, tmp_path):
    make_repo(str(tmp_path / "ws" / "repos" / "local"))
    (tmp_path / "ws" / "repos" / "not-a-repo").mkdir()
    repos = {r["name"]: r for r in agent.http.get("/repos").json()["repos"]}
    assert set(repos) == {"demo", "local"}
    assert repos["demo"]["label"] == "acme/demo" and repos["demo"]["branch"] == "main"
    assert repos["demo"]["head"] == agent.first_commit
    assert repos["local"]["label"] == "workspace/local"
    # whether each has a remote to push to: what Ship needs, so a repository without one is offered Finish only
    assert repos["demo"]["origin"] is True and repos["local"]["origin"] is False
    assert repos["demo"]["github"] is True and repos["local"]["github"] is False
    # Ship opens its pull request on GitHub: a remote elsewhere is not one
    git("remote", "add", "origin", "https://gitlab.example/acme/local.git", cwd=str(tmp_path / "ws" / "repos" / "local"))
    repos = {r["name"]: r for r in agent.http.get("/repos").json()["repos"]}
    assert repos["local"]["origin"] is True and repos["local"]["github"] is False


@pytest.mark.parametrize("origin, slug, address", [
    ("https://github.com/acme/local.git", "acme/local", "https://github.com/acme/local.git"),
    ("https://github.com/acme/local", "acme/local", "https://github.com/acme/local.git"),
    ("git@github.com:acme/local.git", "acme/local", "https://github.com/acme/local.git"),
    ("git@github.com:acme/local", "acme/local", "https://github.com/acme/local.git"),
    ("https://gitlab.example/acme/local.git", None, "https://gitlab.example/acme/local.git"),
    ("https://gitlab.example:8443/acme/local.git", None, "https://gitlab.example:8443/acme/local.git"),
    ("https://ana:s3cr3t-token@github.com/acme/local.git", None, None),
    ("https://s3cr3t-token@gitlab.example/acme/local.git", None, None),
    ("https://gitlab.example/acme/local.git?s3cr3t-token", None, None),
    ("https://gitlab.example/acme/local.git#s3cr3t-token", None, None),
    ("http://gitlab.example/acme/local.git", None, None),
    ("ssh://git@gitlab.example/acme/local.git", None, None),
    ("/srv/git/local.git", None, None),
    ("https://gitlab.example/" + "a" * 480 + ".git", None, None),
    (None, None, None)])
def test_a_repository_s_slug_and_address_come_from_its_origin_with_no_user_information(agent, tmp_path, origin, slug,
                                                                                       address):
    # what the app records for a task on the repository: a hand-off pushes
    # to the address; a secret an origin holds is in neither, nor anywhere in the listing
    local = str(tmp_path / "ws" / "repos" / "local")
    make_repo(local)
    if origin is not None:
        git("remote", "add", "origin", origin, cwd=local)
    r = agent.http.get("/repos")
    got = {e["name"]: e for e in r.json()["repos"]}["local"]
    assert (got["slug"], got["address"]) == (slug, address), got
    assert "s3cr3t-token" not in r.text


@pytest.mark.parametrize("url", [
    "file:///etc", "/etc", "ssh://github.com/acme/x.git", "git@github.com:acme/x.git", "http://github.com/acme/x",
    "https://user:pw@github.com/acme/x", "https://token@github.com/acme/x", "https://github.com/acme/x?a=b",
    "https://github.com/acme/x#frag", "https://github.com/acme/../x", "https://github.com:22/acme/x",
    "https://github.com/", "https://github.com/acme/-x", "https://github.com/acme/.git", "ext::sh -c touch% /tmp/x",
    "https://github.com:bad/acme/x", "https://github.com/acme/x y", "https:///acme/x"])
def test_only_a_plain_https_address_is_cloned(agent, tmp_path, url):
    r = agent.http.post("/repos", json={"url": url})
    assert r.status_code == 400, (url, r.text)
    assert sorted(os.listdir(tmp_path / "ws" / "repos")) == ["demo"]


def test_a_repository_that_exists_is_not_cloned_over(agent):
    r = agent.http.post("/repos", json={"url": "https://github.com/other/demo.git"})
    assert r.status_code == 400 and "already has" in r.json()["detail"]


def test_a_session_is_a_worktree_on_its_own_branch(agent, tmp_path):
    s = _session(agent)
    assert s["running"] is True and s["branch"] == f"pp/{s['id']}" and s["base"] == agent.first_commit
    work = tmp_path / "ws" / "sessions" / s["id"] / "work"
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=str(work)) == s["branch"]
    assert git("rev-parse", "HEAD", cwd=str(work)) == agent.first_commit
    assert [x["id"] for x in agent.http.get("/sessions").json()["sessions"]] == [s["id"]]


def test_a_session_starts_from_a_named_commit(agent, tmp_path):
    repo = str(tmp_path / "ws" / "repos" / "demo")
    git("commit", "-q", "--allow-empty", "-m", "second", cwd=repo)
    s = _session(agent, base=agent.first_commit)
    assert s["base"] == agent.first_commit
    s = _session(agent, base="main")
    assert s["base"] == git("rev-parse", "HEAD", cwd=repo)


@pytest.mark.parametrize("body,status", [
    (dict(repo="demo", program="vim", task="t"), 400),
    (dict(repo="demo", program="shell", task=""), 400),
    (dict(repo="demo", program="shell", task="   "), 400),
    (dict(repo="demo", program="shell", task="x" * 4001), 400),
    (dict(repo="demo", program="shell", task="t", base="-q"), 400),
    (dict(repo="demo", program="shell", task="t", base="main..HEAD"), 400),
    (dict(repo="demo", program="shell", task="t", base="no-such-branch"), 400),
    (dict(repo="nope", program="shell", task="t"), 404),
    (dict(repo="../demo", program="shell", task="t"), 400),
    (dict(repo="demo", program="shell", task="t", cols=0), 400),
    (dict(repo="demo", program="shell", task="t", rows=301), 400),
])
def test_a_session_request_is_checked(agent, tmp_path, body, status):
    r = agent.http.post("/sessions", json=body)
    assert r.status_code == status, (body, r.text)
    assert os.listdir(tmp_path / "ws" / "sessions") == []


@pytest.mark.parametrize("raw,detail", [
    (b"[" * 70 + b"]" * 70, "The request nests too deep."),
    (b'{"repo": "demo", "program": "shell", "task": ' + b"[" * 5000 + b"]" * 5000 + b"}", "The request nests too deep."),
    ('{"repo": "demo", "program": "shell", "task": "t"}'.encode("utf-16"), "The request is not UTF-8."),
    (b'{"repo": "demo", "program": "shell", "task": "a\xed\xa0\xbd"}', "The request is not UTF-8."),
    (b'{"repo": "demo", "program": "shell", "task": "a\\ud83d"}',
     "The request holds half of a character (an unpaired surrogate), which is not text."),
    (b"[1]", "The request is a JSON object."),
    (b"{nope", "The request is not JSON."),
])
def test_a_request_body_the_agent_cannot_take_is_refused_in_words(agent, tmp_path, raw, detail):
    r = agent.http.post("/sessions", content=raw, headers={"Content-Type": "application/json"})
    assert (r.status_code, r.json()) == (400, {"detail": detail})
    assert os.listdir(tmp_path / "ws" / "sessions") == []
    # brackets in a string are text, and a whole pair, escaped, is one character
    r = agent.http.post("/sessions", content=b'{"repo": "demo", "program": "shell", "task": "[[[' + b"[" * 100
                        + b' \\ud83d\\ude80"}', headers={"Content-Type": "application/json"})
    assert r.status_code == 200, r.text


def test_a_program_that_is_not_installed_is_named(tmp_path):
    # the agent's whole PATH: a folder of the test's own holding one link, git, and nothing else, so that no codex
    # can be on it on any machine, and the agent still holds a repository, as a workspace without Codex does
    only_git = tmp_path / "only-git"
    only_git.mkdir()
    os.symlink(shutil.which("git"), only_git / "git")
    agent_path = str(only_git)
    assert shutil.which("codex", path=agent_path) is None
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": agent_path})
    try:
        make_repo(str(tmp_path / "ws" / "repos" / "demo"))
        assert a.http.get("/programs").json()["programs"]["codex"] is False
        assert "demo" in [repo["name"] for repo in a.http.get("/repos").json()["repos"]]
        r = a.http.post("/sessions", json=dict(repo="demo", program="codex", task="t"))
        assert r.status_code == 400
        assert "codex is not installed" in r.json()["detail"]
    finally:
        a.close()


def test_an_unknown_session_is_not_found(agent):
    for path in ("/sessions/aaaaaaaaaaaa", "/sessions/aaaa", "/sessions/AAAAAAAAAAAA"):
        assert agent.http.get(path).status_code == 404, path
    assert agent.http.post("/sessions/aaaaaaaaaaaa/finish",
                           json={"author_name": "a", "author_email": "a@b"}).status_code == 404


def test_the_terminal_runs_the_program_and_keeps_its_output(agent):
    s = _session(agent)
    with agent.terminal(s["id"]) as ws:
        ws.send(b"echo marker-$((40+2))\n")
        read_until(ws, b"marker-42")
    with agent.terminal(s["id"]) as ws:     # a reconnect is shown the screen again
        read_until(ws, b"marker-42")


@pytest.mark.skipif(sys.platform != "darwin" or not os.path.exists("/bin/bash"), reason="macOS's own bash only")
def test_a_bash_login_shell_on_a_mac_opens_at_its_prompt_without_the_zsh_notice(tmp_path):
    # macOS's bash says on every start that zsh is now the default, unless told not to: the person whose login shell
    # it is would read that in every shell of every session
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home), {"SHELL": "/bin/bash"})
    try:
        make_repo(str(tmp_path / "ws" / "repos" / "demo"))
        s = _session(a)
        with a.terminal(s["id"]) as ws:
            ws.send(b"echo ready-$((1+1))\n")
            out = read_until(ws, b"ready-2")
        assert b"default interactive shell is now zsh" not in out, out[-400:]
    finally:
        a.close()


# what programs write to ask the terminal something (xterm.js answers each of these it knows); and output that looks
# like them but asks nothing, which a viewer must still be shown
QUESTIONS = (b"\x1b]11;?\x07", b"\x1b]10;?\x1b\\", b"\x1b]4;1;?\x07", b"\x1b[6n", b"\x1b[?6n", b"\x1b[5n", b"\x1b[c",
             b"\x1b[0c", b"\x1b[>c", b"\x1b[>0c", b"\x1b[=c", b"\x1b[>q", b"\x1b[>0q", b"\x1b[?u", b"\x1b[?2004$p",
             b"\x1b[20$p", b"\x1b[14t", b"\x1b[18t", b"\x1b[21t", b"\x1bP$qm\x1b\\", b"\x1bP+q544e\x1b\\")
SCREEN = (b"\x1b[1;31mred\x1b[0m \x1b[2J\x1b[H\x1b[6;1H\x1b[18;1H\x1b[5;3H\x1b]0;a title\x07\x1b]11;#000000\x07"
          b"\x1b]4;1;rgb:ff/00/00\x1b\\\x1b[?2004h\x1b[?25l\x1b[>4;1m\x1b[>1u\x1b[=1;1u\x1b[8;30;100t"
          b"\x1bPq#0;2;0;0;0\x1b\\text\r\n")


@pytest.mark.parametrize("question", QUESTIONS)
def test_a_question_to_the_terminal_is_left_out_of_a_replay_and_nothing_else_is(question):
    assert terminals.without_queries(SCREEN.replace(b"\x1b[6;1H", question + b"\x1b[6;1H" + question)) == SCREEN
    assert terminals.without_queries(question * 3) == b""
    assert terminals.without_queries(SCREEN) == SCREEN


def test_a_viewer_that_is_there_is_asked_and_a_later_one_is_not(agent):
    # a terminal answers what it is shown, and the answers reach the program as keystrokes: a question asked minutes
    # ago, answered on a reconnect, is typed into whatever the program is reading then (GitHub's sign-in read the
    # answers at its prompt and ended with status 1)
    s = _session(agent)
    with agent.terminal(s["id"]) as ws:
        ws.send(b"printf 'asked:\\033]11;?\\007\\033[6n\\033[31mred\\033[0m:end-%s\\n' $((5+5))\n")
        live = read_until(ws, b":end-10")
    assert b"asked:\x1b]11;?\x07\x1b[6n\x1b[31mred\x1b[0m:end-10" in live
    with agent.terminal(s["id"]) as ws:
        later = read_until(ws, b":end-10")
    # (the command line's echo has the questions as typed, with a backslash, not the escape they print as)
    assert b"asked:\x1b[31mred\x1b[0m:end-10" in later and b"\x1b]11;?" not in later and b"\x1b[6n" not in later


def test_a_session_gets_none_of_the_agents_secrets(agent):
    s = _session(agent)
    with agent.terminal(s["id"]) as ws:
        ws.send(b"env | sort; echo end-$((1+1))\n")
        out = read_until(ws, b"end-2")
    assert LEAK.encode() not in out and b"OPEN_EDITION_TOKEN_SECRET" not in out
    assert SECRET.encode() not in out and b"PP_WORKSPACE_SECRET" not in out
    assert b"PP_SESSION_ID=" + s["id"].encode() in out and b"TERM=xterm-256color" in out


def test_resize_reaches_the_program(agent):
    s = _session(agent)
    with agent.terminal(s["id"]) as ws:
        ws.send('{"type": "resize", "cols": 123, "rows": 45}')
        ws.send(b"stty size; echo done-$((2+2))\n")
        out = read_until(ws, b"done-4")
    assert b"45 123" in out


def test_ctrl_c_reaches_the_program_as_in_a_terminal(agent):
    s = _session(agent)
    with agent.terminal(s["id"]) as ws:
        ws.send(b"sleep 60\n")
        time.sleep(0.5)
        t = time.time()
        ws.send(b"\x03")
        ws.send(b"echo back-$((3+3))\n")
        read_until(ws, b"back-6", timeout=10)    # the shell runs echo only once sleep has ended
    assert time.time() - t < 10


def test_exit_is_reported_and_the_session_can_start_again(agent):
    s = _session(agent)
    assert s["running"] is True and s["exit_code"] is None and s["ended_at"] is None
    with agent.terminal(s["id"]) as ws:
        assert next_control(ws) == {"type": "ready"}
        before = int(time.time())
        ws.send(b"exit 3\n")
        assert next_control(ws) == {"type": "exit", "code": 3}
    got = agent.http.get(f"/sessions/{s['id']}").json()
    assert got["running"] is False and got["exit_code"] == 3 and before <= got["ended_at"] <= time.time()
    assert [x["ended_at"] for x in agent.http.get("/sessions").json()["sessions"]] == [got["ended_at"]]
    time.sleep(1.1)                         # the time it ended, not the time it is asked
    assert agent.http.get(f"/sessions/{s['id']}").json()["ended_at"] == got["ended_at"]
    with agent.terminal(s["id"]) as ws:     # afterwards: the last screen and the exit code, and no input taken
        assert next_control(ws) == {"type": "ready"}
        assert b"exit 3" in read_until(ws, b"exit 3")
        assert next_control(ws) == {"type": "exit", "code": 3}
    r = agent.http.post(f"/sessions/{s['id']}/start", json={})
    assert r.status_code == 200 and r.json()["running"] is True and r.json()["ended_at"] is None
    assert agent.http.post(f"/sessions/{s['id']}/start", json={}).status_code == 400   # already running
    with agent.terminal(s["id"]) as ws:
        ws.send(b"echo again-$((4+4))\n")
        read_until(ws, b"again-8")


def test_stop_hangs_up_the_program(agent):
    s = _session(agent)
    r = agent.http.post(f"/sessions/{s['id']}/stop")
    assert r.status_code == 200 and r.json()["running"] is False


def test_close_stops_the_program_and_marks_the_session_closed_and_open_or_start_brings_it_back(agent, tmp_path):
    """A close stops the session's program as stop does and marks its record closed, which the page leaves
    out of its list; nothing else of it changes, its worktree and branch staying. Opening it again, or starting it
    again, takes the mark away."""
    s = _session(agent)
    work = tmp_path / "ws" / "sessions" / s["id"] / "work"
    (work / "kept.txt").write_text("kept\n")
    r = agent.http.post(f"/sessions/{s['id']}/close")
    assert r.status_code == 200, r.text
    assert r.json()["running"] is False and r.json()["closed"] is True
    listed = {v["id"]: v for v in agent.http.get("/sessions").json()["sessions"]}
    assert listed[s["id"]]["closed"] is True                      # the workspace still lists it, marked
    assert (work / "kept.txt").read_text() == "kept\n"
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=str(work)) == s["branch"]
    r = agent.http.post(f"/sessions/{s['id']}/open")
    assert r.status_code == 200 and not r.json().get("closed"), r.text
    assert agent.http.post(f"/sessions/{s['id']}/close").json()["closed"] is True
    started = agent.http.post(f"/sessions/{s['id']}/start", json={})
    assert started.status_code == 200, started.text
    assert started.json()["running"] is True and not started.json().get("closed")
    assert agent.http.post("/sessions/aaaaaaaaaaaa/close").status_code == 404


def test_a_close_marks_the_session_before_its_program_stops_and_unmarks_it_if_the_stop_is_refused(tmp_path):
    """The mark comes first, so a list read while the program stops (a grace of seconds) leaves it out, and
    an open in that time is kept; a stop that fails takes the mark away again."""
    import asyncio
    from proofpane_workspace.workspace import Workspace, WorkspaceError
    root = tmp_path / "ws"
    make_repo(str(root / "repos" / "demo"))
    w = Workspace(str(root))
    sid = w.prepare("demo", "shell", "Look")[0]["id"]
    seen = []

    async def opened_while_it_stops(session_id):
        seen.append(w._read(session_id).get("closed"))
        w.reopen(session_id)                           # the person opens it again before the stop is done
        return w.session(session_id)

    w.stop = opened_while_it_stops
    asyncio.run(w.close(sid))
    assert seen == [True] and not w._read(sid).get("closed")

    async def refused(session_id):
        raise WorkspaceError("refused")

    w.stop = refused
    with pytest.raises(WorkspaceError):
        asyncio.run(w.close(sid))
    assert not w._read(sid).get("closed")


def test_a_close_of_a_task_s_live_attempt_is_refused_before_anything_is_written(tmp_path):
    """A task's attempt's own session is refused (409) as stop refuses it, before
    its record is written at all: an attempt's record is its run's, which the attempt's thread writes too."""
    import asyncio
    from proofpane_workspace import agents as agentry
    from proofpane_workspace.workspace import Workspace
    root = tmp_path / "ws"
    make_repo(str(root / "repos" / "demo"))
    w = Workspace(str(root))
    sid = w.prepare("demo", "shell", "Look")[0]["id"]
    w.agents.task_runs.live[sid] = object()                # held as task_runs holds an attempt while it runs
    written = []
    w._write = written.append
    with pytest.raises(agentry.TaskConflict):
        asyncio.run(w.close(sid))
    assert written == []


def test_a_start_refused_leaves_a_closed_session_closed(tmp_path):
    """A start takes a closed session's mark away once the program has started (for a start as for a chat's
    message), so a start refused leaves it closed and out of the list."""
    import asyncio
    from proofpane_workspace.workspace import Workspace, WorkspaceError
    root = tmp_path / "ws"
    make_repo(str(root / "repos" / "demo"))
    w = Workspace(str(root))
    sid = w.prepare("demo", "shell", "Look")[0]["id"]
    w._update(sid, lambda record: record.__setitem__("closed", True))

    def refused(program):
        raise WorkspaceError("not installed here")

    w.argv = refused

    async def start():
        return w.launch(sid)

    with pytest.raises(WorkspaceError):
        asyncio.run(start())
    assert w._read(sid).get("closed") is True


def test_finish_commits_the_worktree_and_reports_the_diff(agent, tmp_path):
    s = _session(agent)
    work = tmp_path / "ws" / "sessions" / s["id"] / "work"
    (work / "src.py").write_text("def f():\n    return 2\n")
    r = agent.http.post(f"/sessions/{s['id']}/finish", json={"author_name": "Owner", "author_email": "o@example.com"})
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["committed"] is True and rep["base"] == agent.first_commit and rep["head"] != rep["base"]
    assert rep["label"] == "acme/demo" and rep["branch"] == s["branch"] and rep["files"] == 1
    assert "+++ b/src.py" in rep["diff"] and rep["too_large"] is False
    assert git("rev-parse", "HEAD", cwd=str(work)) == rep["head"]
    message = git("log", "-1", "--format=%B", cwd=str(work))
    assert message.startswith("Fix the thing\n\nIn more words.") and f"Proofpane-Session: {s['id']}" in message
    assert git("log", "-1", "--format=%an <%ae>", cwd=str(work)) == "Owner <o@example.com>"
    # nothing new: no commit, the same report
    again = agent.http.post(f"/sessions/{s['id']}/finish",
                            json={"author_name": "Owner", "author_email": "o@example.com"}).json()
    assert again["committed"] is False and again["head"] == rep["head"] and again["diff"] == rep["diff"]
    assert agent.http.get(f"/sessions/{s['id']}").json()["finished"]["head"] == rep["head"]


def test_finish_keeps_an_identity_the_repository_has(agent, tmp_path):
    s = _session(agent)
    work = tmp_path / "ws" / "sessions" / s["id"] / "work"
    git("config", "user.name", "Own Name", cwd=str(work))
    git("config", "user.email", "own@example.com", cwd=str(work))
    (work / "a.txt").write_text("a\n")
    agent.http.post(f"/sessions/{s['id']}/finish", json={"author_name": "Owner", "author_email": "o@example.com"})
    assert git("log", "-1", "--format=%ae", cwd=str(work)) == "own@example.com"


@pytest.mark.parametrize("body", [{}, {"author_name": "a"}, {"author_name": "a", "author_email": "no-at"},
                                  {"author_name": "a\n", "author_email": "a@b"},
                                  {"author_name": "a", "author_email": "a@b\nc"},
                                  {"author_name": "a", "author_email": "<a@b>"}])
def test_finish_names_who_asked(agent, body):
    s = _session(agent)
    assert agent.http.post(f"/sessions/{s['id']}/finish", json=body).status_code == 400


def test_a_diff_too_large_is_not_sent(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    import proofpane_workspace.workspace as w
    ws = w.Workspace(str(tmp_path / "ws"), max_diff=100)
    make_repo(str(tmp_path / "ws" / "repos" / "demo"))
    record, _ = ws.prepare("demo", "shell", "big")
    work = tmp_path / "ws" / "sessions" / record["id"] / "work"
    (work / "big.txt").write_text("x\n" * 200)
    rep = ws.finish(record["id"], "Owner", "o@example.com")
    assert rep["too_large"] is True and rep["diff"] is None and rep["files"] == 1


def test_remove_deletes_the_worktree_and_keeps_the_branch(agent, tmp_path):
    s = _session(agent)
    work = tmp_path / "ws" / "sessions" / s["id"] / "work"
    (work / "kept.txt").write_text("kept\n")
    agent.http.post(f"/sessions/{s['id']}/finish", json={"author_name": "O", "author_email": "o@example.com"})
    r = agent.http.delete(f"/sessions/{s['id']}")
    assert r.status_code == 200 and r.json()["branch"] == s["branch"]
    assert not (tmp_path / "ws" / "sessions" / s["id"]).exists()
    repo = str(tmp_path / "ws" / "repos" / "demo")
    assert "kept.txt" in git("show", "--name-only", "--format=", s["branch"], cwd=repo)
    assert agent.http.get(f"/sessions/{s['id']}").status_code == 404


def test_at_most_max_sessions_run_at_once(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home), {"PP_WORKSPACE_MAX_SESSIONS": "2"})
    try:
        make_repo(str(tmp_path / "ws" / "repos" / "demo"))
        ids = [_session(a)["id"] for _ in range(2)]
        r = a.http.post("/sessions", json=dict(repo="demo", program="shell", task="t"))
        assert r.status_code == 400 and "At most 2" in r.json()["detail"]
        a.http.post(f"/sessions/{ids[0]}/stop")
        assert _session(a)["running"] is True
        assert a.http.post(f"/sessions/{ids[0]}/start", json={}).status_code == 400
    finally:
        a.close()


def test_the_agent_refuses_to_start_without_a_long_secret(tmp_path, own_home):
    import sys
    from workspace_fixtures import ROOT
    for secret in ("", "short"):
        p = subprocess.run([sys.executable, "-m", "proofpane_workspace", "--root", str(tmp_path / "ws")], cwd=ROOT,
                           env={"PATH": os.environ["PATH"], "PP_WORKSPACE_SECRET": secret, "HOME": own_home},
                           capture_output=True, timeout=30)
        assert p.returncode != 0 and b"at least 32 bytes" in p.stderr
        assert not (tmp_path / "ws" / "agent.json").exists()


def _listening(pid):
    """Where a process listens: [(address, port)], from lsof."""
    out = subprocess.run(["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN", "-Fpn"],
                         capture_output=True, text=True).stdout
    return [(a, p) for _, _, a, p in ports.parse_lsof_listen(out)]


def _listeners():
    """{socket inode: (address, port)} of the TCP sockets listening in this network namespace (Linux's /proc); a
    kernel without IPv6 has no tcp6 there, which ports.listening skips too."""
    sockets = {}
    for name, v6 in (("tcp", False), ("tcp6", True)):
        try:
            with open(f"/proc/self/net/{name}", encoding="ascii") as fh:
                sockets.update(ports.parse_proc_net_tcp(fh.read(), v6))
        except FileNotFoundError:
            pass
    return sockets


def test_the_agent_listens_on_localhost_only_and_its_files_are_private(tmp_path):
    # on Linux, the sockets that began to listen as it started: the agent keeps its memory private (prctl, __main__),
    # so not even its own user reads its file descriptors in /proc, and lsof, which reads them there, finds none of
    # its sockets but as root (GitHub's runners have lsof, and run the suite as another user); lsof elsewhere, as
    # ports.listening chooses by the platform too
    home = tmp_path / "home"
    home.mkdir()
    before = _listeners() if sys.platform.startswith("linux") else None
    a = Agent(str(tmp_path / "ws"), str(home))
    try:
        if before is None:
            got = _listening(a.proc.pid)
        else:
            got = [where for inode, where in _listeners().items() if inode not in before]
        assert got == [("127.0.0.1", a.port)]
        assert oct(os.stat(tmp_path / "ws" / "agent.json").st_mode & 0o777) == "0o600"
    finally:
        a.close()


def test_stopping_the_agent_hangs_up_its_programs(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home))
    make_repo(str(tmp_path / "ws" / "repos" / "demo"))
    s = _session(a)
    with a.terminal(s["id"]) as ws:
        ws.send(b"trap '' HUP; echo $$ > pid.txt; echo ready-$((5+5))\n")
        read_until(ws, b"ready-10")
    pid = int((tmp_path / "ws" / "sessions" / s["id"] / "work" / "pid.txt").read_text())
    a.close()
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    raise AssertionError("a program that ignores a hangup outlived the agent")


def test_a_terminal_says_ready_before_any_output(agent):
    s = _session(agent)
    with agent.terminal(s["id"]) as ws:
        ws.send(b"echo first-$((9+9))\n")
        read_until(ws, b"first-18")
    with agent.terminal(s["id"]) as ws:
        first = ws.recv(timeout=10)
        assert first == '{"type": "ready"}'
        read_until(ws, b"first-18")
    with agent.terminal("aaaaaaaaaaaa") as ws:
        assert next_control(ws) == {"type": "error", "detail": "The session's program is not running."}


def test_a_clone_is_listed_only_once_it_is_whole_and_a_failed_one_leaves_nothing(tmp_path, monkeypatch):
    from proofpane_workspace import workspace as ws
    w = ws.Workspace(str(tmp_path / "ws"))
    make_repo(os.path.join(w.repos_dir, "ready"))
    os.makedirs(os.path.join(w.repos_dir, "broken", ".git"))           # git made its folder, and nothing in it yet
    real, seen = ws.git, {}

    def cloning(*args, cwd, **kw):
        if "clone" not in args:
            return real(*args, cwd=cwd, **kw)
        dest = args[-1]
        named = args[-2].rsplit("/", 1)[-1].removesuffix(".git")
        assert os.path.basename(dest).startswith(f"{ws.PARTIAL_CLONE}{named}-")
        real("init", "--quiet", "-b", "main", dest, cwd=cwd)            # a clone makes its .git before its HEAD's commit
        seen["while"] = [r["name"] for r in w.repos()]
        if seen.get("fail"):
            raise ws.WorkspaceError("git clone failed: unable to access the repository")
        real("-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "--allow-empty", "-m", "first", cwd=dest)
        real("remote", "add", "origin", args[-2], cwd=dest)
        return 0, ""

    monkeypatch.setattr(ws, "git", cloning)
    assert w.clone("https://github.com/acme/demo.git") == {"name": "demo", "label": "acme/demo"}
    assert seen["while"] == ["ready"]                     # neither the half-made clone nor the folder git cannot read
    assert [r["name"] for r in w.repos()] == ["demo", "ready"]
    assert [n for n in os.listdir(w.repos_dir) if n.startswith(".")] == []
    with pytest.raises(ws.WorkspaceError, match="already has a repository named demo"):
        w.clone("https://github.com/acme/demo.git")
    seen["fail"] = True
    with pytest.raises(ws.WorkspaceError, match="unable to access"):
        w.clone("https://github.com/acme/other.git")
    assert sorted(os.listdir(w.repos_dir)) == ["broken", "demo", "ready"]
    # a clone that never finished, from long ago, is swept away by the next
    old = os.path.join(w.repos_dir, ws.PARTIAL_CLONE + "gone-x1")
    os.makedirs(old)
    os.utime(old, (1, 1))
    with pytest.raises(ws.WorkspaceError):
        w.clone("https://github.com/acme/third.git")
    assert not os.path.exists(old)


def test_a_repository_git_cannot_read_is_never_taken_for_one_around_the_workspace(tmp_path):
    from proofpane_workspace import workspace as ws
    outer = tmp_path / "outer"
    make_repo(str(outer))                                       # the workspace is inside someone's checkout
    w = ws.Workspace(str(outer / "ws"))
    make_repo(os.path.join(w.repos_dir, "ready"))
    os.makedirs(os.path.join(w.repos_dir, "broken", ".git"))
    assert [r["name"] for r in w.repos()] == ["ready"]
    # nor does anything git is asked of it reach the checkout around it
    with pytest.raises(ws.WorkspaceError, match="not a git repository"):
        ws.git("rev-parse", "HEAD", cwd=os.path.join(w.repos_dir, "broken"))
    with pytest.raises(ws.WorkspaceError):
        w.prepare("broken", "shell", "Work on it")
    assert ws.git("rev-parse", "--show-toplevel", cwd=os.path.join(w.repos_dir, "ready"))[1].strip() == \
        os.path.realpath(os.path.join(w.repos_dir, "ready"))


def test_a_diff_names_a_file_past_ascii_as_it_is_and_verify_reads_every_name_back(agent, tmp_path):
    from proofpane_open import diff as diffs
    s = _session(agent)
    work = tmp_path / "ws" / "sessions" / s["id"] / "work"
    names = ["r\xe9sum\xe9.md", "a b.txt", 'q"x.txt']
    for name in names:
        (work / name).write_text("x\n")
    changes = agent.http.get(f"/sessions/{s['id']}/diff").json()
    assert "+++ b/r\xe9sum\xe9.md" in changes["diff"]                  # not "b/r\\303\\251sum..."
    rep = agent.http.post(f"/sessions/{s['id']}/finish", json={"author_name": "Owner", "author_email": "o@example.com"}).json()
    assert "+++ b/r\xe9sum\xe9.md" in rep["diff"]
    # what Verify reads of the diff a finish gives: every name as it is on disk
    assert sorted(f["path"] for f in diffs.parse(rep["diff"])) == sorted(names)


def test_the_person_s_ssh_agent_reaches_git_and_the_programs_and_nothing_else_does(monkeypatch):
    # git over ssh (a repository added from this machine, its remote by ssh) needs the person's ssh agent, as in their
    # own terminal; what else is in the agent's environment stays out
    from proofpane_workspace import workspace as w
    monkeypatch.setenv("SSH_AUTH_SOCK", "/private/tmp/launch-x/Listeners")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "not-for-programs")
    env = w.program_env()
    assert env["SSH_AUTH_SOCK"] == "/private/tmp/launch-x/Listeners" and "AWS_SECRET_ACCESS_KEY" not in env


def test_a_removed_session_s_branch_with_nothing_of_its_own_goes_and_one_with_commits_stays(agent, tmp_path):
    repo = str(tmp_path / "ws" / "repos" / "demo")
    empty = _session(agent)
    r = agent.http.delete(f"/sessions/{empty['id']}")
    assert r.status_code == 200 and r.json()["branch_kept"] is False
    assert git("branch", "--list", empty["branch"], cwd=repo).strip() == ""          # nothing was on it but its base
    worked = _session(agent)
    (tmp_path / "ws" / "sessions" / worked["id"] / "work" / "kept.txt").write_text("kept\n")
    agent.http.post(f"/sessions/{worked['id']}/finish", json={"author_name": "O", "author_email": "o@example.com"})
    r = agent.http.delete(f"/sessions/{worked['id']}")
    assert r.status_code == 200 and r.json()["branch_kept"] is True
    assert "kept.txt" in git("show", "--name-only", "--format=", worked["branch"], cwd=repo)


def test_a_removed_session_s_branch_checked_out_elsewhere_stays(agent, tmp_path):
    repo = str(tmp_path / "ws" / "repos" / "demo")
    s = _session(agent)
    # the person's own worktree on the same branch (forced, as git allows): its branch is never taken from under it
    git("worktree", "add", "-q", "-f", str(tmp_path / "mine"), s["branch"], cwd=repo)
    r = agent.http.delete(f"/sessions/{s['id']}")
    assert r.status_code == 200 and r.json()["branch_kept"] is True
    assert git("branch", "--list", s["branch"], cwd=repo).strip() != ""



def test_a_removed_session_s_branch_rewound_after_an_attempt_stays_with_its_reflog(agent, tmp_path):
    # the agent commits an attempt, then starts over from where it began: the attempt is on the branch's reflog, and
    # recoverable from it while the branch is there
    repo = str(tmp_path / "ws" / "repos" / "demo")
    s = _session(agent)
    work = str(tmp_path / "ws" / "sessions" / s["id"] / "work")
    (tmp_path / "ws" / "sessions" / s["id"] / "work" / "attempt.txt").write_text("try\n")
    git("add", "attempt.txt", cwd=work)
    git("commit", "-q", "-m", "an attempt", cwd=work)
    attempt = git("rev-parse", "HEAD", cwd=work)
    git("reset", "-q", "--hard", s["base"], cwd=work)
    r = agent.http.delete(f"/sessions/{s['id']}")
    assert r.status_code == 200 and r.json()["branch_kept"] is True
    assert attempt in git("reflog", "show", "--format=%H", s["branch"], cwd=repo)


def test_a_removed_session_s_branch_holding_the_only_way_to_its_start_stays(agent, tmp_path):
    # started from a commit no other ref reaches (a branch since deleted): the session's branch is what keeps it
    repo = str(tmp_path / "ws" / "repos" / "demo")
    git("checkout", "-q", "-b", "scratch", cwd=repo)
    (tmp_path / "ws" / "repos" / "demo" / "scratch.txt").write_text("s\n")
    git("add", "scratch.txt", cwd=repo)
    git("commit", "-q", "-m", "scratch", cwd=repo)
    lone = git("rev-parse", "HEAD", cwd=repo)
    git("checkout", "-q", "-", cwd=repo)
    git("branch", "-q", "-D", "scratch", cwd=repo)
    r = agent.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "From a lone commit", "base": lone})
    assert r.status_code == 200, r.text
    s = r.json()
    r = agent.http.delete(f"/sessions/{s['id']}")
    assert r.status_code == 200 and r.json()["branch_kept"] is True
    assert git("rev-parse", s["branch"], cwd=repo) == lone


def test_a_removed_session_s_branch_stays_when_git_cannot_say_and_the_removal_still_happens(tmp_path, monkeypatch):
    import asyncio
    from proofpane_workspace import workspace as ws_module
    make_repo(str(tmp_path / "ws" / "repos" / "demo"))
    w = ws_module.Workspace(str(tmp_path / "ws"))
    record, _ = w.prepare("demo", "shell", "Gone")
    real = ws_module.git

    def failing(*args, **kw):
        if args and args[0] == "reflog":
            raise ws_module.WorkspaceError("git reflog took longer than 30 seconds.")
        return real(*args, **kw)
    monkeypatch.setattr(ws_module, "git", failing)
    out = asyncio.run(w.remove(record["id"]))
    assert out["branch_kept"] is True and not os.path.exists(w._dir(record["id"]))
    assert git("branch", "--list", record["branch"], cwd=str(tmp_path / "ws" / "repos" / "demo")).strip() != ""


def test_a_removed_session_s_branch_stays_when_its_record_names_another_start(tmp_path):
    import asyncio
    from proofpane_workspace import workspace as ws_module
    repo = str(tmp_path / "ws" / "repos" / "demo")
    make_repo(repo)
    w = ws_module.Workspace(str(tmp_path / "ws"))
    record, _ = w.prepare("demo", "shell", "Moved on")
    (tmp_path / "ws" / "repos" / "demo" / "later.txt").write_text("l\n")
    git("add", "later.txt", cwd=repo)
    git("commit", "-q", "-m", "later", cwd=repo)
    w._update(record["id"], lambda r: r.update(base=git("rev-parse", "HEAD", cwd=repo)))    # as an update leaves it
    assert asyncio.run(w.remove(record["id"]))["branch_kept"] is True


def test_a_removed_session_s_branch_made_to_point_at_main_is_kept_and_main_with_it(agent, tmp_path):
    # a session's program writes its branch as a pointer to main (a plain file write, no reflog entry of its own):
    # every other check passes, and deleting it would delete main; it is kept, and main and its reflog with it
    repo = str(tmp_path / "ws" / "repos" / "demo")
    s = _session(agent)
    common = git("rev-parse", "--git-common-dir", cwd=repo)
    common = common if os.path.isabs(common) else os.path.join(repo, common)
    main = git("rev-parse", "main", cwd=repo)
    with open(os.path.join(common, "refs", "heads", "pp", s["id"]), "w") as fh:
        fh.write("ref: refs/heads/main\n")
    r = agent.http.delete(f"/sessions/{s['id']}")
    assert r.status_code == 200 and r.json()["branch_kept"] is True
    assert git("rev-parse", "main", cwd=repo) == main
    assert git("reflog", "show", "--format=%H", "main", cwd=repo).strip()


def test_a_record_whose_base_is_not_a_commit_is_listed_and_given_to_git_never(agent, tmp_path):
    # a session's programs can write its record; a base that is an option (git diff would write the file it names)
    s = _session(agent)
    record = tmp_path / "ws" / "sessions" / s["id"] / "session.json"
    target = tmp_path / "written-by-git.txt"
    data = json.loads(record.read_text())
    data["base"] = f"--output={target}"
    record.write_text(json.dumps(data))
    listed = next(x for x in agent.http.get("/sessions").json()["sessions"] if x["id"] == s["id"])
    assert listed["base"] is None
    r = agent.http.get(f"/sessions/{s['id']}/diff")
    assert r.status_code == 400 and "no longer names the commit it started from" in r.json()["detail"]
    time.sleep(1)
    assert not target.exists()
    assert agent.http.delete(f"/sessions/{s['id']}").status_code == 200       # and it can still be removed
