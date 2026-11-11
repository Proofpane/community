"""An agent's quality in the app (quality.py): verdicts, anomalies and acceptance held to their shapes; which runs the
judge is asked about; trust per judge version; drift that drives nothing until its judge is trusted; replays and their
agreement; a schedule's adherence; and the window's numbers."""

import json
import time

from test_agents_app import A, agent, owner, local, until  # noqa: F401  (fixtures)

DAY = 86400
GOOD = "a" * 16


def me(client, h):
    return client.get("/api/auth/me", headers=h).json()["id"]


def quiet(monkeypatch):
    monkeypatch.setattr("proofpane_open.ade.agents._deliver_soon", lambda user, start=False: False)


def made(client, h, agent_id):
    return client.post(f"{A}/{agent_id}/runs", headers=h, json={}).json()["run_id"]


def verdict(score, version=GOOD, cost=0.01):
    return {"scores": {d: score for d in ("task_fit", "grounding", "completeness", "hygiene", "actionable")},
            "pass": score >= 0.6, "score": score, "version": version, "why": "ok", "cost_est_usd": cost}


def ended(run_id, agent_id, at, **over):
    return dict({"run_id": run_id, "agent_id": agent_id, "state": "done", "started_at": at - 30, "ended_at": at,
                 "cost_est_usd": 0.1}, **over)


def apply(user_id, *views):
    from proofpane_open.ade import agents
    agents._apply(user_id, list(views))


def shown_run(user_id, run_id):
    from proofpane_open import store
    from proofpane_open.ade import agents
    with store.connect() as db:
        return agents.run_view(agents._run_row(db, user_id, run_id))


def judged(run_id, given):
    """A verdict as the app keeps it from its own judge call (_judge_due): a run's view never carries one."""
    from proofpane_open import store
    from proofpane_open.ade import quality
    with store.connect() as db:
        db.execute("UPDATE ade_agent_runs SET judge = ? WHERE run_id = ?", (quality.clean_judge(given), run_id))


def test_verdicts_anomalies_and_acceptance_are_held_to_their_shapes():
    from proofpane_open.ade import quality
    assert json.loads(quality.clean_judge(verdict(0.7)))["pass"] is True
    for bad in ({"scores": {"task_fit": 2}}, dict(verdict(0.7), version="short"), "graded", None,
                dict(verdict(0.7), scores=dict(verdict(0.7)["scores"], hygiene=True))):
        assert quality.clean_judge(bad) is None, bad
    forged = dict(verdict(0.9), pass_=True)
    forged["scores"]["task_fit"] = 0.2
    assert json.loads(quality.clean_judge(forged))["pass"] is False                   # the pass is recomputed
    assert json.loads(quality.clean_anomalies([{"rule": "spend", "level": "critical", "at": 1, "evidence": "x"},
                                               {"rule": "made_up", "level": "warning", "at": 1}])) == \
        [{"rule": "spend", "level": "critical", "at": 1, "evidence": "x"}]
    assert quality.clean_acceptance({"pass": "yes"}) is None
    assert json.loads(quality.clean_acceptance({"pass": False, "failures": ["no key a", 3]})) == \
        {"pass": False, "failures": ["no key a"], "retried": False}


def test_the_judge_is_asked_about_a_sample_the_labelled_runs_and_replays_each_once(client, owner, monkeypatch):
    from proofpane_open import store
    from proofpane_open.ade import quality
    quiet(monkeypatch)
    user_id = me(client, owner)
    a = agent(client, owner)
    assert a["judge_runs"] is False                      # off until the owner switches the agent on
    now = int(time.time())
    runs = []
    while len(runs) < 3 or not any(quality.sampled(r) for r in runs):
        r = made(client, owner, a["id"])
        apply(user_id, ended(r, a["id"], now))
        runs.append(r)
    sampled = [r for r in runs if quality.sampled(r)]
    labelled = [r for r in runs if not quality.sampled(r)][0]
    client.post(f"{A}/runs/{labelled}/thumbs", headers=owner, json={"value": "down"})
    with store.connect() as db:
        assert quality.due(db, user_id, limit=50) == []                                # nothing while it is off
    assert client.put(f"{A}/{a['id']}", headers=owner, json={"judge_runs": True}).json()["judge_runs"] is True
    with store.connect() as db:
        due = quality.due(db, user_id, limit=50)
    assert sampled[0] in due and labelled in due
    assert not set(due) - set(sampled) - {labelled}                                   # nothing else
    with store.connect() as db:
        db.execute("UPDATE ade_agent_runs SET judge_asked = 1 WHERE run_id = ?", (labelled,))
        assert labelled not in quality.due(db, user_id, limit=50)                     # asked once


