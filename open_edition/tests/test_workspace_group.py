"""A session joins a group, to be compared with sessions started later on its task ("Try with another agent"): a
session with no group takes the one named; one already compared with others keeps its own; a group is a label only."""

from test_workspace_agent import _session, agent  # noqa: F401  (agent is a fixture)


def test_a_session_with_no_group_joins_the_one_named(agent):  # noqa: F811
    s = _session(agent, task="Fix the totals")
    assert s.get("group") is None
    r = agent.http.post(f"/sessions/{s['id']}/group", json={"group": "abcd1234efgh"})
    assert r.status_code == 200, r.text
    assert r.json()["group"] == "abcd1234efgh"
    assert agent.http.get(f"/sessions/{s['id']}").json()["group"] == "abcd1234efgh"
    # the same group again changes nothing
    assert agent.http.post(f"/sessions/{s['id']}/group", json={"group": "abcd1234efgh"}).status_code == 200


def test_one_already_compared_with_others_keeps_its_group(agent):  # noqa: F811
    s = _session(agent, task="Fix the totals", group="first0001")
    r = agent.http.post(f"/sessions/{s['id']}/group", json={"group": "second001"})
    assert r.status_code == 400 and "already" in r.json()["detail"]
    assert agent.http.get(f"/sessions/{s['id']}").json()["group"] == "first0001"


def test_a_group_is_a_label_of_its_own_form(agent):  # noqa: F811
    s = _session(agent, task="Fix the totals")
    for bad in ("short", "UPPER12345", "a" * 33, "with-dash12", 12345678, None):
        r = agent.http.post(f"/sessions/{s['id']}/group", json={"group": bad})
        assert r.status_code == 400, bad
    assert agent.http.post("/sessions/0123456789ab/group", json={"group": "abcd1234efgh"}).status_code == 404
