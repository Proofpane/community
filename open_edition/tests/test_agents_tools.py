"""An agent's tools (agents.py, mcp.py, registry.py): its own MCP servers, given at each run's start and never turned
off or on; strict unless it uses the claude.ai connectors, and then the workspace's own servers held back by name;
a server whose tools changed since the owner accepted them left out before the run's first message; its secrets kept
at the workspace alone; and the registry searched, and a server proposed, by the run itself. Through a real workspace
agent with the stand-in claude, whose start's arguments are written down."""

import http.server
import json
import os
import stat
import threading
import time

import pytest

from test_agents_runs import AGENT, OTHER, Workspace, log, put, run, starts, until

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "mcp_registry_filesystem.json")
ADDRESS = {"name": "notes", "kind": "address", "transport": "http", "url": "https://mcp.example.org/mcp",
           "headers": {"X-Team": "docs"}, "secret_headers": ["Authorization"]}


@pytest.fixture
def ws(tmp_path):
    w = Workspace(tmp_path)
    yield w
    w.stop()


def given(start):
    """The servers a start of claude was given (on its command line or in the file named there), but the
    workspace's own, as the stand-in read them."""
    return start.get("mcp") or {}


def settings(start):
    value = start["args"][start["args"].index("--settings") + 1] if "--settings" in start["args"] else "{}"
    return json.loads(value) if value.lstrip().startswith("{") else json.load(open(value))


def controls(ws):
    path = ws.home / "stand-in-controls.jsonl"
    return [json.loads(line) for line in open(path)] if path.exists() else []


def test_an_agent_s_server_is_its_own_runs_alone(ws):
    put(ws, "hello", mcp={"servers": [ADDRESS]})
    put(ws, "hello", agent_id=OTHER)
    mine = run(ws)["run_id"]
    until(ws, mine, ("done",))
    count = len(starts(ws))
    other = run(ws, agent_id=OTHER)["run_id"]
    until(ws, other, ("done",))
    first, second = starts(ws)[count - 1], starts(ws)[-1]
    assert given(first) == {"notes": {"type": "http", "url": "https://mcp.example.org/mcp",
                                      "headers": {"X-Team": "docs"}}}
    assert given(second) == {}                                            # the other agent's, on the same repository
    chat = ws.http.post("/sessions", json={"repo": "demo", "program": "chat", "task": "hello"}).json()["id"]
    deadline = time.time() + 30
    while not any(e["kind"] == "result" for e in log(ws, chat)["events"]):
        assert time.time() < deadline
        time.sleep(0.05)
    assert given(starts(ws)[-1]) == {}                                    # nor the owner's own chats


def test_a_run_is_strict_unless_its_agent_uses_the_connectors_and_then_the_inherited_are_held_back(ws):
    with open(ws.home / ".claude.json", "w") as fh:                     # a server the workspace keeps, user scope
        json.dump({"mcpServers": {"inherited": {"type": "stdio", "command": "x"}}}, fh)
    put(ws, "hello", mcp={"servers": [ADDRESS]})
    strict = run(ws)["run_id"]
    done = until(ws, strict, ("done",))
    assert "--strict-mcp-config" in starts(ws)[-1]["args"]
    assert [s["name"] for s in done["mcp"]] == ["notes"]                 # what claude reported it has
    put(ws, "hello", mcp={"servers": [ADDRESS], "connectors": True})
    loose = run(ws)["run_id"]
    done = until(ws, loose, ("done",))
    start = starts(ws)[-1]
    assert "--strict-mcp-config" not in start["args"]
    assert settings(start)["deniedMcpServers"] == [{"serverName": "inherited"}]
    assert [s["name"] for s in done["mcp"]] == ["notes"]                 # the inherited one held back by name


def test_a_run_never_turns_a_server_off_or_on(ws):
    with open(os.path.join(ws.root, "repos", "demo", ".mcp.json"), "w") as fh:     # not approved anywhere
        json.dump({"mcpServers": {"stray": {"type": "stdio", "command": "y"}}}, fh)
    os.system(f"cd {os.path.join(ws.root, 'repos', 'demo')} && git add .mcp.json && "
              "git -c user.name=t -c user.email=t@x commit -qm mcp")
    put(ws, "hello", mcp={"servers": [ADDRESS], "connectors": True})
    r = run(ws)["run_id"]
    until(ws, r, ("done",))
    assert settings(starts(ws)[-1])["disabledMcpjsonServers"] == ["stray"]    # held back per run, by name
    assert [c for c in controls(ws) if c.get("subtype") == "mcp_toggle"] == []
    refused = ws.http.post(f"/sessions/{r}/chat/mcp", json={"action": "stop", "name": "stray"})
    assert refused.status_code == 400 and "agent's tools" in refused.json()["detail"]


