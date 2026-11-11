"""A chat's MCP servers (mcp.py, and what the image's claude was found to do: it starts
every server of each .mcp.json in the chat's folder and above it, without asking). First mcp.py's own rules, then a
real agent with the real Claude Agent SDK driving the stand-in claude (chat_stand_in.py), whose MCP servers are the
real one's as the image showed them. The real claude with a real server is the Docker check's."""

import json
import os
import subprocess
import sys
import time

import pytest

from proofpane_workspace import chat as chats, mcp
from workspace_fixtures import Agent, make_repo

STAND_IN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_stand_in.py")
SDK = pytest.mark.skipif(not chats.available(), reason="needs the Claude Agent SDK (requirements.txt)")
ECHO = {"command": "python3", "args": ["server.py", "--port", "0"], "env": {"TOKEN": "s3cret"}}


def write(path, doc):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(doc if isinstance(doc, str) else json.dumps(doc))


# ---- mcp.py's rules ---------------------------------------------------------------------------------------------------

def test_every_mcp_json_in_the_folder_and_above_it_is_found_nearest_first(tmp_path):
    work = tmp_path / "ws" / "sessions" / "s1" / "work"
    work.mkdir(parents=True)
    for folder in (work, tmp_path / "ws", tmp_path):
        write(folder / ".mcp.json", {"mcpServers": {}})
    (tmp_path / "ws" / "sessions" / ".mcp.json").mkdir()            # not a file: found, and cannot be read
    got = mcp.files(str(work))
    assert got[:4] == [str(work / ".mcp.json"), str(tmp_path / "ws" / "sessions" / ".mcp.json"),
                       str(tmp_path / "ws" / ".mcp.json"), str(tmp_path / ".mcp.json")]
    # along the path as it really is too, when a folder on the way is a link
    real = tmp_path / "real"
    (real / "inner").mkdir(parents=True)
    write(real / ".mcp.json", {"mcpServers": {}})
    (tmp_path / "linked").symlink_to(real)
    assert str(real / ".mcp.json") in mcp.files(str(tmp_path / "linked" / "inner"))


@pytest.mark.parametrize("text, servers, why", [
    ('{"mcpServers": {"a": {"command": "x"}, "b": {"url": "https://e.org"}}}', {"a": {"command": "x"},
                                                                                  "b": {"url": "https://e.org"}}, None),
    ("\ufeff" + '{"mcpServers": {"a": {"command": "x"}}}', {"a": {"command": "x"}}, None),
    ('{"other": 1}', {}, None),
    ('["a", "b"]', {}, None),
    ('{"mcpServers": ["a"]}', None, "its mcpServers is not an object"),
    ('{"mcpServers": {"a": 1,}}', None, "is not JSON"),
    ("", None, "is not JSON")])
def test_a_file_s_servers_are_read_as_json_or_not_at_all(tmp_path, text, servers, why):
    path = tmp_path / ".mcp.json"
    path.write_bytes(text.encode("utf-8"))
    if why is None:
        assert mcp.read(str(path)) == servers
    else:
        with pytest.raises(mcp.McpError, match=why):
            mcp.read(str(path))


def test_a_file_too_large_or_not_a_file_is_not_read(tmp_path):
    big = tmp_path / "big.json"
    big.write_bytes(b" " * (mcp.MAX_FILE + 1))
    with pytest.raises(mcp.McpError, match="larger than 1024 KB"):
        mcp.read(str(big))
    (tmp_path / "dir").mkdir()
    with pytest.raises(mcp.McpError, match="could not be read"):
        mcp.read(str(tmp_path / "dir"))