def test_a_labelled_run_is_due_behind_any_number_of_newer_runs_and_a_labelled_failed_one_too(client, owner,
                                                                                            monkeypatch):
    import secrets
    from proofpane_open import store
    from proofpane_open.ade import quality
    quiet(monkeypatch)
    user_id = me(client, owner)
    a = agent(client, owner)
    client.put(f"{A}/{a['id']}", headers=owner, json={"judge_runs": True})
    now = int(time.time())
    labelled, failed = made(client, owner, a["id"]), made(client, owner, a["id"])
    apply(user_id, ended(labelled, a["id"], now - 1000), ended(failed, a["id"], now - 900, state="failed"))
    client.post(f"{A}/runs/{labelled}/thumbs", headers=owner, json={"value": "up"})
    client.post(f"{A}/runs/{failed}/thumbs", headers=owner, json={"value": "down"})
    with store.connect() as db:                         # 250 newer runs, none sampled nor labelled
        newer = 0
        while newer < 250:
            run_id = secrets.token_hex(6)
            if quality.sampled(run_id):
                continue
            db.execute("INSERT INTO ade_agent_runs (run_id, agent_id, user_id, state, sent, queued_at, ended_at, "
                       "updated_at) VALUES (?, ?, ?, 'done', 1, ?, ?, ?)", (run_id, a["id"], user_id, now, now, now))
            newer += 1
        assert set(quality.due(db, user_id, limit=50)) == {labelled, failed}
    unlabelled = made(client, owner, a["id"])
    apply(user_id, ended(unlabelled, a["id"], now, state="failed"))
    with store.connect() as db:
        assert unlabelled not in quality.due(db, user_id, limit=50)                  # failed, and not labelled


def test_the_spend_baseline_is_the_median_of_the_agent_s_work_runs_alone(client, owner, monkeypatch):
    from proofpane_open import store
    from proofpane_open.ade import agents, improve
    quiet(monkeypatch)
    user_id = me(client, owner)
    a = agent(client, owner)
    now = int(time.time())
    with store.connect() as db:
        for n in range(40):                         # 10 work runs at 0.50, 30 eval questions at 0.02
            kind = "manual" if n < 10 else "eval"
            db.execute("INSERT INTO ade_agent_runs (run_id, agent_id, user_id, state, trigger, sent, queued_at, "
                       "ended_at, cost_est_usd, updated_at) VALUES (?, ?, ?, 'done', ?, 1, ?, ?, ?, ?)",
                       (f"{n:012x}", a["id"], user_id, json.dumps({"kind": kind}), now, now - 60,
                        0.5 if kind == "manual" else 0.02, now))
        assert agents._median_cost(db, a["id"], now) == 0.5 and improve._median_cost(db, a["id"], now) == 0.5


def test_one_run_in_ten_is_sampled_the_same_wherever_it_is_asked():
    from proofpane_open.ade import quality
    ids = [f"{n:012x}" for n in range(2000)]
    share = sum(quality.sampled(i) for i in ids) / len(ids)
    assert 0.08 <= share <= 0.12 and [quality.sampled(i) for i in ids[:50]] == [quality.sampled(i) for i in ids[:50]]


