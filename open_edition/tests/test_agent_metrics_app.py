"""Agents' numbers in the app (agent_metrics.py): each run's held to its shapes, added to its agent's day once at its
end, read back over 7 or 30 days with percentiles from summed bucket counts, listed, and given as CSV."""

import csv
import io
import json
import random
import time

from proofpane_workspace import metrics as measured
from test_agents_app import A, agent, listing, owner, local  # noqa: F401  (fixtures)

DAY = 86400


def me(client, h):
    return client.get("/api/auth/me", headers=h).json()["id"]


def made_runs(client, h, agent_id, n, monkeypatch):
    monkeypatch.setattr("proofpane_open.ade.agents._deliver_soon", lambda user, start=False: False)
    return [client.post(f"{A}/{agent_id}/runs", headers=h, json={}).json()["run_id"] for _ in range(n)]


def numbers(wall=60, tools=None, cache=(800, 1000), together=None, sources=("src/a.py",), cited=1):
    tools = tools or {"Read": [100, 200], "Bash": [3000]}
    return {"time": {"wall": wall, "queue_wait": 2, "active": wall - 5, "model": wall / 2, "tool": wall / 3,
                     "waiting_owner": 1},
            "wall_hist": measured.bucket_counts([wall]),
            "calls": sum(len(v) for v in tools.values()), "errors": 1, "refused": 0,
            "tools": {name: {"calls": len(ms), "errors": 1 if name == "Bash" else 0, "refused": 0,
                             "p50_ms": measured.exact(ms, 0.5), "p95_ms": measured.exact(ms, 0.95),
                             "hist": measured.bucket_counts(ms)} for name, ms in tools.items()},
            "servers": {}, "together": dict(dict.fromkeys(measured.TOGETHER, 0), **(together or {})),
            "cache": {"read": cache[0], "input": cache[1], "requests": 4, "cold_requests": 1, "ratio": cache[0] / cache[1]},
            "retrieval": {"calls": 3, "surfaced": len(sources), "cited": cited, "sources": list(sources)}}


def ended(run_id, agent_id, at, state="done", cost=0.5, **over):
    return {"run_id": run_id, "agent_id": agent_id, "state": state, "started_at": at - 60, "ended_at": at,
            "cost_est_usd": cost, "metrics": numbers(**over)}


def test_a_run_s_numbers_are_held_to_their_shapes(client, owner, monkeypatch):
    from proofpane_open.ade import agent_metrics
    forged = numbers()
    forged["tools"]["=cmd()|x"] = {"calls": 5}
    forged["tools"]["Read"]["calls"] = "lots"
    forged["time"]["wall"] = -4
    forged["wall_hist"] = [1, 2]
    forged["cache"]["ratio"] = 7
    forged["retrieval"]["sources"] = ["src/a.py", "bad\x07name", 3]
    forged["retrieval"]["cited"] = 9
    got = json.loads(agent_metrics.clean(forged))
    assert "=cmd()|x" not in got["tools"] and got["tools"]["Read"]["calls"] == 0
    assert got["time"]["wall"] == 0 and got["wall_hist"] == [0] * 16 and got["cache"]["ratio"] is None
    assert got["retrieval"] == {"calls": 3, "surfaced": 1, "cited": 1, "sources": ["src/a.py"]}
    assert agent_metrics.clean("not numbers") is None and agent_metrics.clean(None) is None


def test_an_ended_run_is_added_to_its_day_once(client, owner, monkeypatch):
    from proofpane_open import store
    from proofpane_open.ade import agents
    user_id = me(client, owner)
    made = agent(client, owner)
    (r,) = made_runs(client, owner, made["id"], 1, monkeypatch)
    now = int(time.time())
    agents._apply(user_id, [dict(ended(r, made["id"], now), state="working", ended_at=None)])
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_agent_metrics_daily").fetchone()[0] == 0     # not yet: it works
    for _ in range(2):                                        # read again: a new volume's poll gives it once more
        agents._apply(user_id, [ended(r, made["id"], now)])
    with store.connect() as db:
        day = db.execute("SELECT * FROM ade_agent_metrics_daily").fetchone()
        tools = {t["tool"]: t for t in db.execute("SELECT * FROM ade_agent_tool_daily")}
    assert (day["runs"], day["done"], day["calls"], day["errors"], day["wall_s"]) == (1, 1, 3, 1, 60)
    assert (tools["Read"]["calls"], sum(json.loads(tools["Read"]["hist"]))) == (2, 2)
    assert day["day"] == time.strftime("%Y-%m-%d", time.gmtime(now))


def test_a_month_s_percentile_comes_from_the_days_counts_added_together(client, owner, monkeypatch):
    from proofpane_open.ade import agent_metrics, agents
    from proofpane_open import store
    user_id = me(client, owner)
    made = agent(client, owner)
    rng = random.Random(3)
    walls = [round(rng.expovariate(1 / 300)) for _ in range(45)]
    now = int(time.time())
    for i, wall in enumerate(walls):
        (r,) = made_runs(client, owner, made["id"], 1, monkeypatch)       # one at a time: its queue holds ten
        agents._apply(user_id, [ended(r, made["id"], now - (i % 3) * DAY, wall=wall)])     # over three days
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_agent_metrics_daily").fetchone()[0] == 3
        month = agent_metrics.window(db, user_id, made["id"], 30)
    pooled = measured.exact(walls, 0.95)
    bucket = measured.percentile(measured.bucket_counts(walls), 0.95)
    assert bucket["low"] < pooled <= bucket["high"]
    assert bucket["low"] <= month["time"]["wall_p95_s"] <= bucket["high"]
    assert month["runs"]["runs"] == 45 and month["time"]["wall_s"] == sum(walls)


