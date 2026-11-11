"""The hosted workspace through the app: off by default, the local provider, the routes, Verify on finish, and the
terminal proxy, with a real workspace agent behind it."""

import json
import os
import shutil
import subprocess
import sys
import threading
import time

import pytest
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect

from conftest import ADMIN, signed_up
from proofpane_stand_in import CODE, TOKEN, proofpane  # noqa: F401  a fixture
from workspace_fixtures import git, make_repo, next_control, read_until

OTHER_ADMIN = "second-admin@example.com"


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


def _user_id(client, headers):
    return client.get("/api/auth/me", headers=headers).json()["id"]


def _started(client, headers, env):
    r = client.post("/api/ade/workspace/start", headers=headers)
    assert r.status_code == 200 and r.json()["running"] is True, r.text
    root = env / "workspaces" / _user_id(client, headers)
    first = make_repo(str(root / "repos" / "demo"), origin="https://github.com/acme/demo.git")
    return root, first


def _session(client, headers, task="Return two\n\nf now returns 2."):
    r = client.post("/api/ade/workspace/sessions", headers=headers,
                    json={"repo": "demo", "program": "shell", "task": task})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_workspace_is_off_by_default(client, monkeypatch):
    monkeypatch.delenv("OPEN_EDITION_ADE", raising=False)
    h = signed_up(client)
    assert client.get("/api/ade/workspace", headers=h).json() == {"enabled": False}
    for method, path in (("POST", "/start"), ("GET", "/repos"), ("GET", "/sessions"), ("GET", "/finishes"),
                         ("POST", "/sessions/aaaaaaaaaaaa/finish")):
        r = client.request(method, "/api/ade/workspace" + path, headers=h)
        assert r.status_code == 404 and "off" in r.json()["detail"], (path, r.text)


def test_every_route_needs_an_account(client, local):
    for method, path in (("GET", ""), ("POST", "/start"), ("POST", "/stop"), ("GET", "/repos"),
                         ("GET", "/sessions"), ("GET", "/finishes"), ("GET", "/claude"), ("POST", "/claude/sign-out"),
                         ("GET", "/sessions/aaaaaaaaaaaa/chat"), ("POST", "/sessions/aaaaaaaaaaaa/chat"),
                         ("POST", "/sessions/aaaaaaaaaaaa/chat/answer"), ("POST", "/sessions/aaaaaaaaaaaa/chat/interrupt"),
                         ("POST", "/sessions/aaaaaaaaaaaa/sign-in-code"), ("GET", "/sessions/aaaaaaaaaaaa/diff"),
                         ("GET", "/sessions/aaaaaaaaaaaa/files"), ("GET", "/sessions/aaaaaaaaaaaa/file"),
                         ("POST", "/sessions/aaaaaaaaaaaa/ship"), ("GET", "/sessions/aaaaaaaaaaaa/gate"),
                         ("GET", "/sessions/aaaaaaaaaaaa/pr"), ("POST", "/sessions/aaaaaaaaaaaa/pr/merge"),
                         ("GET", "/repos/demo/issues"), ("GET", "/sessions/aaaaaaaaaaaa/paths"), ("GET", "/programs"),
                         ("POST", "/sessions/aaaaaaaaaaaa/discard"), ("POST", "/sessions/aaaaaaaaaaaa/shell"),
                         ("POST", "/sessions/aaaaaaaaaaaa/shell/1/stop"), ("DELETE", "/sessions/aaaaaaaaaaaa/shell/1"), ("POST", "/sessions/aaaaaaaaaaaa/title"),
                         ("POST", "/sessions/aaaaaaaaaaaa/close"), ("POST", "/sessions/aaaaaaaaaaaa/open"),
                         ("GET", "/sessions/aaaaaaaaaaaa/search"), ("PUT", "/sessions/aaaaaaaaaaaa/file"),
                         ("GET", "/sessions/aaaaaaaaaaaa/image"), ("GET", "/asks"), ("POST", "/asks/settings"),
                         ("POST", "/asks/0123456789ab/start"), ("POST", "/asks/0123456789ab/refuse"),
                         ("GET", "/linear"), ("PUT", "/linear/key"), ("DELETE", "/linear/key"), ("GET", "/linear/issues"),
                         ("GET", "/proofpane"), ("PUT", "/proofpane/pair"), ("DELETE", "/proofpane/pair"),
                         ("PUT", "/proofpane/holds"),
                         ("POST", "/repos/link"), ("DELETE", "/repos/demo"), ("GET", "/sessions/aaaaaaaaaaaa/update"),
                         ("POST", "/sessions/aaaaaaaaaaaa/update"), ("POST", "/sessions/aaaaaaaaaaaa/update/abort"),
                         ("GET", "/accounts"), ("POST", "/accounts"), ("POST", "/accounts/choose"),
                         ("POST", "/accounts/aaaaaaaaaaaa/sign-out"), ("DELETE", "/accounts/aaaaaaaaaaaa")):
        assert client.request(method, "/api/ade/workspace" + path).status_code == 401, path


def test_a_provider_that_is_not_known_or_not_allowed_here_stops_the_app(monkeypatch):
    from proofpane_open.ade import workspaces
    monkeypatch.setenv("OPEN_EDITION_ADE", "docker")
    with pytest.raises(RuntimeError, match="local or fly"):
        workspaces.check_config()
    monkeypatch.setenv("OPEN_EDITION_ADE", "local")
    monkeypatch.setenv("FLY_APP_NAME", "community-app")
    with pytest.raises(RuntimeError, match="never runs on the hosted site"):
        workspaces.check_config()
    monkeypatch.delenv("FLY_APP_NAME")
    workspaces.check_config()


def test_the_local_workspace_is_an_admins_only(client, local):
    h = signed_up(client, "member@example.com")
    got = client.get("/api/ade/workspace", headers=h).json()
    assert got["enabled"] is True and got["allowed"] is False and "only an admin" in got["detail"]
    assert client.post("/api/ade/workspace/start", headers=h).status_code == 403


def test_the_local_workspace_refuses_on_the_hosted_site_even_if_started(client, local, monkeypatch):
    h = signed_up(client)
    monkeypatch.setenv("FLY_APP_NAME", "community-app")
    assert client.post("/api/ade/workspace/start", headers=h).status_code == 403


def test_the_agent_is_started_with_none_of_the_apps_secrets(client, local, monkeypatch):
    seen = []
    real = local.subprocess.Popen

    def recording(*args, **kwargs):
        seen.append((list(args[0]), kwargs.get("env")))
        return real(*args, **kwargs)

    monkeypatch.setattr(local.subprocess, "Popen", recording)
    monkeypatch.setenv("OPEN_EDITION_KEYSTORE_KEY", "k" * 43)
    h = signed_up(client)
    assert client.post("/api/ade/workspace/start", headers=h).status_code == 200
    # every process the provider starts (the settings it writes first, then the agent) gets none of the app's
    for argv, env in seen:
        assert env is not None and not [k for k in env if k.startswith(("OPEN_EDITION", "FLY_"))], argv
    ((argv, agent),) = [(argv, env) for argv, env in seen if argv[1:3] == ["-m", "proofpane_workspace"]]
    assert set(agent) <= set(local.AGENT_PASS_ENV) | {"PP_WORKSPACE_CLAUDE_SETTINGS", "PP_WORKSPACE_GEMINI_SETTINGS",
                                                      "PP_WORKSPACE_QWEN_SETTINGS", "PP_WORKSPACE_PARENT"}
    # the app names itself, so the agent stops once the app is gone without stopping it (test_workspace_start.py)
    assert agent["PP_WORKSPACE_PARENT"] == str(os.getpid())
    # its secret came in a file, which it read and deleted as it started
    assert not os.path.exists(argv[argv.index("--secret-file") + 1])
    # the settings are written three times, Claude Code's, Gemini CLI's and Qwen Code's, each with nothing of the app's
    settings_runs = [(argv, env) for argv, env in seen if argv[1:3] == ["-m", "proofpane_workspace.settings"]]
    assert [("--gemini" in argv, "--qwen" in argv) for argv, _ in settings_runs] == [(False, False), (True, False),
                                                                                    (False, True)]
    assert all(set(env) <= set(local.AGENT_PASS_ENV) for _, env in settings_runs)


def test_the_workspace_s_pairing_with_proofpane_goes_through_and_its_token_never_comes_back(client, local, env,
                                                                                            proofpane):
    # Proofpane's core: the app passes the code, the address and the holds to the workspace, which keeps the
    # device's token in its own record; neither answer here holds it
    h = signed_up(client)
    _started(client, h, env)
    st = client.get("/api/ade/workspace/proofpane", headers=h)
    assert st.status_code == 200 and st.json()["paired"] is False, st.text
    r = client.put("/api/ade/workspace/proofpane/pair", headers=h,
                   json={"code": "ZZZ-ZZZ-ZZZ", "cloud_url": proofpane})
    assert r.status_code == 400 and "Invalid pairing code" in r.json()["detail"]
    r = client.put("/api/ade/workspace/proofpane/pair", headers=h,
                   json={"code": CODE, "cloud_url": proofpane, "holds": ["^deploy"]})
    assert r.status_code == 200 and r.json()["paired"] is True and r.json()["holds"] == ["^deploy"], r.text
    assert TOKEN not in r.text and TOKEN not in client.get("/api/ade/workspace/proofpane", headers=h).text
    kept = json.loads((env / "home" / ".proofpane" / "proofpane-core" / "config.json").read_text())
    assert kept["token"] == TOKEN
    r = client.put("/api/ade/workspace/proofpane/holds", headers=h, json={"holds": []})
    assert r.status_code == 200 and r.json()["holds"] == []
    assert client.put("/api/ade/workspace/proofpane/holds", headers=h, json={"holds": ["x"] * 21}).status_code == 422
    assert client.delete("/api/ade/workspace/proofpane/pair", headers=h).json()["paired"] is False
    assert not (env / "home" / ".proofpane" / "proofpane-core" / "config.json").exists()


def test_the_agent_s_secret_is_not_in_the_environment_it_started_with(client, local, env):
    # its sessions' programs run as its user, and could read that environment (ps -E on a Mac, /proc/PID/environ on
    # Linux) even after the agent removed the secret from its own: it comes in a file the agent reads and deletes
    from proofpane_open import auth
    from proofpane_open.ade import workspaces
    h = signed_up(client)
    root, _ = _started(client, h, env)
    user = auth.user(_user_id(client, h))
    secret = workspaces.for_user(user).endpoint(user).secret
    pid = json.loads((root / "agent.json").read_text())["pid"]
    if sys.platform.startswith("linux"):
        try:
            with open(f"/proc/{pid}/environ", "rb") as fh:
                started_with = fh.read().decode("utf-8", "replace")
        except PermissionError:
            started_with = None         # closed by the agent (keep_memory_private): nothing to read at all
    else:
        started_with = subprocess.run(["ps", "-E", "-ww", "-o", "command=", "-p", str(pid)], capture_output=True,
                                      text=True, check=True).stdout
        assert "PATH=" in started_with          # the environment it started with is there to read
    assert started_with is None or secret not in started_with
    assert [p for p in os.listdir(root) if p.startswith("secret-")] == []
    assert client.get("/api/ade/workspace/sessions", headers=h).status_code == 200      # and the agent has it


def test_a_secret_file_an_earlier_start_left_goes_before_the_next_start(client, local, env):
    """The app and its agent both ended before the agent read its secret file (a crash, a kill -9), so nothing deleted
    it, and it holds the key the runs' records are signed with, the same at every start. The next start removes it
    before it writes its own; a file of the root not named as the start names one stays."""
    h = signed_up(client)
    root = env / "workspaces" / _user_id(client, h)
    root.mkdir(parents=True, mode=0o700)
    left = root / "secret-0123456789abcdef"
    left.write_text("s" * 43 + "\nrecords-key=" + "ab" * 32)
    others = [root / name for name in ("secret-notes", "secret-0123456789abcdeg", "secret-0123456789abcdef0",
                                       "my-secret-0123456789abcdef")]
    for f in others:
        f.write_text("not the start's")
    _started(client, h, env)
    assert not left.exists()
    assert [f.name for f in others if not f.exists()] == []


