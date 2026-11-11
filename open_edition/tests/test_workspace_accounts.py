"""More than one sign-in to Claude Code, or to Codex, in one workspace (accounts.py): each account a folder of its own,
given to its program as CLAUDE_CONFIG_DIR or CODEX_HOME, with the person's own setup linked in; new sessions start on
the account chosen, and a session stays on its own; each account signs in and out, and is counted, on its own; one a
session runs on is not removed, and removing one signs it out first; and the gate refuses any call that names the
accounts' folder. Stand-in `claude` and `codex` programs say which folder they were started with, and keep a sign-in in
it as the real ones do (a file in it)."""

import json
import os
import stat
import sys
import time

import pytest

from proofpane_workspace import gate, statusline
from test_workspace_usage import write_rollout
from workspace_fixtures import CLAUDE_VERSION_SH, Agent, make_repo, read_until

GOOD = "GOOD-code_123#state.4"
URL = "https://claude.com/cai/oauth/authorize?code=true&client_id=x&state=y"

CLAUDE = r'''#!{python}
import json, os, sys, time
if sys.argv[1:2] in (["-v"], ["--version"]):        # as the real one says it, before anything is written down
    print("2.1.274 (Claude Code)")
    sys.exit(0)
folder = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.environ["HOME"], ".claude")
marker = os.path.join(folder, ".credentials.json")
log = os.path.join(os.environ["HOME"], "claude-calls.txt")
with open(log, "a") as fh:
    fh.write(" ".join(sys.argv[1:3]) + " @ " + os.environ.get("CLAUDE_CONFIG_DIR", "default") + "\n")
args = sys.argv[1:3]
if args == ["auth", "login"]:
    sys.stdout.write("If the browser didn't open, visit: " + {url!r} + "\r\n")
    sys.stdout.write("Paste code here if prompted > ")
    sys.stdout.flush()
    if sys.stdin.readline().rstrip("\r\n") == {good!r}:
        os.makedirs(folder, exist_ok=True)
        with open(marker, "w") as fh:
            fh.write(os.path.basename(os.path.dirname(folder)) if os.environ.get("CLAUDE_CONFIG_DIR") else "default")
        print("Login successful.")
        sys.exit(0)
    sys.exit(1)
if args == ["auth", "logout"]:
    if os.path.exists(os.path.join(os.environ["HOME"], "logout-fails")):
        sys.exit(1)
    if os.path.exists(marker):
        os.remove(marker)
    sys.exit(0)
if args == ["auth", "status"]:
    if os.path.exists(os.path.join(os.environ["HOME"], "slow-status")):
        time.sleep(1.5)
    if os.path.exists(os.path.join(os.environ["HOME"], "logout-fails")) and os.environ.get("CLAUDE_CONFIG_DIR"):
        # a sign-in its own sign-out could not end, as a Mac's keychain item would be: no file of it to remove
        print(json.dumps({{"loggedIn": True, "authMethod": "claude.ai", "email": "kept@example.org"}}))
        sys.exit(0)
    if os.path.exists(marker):
        who = open(marker).read()
        print(json.dumps({{"loggedIn": True, "authMethod": "claude.ai", "email": who + "@example.org",
                          "subscriptionType": "max"}}))
        sys.exit(0)
    print(json.dumps({{"loggedIn": False, "authMethod": "none"}}))
    sys.exit(1)
print("started:" + os.environ.get("CLAUDE_CONFIG_DIR", "default") + "|args:" + "|".join(sys.argv[1:]) + ":end", flush=True)
time.sleep(600)
'''

CODEX = r'''#!{python}
import os, sys, time
home = os.environ.get("CODEX_HOME") or os.path.join(os.environ["HOME"], ".codex")
auth = os.path.join(home, "auth.json")
if sys.argv[1:3] == ["login", "status"]:
    if os.path.exists(auth):
        print("Logged in using ChatGPT", file=sys.stderr)
        sys.exit(0)
    print("Not logged in", file=sys.stderr)
    sys.exit(1)
if sys.argv[1:2] == ["logout"]:
    if os.path.exists(auth):
        os.remove(auth)
    print("Successfully logged out", file=sys.stderr)
    sys.exit(0)
print("codex:" + os.environ.get("CODEX_HOME", "default") + "|args:" + "|".join(sys.argv[1:]) + ":end", flush=True)
time.sleep(600)
'''


