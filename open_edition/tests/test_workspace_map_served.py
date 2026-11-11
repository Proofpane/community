"""The control view's map against the real workspace agent: the app's two map routes (`workspace_routes.workspace_map`
and `session_map`) run through to a workspace agent the app starts, whose sessions' records and map sources are made as
the map's own tests make them (test_workspace_sessionmap.py: a stand-in claude that runs the status lines and the hooks
its settings name, with the inputs Claude Code gives them, and a transcript where Claude Code keeps one). What the
routes answer is kept in tests/fixtures/session-map-served.json, which the page's
test (ui/src/sessionMapServed.test.ts) reads with sessionMap.ts's mapOf and callsOf, as the page reads the routes: each
field the agent serves must arrive there with its value and its type. So a field renamed in the agent, or served as
another type (a boolean as a number, say), fails this test, and one renamed in the page fails the page's.

In the file, the session ids and every time are made fixed, and each list is kept to its first three items, so that it
is the same at every run; each time is first checked to be a whole number of seconds, within the run or one the inputs
gave. The cases the page must read are in those items: an agent's effort that is a token budget, agents left out past
the 50 given (`agents_more`), an agent whose status is not known (null) with no end and one with an end, a call whose
target is not named (null), and an answer cut to 64 KB. Where a field changed on purpose, change
ui/src/sessionMapExamples.json, which names the fields both tests hold each answer to, and the page's reader to match
(where a field's type or a time's unit changed, this test's own checks of them too); then write the file again
(PP_MAP_VECTORS_WRITE=1) and run the page's test."""

import json
import os
import stat
import sys
import time

import pytest

from conftest import ADMIN, signed_up
from proofpane_workspace import activity, usage
from test_workspace_sessionmap import ROWS, SECRET, STAND_IN, _calls, _inputs, _reply, _transcript, _user
from workspace_fixtures import make_repo

HERE = os.path.dirname(os.path.abspath(__file__))
SERVED = os.path.join(HERE, "fixtures", "session-map-served.json")
with open(os.path.join(HERE, "..", "ui", "src", "sessionMapExamples.json"), encoding="utf-8") as fh:
    EXAMPLES = json.load(fh)
# the map's fields, each object's, as its examples have them (with agents_more, which the agent serves beyond
# them)
SESSION = set(EXAMPLES["map"]["sessions"][0])
AGENT = set(EXAMPLES["map"]["sessions"][0]["agents"][0])
ONE = set(EXAMPLES["session_map"])
HOLDER = set(EXAMPLES["session_map"]["agents"][0])
CALL = set(EXAMPLES["session_map"]["main"]["calls"][0])
TURN = set(EXAMPLES["session_map"]["turns"][0])
MAP_ROUTE = "/api/ade/workspace/map"
KEEP = 3
# the fixed values the file holds in place of the session ids and the times, which differ at each run
IDS = ("aaaaaaaaaaa1", "bbbbbbbbbbb2", "ccccccccccc3")
TIMES = {"at": 1760730000, "started_at": 1760720000, "updated_at": 1760729990, "ended_at": 1760725000}
# the times of the calls written straight into the second session's file, to cut its answer
FLOOD = range(1760729000, 1760732000)
# test_workspace_sessionmap.py's stand-in, signed in as well: the app starts a claude session only once Claude Code
# says it is
# (workspace_routes._claude_ready, which asks `claude auth status --json`, as test_workspace_accounts.py's stand-in
# answers)
_VERSION_END = '    print("2.1.274 (Claude Code)")\n    sys.exit(0)\n'
assert STAND_IN.count(_VERSION_END) == 1
CLAUDE = STAND_IN.replace(_VERSION_END, _VERSION_END + (
    'if sys.argv[1:3] == ["auth", "status"]:\n'
    '    print(json.dumps({"loggedIn": True, "authMethod": "claude.ai"}))\n'
    '    sys.exit(0)\n'))
FROM = ("Written by open_edition/tests/test_workspace_map_served.py from what the app's two map routes answer "
        "with the real workspace agent behind them: the map, then each session's calls and turns, the sessions in the "
        "order they were made. "
        "Its session ids and times are made fixed and each list kept to its first three items; the page's test, "
        "ui/src/sessionMapServed.test.ts, reads it with sessionMap.ts's mapOf and callsOf.")
