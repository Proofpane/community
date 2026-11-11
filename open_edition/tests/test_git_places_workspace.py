"""Git's places through the workspace (gitplaces.py, workspace.py, agents.py): an agent's run can write any repository
of the workspace and the user's git settings, not only its own repository; what it leaves there for the workspace's
own git to start later is never started by it. The workspace's reads run no hook, no fsmonitor and no lazy fetch
whatever the settings say; the owner's own actions run the hooks he installed; and a change while a run worked holds
the repository, and the agents that worked, until the owner resumes one. Through a real workspace agent with the
stand-in claude, as test_agents_watch.py's are."""

import json
import os
import subprocess
import threading
import time

import pytest

from proofpane_workspace import gitplaces
from proofpane_workspace import workspace as ws_module
from proofpane_workspace.workspace import WorkspaceError
from test_agents_runs import AGENT, Workspace, _answer, put, run, until, view
from workspace_fixtures import git, make_repo

WHO = {"author_name": "Ana", "author_email": "ana@example.org"}


@pytest.fixture
def ws(tmp_path):
    make_repo(str(tmp_path / "ws" / "repos" / "other"))       # another repository, there before the workspace starts
    w = Workspace(tmp_path)
    yield w
    w.stop()


def script(ws, name, text):
    path = ws.tmp / name
    path.write_text("#!/bin/sh\nset -e\n" + text)
    path.chmod(0o755)
    return str(path)


def paused(ws):
    return ws.http.get(f"/agents/{AGENT}").json()["paused"]


def resume(ws):
    put(ws, "hello", paused=True)                       # the app mirrored it paused, then the owner resumed it
    put(ws, "hello")


def session(ws, repo):
    r = ws.http.post("/sessions", json={"repo": repo, "program": "shell", "task": "Look around"})
    assert r.status_code == 200, r.text
    return r.json()["id"], os.path.join(ws.root, "sessions", r.json()["id"], "work")


def changed_in(work):
    with open(os.path.join(work, "README.md"), "a") as fh:
        fh.write("the owner's own line\n")


def planting(ws, marker, then=""):
    """A script that leaves a clean filter in the repository other (its config, and its info/attributes giving it to
    every file): what the workspace's git would start at its next read of a session there."""
    other = os.path.join(ws.root, "repos", "other", ".git")
    filt = script(ws, "filter.sh", f"touch {marker}\ncat\n")
    return script(ws, "plant.sh", f"printf '[filter \"f\"]\\n\\tclean = {filt}\\n' >> {other}/config\n"
                                  f"printf '* filter=f\\n' > {other}/info/attributes\n"
                                  f"printf '#!/bin/sh\\ntouch {marker}\\n' > {other}/hooks/post-checkout\n"
                                  f"chmod +x {other}/hooks/post-checkout\n{then}")


def test_a_run_that_plants_in_another_repository_is_held_at_its_end_and_nothing_of_it_runs(ws):
    marker = ws.tmp / "planted-ran"
    sid, work = session(ws, "other")
    changed_in(work)
    put(ws, f"run {planting(ws, marker)}")
    r = run(ws)["run_id"]
    _answer(ws, r)
    ended = until(ws, r, ("blocked",))
    assert ended["cause"] == "watch" and ended["reason"].startswith(
        "The git settings of the repository other changed while an agent's run worked (config filter.f.clean, "
        "hooks/post-checkout, info/attributes), so no git of the workspace's runs there")
    # its end's record carries the hold its look found, with no late hold beside it (read as it ends: that the pump
    # after the end does not report it again is shown in this process, by
    # test_a_run_s_end_s_record_carries_what_its_look_found_and_a_hold_found_meanwhile_comes_after)
    assert ended["late_hold"] is None
    assert paused(ws) is True
    # the owner's session there: its change is not read, a new one is not made, and what was planted never ran
    diff = ws.http.get(f"/sessions/{sid}/diff")
    assert diff.status_code == 400 and diff.json()["detail"].startswith(
        "The git settings of the repository other changed while an agent's run worked")
    listed = ws.http.get("/sessions").json()
    made = ws.http.post("/sessions", json={"repo": "other", "program": "shell", "task": "x"})
    assert made.status_code == 400 and "resume the agent held for it" in made.json()["detail"]
    (held,) = [r for r in ws.http.get("/repos").json()["repos"] if r["name"] == "other"]
    assert "changed while an agent's run worked" in held["held"]
    time.sleep(1)                                       # the sizes' thread, which a listing starts, had its turn
    assert not marker.exists() and listed
    # its own repository is another's: its git runs
    assert ws.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "x"}).status_code == 200
    resume(ws)                                          # the owner looked: what is there now is accepted
    assert paused(ws) is False
    assert ws.http.post("/sessions", json={"repo": "other", "program": "shell", "task": "x"}).status_code == 200


def test_a_change_found_while_the_run_still_works_holds_before_its_end(ws):
    marker = ws.tmp / "planted-ran"
    sid, work = session(ws, "other")
    changed_in(work)
    planted = ws.tmp / "planted"
    put(ws, f"run {planting(ws, marker, then=f'touch {planted}; sleep 6')}")
    r = run(ws)["run_id"]
    _answer(ws, r)
    deadline = time.time() + 30
    while not planted.exists():
        assert time.time() < deadline
        time.sleep(0.05)
    diff = ws.http.get(f"/sessions/{sid}/diff")         # the owner's session there, while the run still works
    assert diff.status_code == 400 and "while an agent's run worked" in diff.json()["detail"]
    assert not marker.exists()
    ended = until(ws, r, ("blocked",), timeout=60)
    assert "The git settings of the repository other changed" in ended["reason"]


def test_the_user_s_git_settings_changed_by_a_run_hold_every_git_of_the_workspace(ws):
    marker = ws.tmp / "filter-ran"
    filt = script(ws, "filter.sh", f"touch {marker}\ncat\n")
    plant = script(ws, "home.sh", f"printf '[filter \"f\"]\\n\\tclean = {filt}\\n' >> {ws.home}/.gitconfig\n"
                                  f"mkdir -p {ws.home}/.config/git\nprintf '* filter=f\\n' > "
                                  f"{ws.home}/.config/git/attributes\n")
    sid, work = session(ws, "other")
    changed_in(work)
    put(ws, f"run {plant}")
    r = run(ws)["run_id"]
    _answer(ws, r)
    ended = until(ws, r, ("blocked",))
    assert "Your git settings outside the repositories changed while an agent's run worked" in ended["reason"]
    for repo in ("demo", "other"):
        made = ws.http.post("/sessions", json={"repo": repo, "program": "shell", "task": "x"})
        assert made.status_code == 400 and "no git of the workspace's runs anywhere" in made.json()["detail"]
    assert ws.http.get(f"/sessions/{sid}/diff").status_code == 400
    assert not marker.exists()
    resume(ws)
    assert ws.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "x"}).status_code == 200


def test_the_workspace_s_reads_run_no_hook_and_no_fsmonitor_the_owner_set(ws):
    """The owner's own settings, his and accepted: the workspace's reads (the size of a session's change, its diff)
    start none of them all the same, as RUN_GIT says; his own Finish runs his hooks, as his own git would."""
    other = os.path.join(ws.root, "repos", "other", ".git")
    marks = {name: ws.tmp / name for name in ("fsmonitor", "post-index-change", "pre-commit")}
    fsmonitor = script(ws, "fsmonitor.sh", f"touch {marks['fsmonitor']}\n")
    git("config", "core.fsmonitor", fsmonitor, cwd=other)
    for name in ("post-index-change", "pre-commit"):
        hook = os.path.join(other, "hooks", name)
        with open(hook, "w") as fh:
            fh.write(f"#!/bin/sh\ntouch {marks[name]}\n")
        os.chmod(hook, 0o755)
    sid, work = session(ws, "other")
    changed_in(work)
    for mark in marks.values():
        if mark.exists():
            mark.unlink()                               # New session's own (the owner's action) is not a read
    ws.http.get("/sessions")
    deadline = time.time() + 30
    while ws.http.get("/sessions").json()["sessions"][0].get("diff") is None:
        assert time.time() < deadline                   # its size, worked out in the background
        time.sleep(0.1)
    assert ws.http.get(f"/sessions/{sid}/diff").status_code == 200
    assert [n for n, m in marks.items() if m.exists()] == []
    done = ws.http.post(f"/sessions/{sid}/finish", json=WHO)
    assert done.status_code == 200, done.text
    assert marks["pre-commit"].exists()                 # his own Finish runs his hook


