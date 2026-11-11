"""The improve loop's runs in the workspace (improve.py): an agent's skills written into its worktree in folders that
ignore themselves; a review run that reads its digest and proposes at most two changes, each held to its shape; a
sandbox run that replays an ended run from its base with its arm's brief, skills, settings and memory, never the
agent's; the critic and the pairwise judge; through a real workspace agent with the stand-in claude."""

import json
import os
import subprocess
import time

import pytest

from proofpane_workspace import gate
from test_agents_runs import AGENT, Workspace, log, put, run, settings_of, starts, until

SKILL = "---\nname: labels\ndescription: Label the new issues by area.\nwhen_to_use: new issues\n---\nLabel each one.\n"
REVIEW = "Review this agent's recent runs, and propose what would make them pass more often."
PROPOSAL = "e1e2e3e4e5e6"


@pytest.fixture
def ws(tmp_path):
    w = Workspace(tmp_path)
    yield w
    w.stop()


def agent_dir(ws):
    return os.path.join(ws.root, "agents", AGENT)


def queue(ws, trigger):
    r = ws.http.post(f"/agents/{AGENT}/runs", json={"trigger": trigger})
    assert r.status_code == 200, r.text
    return r.json()


def words(ws, run_id):
    return [e["text"] for e in log(ws, run_id)["events"] if e["kind"] == "text"][-1]


def gone(path, timeout=10):
    """Whether `path` is gone within `timeout` seconds (a loop run's worktree goes just after its end is written)."""
    deadline = time.time() + timeout
    while os.path.exists(path):
        if time.time() > deadline:
            return False
        time.sleep(0.05)
    return True


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True).stdout


def test_a_skill_with_more_than_its_name_description_and_when_is_refused_by_the_workspace(ws):
    for bad in ("---\ndescription: d\nallowed-tools: Bash(*)\n---\n", "---\ndescription: d\nhooks:\n  Stop: []\n---\n"):
        r = ws.http.put(f"/agents/{AGENT}", json={"id": AGENT, "name": "T", "brief": "x", "repo": "demo",
                                                  "skills": {"labels": bad}})
        assert r.status_code == 400 and "name, description and when_to_use only" in r.json()["detail"], r.text


def test_an_agent_s_skills_reach_its_runs_in_folders_that_ignore_themselves(ws):
    put(ws, "work Label them.", skills={"labels": SKILL})
    first = until(ws, run(ws)["run_id"], ("done",))
    assert words(ws, first["run_id"]) == "Worked: Label them. (skills: pp-labels)"
    work = os.path.join(agent_dir(ws), "work")
    folder = os.path.join(work, ".claude", "skills", "pp-labels")
    assert open(os.path.join(folder, "SKILL.md")).read() == SKILL
    assert git("status", "--porcelain", "--untracked-files=all", cwd=work) == ""        # never committed
    assert ".claude" not in git("ls-files", cwd=work)
    os.makedirs(os.path.join(work, ".claude", "skills", "pp-mine"))                     # a folder it did not write
    with open(os.path.join(work, ".claude", "skills", "pp-mine", "SKILL.md"), "w") as fh:
        fh.write("---\ndescription: the owner's\n---\n")
    put(ws, "work Label them.", skills={"mine": SKILL})                                    # not even of its name
    until(ws, run(ws)["run_id"], ("done",))
    assert open(os.path.join(work, ".claude", "skills", "pp-mine", "SKILL.md")).read() == "---\ndescription: the owner's\n---\n"
    put(ws, "work Label them.", skills={})
    until(ws, run(ws)["run_id"], ("done",))
    assert not os.path.exists(folder) and os.path.exists(os.path.join(work, ".claude", "skills", "pp-mine"))
    elsewhere = os.path.join(ws.root, "elsewhere")                         # a skills folder that is a link: nothing
    os.makedirs(elsewhere)                                                 # is written through it
    import shutil
    shutil.rmtree(os.path.join(work, ".claude", "skills"))
    os.symlink(elsewhere, os.path.join(work, ".claude", "skills"))
    put(ws, "work Label them.", skills={"labels": SKILL})
    until(ws, run(ws)["run_id"], ("done", "failed", "blocked"))
    assert os.listdir(elsewhere) == []


