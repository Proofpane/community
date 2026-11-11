"""The desktop app's server (proofpane_open/desktop.py), started as the desktop shell starts it: it says where it
listens, with a sign-in for its one account, on the loopback address only; that account is an admin with no password,
and its workspace runs the agents the person's login shell finds; a second start keeps the account and the secret; and
it stops, with its workspace, when its standard input closes, or cleanly on SIGTERM or SIGINT."""

import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time

import httpx
import pytest

from conftest import ROOT
from proofpane_open import desktop


def _start(tmp_path, shell):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home"), "SHELL": str(shell), "LANG": "C.UTF-8"}
    (tmp_path / "home").mkdir(exist_ok=True)
    proc = subprocess.Popen([sys.executable, "-m", "proofpane_open.desktop", "--data", str(tmp_path / "data")],
                            cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True)
    line = proc.stdout.readline()
    assert line, proc.stderr.read()[-2000:]
    return proc, json.loads(line)


def _stop(proc, wait=30):
    proc.stdin.close()            # the shell ended: its pipe closes
    try:
        return proc.wait(wait)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise


def _fake_shell(tmp_path, path):
    """A login shell that talks before it answers, as a profile may, and reports PATH as `path`."""
    shell = tmp_path / "fake-shell"
    shell.write_text(f'#!/bin/sh\necho "Welcome back"\nPATH="{path}"\neval "$2"\necho "bye"\n')    # $1 is -ilc
    shell.chmod(0o755)
    return shell


@pytest.fixture
def agents_bin(tmp_path):
    folder = tmp_path / "agents-bin"
    folder.mkdir()
    for name in ("claude", "kimi"):
        (folder / name).write_text("#!/bin/sh\nexit 0\n")
        (folder / name).chmod(0o755)
    return folder