def test_the_judge_is_asked_by_the_background_sync_alone_each_run_once_its_verdict_kept_if_well_formed(
        client, owner, monkeypatch):
    from proofpane_open import store
    from proofpane_open.ade import agents
    from proofpane_open.ade.workspace_client import WorkspaceCallError
    quiet(monkeypatch)
    user_id = me(client, owner)
    a = agent(client, owner, judge_runs=True)
    now = int(time.time())
    good, down, odd = (made(client, owner, a["id"]) for _ in range(3))
    apply(user_id, *(ended(r, a["id"], now) for r in (good, down, odd)))
    for r in (good, down, odd):
        client.post(f"{A}/runs/{r}/thumbs", headers=owner, json={"value": "up"})
    asked = []

    def call(endpoint, method, path, body=None, timeout=None):
        run_id = path.split("/")[3]
        asked.append((method, path))
        if run_id == down:
            raise WorkspaceCallError(502, "the machine went away")
        return verdict(0.8) if run_id == good else {"scores": {"task_fit": 3}}
    monkeypatch.setattr(agents.client, "call", call)
    called = []
    for name in ("_mirror", "_send", "_poll", "_take_sweep"):
        monkeypatch.setattr(agents, name, lambda user, endpoint: None)
    monkeypatch.setattr(agents, "_endpoint", lambda user: "http://workspace")
    real = agents._judge_due
    monkeypatch.setattr(agents, "_judge_due", lambda user, endpoint: called.append(endpoint) or real(user, endpoint))
    assert agents.sync({"id": user_id}) is True and called == []                      # a page's sync: no judge
    assert agents.sync({"id": user_id}, background=True) is True
    agents.settled(user_id, 30)                         # in a thread of the account's own
    assert called == ["http://workspace"]
    assert sorted(asked) == sorted(("POST", f"/agents/runs/{r}/judge") for r in (good, down, odd))
    with store.connect() as db:
        rows = {r["run_id"]: r for r in db.execute("SELECT run_id, judge, judge_asked, judge_error FROM "
                                                   "ade_agent_runs")}
    assert json.loads(rows[good]["judge"])["score"] == 0.8 and rows[good]["judge_error"] is None
    assert rows[down]["judge"] is None and rows[odd]["judge"] is None                 # a failure, a reply out of shape
    assert "the machine went away" in rows[down]["judge_error"]                       # and why, kept on the run
    assert rows[odd]["judge_error"] == "its reply was not a verdict"
    assert all(rows[r]["judge_asked"] == 1 for r in (good, down, odd))
    agents.sync({"id": user_id}, background=True)
    agents.settled(user_id, 30)
    assert len(asked) == 3                                                            # each once


def test_a_slow_judge_holds_no_sync_and_one_round_at_a_time_runs_per_account(client, owner, monkeypatch):
    import threading
    from proofpane_open.ade import agents
    quiet(monkeypatch)
    user_id = me(client, owner)
    for name in ("_mirror", "_send", "_poll", "_take_sweep", "_improve"):
        monkeypatch.setattr(agents, name, lambda user, endpoint: None)
    monkeypatch.setattr(agents, "_endpoint", lambda user: "http://workspace")
    started, release = [], threading.Event()
    monkeypatch.setattr(agents, "_judge_due", lambda user, endpoint: started.append(1) or release.wait(10))
    t0 = time.time()
    assert agents.sync({"id": user_id}, background=True) is True and time.time() - t0 < 2      # it held nothing
    deadline = time.time() + 5
    while not started:
        assert time.time() < deadline
        time.sleep(0.01)
    agents.sync({"id": user_id}, background=True)     # the round still works: no second one beside it
    release.set()
    agents.settled(user_id, 10)
    assert started == [1]