def test_a_review_run_reads_its_digest_and_proposes_through_its_own_tool_alone(ws):
    put(ws, "work Label them. BAD", mcp={"servers": [{"name": "notes", "kind": "address", "transport": "http",
                                                      "url": "https://notes.example/mcp"}]})
    first = until(ws, run(ws)["run_id"], ("done",))["run_id"]
    assert "notes" in starts(ws)[-1]["mcp"]                                                 # the agent's own run has it
    digest = {"runs": [{"run_id": first}], "current": {"brief": "work Label them. BAD"}}
    review = until(ws, queue(ws, {"kind": "review", "review": {"prompt": REVIEW, "digest": digest}})["run_id"],
                   ("done", "failed"))
    assert review["state"] == "done", review
    assert review["changes"] == [{"target": "brief", "name": None, "value": "work Label them. GOOD",
                                  "hypothesis": "Saying GOOD passes.",
                                  "evidence": {"runs": [first], "metric": "judge task_fit"}}]
    assert words(ws, review["run_id"]).endswith("first run said: Worked: Label them. BAD")     # its digest, read
    start = starts(ws)[-1]
    assert "PP_REPLAY" in start["env"]
    assert start["args"][start["args"].index("--allowedTools") + 1] == "mcp__proofpane__propose_change"
    assert settings_of(start)["autoMemoryDirectory"] == os.path.join(agent_dir(ws), "runs", review["run_id"], "memory")
    assert "--strict-mcp-config" in start["args"] and not start["mcp"]                     # the review has not
    assert "notes" not in json.dumps(start["args"])
    repo = os.path.join(ws.root, "repos", "demo")
    assert gone(os.path.join(agent_dir(ws), "runs", review["run_id"], "work"))                  # its worktree gone
    deadline = time.time() + 10                                      # its branch, gone after it
    while f"pp/run-{review['run_id']}" in git("branch", "--list", cwd=repo):
        assert time.time() < deadline
        time.sleep(0.05)
    assert gate.refusal({"tool_name": "mcp__proofpane__propose_change", "tool_input": {}}, env={"PP_REPLAY": "1"}) is None


def test_a_review_proposes_at_most_two_changes_and_none_a_skill_with_tools(ws):
    put(ws, "work THREE SKILLTOOLS")
    digest = {"runs": [], "current": {"brief": "work THREE SKILLTOOLS"}}
    review = until(ws, queue(ws, {"kind": "review", "review": {"prompt": REVIEW, "digest": digest}})["run_id"],
                   ("done", "failed"))
    assert [(c["target"], c["hypothesis"]) for c in review["changes"]] == [("effort", "try 0"), ("effort", "try 1")]
    said = words(ws, review["run_id"])
    assert "Not proposed: A skill's frontmatter holds name, description and when_to_use only" in said
    assert "Not proposed: a review proposes at most 2 changes." in said
    for bad in ({"kind": "review", "review": {"prompt": "", "digest": {}}},
                {"kind": "review", "review": {"prompt": REVIEW, "digest": {"x": "y" * (1024 * 1024)}}},
                {"kind": "review"}):
        assert ws.http.post(f"/agents/{AGENT}/runs", json={"trigger": bad}).status_code == 400


def sandbox(base, brief, **over):
    return dict({"base": base, "brief": brief, "skills": {}, "chat_settings": {}, "autocompact_window": None,
                 "memory": {}}, **over)


