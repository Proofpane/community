"""The app's patterns that this change closes,
each of which checks a value a request gives (or, `_OBJECT_ID`, the commit the workspace's Finish names), refuse the
value with a line break after it. In Python's re, `$` matches at the end of the text and also just before a final
newline, so `^[0-9a-f]{12}$` let "0123456789ab\\n" through, and the value went on, into the path of a call to the
workspace (which http.client refuses, and the app answered 500) or into an agent's record. Each such pattern now ends
with `\\Z`; main.py's three body-limit patterns are left as they were. Each value is sent
through the route that takes it (Continue's head as the workspace's answer, through a recorder), with "\\n" and with
"\\r\\n" after it (as %0A and %0D%0A in a path, which the app decodes), and the route's own refusal comes back. Each
value here with "\\r\\n" after it was refused before the change too, so those cases guard that it still is (Continue's
by the Finish's own check, before `_OBJECT_ID`, as its case says); the "\\n" cases fail on the code before the change,
all but those of an agent's id and a run's id, which their routes refused before as after. Each value without the line
break is a positive control, its check taking it, as it takes the optional forms of OPTIONAL_FORMS and the addresses
with no path, the http one with and with no port."""

import os
import sys
from urllib.parse import quote

import pytest

from conftest import ADMIN, signed_up
from workspace_fixtures import make_repo

W = "/api/ade/workspace"
A = "/api/ade/agents"
ENDS = {"a line feed": "\n", "a carriage return and a line feed": "\r\n"}
STAND_IN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_stand_in.py")
SID = "0123456789ab"


