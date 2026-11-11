"""An agent's schedules and hooks (triggers.py, cron.py): a schedule fired by the app's scheduler, or missed past its
grace; a hook that starts nothing without its signature; and every firing held back, with why, when the agent is
paused, its day's runs are spent, the plan's window is exhausted, a run of it waits already, or the user's machine time
has reached its share."""

import base64
import datetime
import hashlib
import hmac
import json
import time

import pytest

from conftest import signed_up
from proofpane_open import store
from proofpane_open.ade import agents, cron, push, triggers

A = "/api/ade/agents"


@pytest.fixture
def owner(client, monkeypatch, tmp_path):
    monkeypatch.setenv("OPEN_EDITION_ADE", "local")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    sent = []
    # a start asked for (a run's); a mirror alone asks for none
    monkeypatch.setattr(agents, "_deliver_soon", lambda user, start=False: (sent.append(start) if start else None) or False)
    h = signed_up(client)
    yield h, sent


def make(client, h, **over):
    r = client.post(A, headers=h, json=dict({"name": "Triage", "brief": "hello", "repo": "demo"}, **over))
    assert r.status_code == 200, r.text
    return r.json()


def trigger(client, h, agent_id, **given):
    r = client.post(f"{A}/{agent_id}/triggers", headers=h, json=given)
    assert r.status_code == 200, r.text
    return r.json()


def runs_of(agent_id):
    with store.connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM ade_agent_runs WHERE agent_id = ? ORDER BY queued_at, run_id",
                                            (agent_id,))]


def test_a_schedule_fires_at_its_time_and_its_next_is_set(client, owner):
    h, sent = owner
    made = make(client, h)
    t = trigger(client, h, made["id"], kind="schedule", cron="*/5 * * * *", tz="UTC", prompt="the new issues")
    assert t["next_at"] % 300 == 0 and t["next_at"] > time.time() - 1
    fired = triggers.tick(now=t["next_at"])
    ((tid, run_id, why),) = fired
    assert (tid, why) == (t["id"], None) and sent == [True]                 # the machine started for it
    (run,) = runs_of(made["id"])
    assert run["run_id"] == run_id and json.loads(run["trigger"]) == {"kind": "schedule", "prompt": "the new issues",
                                                                      "at": t["next_at"], "id": t["id"],
                                                                      "due": t["next_at"]}
    got = client.get(f"{A}/{made['id']}", headers=h).json()["triggers"][0]
    assert got["next_at"] == t["next_at"] + 300 and got["last_at"] == t["next_at"] and got["last_run_id"] == run_id
    assert triggers.tick(now=t["next_at"] + 10) == []                        # nothing due again until its next


def test_a_firing_while_a_run_of_it_waits_to_start_is_not_queued_again(client, owner):
    h, _ = owner
    made = make(client, h)
    t = trigger(client, h, made["id"], kind="schedule", cron="* * * * *", tz="UTC")
    triggers.tick(now=t["next_at"])
    ((_, run_id, why),) = triggers.tick(now=t["next_at"] + 60)
    assert run_id is None and why == "a run of it waits to start already" and len(runs_of(made["id"])) == 1


def test_a_firing_later_than_its_grace_is_missed_not_run(client, owner):
    h, _ = owner
    made = make(client, h)
    t = trigger(client, h, made["id"], kind="schedule", cron="0 * * * *", tz="UTC", grace_minutes=30)
    ((_, run_id, why),) = triggers.tick(now=t["next_at"] + 31 * 60)
    assert (run_id, why) == (None, "missed") and runs_of(made["id"]) == []
    got = client.get(f"{A}/{made['id']}", headers=h).json()["triggers"][0]
    assert got["missed"] == 1 and got["last_error"].startswith("missed the firing of ")
    assert got["next_at"] == t["next_at"] + 3600
    keys = dict(push.items_for(client.get("/api/auth/me", headers=h).json()["id"], [], int(time.time())))
    (told,) = [m for k, m in keys.items() if k.startswith(f"agent-trigger:{t['id']}:")]
    # a schedule's, in fixed words: never the agent's name or why, which the panel shows
    assert (told.title, told.body) == ("An agent's schedule did not start a run", "Open Agents.")