def test_a_fingerprint_is_the_configuration_exactly_and_only_names_are_shown():
    assert mcp.fingerprint({"a": 1, "b": [1, 2]}) == mcp.fingerprint({"b": [1, 2], "a": 1})
    assert mcp.fingerprint(ECHO) != mcp.fingerprint(dict(ECHO, env={"TOKEN": "other"}))
    assert mcp.shown(ECHO) == {"kind": "command", "line": "python3 server.py --port 0", "names": ["TOKEN"]}
    assert mcp.shown({"type": "stdio", "command": "npx", "args": ["-y", "a b"]})["line"] == "npx -y 'a b'"
    web = {"type": "http", "url": "https://e.org/mcp", "headers": {"Authorization": "Bearer s3cret"}}
    assert mcp.shown(web) == {"kind": "http", "line": "https://e.org/mcp", "names": ["Authorization"]}
    assert mcp.shown({"type": "sse", "url": "https://e.org/sse"})["kind"] == "sse"
    assert "s3cret" not in json.dumps([mcp.shown(ECHO), mcp.shown(web)])
    assert mcp.shown({"type": "ws"}) == {"kind": "ws", "line": ""} and mcp.shown(7) == {"kind": "unknown", "line": ""}
    assert len(mcp.shown({"command": "x" * 1000})["line"]) == mcp.SHOWN


def test_only_an_approved_server_with_its_exact_configuration_is_left_on(tmp_path):
    work = tmp_path / "repo"
    work.mkdir()
    write(work / ".mcp.json", {"mcpServers": {"echo": ECHO, "web": {"type": "http", "url": "https://e.org"}}})
    fp = mcp.fingerprint(ECHO)
    got = mcp.plan(str(work), {("echo", fp)})
    assert got["off"] == ["web"] and got["strict"] is None
    echo, web = got["servers"]
    assert (echo["name"], echo["approved"], echo["conflict"], echo["fingerprint"], echo["files"]) == \
        ("echo", True, False, fp, ["this repository"])
    assert (web["approved"], web["line"]) == (False, "https://e.org")
    # the same name with another configuration is not the server approved
    write(work / ".mcp.json", {"mcpServers": {"echo": dict(ECHO, args=["evil.py"])}})
    assert mcp.plan(str(work), {("echo", fp)})["off"] == ["echo"]
    # an approval for another name, or none, leaves it off
    assert mcp.plan(str(work), set())["off"] == ["echo"]


def test_a_name_two_files_define_differently_stays_off_and_one_that_cannot_be_read_allows_none(tmp_path):
    work = tmp_path / "ws" / "work"
    work.mkdir(parents=True)
    write(work / ".mcp.json", {"mcpServers": {"echo": ECHO}})
    write(tmp_path / "ws" / ".mcp.json", {"mcpServers": {"echo": dict(ECHO, command="sh")}})
    got = mcp.plan(str(work), {("echo", mcp.fingerprint(ECHO))})
    (echo,) = got["servers"]
    assert (got["off"], echo["conflict"], echo["fingerprint"], echo["approved"]) == (["echo"], True, None, False)
    assert echo["files"] == ["this repository", str(tmp_path / "ws" / ".mcp.json")]
    # the same definition twice is one server
    write(tmp_path / "ws" / ".mcp.json", {"mcpServers": {"echo": ECHO}})
    assert mcp.plan(str(work), {("echo", mcp.fingerprint(ECHO))})["off"] == []
    # a file that cannot be read: no server may run, and it says which file
    write(tmp_path / ".mcp.json", "{not json")
    got = mcp.plan(str(work), set())
    assert got["strict"] == f"{tmp_path / '.mcp.json'} is not JSON" and got["off"] == ["echo"]


def test_the_servers_held_back_are_given_with_the_workspace_s_own_settings(tmp_path):
    assert mcp.settings_value(None, []) is None and mcp.settings_value("/x/settings.json", []) == "/x/settings.json"
    assert json.loads(mcp.settings_value(None, ["b", "a"])) == {"disabledMcpjsonServers": ["a", "b"]}
    base = tmp_path / "settings.json"
    write(base, {"hooks": {"PreToolUse": []}, "disabledMcpjsonServers": ["old"]})
    assert json.loads(mcp.settings_value(str(base), ["a"])) == {"hooks": {"PreToolUse": []},
                                                                 "disabledMcpjsonServers": ["a", "old"]}
    write(base, [])
    with pytest.raises(mcp.McpError):
        mcp.settings_value(str(base), ["a"])