@pytest.fixture
def local(monkeypatch, tmp_path):
    """The local workspace provider as test_agents_app.py gives it: a home of the test's own and the stand-in claude
    first on the PATH."""
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(f"#!{sys.executable}\n" + open(STAND_IN).read())
    (bin_dir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OPEN_EDITION_ADE", "local")
    monkeypatch.setenv("OPEN_EDITION_ADMINS", ADMIN)
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    from proofpane_open.ade import workspaces
    yield workspaces
    workspaces.shutdown()


@pytest.fixture
def workspace(client, local, env, monkeypatch):
    """A signed-in owner whose workspace is said to be running and answers through a recorder: each route reaches its
    own checks, and every call that would go to the workspace is kept, answered with nothing."""
    from proofpane_open.ade import workspace_routes
    calls = []
    monkeypatch.setattr(workspace_routes, "_running", lambda user: "http://workspace.invalid")
    monkeypatch.setattr(workspace_routes.client, "call",
                        lambda endpoint, method, path, *a, **k: calls.append((method, path)) or {})
    return signed_up(client), calls


# (method, the path with the value in it, a value the check takes, the route's refusal)
WORKSPACE_ROUTES = [
    ("DELETE", "/repos/{v}", "demo", "No such repository."),
    ("GET", "/repos/{v}/issues", "demo", "No such repository."),
    ("GET", "/repos/{v}/pulls", "demo", "No such repository."),
    ("GET", "/claude?account={v}", "default", "No such account."),
    ("GET", "/claude?account={v}", SID, "No such account."),
    ("POST", "/accounts/{v}/sign-out", "default", "No such account."),
    ("POST", "/accounts/{v}/sign-out", SID, "No such account."),
    ("DELETE", "/accounts/{v}", SID, "No such account."),
    ("POST", "/asks/{v}/start", SID, "No such ask."),
    ("POST", "/asks/{v}/refuse", SID, "No such ask."),
]
ROUTE_IDS = [f"{m} {p.replace('{v}', v)}" for m, p, v, _ in WORKSPACE_ROUTES]


@pytest.mark.parametrize("end", ENDS.values(), ids=ENDS.keys())
@pytest.mark.parametrize("method,path,value,refusal", WORKSPACE_ROUTES, ids=ROUTE_IDS)
def test_a_workspace_route_refuses_its_value_with_a_line_break_after_it(client, workspace, method, path, value,
                                                                       refusal, end):
    h, calls = workspace
    r = client.request(method, W + path.format(v=quote(value + end, safe="")), headers=h)
    assert (r.status_code, r.json().get("detail")) == (404, refusal), r.text
    assert calls == []          # nothing reached the workspace


@pytest.mark.parametrize("method,path,value,refusal", WORKSPACE_ROUTES, ids=ROUTE_IDS)
def test_the_same_value_with_no_line_break_passes_the_route_s_check(client, workspace, method, path, value, refusal):
    h, calls = workspace
    r = client.request(method, W + path.format(v=quote(value, safe="")), headers=h)
    assert r.json().get("detail") != refusal and calls, r.text


def _finish(head):
    """The workspace's answers to Continue in a new session, the Finish's report naming `head`."""
    def answer(method, path):
        if method == "GET" and path == f"/sessions/{SID}":
            return {"id": SID, "program": "codex", "repo": "demo"}
        if method == "POST" and path == f"/sessions/{SID}/finish":
            return {"session": SID, "branch": f"pp/{SID}", "base": "a" * 40, "head": head, "label": "acme/demo",
                    "repo": "demo", "task": "Return two", "too_large": False, "diff": "", "committed": True}
        return {}
    return answer


@pytest.mark.parametrize("end,refusal", [
    ("\n", "The workspace's Finish named no commit."),
    # a carriage return the Finish's own check refuses first (finishes.check_report), as it refuses a head of None
    ("\r\n", "The workspace's report is not in the expected form.")], ids=ENDS.keys())
def test_continue_refuses_a_finish_whose_head_has_a_line_break_after_it(client, workspace, monkeypatch, end, refusal):
    """`_OBJECT_ID` checks the commit the workspace's Finish names, which then goes into the path of the next call."""
    from proofpane_open.ade import workspace_routes
    h, calls = workspace
    answer = _finish("b" * 40 + end)
    monkeypatch.setattr(workspace_routes.client, "call",
                        lambda endpoint, method, path, *a, **k: calls.append((method, path)) or answer(method, path))
    r = client.post(f"{W}/sessions/{SID}/continue", headers=h, json={"program": "codex", "task": "Return two"})
    assert (r.status_code, r.json().get("detail")) == (502, refusal), r.text
    assert not any("\n" in path or "\r" in path for _, path in calls), calls
    assert ("POST", "/sessions") not in calls       # no new session


def test_continue_goes_on_from_a_finish_whose_head_has_no_line_break(client, workspace, monkeypatch):
    from proofpane_open.ade import workspace_routes
    h, calls = workspace
    answer = _finish("b" * 40)
    monkeypatch.setattr(workspace_routes.client, "call",
                        lambda endpoint, method, path, *a, **k: calls.append((method, path)) or answer(method, path))
    client.post(f"{W}/sessions/{SID}/continue", headers=h, json={"program": "codex", "task": "Return two"})
    assert ("GET", f"/sessions/{SID}/observed?head={'b' * 40}") in calls, calls


# ---- an agent's fields (agents.check, with shapes.check_mcp and shapes.check_acceptance), on create and on update ----

@pytest.fixture
def owner(client, local, env):
    h = signed_up(client)
    user_id = client.get("/api/auth/me", headers=h).json()["id"]
    make_repo(str(env / "workspaces" / user_id / "repos" / "demo"))
    return h


def _agent_fields():
    from proofpane_open.ade import agents, shapes
    address = {"name": "notes", "kind": "address", "transport": "http", "url": "https://mcp.example.org/mcp"}
    package = {"name": "pad", "kind": "package", "identifier": "left-pad", "version": "1.3.0"}
    address_refused = "An address is an https address (or http on this machine), over http or sse."
    package_refused = "A package is an npm package's name with the exact version it runs at (1.2.3), and its arguments."
    rules_refused = (f"Allow rules are at most {agents.MAX_RULES}, each a tool's name with its pattern, such as "
                     "Bash(npm test:*).")
    # (a name, the fields with {e} where the line break goes, the route's refusal)
    return [
        ("repo", {"repo": "demo{e}"}, "An agent's repository is one of the workspace's, by its name, or none."),
        ("base_ref", {"base_ref": "main{e}"}, "An agent starts from a branch, a tag or a commit of its repository."),
        ("account", {"account": SID + "{e}"}, "An agent's account is one of the workspace's, by its id, or the "
                                              "default."),
        ("model", {"chat_settings": {"model": "claude-opus-4{e}"}}, "An agent's model is a word, as Claude Code names "
                                                                    "it."),
        ("effort", {"chat_settings": {"effort": "high{e}"}}, "An agent's effort is a word, as Claude Code names it."),
        ("permission mode", {"chat_settings": {"permission_mode": "dontAsk{e}"}},
         "An agent's permission mode is a word, as Claude Code names it."),
        ("an allow rule with its pattern", {"allow": ["Bash(npm test:*){e}"]}, rules_refused),
        ("an allow rule of a name alone", {"allow": ["Read{e}"]}, rules_refused),
        ("an https address", {"mcp_servers": [dict(address, url="https://mcp.example.org/mcp{e}")]}, address_refused),
        ("an http address on this machine", {"mcp_servers": [dict(address, url="http://localhost:8080/mcp{e}")]},
         address_refused),
        ("an https address with no path", {"mcp_servers": [dict(address, url="https://mcp.example.org{e}")]},
         address_refused),
        ("an http address on this machine with no path", {"mcp_servers": [dict(address,
                                                                                url="http://localhost:8080{e}")]},
         address_refused),
        ("an http address on this machine with no port", {"mcp_servers": [dict(address, url="http://127.0.0.1{e}")]},
         address_refused),
        ("a package's name", {"mcp_servers": [dict(package, identifier="left-pad{e}")]}, package_refused),
        ("a package's version", {"mcp_servers": [dict(package, version="1.3.0{e}")]}, package_refused),
        ("an acceptance path", {"acceptance": {"json_path_equals": {"a.b{e}": 1}}},
         "json_path_equals maps a path such as a.b.0.c to the value it must have, at most "
         f"{shapes.MAX_ACCEPTANCE_TEXT} characters of JSON."),
        ("an acceptance path of one part", {"acceptance": {"json_path_equals": {"a{e}": 1}}},
         "json_path_equals maps a path such as a.b.0.c to the value it must have, at most "
         f"{shapes.MAX_ACCEPTANCE_TEXT} characters of JSON."),
        ("a model with its context window", {"chat_settings": {"model": "claude-opus-4[1m]{e}"}},
         "An agent's model is a word, as Claude Code names it."),
        ("a scoped package's name", {"mcp_servers": [dict(package, identifier="@acme/left-pad{e}")]}, package_refused),
        ("a pre-release version", {"mcp_servers": [dict(package, version="1.3.0-beta.1{e}")]}, package_refused),
        ("a fallback model with its context window", {"acceptance": {"required_keys": ["a"], "on_fail": "fallback",
                                                                 "fallback_model": "claude-haiku-4[1m]{e}"}},
         "A fallback needs its model, as Claude Code names it."),
        ("an acceptance fallback model", {"acceptance": {"required_keys": ["a"], "on_fail": "fallback",
                                                         "fallback_model": "claude-haiku-4{e}"}},
         "A fallback needs its model, as Claude Code names it."),
    ]


def _with(fields, end):
    """`fields` with each "{e}" in its strings, keys among them, made `end`."""
    if isinstance(fields, dict):
        return {_with(k, end): _with(v, end) for k, v in fields.items()}
    if isinstance(fields, list):
        return [_with(v, end) for v in fields]
    return fields.replace("{e}", end) if isinstance(fields, str) else fields


FIELD_NAMES = [name for name, _, _ in _agent_fields()]


@pytest.mark.parametrize("end", ENDS.values(), ids=ENDS.keys())
@pytest.mark.parametrize("name", FIELD_NAMES)
def test_an_agent_s_field_with_a_line_break_after_it_is_refused_on_create_and_on_update(client, owner, name, end):
    fields, refusal = next((f, r) for n, f, r in _agent_fields() if n == name)
    base = {"name": "Triage", "brief": "hello", "repo": "demo"}
    r = client.post(A, headers=owner, json=dict(base, **_with(fields, end)))
    assert (r.status_code, r.json().get("detail")) == (400, refusal), r.text
    made = client.post(A, headers=owner, json=base)
    assert made.status_code == 200, made.text
    r = client.put(f"{A}/{made.json()['id']}", headers=owner, json=_with(fields, end))
    assert (r.status_code, r.json().get("detail")) == (400, refusal), r.text


@pytest.mark.parametrize("name", FIELD_NAMES)
def test_an_agent_s_field_with_no_line_break_is_taken(client, owner, name):
    fields, _ = next((f, r) for n, f, r in _agent_fields() if n == name)
    r = client.post(A, headers=owner, json=dict({"name": "Triage", "brief": "hello", "repo": "demo"},
                                                **_with(fields, "")))
    assert r.status_code == 200, r.text


# (a name, fields in a form that a pattern's optional part or its repetition takes; and a base with a slash in it)
OPTIONAL_FORMS = [
    ("a model with its context window", {"chat_settings": {"model": "claude-opus-4[1m]"}}),
    ("a base with a slash in it", {"base_ref": "release/v1.2"}),
    ("a scoped package", {"mcp_servers": [{"name": "pad", "kind": "package", "identifier": "@acme/left-pad",
                                           "version": "1.3.0"}]}),
    ("a pre-release version", {"mcp_servers": [{"name": "pad", "kind": "package", "identifier": "left-pad",
                                                "version": "1.3.0-beta.1"}]}),
    ("a build's version", {"mcp_servers": [{"name": "pad", "kind": "package", "identifier": "left-pad",
                                            "version": "1.3.0+build.5"}]}),
    ("an acceptance path of four parts", {"acceptance": {"json_path_equals": {"a.b.0.c": 1}}}),
    ("a fallback model with its context window", {"acceptance": {"required_keys": ["a"], "on_fail": "fallback",
                                                                 "fallback_model": "claude-haiku-4[1m]"}}),
]


@pytest.mark.parametrize("name,fields", OPTIONAL_FORMS, ids=[n for n, _ in OPTIONAL_FORMS])
def test_an_agent_s_field_in_an_optional_form_of_its_pattern_is_taken(client, owner, name, fields):
    r = client.post(A, headers=owner, json=dict({"name": "Triage", "brief": "hello", "repo": "demo"}, **fields))
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("end", ENDS.values(), ids=ENDS.keys())
def test_keep_as_an_agent_refuses_a_session_id_with_a_line_break_after_it(client, owner, end):
    r = client.get(f"{A}/draft", headers=owner, params={"session": SID + end})
    assert (r.status_code, r.json().get("detail")) == (404, "No such session."), r.text


def test_keep_as_an_agent_takes_a_session_id_with_no_line_break_to_its_next_step(client, owner):
    r = client.get(f"{A}/draft", headers=owner, params={"session": SID})
    assert (r.status_code, r.json().get("detail")) == (409, "The workspace is not running: start it first."), r.text


@pytest.mark.parametrize("end", ENDS.values(), ids=ENDS.keys())
def test_an_agent_id_with_a_line_break_after_it_finds_no_agent(client, owner, end):
    """A control, before the change as after it: `agents._ID` took "<id>\\n", but the agent's row is looked up by the
    whole value, which no row has, so the route said "No such agent." either way."""
    made = client.post(A, headers=owner, json={"name": "Triage", "brief": "hello", "repo": "demo"}).json()
    r = client.get(f"{A}/{quote(made['id'] + end, safe='')}", headers=owner)
    assert (r.status_code, r.json().get("detail")) == (404, "No such agent."), r.text


@pytest.mark.parametrize("end", ENDS.values(), ids=ENDS.keys())
def test_a_run_id_with_a_line_break_after_it_finds_no_run(client, owner, end):
    """A control too: Stop of a run checks its id with `agents._ID` twice (tasks.stop_attempt, then agents.stop_run),
    and looks it up whole each time, which no task's lease nor any run has, so the route said "No such run." either
    way."""
    r = client.post(f"{A}/runs/{quote(SID + end, safe='')}/stop", headers=owner)
    assert (r.status_code, r.json().get("detail")) == (404, "No such run."), r.text
