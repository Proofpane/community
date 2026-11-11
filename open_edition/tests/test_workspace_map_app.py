"""The control view's map through the app: `GET /api/ade/workspace/map` and `GET /api/ade/workspace/sessions/
{id}/map`, each a call through to the person's own workspace, with a real workspace agent behind it and a stand-in for
its two map routes (session_map_stand_in.py, the map's shapes exactly);
test_workspace_map_served.py runs the same routes against the real agent's own map."""

import pytest

from conftest import ADMIN, signed_up
from session_map_stand_in import MAP, SESSION_MAP, install

OTHER_ADMIN = "second-admin@example.com"
MAP_ROUTE = "/api/ade/workspace/map"


@pytest.fixture
def local(monkeypatch, tmp_path):
    home = tmp_path / "home"     # no git identity of the machine's user in a test
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OPEN_EDITION_ADE", "local")
    monkeypatch.setenv("OPEN_EDITION_ADMINS", f"{ADMIN},{OTHER_ADMIN}")
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    from proofpane_open.ade import workspaces
    yield workspaces
    workspaces.shutdown()


@pytest.fixture
def stand_in(monkeypatch):
    from proofpane_open.ade import workspace_client
    return install(monkeypatch, workspace_client)


def _one(sid):
    return f"/api/ade/workspace/sessions/{sid}/map"


def _started(client, headers, env):
    from workspace_fixtures import make_repo
    r = client.post("/api/ade/workspace/start", headers=headers)
    assert r.status_code == 200 and r.json()["running"] is True, r.text
    me = client.get("/api/auth/me", headers=headers).json()["id"]
    make_repo(str(env / "workspaces" / me / "repos" / "demo"), origin="https://github.com/acme/demo.git")