def test_it_starts_on_loopback_with_a_sign_in_for_its_one_account(tmp_path, agents_bin):
    proc, said = _start(tmp_path, _fake_shell(tmp_path, f"{agents_bin}:/usr/bin:/bin"))
    try:
        port = said["port"]
        assert said["url"].startswith(f"http://127.0.0.1:{port}/app/#signin=")
        token = said["url"].split("#signin=", 1)[1]
        base = f"http://127.0.0.1:{port}"
        # the server and the token, for the app's own page
        assert said["server"] == base and said["token"] == token
        me = httpx.get(f"{base}/api/auth/me", headers={"Authorization": f"Bearer {token}"}, timeout=10).json()
        assert me["email"] == desktop.DEFAULT_EMAIL and me["admin"] is True
        # with no password and no passkey, it could not sign in again: the page never offers to sign it out
        assert me["can_sign_in_again"] is False
        # which may call it from the app's origin, and no other page may
        def preflight(origin):
            return httpx.options(f"{base}/api/auth/me", timeout=10, headers={
                "Origin": origin, "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization"})
        allowed = preflight("tauri://localhost")
        assert allowed.status_code == 200 and allowed.headers["access-control-allow-origin"] == "tauri://localhost"
        assert "access-control-allow-credentials" not in allowed.headers
        for origin in ("https://evil.example", "http://localhost:3000", "null"):
            assert "access-control-allow-origin" not in preflight(origin).headers
        # the page itself where the UI has been built (web/app is built, not kept in the repository)
        if os.path.exists(os.path.join(ROOT, "web", "app", "index.html")):
            assert httpx.get(f"{base}/app/", timeout=10).status_code == 200
        # no password signs it in, not even an empty one
        for password in ("", "anything-at-all-123"):
            r = httpx.post(f"{base}/api/auth/login", json={"email": desktop.DEFAULT_EMAIL, "password": password},
                           timeout=10)
            assert r.status_code in (400, 401, 422) and "token" not in r.text
        # its workspace is the local provider's, started with it, and finds the agents where the login shell's PATH
        # has them
        auth = {"Authorization": f"Bearer {token}"}
        assert httpx.get(f"{base}/api/ade/workspace", headers=auth, timeout=10).json()["running"] is True
        programs = httpx.get(f"{base}/api/ade/workspace/programs", headers=auth, timeout=30).json()["programs"]
        # the stand-ins, found only through the login shell's PATH; every other agent as the rest of the server's
        # PATH has it (the usual install folders after the login shell's): on a machine with none, none
        assert programs["claude"] is True and programs["kimi"] is True
        home = str(tmp_path / "home")
        rest = ["/usr/bin", "/bin"] + [os.path.join(home, b[2:]) if b.startswith("~/") else b for b in desktop.USUAL_BINS]
        for name, found in programs.items():
            if name not in ("claude", "kimi"):
                assert found is (shutil.which(name, path=os.pathsep.join(rest)) is not None), name
    finally:
        assert _stop(proc) == 0
    data = tmp_path / "data"
    assert stat.S_IMODE(os.stat(data).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(data / desktop.SECRET_FILE).st_mode) == 0o600
    # its workspace stopped with it
    agent = json.loads(next((data / "workspaces").glob("*/agent.json")).read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(agent["pid"], 0)


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_a_signal_stops_it_cleanly_while_its_input_is_still_open(tmp_path, sig):
    # as at logout, or Ctrl-C in a terminal: the shell, its input's other end, is still there. The interpreter used to
    # abort as it exited (a thread waiting on sys.stdin held that stream's lock), and a Mac said that Python quit
    # unexpectedly.
    proc, _ = _start(tmp_path, _fake_shell(tmp_path, "/usr/bin:/bin"))
    try:
        proc.send_signal(sig)
        code = proc.wait(30)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdin.close()
    err = proc.stderr.read()
    assert code == 0 and "Fatal Python error" not in err, (code, err[-2000:])
    # its workspace stopped with it
    agent = json.loads(next((tmp_path / "data" / "workspaces").glob("*/agent.json")).read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(agent["pid"], 0)


def test_a_second_start_keeps_the_account_and_the_secret(tmp_path):
    shell = _fake_shell(tmp_path, "/usr/bin:/bin")
    proc, first = _start(tmp_path, shell)
    _stop(proc)
    proc, second = _start(tmp_path, shell)
    try:
        old = first["url"].split("#signin=", 1)[1]
        me = httpx.get(f"http://127.0.0.1:{second['port']}/api/auth/me", headers={"Authorization": f"Bearer {old}"},
                       timeout=10)
        assert me.status_code == 200 and me.json()["email"] == desktop.DEFAULT_EMAIL
    finally:
        _stop(proc)


def test_it_listens_on_the_loopback_address_only():
    sock = desktop._listen()
    try:
        host, port = sock.getsockname()
        assert host == "127.0.0.1"
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("192.0.2.1", 9))          # no packet is sent: this only picks the outward address
            outward = probe.getsockname()[0]
        except OSError:
            outward = None
        finally:
            probe.close()
        if outward and not outward.startswith("127."):
            with pytest.raises(OSError):
                socket.create_connection((outward, port), timeout=2).close()
    finally:
        sock.close()


def test_the_login_shell_s_path_comes_first_and_the_usual_folders_after_it(tmp_path):
    shell = _fake_shell(tmp_path, "/from/the/profile:/usr/bin")
    got = desktop.login_path({"SHELL": str(shell), "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}).split(os.pathsep)
    assert got[:2] == ["/from/the/profile", "/usr/bin"]
    assert "/usr/local/bin" in got and got.count("/usr/bin") == 1
    assert os.path.join(str(tmp_path), ".local", "bin") in got


def test_a_shell_that_cannot_say_leaves_the_path_it_was_given(tmp_path):
    slow = tmp_path / "slow-shell"
    slow.write_text("#!/bin/sh\nsleep 5\n")
    slow.chmod(0o755)
    for shell in (slow, tmp_path / "no-such-shell"):
        got = desktop.login_path({"SHELL": str(shell), "PATH": "/given/bin:/bin", "HOME": str(tmp_path)},
                                 timeout=0.5).split(os.pathsep)
        assert got[0] == "/given/bin"


def test_it_stops_when_its_standard_input_closes(tmp_path):
    proc, _ = _start(tmp_path, _fake_shell(tmp_path, "/usr/bin:/bin"))
    started = time.time()
    assert _stop(proc, wait=20) == 0
    assert time.time() - started < 20


def test_a_second_server_on_the_same_data_folder_refuses_to_start(tmp_path):
    shell = _fake_shell(tmp_path, "/usr/bin:/bin")
    proc, first = _start(tmp_path, shell)
    try:
        second = subprocess.run([sys.executable, "-m", "proofpane_open.desktop", "--data", str(tmp_path / "data")],
                                cwd=ROOT, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home"), "SHELL": str(shell)},
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        assert second.returncode == 3 and second.stdout == ""
        assert "Another Proofpane Community is running" in second.stderr
        # the first goes on as it was
        token = first["url"].split("#signin=", 1)[1]
        assert httpx.get(f"http://127.0.0.1:{first['port']}/api/auth/me", headers={"Authorization": f"Bearer {token}"},
                         timeout=10).status_code == 200
    finally:
        assert _stop(proc) == 0
    # and once it has stopped, the folder is free again
    proc, _ = _start(tmp_path, shell)
    _stop(proc)


def test_each_thing_that_needs_the_person_is_said_once_and_again_when_it_comes_back(monkeypatch):
    from proofpane_open.ade import push
    session = "abc123abc123"
    now = {"items": [("waiting:abc:1", push.words("workspace", key="waiting:abc:1", session=session))]}
    monkeypatch.setattr(push, "items_for", lambda user_id, alerts, at: list(now["items"]))
    said = []
    seen = desktop.notify_round("u", set(), said.append)
    # the words a Web Push notice says (push.words): the title, the body and the page, and nothing more
    assert said == [{"notify": {"title": "A session in your workspace waits for you", "body": "Open the ADE.",
                                "url": "/app/#session=" + session}}]
    seen = desktop.notify_round("u", seen, said.append)
    assert len(said) == 1                                   # still there: not said again
    now["items"] = []
    seen = desktop.notify_round("u", seen, said.append)
    now["items"] = [("waiting:abc:1", push.words("workspace", key="waiting:abc:1", session=session))]
    desktop.notify_round("u", seen, said.append)
    assert len(said) == 2                                   # gone and back: new again


def test_a_round_that_cannot_read_keeps_what_it_had_seen(monkeypatch):
    from proofpane_open.ade import push

    def broken(user_id, alerts, at):
        raise OSError("the workspace did not answer")

    monkeypatch.setattr(push, "items_for", broken)
    said = []
    assert desktop.notify_round("u", {"a"}, said.append) == {"a"} and said == []


def test_a_waiting_agent_is_said_in_fixed_words_and_never_its_command(tmp_path):
    """The line the server prints for an agent that waits, end to end: its session's activity says it waits for a
    shell command; the line says, in the words a Web Push notice says (ade/push.py words), that a session waits, and
    opens it: never the agent, the repository, the task, the tool or the command, which can carry a secret."""
    import shutil
    bin_dir = tmp_path / "agents-bin"
    bin_dir.mkdir()
    (bin_dir / "codex").write_text("#!/bin/sh\nsleep 600\n")
    (bin_dir / "codex").chmod(0o755)
    proc, said = _start(tmp_path, _fake_shell(tmp_path, f"{bin_dir}:/usr/bin:/bin"))
    try:
        token = said["url"].split("#signin=", 1)[1]
        base, auth = f"http://127.0.0.1:{said['port']}", {"Authorization": f"Bearer {token}"}
        from workspace_fixtures import make_repo
        root = next((tmp_path / "data" / "workspaces").glob("*"))
        make_repo(str(root / "repos" / "demo"))                 # a repository to start a session in
        s = httpx.post(f"{base}/api/ade/workspace/sessions", headers=auth,
                       json={"repo": "demo", "program": "codex", "task": "Fix it"}, timeout=30).json()
        (root / "sessions" / s["id"] / "activity.json").write_text(json.dumps(
            {"state": "waiting", "at": int(time.time()), "tool": "shell", "kind": "permission",
             "note": "curl -H 'Authorization: Bearer sk-secret-123' https://example.org"}))
        line = json.loads(proc.stdout.readline())
        assert line["notify"] == {"title": "A session in your workspace waits for you", "body": "Open the ADE.",
                                  "url": "/app/#session=" + s["id"]}
        assert not any(w in json.dumps(line) for w in ("sk-secret", "Codex", "demo", "Fix it", "shell"))
    finally:
        _stop(proc)
        shutil.rmtree(tmp_path / "data", ignore_errors=True)


def test_the_browser_is_opened_through_a_page_only_this_user_reads_and_never_with_the_sign_in_on_its_command_line(
        tmp_path, monkeypatch):
    import webbrowser
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda address: opened.append(address))
    url = "http://127.0.0.1:5555/app/#signin=a.b"
    path = desktop.open_in_browser(str(tmp_path), url, remove_after=0.3)
    assert opened == ["file://" + path] and "signin" not in opened[0]
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600 and json.dumps(url) in open(path).read()
    time.sleep(0.8)
    assert not os.path.exists(path)                    # removed once the browser has had it