def test_approvals_are_the_workspace_s_own_file_and_a_bad_one_approves_nothing(tmp_path):
    root = str(tmp_path)
    assert mcp.load_approvals(root) == {}
    mcp.save_approvals(root, {"demo": [["echo", "f1"]]})
    assert mcp.load_approvals(root) == {"demo": [["echo", "f1"]]}
    assert os.stat(os.path.join(root, mcp.APPROVALS)).st_mode & 0o777 == 0o600
    write(tmp_path / mcp.APPROVALS, {"demo": [["echo", "f1"], ["only-one"], [1, "f"], "x"], "other": "no"})
    assert mcp.load_approvals(root) == {"demo": [["echo", "f1"]]}
    write(tmp_path / mcp.APPROVALS, "[1, 2]")
    assert mcp.load_approvals(root) == {}


def test_a_server_added_from_the_page_goes_to_claude_mcp_add_in_the_form_it_takes():
    got = mcp.add_argv("claude", {"name": "gh", "scope": "repository",
                                  "command": 'npx -y "@modelcontextprotocol/server-github" --dir "a b"',
                                  "lines": "GITHUB_TOKEN=ab=c\n\n  OTHER=1  "})
    assert got == ["claude", "mcp", "add", "--scope=local", "--env=GITHUB_TOKEN=ab=c", "--env=OTHER=1", "gh", "--",
                   "npx", "-y", "@modelcontextprotocol/server-github", "--dir", "a b"]
    got = mcp.add_argv("claude", {"name": "web.1", "scope": "workspace", "url": " https://e.org/mcp ",
                                  "lines": "Authorization: Bearer x:y"})
    assert got == ["claude", "mcp", "add", "--scope=user", "--header=Authorization: Bearer x:y", "--transport=http",
                   "web.1", "https://e.org/mcp"]
    # an older server, over SSE
    got = mcp.add_argv("claude", {"name": "old", "scope": "workspace", "url": "https://e.org/sse", "transport": "sse"})
    assert got == ["claude", "mcp", "add", "--scope=user", "--transport=sse", "old", "https://e.org/sse"]


@pytest.mark.parametrize("spec, why", [
    ({"name": "-x", "scope": "repository", "command": "a"}, "name"),
    ({"name": "", "scope": "repository", "command": "a"}, "name"),
    ({"name": "a" * 65, "scope": "repository", "command": "a"}, "name"),
    ({"name": "a b", "scope": "repository", "command": "a"}, "name"),
    ({"name": "a", "scope": "project", "command": "a"}, "for this repository or for the whole workspace"),
    ({"name": "a", "scope": "repository"}, "not both"),
    ({"name": "a", "scope": "repository", "command": "a", "url": "https://e.org"}, "not both"),
    ({"name": "a", "scope": "repository", "command": "   "}, "not both"),
    ({"name": "a", "scope": "repository", "command": "a 'b"}, "unclosed quote"),
    ({"name": "a", "scope": "repository", "command": "a", "lines": "no equals"}, "NAME=value"),
    ({"name": "a", "scope": "repository", "command": "a", "lines": "1BAD=x"}, "NAME=value"),
    ({"name": "a", "scope": "repository", "command": "a", "lines": "A=" + "x" * 9000}, "at most 8 KB"),
    ({"name": "a", "scope": "workspace", "url": "ftp://e.org"}, "https://"),
    ({"name": "a", "scope": "workspace", "url": "https://e.org/a b"}, "https://"),
    ({"name": "a", "scope": "workspace", "url": "https://e.org", "transport": "ws"}, "http or, for an older server, sse"),
    ({"name": "a", "scope": "workspace", "url": "https://e.org", "lines": "Authorization Bearer"}, "Name: value"),
    ({"name": "a", "scope": "workspace", "url": "https://e.org", "lines": "Bad Name: v"}, "Name: value"),
    ({"name": "a", "scope": "workspace", "url": "https://e.org", "lines": "Name:   "}, "Name: value"),
    ("not a spec", "name")])