@pytest.fixture
def ws(tmp_path):
    home = tmp_path / "home"
    (home / ".claude" / "agents").mkdir(parents=True)
    (home / ".claude" / "agents" / "reviewer.md").write_text("Review carefully.\n")
    (home / ".claude" / "settings.json").write_text('{"theme": "light"}')
    (home / ".claude" / "CLAUDE.md").write_text("Write tests first.\n")
    (home / ".codex").mkdir()
    (home / ".codex" / "AGENTS.md").write_text("Be brief.\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in (("claude", CLAUDE), ("codex", CODEX)):
        (bin_dir / name).write_text(text.format(python=sys.executable, url=URL, good=GOOD))
        (bin_dir / name).chmod(0o755)
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": f"{bin_dir}:{os.environ['PATH']}"})
    make_repo(str(tmp_path / "ws" / "repos" / "demo"))
    a.home = home
    yield a
    a.close()


def _add(ws, kind="claude", name="Work"):
    r = ws.http.post("/accounts", json={"kind": kind, "name": name})
    assert r.status_code == 200, r.text
    return r.json()


def _folder(ws, account):
    return os.path.join(ws.root, "agent-accounts", account["id"], account["kind"])


def _start(ws, program="claude", **more):
    r = ws.http.post("/sessions", json={"repo": "demo", "program": program, "task": "Fix it", **more})
    assert r.status_code == 200, r.text
    return r.json()


def _said(ws, sid, needle=b":end"):
    with ws.terminal(sid) as t:
        return read_until(t, needle).decode("utf-8", "replace")


def test_an_account_is_a_folder_of_its_own_with_the_person_s_setup_linked_in(ws):
    account = _add(ws, name="  Work   laptop ")
    assert (account["kind"], account["name"]) == ("claude", "Work laptop")
    folder = _folder(ws, account)
    for path in (folder, os.path.dirname(folder), os.path.join(ws.root, "agent-accounts")):
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o700, path
    # the person's own settings, instructions and agents, linked; their sign-in, transcripts and history, not
    assert os.readlink(os.path.join(folder, "settings.json")) == str(ws.home / ".claude" / "settings.json")
    assert os.readlink(os.path.join(folder, "CLAUDE.md")) == str(ws.home / ".claude" / "CLAUDE.md")
    assert os.readlink(os.path.join(folder, "agents")) == str(ws.home / ".claude" / "agents")
    assert not os.path.lexists(os.path.join(folder, "commands"))          # the person has none
    # its Claude Code opens without its first-run wizard; on the person's own machine it asks about each folder, as
    # their own Claude Code does
    assert json.load(open(os.path.join(folder, ".claude.json"))) == {"hasCompletedOnboarding": True}
    # what is kept of it: its name and kind, nothing of a sign-in, readable by the agent's user alone
    registry = os.path.join(ws.root, "agent-accounts.json")
    assert stat.S_IMODE(os.stat(registry).st_mode) == 0o600
    assert json.load(open(registry))["accounts"] == [account]
    codex = _add(ws, kind="codex", name="Work laptop")       # a name is a kind's own
    assert os.readlink(os.path.join(_folder(ws, codex), "AGENTS.md")) == str(ws.home / ".codex" / "AGENTS.md")


@pytest.mark.parametrize("body, says", [
    ({"kind": "gemini", "name": "x"}, "Claude Code's or Codex's"),
    ({"kind": "claude", "name": ""}, "1 to 40 characters"),
    ({"kind": "claude", "name": "x" * 41}, "1 to 40 characters"),
    ({"kind": "claude", "name": "Wo\u202erk"}, "plain text"),
    ({"kind": "claude", "name": "default"}, "already"),
])
def test_what_is_not_an_account_is_refused(ws, body, says):
    r = ws.http.post("/accounts", json=body)
    assert r.status_code == 400 and says in r.json()["detail"], r.text
    assert not os.path.exists(os.path.join(ws.root, "agent-accounts.json"))


def test_one_name_once_and_at_most_eight_of_a_kind(ws):
    _add(ws, name="Work")
    r = ws.http.post("/accounts", json={"kind": "claude", "name": "WORK"})
    assert r.status_code == 400 and "already" in r.json()["detail"]
    for n in range(7):
        _add(ws, name=f"Spare {n}")
    r = ws.http.post("/accounts", json={"kind": "claude", "name": "Ninth"})
    assert r.status_code == 400 and "At most 8" in r.json()["detail"]


def test_new_sessions_start_on_the_chosen_account_and_each_stays_on_its_own(ws):
    account = _add(ws)
    folder = _folder(ws, account)
    first = _start(ws)                                      # before any is chosen: the default
    assert first["account"] is None
    assert "started:default|" in _said(ws, first["id"])
    r = ws.http.post("/accounts/choose", json={"kind": "claude", "account": account["id"]})
    assert r.json() == {"kind": "claude", "active": account["id"]}
    second = _start(ws)
    assert second["account"] == account["id"]
    assert f"started:{folder}|" in _said(ws, second["id"])
    assert json.load(open(os.path.join(folder, ".claude.json"))) == {"hasCompletedOnboarding": True}
    # the default chosen again: a new session is on it; the second, started again, stays on its own
    ws.http.post("/accounts/choose", json={"kind": "claude", "account": "default"})
    assert _start(ws)["account"] is None
    assert ws.http.post(f"/sessions/{second['id']}/stop").status_code == 200
    assert ws.http.post(f"/sessions/{second['id']}/start", json={}).status_code == 200
    assert f"started:{folder}|" in _said(ws, second["id"])
    # a codex session is not on a Claude account, nor a shell on any
    assert _start(ws, program="codex")["account"] is None


def test_a_session_names_its_account_or_the_default_and_nothing_else(ws):
    claude, codex = _add(ws), _add(ws, kind="codex", name="Spare")
    ws.http.post("/accounts/choose", json={"kind": "claude", "account": claude["id"]})
    assert _start(ws, account="default")["account"] is None
    assert _start(ws, program="codex", account=codex["id"])["account"] == codex["id"]
    for program, account in (("claude", codex["id"]), ("claude", "0123456789ab"), ("shell", claude["id"]),
                             ("claude", "../x")):
        r = ws.http.post("/sessions", json={"repo": "demo", "program": program, "task": "t", "account": account})
        assert r.status_code == 400, (program, account, r.text)
    r = ws.http.post("/accounts/choose", json={"kind": "codex", "account": claude["id"]})
    assert r.status_code == 400 and "No such account" in r.json()["detail"]


def test_a_claude_started_again_continues_its_conversation_from_its_account_s_folder(ws):
    account = _add(ws)
    s = _start(ws, account=account["id"])
    _said(ws, s["id"])
    ws.http.post(f"/sessions/{s['id']}/stop")
    work = os.path.realpath(os.path.join(ws.root, "sessions", s["id"], "work"))
    import re
    slug = re.sub(r"[^A-Za-z0-9]", "-", work)
    # a transcript in the default folder is not this session's: it starts afresh
    os.makedirs(ws.home / ".claude" / "projects" / slug)
    (ws.home / ".claude" / "projects" / slug / "x.jsonl").write_text("{}\n")
    ws.http.post(f"/sessions/{s['id']}/start", json={})
    assert "--continue" not in _said(ws, s["id"])
    ws.http.post(f"/sessions/{s['id']}/stop")
    os.makedirs(os.path.join(_folder(ws, account), "projects", slug))
    open(os.path.join(_folder(ws, account), "projects", slug, "y.jsonl"), "w").write("{}\n")
    ws.http.post(f"/sessions/{s['id']}/start", json={})
    # after the control window's map settings every claude terminal start is given first (workspace._with_map)
    mapped = os.path.join(ws.root, "sessions", s["id"], "map-settings.json")
    assert f"args:--settings|{mapped}|--continue:end" in _said(ws, s["id"])


def test_each_account_signs_in_and_out_on_its_own(ws):
    account = _add(ws)
    s = ws.http.post("/sessions", json={"repo": None, "program": "claude-sign-in", "task": "Sign in",
                                        "account": account["id"]}).json()
    assert s["account"] == account["id"]
    deadline = time.time() + 20
    while not ws.http.get(f"/sessions/{s['id']}").json().get("sign_in_url") and time.time() < deadline:
        time.sleep(0.1)
    assert ws.http.post(f"/sessions/{s['id']}/sign-in-code", json={"code": GOOD}).status_code == 200
    deadline = time.time() + 20
    while not os.path.exists(os.path.join(_folder(ws, account), ".credentials.json")) and time.time() < deadline:
        time.sleep(0.1)
    view = ws.http.get("/accounts").json()["claude"]
    rows = {r["id"]: r for r in view["accounts"]}
    assert rows["default"]["status"]["signed_in"] is False
    assert rows[account["id"]]["status"] == {"installed": True, "signed_in": True,
                                             "email": account["id"] + "@example.org", "method": "claude.ai",
                                             "subscription": "max"}
    assert view["active"] == "default"
    # its own sign-out: the default's is untouched
    r = ws.http.post(f"/accounts/{account['id']}/sign-out")
    assert r.status_code == 200 and r.json()["signed_in"] is False
    assert f"auth logout @ {_folder(ws, account)}" in (ws.home / "claude-calls.txt").read_text()
    assert "auth logout @ default" not in (ws.home / "claude-calls.txt").read_text()


def test_codex_accounts_sign_in_and_run_in_their_own_home(ws):
    account = _add(ws, kind="codex", name="Team")
    home = _folder(ws, account)
    s = _start(ws, program="codex", account=account["id"])
    assert f"codex:{home}|" in _said(ws, s["id"])
    # its sign-in: in a workspace machine, the device code's; the program's own words
    sign = ws.http.post("/sessions", json={"repo": None, "program": "codex-sign-in", "task": "Sign in",
                                           "account": account["id"]}).json()
    assert f"codex:{home}|args:login|--device-auth:end" in _said(ws, sign["id"])
    # its words, for the page to show where a person signs in and with what code: plain text, and only Codex's own
    # sign-in's
    view = ws.http.get(f"/sessions/{sign['id']}").json()
    assert view["sign_in_text"] == f"codex:{home}|args:login|--device-auth:end"
    assert ws.http.get(f"/sessions/{s['id']}").json()["sign_in_text"] is None
    open(os.path.join(home, "auth.json"), "w").write("{}")
    rows = {r["id"]: r for r in ws.http.get("/accounts").json()["codex"]["accounts"]}
    assert rows[account["id"]]["status"] == {"installed": True, "signed_in": True, "method": "chatgpt"}
    assert rows["default"]["status"]["signed_in"] is False
    assert ws.http.post(f"/accounts/{account['id']}/sign-out").json()["signed_in"] is False
    assert not os.path.exists(os.path.join(home, "auth.json"))


def test_each_account_s_windows_are_its_own(ws):
    claude = _add(ws)
    codex = _add(ws, kind="codex", name="Team")
    on_default, on_work = _start(ws), _start(ws, account=claude["id"])
    now = int(time.time())
    for sid, used, at in ((on_default["id"], 12.0, now - 60), (on_work["id"], 91.0, now - 30)):
        with open(os.path.join(ws.root, "sessions", sid, statusline.FILE), "w") as fh:
            json.dump({"at": at, "five_hour": {"used_percentage": used, "resets_at": now + 3600}}, fh)
    write_rollout(_folder(ws, codex), "/elsewhere", now - 100,
                  [(now - 90, {"input_tokens": 1}, {"primary": {"used_percent": 40.0, "window_minutes": 300,
                                                                "resets_at": now + 600}})])
    view = ws.http.get("/accounts").json()
    claude_rows = {r["id"]: r for r in view["claude"]["accounts"]}
    assert claude_rows["default"]["usage"]["five_hour"]["used_percentage"] == 12.0
    assert claude_rows[claude["id"]]["usage"]["five_hour"]["used_percentage"] == 91.0
    assert (claude_rows["default"]["sessions"], claude_rows[claude["id"]]["sessions"]) == (1, 1)
    codex_rows = {r["id"]: r for r in view["codex"]["accounts"]}
    assert codex_rows[codex["id"]]["usage"]["five_hour"]["used_percentage"] == 40.0
    assert codex_rows["default"]["usage"] is None
    # the windows alone, as the title bar asks for them often: no program asked its status
    calls = (ws.home / "claude-calls.txt").read_text() if (ws.home / "claude-calls.txt").exists() else ""
    quick = ws.http.get("/accounts?status=0").json()
    assert [r["status"] for k in ("claude", "codex") for r in quick[k]["accounts"]] == [None] * 4
    assert {r["id"]: r["usage"] for r in quick["claude"]["accounts"]} == \
        {r["id"]: r["usage"] for r in view["claude"]["accounts"]}
    after = (ws.home / "claude-calls.txt").read_text() if (ws.home / "claude-calls.txt").exists() else ""
    assert after.count("auth status") == calls.count("auth status")


def test_an_account_a_session_runs_on_stays_and_one_removed_is_signed_out_first(ws):
    account = _add(ws)
    ws.http.post("/accounts/choose", json={"kind": "claude", "account": account["id"]})
    s = _start(ws)
    r = ws.http.delete(f"/accounts/{account['id']}")
    assert r.status_code == 400 and "1 session runs on Work: remove it first." == r.json()["detail"]
    assert ws.http.delete(f"/sessions/{s['id']}").status_code == 200
    assert ws.http.delete(f"/accounts/{account['id']}").json() == {"removed": account["id"]}
    assert f"auth logout @ {_folder(ws, account)}" in (ws.home / "claude-calls.txt").read_text()
    assert not os.path.exists(os.path.join(ws.root, "agent-accounts", account["id"]))
    view = ws.http.get("/accounts").json()["claude"]
    assert [r["id"] for r in view["accounts"]] == ["default"] and view["active"] == "default"
    # the person's own setup, linked into it, is still theirs: a link is removed, never what it leads to
    assert (ws.home / ".claude" / "settings.json").read_text() == '{"theme": "light"}'
    assert (ws.home / ".claude" / "agents" / "reviewer.md").read_text() == "Review carefully.\n"
    assert ws.http.delete(f"/accounts/{account['id']}").status_code == 404


@pytest.mark.parametrize("payload", [
    {"tool_name": "Read", "tool_input": {"file_path": "/home/u/ws/agent-accounts/0123456789ab/claude/.credentials.json"}},
    {"tool_name": "Bash", "tool_input": {"command": "cat ~/ws/agent-accounts/0123456789ab/codex/auth.json"}},
    {"tool_name": "Bash", "tool_input": {"command": 'cat "$CLAUDE_CONFIG_DIR/.credentials.json"'}},
    {"tool_name": "Bash", "tool_input": {"command": "cat agent-accounts.json"}},
    {"tool_name": "Edit", "tool_input": {"file_path": "/home/u/ws/agent-accounts.json", "old_string": "a",
                                         "new_string": "b"}},
])
def test_the_gate_refuses_an_account_s_sign_in_and_the_record_of_the_accounts(payload):
    out = gate.decide(dict(payload, session_id="s", cwd="/w"), home="/home/u")
    assert out is not None and out["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("payload", [
    # Claude Code's own work in its account's folder: a plan, its memory, a tool's output it saved and is told to read
    {"tool_name": "Write", "tool_input": {"file_path": "/home/u/ws/agent-accounts/0123456789ab/claude/plans/fix.md",
                                          "content": "1. Fix it"}},
    {"tool_name": "Edit", "tool_input": {"file_path": "/home/u/ws/agent-accounts/0123456789ab/claude/projects/-w/memory/"
                                                      "MEMORY.md", "old_string": "a", "new_string": "b"}},
    {"tool_name": "Read", "tool_input": {"file_path": "/home/u/ws/agent-accounts/0123456789ab/claude/projects/-w/s1/"
                                                      "tool-results/out.txt"}},
])
def test_the_gate_lets_claude_code_work_in_its_account_s_folder(payload):
    assert gate.decide(dict(payload, session_id="s", cwd="/w"), home="/home/u") is None


