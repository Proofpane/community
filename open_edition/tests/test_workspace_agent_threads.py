"""The workspace agent's threads: a running program is waited for on no thread of the pool the
agent's requests run on (asyncio.to_thread), and that pool's size is the agent's own, not the machine's CPUs'. Python
sizes an event loop's default pool by the CPU count, min(32, CPUs + 4) threads, five on a workspace machine's one CPU;
each running program held one of them for its whole run, so with five running every other read waited for good, and
the app said "The workspace is not answering.". Here the agent is given one
CPU by a sitecustomize of the test's own, first on its PYTHONPATH, and stand-ins for claude, codex and gh first on its
PATH, so that no real one answers."""

import asyncio
import concurrent.futures
import os
import shlex
import subprocess
import sys
import threading
import time

import httpx
import pytest

from proofpane_workspace import terminals
from workspace_fixtures import CLAUDE_VERSION_SH, Agent, make_repo

ONE_CPU_POOL = 5            # Python's own pool on one CPU: min(32, 1 + 4)

# one CPU, by both of the counts Python has sized its pool by (os.process_cpu_count from 3.13, os.cpu_count before),
# for the agent given this folder first on its PYTHONPATH, in place of any sitecustomize of the interpreter's own. The
# programs it starts are given no PYTHONPATH (workspace.PASS_ENV), and a terminal's own interpreter runs isolated (-I)
ONE_CPU = "import os\nos.process_cpu_count = os.cpu_count = lambda: 1\n"
COUNTS = "import os; print(getattr(os, 'process_cpu_count', os.cpu_count)(), os.cpu_count())"

# the stand-ins: claude and codex signed out, as the reads ask them (claude's version too, which the gate's floor
# reads); and gh, whose `api user` (GitHub's read of who is signed in) notes that it started, then waits until the test
# lets it go, half a minute at most
CLAUDE = ("#!/bin/sh\n" + CLAUDE_VERSION_SH +
          'if [ "$1 $2" = "auth status" ]; then echo \'{"loggedIn": false, "authMethod": "none"}\'; exit 1; fi\n'
          "exit 2\n")
CODEX = '#!/bin/sh\nif [ "$1 $2" = "login status" ]; then echo "Not logged in" >&2; exit 1; fi\nexit 2\n'
GH = """#!/bin/sh
[ "$1 $2" = "api user" ] || exit 1
touch {started}/$$
i=0
while [ ! -e {go} ] && [ "$i" -lt 300 ]; do sleep 0.1; i=$((i + 1)); done
exit 1
"""


@pytest.fixture
def agent(tmp_path):
    """A workspace agent on one CPU, with the stand-ins first on its PATH, and a repository, demo."""
    site, bin_dir, started = tmp_path / "one-cpu", tmp_path / "bin", tmp_path / "gh-started"
    for folder in (site, bin_dir, started):
        folder.mkdir()
    (site / "sitecustomize.py").write_text(ONE_CPU)
    go = tmp_path / "gh-go"
    for name, text in (("claude", CLAUDE), ("codex", CODEX),
                       ("gh", GH.format(started=shlex.quote(str(started)), go=shlex.quote(str(go))))):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "PYTHONPATH": str(site)}
    # the agent's interpreter, started so, counts one CPU: else its pool is this machine's, and the tests show nothing
    seen = subprocess.run([sys.executable, "-c", COUNTS], env=dict(env, HOME=str(home)), capture_output=True,
                          text=True, timeout=60)
    assert seen.stdout.split() == ["1", "1"], (seen.stdout, seen.stderr)
    a = Agent(str(tmp_path / "ws"), str(home), env)
    make_repo(str(tmp_path / "ws" / "repos" / "demo"))
    a.gh_started, a.gh_go = started, go
    yield a
    a.close()


def _until(check, what, timeout=20):
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, what
        time.sleep(0.05)


def _running(agent):
    """How many programs run in the agent's sessions: each session's own, and its shells."""
    views = agent.http.get("/sessions").json()["sessions"]
    return sum(s["running"] for s in views) + sum(sh["running"] for s in views for sh in s["shells"])


def _read(agent, path):
    """A read of `path`, waited for two seconds at most: (its status, or "no answer", the seconds it took, its JSON)."""
    began = time.monotonic()
    try:
        r = agent.http.get(path, timeout=2)
    except httpx.TimeoutException:
        return "no answer", round(time.monotonic() - began, 2), None
    return r.status_code, round(time.monotonic() - began, 2), r.json() if r.status_code == 200 else r.text


