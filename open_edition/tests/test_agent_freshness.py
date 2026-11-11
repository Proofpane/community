"""How an agent's knowledge stays current (knowledge.py, freshness.py, the stale group of suites.py): knowledge files
by tier, past their check or carrying none; the freshness the app shows from the sweep and its ledger, told once past
a fifth stale; the weekly re-verify run, off unless turned on; and a stale question scored a miss when the reply gives
a superseded value."""

import datetime
import json
import os
import time

from proofpane_workspace import knowledge
from test_agents_app import A, agent, owner, local  # noqa: F401  (fixtures)

DAY = 86400
TODAY = datetime.date(2026, 10, 3)


def note(root, rel, front=None, body="text"):
    path = os.path.join(root, "knowledge", rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    head = "---\n" + "".join(f"{k}: {v}\n" for k, v in front.items()) + "---\n" if front is not None else ""
    with open(path, "w") as fh:
        fh.write(head + body)


def test_knowledge_files_by_tier_past_their_check_or_carrying_none(tmp_path):
    root = str(tmp_path)
    note(root, "deploy.md", {"status": "current", "as_of": "2026-09-01", "source": "the runbook"})      # to 11-30
    note(root, "labels.md", {"status": "current", "as_of": "2026-05-01", "source": "the tracker"})       # past 07-30
    note(root, "api.md", {"status": "current", "verify_by": "2026-10-02", "source": "the docs"})        # past
    note(root, "old/plan.md", {"status": "historical"})
    note(root, "gone.md", {"status": "deprecated"})
    note(root, "review-2026-09-29.md")                                                     # dated: historical
    note(root, "loose.md", body="no frontmatter")
    note(root, "odd.md", {"status": "maybe"})
    note(root, "notes.txt", {"status": "current", "as_of": "2020-01-01"})                  # not Markdown: not read
    os.symlink(os.path.join(root, "knowledge", "labels.md"), os.path.join(root, "knowledge", "link.md"))
    got = knowledge.scan(root, TODAY)
    assert {k: got[k] for k in ("files", "current", "historical", "deprecated", "untiered")} == \
        {"files": 8, "current": 3, "historical": 2, "deprecated": 1, "untiered": 2}
    assert got["stale"] == [{"path": "knowledge/api.md", "verify_by": "2026-10-02", "source": "the docs"},
                            {"path": "knowledge/labels.md", "verify_by": "2026-07-30", "source": "the tracker"}]
    assert sorted(got["untiered_paths"]) == ["knowledge/loose.md", "knowledge/odd.md"]
    assert knowledge.scan(str(tmp_path / "none"), TODAY)["files"] == 0
    assert knowledge.front("---\nstatus: current\nno end") == {}


def sweep(day, agent_id, stale=(), current=3, files=5, archived=()):
    return {"day": day, "result": {agent_id: {"archived": list(archived), "knowledge": {
        "files": files, "current": current, "historical": 1, "deprecated": 1, "untiered": files - current - 2,
        "stale": [{"path": p, "verify_by": "2026-09-01", "source": "the docs"} for p in stale]}}}}


def told(db, agent_id, user_id, fid, recorded_at, contradicts=None):
    db.execute("INSERT INTO ade_agent_facts (id, user_id, agent_id, subject, relation, object, valid_at, recorded_at, "
               "source, basis, contradicts, changed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
               (fid, user_id, agent_id, "service api", "owner", "Ana", recorded_at, recorded_at, "a chat", "told",
                contradicts, recorded_at))


def test_freshness_from_the_sweep_and_the_ledger_told_once_past_a_fifth_stale(client, owner):
    from proofpane_open import store
    from proofpane_open.ade import agents, freshness
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    made = agent(client, owner)
    now = int(time.time())
    with store.connect() as db:
        freshness.take(db, user_id, sweep("2026-10-03", made["id"], archived=["b.md"]), now)
        told(db, made["id"], user_id, "a" * 12, now - 40 * DAY)                       # an old claim
        told(db, made["id"], user_id, "b" * 12, now - DAY, contradicts="c" * 12)      # a new one, contradicting
        got = freshness.summary(db, made["id"], now)
    assert (got["current_share"], got["stale_count"], got["told_stale"], got["contradictions"], got["items"]) == \
        (0.6, 1, 1, 1, 7)
    assert got["archived"] == ["b.md"] and got["stale_share"] == round(1 / 7, 4)
    assert not [a for a in agents.alerts(user_id, now) if a[0].startswith("agent-stale:")]          # under a fifth
    with store.connect() as db:
        freshness.take(db, user_id, sweep("2026-10-04", made["id"], stale=["knowledge/x.md", "knowledge/y.md"]), now)
    first = [a for a in agents.alerts(user_id, now) if a[0].startswith("agent-stale:")]
    # told in fixed words: the counts are the page's (its freshness, below)
    assert len(first) == 1 and first[0][1] == "agent-look"
    assert [a for a in agents.alerts(user_id, now + DAY) if a[0].startswith("agent-stale:")] == first   # once
    with store.connect() as db:
        freshness.take(db, user_id, sweep("2026-10-05", made["id"]), now)
    assert not [a for a in agents.alerts(user_id, now) if a[0].startswith("agent-stale:")]
    with store.connect() as db:
        freshness.take(db, user_id, sweep("2026-10-06", made["id"], stale=["knowledge/x.md", "knowledge/y.md"]), now)
    again = [a for a in agents.alerts(user_id, now + 3 * DAY) if a[0].startswith("agent-stale:")]
    assert len(again) == 1 and again[0][0] != first[0][0]                                # past it again: told again
    listed = [a for g in client.get(A, headers=owner).json()["groups"] for a in g["agents"] if a["id"] == made["id"]][0]
    assert listed["freshness"]["stale_count"] == 3 and listed["reverify"] is False


def test_a_current_file_past_its_check_is_not_counted_current(client, owner):
    from proofpane_open import store
    from proofpane_open.ade import freshness
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    made = agent(client, owner)
    now = int(time.time())
    with store.connect() as db:
        freshness.take(db, user_id, sweep("2026-10-04", made["id"], stale=["knowledge/x.md"]), now)
        assert freshness.summary(db, made["id"], now)["current_share"] == 0.4                # (3 - 1) of 5 tiered


def test_a_re_verify_run_only_when_turned_on_with_each_flagged_item_and_its_source_weekly(client, owner, monkeypatch):
    from proofpane_open import store
    from proofpane_open.ade import freshness
    monkeypatch.setattr("proofpane_open.ade.agents._deliver_soon", lambda user, start=False: False)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    made = agent(client, owner)
    now = int(time.time())
    with store.connect() as db:
        told(db, made["id"], user_id, "a" * 12, now - 40 * DAY)
        assert freshness.take(db, user_id, sweep("2026-10-03", made["id"], stale=["knowledge/x.md"]), now) == []
    assert client.put(f"{A}/{made['id']}", headers=owner, json={"reverify": True}).json()["reverify"] is True
    with store.connect() as db:
        due = freshness.take(db, user_id, sweep("2026-10-04", made["id"], stale=["knowledge/x.md"]), now)
        (agent_row, items), = due
        assert agent_row["id"] == made["id"]
        assert items[0]["kind"] == "knowledge" and items[0]["path"] == "knowledge/x.md"
        assert items[0]["source"].startswith("the docs")
        assert items[1] == {"kind": "fact", "id": "a" * 12, "subject": "service api", "relation": "owner",
                            "object": "Ana", "source": "a chat"}
        assert freshness.take(db, user_id, sweep("2026-10-05", made["id"], stale=["knowledge/x.md"]), now + DAY) == []
        assert len(freshness.take(db, user_id, sweep("2026-10-11", made["id"], stale=["knowledge/x.md"]),
                                  now + 7 * DAY)) == 1


def test_the_sync_takes_the_day_s_sweep_once_and_queues_the_re_verify_run(client, owner, monkeypatch):
    from proofpane_open import auth, store
    from proofpane_open.ade import agents
    monkeypatch.setattr("proofpane_open.ade.agents._deliver_soon", lambda user, start=False: False)
    user = auth.user(client.get("/api/auth/me", headers=owner).json()["id"])
    made = agent(client, owner, reverify=True)
    asked, sent = [], []

    def call(endpoint, method, path, body=None, timeout=None):
        if path == "/agents/sweep":
            asked.append(path)
            return sweep("2026-10-03", made["id"], stale=["knowledge/x.md"])
        if path.startswith("/agents/runs?since="):
            return {"seq": 0, "runs": []}
        if method == "POST" and path.endswith("/runs"):
            sent.append(body["trigger"])
            return {"run_id": body["run_id"], "agent_id": made["id"], "state": "queued"}
        return {}
    monkeypatch.setattr(agents, "_endpoint", lambda user: "http://workspace")
    monkeypatch.setattr(agents.client, "call", call)
    taken = []
    keep = agents.freshness.take
    monkeypatch.setattr(agents.freshness, "take", lambda *a, **k: taken.append(a[2]["day"]) or keep(*a, **k))
    agents.sync(user)
    agents.sync(user)
    with store.connect() as db:
        runs = db.execute("SELECT trigger FROM ade_agent_runs WHERE agent_id = ?", (made["id"],)).fetchall()
    assert [json.loads(r["trigger"])["kind"] for r in runs] == ["reverify"] and len(asked) == 2
    assert taken == ["2026-10-03"]                                                # the day's sweep, taken once
    (trigger,) = sent                                   # its items as data, never a prompt
    assert trigger["prompt"] is None and trigger["reverify"]["items"][0]["path"] == "knowledge/x.md"


def test_a_stale_question_is_a_miss_when_the_reply_gives_a_superseded_value(client, owner, monkeypatch):
    from proofpane_open.ade import agents
    monkeypatch.setattr("proofpane_open.ade.agents._deliver_soon", lambda user, start=False: False)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    made = agent(client, owner)
    item = lambda q, a="1.5.0", old=("1.4.2",): {"question": q, "group": "stale", "answer": a,  # noqa: E731
                                                 "superseded": list(old)}
    for bad in ({"question": "Which?", "group": "stale"}, {"question": "Which?", "group": "stale", "answer": "x",
                                                           "superseded": "1.4.2"}):
        assert client.post(f"{A}/{made['id']}/suites", headers=owner, json={"name": "s", "items": [bad]}).status_code == 400
    suite = client.post(f"{A}/{made['id']}/suites", headers=owner,
                        json={"name": "Versions", "items": [item("Which version is live? (1)"), item("Which? (2)"),
                                                            item("Which? (3)"),
                                                            item("Who owns it? (4)", "Ana Smith", ("Ben Jones",))]}).json()
    first = [i for i in suite["items"] if i["question"] == "Which version is live? (1)"][0]
    assert first["answer"] == "1.5.0" and first["superseded"] == ["1.4.2"]
    client.post(f"{A}/suites/{suite['id']}/evals", headers=owner, json={})
    from proofpane_open import store
    with store.connect() as db:
        runs = {json.loads(r["trigger"])["prompt"]: r["run_id"] for r in
                db.execute("SELECT run_id, trigger FROM ade_agent_runs WHERE agent_id = ?", (made["id"],))}
    replies = {"Which version is live? (1)": "Version 1.5.0 is live.", "Which? (2)": "It was 1.4.2, now 1.5.0.",
               "Which? (3)": "1.4.2", "Who owns it? (4)": "It is ana   SMITH now."}       # the same name, written so
    agents._apply(user_id, [{"run_id": runs[q], "agent_id": made["id"], "state": "done",
                             "metrics": {"answer": reply}} for q, reply in replies.items()])
    ev = client.get(f"{A}/{made['id']}/suites", headers=owner).json()[0]["evals"][0]
    assert ev["stale_accuracy"] == 0.5 and ev["hit_at_5"] is None and ev["mrr"] is None
    assert ev["by_group"] == {"stale": {"items": 4, "hit_at_5": 0.5, "mrr": None}}
    assert sorted(r["seen"] for r in ev["results"]) == [[], [], ["1.4.2"], ["1.4.2"]]