CHANGED = ("if a field changed on purpose, change ui/src/sessionMapExamples.json, which names the fields both tests "
           "hold each answer to, and the page's reader (ui/src/sessionMap.ts) to match (a field's type or a time's "
           "unit, this test's own checks of them too); then write the file again (PP_MAP_VECTORS_WRITE=1) and run "
           "ui/src/sessionMapServed.test.ts")


def _one(sid):
    return f"/api/ade/workspace/sessions/{sid}/map"


def _rich():
    """The first session's inputs: test_workspace_sessionmap.py's (two agents the line shows, their calls, a failure,
    a stop, commands whose program is not named), and an agent only the hook saw start, so known with no name, tokens,
    model or effort."""
    inputs = _inputs()
    inputs["events"].append({"hook_event_name": "SubagentStart", "agent_id": "agent-h1", "agent_type": "Plan"})
    return inputs


def _budget():
    """The second session's: an agent whose effort is a token budget; one whose status this release does not know; one
    that ended with its status not known (the line saw it running, then the hook saw it stop: most agents that end well
    are seen so); and more agents than the 50 the map gives, the four oldest left out
    (`agents_more`). None of them makes a call."""
    def task(agent_id, status, start, **given):
        row = {"id": agent_id, "type": "local_agent", "agentType": "Explore", "status": status,
               "description": f"Read part {agent_id}", "startTime": start, "model": "claude-haiku-5-5",
               "effort": "low", "tokenCount": 120}
        row.update(given)
        return row
    tasks = [task("agent-c1", "running", 1760500000000, name="on a budget", agentType="general-purpose",
                  model="claude-sonnet-5-5", effort=31999, tokenCount=48200)]
    tasks += [task(f"agent-f{n:02d}", "completed", 1760600000000 + n * 1000) for n in range(4)]
    tasks += [task("agent-p1", "pending", 1760650000000, effort="medium")]
    tasks += [task("agent-e1", "running", 1760660000000, name="ended well", effort="high", tokenCount=3300)]
    tasks += [task(f"agent-f{n:02d}", "completed", 1760700000000 + n * 1000) for n in range(4, 51)]
    status = {"model": {"id": "claude-sonnet-5-5", "display_name": "Sonnet 5.5"}, "effort": {"level": "high"},
              "context_window": {"used_percentage": 12, "total_input_tokens": 120000, "total_output_tokens": 3}}
    events = [{"hook_event_name": "SubagentStart", "agent_id": "agent-e1", "agent_type": "Explore"},
              {"hook_event_name": "SubagentStop", "agent_id": "agent-e1", "agent_type": "Explore",
               "last_assistant_message": SECRET}]
    return {"status": status, "agents": {"tasks": tasks}, "events": events}


def _claude(client, h, bin_dir, env_dir, me, task, inputs):
    """A claude session started through the app, its stand-in run to its end with these inputs."""
    (bin_dir / "INPUTS").write_text(json.dumps(inputs))
    r = client.post("/api/ade/workspace/sessions", headers=h, json={"repo": "demo", "program": "claude", "task": task})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    folder = env_dir / "workspaces" / me / "sessions" / sid
    deadline = time.time() + 30
    while not (folder / "done").exists():
        assert time.time() < deadline, "the stand-in did not run its hooks"
        time.sleep(0.05)
    return sid, folder