@pytest.mark.parametrize("command", ['cat "$CODEX_HOME/auth.json"', "cat ${CODEX_HOME}/auth.json",
                                     'cp "$CODEX_HOME"/auth.json /tmp/x', "base64 $CODEX_HOME/'auth.json'"])
def test_the_gate_refuses_codex_s_sign_in_through_its_home_s_variable(command):
    # a Codex session on another account has CODEX_HOME, the readiest way to name its sign-in
    out = gate.decide({"tool_name": "Bash", "tool_input": {"command": command}, "session_id": "s", "cwd": "/w"},
                      home="/home/u")
    assert out is not None and out["hookSpecificOutput"]["permissionDecision"] == "deny", command


def test_the_gate_lets_codex_s_home_itself_be_named():
    for command in ("echo $CODEX_HOME", "ls $CODEX_HOME/sessions", "cat ./auth.json.example"):
        assert gate.decide({"tool_name": "Bash", "tool_input": {"command": command}, "session_id": "s", "cwd": "/w"},
                           home="/home/u") is None, command


def test_the_gate_lets_a_name_that_only_begins_alike_pass():
    payload = {"tool_name": "Read", "tool_input": {"file_path": "/w/src/agent-accounts-page.tsx"}, "session_id": "s",
               "cwd": "/w"}
    assert gate.decide(payload, home="/home/u") is None