def test_a_sandbox_run_replays_its_input_from_its_base_with_its_arm_s_brief_skills_settings_and_memory(ws):
    put(ws, "work Label them. BAD", limits={"queue_max": 1})
    first = until(ws, run(ws)["run_id"], ("done",))
    base = first["base"]
    with open(os.path.join(ws.root, "repos", "demo", "later.txt"), "w") as fh:
        fh.write("after the run's base")
    git("-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qam", "x", cwd=os.path.join(ws.root, "repos", "demo"))
    control = queue(ws, {"kind": "sandbox", "of": first["run_id"], "arm": "control", "proposal": PROPOSAL,
                         "sandbox": sandbox(base, "work Label them. BAD", memory={"MEMORY.md": "the input's memory"})})
    treatment = queue(ws, {"kind": "sandbox", "of": first["run_id"], "arm": "treatment", "proposal": PROPOSAL,
                           "sandbox": sandbox(base, "work Label them. GOOD", skills={"labels": SKILL},
                                              chat_settings={"effort": "high"}, autocompact_window=200_000)})
    a = until(ws, control["run_id"], ("done", "failed"))
    b = until(ws, treatment["run_id"], ("done", "failed"))       # two queued past the agent's queue of 1: the loop's
    uses = os.path.join(agent_dir(ws), "memory-uses.json")
    assert json.load(open(uses))["runs"] == 1                    # the agent's own run alone counted for its memory
    assert (a["state"], b["state"]) == ("done", "done")
    assert words(ws, a["run_id"]) == "Worked: Label them. BAD"
    assert words(ws, b["run_id"]) == "Worked: Label them. GOOD (skills: pp-labels) (window: 200000)"
    assert (a["base"], b["base"]) == (base, base) and a["replay_of"] is None                 # not of kind "replay"
    assert (b["trigger"]["kind"], b["trigger"]["arm"], b["trigger"]["of"]) == ("sandbox", "treatment", first["run_id"])
    started = {os.path.basename(os.path.dirname(s["cwd"])): s for s in starts(ws)}      # each in runs/<id>/work
    tb = started[b["run_id"]]
    assert "PP_REPLAY" in tb["env"] and tb["args"][tb["args"].index("--effort") + 1] == "high"
    memory_a = settings_of(started[a["run_id"]])["autoMemoryDirectory"]
    assert open(os.path.join(memory_a, "MEMORY.md")).read() == "the input's memory"
    assert not os.path.exists(os.path.join(agent_dir(ws), "memory", "MEMORY.md"))           # the agent's untouched
    assert gone(os.path.join(agent_dir(ws), "runs", a["run_id"], "work"))
    for bad in ({"of": first["run_id"], "arm": "both", "proposal": PROPOSAL, "sandbox": sandbox(base, "x")},
                {"of": first["run_id"], "arm": "control", "proposal": PROPOSAL, "sandbox": sandbox("HEAD", "x")},
                {"of": first["run_id"], "arm": "control", "proposal": PROPOSAL,
                 "sandbox": sandbox(base, "x", skills={"s": "---\ndescription: d\nhooks: x\n---\n"})},
                {"of": a["run_id"], "arm": "control", "proposal": PROPOSAL, "sandbox": sandbox(base, "x")}):
        r = ws.http.post(f"/agents/{AGENT}/runs", json={"trigger": dict(bad, kind="sandbox")})
        assert r.status_code == 400, (bad, r.text)