def test_a_hook_the_owner_installs_while_no_run_works_is_his_and_held_nothing(ws):
    put(ws, "hello")
    until(ws, run(ws)["run_id"], ("done",))             # a run, ended: what changes after it is not its
    other = os.path.join(ws.root, "repos", "other", ".git")
    marker = ws.tmp / "post-checkout"
    hook = os.path.join(other, "hooks", "post-checkout")
    with open(hook, "w") as fh:
        fh.write(f"#!/bin/sh\ntouch {marker}\n")
    os.chmod(hook, 0o755)
    sid, work = session(ws, "other")
    assert marker.exists()                              # New session, his own action, ran his hook
    assert paused(ws) is False


def test_a_session_removed_and_a_session_made_while_a_run_works_hold_nothing(ws):
    sid, _ = session(ws, "other")
    put(ws, "sleep 6")
    r = run(ws)["run_id"]
    until(ws, r, ("working",))
    assert ws.http.delete(f"/sessions/{sid}").status_code == 200
    session(ws, "other")
    assert until(ws, r, ("done",), timeout=60)["hold"] is None
    assert paused(ws) is False


def test_a_record_of_accepted_settings_rewritten_while_the_workspace_was_stopped_is_not_believed(ws):
    put(ws, "hello")
    r = run(ws)["run_id"]
    until(ws, r, ("done",))
    ws.stop()
    path = os.path.join(ws.root, gitplaces.RECORD)
    with open(path) as fh:
        text = fh.read()
    with open(path, "w") as fh:
        fh.write(text.replace('"how": "', '"how": "forged: '))
    ws.start()
    made = ws.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "x"})
    assert made.status_code == 400 and gitplaces.UNTRUSTED in made.json()["detail"]
    deadline = time.time() + 10
    while not paused(ws) or gitplaces.UNTRUSTED not in (until(ws, r, ("done",))["hold"] or ""):
        assert time.time() < deadline                   # every agent held with it, why on its latest run
        time.sleep(0.05)
    resume(ws)
    assert ws.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "x"}).status_code == 200


def test_a_hold_found_after_a_run_s_end_is_on_its_record_for_the_app_once(ws):
    # an older release wrote a hold found after a run's end only as that run's `hold`, which the app no longer reads
    # once it has the run as ended: the agent was held here and shown not paused there. It is reported on the record as
    # `late_hold`, with the change number of that write, which the app takes once (its _apply); and reported once,
    # across the workspace's restarts too
    put(ws, "hello")
    r = run(ws)["run_id"]
    assert until(ws, r, ("done",))["late_hold"] is None
    ws.stop()
    path = os.path.join(ws.root, gitplaces.RECORD)
    with open(path) as fh:
        text = fh.read()
    with open(path, "w") as fh:
        fh.write(text.replace('"how": "', '"how": "forged: '))
    ws.start()
    assert ws.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "x"}).status_code == 400
    deadline = time.time() + 10
    while view(ws, r)["late_hold"] is None:
        assert time.time() < deadline
        time.sleep(0.05)
    # what else the first look holds is reported by then: the run's record unchanged for a second
    got, still = view(ws, r), time.time()
    while time.time() - still < 1:
        assert time.time() < deadline + 10
        time.sleep(0.1)
        now = view(ws, r)
        if now["seq"] != got["seq"]:
            got, still = now, time.time()
    assert got["state"] == "done" and paused(ws) is True
    assert gitplaces.UNTRUSTED in got["late_hold"]["words"] and got["late_hold"]["seq"] == got["seq"]
    assert gitplaces.UNTRUSTED in got["hold"]
    ws.stop()
    ws.start()
    # a smoke check, after a second for the start's pump: the strict one, a start's report run to its end and the run
    # read after it, is test_a_stop_between_a_run_s_end_s_look_and_its_record_leaves_what_it_found_to_report_after_
    # the_start, in this process
    time.sleep(1)
    assert view(ws, r)["seq"] == got["seq"] and view(ws, r)["late_hold"] == got["late_hold"]


def test_a_repository_an_agent_s_run_makes_is_held_at_its_end_and_across_a_restart(ws):
    # an agent's own run's end looks at every folder git runs in, as a task's attempt's does. An older release's looked
    # only at the folders its record knew, so a repository the run made was first looked at after the workspace's next
    # start, whose suspects are counted from that start: taken as the owner's, and its hook run at his next New session
    # there
    marker = ws.tmp / "made-ran"
    made = os.path.join(ws.root, "repos", "made")
    who = "-c user.name=t -c user.email=t@example.org"
    maker = script(ws, "make.sh", f"git init -q -b main {made}\n"
                                  f"git -C {made} {who} commit -q --allow-empty -m first\n"
                                  f"printf '#!/bin/sh\\ntouch {marker}\\n' > {made}/.git/hooks/post-checkout\n"
                                  f"chmod +x {made}/.git/hooks/post-checkout\n")
    put(ws, f"run {maker}")
    r = run(ws)["run_id"]
    _answer(ws, r)
    ended = until(ws, r, ("blocked", "done"))
    assert ended["state"] == "blocked" and ended["cause"] == "watch", ended
    assert "The git settings of the repository made changed while an agent's run worked" in ended["reason"]
    assert paused(ws) is True
    ws.stop()
    ws.start()
    held = ws.http.post("/sessions", json={"repo": "made", "program": "shell", "task": "x"})
    assert held.status_code == 400 and "changed while an agent's run worked" in held.json()["detail"]
    assert not marker.exists()
    resume(ws)
    assert ws.http.post("/sessions", json={"repo": "made", "program": "shell", "task": "x"}).status_code == 200


@pytest.mark.parametrize("goes_on", [True, False], ids=["the run goes on", "the run fails at its start"])
def test_a_repository_an_agent_s_precheck_makes_is_held_and_across_a_restart(ws, goes_on):
    # the precheck makes a repository, and the run goes on to its end, or fails at its start (the precheck says no),
    # whose path looks at git's places as an end does (agents._start). Either way it is held then, with the agent
    # (a suspect while its precheck ran), and across the workspace's restart
    marker = ws.tmp / "made-ran"
    made = os.path.join(ws.root, "repos", "made")
    who = "-c user.name=t -c user.email=t@example.org"
    maker = script(ws, "make.sh", f"git init -q -b main {made}\n"
                                  f"git -C {made} {who} commit -q --allow-empty -m first\n"
                                  f"printf '#!/bin/sh\\ntouch {marker}\\n' > {made}/.git/hooks/post-checkout\n"
                                  f"chmod +x {made}/.git/hooks/post-checkout\n")
    put(ws, "hello", precheck=maker if goes_on else f"{maker}; exit 3")
    r = run(ws)["run_id"]
    ended = until(ws, r, ("blocked", "done", "skipped", "failed"))
    assert ended["state"] == "blocked" and ended["cause"] == "watch", ended
    assert "The git settings of the repository made changed while an agent's run worked" in ended["reason"]
    assert ("started_at" in ended and ended["started_at"] is not None) is goes_on, ended
    assert paused(ws) is True
    ws.stop()
    ws.start()
    held = ws.http.post("/sessions", json={"repo": "made", "program": "shell", "task": "x"})
    assert held.status_code == 400 and "changed while an agent's run worked" in held.json()["detail"]
    assert not marker.exists()
    resume(ws)
    assert ws.http.post("/sessions", json={"repo": "made", "program": "shell", "task": "x"}).status_code == 200