def test_trust_per_judge_version_and_drift_that_drives_nothing_until_its_judge_is_trusted(client, owner, monkeypatch):
    from proofpane_open import store
    from proofpane_open.ade import agents, quality
    quiet(monkeypatch)
    user_id = me(client, owner)
    a = agent(client, owner)
    now = int(time.time())
    runs = []
    for n in range(40):                                  # 20 passing, then 20 of which 15 pass: down 25 points
        passes = n < 20 or n % 4 != 0
        r = made(client, owner, a["id"])
        apply(user_id, ended(r, a["id"], now - (40 - n) * 60))
        with store.connect() as db:
            db.execute("UPDATE ade_agent_runs SET judge = ? WHERE run_id = ?",
                       (quality.clean_judge(verdict(0.9 if passes else 0.3)), r))
        runs.append((r, passes))
    with store.connect() as db:
        d = quality.drift(db, user_id, a["id"])
    assert (d["current"], d["baseline"], d["band"], d["level"]) == (0.75, 1.0, None, None)        # no labels yet
    assert not [k for k, _ in agents.alerts(user_id, now) if k.startswith("agent-drift:")]
    for r, passes in runs[:19]:                          # 19 labels: still nothing said of the judge
        client.post(f"{A}/runs/{r}/thumbs", headers=owner, json={"value": "up" if passes else "down"})
    assert client.get(f"{A}/judge-trust", headers=owner).json()[GOOD]["band"] is None
    with store.connect() as db:                          # a tentative judge is shown and drives nothing; a trusted one does
        assert quality.drift(db, user_id, a["id"], trusts={GOOD: {"band": "tentative"}})["level"] is None
        assert quality.drift(db, user_id, a["id"], trusts={GOOD: {"band": "trusted"}})["level"] == "critical"
    for r, passes in runs[19:]:
        client.post(f"{A}/runs/{r}/thumbs", headers=owner, json={"value": "up" if passes else "down"})
    got = client.get(f"{A}/judge-trust", headers=owner).json()[GOOD]
    assert got["labelled"] == 40 and got["agreement"] == 1.0 and got["band"] == "trusted"
    drifts = [(k, t) for k, t in agents.alerts(user_id, now) if k.startswith("agent-drift:")]
    assert drifts == [(f"agent-drift:{a['id']}:{GOOD}:critical", "agent-look")]         # in fixed words


def test_a_replay_that_started_on_another_brief_is_not_compared(client, owner, monkeypatch):
    """The brief edited while the replay was queued: it ran another agent than its run did, so it says nothing of
    their agreement."""
    from proofpane_open import store
    from proofpane_open.ade import quality
    quiet(monkeypatch)
    user_id = me(client, owner)
    a = agent(client, owner)
    now = int(time.time())
    plain = made(client, owner, a["id"])
    apply(user_id, ended(plain, a["id"], now, metrics={"replayable": True}))
    judged(plain, verdict(0.8))
    again = client.post(f"{A}/runs/{plain}/replay", headers=owner).json()["run_id"]
    assert client.put(f"{A}/{a['id']}", headers=owner, json={"brief": "Other words."}).status_code == 200
    apply(user_id, ended(again, a["id"], now + 5, brief_version=2))           # it started on the new brief
    judged(again, verdict(0.2))
    with store.connect() as db:
        assert quality.agreement(db, user_id, a["id"]) == {"replays": 1, "compared": 0, "agreement": None}


