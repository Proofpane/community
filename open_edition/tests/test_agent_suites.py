"""An agent's suites (suites.py): items checked, an eval's runs given the question alone and queued within the
agent's queue, each scored from the order its tool results showed sources; and through a real workspace with the
stand-in claude, a suite with one known miss and one late hit."""

import json
import time

from test_agents_app import A, agent, owner, local, until  # noqa: F401  (fixtures)


def me(client, h):
    return client.get("/api/auth/me", headers=h).json()["id"]


def item(question, *sources, group="rag", held_out=False):
    return {"question": question, "sources": list(sources), "group": group, "held_out": held_out}


def quiet(monkeypatch):
    monkeypatch.setattr("proofpane_open.ade.agents._deliver_soon", lambda user, start=False: False)


def test_items_are_checked(client, owner):
    made = agent(client, owner)
    for bad, why in ((item("Where?"), "from 1 to 20 sources"), (item("", "a.py"), "question is some text"),
                     (item("Where?", "a.py", group="other"), "rag or stale"), ({"question": "x", "sources": "a.py"},
                                                                             "from 1 to 20 sources"),
                     (item("Where?", "bad\x07name"), "A source is a path")):
        r = client.post(f"{A}/{made['id']}/suites", headers=owner, json={"name": "Labels", "items": [bad]})
        assert r.status_code == 400 and why in r.json()["detail"], (bad, r.text)
    r = client.post(f"{A}/{made['id']}/suites", headers=owner,
                    json={"name": "Labels", "items": [item("Where are labels set?", "./src/rules.py", "src/rules.py")]})
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["sources"] == ["src/rules.py"]                   # one, as runs name it
    too_many = [item(f"Q{n}", "a.py") for n in range(51)]
    assert client.put(f"{A}/suites/{r.json()['id']}", headers=owner, json={"items": too_many}).status_code == 400


def test_an_eval_s_runs_get_the_question_alone_within_the_queue_and_are_scored(client, owner, monkeypatch):
    from proofpane_open import store
    from proofpane_open.ade import agents
    quiet(monkeypatch)
    user_id = me(client, owner)
    made = agent(client, owner)
    items = [item(f"Question {n}?", f"src/f{n}.py", "docs/secret-answer.md") for n in range(12)] + \
        [item("Held out?", "src/h.py", held_out=True)]
    suite = client.post(f"{A}/{made['id']}/suites", headers=owner, json={"name": "Labels", "items": items}).json()
    r = client.post(f"{A}/suites/{suite['id']}/evals", headers=owner, json={})
    assert r.status_code == 200, r.text
    ev = r.json()["evals"][0]
    assert (ev["items"], ev["open"], ev["scored"]) == (12, 12, 0)                # the held-out item is not asked
    with store.connect() as db:
        runs = db.execute("SELECT run_id, trigger FROM ade_agent_runs WHERE agent_id = ? ORDER BY queued_at, run_id",
                          (made["id"],)).fetchall()
    assert len(runs) == 10                                                         # its queue holds ten
    for row in runs:
        trigger = json.loads(row["trigger"])
        assert trigger["kind"] == "eval" and trigger["prompt"].startswith("Question ")
        assert "secret-answer" not in row["trigger"]                               # never the answer's sources
    by_run = {r["run_id"]: json.loads(r["trigger"])["prompt"] for r in runs}
    first, second, third, fourth = list(by_run)[:4]
    n_first = int(by_run[first].split()[1].rstrip("?"))
    view = lambda run_id, state, sources: {"run_id": run_id, "agent_id": made["id"], "state": state,  # noqa: E731
                                           "metrics": {"retrieval": {"sources": sources}}}
    n_fourth = int(by_run[fourth].split()[1].rstrip("?"))
    agents._apply(user_id, [view(first, "done", ["README.md", "src/x.py", f"src/f{n_first}.py"]),
                            view(second, "done", ["src/none.py"]), view(third, "failed", []),
                            view(fourth, "done", [f"src/o{n}.py" for n in range(5)] + [f"src/f{n_fourth}.py"])])
    ev = client.get(f"{A}/{made['id']}/suites", headers=owner).json()[0]["evals"][0]
    assert (ev["scored"], ev["failed"]) == (3, 1)
    # found third: a hit; not found; found sixth: past the first five, no hit, a reciprocal rank of a sixth
    assert ev["hit_at_5"] == round(1 / 3, 4) and ev["mrr"] == round((1 / 3 + 0 + 1 / 6) / 3, 4)
    with store.connect() as db:                                                    # their ends queued the next ones
        assert db.execute("SELECT COUNT(*) FROM ade_agent_runs WHERE agent_id = ?", (made["id"],)).fetchone()[0] == 12
    held = client.post(f"{A}/suites/{suite['id']}/evals", headers=owner, json={"held_out": True}).json()["evals"][0]
    assert held["held_out"] is True and held["items"] == 1