class _Places:
    """A stand-in for the record of git's places, as agents._report_late reads it: the holds held for AGENT, and the
    ones a record of its runs was told of."""

    def __init__(self, found):
        self.found, self.told = list(found), []

    def holding(self, agent_id):
        return list(self.found)

    def unreported(self, agent_id):
        return [f for f in self.found if (agent_id, f[0]) not in self.told]

    def for_report(self, agent_id):
        return self.unreported(agent_id), self.holding(agent_id)

    def reported(self, agent_id, commons):
        self.told += [(agent_id, c) for c in commons]

    def held(self):
        return {c: {"agents": [AGENT], "reported": [a for a, x in self.told if x == c]} for c, _, _ in self.found}


def test_a_hold_no_record_carries_yet_goes_on_the_agent_s_latest_run_when_no_run_of_it_is_live(tmp_path):
    # agents._report_late, in this process. Not while the
    # agent's latest run works, is being ended or is being started, whose end looks at git's places and reports what
    # they hold; else as `late_hold` with the change number of its own write, once, and as the ended run's `hold` where
    # it has none; on a queued run, its late hold alone
    from proofpane_workspace import agents as agentry
    from test_agents_runs import _in_process
    agents, r = _in_process(tmp_path)                   # its run working
    found = [("/c/other", "other", ["hooks/pre-push"])]
    words = agentry._hold_reason([], [], [], found)
    agents.ws.places = _Places(found)
    agents._report_late(AGENT)
    assert "late_hold" not in agents.read(r)["run"] and agents.ws.places.told == []
    agents._change(r, lambda rec: rec["run"].update(state="done", ended_at=100, queued_at=1))
    for busy in (agents.ending, agents._starting):
        busy.add(r)
        agents._report_late(AGENT)
        busy.discard(r)
        assert "late_hold" not in agents.read(r)["run"], busy
    agents._report_late(AGENT)
    run = agents.read(r)["run"]
    assert run["late_hold"] == {"seq": run["seq"], "words": words} and run["hold"] == words
    assert agents.view(agents.read(r))["late_hold"] == run["late_hold"]
    agents._report_late(AGENT)                          # reported: once
    assert agents.read(r)["run"]["seq"] == run["seq"] and agents.ws.places.told == [(AGENT, "/c/other")]
    agents._change(r, lambda rec: rec["run"].update(hold="what its end found"))
    queued = agents.queue(AGENT)["run_id"]
    agents.ws.places.found.append((gitplaces.USER, "your git settings", ["config core.pager"]))
    agents._starting.add(r)                             # an earlier run of it being started: not now, either
    agents._report_late(AGENT)
    agents._starting.discard(r)
    assert "late_hold" not in agents.read(queued)["run"]
    agents._report_late(AGENT)
    later = agents.read(queued)["run"]
    assert later["state"] == "queued" and "hold" not in later
    # the words of every place held for it now, the one reported before among them: the app takes the newest late
    # hold of a run alone, so a second found before its next read must not leave the first unsaid
    assert later["late_hold"]["words"] == agentry._hold_reason([], [], [], agents.ws.places.found)
    assert "Your git settings outside the repositories changed" in later["late_hold"]["words"]
    assert later["late_hold"]["seq"] == later["seq"] and agents.read(r)["run"]["hold"] == "what its end found"
    agents._change(queued, lambda rec: rec["run"].update(state="stopped", ended_at=200, hold="its own"))
    agents.ws.places.found.append(("/c/demo", "demo", ["hooks/pre-commit"]))
    agents._report_late(AGENT)                          # an ended run with a hold of its own keeps it
    stopped = agents.read(queued)["run"]
    assert stopped["hold"] == "its own"
    assert stopped["late_hold"]["words"] == agentry._hold_reason([], [], [], agents.ws.places.found)
    assert stopped["late_hold"]["seq"] == stopped["seq"] > later["seq"]


def test_every_pump_reports_a_hold_no_record_of_its_agent_s_runs_carries_yet(tmp_path):
    # what a hold found between a run's end's look and that end's own record, or just before the machine stopped,
    # left unreported: the next pump reports it (agents._late_soon), after every end and at the agent's start
    import asyncio
    from test_agents_runs import _in_process
    agents, r = _in_process(tmp_path)
    agents._change(r, lambda rec: rec["run"].update(state="done", ended_at=100))
    agents.ws.places = _Places([("/c/other", "other", ["hooks/pre-push"])])

    async def pumped():
        agents.started = True
        agents._pump_lock = asyncio.Lock()
        agents.pump()
        deadline = time.time() + 10
        while agents._late_running:
            assert time.time() < deadline
            await asyncio.sleep(0.01)
    asyncio.run(pumped())
    assert agents.read(r)["run"]["late_hold"]["words"].startswith("The git settings of the repository other")


# ---- git's places held in this process with a real record of them ---------------------------------------------------

def _checker(tmp_path, agents):
    """The record of git's places, as the workspace makes it, over the agents in this process (its file in `places`)."""
    os.makedirs(tmp_path / "places", exist_ok=True)
    return gitplaces.Checker(str(tmp_path / "places"), b"k" * 32, suspects=agents.suspects,
                             hold=agents.hold_for_places, known=agents.known, folders=[str(tmp_path / "repos")])


def _placed(tmp_path, monkeypatch):
    """The agents in this process (test_agents_runs._in_process: its run working) with a real record of git's places
    and two repositories it has looked at: the run's agent a suspect while the run works, is being ended or started, or
    ended since a place was last looked at (agents.suspects), held by agents.hold_for_places on the record's thread."""
    from test_agents_runs import _in_process
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    agents, r = _in_process(tmp_path)
    tops = {name: str(tmp_path / "repos" / name) for name in ("demo", "other")}
    for top in tops.values():
        make_repo(top)
    agents.ws.places = _checker(tmp_path, agents)
    agents.ws.places.first_look(list(tops.values()))
    return agents, r, tops


def _hook(top, name):
    """A hook planted in the repository at `top`; its common git folder."""
    common = gitplaces.git_folder(top)[1]
    path = os.path.join(common, "hooks", name)
    with open(path, "w") as fh:
        fh.write("#!/bin/sh\nexit 0\n")
    os.chmod(path, 0o755)
    return common


def _holds_settled():
    """The record of git's places calls agents.hold_for_places on a thread of its own: each one done."""
    for t in threading.enumerate():
        if t.name == "pp-git-places-hold":
            t.join(10)
            assert not t.is_alive()


def _ending(agents):
    """agents.end in this process: the parts of it that need a workspace's programs stood in for (what it watched did
    not change, its worktree commits nothing, it measures nothing); git's places looked at for real."""
    agents._seen = lambda run_id: ([], [])
    agents._settle = lambda run_id, record, c, commit=True: (None, {}, [])
    agents._metrics = lambda run_id, c, ended_at: None
    agents._memory_at_end = lambda run_id, c: None
    agents._keep_within_storage = lambda agent_id: None


def _pumped(agents):
    """A pump, and the sweep it starts for holds no written record carries (agents._late_soon), to its end."""
    import asyncio

    async def pump():
        agents.started = True
        agents._pump_lock = asyncio.Lock()
        agents.pump()
        deadline = time.time() + 10
        while agents._late_running:
            assert time.time() < deadline
            await asyncio.sleep(0.01)
    asyncio.run(pump())