def test_a_server_the_workspace_could_not_hold_back_makes_the_run_strict_and_none_is_turned_off(ws):
    import sys
    from test_agents_runs import STAND_IN
    (ws.bin / "claude").write_text(f"#!{sys.executable}\nSTRAY = 'stray'\n" + open(STAND_IN).read())
    put(ws, "hello", mcp={"servers": [ADDRESS], "connectors": True})
    r = run(ws)["run_id"]
    done = until(ws, r, ("done",))
    later = starts(ws)                                                  # this workspace's first claude, and its second
    assert "--strict-mcp-config" not in later[0]["args"] and "--strict-mcp-config" in later[-1]["args"]
    assert [c for c in controls(ws) if c.get("subtype") == "mcp_toggle"] == []
    assert "could not hold back" in done["mcp_unavailable"]["connectors"]
    assert [s["name"] for s in done["mcp"]] == ["notes"]
    assert any(e["kind"] == "notice" and "stray" in e["text"] for e in log(ws, r)["events"])


def test_a_server_whose_tools_changed_is_left_out_before_the_run_s_first_message(ws):
    package = {"name": "fs", "kind": "package", "identifier": "@acme/fs-mcp", "version": "1.2.3",
               "env": {"STAND_IN_TOOLS": "read,write"}}
    put(ws, "hello", mcp={"servers": [package]})
    first = run(ws)["run_id"]
    seen = until(ws, first, ("done",))["mcp_seen"]["fs"]
    assert len(seen) == 64 and given(starts(ws)[-1])["fs"]["args"] == ["-y", "@acme/fs-mcp@1.2.3"]
    # accepted as it was; then its tools change
    changed = dict(package, env={"STAND_IN_TOOLS": "read,write,delete"})
    put(ws, "hello", mcp={"servers": [changed], "accepted": {"fs": seen}})
    count = len(starts(ws))
    second = run(ws)["run_id"]
    done = until(ws, second, ("done",))
    assert done["mcp_changed"] == ["fs"] and done["mcp_seen"]["fs"] != seen
    later = starts(ws)[count:]
    assert "fs" in given(later[0]) and "fs" not in given(later[-1])     # started again without it
    assert [e["kind"] for e in log(ws, second)["events"]].index("notice") < \
        [e["kind"] for e in log(ws, second)["events"]].index("text")     # said before the run's first reply
    # held (as the app mirrors it): left out from the start, with why
    put(ws, "hello", mcp={"servers": [changed], "accepted": {"fs": seen}, "held": ["fs"]})
    third = run(ws)["run_id"]
    done = until(ws, third, ("done",))
    assert "fs" not in given(starts(ws)[-1]) and "tools changed" in done["mcp_unavailable"]["fs"]


def test_secrets_are_kept_at_the_workspace_alone_and_never_given_back(ws):
    put(ws, "hello", mcp={"servers": [ADDRESS]})
    r = ws.http.put(f"/agents/{AGENT}/mcp-secrets", json={"secrets": {"notes": {"Authorization": "Bearer s3cr3t"}}})
    assert r.status_code == 200 and r.json() == {"notes": ["Authorization"]}
    path = os.path.join(ws.root, "agents", AGENT, "mcp.json")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    bad = ws.http.put(f"/agents/{AGENT}/mcp-secrets", json={"secrets": {"notes": {"X-Team": "not a secret one"}}})
    assert bad.status_code == 400
    one = run(ws)["run_id"]
    until(ws, one, ("done",))
    assert given(starts(ws)[-1])["notes"]["headers"] == {"X-Team": "docs", "Authorization": "Bearer s3cr3t"}
    assert "s3cr3t" not in json.dumps(starts(ws)[-1]["args"])            # never on claude's command line
    (filed,) = starts(ws)[-1]["mcp_files"]                                # but in a file only its user reads
    servers_file = os.path.join(ws.root, "agents", AGENT, f"servers-{one}.json")
    assert os.path.realpath(filed["path"]) == os.path.realpath(servers_file) and filed["mode"] == 0o600
    assert not os.path.exists(servers_file)                               # gone at the run's end
    assert "s3cr3t" not in json.dumps(ws.http.get("/agents/runs?since=0").json())
    assert "s3cr3t" not in json.dumps(ws.http.get(f"/agents/{AGENT}").json())
    cleared = ws.http.put(f"/agents/{AGENT}/mcp-secrets", json={"secrets": {"notes": {"Authorization": ""}}})
    assert cleared.json() == {}


class Registry(http.server.BaseHTTPRequestHandler):
    asked = []

    def do_GET(self):
        Registry.asked.append(self.path)
        body = open(FIXTURE, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class RegistryWorkspace(Workspace):
    """A workspace agent that asks a registry of the test's own."""

    def __init__(self, tmp_path, url):
        self.url = url
        super().__init__(tmp_path)

    def start(self):
        from workspace_fixtures import Agent
        info = os.path.join(self.root, "agent.json")
        if os.path.exists(info):
            os.unlink(info)
        self.agent = Agent(self.root, str(self.home), {"PATH": f"{self.bin}:{os.environ['PATH']}",
                                                       "PP_MCP_REGISTRY": self.url})
        self.http = self.agent.http


@pytest.fixture
def registry(tmp_path):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Registry)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    Registry.asked = []
    w = RegistryWorkspace(tmp_path, f"http://127.0.0.1:{server.server_address[1]}")
    yield w
    w.stop()
    server.shutdown()