def test_a_claude_started_again_resumes_its_own_conversation_from_its_account_s_folder(ws):
    from proofpane_workspace import usage
    account = _add(ws)
    s = _start(ws, account=account["id"])
    _said(ws, s["id"])
    ws.http.post(f"/sessions/{s['id']}/stop")
    work = os.path.realpath(os.path.join(ws.root, "sessions", s["id"], "work"))
    with open(os.path.join(ws.root, "sessions", s["id"], "activity.json"), "w") as fh:
        json.dump({"state": "idle", "at": int(time.time()), "program_session": "conv-1"}, fh)
    # its own conversation's transcript, in the default folder only: not this session's account's, so not resumed
    os.makedirs(ws.home / ".claude" / "projects" / usage.slug(work))
    (ws.home / ".claude" / "projects" / usage.slug(work) / "conv-1.jsonl").write_text("{}\n")
    ws.http.post(f"/sessions/{s['id']}/start", json={})
    assert "--resume" not in _said(ws, s["id"])
    ws.http.post(f"/sessions/{s['id']}/stop")
    mine = os.path.join(_folder(ws, account), "projects", usage.slug(work))
    os.makedirs(mine)
    open(os.path.join(mine, "conv-1.jsonl"), "w").write("{}\n")
    ws.http.post(f"/sessions/{s['id']}/start", json={})
    # after the control window's map settings every claude terminal start is given first (workspace._with_map)
    mapped = os.path.join(ws.root, "sessions", s["id"], "map-settings.json")
    assert f"args:--settings|{mapped}|--resume|conv-1:end" in _said(ws, s["id"])


