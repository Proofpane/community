"""Linear, for a session's task (proofpane_workspace/linear.py, through the workspace agent): the person's key, kept in
their home and never given back; their open issues, and a search of their workspace's; the gate keeps the key from a
session. Linear is a stand-in here that answers as its GraphQL API does, and checks the key comes as Linear takes it."""

import json
import os
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from proofpane_workspace import gate, linear
from workspace_fixtures import Agent, make_repo

KEY = "lin_api_" + "k" * 40


def _node(n, **over):
    return dict({"identifier": f"ENG-{n}", "title": f"Issue {n}", "description": "It breaks when the list is empty.",
                 "url": f"https://linear.app/acme/issue/ENG-{n}/issue-{n}", "updatedAt": "2026-10-01T10:00:00.000Z",
                 "priorityLabel": "High", "state": {"name": "In Progress", "type": "started"},
                 "team": {"key": "ENG", "name": "Engineering"}, "assignee": {"displayName": "ana"},
                 "labels": {"nodes": [{"name": "bug"}]}}, **over)


class Linear(BaseHTTPRequestHandler):
    asked = []
    nodes = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Linear.asked.append({"auth": self.headers.get("Authorization"), **body})
        if self.headers.get("Authorization") != KEY:
            self._answer(401, {"errors": [{"message": "Authentication required, not authenticated"}]})
        elif "viewer { name email }" in body["query"]:
            self._answer(200, {"data": {"viewer": {"name": "Ana", "email": "ana@example.com"}}})
        elif "assignedIssues" in body["query"]:
            self._answer(200, {"data": {"viewer": {"assignedIssues": {"nodes": Linear.nodes}}}})
        elif "issues(" in body["query"]:
            self._answer(200, {"data": {"issues": {"nodes": Linear.nodes[:1]}}})
        else:
            self._answer(400, {"errors": [{"message": "Unknown query"}]})

    def _answer(self, code, value):
        raw = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def stand_in():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Linear)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    Linear.asked, Linear.nodes = [], []
    yield f"http://127.0.0.1:{server.server_address[1]}/graphql"
    server.shutdown()


@pytest.fixture
def agent(tmp_path, stand_in):
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home), {"PP_LINEAR_API": stand_in})
    make_repo(str(tmp_path / "ws" / "repos" / "demo"))
    a.home = home
    yield a
    a.close()


def test_a_key_is_kept_only_once_linear_takes_it_and_never_given_back(agent):
    assert agent.http.get("/linear").json() == {"set": False}
    r = agent.http.put("/linear/key", json={"key": "short"})
    assert r.status_code == 400 and "8 to 400 printable characters" in r.json()["detail"]
    r = agent.http.put("/linear/key", json={"key": "lin_api_" + "x" * 40})
    assert r.status_code == 400 and "Linear did not take the key" in r.json()["detail"]
    store = agent.home / ".proofpane" / "linear-key.json"
    assert not store.exists()
    r = agent.http.put("/linear/key", json={"key": f"  {KEY}  "})
    assert r.json() == {"set": True, "user": {"name": "Ana", "email": "ana@example.com"}}
    assert json.loads(store.read_text()) == {"key": KEY}
    assert stat.S_IMODE(store.stat().st_mode) == 0o600 and stat.S_IMODE(store.parent.stat().st_mode) == 0o700
    assert agent.http.get("/linear").json() == {"set": True, "user": {"name": "Ana", "email": "ana@example.com"}}
    assert all(a["auth"] in (KEY, "lin_api_" + "x" * 40) for a in Linear.asked)     # as it is, no "Bearer"
    assert agent.http.delete("/linear/key").json() == {"set": False}
    assert not store.exists()
    r = agent.http.get("/linear/issues")
    assert r.status_code == 400 and r.json()["detail"] == "Add a Linear API key first, in Connections."


def test_the_person_s_open_issues_are_listed_as_linear_gives_them_and_what_is_odd_is_left_out(agent):
    agent.http.put("/linear/key", json={"key": KEY})
    Linear.nodes = [_node(12), _node(13, url="javascript:alert(1)", description="x" * (linear.DESCRIPTION + 5),
                                     labels=None, assignee=None, state=None),
                    _node(14, identifier="not an id"), {"identifier": "ENG-15"}, "junk"]
    r = agent.http.get("/linear/issues")
    assert r.status_code == 200, r.text
    got = r.json()["issues"]
    assert got[0] == {"identifier": "ENG-12", "title": "Issue 12", "description": "It breaks when the list is empty.",
                      "cut": False, "url": "https://linear.app/acme/issue/ENG-12/issue-12", "state": "In Progress",
                      "state_type": "started", "team": "Engineering", "team_key": "ENG", "assignee": "ana",
                      "priority": "High", "updated_at": "2026-10-01T10:00:00.000Z", "labels": ["bug"]}
    assert (got[1]["url"], got[1]["cut"], len(got[1]["description"]), got[1]["labels"], got[1]["state"]) == \
        (None, True, linear.DESCRIPTION, [], None)
    assert len(got) == 2
    mine = Linear.asked[-1]
    assert "assignedIssues" in mine["query"] and mine["variables"] == {"first": linear.MAX_ISSUES}