def test_a_suite_with_one_known_miss_through_the_workspace(client, owner):
    made = agent(client, owner, brief="readnamed", repo="demo")
    questions = [("Where are labels set? See src/f1.py.", "src/f1.py"),
                 ("And the rules? See src/f2.py.", "src/f2.py"),
                 ("The docs? Start at docs/a.md, then docs/b.md, then src/f3.py.", "src/f3.py"),
                 ("The tests? See tests/t4.py.", "tests/t4.py"),
                 ("The owner? See src/wrong.py.", "src/f5.py")]                    # it reads the wrong file: a miss
    suite = client.post(f"{A}/{made['id']}/suites", headers=owner,
                        json={"name": "Where", "items": [item(q, s, "docs/secret-answer.md") for q, s in questions]})
    assert suite.status_code == 200, suite.text
    r = client.post(f"{A}/suites/{suite.json()['id']}/evals", headers=owner, json={})
    assert r.status_code == 200, r.text
    deadline = time.time() + 120
    while True:
        ev = client.get(f"{A}/{made['id']}/suites", headers=owner).json()[0]["evals"][0]
        if ev["scored"] + ev["failed"] == 5:
            break
        assert time.time() < deadline, ev
        time.sleep(0.5)
    assert (ev["scored"], ev["hit_at_5"]) == (5, 0.8)                           # 4 of 5
    assert ev["mrr"] == round((1 + 1 + 1 / 3 + 1 + 0) / 5, 4)
    assert ev["by_group"] == {"rag": {"items": 5, "hit_at_5": 0.8, "mrr": ev["mrr"]}}
    for result in ev["results"]:
        log = client.get(f"/api/ade/workspace/sessions/{result['run_id']}/chat", headers=owner).json()
        first = [e["text"] for e in log["events"] if e["kind"] == "user"][0]
        assert first.startswith("readnamed\n\n") and "secret-answer" not in first


def test_an_eval_s_next_question_is_sent_in_the_sync_that_took_the_one_before(client, owner, monkeypatch):
    from proofpane_open import auth
    from proofpane_open.ade import agents
    quiet(monkeypatch)
    user = auth.user(me(client, owner))
    made = agent(client, owner)
    suite = client.post(f"{A}/{made['id']}/suites", headers=owner,
                        json={"name": "Many", "items": [item(f"Q{n}?", f"src/f{n}.py") for n in range(11)]}).json()
    client.post(f"{A}/suites/{suite['id']}/evals", headers=owner, json={})          # ten queued, one waits
    sent, done = [], {}

    def call(endpoint, method, path, body=None, timeout=None):
        if method == "POST" and path.endswith("/runs"):
            sent.append(body["run_id"])
            return {"run_id": body["run_id"], "agent_id": made["id"], "state": "queued"}
        if path.startswith("/agents/runs?since="):
            first = sent[0]                                    # the workspace says the first one ended
            done[first] = True
            return {"seq": 1, "more": False, "runs": [{"run_id": first, "agent_id": made["id"], "state": "done",
                                                       "metrics": {"retrieval": {"sources": ["src/f0.py"]}}}]}
        return {}
    monkeypatch.setattr(agents, "_endpoint", lambda user: "http://workspace")
    monkeypatch.setattr(agents.client, "call", call)
    agents.sync(user)
    assert len(sent) == 11 and len(set(sent)) == 11                # the eleventh went in the same sync