def test_a_session_s_tokens_are_read_from_its_account_s_folder(ws):
    from test_workspace_usage import line
    from proofpane_workspace import usage
    claude, codex = _add(ws), _add(ws, kind="codex", name="Team")
    s = _start(ws, account=claude["id"])
    work = os.path.realpath(os.path.join(ws.root, "sessions", s["id"], "work"))
    for folder, tokens in ((str(ws.home / ".claude"), 1000), (_folder(ws, claude), 7)):
        os.makedirs(os.path.join(folder, "projects", usage.slug(work)), exist_ok=True)
        with open(os.path.join(folder, "projects", usage.slug(work), "a.jsonl"), "w") as fh:
            fh.write(line(work, "m1", input_tokens=tokens))
    assert ws.http.get(f"/sessions/{s['id']}/usage").json()["input_tokens"] == 7
    c = _start(ws, program="codex", account=codex["id"])
    cwork = os.path.realpath(os.path.join(ws.root, "sessions", c["id"], "work"))
    now = int(time.time())
    write_rollout(str(ws.home / ".codex"), cwork, now, [], calls=[{"input_tokens": 500}])
    write_rollout(_folder(ws, codex), cwork, now, [], calls=[{"input_tokens": 9}])
    assert ws.http.get(f"/sessions/{c['id']}/usage").json()["input_tokens"] == 9