def test_a_search_is_of_the_open_issues_text_or_the_one_its_identifier_names(agent):
    agent.http.put("/linear/key", json={"key": KEY})
    Linear.nodes = [_node(12)]
    assert agent.http.get("/linear/issues", params={"search": "empty list", "limit": "5"}).json()["issues"][0][
        "identifier"] == "ENG-12"
    asked = Linear.asked[-1]
    assert asked["variables"] == {"first": 5, "filter": {
        "state": {"type": {"nin": ["completed", "canceled"]}},
        "or": [{"title": {"containsIgnoreCase": "empty list"}}, {"description": {"containsIgnoreCase": "empty list"}}]}}
    agent.http.get("/linear/issues", params={"search": "eng-12"})
    assert Linear.asked[-1]["variables"]["filter"] == {"team": {"key": {"eq": "ENG"}}, "number": {"eq": 12}}
    assert agent.http.get("/linear/issues", params={"search": "x" * 201}).status_code == 400
    assert agent.http.get("/linear/issues", params={"limit": "many"}).status_code == 400


def test_what_linear_says_when_it_refuses_is_said_in_words(agent, monkeypatch, tmp_path):
    # a key kept, then turned down by Linear (revoked, say): its status says so, and no listing goes on
    store = agent.home / ".proofpane"
    store.mkdir(mode=0o700)
    (store / "linear-key.json").write_text(json.dumps({"key": "lin_api_" + "z" * 40}))
    st = agent.http.get("/linear").json()
    assert st["set"] is True and st["user"] is None and "Linear did not take the key" in st["error"]
    r = agent.http.get("/linear/issues")
    assert r.status_code == 400 and "Linear did not take the key" in r.json()["detail"]


def test_the_gate_keeps_the_key_from_a_session(tmp_path):
    home = str(tmp_path)
    for tool_input in ({"command": "cat ~/.proofpane/linear-key.json"},
                       {"file_path": f"{home}/.proofpane/linear-key.json"}):
        why = gate.refusal({"tool_name": "Bash" if "command" in tool_input else "Read", "tool_input": tool_input}, home)
        assert why == gate.SIGN_IN, tool_input


def test_linear_unreachable_is_said(monkeypatch, tmp_path):
    monkeypatch.setenv("PP_LINEAR_API", "http://127.0.0.1:9/graphql")
    with pytest.raises(linear.LinearError, match="Linear could not be reached"):
        linear.save(str(tmp_path), KEY)
    assert not os.path.exists(linear.store_path(str(tmp_path)))


def test_a_key_with_a_line_break_kept_after_it_is_not_taken(tmp_path):
    store = tmp_path / ".proofpane"
    store.mkdir()
    (store / "linear-key.json").write_text(json.dumps({"key": KEY + "\n"}))
    assert linear.key(str(tmp_path)) is None and linear.status(str(tmp_path)) == {"set": False}


class Redirecting(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        Redirecting.seen.append((self.path, self.headers.get("Authorization")))
        if self.path == "/graphql":
            self.send_response(307)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_address[1]}/elsewhere")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_response(200)
            self.end_headers()


def test_a_redirect_is_not_followed_so_the_key_goes_nowhere_else(tmp_path, monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Redirecting)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    Redirecting.seen = []
    try:
        monkeypatch.setenv("PP_LINEAR_API", f"http://127.0.0.1:{server.server_address[1]}/graphql")
        with pytest.raises(linear.LinearError, match="redirect, which is not followed"):
            linear.save(str(tmp_path), KEY)
    finally:
        server.shutdown()
    assert [p for p, _ in Redirecting.seen] == ["/graphql"]          # the key went to Linear's address only


def test_an_error_s_own_text_never_carries_the_key(tmp_path, monkeypatch):
    import http.client

    def broken(*a, **k):
        raise http.client.InvalidURL("Invalid header value b'" + KEY + "'")
    monkeypatch.setattr(linear._OPENER, "open", broken)
    with pytest.raises(linear.LinearError) as got:
        linear.save(str(tmp_path), KEY)
    assert KEY not in str(got.value) and "could not be reached (InvalidURL)" in str(got.value)