def test_a_firing_is_held_with_why(client, owner, monkeypatch):
    h, _ = owner
    made = make(client, h, limits={"max_runs_per_day": 1})
    user_id = client.get("/api/auth/me", headers=h).json()["id"]
    t = trigger(client, h, made["id"], kind="schedule", cron="* * * * *", tz="UTC")
    now = t["next_at"]
    client.post(f"{A}/{made['id']}/pause", headers=h)
    assert triggers.tick(now=now)[0][2] == "the agent is paused"
    client.post(f"{A}/{made['id']}/resume", headers=h)
    assert triggers.tick(now=now + 60)[0][1] is not None                    # one run: the day's one
    with store.connect() as db:
        db.execute("UPDATE ade_agent_runs SET state = 'done' WHERE agent_id = ?", (made["id"],))
    assert triggers.tick(now=now + 120)[0][2].startswith("its day's runs are spent (1 of 1)")
    client.put(f"{A}/{made['id']}", headers=h, json={"limits": {"max_runs_per_day": 10}})
    with store.connect() as db:
        db.execute("UPDATE ade_agent_runs SET cause = 'rate_limit', resets_at = ? WHERE agent_id = ?",
                   (now + 3600, made["id"]))
    assert triggers.tick(now=now + 180)[0][2].startswith("held until the plan's window resets at ")
    with store.connect() as db:
        db.execute("UPDATE ade_agent_runs SET resets_at = NULL")
    from proofpane_open.ade import workspaces
    monkeypatch.setattr(workspaces.LocalProvider, "month_hours", lambda self, user, now=None: 200.0)
    assert triggers.tick(now=now + 240)[0][2].startswith("this month's machine time, 200 hours, has reached")
    monkeypatch.setattr(workspaces.LocalProvider, "month_hours", lambda self, user, now=None: None)
    assert triggers.tick(now=now + 300)[0][1] is not None


def _github(secret, body):
    return {"X-Hub-Signature-256": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(),
            "X-GitHub-Event": "issues"}


def test_a_hook_starts_a_run_only_with_its_signature(client, owner):
    h, sent = owner
    made = make(client, h)
    t = trigger(client, h, made["id"], kind="hook", hook_type="github", prompt="label it")
    secret = t["secret"]
    assert t["url"].endswith(f"/api/ade/hooks/{t['id']}")
    assert "secret" not in client.get(f"{A}/{made['id']}", headers=h).json()["triggers"][0]     # shown once
    body = json.dumps({"action": "opened", "issue": {"number": 7}}).encode()
    wrong = client.post(f"/api/ade/hooks/{t['id']}", content=body, headers=_github("not-it", body))
    assert wrong.status_code == 401 and runs_of(made["id"]) == [] and sent == []
    assert client.get(f"{A}/{made['id']}", headers=h).json()["triggers"][0]["refused"] == 1
    ok = client.post(f"/api/ade/hooks/{t['id']}", content=body, headers=_github(secret, body))
    assert ok.status_code == 202 and sent == [True]
    with store.connect() as db:
        kept = db.execute("SELECT payload FROM ade_agent_payloads WHERE run_id = ?", (ok.json()["run_id"],)).fetchone()
    assert bytes(kept["payload"]) == body
    ping = client.post(f"/api/ade/hooks/{t['id']}", content=b"{}", headers=dict(_github(secret, b"{}"),
                                                                                  **{"X-GitHub-Event": "ping"}))
    assert ping.status_code == 200 and len(runs_of(made["id"])) == 1
    big = b"x" * (triggers.MAX_DELIVERY + 1)
    assert client.post(f"/api/ade/hooks/{t['id']}", content=big, headers=_github(secret, big)).status_code == 413
    # the app's own body limit answers first today; the hook's holds whatever that limit becomes
    headers = {k.lower(): v for k, v in _github(secret, big).items()}
    assert triggers.deliver(t["id"], headers, big)[0] == 413
    assert "refused: at most" in client.get(f"{A}/{made['id']}", headers=h).json()["triggers"][0]["last_error"]
    assert client.post("/api/ade/hooks/aaaaaaaaaaaa", content=body, headers=_github(secret, body)).status_code == 404


def test_a_bearer_hook_and_its_rate(client, owner, monkeypatch):
    h, _ = owner
    made = make(client, h, limits={"queue_max": 100, "max_runs_per_day": 1000})
    t = trigger(client, h, made["id"], kind="hook", hook_type="bearer")
    with store.connect() as db:
        stored = db.execute("SELECT secret FROM ade_agent_triggers WHERE id = ?", (t["id"],)).fetchone()["secret"]
    assert stored == hashlib.sha256(t["secret"].encode()).hexdigest()       # only its digest kept
    assert client.post(f"/api/ade/hooks/{t['id']}", content=b"{}",
                       headers={"Authorization": "Bearer nope"}).status_code == 401
    monkeypatch.setattr(triggers, "HOOK_MOST", 3)
    codes = [client.post(f"/api/ade/hooks/{t['id']}", content=b"{}",
                         headers={"Authorization": f"Bearer {t['secret']}"}).status_code for _ in range(4)]
    assert codes == [202, 202, 202, 429]