def test_an_agent_s_window_its_tools_its_cache_and_who_it_worked_with(client, owner, monkeypatch):
    from proofpane_open.ade import agents
    user_id = me(client, owner)
    triage, labeler = agent(client, owner), agent(client, owner, name="Labeler")
    first, second, old = made_runs(client, owner, triage["id"], 3, monkeypatch)
    now = int(time.time())
    agents._apply(user_id, [ended(first, triage["id"], now, cost=0.25, together={"dispatches_sent": 1}),
                            ended(second, triage["id"], now, state="failed", cost=0.75,
                                  tools={"Read": [100], "Grep": [40, 60, 80]}, cache=(0, 1000)),
                            ended(old, triage["id"], now - 10 * DAY, cost=9)])
    agents._apply(user_id, [{"run_id": "c" * 12, "agent_id": labeler["id"], "state": "done", "queued_at": now,
                             "trigger": {"kind": "dispatch", "parent_run_id": first, "depth": 1}}])
    r = client.get(f"{A}/{triage['id']}/metrics?days=7", headers=owner)
    assert r.status_code == 200, r.text
    week = r.json()
    assert week["runs"] | {"blocked": 0} == {"runs": 2, "done": 1, "failed": 1, "blocked": 0, "stopped": 0, "skipped": 0}
    assert (week["success_rate"], week["cost_usd"], week["cost_per_success"]) == (0.5, 1.0, 1.0)
    assert [t["name"] for t in week["tools"]] == ["Grep", "Read", "Bash"]                 # by calls
    grep = week["tools"][0]
    assert (grep["calls"], grep["p50_ms"] is not None, 50 < grep["p95_ms"] <= 100) == (3, True, True)
    assert week["cache"]["ratio"] == round(800 / 2000, 4)
    assert week["together"]["dispatches_sent"] == 1 and week["together"]["collaborators"] == 1
    assert week["retrieval"]["read_then_cited"] == 1.0
    month = client.get(f"{A}/{triage['id']}/metrics?days=30", headers=owner).json()
    assert month["runs"]["runs"] == 3 and month["cost_usd"] == 10.0
    assert client.get(f"{A}/{triage['id']}/metrics?days=8", headers=owner).status_code == 400
    listed = [a for g in listing(client, owner)["groups"] for a in g["agents"] if a["id"] == triage["id"]][0]
    assert listed["numbers"]["calls"] == 7 and listed["numbers"]["cache_ratio"] == 0.4
    assert listed["numbers"]["wall_p50_s"] is not None


def test_the_csv_names_its_columns_as_open_telemetry_does_and_is_safe_to_open(client, owner, monkeypatch):
    from proofpane_open.ade import agents
    user_id = me(client, owner)
    made = agent(client, owner, name="=HYPERLINK(1)")
    (r,) = made_runs(client, owner, made["id"], 1, monkeypatch)
    agents._apply(user_id, [ended(r, made["id"], int(time.time()))])
    got = client.get(f"{A}/metrics.csv?days=30", headers=owner)
    assert got.status_code == 200 and got.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(got.text)))
    assert len(rows) == 1 and rows[0]["agent"] == "'=HYPERLINK(1)"
    assert rows[0]["gen_ai.invoke_agent.tool_calls"] == "3" and float(rows[0]["gen_ai.invoke_agent.duration.p50_s"]) > 0
    assert rows[0]["cache_hit_ratio"] == "0.8" and rows[0]["est_cost_usd"] == "0.5"
    assert client.get(f"{A}/metrics.csv?days=5", headers=owner).status_code == 400


def test_days_older_than_a_year_go(client, owner, monkeypatch):
    from proofpane_open import store
    from proofpane_open.ade import agents
    user_id = me(client, owner)
    made = agent(client, owner)
    old, new = made_runs(client, owner, made["id"], 2, monkeypatch)
    now = int(time.time())
    agents._apply(user_id, [ended(old, made["id"], now - 370 * DAY), ended(new, made["id"], now)])
    with store.connect() as db:
        agents.forget(db, now)
        assert db.execute("SELECT COUNT(*) FROM ade_agent_metrics_daily").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM ade_agent_tool_daily WHERE day < ?",
                          (time.strftime("%Y-%m-%d", time.gmtime(now - 366 * DAY)),)).fetchone()[0] == 0


def test_a_day_s_numbers_are_kept_a_year(env):
    from proofpane_open import store
    from proofpane_open.ade import agent_metrics
    now = int(time.time())
    with store.connect() as db:
        for age in (89, 200, 365, 400):
            db.execute("INSERT INTO ade_agent_metrics_daily (agent_id, user_id, day, runs) VALUES (?, 'u', ?, 1)",
                       (f"{age:012d}", agent_metrics._day(now - age * 86400)))
        agent_metrics.forget(db, now)
        kept = sorted(int(r[0]) for r in db.execute("SELECT agent_id FROM ade_agent_metrics_daily"))
    assert kept == [89, 200, 365]