def test_what_is_added_from_the_page_is_checked_before_anything_runs(spec, why):
    with pytest.raises(mcp.McpError, match=why):
        mcp.add_argv("claude", spec)


# ---- through the agent, with the stand-in claude -----------------------------------------------------------------------

@pytest.fixture
def magent(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    home = tmp_path / "home"
    home.mkdir()

    def stand_in(**extra):
        text = f"#!{sys.executable}\n" + "".join(f"{k} = {v!r}\n" for k, v in extra.items()) + open(STAND_IN).read()
        (bin_dir / "claude").write_text(text)
        (bin_dir / "claude").chmod(0o755)
    stand_in()
    repo = tmp_path / "ws" / "repos" / "demo"
    make_repo(str(repo), origin="https://github.com/acme/demo.git")
    write(repo / ".mcp.json", {"mcpServers": {"echo": ECHO}})
    subprocess.run(["git", "-C", str(repo), "add", ".mcp.json"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m",
                    "mcp"], check=True)
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": f"{bin_dir}:{os.environ['PATH']}"})
    a.home, a.repo, a.stand_in = home, repo, stand_in
    yield a
    a.close()


def _chat(agent, task="hello"):
    r = agent.http.post("/sessions", json={"repo": "demo", "program": "chat", "task": task})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _idle(agent, sid, n, timeout=30):
    deadline = time.time() + timeout
    while True:
        log = agent.http.get(f"/sessions/{sid}/chat").json()
        if log["state"] == "idle" and sum(e["kind"] == "result" for e in log["events"]) >= n:
            return log
        assert time.time() < deadline, log
        time.sleep(0.05)


def _runs(agent):
    with open(agent.home / "stand-in-runs.jsonl") as fh:
        return [json.loads(line) for line in fh]


def _chat_runs(agent):
    return [r for r in _runs(agent) if "--setting-sources=user,project,local" in r["args"]]


def _held(run):
    if "--settings" not in run["args"]:
        return []
    return json.loads(run["args"][run["args"].index("--settings") + 1]).get("disabledMcpjsonServers", [])


def _controls(agent):
    try:
        with open(agent.home / "stand-in-controls.jsonl") as fh:
            return [json.loads(line) for line in fh]
    except FileNotFoundError:
        return []


def _mcp(agent, sid, **body):
    if not body:
        return agent.http.get(f"/sessions/{sid}/chat/mcp")
    return agent.http.post(f"/sessions/{sid}/chat/mcp", json=body)


def _events(log, kind):
    return [e for e in log["events"] if e["kind"] == kind]


@SDK
def test_a_repository_s_server_is_held_back_until_approved_then_runs_from_the_next_message(magent):
    sid = _chat(magent)
    log = _idle(magent, sid, 1)
    (run,) = _chat_runs(magent)
    assert _held(run) == ["echo"] and "--strict-mcp-config" not in run["args"]
    (line,) = _events(log, "mcp")
    assert (line["servers"], line["held"], line["strict"], line["stray"]) == ([], ["echo"], None, [])
    view = _mcp(magent, sid).json()
    (echo,) = view["servers"]
    assert (echo["name"], echo["approved"], echo["line"], echo["names"], echo["files"]) == \
        ("echo", False, "python3 server.py --port 0", ["TOKEN"], ["this repository"])
    assert (view["live"], view["running"], view["strict"], view["repo"]) == ([], True, None, "demo")
    assert "s3cret" not in json.dumps(view)
    # approved as the page showed it: from the next message, claude starts with it
    got = _mcp(magent, sid, action="approve", name="echo", fingerprint=echo["fingerprint"]).json()
    assert got["servers"][0]["approved"] is True and got["restart"] is True
    assert mcp.load_approvals(magent.root) == {"demo": [["echo", echo["fingerprint"]]]}
    magent.http.post(f"/sessions/{sid}/chat", json={"text": "hello"})
    log = _idle(magent, sid, 2)
    assert len(_chat_runs(magent)) == 2 and _held(_chat_runs(magent)[1]) == []
    change = _events(log, "mcp_change")
    assert [(e["change"], e["name"], e["line"]) for e in change] == [("approved", "echo", "python3 server.py --port 0")]
    assert _events(log, "mcp")[-1]["servers"] == [{"name": "echo", "scope": "project", "status": "connected"}]
    live = _mcp(magent, sid).json()["live"]
    assert [(s["name"], s["scope"], s["status"], s["tools"], s["line"]) for s in live] == \
        [("echo", "project", "connected", 1, "python3 server.py --port 0")]
    # the capabilities handshake starts none at all
    magent.http.get("/capabilities")
    caps = [r for r in _runs(magent) if "--setting-sources=user,project,local" not in r["args"]]
    assert caps and all("--strict-mcp-config" in r["args"] for r in caps)


@SDK
def test_an_approval_is_for_the_server_as_it_was_shown(magent):
    sid = _chat(magent)
    _idle(magent, sid, 1)
    fp = _mcp(magent, sid).json()["servers"][0]["fingerprint"]
    # the entry changed in the worktree after the page showed it: the approval shown is refused
    work = os.path.join(magent.root, "sessions", sid, "work")
    write(os.path.join(work, ".mcp.json"), {"mcpServers": {"echo": dict(ECHO, args=["evil.py"])}})
    r = _mcp(magent, sid, action="approve", name="echo", fingerprint=fp)
    assert r.status_code == 400 and "changed since the page showed it" in r.json()["detail"]
    for body, why in (({"name": "none", "fingerprint": fp}, "declares no such server"),
                      ({"name": "echo", "fingerprint": None}, "changed since"), ({}, "declares no such server")):
        r = _mcp(magent, sid, action="approve", **body)
        assert r.status_code == 400 and why in r.json()["detail"], body
    # approved as it is now, then changed again: held back at claude's next start
    now = _mcp(magent, sid).json()["servers"][0]["fingerprint"]
    assert _mcp(magent, sid, action="approve", name="echo", fingerprint=now).status_code == 200
    write(os.path.join(work, ".mcp.json"), {"mcpServers": {"echo": dict(ECHO, args=["other.py"])}})
    magent.http.post(f"/sessions/{sid}/chat", json={"text": "hello"})
    _idle(magent, sid, 2)
    assert _held(_chat_runs(magent)[-1]) == ["echo"]
    # approving this one replaces the approval before: the configuration approved then, back again, waits for a tap
    other = _mcp(magent, sid).json()["servers"][0]["fingerprint"]
    assert _mcp(magent, sid, action="approve", name="echo", fingerprint=other).status_code == 200
    assert mcp.load_approvals(magent.root) == {"demo": [["echo", other]]}
    write(os.path.join(work, ".mcp.json"), {"mcpServers": {"echo": dict(ECHO, args=["evil.py"])}})
    assert _mcp(magent, sid).json()["servers"][0]["approved"] is False
    magent.http.post(f"/sessions/{sid}/chat", json={"text": "hello"})
    _idle(magent, sid, 3)
    assert _held(_chat_runs(magent)[-1]) == ["echo"]
    # a name a folder above defines otherwise cannot be approved, and stays off
    write(os.path.join(magent.root, ".mcp.json"), {"mcpServers": {"echo": {"command": "sh"}}})
    view = _mcp(magent, sid).json()
    assert view["servers"][0]["conflict"] is True and view["servers"][0]["fingerprint"] is None
    r = _mcp(magent, sid, action="approve", name="echo", fingerprint=now)
    assert r.status_code == 400 and "define that server differently" in r.json()["detail"]
    assert _mcp(magent, sid, action="whatever").status_code == 400


@SDK
def test_a_file_that_cannot_be_read_leaves_the_chat_with_no_server_and_says_why(magent):
    write(os.path.join(magent.root, ".mcp.json"), "{broken")
    sid = _chat(magent)
    log = _idle(magent, sid, 1)
    (run,) = _chat_runs(magent)
    assert "--strict-mcp-config" in run["args"]
    (line,) = _events(log, "mcp")
    assert line["strict"] == f"{os.path.realpath(magent.root)}/.mcp.json is not JSON" or \
        line["strict"] == f"{magent.root}/.mcp.json is not JSON"
    assert _mcp(magent, sid).json()["strict"] == line["strict"]


@SDK
def test_a_repository_server_no_file_declares_is_turned_off_at_once(magent):
    magent.stand_in(STRAY="sneaky")
    sid = _chat(magent)
    log = _idle(magent, sid, 1)
    assert {"subtype": "mcp_toggle", "serverName": "sneaky", "enabled": False} in _controls(magent)
    assert _kept_off(magent) == ["sneaky"]                    # claude keeps it off for the repository
    (line,) = _events(log, "mcp")
    assert line["stray"] == ["sneaky"] and line["held"] == ["echo"]
    live = _mcp(magent, sid).json()["live"]
    assert [(s["name"], s["status"]) for s in live] == [("sneaky", "disabled")]
    # the next claude: kept off, and left off
    magent.http.post(f"/sessions/{sid}/stop")
    magent.http.post(f"/sessions/{sid}/chat", json={"text": "hello"})
    _idle(magent, sid, 2)
    assert {"subtype": "mcp_toggle", "serverName": "sneaky", "enabled": True} not in _controls(magent)
    assert [(s["name"], s["status"]) for s in _mcp(magent, sid).json()["live"]] == [("sneaky", "disabled")]


def _kept_off(agent):
    try:
        data = json.loads((agent.home / ".claude.json").read_text())
    except FileNotFoundError:
        return []
    return (data.get("projects") or {}).get(os.path.realpath(agent.repo), {}).get("disabledMcpServers") or []


@SDK
def test_stopping_a_server_ends_it_holds_it_back_and_an_approval_brings_it_back(magent):
    # not by turning it off: claude keeps that for every chat on the repository (seen with 2.1.274 and 2.1.277),
    # and a server approved again would stay off; claude, idle, stops and resumes
    sid = _chat(magent)
    _idle(magent, sid, 1)
    fp = _mcp(magent, sid).json()["servers"][0]["fingerprint"]
    _mcp(magent, sid, action="approve", name="echo", fingerprint=fp)
    magent.http.post(f"/sessions/{sid}/chat", json={"text": "hello"})
    _idle(magent, sid, 2)
    got = _mcp(magent, sid, action="stop", name="echo").json()
    assert got["servers"][0]["approved"] is False and mcp.load_approvals(magent.root) == {"demo": []}
    assert got["running"] is False and magent.http.get(f"/sessions/{sid}/chat").json()["state"] == "stopped"
    assert all(c["subtype"] != "mcp_toggle" for c in _controls(magent)) and _kept_off(magent) == []
    magent.http.post(f"/sessions/{sid}/chat", json={"text": "hello"})
    log = _idle(magent, sid, 3)
    assert _held(_chat_runs(magent)[-1]) == ["echo"]
    assert [e["change"] for e in _events(log, "mcp_change")] == ["approved", "stopped"]
    # approved again, it runs again
    _mcp(magent, sid, action="approve", name="echo", fingerprint=fp)
    magent.http.post(f"/sessions/{sid}/chat", json={"text": "hello"})
    _idle(magent, sid, 4)
    assert [(s["name"], s["status"]) for s in _mcp(magent, sid).json()["live"]] == [("echo", "connected")]
    assert _mcp(magent, sid, action="stop").status_code == 400


@SDK
def test_an_approved_server_claude_kept_off_is_turned_on(magent):
    # claude keeps a server turned off for the repository (a terminal's /mcp, a stray's turn-off, an earlier version of
    # this page); the owner's approval on the page is the word now
    data = {"projects": {os.path.realpath(magent.repo): {"disabledMcpServers": ["echo"]}}}
    (magent.home / ".claude.json").write_text(json.dumps(data))
    sid = _chat(magent)
    _idle(magent, sid, 1)
    fp = _mcp(magent, sid).json()["servers"][0]["fingerprint"]
    _mcp(magent, sid, action="approve", name="echo", fingerprint=fp)
    magent.http.post(f"/sessions/{sid}/chat", json={"text": "hello"})
    log = _idle(magent, sid, 2)
    assert {"subtype": "mcp_toggle", "serverName": "echo", "enabled": True} in _controls(magent)
    assert _kept_off(magent) == []
    assert _events(log, "mcp")[-1]["servers"] == [{"name": "echo", "scope": "project", "status": "connected"}]


@SDK
def test_a_server_is_added_for_the_repository_or_the_workspace_and_removed(magent):
    sid = _chat(magent)
    _idle(magent, sid, 1)
    got = _mcp(magent, sid, action="add", spec={"name": "gh", "scope": "repository",
                                                "command": "npx -y server-github", "lines": "GITHUB_TOKEN=abc"})
    assert got.status_code == 200 and got.json()["restart"] is True
    got = _mcp(magent, sid, action="add", spec={"name": "web", "scope": "workspace", "url": "https://e.org/mcp",
                                                "lines": "Authorization: Bearer xyz"})
    assert got.status_code == 200
    got = _mcp(magent, sid, action="add", spec={"name": "old", "scope": "workspace", "url": "https://e.org/sse",
                                                "transport": "sse"})
    assert got.status_code == 200
    assert json.loads((magent.home / ".claude.json").read_text())["mcpServers"]["old"] == \
        {"type": "sse", "url": "https://e.org/sse"}
    assert _mcp(magent, sid, action="remove", name="old", scope="user").status_code == 200
    data = json.loads((magent.home / ".claude.json").read_text())
    assert data["mcpServers"] == {"web": {"type": "http", "url": "https://e.org/mcp",
                                          "headers": {"Authorization": "Bearer xyz"}}}
    assert data["projects"][os.path.realpath(magent.repo)]["mcpServers"] == \
        {"gh": {"type": "stdio", "command": "npx", "args": ["-y", "server-github"], "env": {"GITHUB_TOKEN": "abc"}}}
    # the same name again: Claude Code's own words
    r = _mcp(magent, sid, action="add", spec={"name": "gh", "scope": "repository", "command": "x"})
    assert r.status_code == 400 and r.json()["detail"] == "MCP server gh already exists in local config"
    r = _mcp(magent, sid, action="add", spec={"name": "-x", "scope": "repository", "command": "x"})
    assert r.status_code == 400 and "A server's name" in r.json()["detail"]
    # claude starts with them from the next message; the page never shows a value of theirs
    magent.http.post(f"/sessions/{sid}/chat", json={"text": "hello"})
    log = _idle(magent, sid, 2)
    view = _mcp(magent, sid).json()
    assert [(s["name"], s["scope"], s["line"], s["names"]) for s in view["live"]] == \
        [("web", "user", "https://e.org/mcp", ["Authorization"]), ("gh", "local", "npx -y server-github",
                                                                   ["GITHUB_TOKEN"])]
    assert "xyz" not in json.dumps(view) and "abc" not in json.dumps(view)
    assert [(e["change"], e["name"]) for e in _events(log, "mcp_change")] == [
        ("added", "gh"), ("added", "web"), ("added", "old"), ("removed", "old")]
    # removed, each from its own scope
    assert _mcp(magent, sid, action="remove", name="gh", scope="local").status_code == 200
    assert _mcp(magent, sid, action="remove", name="web", scope="user").status_code == 200
    data = json.loads((magent.home / ".claude.json").read_text())
    assert data["mcpServers"] == {} and data["projects"][os.path.realpath(magent.repo)]["mcpServers"] == {}
    # claude, idle, stopped with them: the list is what it had, marked removed, and nothing is kept off for them
    view = _mcp(magent, sid).json()
    assert [(s["name"], s["removed"]) for s in view["live"]] == [("web", True), ("gh", True)] and view["running"] is False
    assert all(c["subtype"] != "mcp_toggle" for c in _controls(magent))
    # the same name added again: the claude that starts next has it, not removed
    assert _mcp(magent, sid, action="add", spec={"name": "web", "scope": "workspace",
                                                 "url": "https://e.org/mcp"}).status_code == 200
    magent.http.post(f"/sessions/{sid}/chat", json={"text": "hello"})
    _idle(magent, sid, 3)
    live = _mcp(magent, sid).json()["live"]
    assert [(s["name"], s["status"], s["removed"]) for s in live] == [("web", "connected", False)]
    for body, why in (({"name": "gh", "scope": "local"}, 'No MCP server named "gh" in local scope'),
                      ({"name": "echo", "scope": "project"}, "Only a server added"),
                      ({"name": "claude.ai Drive", "scope": "claudeai"}, "cannot be removed from here")):
        r = _mcp(magent, sid, action="remove", **body)
        assert r.status_code == 400 and why in r.json()["detail"], body


@SDK
def test_a_server_added_again_that_claude_kept_off_is_turned_on_and_no_other(magent):
    # an earlier version's Remove turned a server off, and claude keeps that for the repository: added again from the
    # page, it runs; one the owner turned off elsewhere (a terminal's /mcp) stays off
    data = {"mcpServers": {"term": {"type": "http", "url": "https://t.org/mcp"}},
            "projects": {os.path.realpath(magent.repo): {"disabledMcpServers": ["web", "term"]}}}
    (magent.home / ".claude.json").write_text(json.dumps(data))
    sid = _chat(magent)
    _idle(magent, sid, 1)
    assert _mcp(magent, sid, action="add", spec={"name": "web", "scope": "workspace",
                                                 "url": "https://e.org/mcp"}).status_code == 200
    magent.http.post(f"/sessions/{sid}/chat", json={"text": "hello"})
    _idle(magent, sid, 2)
    live = {s["name"]: s["status"] for s in _mcp(magent, sid).json()["live"]}
    assert live == {"term": "disabled", "web": "connected"} and _kept_off(magent) == ["term"]
    # only that start: turned off again in a terminal later, it stays off
    magent.http.post(f"/sessions/{sid}/stop")
    data = json.loads((magent.home / ".claude.json").read_text())
    data["projects"][os.path.realpath(magent.repo)]["disabledMcpServers"] = ["term", "web"]
    (magent.home / ".claude.json").write_text(json.dumps(data))
    magent.http.post(f"/sessions/{sid}/chat", json={"text": "hello"})
    _idle(magent, sid, 3)
    assert {s["name"]: s["status"] for s in _mcp(magent, sid).json()["live"]} == {"term": "disabled", "web": "disabled"}


@SDK
def test_only_a_chat_has_mcp_servers(magent):
    r = magent.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "look"})
    sid = r.json()["id"]
    assert _mcp(magent, sid).status_code == 400 and "not a chat" in _mcp(magent, sid).json()["detail"]