def test_a_hold_a_git_finds_after_a_run_s_end_is_on_its_record_at_once_whatever_report_runs(tmp_path, monkeypatch):
    # the agent's run ended, nothing queued, and a git of the workspace's own finds a
    # change held for it. The record of git's places calls agents.hold_for_places on a thread of its own: the check
    # that decided it does not wait for a report holding _late_lock (it raises Held at once), and the hold is on the
    # run's record as a late hold once that lock is free, with no pump (none runs in this process)
    from proofpane_workspace import agents as agentry
    agents, r, tops = _placed(tmp_path, monkeypatch)
    now = int(time.time())
    agents._change(r, lambda rec: rec["run"].update(state="done", question=None, started_at=now - 5, ended_at=now))
    demo = _hook(tops["demo"], "pre-push")
    with agents._late_lock:                              # a report in progress
        began = time.monotonic()
        with pytest.raises(gitplaces.Held):
            agents.ws.places.check_at(tops["demo"])
        assert time.monotonic() - began < 5
        assert "late_hold" not in agents.read(r)["run"]
    _holds_settled()
    run = agents.read(r)["run"]
    words = agentry._hold_reason([], [], [], [(demo, "demo", ["hooks/pre-push"])])
    assert run["late_hold"] == {"seq": run["seq"], "words": words} and run["hold"] == words
    assert agents.started is False and agents.agent(AGENT)["paused"] is True
    assert agents.ws.places.unreported(AGENT) == []


def test_a_run_s_end_s_record_carries_what_its_look_found_and_a_hold_found_meanwhile_comes_after(tmp_path,
                                                                                                    monkeypatch):
    # through agents.end, in this process: what the end's look finds is marked
    # reported only once the end's record, which carries it, is written; a hold another git finds between that look
    # and that record is not on it, nor reported while the run is being ended, and the pump after the end reports it,
    # once, as a late hold; the end's own is not reported again, by that pump or the next
    import asyncio
    agents, r, tops = _placed(tmp_path, monkeypatch)
    _ending(agents)
    demo = _hook(tops["demo"], "pre-push")              # changed while the run worked: the end's look finds it
    other = gitplaces.git_folder(tops["other"])[1]

    def meanwhile(run_id, record, c, commit=True):
        assert agents.ws.places.unreported(AGENT) == [(demo, "demo", ["hooks/pre-push"])]     # the look marked none
        _hook(tops["other"], "post-merge")
        with pytest.raises(gitplaces.Held):
            agents.ws.places.check_at(tops["other"])    # another git, while the run is being ended
        _holds_settled()
        assert "late_hold" not in agents.read(run_id)["run"]
        return None, {}, []
    agents._settle = meanwhile
    asyncio.run(agents.end(r, "done", None))            # no pump runs after it here (agents.started is False)
    ended = agents.read(r)["run"]
    assert ended["state"] == "blocked" and ended["cause"] == "watch" and "late_hold" not in ended
    assert "The git settings of the repository demo" in ended["hold"] and "repository other" not in ended["hold"]
    # its record written, what it carries is marked: what was found meanwhile is all that is left to report
    assert agents.ws.places.unreported(AGENT) == [(other, "other", ["hooks/post-merge"])]
    _pumped(agents)
    late = agents.read(r)["run"]
    assert "The git settings of the repository other" in late["late_hold"]["words"]
    assert late["late_hold"]["seq"] == late["seq"] and late["hold"] == ended["hold"]
    assert agents.ws.places.unreported(AGENT) == []
    _pumped(agents)
    assert agents.read(r)["run"]["seq"] == late["seq"]


def test_a_stop_between_a_run_s_end_s_look_and_its_record_leaves_what_it_found_to_report_after_the_start(
        tmp_path, monkeypatch):
    # the end's look found a hold and marked nothing; the machine stopped before the end's
    # record, so no record carries it. The next start's scan blocks the run (the machine stopped) and its report puts
    # the hold on it; after a later start it is not reported again (in this process)
    from types import SimpleNamespace
    from proofpane_workspace import agents as agentry
    agents, r, tops = _placed(tmp_path, monkeypatch)
    demo = _hook(tops["demo"], "pre-push")
    agents.ending.add(r)
    assert agents._places_seen(AGENT) == [(demo, "demo", ["hooks/pre-push"])]      # the end's look, then the stop
    _holds_settled()
    seqs = []
    for _ in range(2):
        again = agentry.Agents(SimpleNamespace(root=str(tmp_path), sessions_dir=str(tmp_path / "sessions"),
                                               chat_settings=lambda s: s, chats={}))
        again.ws.places = _checker(tmp_path, again)
        again._report_late_all()                        # the start's pump
        run = again.read(r)["run"]
        assert run["state"] == "blocked" and run["hold"] == agentry.HELD_STOPPED
        assert run["late_hold"]["words"] == agentry._hold_reason([], [], [], [(demo, "demo", ["hooks/pre-push"])])
        seqs.append(run["seq"])
    assert seqs[0] == seqs[1]


def test_a_task_attempt_s_end_carries_what_its_look_found_and_the_pump_after_reports_none_again(tmp_path,
                                                                                               monkeypatch):
    # a task's attempt's end (task_runs._places_at_end) looks through agents._places_seen, as an
    # agent's run's end does, and writes its own record carrying the words: the next report marks what that record
    # carries, and puts nothing more on the run
    from proofpane_workspace import agents as agentry
    agents, r, tops = _placed(tmp_path, monkeypatch)
    agents.working.add(r)
    queued = agents.queue(AGENT)["run_id"]              # the agent's next run, queued behind it, waits for nothing
    demo = _hook(tops["demo"], "pre-push")
    words = agentry._hold_reason([], [], [], agents._places_seen(AGENT))
    _holds_settled()
    agents._report_late(AGENT)                          # before its record: nothing marked, nothing on the run
    assert agents.ws.places.unreported(AGENT) == [(demo, "demo", ["hooks/pre-push"])]
    agents._change(r, lambda rec: rec["run"].update(state="done", question=None, ended_at=int(time.time()), hold=words))
    agents.working.discard(r)
    agents._report_late_all()
    assert agents.ws.places.unreported(AGENT) == []
    assert "late_hold" not in agents.read(r)["run"] and "late_hold" not in agents.read(queued)["run"]


def test_a_run_that_does_not_start_marks_what_its_look_found_once_its_record_is_written(tmp_path, monkeypatch):
    # through agents._start in this process: a run whose precheck says no ends unstarted by
    # an end's path, its look first. The owner's stop lands just after that look, and a report then marks nothing,
    # the run being still started and its record not yet the one that carries the hold; once that record is written
    # the hold is marked at once, with no pump (none runs in this process)
    import asyncio
    from types import SimpleNamespace
    agents, r, tops = _placed(tmp_path, monkeypatch)
    agents._change(r, lambda rec: rec["run"].update(state="done", question=None, ended_at=int(time.time())))
    agents.put({"id": AGENT, "name": "Triage", "brief": "hello", "repo": "demo", "precheck": "exit 3"})
    q = agents.queue(AGENT)["run_id"]
    agents._prepare = lambda run_id, agent: ("demo", "0" * 40)
    agents._baseline = lambda run_id, agent: None
    agents._precheck = lambda run_id, agent: (3, "no")
    agents._seen = lambda run_id: ([], [])
    agents.ws.accounts = SimpleNamespace(for_session=lambda kind, account: None)
    agents.ws._bind = lambda run_id, on: None
    demo = _hook(tops["demo"], "pre-push")
    held = [(demo, "demo", ["hooks/pre-push"])]
    seen = agents._places_seen

    def looked(agent_id, run_id=None):
        assert run_id == q                              # noted with the run whose record is to carry it
        found = seen(agent_id, run_id)
        _holds_settled()
        agents._change(q, lambda rec: rec["run"].update(state="stopped", ended_at=int(time.time())))
        agents._report_late(AGENT)                      # the run is still being started
        assert agents.ws.places.unreported(AGENT) == held and "late_hold" not in agents.read(q)["run"]
        return found
    agents._places_seen = looked
    agents._starting.add(q)                             # as agents._pump marks a run it starts
    asyncio.run(agents._start(q))
    run = agents.read(q)["run"]
    assert run["state"] == "blocked" and run["hold"].startswith("The git settings of the repository demo")
    assert agents.ws.places.unreported(AGENT) == [] and "late_hold" not in run