def test_on_the_person_s_own_machine_codex_signs_in_with_its_browser(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "codex").write_text(CODEX.format(python=sys.executable))
    (bin_dir / "codex").chmod(0o755)
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": f"{bin_dir}:{os.environ['PATH']}"}, args=["--link-repos"])
    try:
        s = a.http.post("/sessions", json={"repo": None, "program": "codex-sign-in", "task": "Sign in"}).json()
        assert "codex:default|args:login:end" in _said(a, s["id"])
    finally:
        a.close()


def test_its_folder_and_record_are_the_agent_s_user_s_alone_whatever_the_umask(tmp_path, monkeypatch):
    # the agent keeps a strict umask of its own; these hold under a loose one too
    from proofpane_workspace import accounts
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    old = os.umask(0o022)
    try:
        book = accounts.Accounts(str(tmp_path / "ws"))
        os.makedirs(tmp_path / "ws" / "agent-accounts")         # there already, as anyone may read it: closed
        os.chmod(tmp_path / "ws" / "agent-accounts", 0o755)
        account = book.add("claude", "Work")
    finally:
        os.umask(old)
    folder = book.folder(account)
    for path in (folder, os.path.dirname(folder), book.dir):
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o700, path
    assert stat.S_IMODE(os.stat(book.path).st_mode) == 0o600
    # removed while new sessions start on it: none chosen after, on disk as read back
    book.choose("claude", account["id"])
    book.remove(account["id"])
    assert json.load(open(book.path))["active"] == {"claude": None, "codex": None}


def test_its_own_sign_ins_never_keep_it_and_go_with_it(ws):
    # a sign-in is not a session the page lists, so it must not keep the account from being removed: finished or still
    # waiting for its code, it goes with the account
    account = _add(ws)
    done = ws.http.post("/sessions", json={"repo": None, "program": "claude-sign-in", "task": "Sign in",
                                           "account": account["id"]}).json()
    waiting = ws.http.post("/sessions", json={"repo": None, "program": "claude-sign-in", "task": "Sign in",
                                              "account": account["id"]}).json()
    ws.http.post(f"/sessions/{done['id']}/stop")
    rows = {r["id"]: r for r in ws.http.get("/accounts?status=0").json()["claude"]["accounts"]}
    assert rows[account["id"]]["sessions"] == 0
    assert ws.http.delete(f"/accounts/{account['id']}").json() == {"removed": account["id"]}
    ids = [s["id"] for s in ws.http.get("/sessions").json()["sessions"]]
    assert done["id"] not in ids and waiting["id"] not in ids
    assert not os.path.exists(os.path.join(ws.root, "sessions", waiting["id"]))


# guards on the accounts: a sign-in shown as one, a session's binding, the record, sign-outs, status, a choice, a count


def _rewrite(ws, sid, change):
    path = os.path.join(ws.root, "sessions", sid, "session.json")
    record = json.load(open(path))
    change(record)
    json.dump(record, open(path, "w"))