def test_start_stop_and_the_routes_need_it_running(client, local, env):
    h = signed_up(client)
    assert client.get("/api/ade/workspace", headers=h).json()["running"] is False
    assert client.get("/api/ade/workspace/sessions", headers=h).status_code == 409
    root, _ = _started(client, headers=h, env=env)
    assert oct(os.stat(root).st_mode & 0o777) == "0o700"
    assert client.post("/api/ade/workspace/start", headers=h).json()["running"] is True   # already running: the same
    assert client.post("/api/ade/workspace/stop", headers=h).json()["running"] is False
    assert client.get("/api/ade/workspace/repos", headers=h).status_code == 409


def test_a_workspace_restarts_with_a_new_secret(client, local, env):
    h = signed_up(client)
    _started(client, h, env)
    user = client.get("/api/auth/me", headers=h).json()
    first = local.provider().endpoint(user)
    client.post("/api/ade/workspace/stop", headers=h)
    client.post("/api/ade/workspace/start", headers=h)
    second = local.provider().endpoint(user)
    assert first.secret != second.secret


def test_a_workspace_s_run_records_hold_across_its_restarts_whose_secrets_differ(client, local, env, tmp_path,
                                                                                 monkeypatch):
    """Its runs' records are signed with a key the app gives it at each start, the same each time (agents.py): a
    record written before a restart is read as the workspace's own after it."""
    from proofpane_open.ade import workspace_client
    stand_in = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_stand_in.py")
    bin_dir = tmp_path / "standin-bin"                  # its run's claude: the stand-in, never a real one
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(f"#!{sys.executable}\n" + open(stand_in).read())
    (bin_dir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    h = signed_up(client)
    _started(client, h, env)
    user = client.get("/api/auth/me", headers=h).json()
    endpoint = local.provider().endpoint(user)
    agent = {"id": "a1a2a3a4a5a6", "name": "Triage", "brief": "hello", "repo": "demo"}
    workspace_client.call(endpoint, "PUT", f"/agents/{agent['id']}", agent)
    made = workspace_client.call(endpoint, "POST", f"/agents/{agent['id']}/runs", {"trigger": {"kind": "manual"}})
    client.post("/api/ade/workspace/stop", headers=h)
    client.post("/api/ade/workspace/start", headers=h)
    again = local.provider().endpoint(user)
    assert again.secret != endpoint.secret
    (got,) = [r for r in workspace_client.call(again, "GET", "/agents/runs?since=0")["runs"]
              if r["run_id"] == made["run_id"]]
    assert got["unverified"] is False and got["cause"] != "record"


def test_repositories_and_sessions_pass_through(client, local, env):
    h = signed_up(client)
    root, first = _started(client, h, env)
    assert [r["label"] for r in client.get("/api/ade/workspace/repos", headers=h).json()["repos"]] == ["acme/demo"]
    s = _session(client, h)
    assert s["base"] == first and s["running"] is True
    assert [x["id"] for x in client.get("/api/ade/workspace/sessions", headers=h).json()["sessions"]] == [s["id"]]
    r = client.post("/api/ade/workspace/repos", headers=h, json={"url": "file:///etc"})
    assert r.status_code == 400 and "https" in r.json()["detail"]
    r = client.post("/api/ade/workspace/sessions", headers=h, json={"repo": "demo", "program": "vim", "task": "t"})
    assert r.status_code == 400 and "claude, codex, gemini, opencode, grok, qwen, kimi, mcode, a shell, a chat, or" in r.json()["detail"]


def test_a_session_is_brought_up_to_date_through_the_app_as_its_owner(client, local, env):
    h = signed_up(client)
    root, _ = _started(client, h, env)
    repo = root / "repos" / "demo"
    # no origin: the session follows the repository's own main (a clone's follows origin's, fetched: no network here)
    git("remote", "remove", "origin", cwd=str(repo))
    s = _session(client, h)
    (repo / "upstream.txt").write_text("theirs\n")
    git("add", "-A", cwd=str(repo))
    git("commit", "-q", "-m", "upstream", cwd=str(repo))
    status = client.get(f"/api/ade/workspace/sessions/{s['id']}/update", headers=h).json()
    assert (status["behind"], status["merging"]) == (1, None)
    (root / "sessions" / s["id"] / "work" / "mine.txt").write_text("mine\n")
    r = client.post(f"/api/ade/workspace/sessions/{s['id']}/update", headers=h, json={})
    assert r.status_code == 200 and r.json()["updated"] is True, r.text
    # the work it had not committed was committed for the owner, who asked for the update
    work = str(root / "sessions" / s["id"] / "work")
    assert git("log", "-1", "--skip=1", "--format=%ae", cwd=work) == ADMIN
    assert client.post(f"/api/ade/workspace/sessions/{s['id']}/update/abort", headers=h).status_code == 400


def test_a_long_review_in_any_script_reaches_the_workspace_whole(client, local, env, tmp_path, monkeypatch):
    # the app passes a request on as UTF-8: a review's 15,000 characters fit the agent's 64 KB whatever they are
    # (escaped, a character took 6 to 12 bytes, and a long review in another script was refused)
    import sys as _sys
    bin_dir = tmp_path / "ghbin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(f"#!{_sys.executable}\nimport os, sys\nargs = sys.argv[1:]\n"
                                "here = os.path.dirname(os.path.abspath(__file__))\n"
                                "if args[:2] == ['pr', 'review']:\n"
                                "    open(os.path.join(here, 'body.txt'), 'w', encoding='utf-8').write(sys.stdin.read())\n"
                                "else:\n    sys.exit(2)\n")
    (bin_dir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    h = signed_up(client)
    root, _ = _started(client, h, env)
    s = _session(client, h)
    path = root / "sessions" / s["id"] / "session.json"
    record = json.loads(path.read_text())
    record["pull"] = 9
    path.write_text(json.dumps(record))
    from proofpane_open.ade import workspace_routes
    workspace_routes._pull_recorded(_user_id(client, h), s["id"], "started", "https://github.com/acme/demo/pull/9")
    words = chr(0x1F600) * 15000
    r = client.post(f"/api/ade/workspace/sessions/{s['id']}/pr/review", headers=h,
                    json={"event": "comment", "body": words, "url": "https://github.com/acme/demo/pull/9"})
    assert r.status_code == 200, r.text[:300]
    assert (bin_dir / "body.txt").read_text(encoding="utf-8") == words


def test_a_repository_on_this_machine_is_added_through_the_app_and_taken_out_again(client, local, env, tmp_path):
    # the local provider's workspace is on the person's own machine: a repository they have is used where it is
    h = signed_up(client)
    root, _ = _started(client, h, env)
    mine = tmp_path / "elsewhere" / "parser"
    make_repo(str(mine), origin="https://github.com/acme/parser.git")
    r = client.post("/api/ade/workspace/repos/link", headers=h, json={"path": str(mine)})
    assert r.status_code == 200, r.text
    assert (r.json()["name"], r.json()["label"]) == ("parser", "acme/parser")
    listed = {x["name"]: x["linked"] for x in client.get("/api/ade/workspace/repos", headers=h).json()["repos"]}
    assert listed == {"demo": None, "parser": os.path.realpath(mine)}
    s = client.post("/api/ade/workspace/sessions", headers=h, json={"repo": "parser", "program": "shell", "task": "t"})
    assert s.status_code == 200, s.text
    assert client.delete("/api/ade/workspace/repos/parser", headers=h).status_code == 400       # it has a session
    assert client.delete(f"/api/ade/workspace/sessions/{s.json()['id']}", headers=h).status_code == 200
    assert client.delete("/api/ade/workspace/repos/parser", headers=h).json() == {"unlinked": "parser"}
    assert (mine / "README.md").exists()
    assert client.delete("/api/ade/workspace/repos/de..mo", headers=h).status_code == 404
    r = client.post("/api/ade/workspace/repos/link", headers=h, json={"path": "x" * 5000})
    assert r.status_code == 422


@pytest.mark.parametrize("sid", ["aaaa", "AAAAAAAAAAAA", "aaaaaaaaaaaa%2F..%2Frepos", "..%2Frepos"])
def test_a_session_id_is_checked_before_it_reaches_the_workspace(client, local, env, sid, monkeypatch):
    from proofpane_open.ade import workspace_client
    h = signed_up(client)
    _started(client, h, env)
    calls = []
    monkeypatch.setattr(workspace_client, "call", lambda *a, **k: calls.append(a) or {})
    for method, suffix in (("GET", ""), ("DELETE", ""), ("POST", "/start"), ("POST", "/stop"), ("POST", "/finish"),
                           ("POST", "/close"), ("POST", "/open")):
        r = client.request(method, f"/api/ade/workspace/sessions/{sid}{suffix}", headers=h)
        assert r.status_code in (404, 405), (sid, suffix, r.status_code)
    assert calls == []


def test_finish_verifies_and_signs_the_change(client, local, env):
    from proofpane_open import tools
    h = signed_up(client)
    root, first = _started(client, h, env)
    s = _session(client, h)
    work = root / "sessions" / s["id"] / "work"
    (work / "app.py").write_text("def f():\n    return 2\n")
    r = client.post(f"/api/ade/workspace/sessions/{s['id']}/finish", headers=h)
    assert r.status_code == 200, r.text
    got = r.json()
    head = git("rev-parse", "HEAD", cwd=str(work))
    assert got["status"] == "verified" and got["committed"] is True and got["files"] == 1
    assert got["base"] == first and got["head"] == head and got["repo"] == "acme/demo"
    assert got["branch"] == f"pp/{s['id']}" and got["digest"] and got["task"].startswith("Return two")
    assert git("log", "-1", "--format=%ae", cwd=str(work)) == ADMIN
    full = client.get(f"/api/ade/workspace/finishes/{got['id']}", headers=h).json()
    subject = full["manifest"]["subject"]
    assert subject["repo"] == "acme/demo" and subject["head_sha"] == head and subject["base_sha"] == first
    assert subject["claims_digest"] == tools.core_manifest.claims_digest("Return two", "f now returns 2.")
    assert tools.check_signature(full["manifest"], full["signature"])["ok"] is True
    listed = client.get("/api/ade/workspace/finishes", headers=h).json()["finishes"]
    assert [f["id"] for f in listed] == [got["id"]] and "manifest" not in listed[0]
    assert client.get(f"/api/ade/workspace/finishes?session={s['id']}", headers=h).json()["finishes"][0]["id"] == \
        got["id"]


def test_a_sessions_changes_pass_through_for_review(client, local, env):
    h = signed_up(client)
    root, first = _started(client, h, env)
    s = _session(client, h)
    work = root / "sessions" / s["id"] / "work"
    (work / "app.py").write_text("def f():\n    return 2\n")
    (work / "notes.md").write_text("new\n")
    r = client.get(f"/api/ade/workspace/sessions/{s['id']}/diff", headers=h)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["base"] == first and got["branch"] == f"pp/{s['id']}"
    assert sorted(f["path"] for f in got["files"]) == ["app.py", "notes.md"]
    assert "+    return 2" in got["diff"]
    assert client.get(f"/api/ade/workspace/sessions/{s['id']}/diff").status_code == 401
    other = signed_up(client, OTHER_ADMIN)
    assert client.get(f"/api/ade/workspace/sessions/{s['id']}/diff", headers=other).status_code in (404, 409)


def test_a_sessions_files_pass_through_read_only(client, local, env):
    h = signed_up(client)
    root, _ = _started(client, h, env)
    s = _session(client, h)
    (root / "sessions" / s["id"] / "work" / "app.py").write_text("def f():\n    return 2\n")
    listed = client.get(f"/api/ade/workspace/sessions/{s['id']}/files", headers=h)
    assert listed.status_code == 200, listed.text
    assert "app.py" in [e["name"] for e in listed.json()["entries"]]
    got = client.get(f"/api/ade/workspace/sessions/{s['id']}/file", params={"path": "app.py"}, headers=h)
    assert got.status_code == 200 and got.json()["text"] == "def f():\n    return 2\n"
    assert client.get(f"/api/ade/workspace/sessions/{s['id']}/file", params={"path": "../x"}, headers=h).status_code == 400
    assert client.get(f"/api/ade/workspace/sessions/{s['id']}/files").status_code == 401


def test_a_finish_with_no_change_is_skipped_not_passed(client, local, env):
    h = signed_up(client)
    _started(client, h, env)
    s = _session(client, h)
    got = client.post(f"/api/ade/workspace/sessions/{s['id']}/finish", headers=h).json()
    assert got["status"] == "skipped" and "no change" in got["reason"] and got["digest"] is None


def test_another_account_sees_none_of_the_finishes(client, local, env):
    h = signed_up(client)
    root, _ = _started(client, h, env)
    s = _session(client, h)
    (root / "sessions" / s["id"] / "work" / "x.txt").write_text("x\n")
    fid = client.post(f"/api/ade/workspace/sessions/{s['id']}/finish", headers=h).json()["id"]
    other = signed_up(client, OTHER_ADMIN)
    assert client.get(f"/api/ade/workspace/finishes/{fid}", headers=other).status_code == 404
    assert client.get("/api/ade/workspace/finishes", headers=other).json()["finishes"] == []
    assert client.post("/api/ade/workspace/start", headers=other).status_code == 200
    # the other account's workspace has no such session
    assert client.get(f"/api/ade/workspace/sessions/{s['id']}", headers=other).status_code == 404


def _report(**over):
    r = {"session": "a" * 12, "branch": "pp/" + "a" * 12, "base": "1" * 40, "head": "2" * 40, "label": "acme/demo",
         "repo": "demo", "task": "t", "too_large": False, "diff": ""}
    r.update(over)
    return r


@pytest.mark.parametrize("over", [
    {"session": "../x"}, {"branch": "main"}, {"base": "1" * 39}, {"head": "HEAD"}, {"label": "acme demo"},
    {"label": "acme/demo/x"}, {"label": 3}, {"repo": ""}, {"task": ""}, {"task": "t" * 4001}, {"too_large": "no"},
    {"diff": None}, {"diff": 3}])
def test_a_report_not_in_the_expected_form_is_refused(over):
    from proofpane_open.ade import finishes
    finishes.check_report(_report())
    with pytest.raises(finishes.BadReport):
        finishes.check_report(_report(**over))


def test_a_report_too_large_is_skipped():
    from proofpane_open.ade import finishes
    rec = finishes.verify_report(_report(too_large=True, diff=None))
    assert rec["status"] == "skipped" and "larger than" in rec["reason"]


# the terminal, through the app on a real server


@pytest.fixture
def served(client, local):
    import uvicorn
    from proofpane_open.main import app
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_level="warning",
                                           ws="websockets"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started:
        assert time.time() < deadline
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield port
    server.should_exit = True
    thread.join(10)


def _terminal(port, origin=None):
    headers = {"Origin": origin} if origin else {}
    return connect(f"ws://127.0.0.1:{port}/api/ade/workspace/terminal", additional_headers=headers, open_timeout=10)


def _token(headers):
    return headers["Authorization"][len("Bearer "):]


def test_the_terminal_passes_through_the_app(client, served, env):
    h = signed_up(client)
    _started(client, h, env)
    s = _session(client, h)
    with _terminal(served, origin=f"http://127.0.0.1:{served}") as ws:
        ws.send(json.dumps({"token": _token(h), "session": s["id"], "cols": 90, "rows": 33}))
        assert next_control(ws) == {"type": "ready"}
        ws.send(b"stty size; echo via-app-$((6+1))\n")
        out = read_until(ws, b"via-app-7")
        assert b"33 90" in out
        ws.send(json.dumps({"type": "resize", "cols": 101, "rows": 40}))
        ws.send(b"stty size; echo again-$((6+2))\n")
        assert b"40 101" in read_until(ws, b"again-8")
        ws.send(b"exit 5\n")
        assert next_control(ws) == {"type": "exit", "code": 5}


def test_a_session_is_renamed_through_the_app(client, local, env):
    h = signed_up(client)
    _started(client, h, env)
    s = _session(client, h)
    r = client.post(f"/api/ade/workspace/sessions/{s['id']}/title", headers=h, json={"title": "Parser work"})
    assert r.status_code == 200 and r.json()["title"] == "Parser work", r.text
    r = client.post(f"/api/ade/workspace/sessions/{s['id']}/title", headers=h, json={"title": "x" * 121})
    assert r.status_code == 400 and "at most 120" in r.json()["detail"]


def test_a_session_is_closed_and_opened_again_through_the_app(client, local, env):
    """A close stops the session's program and marks it closed, an open takes the mark away; the app passes
    both to the workspace, and the session stays in the workspace's list all along."""
    h = signed_up(client)
    _started(client, h, env)
    s = _session(client, h)
    r = client.post(f"/api/ade/workspace/sessions/{s['id']}/close", headers=h)
    assert r.status_code == 200 and r.json()["closed"] is True and r.json()["running"] is False, r.text
    listed = {v["id"]: v for v in client.get("/api/ade/workspace/sessions", headers=h).json()["sessions"]}
    assert listed[s["id"]]["closed"] is True
    r = client.post(f"/api/ade/workspace/sessions/{s['id']}/open", headers=h)
    assert r.status_code == 200 and not r.json().get("closed"), r.text


def test_a_worktree_is_searched_through_the_app(client, local, env):
    h = signed_up(client)
    root, _ = _started(client, h, env)
    s = _session(client, h)
    (root / "sessions" / s["id"] / "work" / "notes.md").write_text("one\nTwo & three?\n")
    r = client.get(f"/api/ade/workspace/sessions/{s['id']}/search", headers=h, params={"q": "two & three?"})
    assert r.status_code == 200, r.text
    assert [(m["path"], m["line"]) for m in r.json()["matches"]] == [("notes.md", 2)]
    r = client.get(f"/api/ade/workspace/sessions/{s['id']}/search", headers=h,
                   params={"q": "two & three?", "case": "true"})
    assert r.status_code == 200 and r.json()["matches"] == []
    r = client.get(f"/api/ade/workspace/sessions/{s['id']}/search", headers=h, params={"q": "^T.o", "regex": "true"})
    assert [(m["path"], m["line"]) for m in r.json()["matches"]] == [("notes.md", 2)]


def test_a_file_is_saved_through_the_app_and_a_conflict_says_so(client, local, env):
    h = signed_up(client)
    root, _ = _started(client, h, env)
    s = _session(client, h)
    work = root / "sessions" / s["id"] / "work"
    opened = client.get(f"/api/ade/workspace/sessions/{s['id']}/file", headers=h, params={"path": "README.md"}).json()
    r = client.put(f"/api/ade/workspace/sessions/{s['id']}/file", headers=h,
                   json={"path": "README.md", "text": "edited on the page\n", "base": opened["sha"]})
    assert r.status_code == 200 and r.json()["text"] == "edited on the page\n", r.text
    assert (work / "README.md").read_text() == "edited on the page\n"
    r = client.put(f"/api/ade/workspace/sessions/{s['id']}/file", headers=h,
                   json={"path": "README.md", "text": "again\n", "base": opened["sha"]})
    assert r.status_code == 412 and "changed since you opened it" in r.json()["detail"]
    assert (work / "README.md").read_text() == "edited on the page\n"


def test_an_image_passes_through_the_app_so_that_nothing_in_it_can_run(client, local, env):
    h = signed_up(client)
    root, _ = _started(client, h, env)
    s = _session(client, h)
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    (root / "sessions" / s["id"] / "work" / "logo.svg").write_text(svg)
    r = client.get(f"/api/ade/workspace/sessions/{s['id']}/image", headers=h, params={"path": "logo.svg"})
    assert r.status_code == 200 and r.text == svg and r.headers["content-type"].startswith("image/svg+xml")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-store"
    assert "sandbox" in r.headers["content-security-policy"] and "default-src 'none'" in r.headers["content-security-policy"]
    pdf = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"
    (root / "sessions" / s["id"] / "work" / "report.pdf").write_bytes(pdf)
    r = client.get(f"/api/ade/workspace/sessions/{s['id']}/image", headers=h, params={"path": "report.pdf"})
    assert r.status_code == 200 and r.content == pdf and r.headers["content-type"] == "application/pdf"
    assert "sandbox" in r.headers["content-security-policy"] and r.headers["x-content-type-options"] == "nosniff"
    r = client.get(f"/api/ade/workspace/sessions/{s['id']}/image", headers=h, params={"path": "README.md"})
    assert r.status_code == 400 and "Only an image or a PDF" in r.json()["detail"]


def test_a_sessions_shell_passes_through_the_app(client, served, env):
    h = signed_up(client)
    root, _ = _started(client, h, env)
    s = _session(client, h)
    origin = f"http://127.0.0.1:{served}"
    with _terminal(served, origin=origin) as ws:        # none open yet: the agent says so, through the app
        ws.send(json.dumps({"token": _token(h), "session": s["id"], "shell": 1}))
        msg = next_control(ws)
        assert msg["type"] == "error" and "no such shell open" in msg["detail"]
    r = client.post(f"/api/ade/workspace/sessions/{s['id']}/shell", headers=h, json={"cols": 88, "rows": 31})
    assert r.status_code == 200 and r.json()["opened"] == 1 and r.json()["shells"][0]["running"] is True, r.text
    with _terminal(served, origin=origin) as ws:
        ws.send(json.dumps({"token": _token(h), "session": s["id"], "cols": 88, "rows": 31, "shell": 1}))
        assert next_control(ws) == {"type": "ready"}
        ws.send(b"stty size; echo shell-$((7*6)) > from-shell.txt; cat from-shell.txt\n")
        out = read_until(ws, b"shell-42")
        assert b"31 88" in out
    assert (root / "sessions" / s["id"] / "work" / "from-shell.txt").read_text() == "shell-42\n"
    with _terminal(served, origin=origin) as ws:        # without "shell", the session's program, as before
        ws.send(json.dumps({"token": _token(h), "session": s["id"]}))
        assert next_control(ws) == {"type": "ready"}
        ws.send(b"echo program-$((2+3))\n")
        assert b"shell-42" not in read_until(ws, b"program-5")
    r = client.post(f"/api/ade/workspace/sessions/{s['id']}/shell/1/stop", headers=h)
    assert r.status_code == 200 and r.json()["shells"][0]["running"] is False and r.json()["running"] is True
    assert client.post(f"/api/ade/workspace/sessions/{s['id']}/shell/5/stop", headers=h).status_code == 404
    with _terminal(served, origin=origin) as ws:        # a shell number past the four is no shell
        ws.send(json.dumps({"token": _token(h), "session": s["id"], "shell": 5}))
        msg = next_control(ws)
        assert msg["type"] == "error" and "No such shell" in msg["detail"]
    r = client.delete(f"/api/ade/workspace/sessions/{s['id']}/shell/1", headers=h)
    assert r.status_code == 200 and r.json()["shells"] == []


@pytest.mark.parametrize("hello", [None, "not json", [], {"token": "nope", "session": "a" * 12},
                                   {"session": "a" * 12},
                                   "[" * 4000 + "]" * 4000,     # nested past what a reader's stack takes, but short
                                   {"token": "x" * 9000, "session": "a" * 12}])        # longer than a sign-in is
def test_the_terminal_needs_a_signed_in_first_message(client, served, env, hello):
    with _terminal(served) as ws:
        if hello is not None:
            ws.send(hello if isinstance(hello, str) else json.dumps(hello))
        assert next_control(ws, timeout=20) == {"type": "error", "detail": "Sign in first."}


def _wait_for(check, timeout=20):
    deadline = time.time() + timeout
    while True:
        got = check()
        if got:
            return got
        assert time.time() < deadline
        time.sleep(0.1)


def test_a_slow_start_goes_on_while_the_page_watches(client, local, monkeypatch):
    # a phone drops a request held open for a minute ("Load failed"): the start answers soon
    # and goes on in the background, and the status says so until the workspace runs
    from proofpane_open.ade import workspace_routes
    monkeypatch.setattr(workspace_routes, "START_IN_REQUEST", 0.2)
    calls = []
    real = local.LocalProvider.ensure

    def slow(self, user):
        calls.append(user["id"])
        time.sleep(1.5)
        return real(self, user)

    monkeypatch.setattr(local.LocalProvider, "ensure", slow)
    h = signed_up(client)
    first = client.post("/api/ade/workspace/start", headers=h).json()
    second = client.post("/api/ade/workspace/start", headers=h).json()          # a second press joins the first
    assert first["starting"] is True and first["running"] is False and second["starting"] is True
    assert client.get("/api/ade/workspace", headers=h).json()["starting"] is True
    done = _wait_for(lambda: (lambda st: st if st["running"] else None)(
        client.get("/api/ade/workspace", headers=h).json()))
    assert "starting" not in done and "start_error" not in done and len(calls) == 1


def test_while_a_start_waits_for_its_agent_the_workspace_is_not_said_to_run(client, local, served, monkeypatch):
    # a machine can be up two seconds before its agent listens; the page's start watch then sees "running", reads the
    # sessions, and says "The workspace answered 502". Until the start has seen its agent answer
    # (fly_provider.wait_healthy), the status says starting, not running, and nothing is sent to the agent.
    from proofpane_open import auth
    from proofpane_open.ade import workspace_routes
    monkeypatch.setattr(workspace_routes, "START_IN_REQUEST", 0.2)
    up, go_on = threading.Event(), threading.Event()
    real = local.LocalProvider.ensure

    def waits_for_its_agent(self, user):
        got = real(self, user)          # the machine is up, and the provider says it runs
        up.set()
        assert go_on.wait(30)           # the start still waits for its agent to answer
        return got

    monkeypatch.setattr(local.LocalProvider, "ensure", waits_for_its_agent)
    h = signed_up(client)
    first = client.post("/api/ade/workspace/start", headers=h).json()
    assert first["starting"] is True and first["running"] is False, first
    assert up.wait(30)
    assert local.provider().status(auth.user(_user_id(client, h)))["running"] is True
    st = client.get("/api/ade/workspace", headers=h).json()
    assert st["starting"] is True and st["running"] is False, st
    again = client.post("/api/ade/workspace/start", headers=h).json()          # a second press joins the first
    assert again["starting"] is True and again["running"] is False, again
    for method, path in (("GET", "/sessions"), ("GET", "/repos"), ("GET", "/claude")):
        r = client.request(method, "/api/ade/workspace" + path, headers=h)
        assert r.status_code == 409 and r.json()["detail"] == workspace_routes.STARTING, (path, r.text)
    with _chat_socket(served, _token(h)) as ws:
        assert next_control(ws, timeout=20) == {"type": "error", "detail": workspace_routes.STARTING}
    with _terminal(served, origin=f"http://127.0.0.1:{served}") as ws:
        ws.send(json.dumps({"token": _token(h), "session": "a" * 12, "cols": 80, "rows": 24}))
        assert next_control(ws, timeout=20) == {"type": "error", "detail": workspace_routes.STARTING}
    go_on.set()
    done = _wait_for(lambda: (lambda st: st if st["running"] else None)(
        client.get("/api/ade/workspace", headers=h).json()))
    assert "starting" not in done and "start_error" not in done, done
    r = client.get("/api/ade/workspace/sessions", headers=h)
    assert r.status_code == 200 and r.json()["sessions"] == [], r.text


def test_a_start_that_fails_in_the_background_says_why(client, local, monkeypatch):
    from proofpane_open.ade import workspace_routes
    monkeypatch.setattr(workspace_routes, "START_IN_REQUEST", 0.2)

    def fails(self, user):
        time.sleep(0.6)
        raise local.WorkspaceAccess(504, "The workspace machine did not start in time.")

    monkeypatch.setattr(local.LocalProvider, "ensure", fails)
    h = signed_up(client)
    assert client.post("/api/ade/workspace/start", headers=h).json()["starting"] is True
    st = _wait_for(lambda: (lambda st: st if "start_error" in st else None)(
        client.get("/api/ade/workspace", headers=h).json()))
    assert st["start_error"] == "The workspace machine did not start in time." and st["running"] is False

    def fails_at_once(self, user):
        raise local.WorkspaceAccess(502, "Fly: boom")

    monkeypatch.setattr(local.LocalProvider, "ensure", fails_at_once)
    r = client.post("/api/ade/workspace/start", headers=h)                     # within the wait: said at once
    assert r.status_code == 502 and r.json()["detail"] == "Fly: boom"


def test_a_program_that_ended_before_its_terminal_opened_is_shown_ending(client, served, env, monkeypatch):
    # seen on a phone: the agent says ready, hands over the ended program's output and status and
    # closes; the app then sent the window size to a closed socket, raised, and the page said only "Disconnected."
    import asyncio
    from proofpane_open.ade import workspace_client, workspace_routes
    h = signed_up(client)
    _started(client, h, env)
    s = _session(client, h)
    with _terminal(served, origin=f"http://127.0.0.1:{served}") as ws:
        ws.send(json.dumps({"token": _token(h), "session": s["id"]}))
        assert next_control(ws) == {"type": "ready"}
        ws.send(b"echo bye-$((2+2)); exit 4\n")
        assert next_control(ws) == {"type": "exit", "code": 4}
    real = workspace_client.open_terminal

    async def after_the_agent_closed(endpoint, sid, **kw):
        agent = await real(endpoint, sid, **kw)
        await asyncio.sleep(0.5)                  # the agent has closed its side by now, every time
        return agent

    monkeypatch.setattr(workspace_routes.client, "open_terminal", after_the_agent_closed)
    with _terminal(served, origin=f"http://127.0.0.1:{served}") as ws:
        ws.send(json.dumps({"token": _token(h), "session": s["id"], "cols": 90, "rows": 33}))
        assert next_control(ws) == {"type": "ready"}
        assert b"bye-4" in read_until(ws, b"bye-4")
        assert next_control(ws) == {"type": "exit", "code": 4}


def test_a_terminal_that_cannot_open_says_so(client, served, env, monkeypatch):
    from proofpane_open.ade import workspace_routes
    h = signed_up(client)
    _started(client, h, env)
    s = _session(client, h)

    async def broken(endpoint, sid, **kw):
        raise RuntimeError("something nobody expected")

    monkeypatch.setattr(workspace_routes.client, "open_terminal", broken)
    with _terminal(served, origin=f"http://127.0.0.1:{served}") as ws:
        ws.send(json.dumps({"token": _token(h), "session": s["id"]}))
        msg = next_control(ws)
        assert msg["type"] == "error" and "could not open" in msg["detail"] and "nobody" not in msg["detail"]


def test_a_sign_in_check_that_fails_to_answer_ends_nothing(client, served, env, monkeypatch):
    # only a definite "signed out" closes an open terminal; a check that raised (a locked database) is not one
    import sqlite3
    from proofpane_open.ade import workspace_routes
    monkeypatch.setattr(workspace_routes, "SIGNIN_CHECK_EVERY", 0.2)
    h = signed_up(client)
    _started(client, h, env)
    s = _session(client, h)
    real = workspace_routes._hello_user
    calls = []

    def flaky(token):
        calls.append(token)
        if len(calls) > 1:
            raise sqlite3.OperationalError("database is locked")
        return real(token)

    monkeypatch.setattr(workspace_routes, "_hello_user", flaky)
    with _terminal(served, origin=f"http://127.0.0.1:{served}") as ws:
        ws.send(json.dumps({"token": _token(h), "session": s["id"]}))
        assert next_control(ws) == {"type": "ready"}
        time.sleep(1.2)                               # several checks, every one of them raising
        ws.send(b"echo alive-$((3+4))\n")
        assert b"alive-7" in read_until(ws, b"alive-7")
    assert len(calls) > 3


def test_signing_out_everywhere_closes_a_terminal_already_open(client, served, env, monkeypatch):
    # a lost device's open terminal (and what gh holds there) does not outlive the sign-out
    from proofpane_open.ade import workspace_routes
    monkeypatch.setattr(workspace_routes, "SIGNIN_CHECK_EVERY", 0.3)
    h = signed_up(client)
    _started(client, h, env)
    s = _session(client, h)
    with _terminal(served, origin=f"http://127.0.0.1:{served}") as ws:
        ws.send(json.dumps({"token": _token(h), "session": s["id"]}))
        assert next_control(ws) == {"type": "ready"}
        ws.send(b"echo still-$((1+1))\n")
        read_until(ws, b"still-2")
        time.sleep(1)                                     # a few checks pass while the sign-in holds
        ws.send(b"echo again-$((2+2))\n")
        read_until(ws, b"again-4")
        other = client.post("/api/auth/login", json={"email": ADMIN, "password": "correct horse battery"})
        elsewhere = {"Authorization": "Bearer " + other.json()["token"]}
        confirmed = client.post("/api/account/confirm", headers=elsewhere,
                                json={"password": "correct horse battery", "change": "sign-out-everywhere"})
        client.post("/api/account/sign-out-everywhere", headers=elsewhere, json=confirmed.json())
        msg = next_control(ws, timeout=10)
        assert msg["type"] == "error" and "Signed out everywhere" in msg["detail"]
        with pytest.raises(ConnectionClosed) as closed:
            ws.recv(timeout=10)
        assert closed.value.rcvd is not None and closed.value.rcvd.code == 1008


def test_the_terminal_refuses_another_site(client, served):
    with pytest.raises(InvalidStatus):
        _terminal(served, origin="https://evil.example")


def test_the_phone_app_s_pages_open_a_terminal_and_call_the_api_and_another_site_does_neither(client, served, env,
                                                                                               monkeypatch):
    h = signed_up(client)
    _started(client, h, env)
    s = _session(client, h)
    with _terminal(served, origin="tauri://localhost") as ws:        # the iOS app's own pages
        ws.send(json.dumps({"token": _token(h), "session": s["id"]}))
        assert next_control(ws) == {"type": "ready"}
    ask = {"Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"}
    for origin in ("tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"):
        pre = client.options("/api/auth/me", headers=dict(ask, Origin=origin))
        assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == origin
        assert "access-control-allow-credentials" not in pre.headers          # no cookies, only the token
        got = client.get("/api/auth/me", headers=dict(h, Origin=origin))
        assert got.status_code == 200 and got.headers["access-control-allow-origin"] == origin
    pre = client.options("/api/auth/me", headers=dict(ask, Origin="https://evil.example"))
    assert pre.status_code == 400 and "access-control-allow-origin" not in pre.headers
    assert "access-control-allow-origin" not in client.get("/api/auth/me", headers=dict(h, Origin="https://evil.example")).headers
    # named none, the app's pages are another site too
    monkeypatch.setenv("OPEN_EDITION_APP_ORIGINS", "")
    with pytest.raises(InvalidStatus):
        _terminal(served, origin="tauri://localhost")


def test_the_terminal_reaches_only_the_accounts_own_sessions(client, served, env):
    h = signed_up(client)
    _started(client, h, env)
    s = _session(client, h)
    other = signed_up(client, OTHER_ADMIN)
    assert client.post("/api/ade/workspace/start", headers=other).status_code == 200
    with _terminal(served) as ws:
        ws.send(json.dumps({"token": _token(other), "session": s["id"]}))
        msg = next_control(ws)
        assert msg["type"] == "error" and "not running" in msg["detail"]
    with _terminal(served) as ws:
        ws.send(json.dumps({"token": _token(h), "session": "../../repos"}))
        assert next_control(ws) == {"type": "error", "detail": "No such session."}


def test_the_terminal_ends_on_a_message_it_does_not_know(client, served, env):
    h = signed_up(client)
    _started(client, h, env)
    s = _session(client, h)
    with _terminal(served) as ws:
        ws.send(json.dumps({"token": _token(h), "session": s["id"]}))
        assert next_control(ws) == {"type": "ready"}
        ws.send(json.dumps({"type": "exit", "code": 0}))
        with pytest.raises(ConnectionClosed):          # closed by the app, not a silence that times out
            for _ in range(50):
                ws.recv(timeout=10)
    got = client.get(f"/api/ade/workspace/sessions/{s['id']}", headers=h).json()
    assert got["running"] is True     # the message did not reach the program or the agent


def test_the_terminal_ends_on_a_resize_that_carries_anything_else(client, served, env):
    h = signed_up(client)
    _started(client, h, env)
    s = _session(client, h)
    with _terminal(served) as ws:
        ws.send(json.dumps({"token": _token(h), "session": s["id"]}))
        assert next_control(ws) == {"type": "ready"}
        ws.send(json.dumps({"type": "resize", "cols": 50, "rows": 20, "extra": 1}))
        with pytest.raises(ConnectionClosed):
            for _ in range(50):
                ws.recv(timeout=10)


def test_the_terminal_takes_the_token_only_from_the_first_message(client, served, env):
    h = signed_up(client)
    _started(client, h, env)
    s = _session(client, h)
    # a token in the address, which an access log would keep, signs nothing in
    with connect(f"ws://127.0.0.1:{served}/api/ade/workspace/terminal?token={_token(h)}", open_timeout=10) as ws:
        ws.send(json.dumps({"session": s["id"]}))
        assert next_control(ws, timeout=20) == {"type": "error", "detail": "Sign in first."}


def test_the_terminal_when_the_workspace_is_off(client, served, env, monkeypatch):
    h = signed_up(client)
    monkeypatch.delenv("OPEN_EDITION_ADE")
    with _terminal(served) as ws:
        ws.send(json.dumps({"token": _token(h), "session": "a" * 12}))
        msg = next_control(ws)
        assert msg["type"] == "error" and "off" in msg["detail"]


def _chat_socket(port, token=None, origin=None):
    ws = connect(f"ws://127.0.0.1:{port}/api/ade/workspace/chat",
                 additional_headers={"Origin": origin or f"http://127.0.0.1:{port}"}, open_timeout=10)
    if token is not None:
        ws.send(json.dumps({"token": token}))
    return ws


def _next_event(ws, session, kind, timeout=30, deltas=None):
    """The next logged event of `kind`; what claude wrote as it wrote it, on the way, goes in `deltas`."""
    deadline = time.time() + timeout
    while True:
        msg = json.loads(ws.recv(timeout=max(0.1, deadline - time.time())))
        if msg["type"] == "delta" and deltas is not None:
            deltas.append(msg)
            continue
        assert msg["type"] == "event", msg
        if msg["session"] == session and msg["event"]["kind"] == kind:
            return msg["event"]


def test_a_chat_goes_through_the_app_and_its_events_come_back_live(client, served, env, tmp_path, monkeypatch):
    from proofpane_workspace import chat as chats
    if not chats.available():
        pytest.skip("needs the Claude Agent SDK (requirements.txt)")
    import sys
    stand_in = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_stand_in.py")
    bin_dir = tmp_path / "chatbin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(f"#!{sys.executable}\n" + open(stand_in).read())
    (bin_dir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    h = signed_up(client)
    _started(client, h, env)
    with _chat_socket(served, _token(h)) as ws:
        assert next_control(ws) == {"type": "ready"}
        r = client.post("/api/ade/workspace/sessions", headers=h,
                        json={"repo": "demo", "program": "chat", "task": "run echo via-app-$((2+3))"})
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        deltas = []
        ask = _next_event(ws, sid, "ask", deltas=deltas)
        assert (ask["name"], ask["summary"]) == ("Bash", "echo via-app-$((2+3))")
        path = f"/api/ade/workspace/sessions/{sid}/chat"
        assert client.post(path + "/answer", json={"id": ask["id"], "decision": "allow"}).status_code == 401
        r = client.post(path + "/answer", headers=h, json={"id": ask["id"], "decision": "allow"})
        assert r.status_code == 200, r.text
        assert _next_event(ws, sid, "tool_end", deltas=deltas)["summary"] == "via-app-5"
        _next_event(ws, sid, "result", deltas=deltas)
        r = client.post(path, headers=h, json={"text": "hello again"})
        assert r.status_code == 200, r.text
        assert _next_event(ws, sid, "text", deltas=deltas)["text"] == "You said: hello again"
        # what claude wrote reached the page through the app as it wrote it, re-made from its known parts
        assert {"type": "delta", "session": sid, "delta": {"after": 3, "block": 1, "tool": "Bash"}} in deltas
        assert "You said: hello again" in "".join(d["delta"].get("text", "") for d in deltas)
        assert all(set(d["delta"]) <= {"after", "block", "offset", "text", "thinking", "tool"} for d in deltas)
        log = client.get(path, headers=h, params={"after": 0}).json()
        assert log["more"] is False and [e["seq"] for e in log["events"]] == list(range(1, len(log["events"]) + 1))
        assert client.get(path, params={"after": 0}).status_code == 401
        r = client.post(path + "/interrupt", headers=h)
        assert r.status_code in (200, 400)         # it may have finished already; either way the app passed it on


@pytest.mark.parametrize("hello", [None, "not json", [], {"token": "nope"}, {}])
def test_the_chat_socket_needs_a_signed_in_first_message(client, served, env, hello):
    with _chat_socket(served) as ws:
        if hello is not None:
            ws.send(hello if isinstance(hello, str) else json.dumps(hello))
        assert next_control(ws, timeout=20) == {"type": "error", "detail": "Sign in first."}


def test_a_page_on_another_site_gets_no_chat_socket(client, served, env):
    # a browser's socket comes with its page's origin: another site's page is closed before it can say anything
    h = signed_up(client)
    with pytest.raises((ConnectionClosed, InvalidStatus)):
        with _chat_socket(served, _token(h), origin="https://elsewhere.example") as ws:
            ws.recv(timeout=10)


def test_a_chat_s_settings_go_through_the_app_one_at_a_time(client, local, env, tmp_path, monkeypatch):
    # the model, the effort and the permission mode: chosen with the chat, changed while it
    # runs; the app passes on what is given, so a change of one leaves the others as they were
    from proofpane_workspace import chat as chats
    if not chats.available():
        pytest.skip("needs the Claude Agent SDK (requirements.txt)")
    import sys
    stand_in = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_stand_in.py")
    bin_dir = tmp_path / "chatbin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(f"#!{sys.executable}\n" + open(stand_in).read())
    (bin_dir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    h = signed_up(client)
    _started(client, h, env)
    r = client.post("/api/ade/workspace/sessions", headers=h,
                    json={"repo": "demo", "program": "chat", "task": "hello", "chat_settings": {"model": "sonnet"}})
    assert r.status_code == 200 and r.json()["chat_settings"] == {"model": "sonnet"}, r.text
    path = f"/api/ade/workspace/sessions/{r.json()['id']}/chat/settings"
    assert client.post(path, json={"effort": "low"}).status_code == 401
    assert client.post(path, headers=h, json={"permission_mode": "plan"}).json()["chosen"] == \
        {"model": "sonnet", "permission_mode": "plan"}
    assert client.post(path, headers=h, json={"effort": "low"}).json()["chosen"] == \
        {"model": "sonnet", "permission_mode": "plan", "effort": "low"}
    r = client.post(path, headers=h, json={"permission_mode": "dontAsk"})
    assert r.status_code == 400 and r.json()["detail"].startswith("A permission mode is one of"), r.text


def test_a_chat_s_mcp_servers_go_through_the_app(client, local, env, tmp_path, monkeypatch):
    # the repository's servers, approved on the page, and one added; the app passes on
    # what is given and the agent checks it (tests/test_workspace_mcp.py has the rest)
    from proofpane_workspace import chat as chats
    if not chats.available():
        pytest.skip("needs the Claude Agent SDK (requirements.txt)")
    import subprocess
    import sys
    stand_in = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_stand_in.py")
    bin_dir = tmp_path / "chatbin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(f"#!{sys.executable}\n" + open(stand_in).read())
    (bin_dir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    h = signed_up(client)
    root, _ = _started(client, h, env)
    repo = root / "repos" / "demo"
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"echo": {"command": "python3", "args": ["s.py"]}}}))
    subprocess.run(["git", "-C", str(repo), "add", ".mcp.json"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q",
                    "-m", "mcp"], check=True)
    r = client.post("/api/ade/workspace/sessions", headers=h, json={"repo": "demo", "program": "chat", "task": "hello"})
    path = f"/api/ade/workspace/sessions/{r.json()['id']}/chat/mcp"
    assert client.get(path).status_code == 401 and client.post(path, json={"action": "stop"}).status_code == 401
    view = client.get(path, headers=h).json()
    (echo,) = view["servers"]
    assert (echo["name"], echo["approved"], echo["line"]) == ("echo", False, "python3 s.py")
    got = client.post(path, headers=h, json={"action": "approve", "name": "echo", "fingerprint": echo["fingerprint"]})
    assert got.status_code == 200 and got.json()["servers"][0]["approved"] is True, got.text
    got = client.post(path, headers=h, json={"action": "add", "spec": {"name": "web", "scope": "workspace",
                                                                       "url": "https://e.org/mcp"}})
    assert got.status_code == 200, got.text
    got = client.post(path, headers=h, json={"action": "add", "spec": {"name": "old", "scope": "workspace",
                                                                       "url": "https://e.org/sse", "transport": "sse"}})
    assert got.status_code == 200, got.text
    kept = json.loads((tmp_path / "home" / ".claude.json").read_text())["mcpServers"]
    assert kept == {"web": {"type": "http", "url": "https://e.org/mcp"}, "old": {"type": "sse", "url": "https://e.org/sse"}}
    r = client.post(path, headers=h, json={"action": "add", "spec": {"name": "x", "scope": "everywhere", "command": "a"}})
    assert r.status_code == 400 and "this repository or for the whole workspace" in r.json()["detail"]
    assert client.post(path, headers=h, json={"action": "add", "spec": {"name": "x"}}).status_code == 422
    assert client.post(path, headers=h, json={}).status_code == 422