def test_a_run_s_end_that_cannot_list_or_look_holds_its_agent_saying_so(tmp_path, monkeypatch):
    # the end's look fails closed. The folders git runs in that cannot be listed (the
    # sessions' folder a link to nowhere, as a run can leave it) hold the run's agent with words saying so, never as
    # though nothing changed; so does a look that fails. A workspace with no record of git's places finds nothing.
    import asyncio
    import types
    from proofpane_workspace import agents as agentry
    agents, r, tops = _placed(tmp_path, monkeypatch)
    space = agents.ws
    space.repos_dir, space.sessions_dir, space.agents = str(tmp_path / "repos"), str(tmp_path / "gone"), agents
    os.symlink(str(tmp_path / "nowhere"), space.sessions_dir)
    space._git_tops = types.MethodType(ws_module.Workspace._git_tops, space)
    agents.ending.add(r)
    assert agents._places_seen(AGENT) == [(agentry.UNSEEN, "listed", ["FileNotFoundError"])]
    real = space.places

    class Failing:
        def look(self, agent_id, tops=()):
            raise OSError("no space left on the device")
    space.places = Failing()
    found = agents._places_seen(AGENT)
    assert found == [(agentry.UNSEEN, "listed", ["FileNotFoundError"]), (agentry.UNSEEN, "looked", ["OSError"])]
    assert agentry._hold_reason([], [], [], found) == (
        "The folders the workspace runs git in could not be listed at the run's end (FileNotFoundError), so a "
        "repository the run made in them may not have been looked at. Git's places could not be looked at at the "
        "run's end (OSError), so what the run changed in them may not have been found. Look at what changed, then "
        "resume the agent.")
    del space.places
    assert agents._places_seen(AGENT) == []
    space.places = real
    agents.ending.discard(r)
    _ending(agents)
    asyncio.run(agents.end(r, "done", None))
    ended = agents.read(r)["run"]
    assert ended["state"] == "blocked" and ended["cause"] == "watch"
    assert ended["hold"].startswith("The folders the workspace runs git in could not be listed")
    assert agents.agent(AGENT)["paused"] is True


# ---- a write that was to carry a look, a task's attempt's end, two runs in one look ---------------------------------

def _failing(agents, name, run_id):
    """agents._change with the change named `name` (a write's own function: `left`, `ended`) raising once for
    `run_id`, as a write of the run's record that fails (a full disk) would: [run_id] once it has."""
    real = agents._change
    failed = []

    def change(rid, fn):
        if rid == run_id and getattr(fn, "__name__", "") == name and not failed:
            failed.append(rid)
            raise OSError("no space left on the device")
        return real(rid, fn)
    agents._change = change
    return failed


def _ended_first(agents, run_id, **more):
    """The run ended, and queued before every other run of its agent: queue times are whole seconds, and runs queued in
    the same one are taken in the order of their ids, so the run a late hold goes on (the agent's latest) would
    otherwise be either."""
    agents._change(run_id, lambda rec: rec["run"].update(state="done", question=None, ended_at=int(time.time()),
                                                         queued_at=1, **more))


def _unstarted(agents):
    """agents._start's own steps stood in for, for a run whose precheck says no: its worktree, its watch's baseline,
    the precheck, the watched places and the account it would run on."""
    from types import SimpleNamespace
    agents.put({"id": AGENT, "name": "Triage", "brief": "hello", "repo": "demo", "precheck": "exit 3"})
    agents._prepare = lambda run_id, agent: ("demo", "0" * 40)
    agents._baseline = lambda run_id, agent: None
    agents._precheck = lambda run_id, agent: (3, "no")
    agents._seen = lambda run_id: ([], [])
    agents.ws.accounts = SimpleNamespace(for_session=lambda kind, account: None)
    agents.ws._bind = lambda run_id, on: None


@pytest.mark.parametrize("stopped", [True, False], ids=["after the owner's stop", "with no stop"])
def test_a_start_whose_record_cannot_be_written_leaves_what_its_look_found_to_a_late_hold(tmp_path, monkeypatch,
                                                                                          stopped):
    # through agents._started in this process: a run whose precheck says no ends
    # unstarted, its look first; the write of its record that was to carry what the look found raises. The run is
    # ended by the owner's stop before that write, or by its start's own failure after it, and neither record holds
    # the agent: what the look found is not counted as reported, and the pump's report writes it on the run as a late
    # hold, which the app takes to pause the agent
    import asyncio
    from proofpane_workspace import agents as agentry
    agents, r, tops = _placed(tmp_path, monkeypatch)
    _ended_first(agents, r)
    _unstarted(agents)
    q = agents.queue(AGENT)["run_id"]
    demo = _hook(tops["demo"], "pre-push")
    held = [(demo, "demo", ["hooks/pre-push"])]
    seen = agents._places_seen

    def looked(agent_id, run_id=None):
        found = seen(agent_id, run_id)
        _holds_settled()
        if stopped:
            agents._if_queued(q, state="stopped", reason="stopped by you")      # as agents.stop does
        return found
    agents._places_seen = looked
    failed = _failing(agents, "left", q)
    agents._starting.add(q)                             # as agents._pump marks a run it starts
    asyncio.run(agents._started(q))
    run = agents.read(q)["run"]
    assert failed == [q] and q not in agents._starting
    assert run["state"] == ("stopped" if stopped else "failed") and not run.get("hold") and "late_hold" not in run
    assert agents.ws.places.unreported(AGENT) == held  # no record carries it: not counted as reported
    _pumped(agents)
    run = agents.read(q)["run"]
    words = agentry._hold_reason([], [], [], held)
    assert run["late_hold"] == {"seq": run["seq"], "words": words} and run["hold"] == words
    assert agents.ws.places.unreported(AGENT) == [] and agents.agent(AGENT)["paused"] is True
    assert AGENT not in agents._carrying               # dropped, not kept to be counted by a record never written


def test_an_end_whose_record_cannot_be_written_counts_nothing_and_its_next_end_carries_it(tmp_path, monkeypatch):
    # at agents.end: the write of the end's record raises, so the run is still working
    # on its record; nothing its look found is counted as reported, and no report writes it while the run works. Its
    # next end looks again, and its record carries it, counted as reported then, with no late hold
    import asyncio
    agents, r, tops = _placed(tmp_path, monkeypatch)
    _ending(agents)
    demo = _hook(tops["demo"], "pre-push")
    held = [(demo, "demo", ["hooks/pre-push"])]
    failed = _failing(agents, "ended", r)
    with pytest.raises(OSError):
        asyncio.run(agents.end(r, "done", None))
    _holds_settled()
    assert failed == [r] and agents.read(r)["run"]["state"] == "working" and r not in agents.ending
    assert agents.ws.places.unreported(AGENT) == held
    agents._report_late_all()                           # the pump's report: the run works, so not now
    assert "late_hold" not in agents.read(r)["run"] and agents.ws.places.unreported(AGENT) == held
    asyncio.run(agents.end(r, "done", None))
    ended = agents.read(r)["run"]
    assert ended["state"] == "blocked" and "The git settings of the repository demo" in ended["hold"]
    assert agents.ws.places.unreported(AGENT) == [] and "late_hold" not in ended
    agents._report_late_all()
    assert agents.read(r)["run"]["seq"] == ended["seq"]


class _Attempt:
    """A task's attempt as task_runs.TaskRuns._run reads one (task_attempt.Attempt): its run's view at its end, and its
    chat's numbers; nothing its own watch looks at changed."""

    def __init__(self):
        from types import SimpleNamespace
        self.chat = SimpleNamespace(cost=0.0, last_text=None)
        self.end_look = None

    async def run(self):
        return {"state": "done", "cause": None, "reason": None, "task": {}, "backend": None, "rate_limits": None,
                "question": None}