def test_a_replay_only_of_a_replayable_run_and_its_agreement(client, owner, monkeypatch):
    from proofpane_open import store
    from proofpane_open.ade import agents, quality
    quiet(monkeypatch)
    user_id = me(client, owner)
    a = agent(client, owner)
    now = int(time.time())
    plain, reached = made(client, owner, a["id"]), made(client, owner, a["id"])
    apply(user_id, ended(plain, a["id"], now, metrics={"replayable": True}),
          ended(reached, a["id"], now, metrics={"replayable": False}))
    judged(plain, verdict(0.8))
    assert client.post(f"{A}/runs/{reached}/replay", headers=owner).status_code == 409
    with store.connect() as db:                          # a run of an older brief is not replayed: it would measure the new
        db.execute("UPDATE ade_agent_runs SET brief_version = 0 WHERE run_id = ?", (plain,))
    assert client.post(f"{A}/runs/{plain}/replay", headers=owner).status_code == 409
    with store.connect() as db:
        db.execute("UPDATE ade_agent_runs SET brief_version = 1 WHERE run_id = ?", (plain,))
    r = client.post(f"{A}/runs/{plain}/replay", headers=owner)
    assert r.status_code == 200, r.text
    again = r.json()
    assert again["replay_of"] == plain and again["trigger"] == {"kind": "replay", "of": plain, "at": again["queued_at"]}
    with store.connect() as db:                          # as mirrored: its runs may be sent
        db.execute("UPDATE ade_agents SET mirrored_at = updated_at + 1 WHERE id = ?", (a["id"],))
    sent = []
    monkeypatch.setattr(agents.client, "call", lambda endpoint, method, path, body=None, timeout=None:
                        sent.append(body) or {"run_id": body["run_id"], "agent_id": a["id"], "state": "queued"})
    agents._send({"id": user_id}, "http://workspace")
    assert {"kind": "replay", "prompt": None, "id": None, "of": plain, "replay": {"memory": {}}} in \
        [b["trigger"] for b in sent]                    # with the memory it started with: none, for this agent
    apply(user_id, ended(again["run_id"], a["id"], now + 5, replay_of="0" * 12))       # what it replays is the app's
    judged(again["run_id"], verdict(0.75))
    with store.connect() as db:
        assert quality.agreement(db, user_id, a["id"]) == {"replays": 1, "compared": 1, "agreement": 1.0}
        db.execute("UPDATE ade_agent_runs SET judge = ? WHERE run_id = ?",
                   (quality.clean_judge(verdict(0.7)), again["run_id"]))
        assert quality.agreement(db, user_id, a["id"])["agreement"] == 1.0           # 0.1 apart: within
        db.execute("UPDATE ade_agent_runs SET judge = ? WHERE run_id = ?",
                   (quality.clean_judge(verdict(0.5)), again["run_id"]))
        assert quality.agreement(db, user_id, a["id"])["agreement"] == 0.0           # 0.3 apart
        db.execute("UPDATE ade_agent_runs SET judge = ?, acceptance = ? WHERE run_id = ?",
                   (quality.clean_judge(verdict(0.8)), quality.clean_acceptance({"pass": False, "failures": ["no key a"]}),
                    again["run_id"]))
        assert quality.agreement(db, user_id, a["id"])["agreement"] == 0.0           # the same grade, a check apart
        db.execute("UPDATE ade_agent_runs SET judge = NULL WHERE run_id = ?", (again["run_id"],))
        assert quality.agreement(db, user_id, a["id"]) == {"replays": 1, "compared": 1, "agreement": 0.0}   # unjudged


def test_a_run_s_view_gives_no_verdict_and_names_no_replay_and_a_rewrite_is_told(client, owner, monkeypatch):
    from proofpane_open import store
    from proofpane_open.ade import agents
    quiet(monkeypatch)
    user_id = me(client, owner)
    a = agent(client, owner)
    now = int(time.time())
    plain, other = made(client, owner, a["id"]), made(client, owner, a["id"])
    apply(user_id, ended(plain, a["id"], now, judge=verdict(1.0), replay_of=other, metrics={"replayable": True}))
    with store.connect() as db:
        row = db.execute("SELECT judge, replay_of, rewritten FROM ade_agent_runs WHERE run_id = ?", (plain,)).fetchone()
    assert (row["judge"], row["replay_of"], row["rewritten"]) == (None, None, None)     # the app's own, never a view's
    # a record the workspace put back: told, as many times as it was
    apply(user_id, ended(plain, a["id"], now, rewritten=2, metrics={"replayable": True}))
    shown = shown_run(user_id, plain)
    assert shown["rewritten"] == ("Its record was rewritten by something other than the workspace (2 times), and put "
                                  "back as the workspace wrote it.")
    assert shown["metrics"]["replayable"] is True
    # one it had no copy of, of a run the store has ended: told, and the run kept as it ended (the store's own copy is
    # how it stands); of a run the store has working, how it stands, and nothing else it says
    judged(other, verdict(0.8))
    apply(user_id, ended(other, a["id"], now, metrics={"replayable": True}, cost_est_usd=0.5))
    apply(user_id, ended(other, a["id"], now + 1, unverified=True, metrics={"replayable": False}, cost_est_usd=9.0,
                         facts=[{"id": "f" * 12, "subject": "x", "relation": "y", "object": "z", "valid_at": now,
                                 "recorded_at": now, "basis": "observed"}], reason="its record was rewritten"))
    shown = shown_run(user_id, other)
    assert (shown["metrics"]["replayable"], shown["cost_est_usd"], shown["judge"]["score"]) == (True, 0.5, 0.8)
    assert shown["ended_at"] == now and shown["reason"] is None
    live = made(client, owner, a["id"])
    apply(user_id, dict(ended(live, a["id"], now), state="working", ended_at=None, metrics={"replayable": True}))
    apply(user_id, ended(live, a["id"], now + 1, state="failed", unverified=True, metrics={"replayable": False},
                         cost_est_usd=9.0, reason="its record was rewritten"))
    shown_live = shown_run(user_id, live)
    assert (shown_live["state"], shown_live["ended_at"], shown_live["reason"]) == ("failed", now + 1,
                                                                                   "its record was rewritten")
    assert shown_live["metrics"]["replayable"] is True and shown_live["cost_est_usd"] != 9.0
    assert shown["rewritten"].startswith("Its record was rewritten by something other than the workspace, which had")
    assert client.get(f"{A}/{a['id']}/facts", headers=owner).json()["current"] == []
    told = dict(agents.alerts(user_id))
    assert told[f"agent-rewritten:{plain}"] == "agent-look"       # told in fixed words; the page says what was rewritten
    assert f"agent-rewritten:{other}" in told