@pytest.fixture
def served(client, env, monkeypatch, tmp_path):
    began = int(time.time())
    bin_dir, home = tmp_path / "bin", tmp_path / "home"
    bin_dir.mkdir()
    home.mkdir()                        # no git identity of the machine's user in a test
    fake = bin_dir / "claude"
    fake.write_text(CLAUDE.replace("{python}", sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OPEN_EDITION_ADE", "local")
    monkeypatch.setenv("OPEN_EDITION_ADMINS", ADMIN)
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    from proofpane_open.ade import workspaces
    rich_inputs, budget_inputs = _rich(), _budget()
    # the times the inputs give, which the agent serves as they are: each agent's start, as the line has it in seconds
    given = {t["startTime"] // 1000 for i in (rich_inputs, budget_inputs) for t in i["agents"]["tasks"]} | set(FLOOD)
    try:
        h = signed_up(client)
        r = client.post("/api/ade/workspace/start", headers=h)
        assert r.status_code == 200 and r.json()["running"] is True, r.text
        me = client.get("/api/auth/me", headers=h).json()["id"]
        make_repo(str(env / "workspaces" / me / "repos" / "demo"))
        rich, rich_dir = _claude(client, h, bin_dir, env, me, "Map the parser", rich_inputs)
        # what Claude Code writes of the conversation, and the conversation its hooks named, as
        # test_workspace_sessionmap.py writes it
        work = os.path.realpath(rich_dir / "work")
        _transcript(str(home / ".claude" / "projects" / usage.slug(work) / "c0ffee-1.jsonl"), *ROWS)
        _transcript(str(home / ".claude" / "projects" / usage.slug(work) / "c0ffee-1" / "subagents"
                        / "agent-agent-a1.jsonl"), _user(f"sub prompt {SECRET}"), _reply("s1", f"sub reply {SECRET}"))
        activity.record("working", {"hook_event_name": "UserPromptSubmit", "session_id": "c0ffee-1"},
                        directory=str(rich_dir))
        budget, budget_dir = _claude(client, h, bin_dir, env, me, "Plan on a budget", budget_inputs)
        activity.record("working", {"hook_event_name": "UserPromptSubmit", "session_id": "c0ffee-2"},
                        directory=str(budget_dir))
        # its calls past 64 KB, so that its answer is cut, the calls left out counted
        _calls(budget_dir, *[{"at": at, "agent": None, "tool": "Read", "target": f"/w/part/{at - FLOOD[0]:04d}.py",
                              "failed": False} for at in FLOOD])
        r = client.post("/api/ade/workspace/sessions", headers=h, json={"repo": "demo", "program": "shell",
                                                                        "task": "A shell"})
        assert r.status_code == 200, r.text
        shell = r.json()["id"]
        r = client.post("/api/ade/workspace/sessions", headers=h, json={"repo": "demo", "program": "shell",
                                                                        "task": "Closed"})
        closed = r.json()["id"]
        assert client.post(f"/api/ade/workspace/sessions/{closed}/close", headers=h).status_code == 200
        yield client, h, (rich, budget, shell), closed, began, given
    finally:
        workspaces.shutdown()


def _made_fixed(answer, ids):
    """The answer with its session ids and times made fixed, and each list kept to its first KEEP items."""
    text = json.dumps(answer)
    for real, fixed in zip(ids, IDS):
        text = text.replace(real, fixed)

    def fixed(value):
        if isinstance(value, dict):
            return {k: (TIMES[k] if k in TIMES and type(v) is int else fixed(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [fixed(v) for v in value[:KEEP]]
        return value
    return fixed(json.loads(text))


def _times_wrong(value, low, high, given, path="answer"):
    """Each time in the answer that is not a whole number of seconds, within the run (low to high) or one the inputs
    gave, or null: the file keeps none of them, so this is where their unit and their kind are held."""
    wrong = []
    if isinstance(value, dict):
        for k, v in value.items():
            if k in TIMES and not (v is None or (type(v) is int and (low <= v <= high or v in given))):
                wrong.append((f"{path}.{k}", v))
            elif isinstance(v, (dict, list)):
                wrong += _times_wrong(v, low, high, given, f"{path}.{k}")
    elif isinstance(value, list):
        for n, v in enumerate(value):
            wrong += _times_wrong(v, low, high, given, f"{path}[{n}]")
    return wrong


def _differs(kept, made, path="file"):
    """Where the file and the answer first differ, each value of the same type as well as equal (in Python, True is
    1 and 1.0 is 1: a boolean served as a number would otherwise pass); None where they do not."""
    if type(kept) is not type(made):
        return f"{path}: {kept!r} in the file, {made!r} served"
    if isinstance(made, dict):
        if set(kept) != set(made):
            return f"{path}: fields {sorted(kept)} in the file, {sorted(made)} served"
        return next((d for d in (_differs(kept[k], made[k], f"{path}.{k}") for k in made) if d), None)
    if isinstance(made, list):
        if len(kept) != len(made):
            return f"{path}: {len(kept)} items in the file, {len(made)} served"
        pairs = enumerate(zip(kept, made))
        return next((d for d in (_differs(a, b, f"{path}[{n}]") for n, (a, b) in pairs) if d), None)
    return None if kept == made else f"{path}: {kept!r} in the file, {made!r} served"


def test_what_the_routes_answer_against_the_real_agent_is_what_the_page_s_test_reads(served):
    client, h, ids, closed, began, given = served
    r = client.get(MAP_ROUTE, headers=h)
    assert r.status_code == 200, r.text
    got = r.json()
    rows = {row["id"]: row for row in got["sessions"]}
    assert set(rows) == set(ids) and closed not in rows      # the closed one left out, as the list leaves it
    ones = {}
    for sid in ids:
        r = client.get(_one(sid), headers=h)
        assert r.status_code == 200, r.text
        ones[sid] = r.json()
    # each object has the map's fields, no fewer and no more: a field renamed in the agent fails here first
    assert set(got) == {"at", "sessions"}, CHANGED
    for row in rows.values():
        assert set(row) == SESSION, (row["id"], CHANGED)
        for agent in row["agents"]:
            assert set(agent) == AGENT, (agent["id"], CHANGED)
    for one in ones.values():
        assert set(one) == ONE, CHANGED
        for holder in [one["main"], *one["agents"]]:
            assert set(holder) - {"id"} == HOLDER - {"id"}, CHANGED
            for call in holder["calls"]:
                assert set(call) == CALL and type(call["failed"]) is bool, (call, CHANGED)
        assert type(one["cut"]) is bool
        for turn in one["turns"]:
            assert set(turn) == TURN, CHANGED
    # each time a whole number of seconds, within the run or as the inputs gave it, before the file makes them fixed
    assert _times_wrong([got, *ones.values()], began - 5, int(time.time()) + 5, given) == []
    # the cases the page must read, as the agent serves them
    rich, budget, shell = ids
    by_id = {a["id"]: a for a in rows[budget]["agents"]}
    assert rows[budget]["agents_more"] == 4 and len(rows[budget]["agents"]) == 50
    assert by_id["agent-c1"]["effort"] == 31999 and by_id["agent-p1"]["status"] is None
    assert by_id["agent-e1"]["status"] is None and type(by_id["agent-e1"]["ended_at"]) is int
    assert by_id["agent-p1"]["ended_at"] is None
    assert [a["id"] for a in rows[budget]["agents"][:KEEP]] == ["agent-c1", "agent-p1", "agent-e1"]
    assert {a["id"]: a["name"] for a in rows[rich]["agents"]}["agent-h1"] is None
    assert rows[shell]["agents_read"] == "no" and rows[shell]["account"] is None
    assert {x["id"]: x for x in ones[rich]["agents"]}["agent-b2"]["calls"][0]["target"] is None
    assert any(c["failed"] is True for x in ones[rich]["agents"] for c in x["calls"][:KEEP])
    assert ones[budget]["cut"] is True and ones[budget]["main"]["more"] > 0 and ones[rich]["cut"] is False
    assert any(t["agent"] for t in ones[rich]["turns"][:KEEP])
    # the file, as the page's test reads it: the map, then each session's calls and turns, the sessions in the order
    # they were made
    made = {"from": FROM, "map": _made_fixed(dict(got, sessions=[rows[sid] for sid in ids]), ids),
            "session_maps": [_made_fixed(ones[sid], ids) for sid in ids]}
    if os.environ.get("PP_MAP_VECTORS_WRITE") == "1":
        with open(SERVED, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(made, indent=1, ensure_ascii=True) + "\n")
    with open(SERVED, encoding="utf-8") as fh:
        kept = json.load(fh)
    differs = _differs(kept, made)
    assert differs is None, f"the agent no longer serves what the page's test reads ({differs}): {CHANGED}"