def test_a_task_attempt_s_end_as_task_runs_makes_it_is_reported_once_by_its_own_record(tmp_path, monkeypatch):
    # the attempt's end as task_runs.TaskRuns._run makes it, with a stand-in attempt.
    # Its look (_places_at_end, through agents._places_seen, which names no run) notes the attempt's run, working; its
    # own record carries the hold; the pump after counts it as reported and puts nothing more on the run
    import asyncio
    agents, r, tops = _placed(tmp_path, monkeypatch)
    agents.working.add(r)
    demo = _hook(tops["demo"], "pre-push")
    asyncio.run(agents.task_runs._run(r, _Attempt(), AGENT, "t1"))
    _holds_settled()
    ended = agents.read(r)["run"]
    assert ended["state"] == "done" and "The git settings of the repository demo" in ended["hold"]
    assert agents.ws.places.unreported(AGENT) == [(demo, "demo", ["hooks/pre-push"])]     # counted by the report
    _pumped(agents)
    assert agents.ws.places.unreported(AGENT) == [] and "late_hold" not in agents.read(r)["run"]
    _pumped(agents)
    assert agents.read(r)["run"]["seq"] == ended["seq"]


@pytest.mark.parametrize("how", ["written", "fails after the owner's stop", "fails"])
def test_a_task_attempt_that_ends_unstarted_is_reported_once_whether_its_record_is_written_or_not(tmp_path,
                                                                                                   monkeypatch, how):
    # an attempt that ends before its claude starts, once its own code could
    # run (its precheck said no), as task_runs.TaskRuns.start ends it: its look (_places_at_end), then its record
    # (_end_unstarted). Written, that record carries the hold, and the pump after counts it as reported. When the
    # write raises, the run is ended by the owner's stop before it or by its start's failure after it (agents.
    # _started), neither record holds the agent, and the pump writes the hold on the run as a late hold
    import asyncio
    from proofpane_workspace import agents as agentry
    agents, r, tops = _placed(tmp_path, monkeypatch)
    _ended_first(agents, r)
    q = agents.queue(AGENT)["run_id"]
    agents._starting.add(q)                             # as agents._pump marks it, and task_runs' start once its own
    agents._acting.add(q)                               # code may run
    demo = _hook(tops["demo"], "pre-push")
    held = [(demo, "demo", ["hooks/pre-push"])]
    words = agentry._hold_reason([], [], [], held)
    places = asyncio.run(agents.task_runs._places_at_end(q, AGENT))
    _holds_settled()
    assert places == words
    if how.endswith("stop"):
        agents._if_queued(q, state="stopped", reason="stopped by you")
    failed = [] if how == "written" else _failing(agents, "left", q)
    try:
        agents.task_runs._end_unstarted(q, AGENT, places, state="skipped", cause=None, reason="its precheck exited 3")
    except OSError as e:                                # as agents._started ends a start that raised
        agents._if_queued(q, state="failed", reason=str(e), cause="start")
    agents._starting.discard(q)
    agents._acting.discard(q)
    run = agents.read(q)["run"]
    if how == "written":
        assert run["state"] == "blocked" and run["cause"] == "watch" and run["hold"] == words
    else:
        assert failed == [q] and run["state"] == ("stopped" if how.endswith("stop") else "failed")
        assert not run.get("hold")
    assert agents.ws.places.unreported(AGENT) == held  # no report yet
    _pumped(agents)
    run = agents.read(q)["run"]
    assert agents.ws.places.unreported(AGENT) == [] and AGENT not in agents._carrying
    if how == "written":
        assert "late_hold" not in run and run["hold"] == words
    else:
        assert run["late_hold"] == {"seq": run["seq"], "words": words} and run["hold"] == words


@pytest.mark.parametrize("second", ["its record holds it", "its record holds nothing"])
def test_what_a_look_notes_with_two_runs_is_counted_only_once_each_record_carries_it(tmp_path, monkeypatch, second):
    # a look whose caller names no run (a task's attempt's end) notes every run of
    # the agent working, starting or being ended, of which the pump has one at a time per agent. Were there two, what
    # it found is counted as reported only once each of their records is written holding the agent, and the one's
    # own end does not count it for the other's; a record that holds nothing leaves it to a late hold. Here the
    # second run is marked as being started by hand
    from proofpane_workspace import agents as agentry
    agents, r, tops = _placed(tmp_path, monkeypatch)
    agents.working.add(r)
    q = agents.queue(AGENT)["run_id"]
    agents._starting.add(q)
    demo = _hook(tops["demo"], "pre-push")
    held = [(demo, "demo", ["hooks/pre-push"])]
    words = agentry._hold_reason([], [], [], held)
    assert agents._places_seen(AGENT) == held
    _holds_settled()
    assert agents._carrying[AGENT] == [({r, q}, [demo])]
    _ended_first(agents, r, hold=words)
    agents.working.discard(r)
    agents._places_carried(r)                           # its own end's count: not what was noted with q too
    agents._report_late_all()                           # q still being started: not yet
    assert agents.ws.places.unreported(AGENT) == held and "late_hold" not in agents.read(r)["run"]
    holds = second == "its record holds it"
    agents._change(q, lambda rec: rec["run"].update(state="blocked" if holds else "stopped", ended_at=int(time.time()),
                                                    **({"cause": "watch", "hold": words} if holds else {})))
    agents._starting.discard(q)
    agents._report_late_all()
    assert agents.ws.places.unreported(AGENT) == []
    late = agents.read(q)["run"].get("late_hold")
    assert (late is None) is holds, late


def test_a_run_s_end_counts_its_own_look_whatever_other_run_of_its_agent_is_being_started(tmp_path, monkeypatch):
    # an end's look names its own run (agents.end gives _places_seen its run id), so
    # what it found is counted as reported once that run's record is written, not held up by another run of the agent
    # (here one marked as being started by hand, which the pump does not do while a run of the agent is live)
    import asyncio
    agents, r, tops = _placed(tmp_path, monkeypatch)
    _ending(agents)
    q = agents.queue(AGENT)["run_id"]
    agents._starting.add(q)
    _hook(tops["demo"], "pre-push")
    asyncio.run(agents.end(r, "done", None))
    _holds_settled()
    assert agents.ws.places.unreported(AGENT) == [] and AGENT not in agents._carrying


def test_what_a_look_found_for_a_run_whose_record_cannot_be_read_stays_noted(tmp_path, monkeypatch):
    # whether a record carries what a look found is read from that record; one that
    # cannot be read counts nothing and drops nothing, and the next report reads it again
    from proofpane_workspace import agents as agentry
    agents, r, tops = _placed(tmp_path, monkeypatch)
    agents.working.add(r)
    demo = _hook(tops["demo"], "pre-push")
    held = [(demo, "demo", ["hooks/pre-push"])]
    assert agents._places_seen(AGENT) == held
    _holds_settled()
    _ended_first(agents, r, hold=agentry._hold_reason([], [], [], held))
    agents.working.discard(r)

    def unreadable(run_id):
        raise OSError("the record's disk is gone")
    agents.read = unreadable
    agents._report_late_all()
    assert agents.ws.places.unreported(AGENT) == held and agents._carrying[AGENT] == [({r}, [demo])]
    del agents.read
    agents._report_late_all()
    assert agents.ws.places.unreported(AGENT) == [] and AGENT not in agents._carrying
    assert "late_hold" not in agents.read(r)["run"]