def test_a_file_goes_through_the_app_into_a_session_s_worktree(client, local, env):
    # upload: the app takes a file's bytes, at most 10 MB, and the agent puts it in the worktree
    h = signed_up(client)
    root, _ = _started(client, h, env)
    s = _session(client, h)
    path = f"/api/ade/workspace/sessions/{s['id']}/upload"
    assert client.post(path, params={"name": "notes.txt"}, content=b"hello").status_code == 401
    r = client.post(path, headers=h, params={"name": "notes.txt"}, content=b"hello")
    assert r.status_code == 200 and r.json() == {"path": ".proofpane-uploads/notes.txt", "name": "notes.txt",
                                                 "size": 5}, r.text
    assert (root / "sessions" / s["id"] / "work" / ".proofpane-uploads" / "notes.txt").read_bytes() == b"hello"
    r = client.post(path, headers=h, params={"name": "big.bin"}, content=b"x" * (10 * 1024 * 1024 + 1))
    assert r.status_code == 413 and r.json()["detail"] == "A file is at most 10 MB.", r.text[:200]


def test_the_pages_are_told_what_the_installed_claude_offers(client, local, env, tmp_path, monkeypatch):
    # the UI's content follows the installed claude, read from it
    import sys
    stand_in = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_stand_in.py")
    bin_dir = tmp_path / "capsbin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(f"#!{sys.executable}\n" + open(stand_in).read())
    (bin_dir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    h = signed_up(client)
    assert client.get("/api/ade/workspace/capabilities").status_code == 401
    assert client.get("/api/ade/workspace/capabilities", headers=h).status_code == 409      # not started yet
    _started(client, h, env)
    got = client.get("/api/ade/workspace/capabilities", headers=h).json()
    assert got["claude_version"] == "2.1.274" and got["floor"] is False
    assert got["efforts"] == ["low", "medium", "high", "xhigh", "max", "ultracode"]
    assert got["permission_modes"] == ["default", "acceptEdits", "auto", "bypassPermissions", "plan"]


def test_claude_code_is_kept_current_only_in_a_workspace_machine(client, local, env):
    # in a workspace machine (--claude-updates, start.sh) Claude Code is
    # kept current; the local provider's agent says it is not, and refuses to start one
    h = signed_up(client)
    _started(client, h, env)
    assert client.get("/api/ade/workspace/claude/update").status_code == 401
    assert client.get("/api/ade/workspace/claude/update", headers=h).json() == {"enabled": False}
    r = client.post("/api/ade/workspace/claude/update", headers=h, json={"now": True})
    assert r.status_code == 400 and r.json()["detail"] == "Claude Code is kept current in a workspace machine only."


def test_a_claude_session_on_another_provider_s_model_needs_its_key_and_not_claude_s_sign_in(client, local, env,
                                                                                              tmp_path, monkeypatch):
    # Kimi K3, MiniMax M3 and DeepSeek run in Claude Code with the person's own key (providers.py): such a session does
    # not wait for Claude's sign-in, and does not start without that key; the key is kept by the workspace, never sent
    # back to the page
    import sys
    stand_in = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_stand_in.py")
    signed_out = tmp_path / "signed-out"
    signed_out.write_text("")
    bin_dir = tmp_path / "claudebin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(f"#!{sys.executable}\nSIGNED_OUT = {str(signed_out)!r}\n" + open(stand_in).read())
    (bin_dir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    h = signed_up(client)
    _started(client, h, env)
    base = "/api/ade/workspace/model-providers"
    listed = client.get(base, headers=h).json()["providers"]
    assert [p["id"] for p in listed] == ["kimi", "minimax", "deepseek"] and not any(p["set"] for p in listed)
    new = lambda **body: client.post("/api/ade/workspace/sessions", headers=h,  # noqa: E731
                                     json=dict({"task": "t", "repo": "demo", "program": "claude"}, **body))
    r = new(model_provider="kimi")
    assert r.status_code == 400 and "Kimi K3 API key" in r.json()["detail"], r.text
    assert new(model_provider="kimi", program="shell").status_code == 400          # a shell has no model to change
    assert client.put(base + "/kimi", headers=h, json={"key": "short"}).status_code == 400
    assert client.put(base + "/nobody", headers=h, json={"key": "sk-12345678"}).status_code == 400
    key = "sk-kimi-" + "x" * 24
    r = client.put(base + "/kimi", headers=h, json={"key": key})
    assert r.status_code == 200 and key not in r.text
    assert [p["set"] for p in r.json()["providers"]] == [True, False, False]
    assert key not in client.get(base, headers=h).text
    r = new(model_provider="kimi")                                   # Claude is signed out: it starts all the same
    assert r.status_code == 200 and r.json()["running"] is True and r.json()["model_provider"] == "kimi", r.text
    sid = r.json()["id"]
    client.post(f"/api/ade/workspace/sessions/{sid}/stop", headers=h)
    assert client.post(f"/api/ade/workspace/sessions/{sid}/start", headers=h, json={}).status_code == 200
    client.post(f"/api/ade/workspace/sessions/{sid}/stop", headers=h)
    assert client.delete(base + "/kimi", headers=h).json()["providers"][0]["set"] is False
    r = client.post(f"/api/ade/workspace/sessions/{sid}/start", headers=h, json={})
    assert r.status_code == 400 and "Kimi K3 API key" in r.json()["detail"]
    assert new(program="claude").status_code == 409                  # a claude session on Claude still waits for it


def test_a_claude_session_or_a_chat_waits_for_claude_s_sign_in(client, local, env, tmp_path, monkeypatch):
    # a person who started a claude session before signing in met Claude Code's own sign-in, which
    # came up in its terminal, where a phone can neither copy its link nor paste its code. Until Claude is signed in,
    # the app refuses a claude session or a chat, new or started again, with words the pages recognise.
    import sys
    from proofpane_open.ade import workspace_routes
    stand_in = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_stand_in.py")
    signed_out = tmp_path / "signed-out"
    signed_out.write_text("")
    bin_dir = tmp_path / "claudebin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(f"#!{sys.executable}\nSIGNED_OUT = {str(signed_out)!r}\n" + open(stand_in).read())
    (bin_dir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    h = signed_up(client)
    _started(client, h, env)
    new = lambda **body: client.post("/api/ade/workspace/sessions", headers=h, json=dict({"task": "t"}, **body))
    assert client.get("/api/ade/workspace/claude", headers=h).json()["signed_in"] is False
    for program in ("claude", "chat"):
        r = new(repo="demo", program=program)
        assert r.status_code == 409 and r.json()["detail"] == workspace_routes.CLAUDE_FIRST, (program, r.text)
    assert client.get("/api/ade/workspace/sessions", headers=h).json()["sessions"] == []      # nothing was made
    assert new(repo="demo", program="shell").status_code == 200
    r = new(program="claude")                   # with no repository, the agent's own refusal, as before
    assert r.status_code == 400 and r.json()["detail"] != workspace_routes.CLAUDE_FIRST, r.text
    signed_out.unlink()
    r = new(repo="demo", program="claude")
    assert r.status_code == 200 and r.json()["running"] is True, r.text
    sid = r.json()["id"]
    path = f"/api/ade/workspace/sessions/{sid}"
    assert client.post(path + "/stop", headers=h).status_code == 200
    signed_out.write_text("")                   # signed out again: it is not started again
    r = client.post(path + "/start", headers=h, json={})
    assert r.status_code == 409 and r.json()["detail"] == workspace_routes.CLAUDE_FIRST, r.text
    assert client.get(path, headers=h).json()["running"] is False
    signed_out.unlink()
    r = client.post(path + "/start", headers=h, json={})
    assert r.status_code == 200 and r.json()["running"] is True, r.text
    client.post(path + "/stop", headers=h)


def test_a_claude_session_waits_for_an_installed_and_signed_in_claude(monkeypatch):
    from fastapi import HTTPException
    from proofpane_open.ade import workspace_routes
    for answer, detail in (({"installed": False, "signed_in": False}, "claude is not installed in this workspace's image."),
                           ({"installed": True, "signed_in": False}, workspace_routes.CLAUDE_FIRST),
                           ({"installed": True, "signed_in": "yes"}, workspace_routes.CLAUDE_FIRST),
                           ({"installed": True, "signed_in": True}, None),
                           # the account new sessions start on, when it is not the program's own (accounts.py)
                           ({"installed": True, "signed_in": False, "account": "0123456789ab"},
                            workspace_routes.ACCOUNT_FIRST),
                           ({"installed": True, "signed_in": False, "account": "default"}, workspace_routes.CLAUDE_FIRST),
                           # Claude Code slow to say: not signed out, so not sent to sign in, but asked to try again
                           ({"installed": True, "signed_in": None}, workspace_routes.STATUS_UNREAD)):
        monkeypatch.setattr(workspace_routes.client, "call", lambda endpoint, method, path, **kw: (
            answer if (method, path) == ("GET", "/claude?account=active&fresh=1") else pytest.fail(path)))
        try:
            workspace_routes._claude_ready(object())
            got = None
        except HTTPException as e:
            assert e.status_code == (503 if detail == workspace_routes.STATUS_UNREAD else 409)
            got = e.detail
        assert got == detail, answer


def test_a_waiting_question_reaches_the_owner_s_phone_once_and_opens_its_chat(client, local, env, tmp_path,
                                                                              monkeypatch):
    # a push when a session needs you and no page is open (the ADE's notifier, ade/push.py)
    from proofpane_workspace import chat as chats
    if not chats.available():
        pytest.skip("needs the Claude Agent SDK (requirements.txt)")
    import sys
    from proofpane_open.ade import push
    from test_ade_push import FCM, NOW, Device, PushService
    stand_in = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_stand_in.py")
    bin_dir = tmp_path / "chatbin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(f"#!{sys.executable}\n" + open(stand_in).read())
    (bin_dir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    h = signed_up(client)
    uid = _user_id(client, h)
    device, service = Device(), PushService()
    push.subscribe(uid, FCM, device.p256dh, push.b64u(device.auth), now=NOW)
    no_boards = lambda conn: {"alerts": []}          # noqa: E731
    # a stopped workspace: nothing is said, and nothing starts it
    assert push.tick(now=NOW, post=service, build=no_boards) == {uid: 0}
    assert client.get("/api/ade/workspace", headers=h).json()["running"] is False
    _started(client, h, env)
    s = client.post("/api/ade/workspace/sessions", headers=h,
                    json={"repo": "demo", "program": "chat", "task": "run true"}).json()
    path = f"/api/ade/workspace/sessions/{s['id']}/chat"
    ask = _wait_for(lambda: (client.get(path, headers=h).json()["pending"] or [None])[0])
    assert push.tick(now=NOW + 60, post=service, build=no_boards) == {uid: 1}
    (msg,) = [device.decrypt(body) for _, _, body in service.posts]
    # in fixed words, opening its chat: never the repository or the tool (ade/push.py words)
    key = f"chat-ask:{s['id']}:{ask}"
    assert msg == {"title": "A session in your workspace waits for you", "body": "Open the ADE.",
                   "tag": push.words("workspace", key=key, session=s["id"]).tag, "url": "/app/#session=" + s["id"]}
    assert push.tick(now=NOW + 120, post=service, build=no_boards) == {uid: 0}       # once
    client.post(path + "/answer", headers=h, json={"id": ask, "decision": "allow"})
    _wait_for(lambda: client.get(path, headers=h).json()["state"] == "idle")
    assert push.tick(now=NOW + 180, post=service, build=no_boards) == {uid: 0}       # answered: gone
    assert len(service.posts) == 1


def test_an_agent_s_ask_reaches_the_person_through_the_app_and_starts_only_when_they_start_it(client, local, env,
                                                                                               tmp_path, monkeypatch):
    import httpx
    bin_dir = tmp_path / "askbin"
    bin_dir.mkdir()
    (bin_dir / "codex").write_text("#!/bin/sh\n"
                                   'printf "%s\\n%s\\n" "${PP_ASK_TOKEN-unset}" "${PP_ASK_URL-unset}" > "$PP_SESSION_DIR/a.tmp"\n'
                                   'mv "$PP_SESSION_DIR/a.tmp" "$PP_SESSION_DIR/ask.env"\n'
                                   "sleep 600\n")
    (bin_dir / "codex").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    h = signed_up(client)
    root, _ = _started(client, h, env)
    assert client.get("/api/ade/workspace/asks", headers=h).json() == {"on": False, "asks": []}
    assert client.post("/api/ade/workspace/asks/settings", headers=h, json={"on": True}).json()["on"] is True
    r = client.post("/api/ade/workspace/sessions", headers=h, json={"repo": "demo", "program": "codex", "task": "Fix it"})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    given = root / "sessions" / sid / "ask.env"
    deadline = time.time() + 15
    while not given.exists() and time.time() < deadline:
        time.sleep(0.05)
    token, url = given.read_text().split("\n")[:2]
    asked = httpx.post(url + "/ask/sessions", headers={"Authorization": "Bearer " + token},
                       json={"program": "codex", "task": "Write the tests"}, timeout=30)
    assert asked.status_code == 200, asked.text
    waiting = client.get("/api/ade/workspace/asks", headers=h).json()["asks"]
    assert [(a["task"], a["by"]["id"]) for a in waiting] == [("Write the tests", sid)]
    assert len(client.get("/api/ade/workspace/sessions", headers=h).json()["sessions"]) == 1
    assert client.post("/api/ade/workspace/asks/nope/start", headers=h, json={}).status_code == 404
    started = client.post(f"/api/ade/workspace/asks/{waiting[0]['id']}/start", headers=h, json={})
    assert started.status_code == 200, started.text
    assert (started.json()["asked_by"], started.json()["task"]) == (sid, "Write the tests")
    assert client.get("/api/ade/workspace/asks", headers=h).json() == {"on": True, "asks": []}
    r = client.post(f"/api/ade/workspace/asks/{waiting[0]['id']}/refuse", headers=h)
    assert r.status_code == 404 and r.json()["detail"] == "That ask has been answered, or is gone."


def _asking(client, h, env, tmp_path, monkeypatch, tasks):
    """A codex session (a stand-in that keeps its token) that has asked for one session for each of `tasks`, with asks
    on: (the workspace's root, the asking session's id, {task: its ask's id})."""
    import httpx
    bin_dir = tmp_path / "askbin-asking"
    bin_dir.mkdir()
    (bin_dir / "codex").write_text("#!/bin/sh\n"
                                   'printf "%s\\n%s\\n" "${PP_ASK_TOKEN-unset}" "${PP_ASK_URL-unset}" > "$PP_SESSION_DIR/a.tmp"\n'
                                   'mv "$PP_SESSION_DIR/a.tmp" "$PP_SESSION_DIR/ask.env"\n'
                                   "sleep 600\n")
    (bin_dir / "codex").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    root, _ = _started(client, h, env)
    client.post("/api/ade/workspace/asks/settings", headers=h, json={"on": True})
    sid = client.post("/api/ade/workspace/sessions", headers=h,
                      json={"repo": "demo", "program": "codex", "task": "Fix it"}).json()["id"]
    given = root / "sessions" / sid / "ask.env"
    deadline = time.time() + 15
    while not given.exists() and time.time() < deadline:
        time.sleep(0.05)
    token, url = given.read_text().split("\n")[:2]
    for task in tasks:
        r = httpx.post(url + "/ask/sessions", headers={"Authorization": "Bearer " + token},
                       json={"program": "codex", "task": task}, timeout=30)
        assert r.status_code == 200, r.text
    return root, sid, {a["task"]: a["id"] for a in client.get("/api/ade/workspace/asks", headers=h).json()["asks"]}


def test_an_ask_s_answer_is_shown_from_the_app_s_record_never_from_the_session_s_folder(client, local, env, tmp_path,
                                                                                        monkeypatch):
    # a session's programs can write in its folder: an answer, or an `asked_by`, written there is not the person's
    h = signed_up(client)
    root, sid, waiting = _asking(client, h, env, tmp_path, monkeypatch, ("Write the tests", "Refuse this"))
    started = client.post(f"/api/ade/workspace/asks/{waiting['Write the tests']}/start", headers=h, json={}).json()
    assert client.post(f"/api/ade/workspace/asks/{waiting['Refuse this']}/refuse", headers=h).status_code == 200
    mine = client.post("/api/ade/workspace/sessions", headers=h,
                       json={"repo": "demo", "program": "shell", "task": "Mine"}).json()["id"]
    # what the session's programs could write: a "started" answer the person never gave, and an asked_by
    with open(root / "sessions" / sid / "asks.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": "started", "ask": "0123456789ab", "program": "codex", "session": mine,
                             "at": int(time.time())}) + "\n")
        fh.write(json.dumps({"event": "refused", "ask": waiting["Write the tests"], "program": "codex",
                             "at": int(time.time())}) + "\n")
    record = json.loads((root / "sessions" / mine / "session.json").read_text())
    (root / "sessions" / mine / "session.json").write_text(json.dumps(dict(record, asked_by=sid)))
    asks = client.get(f"/api/ade/workspace/sessions/{sid}/gate", headers=h).json()["asks"]
    assert [(a["event"], a["ask"], a.get("session")) for a in asks] == [
        ("asked", waiting["Write the tests"], None), ("asked", waiting["Refuse this"], None),
        ("started", waiting["Write the tests"], started["id"]), ("refused", waiting["Refuse this"], None)]
    listed = {s["id"]: s.get("asked_by") for s in client.get("/api/ade/workspace/sessions", headers=h).json()["sessions"]}
    assert listed == {sid: None, started["id"]: sid, mine: None}
    assert client.get(f"/api/ade/workspace/sessions/{mine}", headers=h).json()["asked_by"] is None
    assert client.get(f"/api/ade/workspace/sessions/{started['id']}", headers=h).json()["asked_by"] == sid


def test_an_answer_whose_record_cannot_be_written_is_still_given_and_the_log_says_so(client, local, env, tmp_path,
                                                                                     monkeypatch, capsys):
    from proofpane_open import store
    from proofpane_open.ade import workspace_routes
    h = signed_up(client)
    root, sid, waiting = _asking(client, h, env, tmp_path, monkeypatch, ("Write the tests",))
    real = store.connect

    class Full:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, sql, *args):
            if "ade_ask_answers" in sql and sql.lstrip().startswith("INSERT"):
                raise store.sqlite3.OperationalError("database or disk is full")
            return self.conn.execute(sql, *args)

    import contextlib

    @contextlib.contextmanager
    def full():
        with real() as conn:
            yield Full(conn)
    monkeypatch.setattr(workspace_routes.store, "connect", full)
    started = client.post(f"/api/ade/workspace/asks/{waiting['Write the tests']}/start", headers=h, json={})
    assert started.status_code == 200 and started.json()["asked_by"] == sid, started.text
    assert "was not recorded: OperationalError: database or disk is full" in capsys.readouterr().err
    assert client.get("/api/ade/workspace/asks", headers=h).json()["asks"] == []


def test_the_person_s_choice_about_asks_is_kept_by_the_app_and_put_back_in_the_workspace(client, local, env, tmp_path,
                                                                                        monkeypatch):
    from proofpane_open import auth
    from proofpane_open.ade import workspace_client, workspaces
    bin_dir = tmp_path / "askbin2"
    bin_dir.mkdir()
    (bin_dir / "codex").write_text("#!/bin/sh\n"
                                   'printf "%s" "${PP_ASK_TOKEN-unset}" > "$PP_SESSION_DIR/a.tmp"\n'
                                   'mv "$PP_SESSION_DIR/a.tmp" "$PP_SESSION_DIR/token"\n'
                                   "sleep 600\n")
    (bin_dir / "codex").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    h = signed_up(client)
    root, _ = _started(client, h, env)
    user = auth.user(_user_id(client, h))
    endpoint = workspaces.for_user(user).endpoint(user)

    def token_of(sid):
        path = root / "sessions" / sid / "token"
        deadline = time.time() + 15
        while not path.exists() and time.time() < deadline:
            time.sleep(0.05)
        return path.read_text()

    # on, by the person: changed in the workspace by anything else, it is put back on the next look
    client.post("/api/ade/workspace/asks/settings", headers=h, json={"on": True})
    workspace_client.call(endpoint, "POST", "/asks/settings", {"on": False})
    assert client.get("/api/ade/workspace/asks", headers=h).json()["on"] is True
    # off, by the person: turned on in the workspace by anything else, a session started next still gets no token
    client.post("/api/ade/workspace/asks/settings", headers=h, json={"on": False})
    workspace_client.call(endpoint, "POST", "/asks/settings", {"on": True})
    sid = client.post("/api/ade/workspace/sessions", headers=h,
                      json={"repo": "demo", "program": "codex", "task": "t"}).json()["id"]
    assert token_of(sid) == "unset"
    # a workspace started again begins with asks off; the person's choice is given back to it
    client.post("/api/ade/workspace/asks/settings", headers=h, json={"on": True})
    client.post("/api/ade/workspace/stop", headers=h)
    client.post("/api/ade/workspace/start", headers=h)
    assert client.get("/api/ade/workspace/asks", headers=h).json()["on"] is True
    sid = client.post("/api/ade/workspace/sessions", headers=h,
                      json={"repo": "demo", "program": "codex", "task": "t"}).json()["id"]
    assert len(token_of(sid)) >= 40


# the app's own record of a session's pull request: a session's programs can write its record in
# the workspace, so what the page is shown, and what gh merges or reviews with the person's sign-in, is the one the
# app saw the person ship the session as, or start it from

FAKE_GH = """#!{python}
import json, os, sys
args = sys.argv[1:]
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "calls.txt"), "a", encoding="utf-8") as fh:
    fh.write(" ".join(args) + "\\n")
if args[:2] == ["pr", "create"]:
    sys.stdin.read()
    print("https://github.com/acme/demo/pull/7")
elif args[:2] == ["pr", "view"]:
    number = int(args[2].rsplit("/", 1)[1])
    print(json.dumps({{"number": number, "title": "Return two", "state": "OPEN", "isDraft": False,
                      "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "reviewDecision": None,
                      "statusCheckRollup": [], "mergedAt": None, "baseRefName": "main",
                      "author": {{"login": "someone"}}}}))
elif args[:2] in (["pr", "merge"], ["pr", "review"]):
    sys.stdin.read()
else:
    sys.exit(2)
"""


@pytest.fixture
def github(local, env, tmp_path, monkeypatch, client):
    """A started workspace whose demo repository's origin, github.com/acme/demo, is a bare repository here (git's
    insteadOf), with pull request 5's head in it, and a gh that answers as GitHub's would and notes each call."""
    bin_dir = tmp_path / "ghbin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH.format(python=sys.executable))
    (bin_dir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    h = signed_up(client)
    root, _ = _started(client, h, env)
    repo = str(root / "repos" / "demo")
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    git("config", f"url.{bare}.insteadOf", "https://github.com/acme/demo.git", cwd=repo)
    git("push", "-q", "origin", "main", cwd=repo)
    git("checkout", "-q", "-b", "theirs", cwd=repo)
    (root / "repos" / "demo" / "theirs.txt").write_text("theirs\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "theirs", cwd=repo)
    git("push", "-q", "origin", "theirs:refs/pull/5/head", cwd=repo)
    git("checkout", "-q", "main", cwd=repo)
    return h, root, bin_dir / "calls.txt"


def _rewrite(root, sid, change):
    path = root / "sessions" / sid / "session.json"
    record = json.loads(path.read_text())
    change(record)
    path.write_text(json.dumps(record))


def _calls(log, *start):
    return [c for c in (log.read_text().splitlines() if log.exists() else []) if c.split()[:len(start)] == list(start)]


def test_a_shipped_session_s_pull_request_is_the_one_the_app_saw_it_shipped_as(client, github):
    h, root, log = github
    s = _session(client, h)
    W = "/api/ade/workspace/sessions/" + s["id"]
    (root / "sessions" / s["id"] / "work" / "app.py").write_text("x = 2\n")
    assert client.post(W + "/finish", headers=h).status_code == 200
    r = client.post(W + "/ship", headers=h, json={})
    assert r.status_code == 200 and r.json()["url"] == "https://github.com/acme/demo/pull/7", r.text
    ours = "https://github.com/acme/demo/pull/7"
    assert client.get(W, headers=h).json()["shipped"]["url"] == ours
    assert client.get(W + "/pr", headers=h).json()["number"] == 7
    # its programs point the session at another pull request on GitHub
    _rewrite(root, s["id"], lambda r: r["shipped"].update(url="https://github.com/elsewhere/other/pull/1"))
    assert client.get(W, headers=h).json()["shipped"]["url"] == ours              # the page is shown the app's
    listed = {x["id"]: x for x in client.get("/api/ade/workspace/sessions", headers=h).json()["sessions"]}
    assert listed[s["id"]]["shipped"]["url"] == ours
    for method, path, body in (("GET", "/pr", None), ("GET", "/pr/feedback", None),
                               ("POST", "/pr/merge", {"method": "squash"}),
                               ("POST", "/pr/review", {"event": "approve", "url": ours})):
        r = client.request(method, W + path, headers=h, json=body)
        assert r.status_code == 400, (path, r.text)
    r = client.post(W + "/pr/review", headers=h,
                    json={"event": "approve", "url": "https://github.com/elsewhere/other/pull/1"})
    assert r.status_code == 400 and "not the one shown" in r.json()["detail"]
    assert _calls(log, "pr", "merge") == [] and _calls(log, "pr", "review") == []
    assert [c for c in _calls(log, "pr", "view") if "elsewhere" in c] == []
    # put back, the app's own goes ahead
    _rewrite(root, s["id"], lambda r: r["shipped"].update(url=ours))
    r = client.post(W + "/pr/merge", headers=h, json={"method": "squash"})
    assert r.status_code == 200 and r.json()["number"] == 7, r.text
    assert _calls(log, "pr", "merge") == [f"pr merge {ours} --squash"]
    # removed, its record goes with it
    assert client.delete(W, headers=h).status_code == 200
    from proofpane_open import store
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_session_pulls WHERE session = ?", (s["id"],)).fetchone()[0] == 0


def test_a_session_from_a_pull_request_is_shown_and_reviewed_as_the_one_the_person_chose(client, github):
    h, root, log = github
    r = client.post("/api/ade/workspace/sessions", headers=h,
                    json={"repo": "demo", "program": "shell", "task": "Look at #5", "pull": 5})
    assert r.status_code == 200, r.text
    s = r.json()
    W = "/api/ade/workspace/sessions/" + s["id"]
    chosen = "https://github.com/acme/demo/pull/5"
    assert (s["pull"], s["pull_url"]) == (5, chosen)
    assert client.get(W + "/pr", headers=h).json()["number"] == 5
    # its programs change the number, then the repository's origin
    _rewrite(root, s["id"], lambda r: r.update(pull=6))
    got = client.get(W, headers=h).json()
    assert (got["pull"], got["pull_url"]) == (5, chosen)
    assert client.get(W + "/pr", headers=h).status_code == 400
    r = client.post(W + "/pr/review", headers=h, json={"event": "approve", "url": chosen})
    assert r.status_code == 400 and "not the one shown" in r.json()["detail"]
    _rewrite(root, s["id"], lambda r: r.update(pull=5))
    git("remote", "set-url", "origin", "https://github.com/elsewhere/demo.git", cwd=str(root / "repos" / "demo"))
    assert client.get(W + "/pr", headers=h).status_code == 400
    assert client.post(W + "/pr/review", headers=h, json={"event": "approve", "url": chosen}).status_code == 400
    assert _calls(log, "pr", "review") == []
    git("remote", "set-url", "origin", "https://github.com/acme/demo.git", cwd=str(root / "repos" / "demo"))
    r = client.post(W + "/pr/review", headers=h, json={"event": "approve", "url": chosen})
    assert r.status_code == 200 and r.json() == {"reviewed": "approve", "url": chosen}, r.text
    assert _calls(log, "pr", "review") == [f"pr review {chosen} --approve"]
    # never merged from here: it is not this session's own
    r = client.post(W + "/pr/merge", headers=h, json={"method": "squash"})
    assert r.status_code == 400 and "no record of shipping" in r.json()["detail"]


def test_a_pull_request_the_app_did_not_record_is_not_shown_merged_or_reviewed(client, github):
    h, root, log = github
    s = _session(client, h)
    W = "/api/ade/workspace/sessions/" + s["id"]
    # a record in the session's folder alone: one written there by its programs, or one from before the app kept its own
    _rewrite(root, s["id"], lambda r: r.update(pull=5, shipped={"url": "https://github.com/acme/demo/pull/7",
                                                                "at": 1, "commits": 1}))
    got = client.get(W, headers=h).json()
    assert (got["pull"], got["pull_url"], got["shipped"]) == (None, None, None)
    r = client.get(W + "/pr", headers=h)
    assert r.status_code == 400 and "no record of this session's pull request" in r.json()["detail"]
    r = client.post(W + "/pr/merge", headers=h, json={})
    assert r.status_code == 400 and "no record of shipping" in r.json()["detail"]
    r = client.post(W + "/pr/review", headers=h, json={"event": "approve", "url": "https://github.com/acme/demo/pull/7"})
    assert r.status_code == 400
    assert client.get(W + "/pr/feedback", headers=h).status_code == 400
    assert _calls(log, "pr") == []


def test_only_the_pull_request_the_person_chose_or_shipped_is_recorded(client, local, env, monkeypatch):
    # a workspace that answers with another pull request than the one the person chose, or another session's ship,
    # leaves no record: the app takes the number it was asked for, and the session it asked to ship
    from proofpane_open import store
    from proofpane_open.ade import workspace_client
    h = signed_up(client)
    _started(client, h, env)

    def answer(endpoint, method, path, body=None, **kw):
        if path == "/asks":
            return {"on": False, "asks": []}
        if (method, path) == ("POST", "/sessions"):
            return {"id": "aaaaaaaaaaaa", "program": "shell", "pull": 5,
                    "pull_url": "https://github.com/elsewhere/other/pull/9"}
        if path.endswith("/ship"):
            return {"session": "bbbbbbbbbbbb", "url": "https://github.com/elsewhere/other/pull/1"}
        return {}

    monkeypatch.setattr(workspace_client, "call", answer)
    r = client.post("/api/ade/workspace/sessions", headers=h,
                    json={"repo": "demo", "program": "shell", "task": "t", "pull": 5})
    assert r.status_code == 200 and (r.json()["pull"], r.json()["pull_url"]) == (None, None), r.text
    assert client.post("/api/ade/workspace/sessions/aaaaaaaaaaaa/ship", headers=h, json={}).status_code == 200
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_session_pulls").fetchone()[0] == 0



def test_a_claude_session_starts_only_once_the_account_it_starts_on_is_signed_in(client, local, env, tmp_path,
                                                                                   monkeypatch):
    # accounts: the app checks the sign-in of the account a claude session starts on (the one chosen, or the one it
    # names), not the default's alone; a session started again is checked on its own account
    from test_workspace_accounts import CLAUDE, GOOD, URL
    bin_dir = tmp_path / "claudebin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(CLAUDE.format(python=sys.executable, url=URL, good=GOOD))
    (bin_dir / "claude").chmod(0o755)
    # the accounts' view below has `codex login status` run wherever a codex is on
    # the workspace agent's PATH, which this folder begins, so a codex of the test's own is in it: it answers
    # `login status` signed in, in the words that tests/test_workspace_accounts.py's `CODEX` gives for a sign-in,
    # writes down each call it gets, and exits 97 on any other
    calls = tmp_path / "codex-calls"
    (bin_dir / "codex").write_text(f"#!/bin/sh\necho \"codex $*\" >> '{calls}'\n"
                                   "if [ \"$#\" -eq 2 ] && [ \"$1\" = login ] && [ \"$2\" = status ]; then\n"
                                   "    echo 'Logged in using ChatGPT' >&2\n    exit 0\nfi\nexit 97\n")
    (bin_dir / "codex").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    assert shutil.which("codex", path=os.environ["PATH"]) == str(bin_dir / "codex")
    h = signed_up(client)
    root, _ = _started(client, h, env)
    home = tmp_path / "home"
    (home / ".claude").mkdir(exist_ok=True)
    (home / ".claude" / ".credentials.json").write_text("default")         # the default account is signed in
    A = "/api/ade/workspace/accounts"
    account = client.post(A, headers=h, json={"kind": "claude", "name": "Work"}).json()
    assert client.post(A + "/choose", headers=h, json={"kind": "claude", "account": account["id"]}).status_code == 200
    r = client.post("/api/ade/workspace/sessions", headers=h, json={"repo": "demo", "program": "claude", "task": "t"})
    assert r.status_code == 409 and "this session starts on is not signed in" in r.json()["detail"], r.text
    # on the default, by name: it starts
    r = client.post("/api/ade/workspace/sessions", headers=h,
                    json={"repo": "demo", "program": "claude", "task": "t", "account": "default"})
    assert r.status_code == 200 and r.json()["account"] is None, r.text
    # the account signed in: a session starts on it
    (root / "agent-accounts" / account["id"] / "claude" / ".credentials.json").write_text(account["id"])
    r = client.post("/api/ade/workspace/sessions", headers=h, json={"repo": "demo", "program": "claude", "task": "t"})
    assert r.status_code == 200 and r.json()["account"] == account["id"], r.text
    sid = r.json()["id"]
    views = client.get(A, headers=h).json()
    view = views["claude"]
    assert view["active"] == account["id"]
    assert {x["id"]: x["status"]["signed_in"] for x in view["accounts"]} == {"default": True, account["id"]: True}
    # each Codex row says what this test's codex answered
    signed = {"installed": True, "signed_in": True, "method": "chatgpt"}
    rows = views["codex"]["accounts"]
    assert rows and all(x["status"] == signed for x in rows), [x["status"] for x in rows]
    # signed out again, and the default chosen for new sessions: started again, the session is checked on its own
    assert client.post(f"{A}/{account['id']}/sign-out", headers=h, json={}).json()["signed_in"] is False
    assert client.post(A + "/choose", headers=h, json={"kind": "claude", "account": "default"}).status_code == 200
    client.post(f"/api/ade/workspace/sessions/{sid}/stop", headers=h)
    r = client.post(f"/api/ade/workspace/sessions/{sid}/start", headers=h, json={})
    assert r.status_code == 409 and "this session starts on is not signed in" in r.json()["detail"], r.text
    # its account changed in the workspace's record (its programs could, by indirection past the gate): it is not
    # started again on another account
    client.post(A + "/choose", headers=h, json={"kind": "claude", "account": account["id"]})
    (root / "agent-accounts" / account["id"] / "claude" / ".credentials.json").write_text(account["id"])
    r = client.post("/api/ade/workspace/sessions", headers=h, json={"repo": "demo", "program": "claude", "task": "t"})
    moved = r.json()["id"]
    client.post(f"/api/ade/workspace/sessions/{moved}/stop", headers=h)
    registry = root / "agent-accounts.json"
    record = json.loads(registry.read_text())
    record["sessions"].pop(moved)
    registry.write_text(json.dumps(record))
    r = client.post(f"/api/ade/workspace/sessions/{moved}/start", headers=h, json={})
    assert r.status_code == 409 and "not the one it was started on" in r.json()["detail"], r.text
    # what is not an account is refused before it reaches the workspace
    for body in ({"repo": "demo", "program": "claude", "task": "t", "account": "../x"},):
        assert client.post("/api/ade/workspace/sessions", headers=h, json=body).status_code == 422
    assert client.post(A, headers=h, json={"kind": "gemini", "name": "x"}).status_code == 422
    assert client.delete(f"{A}/default", headers=h).status_code == 404
    r = client.delete(f"{A}/{account['id']}", headers=h)
    assert r.status_code == 400 and "remove it first" in r.json()["detail"]
    asked = calls.read_text().splitlines() if calls.exists() else []
    assert asked and all(c == "codex login status" for c in asked), asked


def test_a_claude_session_starts_on_the_account_that_was_checked(client, local, env, monkeypatch):
    # the app checks the sign-in of the account new sessions start on, then names that account to the workspace: one
    # chosen in between is never started unchecked
    from proofpane_open import store
    from proofpane_open.ade import workspace_client
    h = signed_up(client)
    _started(client, h, env)
    sent = []

    def answer(endpoint, method, path, body=None, **kw):
        if path == "/asks":
            return {"on": False, "asks": []}
        if path == "/claude?account=active&fresh=1":
            return {"installed": True, "signed_in": True, "account": "0123456789ab"}
        if (method, path) == ("POST", "/sessions"):
            sent.append(body)
            return {"id": "aaaaaaaaaaaa", "program": "claude", "account": body.get("account")}
        return {}

    monkeypatch.setattr(workspace_client, "call", answer)
    r = client.post("/api/ade/workspace/sessions", headers=h, json={"repo": "demo", "program": "claude", "task": "t"})
    assert r.status_code == 200, r.text
    assert sent[0]["account"] == "0123456789ab"
    with store.connect() as db:
        assert db.execute("SELECT account FROM ade_session_accounts WHERE session = 'aaaaaaaaaaaa'").fetchone()[0] == \
            "0123456789ab"