@SDK
def test_a_chat_on_another_account_adds_and_removes_its_servers_in_that_account(magent):
    # accounts.py: `claude mcp add` and `remove` run with the chat's account's folder, where Claude Code keeps that
    # account's .claude.json; the default account's is not touched
    account = magent.http.post("/accounts", json={"kind": "claude", "name": "Work"}).json()
    r = magent.http.post("/sessions", json={"repo": "demo", "program": "chat", "task": "hello", "account": account["id"]})
    sid = r.json()["id"]
    _idle(magent, sid, 1)
    got = _mcp(magent, sid, action="add", spec={"name": "web", "scope": "workspace", "url": "https://e.org/mcp"})
    assert got.status_code == 200, got.text
    folder = os.path.join(magent.root, "agent-accounts", account["id"], "claude")
    assert json.loads(open(os.path.join(folder, ".claude.json")).read())["mcpServers"] == \
        {"web": {"type": "http", "url": "https://e.org/mcp"}}
    default = magent.home / ".claude.json"
    assert not default.exists() or "web" not in json.loads(default.read_text()).get("mcpServers", {})
    assert _mcp(magent, sid, action="remove", name="web", scope="user").status_code == 200
    assert json.loads(open(os.path.join(folder, ".claude.json")).read())["mcpServers"] == {}