def test_a_look_whose_runs_cannot_be_read_notes_nothing_and_ends_all_the_same(tmp_path, monkeypatch):
    # the runs that what a look found is noted with are read after the look, in a
    # thread, while the event loop changes them. A failure there notes nothing and never fails the end; the hold is
    # reported once more after it, the safe way
    from proofpane_workspace import agents as agentry
    agents, r, tops = _placed(tmp_path, monkeypatch)
    agents.working.add(r)
    demo = _hook(tops["demo"], "pre-push")
    held = [(demo, "demo", ["hooks/pre-push"])]
    real, here = agents._picked, threading.current_thread()

    def unreadable(agent_id=None, states=None):
        if threading.current_thread() is here:          # the look's own read, not the hold's thread
            raise RuntimeError("dictionary changed size during iteration")
        return real(agent_id, states)
    agents._picked = unreadable
    assert agents._places_seen(AGENT) == held and agents._carrying == {}
    _holds_settled()
    del agents._picked
    words = agentry._hold_reason([], [], [], held)
    _ended_first(agents, r, hold=words)
    agents.working.discard(r)
    agents._report_late_all()
    run = agents.read(r)["run"]
    assert run["late_hold"] == {"seq": run["seq"], "words": words} and agents.ws.places.unreported(AGENT) == []


def test_a_run_s_end_counts_what_its_look_found_off_the_event_loop(tmp_path, monkeypatch):
    # a report in progress holds _late_lock across a run's record's write and a save
    # of the record of git's places. The end's count of what its own look found waits for it in a thread of its own,
    # and the event loop, every start, end and answer with it, goes on meanwhile; the count is made once it is free
    import asyncio
    agents, r, tops = _placed(tmp_path, monkeypatch)
    _ending(agents)
    _hook(tops["demo"], "pre-push")
    taken, free = threading.Event(), threading.Event()

    def report():
        with agents._late_lock:
            taken.set()
            free.wait(10)

    def settled(run_id, record, c, commit=True):        # the report begins after the end's look
        threading.Thread(target=report, daemon=True).start()
        assert taken.wait(10)
        threading.Timer(0.5, free.set).start()
        return None, {}, []
    agents._settle = settled
    ticks = []

    async def main():
        ending = asyncio.ensure_future(agents.end(r, "done", None))
        while not ending.done():
            ticks.append(time.monotonic())
            await asyncio.sleep(0.01)
        await ending
    asyncio.run(main())
    _holds_settled()
    assert free.is_set() and ticks[-1] - ticks[0] >= 0.4
    assert max(b - a for a, b in zip(ticks, ticks[1:])) < 0.3
    assert agents.ws.places.unreported(AGENT) == []


# ---- the workspace's git itself, in this process ------------------------------------------------------------------

def test_a_read_fetches_no_missing_object_and_the_owner_s_own_action_does(tmp_path):
    src = tmp_path / "src"
    make_repo(str(src))
    git("config", "uploadpack.allowfilter", "true", cwd=str(src))
    git("config", "uploadpack.allowanysha1inwant", "true", cwd=str(src))
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--filter=blob:none", "--no-checkout", f"file://{src}", str(clone)],
                   check=True, capture_output=True)
    quiet = dict(os.environ, GIT_NO_LAZY_FETCH="1")
    blob = subprocess.run(["git", "rev-parse", "HEAD:README.md"], cwd=clone, env=quiet, check=True,
                          capture_output=True, text=True).stdout.strip()

    def there():
        return subprocess.run(["git", "cat-file", "-e", blob], cwd=clone, env=quiet).returncode == 0
    assert not there()
    with pytest.raises(WorkspaceError):
        ws_module.git("cat-file", "-p", blob, cwd=str(clone))
    assert not there()                                  # a read: nothing fetched from the promisor
    assert ws_module.git("cat-file", "-p", blob, cwd=str(clone), own=True)[1] == "demo\n"
    assert there()                                      # the owner's own action, as his own git


def test_a_caller_s_settings_are_added_after_the_workspace_s_never_in_their_place(tmp_path):
    env = ws_module._with(ws_module._git_env(str(tmp_path)), {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "x.y",
                                                             "GIT_CONFIG_VALUE_0": "z", "GIT_INDEX_FILE": "/i"})
    pairs = ws_module._config_pairs(env)
    assert pairs == [("core.hooksPath", "/dev/null"), ("core.fsmonitor", "false"), ("commit.gpgSign", "false"),
                     ("push.gpgSign", "false"), ("log.showSignature", "false"), ("protocol.allow", "never"),
                     ("x.y", "z")]
    assert env["GIT_INDEX_FILE"] == "/i" and env["GIT_NO_LAZY_FETCH"] == "1"
    fetching = ws_module._git_env(str(tmp_path), network=True)
    assert ("protocol.allow", "never") not in ws_module._config_pairs(fetching)
    own = ws_module._git_env(str(tmp_path), own=True)
    assert ws_module._config_pairs(own) == [] and "GIT_NO_LAZY_FETCH" not in own