@pytest.mark.parametrize("program, says, field", [
    ("claude-sign-in", f"Paste code here if prompted > {URL}", "sign_in_url"),
    ("codex-sign-in", "1. Open https://auth.openai.com/codex/device 2. Enter ABCD-12345", "sign_in_text"),
    ("github", "! First copy your one-time code: ABCD-1234", "device_code"),
])
def test_a_session_that_calls_itself_a_sign_in_is_never_shown_as_one(ws, program, says, field):
    # its own terminal shows a genuine sign-in's page and code, which it runs there: the page would have the person
    # sign it in to their own account. Only a sign-in this agent started is shown as one, or takes a code
    s = ws.http.post("/sessions", json={"repo": None, "program": "shell", "task": "t"}).json()
    with ws.terminal(s["id"]) as t:
        t.send(f"echo '{says[:10]}''{says[10:]}'\n".encode())
        read_until(t, says[-12:].encode())
    _rewrite(ws, s["id"], lambda r: r.update(program=program))
    view = ws.http.get(f"/sessions/{s['id']}").json()
    assert (view["program"], view["sign_in"], view[field]) == (program, False, None)
    r = ws.http.post(f"/sessions/{s['id']}/sign-in-code", json={"code": GOOD})
    assert r.status_code == 400 and "Only a running Claude sign-in takes a code" in r.json()["detail"]


def test_a_sign_in_the_agent_started_is_shown_as_one(ws):
    account = _add(ws)
    s = ws.http.post("/sessions", json={"repo": None, "program": "claude-sign-in", "task": "Sign in",
                                        "account": account["id"]}).json()
    deadline = time.time() + 20
    while not (view := ws.http.get(f"/sessions/{s['id']}").json())["sign_in_url"] and time.time() < deadline:
        time.sleep(0.1)
    assert (view["sign_in"], view["sign_in_url"]) == (True, URL)


def test_a_session_s_account_is_never_read_from_its_own_record(ws):
    # its programs can write session.json: dropping its account there, or naming another, changes nothing
    work, other = _add(ws), _add(ws, name="Other")
    s = _start(ws, account=work["id"])
    _said(ws, s["id"])
    ws.http.post(f"/sessions/{s['id']}/stop")
    for change in (lambda r: r.pop("account", None), lambda r: r.update(account=other["id"])):
        _rewrite(ws, s["id"], change)
        assert ws.http.get(f"/sessions/{s['id']}").json()["account"] == work["id"]
        ws.http.post(f"/sessions/{s['id']}/start", json={})
        assert f"started:{_folder(ws, work)}|" in _said(ws, s["id"])
        ws.http.post(f"/sessions/{s['id']}/stop")
    # it still counts on its account, which is not removed under it; the other is free to go
    rows = {r["id"]: r for r in ws.http.get("/accounts?status=0").json()["claude"]["accounts"]}
    assert (rows[work["id"]]["sessions"], rows[other["id"]]["sessions"]) == (1, 0)
    assert ws.http.delete(f"/accounts/{work['id']}").status_code == 400
    assert ws.http.delete(f"/accounts/{other['id']}").json() == {"removed": other["id"]}
    # removed, the session takes its binding with it
    assert ws.http.delete(f"/sessions/{s['id']}").status_code == 200
    assert json.load(open(os.path.join(ws.root, "agent-accounts.json")))["sessions"] == {}


def test_an_unreadable_record_of_the_accounts_is_never_written_over(ws):
    account = _add(ws)
    registry = os.path.join(ws.root, "agent-accounts.json")
    open(registry, "w").write("{not json")
    for method, path, body in (("POST", "/accounts", {"kind": "claude", "name": "New"}),
                               ("POST", "/accounts/choose", {"kind": "claude", "account": "default"}),
                               ("POST", "/sessions", {"repo": "demo", "program": "claude", "task": "t",
                                                      "account": account["id"]})):
        r = ws.http.request(method, path, json=body)
        assert r.status_code == 400, (path, r.text)
    assert open(registry).read() == "{not json"
    assert os.path.isdir(_folder(ws, account))
    # a session on it is not started on the program's own sign-in instead; the list is still read
    open(registry, "w").write(json.dumps({"accounts": [account], "active": {}, "sessions": {}}))
    s = _start(ws, account=account["id"])
    ws.http.post(f"/sessions/{s['id']}/stop")
    open(registry, "w").write("{not json")
    r = ws.http.post(f"/sessions/{s['id']}/start", json={})
    assert r.status_code == 400 and "cannot be read" in r.json()["detail"], r.text
    assert [x["id"] for x in ws.http.get("/sessions").json()["sessions"]] == [s["id"]]
    r = ws.http.post("/sessions", json={"repo": "demo", "program": "claude", "task": "t"})
    assert r.status_code == 400 and "cannot be read" in r.json()["detail"], r.text
    assert ws.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "t"}).status_code == 200


def test_in_a_workspace_machine_an_account_trusts_the_workspace_and_a_session_s_repository(tmp_path):
    # as the machine's own claude does there (--claude-first-run, start.sh): its claude opens at its prompt
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home), args=["--claude-first-run"])
    try:
        make_repo(str(tmp_path / "ws" / "repos" / "demo"))
        account = a.http.post("/accounts", json={"kind": "claude", "name": "Work"}).json()
        folder = os.path.join(a.root, "agent-accounts", account["id"], "claude")
        seeded = json.load(open(os.path.join(folder, ".claude.json")))
        assert seeded["hasCompletedOnboarding"] is True
        assert seeded["projects"][os.path.realpath(a.root)]["hasTrustDialogAccepted"] is True
        r = a.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "t"})      # a shell: no account
        assert r.json()["account"] is None
    finally:
        a.close()