def test_a_firing_six_minutes_after_its_time_is_late(client, owner, monkeypatch):
    from proofpane_open import store
    from proofpane_open.ade import triggers
    quiet(monkeypatch)
    a = agent(client, owner)
    t = client.post(f"{A}/{a['id']}/triggers", headers=owner, json={"kind": "schedule", "cron": "0 9 * * *",
                                                                      "tz": "UTC"}).json()
    base = 1_790_000_000
    with store.connect() as db:
        db.execute("UPDATE ade_agent_triggers SET next_at = ? WHERE id = ?", (base, t["id"]))
    [(_, first, _)] = triggers.tick(base + 6 * 60)
    with store.connect() as db:                          # it ran; while it waited to start, the next would be held
        db.execute("UPDATE ade_agent_runs SET state = 'done' WHERE run_id = ?", (first,))
        db.execute("UPDATE ade_agent_triggers SET next_at = ? WHERE id = ?", (base + DAY, t["id"]))
    [(_, second, why)] = triggers.tick(base + DAY + 60)
    assert first and second, why
    shown = client.get(f"{A}/{a['id']}/triggers", headers=owner).json()["triggers"][0]
    assert (shown["on_time"], shown["late"], shown["missed"], shown["skipped"]) == (1, 1, 0, 0)


def test_the_window_s_quality_numbers(client, owner, monkeypatch):
    from proofpane_open.ade import agents
    quiet(monkeypatch)
    user_id = me(client, owner)
    a, child_agent = agent(client, owner), agent(client, owner, name="Labeler")
    now = int(time.time())
    runs = [made(client, owner, a["id"]) for _ in range(3)]
    apply(user_id,
          ended(runs[0], a["id"], now, acceptance={"pass": True, "failures": []},
                anomalies=[{"rule": "spend", "level": "warning", "at": now, "evidence": "x"}],
                metrics={"claims": {"flagged": True}, "autonomy": {"unattended": True, "interventions": 0},
                         "mcp_health": {"needs_auth": ["gh"], "failed": [], "disabled": [], "tool_changes": 1},
                         "dispatch": {"fan_out": 1, "waited_s": 12.5}}, dispatches=1),
          ended(runs[1], a["id"], now, acceptance={"pass": False, "failures": ["no key a"]},
                metrics={"premise": {"flagged": True}, "autonomy": {"unattended": False, "interventions": 2},
                         "mcp_health": {"needs_auth": [], "failed": ["x"], "disabled": []}}),
          ended(runs[2], a["id"], now, cost_est_usd=0.3))
    judged(runs[0], verdict(0.9, cost=0.02))
    apply(user_id, {"run_id": "c" * 12, "agent_id": child_agent["id"], "state": "done", "queued_at": now,
                    "ended_at": now, "trigger": {"kind": "dispatch", "parent_run_id": runs[0], "depth": 1}})
    from proofpane_open import store
    with store.connect() as db:
        db.execute("UPDATE ade_agent_runs SET trigger = ? WHERE run_id = ?", (json.dumps({"kind": "eval"}), runs[2]))
    q = client.get(f"{A}/{a['id']}/metrics?days=7", headers=owner).json()["quality"]
    assert (q["judged"], q["judge_pass_rate"]) == (1, 1.0)
    assert q["acceptance"] == {"checked": 2, "passed": 1, "pass_rate": 0.5}
    assert q["anomalies"] == {"warning": 1, "critical": 0, "by_rule": {"spend": 1}}
    assert (q["claims_flagged"], q["premise_flagged"]) == (1, 1)
    assert q["autonomy"] == {"unattended_share": 0.5, "interventions_per_run": 1.0}
    assert q["mcp_health"] == {"needs_auth": 1, "failed": 1, "disabled": 0, "tool_changes": 1}
    assert q["dispatch"] == {"coordinating_runs": 1, "most_fan_out": 1, "deepest": 1, "waited_s": 12.5}
    assert q["cost_of_improvement"] == {"usd": 0.32, "share": round(0.32 / 0.52, 4)}      # the eval run and a judge
    told = [k for k, _ in agents.alerts(user_id, now) if k.startswith("agent-anomaly:")]
    assert told == [f"agent-anomaly:{runs[0]}:spend:warning"]