def test_more_programs_running_than_one_cpu_s_pool_leave_the_reads_answering(agent):
    # seven programs, more than one CPU's five threads: two shell sessions' own, and five shells opened beside them,
    # each a shell at its prompt, running until it is hung up (a shell opens on the event loop, with no thread of the
    # pool, so each opens whatever the pool holds)
    ids = []
    for _ in range(2):
        r = agent.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "Run beside the reads"})
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
    for sid in [ids[0]] * 4 + [ids[1]]:
        r = agent.http.post(f"/sessions/{sid}/shell", json={})
        assert r.status_code == 200, r.text
    _until(lambda: _running(agent) == 7, "the seven programs did not run")
    got = {path: _read(agent, path) for path in ("/repos", "/claude", "/accounts")}
    assert {path: (status, seconds < 1) for path, (status, seconds, _) in got.items()} == {
        "/repos": (200, True), "/claude": (200, True), "/accounts": (200, True)}, got
    assert [r["name"] for r in got["/repos"][2]["repos"]] == ["demo"]
    assert (got["/claude"][2]["installed"], got["/claude"][2]["signed_in"]) == (True, False)      # the stand-in's word
    assert got["/accounts"][2]["claude"]["accounts"][0]["status"]["signed_in"] is False
    assert _running(agent) == 7                         # answered beside the programs, not once they had ended


def test_more_slow_reads_at_once_than_one_cpu_s_pool_leave_the_others_answering(agent):
    # six of GitHub's reads at once, one more than one CPU's pool, each holding a thread of the agent's while its gh
    # runs (up to GITHUB_TIMEOUT, half a minute), as a burst of the page's reads of `claude auth status` held them: a
    # read beside them answers at once all the same, and all six run together
    answers = []

    def read_github():
        with httpx.Client(base_url=agent.base, headers={"Authorization": "Bearer " + agent.secret}, timeout=60) as c:
            answers.append(c.get("/github"))

    readers = [threading.Thread(target=read_github) for _ in range(ONE_CPU_POOL + 1)]
    for t in readers:
        t.start()
    try:
        _until(lambda: len(os.listdir(agent.gh_started)) >= ONE_CPU_POOL, "five of GitHub's reads did not start")
        status, seconds, body = _read(agent, "/repos")
        assert (status, seconds < 1) == (200, True), (status, seconds)
        assert [r["name"] for r in body["repos"]] == ["demo"]
        _until(lambda: len(os.listdir(agent.gh_started)) == ONE_CPU_POOL + 1, "the six did not run at once")
    finally:
        agent.gh_go.touch()
        for t in readers:
            t.join(60)
    assert [(r.status_code, r.json()["installed"], r.json()["user"]) for r in answers] == [(200, True, None)] * 6


@pytest.mark.parametrize("way", ["pidfd", "thread"])
def test_a_running_program_is_waited_for_on_no_thread_of_its_loop_s_pool(tmp_path, monkeypatch, way):
    # the loop's own pool cut to one thread, and three programs running in terminals: a thread's work (a request's,
    # asyncio.to_thread) runs at once all the same, and each program's exit is seen, with its code. Linux's way is the
    # program's process descriptor (os.pidfd_open), which the loop watches; elsewhere (a Mac), or where the system
    # refuses one, a thread of the program's own waits for it
    if way == "pidfd" and not hasattr(os, "pidfd_open"):
        pytest.skip("os.pidfd_open is Linux's")
    if way == "thread":
        monkeypatch.delattr(os, "pidfd_open", raising=False)

    async def run():
        asyncio.get_running_loop().set_default_executor(concurrent.futures.ThreadPoolExecutor(1))
        env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "LANG": "C"}
        terms = [terminals.Terminal(["/bin/sh", "-c", "read line; exit 7"], str(tmp_path), env) for _ in range(3)]
        try:
            await asyncio.sleep(0.2)                    # each one's wait under way
            began = time.monotonic()
            answer = await asyncio.wait_for(asyncio.to_thread(lambda: "answered"), 2)
            took = time.monotonic() - began
            running = [t.running for t in terms]
            for t in terms:
                await t.write(b"done\n")
            deadline = time.monotonic() + 20
            while any(t.running for t in terms) and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            return answer, took, running, [t.exit_code for t in terms]
        finally:
            await asyncio.gather(*(t.stop() for t in terms))

    answer, took, running, codes = asyncio.run(run())
    assert (answer, took < 1) == ("answered", True), took
    assert running == [True] * 3 and codes == [7] * 3