def _session(client, headers):
    r = client.post("/api/ade/workspace/sessions", headers=headers, json={"repo": "demo", "program": "shell", "task": "t"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_the_map_routes_need_an_account(client, local, stand_in):
    for path in (MAP_ROUTE, _one("0123456789ab")):
        assert client.get(path).status_code == 401, path
    assert stand_in == []


def test_the_map_routes_are_off_with_the_workspace(client, stand_in, monkeypatch):
    monkeypatch.delenv("OPEN_EDITION_ADE", raising=False)
    h = signed_up(client)
    # an id that is no session's too: the workspace's own check comes first, as on the app's other session routes
    for path in (MAP_ROUTE, _one("0123456789ab"), _one("aaaa")):
        r = client.get(path, headers=h)
        assert r.status_code == 404 and "off" in r.json()["detail"], (path, r.text)
    assert stand_in == []


def test_a_workspace_that_is_not_running_is_said_so_and_never_asked(client, local, stand_in):
    h = signed_up(client)
    for path in (MAP_ROUTE, _one("0123456789ab")):
        r = client.get(path, headers=h)
        assert r.status_code == 409 and r.json()["detail"] == "The workspace is not running: start it first.", r.text
    assert stand_in == []


def test_the_map_is_the_owners_own_workspace_s_and_another_persons_session_is_not_found(client, local, env, stand_in):
    a = signed_up(client)
    b = signed_up(client, OTHER_ADMIN)
    _started(client, a, env)
    _started(client, b, env)
    mine = _session(client, a)
    # the owner's map: the workspace's answer as it gave it, in the map's shape, with the owner's session in it
    r = client.get(MAP_ROUTE, headers=a)
    assert r.status_code == 200, r.text
    assert r.json() == dict(MAP, sessions=[dict(MAP["sessions"][0], id=mine)])
    r = client.get(_one(mine), headers=a)
    assert r.status_code == 200 and r.json() == dict(SESSION_MAP, session=mine), r.text
    # the other person's: their own workspace's, which has none of the owner's sessions
    r = client.get(MAP_ROUTE, headers=b)
    assert r.status_code == 200 and r.json()["sessions"] == [], r.text
    r = client.get(_one(mine), headers=b)
    assert r.status_code == 404 and r.json()["detail"] == "No such session.", r.text
    # each asked at its own workspace's address: the owner's twice, then the other's twice
    maps = [(at, method, path) for at, method, path in stand_in if path.endswith("/map")]
    assert [(method, path) for _, method, path in maps] == [("GET", "/map"), ("GET", f"/sessions/{mine}/map")] * 2
    first, second = {at for at, _, _ in maps[:2]}, {at for at, _, _ in maps[2:]}
    assert len(first) == 1 and len(second) == 1 and first != second


def test_a_token_budget_and_the_agents_left_out_reach_the_page_as_the_workspace_gives_them(client, local, env, stand_in):
    # the stand-in's second session is test_workspace_map_served.py's case: a session's effort and its agent's each a
    # token budget (a number), and agents left out past those given (agents_more); the app passes each through as it is
    h = signed_up(client)
    _started(client, h, env)
    made = {_session(client, h), _session(client, h)}
    r = client.get(MAP_ROUTE, headers=h)
    assert r.status_code == 200, r.text
    rows = r.json()["sessions"]
    assert {row["id"] for row in rows} == made
    assert rows == [dict(MAP["sessions"][n], id=row["id"]) for n, row in enumerate(rows)]
    budget = rows[1]
    assert budget["effort"] == 31999 and budget["agents"][0]["effort"] == 16000 and budget["agents_more"] == 3
    assert rows[0]["effort"] == "max" and rows[0]["agents_more"] == 0


# the last: an id with a newline after it, which a pattern ending in `$` would take
@pytest.mark.parametrize("sid", ["aaaa", "AAAAAAAAAAAA", "aaaaaaaaaaaa%2F..%2Frepos", "..%2Frepos", "0123456789abc",
                                 "0123456789ab%0A"])
def test_a_session_id_is_checked_before_the_workspace_is_asked(client, local, env, stand_in, sid):
    h = signed_up(client)
    _started(client, h, env)
    r = client.get(_one(sid), headers=h)
    assert r.status_code == 404, (sid, r.text)
    assert stand_in == []                       # the workspace was asked nothing, under any path
    # a well-formed one is asked, and the workspace's own 404 comes back for one it has not
    r = client.get(_one("0123456789ab"), headers=h)
    assert r.status_code == 404 and r.json()["detail"] == "No such session.", r.text
    assert [(method, path) for _, method, path in stand_in] == [("GET", "/sessions/0123456789ab/map")]


def test_what_the_workspace_says_or_not_reaches_the_page_as_it_is(client, local, env, monkeypatch):
    from proofpane_open.ade import workspace_client
    h = signed_up(client)
    _started(client, h, env)
    real = workspace_client.call
    said = {}

    def call(endpoint, method, path, body=None, timeout=30):
        if path.endswith("/map"):
            raise said["error"]
        return real(endpoint, method, path, body, timeout=timeout)

    monkeypatch.setattr(workspace_client, "call", call)
    for error, status, detail in ((workspace_client.WorkspaceCallError(409, "Not now."), 409, "Not now."),
                                  (workspace_client.NotAnswering(502, workspace_client.NOT_ANSWERING), 502,
                                   workspace_client.NOT_ANSWERING)):
        said["error"] = error
        for path in (MAP_ROUTE, _one("0123456789ab")):
            r = client.get(path, headers=h)
            assert (r.status_code, r.json()["detail"]) == (status, detail), (path, r.text)


def test_a_workspace_from_before_the_map_is_said_to_be_one_in_words(client, local, env, monkeypatch):
    # its agent has neither route, and answers each in its framework's plain words, a 404 with no reason the client
    # can read (workspace_client._refused, run here as it is): the page is told the workspace has no map yet, where it
    # would otherwise be told only "The workspace answered 404."
    import io
    import urllib.error
    from proofpane_open.ade import workspace_client, workspace_routes
    h = signed_up(client)
    _started(client, h, env)
    real = workspace_client.call
    answer = {}

    def call(endpoint, method, path, body=None, timeout=30):
        if path.endswith("/map"):
            status, kind, text = answer["now"]
            raise workspace_client._refused(urllib.error.HTTPError(endpoint.http + path, status, "", {"Content-Type": kind},
                                                                   io.BytesIO(text)))
        return real(endpoint, method, path, body, timeout=timeout)

    monkeypatch.setattr(workspace_client, "call", call)
    for now, status, detail in (
            ((404, "text/plain; charset=utf-8", b"Not Found"), 404, workspace_routes.MAP_NOT_YET),
            # the agent's own refusal keeps its words: a session it does not have
            ((404, "application/json", b'{"detail": "No such session."}'), 404, "No such session."),
            # any other answer with no reason is still only that it answered so
            ((500, "text/plain; charset=utf-8", b"Internal Server Error"), 502, "The workspace answered 500.")):
        answer["now"] = now
        for path in (MAP_ROUTE, _one("0123456789ab")):
            r = client.get(path, headers=h)
            assert (r.status_code, r.json()["detail"]) == (status, detail), (now, path, r.text)