def test_the_critic_and_the_pairwise_judge(ws):
    put(ws, "work Label them. BAD")
    first = until(ws, run(ws)["run_id"], ("done",))
    review = until(ws, queue(ws, {"kind": "review", "review": {"prompt": REVIEW, "digest": {
        "runs": [{"run_id": first["run_id"]}], "current": {"brief": "work Label them. BAD"}}}})["run_id"], ("done",))
    change = review["changes"][0]
    general = ws.http.post(f"/agents/runs/{review['run_id']}/critic", json={"change": change, "inputs": [first["run_id"]]})
    assert general.status_code == 200, general.text
    assert general.json()["specific"] is False
    specific = dict(change, value="work Label them. SPECIFIC")
    assert ws.http.post(f"/agents/runs/{review['run_id']}/critic",
                        json={"change": specific, "inputs": [first["run_id"]]}).json()["specific"] is True
    assert ws.http.post(f"/agents/runs/{first['run_id']}/critic", json={"change": change, "inputs": []}).status_code == 400
    arms = [until(ws, queue(ws, {"kind": "sandbox", "of": first["run_id"], "arm": arm, "proposal": PROPOSAL,
                                 "sandbox": sandbox(first["base"], brief)})["run_id"], ("done",))["run_id"]
            for arm, brief in (("control", "work Label them. BAD"), ("treatment", "work Label them. GOOD"))]
    graded = ws.http.post("/agents/judge-pair", json={"a": arms[0], "b": arms[1]})
    assert graded.status_code == 200, graded.text
    got = graded.json()
    assert (got["A"]["pass"], got["B"]["pass"]) == (False, True) and len(got["version"]) == 16
    swapped = ws.http.post("/agents/judge-pair", json={"a": arms[1], "b": arms[0]}).json()
    assert (swapped["A"]["pass"], swapped["B"]["pass"]) == (True, False)
    assert ws.http.post("/agents/judge-pair", json={"a": arms[0], "b": first["run_id"]}).status_code == 400
    replays = [until(ws, queue(ws, {"kind": "replay", "of": first["run_id"]})["run_id"], ("done",))["run_id"]
               for _ in range(2)]
    assert ws.http.post("/agents/judge-pair", json={"a": replays[0], "b": replays[1]}).status_code == 400


def test_an_agent_s_own_run_starts_before_a_loop_run_queued_before_it(ws):
    put(ws, "work Label them.")
    first = until(ws, run(ws)["run_id"], ("done",))
    put(ws, "wait 2")
    live = run(ws)["run_id"]
    until(ws, live, ("working",))
    loop = queue(ws, {"kind": "sandbox", "of": first["run_id"], "arm": "control", "proposal": PROPOSAL,
                      "sandbox": sandbox(first["base"], "work Label them.")})["run_id"]
    time.sleep(1.1)                                                  # queued a second after the loop's
    own = run(ws)["run_id"]
    put(ws, "wait 2", limits={"queue_max": 1})                        # its queue full with its own run:
    later = queue(ws, {"kind": "sandbox", "of": first["run_id"], "arm": "treatment", "proposal": PROPOSAL,
                       "sandbox": sandbox(first["base"], "work Label them.")})["run_id"]     # the loop's still queued
    assert ws.http.post(f"/agents/{AGENT}/runs", json={"trigger": {"kind": "manual"}}).status_code == 400
    until(ws, later, ("done", "failed"), timeout=60)
    done = {r: until(ws, r, ("done", "failed"), timeout=60) for r in (live, loop, own)}     # "wait" ends in an error
    assert done[own]["started_at"] < done[loop]["started_at"]


def test_a_machine_stop_ends_a_live_loop_run_and_holds_no_agent(tmp_path):
    w = Workspace(tmp_path)
    try:
        put(w, "work Label them.")
        first = until(w, run(w)["run_id"], ("done",))
        loop = queue(w, {"kind": "sandbox", "of": first["run_id"], "arm": "control", "proposal": PROPOSAL,
                         "sandbox": sandbox(first["base"], "wait")})["run_id"]
        until(w, loop, ("working",))
        w.stop()
        w.start()
        blocked = until(w, loop, ("blocked",))
        assert (blocked["reason"], blocked["cause"], blocked["hold"]) == ("machine stopped", "machine", None)
        assert w.http.get(f"/agents/{AGENT}").json()["paused"] is False                # its worktree was its own
        assert until(w, run(w)["run_id"], ("done",))["state"] == "done"
    finally:
        w.stop()
