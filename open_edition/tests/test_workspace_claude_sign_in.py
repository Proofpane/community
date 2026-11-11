"""Claude Code's own sign-in, driven from the page (a sign-in a person new to it can do on a phone): the
agent runs `claude auth login`, reports the page it prints while it waits, and types into it the code that page gives,
checked first; Claude Code keeps what it gets in its own credentials file, and `claude auth status` says whom it
signed in. A stand-in `claude` answers as the real one does in the image (seen 2026-09-28): the link as a terminal
hyperlink, then "Paste code here if prompted > ", and a line read from its terminal."""

import os
import sys
import time

import pytest

from proofpane_workspace import workspace
from workspace_fixtures import Agent, read_until

URL = ("https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e"
       "&response_type=code&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback"
       "&scope=user%3Ainference&code_challenge=abc_DEF-123&code_challenge_method=S256&state=xyz-789")
GOOD = "GOOD-code_123#state.4"

STAND_IN = r'''#!{python}
import json, os, sys
if sys.argv[1:2] in (["-v"], ["--version"]):        # as the real one says it
    print("2.1.274 (Claude Code)")
    sys.exit(0)
marker = os.path.join(os.environ["HOME"], ".claude-signed-in")
args = sys.argv[1:3]
if args == ["auth", "login"]:
    url = {url!r}
    sys.stdout.write("Opening browser to sign in…\r\n")
    sys.stdout.write("If the browser didn't open, visit: \x1b]8;;" + url + "\x1b\\" + url + "\x1b]8;;\x1b\\\r\n")
    sys.stdout.write("Paste code here if prompted > ")
    sys.stdout.flush()
    code = sys.stdin.readline().rstrip("\r\n")          # as typed: the agent sends the code and Enter, nothing else
    if code == {good!r}:
        open(marker, "w").close()
        print("Login successful.")
        sys.exit(0)
    print("OAuth error: the code was not accepted", file=sys.stderr)
    sys.exit(1)
if args == ["auth", "logout"]:              # as the real one says it; the file the agent makes sure of below
    if os.path.exists(marker):
        os.remove(marker)
    print("Successfully logged out from your Anthropic account.")
    sys.exit(0)
if args == ["auth", "status"]:
    odd = os.path.join(os.environ["HOME"], ".claude-status")      # a test's own answer, as it is
    if os.path.exists(odd):
        sys.stdout.write(open(odd).read())
        sys.exit(0)
    if os.path.exists(marker):
        print(json.dumps({{"loggedIn": True, "authMethod": "claude.ai", "email": "owner@example.org",
                          "subscriptionType": "max", "orgName": "Example"}}))
        sys.exit(0)
    print(json.dumps({{"loggedIn": False, "authMethod": "none"}}))
    sys.exit(1)
sys.exit(2)
'''


def _fake_claude(bin_dir):
    bin_dir.mkdir(exist_ok=True)
    claude = bin_dir / "claude"
    claude.write_text(STAND_IN.format(python=sys.executable, url=URL, good=GOOD))
    claude.chmod(0o755)


@pytest.fixture
def cagent(tmp_path):
    _fake_claude(tmp_path / "bin")
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}"})
    yield a
    a.close()


# the same stand-in, but one that, after the code that signs it in, says so and then waits for a key, as the real one
# may do: what the real Claude Code does after a code was never seen
STAND_IN_WAITS = STAND_IN.replace('        print("Login successful.")\n        sys.exit(0)',
                                  '        print("Login successful. Press Enter to continue")\n'
                                  '        sys.stdout.flush()\n'
                                  '        sys.stdin.readline()\n'
                                  '        sys.exit(0)')
assert STAND_IN_WAITS != STAND_IN