def test_a_hook_s_delivery_goes_with_its_run_and_is_then_dropped(client, owner, monkeypatch):
    h, _ = owner
    made = make(client, h)
    user = {"id": client.get("/api/auth/me", headers=h).json()["id"], "email": "a@example.org"}
    t = trigger(client, h, made["id"], kind="hook", hook_type="bearer")
    body = b'{"issue": {"body": "\\u0000 anything"}}'
    run_id = client.post(f"/api/ade/hooks/{t['id']}", content=body,
                         headers={"Authorization": f"Bearer {t['secret']}"}).json()["run_id"]
    with store.connect() as db:
        db.execute("UPDATE ade_agents SET mirrored_at = updated_at + 1")
    calls = []

    def call(endpoint, method, path, body=None, timeout=30):
        calls.append((method, path, body))
        return {"run_id": run_id, "agent_id": made["id"], "state": "queued"}
    monkeypatch.setattr(agents.client, "call", call)
    agents._send(user, "endpoint")
    ((_, path, sent),) = calls
    assert path == f"/agents/{made['id']}/runs" and base64.b64decode(sent["trigger"]["payload_b64"]) == body
    assert sent["trigger"]["kind"] == "hook" and sent["trigger"]["id"] == t["id"]
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_agent_payloads").fetchone()[0] == 0


def test_a_schedule_is_a_cron_line_in_a_timezone(client, owner):
    h, _ = owner
    made = make(client, h)
    for bad in ({"kind": "schedule", "cron": "* * * *", "tz": "UTC"}, {"kind": "schedule", "cron": "* * * * *",
                                                                         "tz": "Mars/Olympus"},
                {"kind": "hook", "hook_type": "basic"}, {"kind": "mail"}):
        assert client.post(f"{A}/{made['id']}/triggers", headers=h, json=bad).status_code == 400, bad
    t = trigger(client, h, made["id"], kind="schedule", cron="0 9 * * 1-5", tz="Pacific/Auckland")
    when = datetime.datetime.fromtimestamp(t["next_at"], cron.Schedule("* * * * *", "Pacific/Auckland").zone)
    assert (when.hour, when.minute) == (9, 0) and when.weekday() < 5
    moved = client.put(f"{A}/{made['id']}/triggers/{t['id']}", headers=h, json={"cron": "30 18 * * *"}).json()
    when = datetime.datetime.fromtimestamp(moved["next_at"], cron.Schedule("* * * * *", "Pacific/Auckland").zone)
    assert (when.hour, when.minute) == (18, 30)
    assert client.delete(f"{A}/{made['id']}/triggers/{t['id']}", headers=h).status_code == 200
    assert client.get(f"{A}/{made['id']}", headers=h).json()["triggers"] == []
    other = signed_up(client, "second-admin@example.com")
    assert client.post(f"{A}/{made['id']}/triggers", headers=other, json={"kind": "hook", "hook_type": "bearer"}) \
        .status_code in (403, 404)


def test_retiring_an_agent_turns_its_triggers_off_and_its_hook_answers_as_gone(client, owner):
    h, _ = owner
    made = make(client, h)
    hook = trigger(client, h, made["id"], kind="hook", hook_type="bearer")
    trigger(client, h, made["id"], kind="schedule", cron="* * * * *", tz="UTC")
    assert client.post(f"{A}/{made['id']}/retire", headers=h).status_code == 200
    listed = client.get(f"{A}/{made['id']}/triggers", headers=h).json()["triggers"]
    assert [t["enabled"] for t in listed] == [False, False]
    assert client.post(f"/api/ade/hooks/{hook['id']}", content=b"{}",
                       headers={"Authorization": f"Bearer {hook['secret']}"}).status_code == 404
    assert triggers.tick(int(time.time()) + 120) == [] and runs_of(made["id"]) == []


def test_deliveries_at_once_keep_to_the_day_s_runs_and_the_minute_s_window(client, owner, monkeypatch):
    import threading
    h, _ = owner
    made = make(client, h, limits={"queue_max": 100, "max_runs_per_day": 2})
    hook = trigger(client, h, made["id"], kind="hook", hook_type="bearer")
    headers = {"authorization": f"Bearer {hook['secret']}"}
    codes, start = [], threading.Barrier(24)
    held = triggers._held

    def slow(*args):                        # the time between a firing's count and its run, made long: they overlap
        why = held(*args)
        time.sleep(0.05)
        return why
    monkeypatch.setattr(triggers, "_held", slow)

    def one():
        start.wait()
        codes.append(triggers.deliver(hook["id"], headers, b"{}")[0])
    threads = [threading.Thread(target=one) for _ in range(24)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sorted(codes).count(202) == 2 and len(runs_of(made["id"])) == 2      # its day's two, and no more
    monkeypatch.setattr(triggers, "HOOK_MOST", 5)
    with store.connect() as db:
        db.execute("UPDATE ade_agent_triggers SET window_start = 0, window_count = 0 WHERE id = ?", (hook["id"],))
    codes.clear()
    threads = [threading.Thread(target=one) for _ in range(24)]
    start = threading.Barrier(24)
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len([c for c in codes if c != 429]) == 5                              # the minute's five, and no more