def test_a_run_searches_the_registry_and_proposes_a_server_and_nothing_is_added(registry):
    w = registry
    put(w, "search filesystem")
    r = run(w)["run_id"]
    done = until(w, r, ("done",))
    assert "com.pulsemcp/remote-filesystem" in done["last_text"]
    assert "version=latest" in Registry.asked[-1] and "search=filesystem" in Registry.asked[-1]
    put(w, "propose io.github.Digital-Defiance/mcp-filesystem")
    r = run(w)["run_id"]
    done = until(w, r, ("done",))
    (proposal,) = done["proposals"]
    assert proposal["name"] == "io.github.Digital-Defiance/mcp-filesystem" and proposal["why"] == "it needs it"
    assert proposal["ways"][0] == {"kind": "package", "registry": "npm",
                                   "identifier": "@ai-capabilities-suite/mcp-filesystem", "version": "0.1.9",
                                   "env": proposal["ways"][0]["env"]}
    assert w.http.get(f"/agents/{AGENT}").json()["mcp"]["servers"] == []      # nothing added
    put(w, "propose no.such/server")
    r = run(w)["run_id"]
    assert "No server of that exact name" in until(w, r, ("done",))["last_text"]


def test_the_secrets_file_is_its_user_s_alone_whatever_the_umask(tmp_path):
    from types import SimpleNamespace
    from proofpane_workspace import agents as agentry
    space = SimpleNamespace(root=str(tmp_path), sessions_dir=str(tmp_path / "sessions"), chat_settings=lambda s: s)
    kept = agentry.Agents(space)
    kept.put({"id": AGENT, "name": "Triage", "brief": "hello", "repo": "demo", "mcp": {"servers": [ADDRESS]}})
    was = os.umask(0o022)
    try:
        kept.put_secrets(AGENT, {"notes": {"Authorization": "Bearer x"}})
    finally:
        os.umask(was)
    assert stat.S_IMODE(os.stat(tmp_path / "agents" / AGENT / "mcp.json").st_mode) == 0o600


@pytest.mark.parametrize("server, refused", [
    (dict(ADDRESS, url="https://mcp.example.org/mcp?token=abc"), True),
    (dict(ADDRESS, url="https://mcp.example.org/mcp?v=2&api_key=abc"), True),
    (dict(ADDRESS, url="https://mcp.example.org/mcp?v=2&monkey=1"), False),
    ({"name": "fs", "kind": "package", "identifier": "@acme/fs-mcp", "version": "1.2.3", "args": ["--api-key=abc"]}, True),
    ({"name": "fs", "kind": "package", "identifier": "@acme/fs-mcp", "version": "1.2.3", "args": ["--port", "3000"]}, False)])
def test_a_key_in_an_address_s_query_or_a_package_s_arguments_is_refused(server, refused):
    """Either would be kept as the agent's plain configuration, in the app's store: a key goes as a secret."""
    from proofpane_workspace import shapes
    if refused:
        with pytest.raises(shapes.ShapeError, match="secret"):
            shapes.check_mcp({"servers": [server]})
    else:
        assert shapes.check_mcp({"servers": [server]})["servers"][0]["name"] == server["name"]


def test_a_run_s_servers_file_is_made_new_and_never_written_through_a_link(tmp_path):
    from proofpane_workspace import chat
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text("kept")
    path = tmp_path / "servers-r.json"
    os.symlink(elsewhere, path)                         # a link planted where the file goes
    chat._write_private(str(path), {"mcpServers": {"notes": {"headers": {"Authorization": "Bearer s3cr3t"}}}})
    assert elsewhere.read_text() == "kept" and not os.path.islink(path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600 and "s3cr3t" in path.read_text()


def test_a_link_planted_after_the_old_file_went_is_refused_not_followed(tmp_path, monkeypatch):
    from proofpane_workspace import chat
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text("kept")
    path = tmp_path / "servers-r.json"
    path.write_text("{}")
    unlink = os.unlink

    def then_a_link(p):                                 # the race: a link made between the unlink and the open
        unlink(p)
        os.symlink(elsewhere, p)
    monkeypatch.setattr(chat.os, "unlink", then_a_link)
    with pytest.raises(FileExistsError):
        chat._write_private(str(path), {"mcpServers": {"notes": {"headers": {"Authorization": "Bearer s3cr3t"}}}})
    monkeypatch.undo()
    assert elsewhere.read_text() == "kept"


def test_a_servers_file_that_cannot_be_made_fails_its_run_and_says_why(ws):
    put(ws, "wait", mcp={"servers": [ADDRESS]})
    first = run(ws)["run_id"]
    until(ws, first, ("working",))
    second = run(ws)["run_id"]                                            # behind the live one
    os.mkdir(os.path.join(ws.root, "agents", AGENT, f"servers-{second}.json"))
    ws.http.post(f"/agents/runs/{first}/stop")
    ended = until(ws, second, ("failed", "blocked", "stopped"))
    assert "could not be written" in (ended.get("reason") or ""), ended