@pytest.fixture
def cagent_waits(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    claude = bin_dir / "claude"
    claude.write_text(STAND_IN_WAITS.format(python=sys.executable, url=URL, good=GOOD))
    claude.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": f"{bin_dir}:{os.environ['PATH']}"})
    yield a
    a.close()


# a stand-in that asks again after a code it refuses, as a prompt that waits for the right one would, and that after
# the right one writes the account's credentials file anew, as a sign-in does, says so and waits for a key
# (an account signed in already is ended only once that file has changed)
_ONE_CODE = r"""    code = sys.stdin.readline().rstrip("\r\n")          # as typed: the agent sends the code and Enter, nothing else
    if code == {good!r}:
        open(marker, "w").close()
        print("Login successful.")
        sys.exit(0)
    print("OAuth error: the code was not accepted", file=sys.stderr)
    sys.exit(1)
"""
_CODES = r"""    while True:
        code = sys.stdin.readline().rstrip("\r\n")
        if code == {good!r}:
            open(marker, "w").close()
            creds = os.path.join(os.environ["HOME"], ".claude", ".credentials.json")
            os.makedirs(os.path.dirname(creds), exist_ok=True)
            with open(creds, "w") as fh:
                fh.write('{{"signed": "anew"}}')
            print("Login successful. Press Enter to continue")
            sys.stdout.flush()
            sys.stdin.readline()
            sys.exit(0)
        print("OAuth error: the code was not accepted. Paste code here if prompted > ")
        sys.stdout.flush()
"""
STAND_IN_REPROMPTS = STAND_IN.replace(_ONE_CODE, _CODES)
assert STAND_IN_REPROMPTS != STAND_IN


@pytest.fixture
def cagent_reprompts(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    claude = bin_dir / "claude"
    claude.write_text(STAND_IN_REPROMPTS.format(python=sys.executable, url=URL, good=GOOD))
    claude.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": f"{bin_dir}:{os.environ['PATH']}"})
    yield a
    a.close()


def _signed_in_already(tmp_path):
    """The default account signed in before any sign-in starts: the stand-in's own word for it, and a credentials file
    where Claude Code keeps one."""
    home = tmp_path / "home"
    (home / ".claude-signed-in").write_text("")
    creds = home / ".claude" / ".credentials.json"
    creds.parent.mkdir(exist_ok=True)
    creds.write_text("{}")

def _until(agent, sid, key, want=lambda v: v is not None):
    deadline = time.time() + 20
    while True:
        got = agent.http.get(f"/sessions/{sid}").json()
        if want(got[key]):
            return got
        assert time.time() < deadline, got
        time.sleep(0.05)


def _sign_in(agent):
    r = agent.http.post("/sessions", json={"program": "claude-sign-in", "task": "Sign in to Claude"})
    assert r.status_code == 200, r.text
    s = r.json()
    assert (s["program"], s["repo"]) == ("claude-sign-in", None)
    return _until(agent, s["id"], "sign_in_url")


@pytest.mark.parametrize("line, url", [
    (b"visit: \x1b]8;;" + URL.encode() + b"\x1b\\" + URL.encode() + b"\x1b]8;;\x1b\\\r\n", URL),
    (b"visit: https://claude.ai/oauth/authorize?client_id=a&state=b\r\n",
     "https://claude.ai/oauth/authorize?client_id=a&state=b"),
    (b"https://claude.com.example.test/cai/oauth/authorize?x=1", None),
    (b"http://claude.com/cai/oauth/authorize?x=1", None),
    (b"https://claude.com/oauth/authorize?x=1", None),
    (b"https://claude.com/cai/oauth/authorize", None)])
def test_only_claude_s_own_sign_in_page_is_read_from_its_output(line, url):
    found = workspace.CLAUDE_SIGN_IN_URL.findall(line)
    assert (found[-1].decode() if found else None) == url


def test_the_sign_in_s_page_is_on_its_session_and_the_code_from_it_signs_claude_in(cagent):
    assert cagent.http.get("/claude").json() == {"installed": True, "signed_in": False, "email": None,
                                                 "method": "none", "subscription": None, "account": "default"}
    shell = cagent.http.post("/sessions", json={"program": "shell", "task": "t"}).json()
    with cagent.terminal(shell["id"]) as ws:              # only a Claude sign-in's output is read for its page
        ws.send(f"echo '{URL[:20]}''{URL[20:]}'\n".encode())
        read_until(ws, URL[-20:].encode())
    got = _sign_in(cagent)
    assert got["sign_in_url"] == URL and got["device_code"] is None and got["running"] is True
    assert {x["id"]: x["sign_in_url"] for x in cagent.http.get("/sessions").json()["sessions"]} == {
        shell["id"]: None, got["id"]: URL}
    r = cagent.http.post(f"/sessions/{got['id']}/sign-in-code", json={"code": f"  {GOOD}\n"})
    assert r.status_code == 200, r.text
    done = _until(cagent, got["id"], "running", lambda v: v is False)
    assert (done["exit_code"], done["sign_in_url"]) == (0, None)
    assert cagent.http.get("/claude").json() == {"installed": True, "signed_in": True, "email": "owner@example.org",
                                                 "method": "claude.ai", "subscription": "max", "account": "default"}


@pytest.mark.parametrize("code", ["short", "a code with spaces", "line\nbreak-code", "semi;colon-code",
                                  "\x1b[Aarrow-code", "x" * 1025, 12345678, None])
def test_only_a_code_s_own_characters_are_typed_into_the_sign_in(cagent, code):
    got = _sign_in(cagent)
    r = cagent.http.post(f"/sessions/{got['id']}/sign-in-code", json={"code": code})
    assert r.status_code == 400 and "not a sign-in code" in r.json()["detail"]
    time.sleep(0.3)
    assert cagent.http.get(f"/sessions/{got['id']}").json()["running"] is True       # nothing reached it


def test_a_code_goes_only_to_a_claude_sign_in_that_is_still_running(cagent):
    shell = cagent.http.post("/sessions", json={"program": "shell", "task": "t"}).json()
    r = cagent.http.post(f"/sessions/{shell['id']}/sign-in-code", json={"code": GOOD})
    assert r.status_code == 400 and "Only a running Claude sign-in takes a code." == r.json()["detail"]
    got = _sign_in(cagent)
    assert cagent.http.post(f"/sessions/{got['id']}/sign-in-code", json={"code": "WRONG-code-1234"}).status_code == 200
    done = _until(cagent, got["id"], "running", lambda v: v is False)
    assert done["exit_code"] == 1 and cagent.http.get("/claude").json()["signed_in"] is False
    r = cagent.http.post(f"/sessions/{got['id']}/sign-in-code", json={"code": GOOD})
    assert r.status_code == 400 and "Only a running Claude sign-in" in r.json()["detail"]


@pytest.mark.parametrize("answer", [
    '{"loggedIn": "true", "email": "not-an-email", "authMethod": "' + "x" * 41 + '", "subscriptionType": ["max"]}',
    '{"loggedIn": 1, "email": "a@b@c", "authMethod": "claude.ai\\n", "subscriptionType": null}',
    "not json at all", "[true]", ""])
def test_only_claude_s_checked_status_fields_are_passed_on(cagent, tmp_path, answer):
    (tmp_path / "home" / ".claude-status").write_text(answer)
    assert cagent.http.get("/claude").json() == {"installed": True, "signed_in": False, "email": None,
                                                 "method": None, "subscription": None, "account": "default"}


def test_signing_out_uses_claude_s_own_sign_out_and_leaves_no_credentials_file(cagent, tmp_path):
    # "Sign out of Claude" deletes the credentials file and says so; the real `claude auth logout` was seen to delete it
    # in the image, and the agent makes sure of it
    got = _sign_in(cagent)
    cagent.http.post(f"/sessions/{got['id']}/sign-in-code", json={"code": GOOD})
    _until(cagent, got["id"], "running", lambda v: v is False)
    creds = tmp_path / "home" / ".claude" / ".credentials.json"
    creds.parent.mkdir(exist_ok=True)
    creds.write_text("{}")
    assert cagent.http.get("/claude").json()["signed_in"] is True
    r = cagent.http.post("/claude/sign-out")
    assert r.status_code == 200 and r.json()["signed_in"] is False
    assert not creds.exists() and not (tmp_path / "home" / ".claude-signed-in").exists()
    assert cagent.http.post("/claude/sign-out").json()["signed_in"] is False       # again: nothing to delete


def test_without_claude_the_page_is_told_so(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": "/usr/bin:/bin"})
    try:
        assert a.http.get("/claude").json() == {"installed": False, "signed_in": False, "email": None,
                                                "method": None, "subscription": None, "account": "default"}
        r = a.http.post("/sessions", json={"program": "claude-sign-in", "task": "t"})
        assert r.status_code == 400 and "claude is not installed" in r.json()["detail"]
        r = a.http.post("/claude/sign-out")
        assert r.status_code == 400 and "claude is not installed" in r.json()["detail"]
    finally:
        a.close()


def test_the_app_passes_claude_s_status_and_the_code_through(client, monkeypatch, tmp_path, env):
    from conftest import signed_up
    home = tmp_path / "apphome"
    home.mkdir()
    _fake_claude(tmp_path / "appbin")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{tmp_path / 'appbin'}:{os.environ['PATH']}")
    monkeypatch.setenv("OPEN_EDITION_ADE", "local")
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    from proofpane_open.ade import workspaces
    try:
        h = signed_up(client)
        assert client.get("/api/ade/workspace/claude").status_code == 401
        assert client.post("/api/ade/workspace/start", headers=h).status_code == 200
        assert client.get("/api/ade/workspace/claude", headers=h).json()["signed_in"] is False
        s = client.post("/api/ade/workspace/sessions", headers=h,
                        json={"program": "claude-sign-in", "task": "Sign in to Claude"}).json()
        deadline = time.time() + 20
        while client.get(f"/api/ade/workspace/sessions/{s['id']}", headers=h).json()["sign_in_url"] is None:
            assert time.time() < deadline
            time.sleep(0.05)
        path = f"/api/ade/workspace/sessions/{s['id']}/sign-in-code"
        assert client.post(path, json={"code": GOOD}).status_code == 401
        r = client.post(path, headers=h, json={"code": "no good"})
        assert r.status_code == 400 and "not a sign-in code" in r.json()["detail"]
        assert client.post(path, headers=h, json={"code": GOOD}).status_code == 200
        while client.get(f"/api/ade/workspace/sessions/{s['id']}", headers=h).json()["running"]:
            assert time.time() < deadline
            time.sleep(0.05)
        assert client.get("/api/ade/workspace/claude", headers=h).json()["email"] == "owner@example.org"
        assert client.post("/api/ade/workspace/claude/sign-out").status_code == 401
        # the local provider's workspace is on the person's own machine: their own Claude Code is not signed out
        # from here (their terminal's would be too)
        r = client.post("/api/ade/workspace/claude/sign-out", headers=h)
        assert r.status_code == 400 and "your own machine" in r.json()["detail"]
    finally:
        workspaces.shutdown()


def test_a_sign_in_that_ends_without_signing_in_says_what_it_said(cagent):
    # a code Claude's page did not take: the sign-in ends, and the page shows its last words, not silence
    got = _sign_in(cagent)
    assert got["sign_in_text"] is None                          # while it waits, its page and code box are the steps
    r = cagent.http.post(f"/sessions/{got['id']}/sign-in-code", json={"code": "WRONG-code_456#state.9"})
    assert r.status_code == 200, r.text
    done = _until(cagent, got["id"], "running", lambda v: v is False)
    assert done["exit_code"] == 1 and "OAuth error: the code was not accepted" in done["sign_in_text"]
    assert cagent.http.get("/claude").json()["signed_in"] is False


def test_a_sign_in_that_signed_in_ends_once_claude_code_says_so_though_its_program_waits(cagent_waits):
    # after a code, the agent reads the account's status every few seconds and, once Claude Code
    # says it is signed in, ends the sign-in's program and session, though that program still waits for a key
    got = _sign_in(cagent_waits)
    r = cagent_waits.http.post(f"/sessions/{got['id']}/sign-in-code", json={"code": GOOD})
    assert r.status_code == 200, r.text
    done = _until(cagent_waits, got["id"], "running", lambda v: v is False)
    assert done["running"] is False
    assert cagent_waits.http.get("/claude").json()["signed_in"] is True


def test_a_code_that_did_not_sign_in_leaves_the_sign_in_s_end_to_its_program(cagent_waits):
    # the agent ends a sign-in only on Claude Code's own word that it signed in: a code that did not sign it in
    # leaves its end to the program, which here ends on a code it refuses, and the account is not signed in
    got = _sign_in(cagent_waits)
    assert cagent_waits.http.post(f"/sessions/{got['id']}/sign-in-code", json={"code": "WRONG-code-1234"}).status_code \
        == 200
    _until(cagent_waits, got["id"], "running", lambda v: v is False)     # the stand-in ends on a code it refuses
    assert cagent_waits.http.get("/claude").json()["signed_in"] is False


def test_a_second_claude_sign_in_of_an_account_stops_the_first(cagent):
    # one Claude sign-in per account at a time, so that retries leave no program running
    first = _sign_in(cagent)
    second = _sign_in(cagent)
    assert second["id"] != first["id"]
    assert _until(cagent, first["id"], "running", lambda v: v is False)["running"] is False
    assert cagent.http.get(f"/sessions/{second['id']}").json()["running"] is True
    running = [s["id"] for s in cagent.http.get("/sessions").json()["sessions"]
               if s["program"] == "claude-sign-in" and s["running"]]
    assert running == [second["id"]]



def test_a_wrong_code_on_an_account_signed_in_already_leaves_its_sign_in_running(cagent_reprompts, tmp_path):
    # an account whose credentials file was there before its code is ended only once that file has
    # changed, a sign-in's own writing of it: a code that did not sign it in, its program asking again, ends nothing,
    # though the account's status says signed in, as it did before the code
    _signed_in_already(tmp_path)
    got = _sign_in(cagent_reprompts)
    assert cagent_reprompts.http.post(f"/sessions/{got['id']}/sign-in-code",
                                      json={"code": "WRONG-code-1234"}).status_code == 200
    time.sleep(3 * workspace.SIGN_IN_WATCH_EVERY + 1)
    assert cagent_reprompts.http.get(f"/sessions/{got['id']}").json()["running"] is True
    assert cagent_reprompts.http.get("/claude").json()["signed_in"] is True


def test_the_right_code_on_an_account_signed_in_already_ends_its_sign_in(cagent_reprompts, tmp_path):
    _signed_in_already(tmp_path)
    got = _sign_in(cagent_reprompts)
    assert cagent_reprompts.http.post(f"/sessions/{got['id']}/sign-in-code", json={"code": GOOD}).status_code == 200
    assert _until(cagent_reprompts, got["id"], "running", lambda v: v is False)["running"] is False
    assert cagent_reprompts.http.get("/claude").json()["signed_in"] is True


def test_a_wrong_code_on_an_account_signed_out_leaves_its_sign_in_running(cagent_reprompts):
    # the agent ends a sign-in only once Claude Code says this sign-in signed its account in: on an
    # account signed out, a code that did not sign it in, its program asking again, ends nothing, past the watch's
    # first read and the settle after it
    got = _sign_in(cagent_reprompts)
    assert cagent_reprompts.http.post(f"/sessions/{got['id']}/sign-in-code",
                                      json={"code": "WRONG-code-1234"}).status_code == 200
    time.sleep(workspace.SIGN_IN_WATCH_EVERY + workspace.SIGN_IN_SETTLE + 2)
    assert cagent_reprompts.http.get(f"/sessions/{got['id']}").json()["running"] is True
    assert cagent_reprompts.http.get("/claude").json()["signed_in"] is False


def test_a_sign_in_started_again_after_its_code_is_not_ended_by_the_earlier_run_s_watch(cagent_waits):
    # the watch after a code is of the run the code was typed into. That run stopped once its code
    # signed the account in, and the sign-in started again, the new run waits for a code of its own: the earlier run's
    # watch, though the account says signed in, neither stops it nor ends its session
    got = _sign_in(cagent_waits)
    with cagent_waits.terminal(got["id"]) as ws:
        assert cagent_waits.http.post(f"/sessions/{got['id']}/sign-in-code", json={"code": GOOD}).status_code == 200
        read_until(ws, b"Login successful")             # the code taken: the account signed in, the program waiting
    assert cagent_waits.http.post(f"/sessions/{got['id']}/stop").status_code == 200
    _until(cagent_waits, got["id"], "running", lambda v: v is False)
    r = cagent_waits.http.post(f"/sessions/{got['id']}/start", json={})
    assert r.status_code == 200, r.text
    time.sleep(workspace.SIGN_IN_WATCH_EVERY + workspace.SIGN_IN_SETTLE + 2)
    assert cagent_waits.http.get(f"/sessions/{got['id']}").json()["running"] is True
    assert cagent_waits.http.get("/claude").json()["signed_in"] is True


def test_a_sign_in_of_another_account_is_left_running(cagent):
    # only the sign-ins of the account a new one starts on are stopped
    r = cagent.http.post("/accounts", json={"kind": "claude", "name": "Work"})
    assert r.status_code == 200, r.text
    r = cagent.http.post("/sessions", json={"program": "claude-sign-in", "task": "Sign in to Claude",
                                            "account": r.json()["id"]})
    assert r.status_code == 200, r.text
    other = _until(cagent, r.json()["id"], "sign_in_url")
    first = _sign_in(cagent)
    second = _sign_in(cagent)
    assert _until(cagent, first["id"], "running", lambda v: v is False)["running"] is False
    assert cagent.http.get(f"/sessions/{other['id']}").json()["running"] is True
    assert cagent.http.get(f"/sessions/{second['id']}").json()["running"] is True


def test_a_claude_sign_in_started_again_stops_the_one_running_on_its_account(cagent):
    # a sign-in started again (its Start again) is one sign-in of its account at a time too
    first = _sign_in(cagent)
    assert cagent.http.post(f"/sessions/{first['id']}/sign-in-code", json={"code": "WRONG-code-1234"}).status_code \
        == 200
    _until(cagent, first["id"], "running", lambda v: v is False)
    second = _sign_in(cagent)
    r = cagent.http.post(f"/sessions/{first['id']}/start", json={})
    assert r.status_code == 200, r.text
    assert _until(cagent, second["id"], "running", lambda v: v is False)["running"] is False
    assert cagent.http.get(f"/sessions/{first['id']}").json()["running"] is True