def test_a_paused_agent_s_queued_run_waits_unsent_and_a_failure_to_start_is_not_its_own(client, owner, monkeypatch):
    from proofpane_open import store
    from proofpane_open.ade import agents
    quiet(monkeypatch)
    user_id = me(client, owner)
    a = agent(client, owner)
    r = made(client, owner, a["id"])
    client.post(f"{A}/{a['id']}/pause", headers=owner)
    with store.connect() as db:
        db.execute("UPDATE ade_agents SET mirrored_at = updated_at + 1")
    sent = []
    monkeypatch.setattr(agents.client, "call", lambda endpoint, method, path, body=None, timeout=None:
                        sent.append(body) or {"run_id": body["run_id"], "agent_id": a["id"], "state": "queued"})
    agents._send({"id": user_id}, "http://workspace")
    with store.connect() as db:
        kept = db.execute("SELECT state, sent FROM ade_agent_runs WHERE run_id = ?", (r,)).fetchone()
    assert sent == [] and tuple(kept) == ("queued", 0)                  # it waits for the resume
    client.post(f"{A}/{a['id']}/resume", headers=owner)
    with store.connect() as db:
        db.execute("UPDATE ade_agents SET mirrored_at = updated_at + 1")
    agents._send({"id": user_id}, "http://workspace")
    assert [b["run_id"] for b in sent] == [r]
    other = made(client, owner, a["id"])
    apply(user_id, ended(other, a["id"], int(time.time()), state="failed", cause="start"))
    totals = client.get(f"{A}/{a['id']}", headers=owner).json()["totals"]
    assert (totals["failed"], totals["success_rate"]) == (0, None)        # the machine's, not the agent's


def test_a_run_s_proposals_and_its_tree_are_held_to_their_shapes(client, owner, monkeypatch):
    """A workspace's programs could write them: the page renders each field as the text or number it should be."""
    from proofpane_open import store
    from proofpane_open.ade import agents
    quiet(monkeypatch)
    user_id = me(client, owner)
    a = agent(client, owner)
    r = made(client, owner, a["id"])
    apply(user_id, dict(ended(r, a["id"], int(time.time())), proposals=[
        {"name": "notes", "description": {"html": "<b>"}, "version": 7, "ways": [{"kind": "package", "env": "all"}, 3]}]))
    (shown,) = agents.proposals(user_id, a["id"])
    assert shown["entry"] == {"name": "notes", "description": "", "version": "", "repository": None,
                              "ways": [{"kind": "package", "registry": "", "identifier": "", "version": "",
                                        "transport": "", "url": "", "env": [], "headers": []}]}
    with store.connect() as db:                                          # a copy kept before shapes were held
        db.execute("INSERT INTO ade_agent_trees (run_id, user_id, tree, copied_at) VALUES (?, ?, ?, 1)",
                   (r, user_id, json.dumps({"turns": [{"text": ["x"], "tools": [{"name": "Read", "ms": "slow"}]}, 5]})))
    got = client.get(f"{A}/runs/{r}/tree", headers=owner).json()
    assert got["tree"]["turns"] == [{"at": None, "text": "", "requests": 0, "tokens": {}, "more": 0, "tools": [
        {"id": "", "name": "Read", "target": "", "ms": 0, "error": False}]}]