def test_the_workspace_s_reflog_checks_no_signature_whatever_log_show_signature_says(tmp_path):
    """A git of the workspace's own reads, here the reflog a removed
    session's branch is read by (_drop_empty_branch), with no RUN_GIT given, checks no commit's signature where the
    repository's settings set log.showSignature, so gpg.program never runs and the branch, at a signed commit, goes.
    With those settings alone git runs it, and its words land among the reflog's lines."""
    repo = str(tmp_path / "repo")
    first = make_repo(repo)
    ran, program = tmp_path / "ran", tmp_path / "gpg.sh"
    program.write_text(f"#!/bin/sh\necho ran >> {ran}\necho 'gpg: a signature' >&2\ncat > /dev/null\nexit 1\n")
    program.chmod(0o755)
    tree = git("rev-parse", f"{first}^{{tree}}", cwd=repo)
    raw = (f"tree {tree}\nparent {first}\nauthor A <a@example.com> 1700000000 +0000\n"
           f"committer A <a@example.com> 1700000000 +0000\ngpgsig -----BEGIN PGP SIGNATURE-----\n \n"
           f" iQEzBAABCAAdFiEE\n -----END PGP SIGNATURE-----\n\nsigned\n")
    signed = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--stdin"], cwd=repo, input=raw.encode(),
                            capture_output=True, check=True).stdout.decode().strip()
    git("update-ref", "refs/heads/main", signed, cwd=repo)          # the session's base, reachable from main
    session_id = "0123456789ab"
    git("branch", f"pp/{session_id}", signed, cwd=repo)
    git("config", "log.showSignature", "true", cwd=repo)
    git("config", "gpg.program", str(program), cwd=repo)
    record = {"branch": f"pp/{session_id}", "base": signed}
    assert ws_module.Workspace._drop_empty_branch(repo, record, session_id) is True
    assert not ran.exists() and git("branch", "--list", f"pp/{session_id}", cwd=repo) == ""
    # the control: the same reflog, as git runs it with the repository's settings alone
    alone = {k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG")}
    control = subprocess.run(["git", "reflog", "show", "--format=%H %gs", "refs/heads/main"], cwd=repo,
                             capture_output=True, text=True,
                             env=dict(alone, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1"))
    assert ran.exists() and "gpg: a signature" in control.stdout, control.stdout


def test_github_s_sign_in_setting_git_s_credential_helper_to_gh_is_known(tmp_path, monkeypatch):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    gh = bin_ / "gh"
    gh.write_text("#!/bin/sh\n")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    known = ws_module.Workspace._known_git_change
    helper, gist = "config credential.https://github.com.helper", "config credential.https://gist.github.com.helper"
    as_gh = json.dumps(["", f"!{gh} auth git-credential"])
    assert known(None, gitplaces.USER, [helper, gist], {helper: as_gh, gist: as_gh})
    assert known(None, gitplaces.USER, [helper], {})                       # taken away
    assert not known(None, gitplaces.USER, [helper], {helper: json.dumps(["!/tmp/evil auth git-credential"])})
    assert not known(None, gitplaces.USER, [helper, "config core.fsmonitor"],
                     {helper: as_gh, "config core.fsmonitor": json.dumps(["/tmp/x"])})
    assert not known(None, gitplaces.USER, ["config credential.helper"], {"config credential.helper": as_gh})
    assert not known(None, "/a/repository/.git", [helper], {helper: as_gh})   # a repository's own settings: not this


def test_gh_runs_where_git_could_and_runs_git_as_the_workspace_s_reads_do(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / "ws"
    top = str(root / "repos" / "demo")
    make_repo(top)
    common = gitplaces.git_folder(top)[1]
    checker = gitplaces.Checker(None, b"k" * 32, suspects=lambda since: ["a1"], hold=lambda *a: None,
                                folders=[str(root / "repos")])
    checker.first_look([top])
    monkeypatch.setitem(ws_module._PLACES, str(root), checker)
    env = ws_module._gh_env(top)
    assert ("core.hooksPath", "/dev/null") in ws_module._config_pairs(env) and env["GIT_NO_LAZY_FETCH"] == "1"
    hook = os.path.join(common, "hooks", "pre-push")
    with open(hook, "w") as fh:
        fh.write("#!/bin/sh\n")
    os.chmod(hook, 0o755)
    with pytest.raises(ws_module.GitHeld):
        ws_module._gh_env(top)


def test_the_owner_s_own_action_runs_no_hook_from_a_hooks_folder_named_by_a_relative_path(tmp_path, monkeypatch):
    """A relative hooks folder (husky's) is in the worktree, among the files a commit brings, which a run can choose by
    moving a branch: the owner's own action runs no hook from it, and runs his hooks where they are named absolutely."""
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / "ws"
    top = str(root / "repos" / "demo")
    make_repo(top)
    common = gitplaces.git_folder(top)[1]
    checker = gitplaces.Checker(None, b"k" * 32, suspects=lambda since: [], hold=lambda *a: None,
                                folders=[str(root / "repos")])
    checker.first_look([top])
    monkeypatch.setitem(ws_module._PLACES, str(root), checker)
    git("config", "core.hooksPath", ".husky/_", cwd=top)
    assert ("core.hooksPath", "/dev/null") in ws_module._config_pairs(ws_module._git_env(top, own=True))
    git("config", "core.hooksPath", str(tmp_path / "hooks"), cwd=top)
    assert ws_module._config_pairs(ws_module._git_env(top, own=True)) == []


def test_finish_runs_no_hook_a_commit_brought_into_a_relative_hooks_folder(ws):
    other = os.path.join(ws.root, "repos", "other")
    marker = ws.tmp / "hook-from-a-commit"
    os.makedirs(os.path.join(other, ".husky", "_"))
    hook = os.path.join(other, ".husky", "_", "pre-commit")
    with open(hook, "w") as fh:
        fh.write(f"#!/bin/sh\ntouch {marker}\n")
    os.chmod(hook, 0o755)
    git("add", "-A", cwd=other)
    git("commit", "-q", "-m", "a hook that came with a commit", cwd=other)
    git("config", "core.hooksPath", ".husky/_", cwd=other)
    sid, work = session(ws, "other")
    changed_in(work)
    done = ws.http.post(f"/sessions/{sid}/finish", json=WHO)
    assert done.status_code == 200, done.text
    assert not marker.exists()


def test_a_run_working_when_the_machine_stopped_is_a_suspect_at_the_next_start(ws):
    """A run planting while it works, the machine stopped before its end, and the record taken away: at the next start
    nothing is accepted for being there, and the first look at what it planted holds it for that agent."""
    other = os.path.join(ws.root, "repos", "other", ".git")
    marker = ws.tmp / "planted"
    plant = script(ws, "plant.sh", f"printf '#!/bin/sh\\ntouch {marker}\\n' > {other}/hooks/post-checkout\n"
                                   f"chmod +x {other}/hooks/post-checkout\ntouch {ws.tmp}/planted-done\n")
    put(ws, f"[{plant}] wait 60")
    r = run(ws)["run_id"]
    until(ws, r, ("working",))
    deadline = time.time() + 30
    while not (ws.tmp / "planted-done").exists():
        assert time.time() < deadline
        time.sleep(0.05)
    ws.stop()
    os.unlink(os.path.join(ws.root, gitplaces.RECORD))
    ws.start()
    until(ws, r, ("blocked",))
    made = ws.http.post("/sessions", json={"repo": "other", "program": "shell", "task": "x"})
    assert made.status_code == 400 and "no record of them from before" in made.json()["detail"]
    assert not marker.exists()
    resume(ws)
    assert ws.http.post("/sessions", json={"repo": "other", "program": "shell", "task": "x"}).status_code == 200


def test_land_s_folder_outside_the_workspace_is_checked_by_the_record_that_links_to_it(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / "ws"
    (root / "repos").mkdir(parents=True)
    mine = str(tmp_path / "Code" / "parser")
    make_repo(mine)
    os.symlink(mine, str(root / "repos" / "parser"))
    checker = gitplaces.Checker(None, b"k" * 32, suspects=lambda since: ["a1"], hold=lambda *a: None,
                                folders=[str(root / "repos")])
    checker.first_look([str(root / "repos" / "parser")])
    monkeypatch.setitem(ws_module._PLACES, str(root), checker)
    assert ws_module._places_for(mine) is checker
    hook = os.path.join(mine, ".git", "hooks", "post-merge")
    with open(hook, "w") as fh:
        fh.write("#!/bin/sh\n")
    os.chmod(hook, 0o755)
    with pytest.raises(ws_module.GitHeld):
        ws_module._git_env(mine, own=True)


def test_a_run_working_when_the_machine_stopped_is_a_suspect_however_long_the_start_took(tmp_path):
    """Its record's end time is the second the start's scan noted it, which can be well before the record of git's
    places starts: it is a suspect for every place not looked at since that start all the same."""
    from test_agents_runs import _in_process
    agents, r = _in_process(tmp_path)
    now = time.time()
    agents._change(r, lambda rec: rec["run"].update(state="blocked", cause="machine", started_at=int(now) - 60,
                                                    ended_at=int(now) - 5))
    agents.stopped_at_start.append(r)
    assert agents.suspects(now) == []                  # by its record's second alone, not a suspect
    agents.stopped_since(now)
    assert agents.suspects(now) == [AGENT]
    assert agents.suspects(now + 1) == []              # a place looked at after the start: not for it


@pytest.mark.parametrize("error", [RuntimeError, ValueError])
@pytest.mark.parametrize("raising", ["processes.end", "_ended_now", "_look_at_folders", "_places_seen"])
def test_a_task_attempt_s_end_whose_look_raises_holds_its_agent_saying_so(tmp_path, monkeypatch, raising, error):
    # a task's attempt's end fails closed on git's places: where its end of what it started, the record of that end, or
    # the look itself raises, the attempt is held with the words an agent's run's end gives a look it could not make
    # (agents._places_seen), never ended as though nothing had changed. A ValueError too, as the workspace's own
    # AgentError is one: an end that caught OSError and RuntimeError alone passed every test in this file with
    # RuntimeError alone; with a ValueError its end raised, and _run neither wrote the run's end nor held its agent
    import asyncio
    from proofpane_workspace import agents as agentry
    from proofpane_workspace import task_runs as task_runs_module
    agents, r, tops = _placed(tmp_path, monkeypatch)
    agents.working.add(r)

    def boom(*args, **kwargs):
        raise error("cannot")
    if raising == "processes.end":
        monkeypatch.setattr(task_runs_module.processes, "end", boom)
    elif raising == "_look_at_folders":
        monkeypatch.setattr(agents.task_runs, "_look_at_folders", boom)
    else:
        monkeypatch.setattr(agents, raising, boom)
    words = agentry._hold_reason([], [], [], [(agentry.UNSEEN, "looked", [error.__name__])])
    assert words.startswith(f"Git's places could not be looked at at the run's end ({error.__name__})")
    assert asyncio.run(agents.task_runs._places_at_end(r, AGENT)) == words
    assert agents.agent(AGENT)["paused"] is False
    asyncio.run(agents.task_runs._run(r, _Attempt(), AGENT, "t1"))
    ended = agents.read(r)["run"]
    assert ended["hold"] == words
    assert agents.agent(AGENT)["paused"] is True            # the agent held here, until the owner resumes it
