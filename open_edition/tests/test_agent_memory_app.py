"""An agent's memory versions in the app (memory_versions.py): each snapshot a run left copied as the next version
with what changed, a rollback written to the workspace and kept as a version itself, and a folder made a version
again known as unchanged when a run leaves it so; through a real workspace behind the local provider."""

import os

from test_agents_app import A, agent, owner, local, until  # noqa: F401  (fixtures)


def me(client, h):
    return client.get("/api/auth/me", headers=h).json()["id"]


def run_and_sync(client, h, agent_id, brief):
    client.put(f"{A}/{agent_id}", headers=h, json={"brief": brief})
    r = client.post(f"{A}/{agent_id}/runs", headers=h, json={}).json()["run_id"]
    until(client, h, r, ("done",))
    from proofpane_open import auth
    from proofpane_open.ade import agents
    agents.sync(auth.user(me(client, h)))               # its record, and then its memory, copied
    agents.sync(auth.user(me(client, h)))
    return r


def test_versions_from_runs_a_rollback_and_a_folder_known_as_unchanged(client, owner, env):
    made = agent(client, owner, brief="hello")
    first = run_and_sync(client, owner, made["id"], "memory write MEMORY.md=- [labels](labels.md) & write labels.md=bug")
    run_and_sync(client, owner, made["id"], "memory write labels.md=bug, docs")
    got = client.get(f"{A}/{made['id']}/memory", headers=owner).json()
    assert [(v["version"], v["kind"], v["files"]) for v in got["versions"]] == [(2, "run", 2), (1, "run", 2)]
    assert got["versions"][1]["run_id"] == first
    assert sorted(f["path"] for f in got["now"]["files"]) == ["MEMORY.md", "labels.md"]
    two = client.get(f"{A}/{made['id']}/memory/2", headers=owner).json()
    assert two["contents"]["labels.md"] == "bug, docs"
    assert two["changes"]["added"] == [] and [c["path"] for c in two["changes"]["changed"]] == ["labels.md"]
    assert "-bug\n" in two["changes"]["changed"][0]["diff"] or "-bug" in two["changes"]["changed"][0]["diff"]
    assert "+bug, docs" in two["changes"]["changed"][0]["diff"]
    one = client.get(f"{A}/{made['id']}/memory/1", headers=owner).json()
    assert one["changes"]["added"] == ["MEMORY.md", "labels.md"]
    run_and_sync(client, owner, made["id"], "hello")                              # nothing changed: no version
    assert [v["version"] for v in client.get(f"{A}/{made['id']}/memory", headers=owner).json()["versions"]] == [2, 1]
    back = client.post(f"{A}/{made['id']}/memory/1/rollback", headers=owner)
    assert back.status_code == 200, back.text
    assert back.json()["version"] == 3 and back.json()["versions"][0]["from_version"] == 1
    path = os.path.join(str(env), "workspaces", me(client, owner), "agents", made["id"], "memory", "labels.md")
    assert open(path).read() == "bug"
    run_and_sync(client, owner, made["id"], "memory read labels.md")             # as version 3 left it: no new one
    assert [v["version"] for v in client.get(f"{A}/{made['id']}/memory", headers=owner).json()["versions"]] == [3, 2, 1]
    assert client.get(f"{A}/{made['id']}/memory/9", headers=owner).status_code == 404


def test_a_snapshot_is_held_to_its_shapes(client, owner):
    from proofpane_open.ade import memory_versions
    files, digest = memory_versions.clean({"digest": "a" * 64, "files": {
        "ok.md": "fine", "../out.md": "x", "/abs.md": "x", "big.md": "x" * 70_000, "bad\x07.md": "x", "bin.md": None}})
    assert files == {"ok.md": "fine", "big.md": None, "bin.md": None} and digest == "a" * 64
    assert memory_versions.clean({"digest": "nope", "files": {}}) is None and memory_versions.clean([]) is None


def test_a_version_with_a_file_that_was_not_text_is_not_rolled_back_to(client, owner, monkeypatch):
    from proofpane_open import store
    from proofpane_open.ade import memory_versions
    made = agent(client, owner)
    with store.connect() as db:
        memory_versions.add(db, me(client, owner), made["id"], None, {"digest": "b" * 64, "files": {"x.bin": None}})
    r = client.post(f"{A}/{made['id']}/memory/1/rollback", headers=owner)
    assert r.status_code == 409 and "cannot be rolled back to" in r.json()["detail"]


def test_an_agent_keeps_its_newest_versions_and_none_older_than_ninety_days_but_its_newest(client, owner):
    from proofpane_open import store
    from proofpane_open.ade import agents, memory_versions
    user_id = me(client, owner)
    a = agent(client, owner)
    now = 1_790_000_000
    versions = lambda db: [r[0] for r in db.execute(  # noqa: E731
        "SELECT version FROM ade_agent_memory WHERE agent_id = ? ORDER BY version", (a["id"],))]
    with store.connect() as db:
        for n in range(40):                             # version n + 1, made 120 - n days ago
            memory_versions.add(db, user_id, a["id"], None, {"digest": f"{n:064x}", "files": {"a.md": f"v{n}"}},
                                now=now - (120 - n) * 86400)
        assert versions(db) == list(range(11, 41))                            # the newest 30
        agents.forget(db, now)
        assert versions(db) == list(range(31, 41))                            # none older than 90 days
        db.execute("UPDATE ade_agent_memory SET made_at = ? WHERE agent_id = ?", (now - 200 * 86400, a["id"]))
        agents.forget(db, now)
        assert versions(db) == [40]                                           # but the newest