def test_on_the_person_s_own_machine_their_own_sign_in_is_not_ended_from_here(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    # stand-ins of the test's own first on the PATH, as the suite's guard asks (conftest.py): Claude Code's sign-out
    # looks for an installed claude before it refuses, so on a machine with none on the PATH the
    # refusal is another; each says its version as a claude does, and writes down any other call, of which no sign-out
    # may be one, the refusal coming first
    bin_dir, ran = tmp_path / "bin", tmp_path / "ran"
    bin_dir.mkdir()
    for name in ("claude", "codex"):
        (bin_dir / name).write_text("#!/bin/sh\n" + CLAUDE_VERSION_SH + f"echo \"{name} $*\" >> '{ran}'\nexit 97\n")
        (bin_dir / name).chmod(0o755)
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": f"{bin_dir}:{os.environ['PATH']}"}, args=["--link-repos"])
    try:
        for path, body in (("/claude/sign-out", {}), ("/accounts/default/sign-out", {"kind": "codex"}),
                           ("/accounts/default/sign-out", {"kind": "claude"})):
            r = a.http.post(path, json=body)
            assert r.status_code == 400 and "your own machine" in r.json()["detail"], (path, body, r.text)
        assert "logout" not in (ran.read_text() if ran.exists() else ""), ran.read_text()
    finally:
        a.close()


def test_an_account_whose_sign_out_does_not_take_is_kept(ws):
    account = _add(ws)
    (ws.home / "logout-fails").write_text("")
    r = ws.http.delete(f"/accounts/{account['id']}")
    assert r.status_code == 400 and "could not be signed out" in r.json()["detail"], r.text
    assert os.path.isdir(_folder(ws, account))
    assert [a["id"] for a in ws.http.get("/accounts?status=0").json()["claude"]["accounts"]] == ["default", account["id"]]
    (ws.home / "logout-fails").unlink()
    assert ws.http.delete(f"/accounts/{account['id']}").json() == {"removed": account["id"]}


def test_a_sign_in_not_said_in_time_is_not_known_and_never_a_sign_out(tmp_path, monkeypatch):
    # Claude Code's and Codex's own status slow to answer: neither signed in nor out, so New session holds neither
    # back and nothing sends the person to sign in again
    from proofpane_workspace import workspace
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("claude", "codex"):
        (bin_dir / name).write_text(f"#!{sys.executable}\nimport time\ntime.sleep(5)\n")
        (bin_dir / name).chmod(0o755)
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setattr(workspace, "CLAUDE_TIMEOUT", 0.3)
    w = workspace.Workspace(str(tmp_path / "ws"))
    assert w.claude() == {"installed": True, "signed_in": None, "email": None, "method": None, "subscription": None}
    assert w.codex_status() == {"installed": True, "signed_in": None, "method": None}


def test_a_choice_made_while_the_accounts_are_read_is_the_one_said(ws):
    import threading
    account = _add(ws)
    (ws.home / "slow-status").write_text("")
    got = {}
    t = threading.Thread(target=lambda: got.update(view=ws.http.get("/accounts").json()))
    t.start()
    time.sleep(0.4)
    assert ws.http.post("/accounts/choose", json={"kind": "claude", "account": account["id"]}).status_code == 200
    t.join(30)
    assert got["view"]["claude"]["active"] == account["id"]


def test_two_readings_of_codex_s_files_at_once_count_each_call_once(tmp_path, monkeypatch):
    import threading
    from proofpane_workspace import usage
    home, work = str(tmp_path / "codex"), str(tmp_path / "work")
    os.makedirs(work)
    write_rollout(home, os.path.realpath(work), int(time.time()), [], calls=[{"input_tokens": 5}] * 3)
    real = usage._read_on
    inside, most = [0], [0]

    def slow(path, known, budget):                  # how many readings are in the files at once
        inside[0] += 1
        most[0] = max(most[0], inside[0])
        time.sleep(0.3)
        try:
            return real(path, known, budget)
        finally:
            inside[0] -= 1

    monkeypatch.setattr(usage, "_read_on", slow)
    usage._read_so_far.clear()
    got = []
    threads = [threading.Thread(target=lambda: got.append(usage.codex_usage(work, home)["input_tokens"])),
               threading.Thread(target=lambda: got.append(usage.codex_limits(home)))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert most[0] == 1 and 15 in got
