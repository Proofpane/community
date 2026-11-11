"""A real workspace agent, started as the local provider starts it, and a repository to work in."""

import json
import os
import subprocess
import sys
import time

import httpx
from websockets.sync.client import connect

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
SECRET = "w" * 40
LEAK = "must-never-reach-a-session-" + "x" * 20
# a stand-in claude's answer to `--version`, as the real one gives it, before it does anything else: the workspace asks
# it before a claude terminal or chat starts, and starts none on a claude older than its floor or whose version it
# cannot read (workspace.claude_floor_refusal). The Python one needs `sys` imported above it.
CLAUDE_VERSION_SH = 'case "$1" in -v|--version) echo "2.1.274 (Claude Code)"; exit 0 ;; esac\n'
CLAUDE_VERSION_PY = ('if sys.argv[1:2] in (["-v"], ["--version"]):\n'
                     '    print("2.1.274 (Claude Code)")\n'
                     '    sys.exit(0)\n')


def git(*args, cwd):
    return subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def make_repo(path, origin=None):
    os.makedirs(path)
    git("init", "-q", "-b", "main", cwd=path)
    with open(os.path.join(path, "README.md"), "w") as fh:
        fh.write("demo\n")
    git("add", "-A", cwd=path)
    git("commit", "-q", "-m", "first", cwd=path)
    if origin:
        git("remote", "add", "origin", origin, cwd=path)
    return git("rev-parse", "HEAD", cwd=path)


_OWN = object()           # Agent.terminal's default: the agent's own secret


class Agent:
    """A workspace agent whose requests carry `secret` (its `http`, and `terminal` by default), the one it is started
    with in PP_WORKSPACE_SECRET, unless `extra_env` gives that another value: a caller that gives the agent its secret
    another way (emptying it, and naming a file in `args`) passes that secret as `secret`, or its requests are
    refused. Two agents given secrets of their own refuse each other's requests, as two people's workspaces do."""

    def __init__(self, root, home, extra_env=None, args=(), secret=SECRET, start_timeout=20, word=True):
        self.root = root
        self.secret = secret
        # PP_WORKSPACE_PARENT: this process, so the agent stops by itself if the test run is killed before it is closed
        env = {"PATH": os.environ["PATH"], "HOME": home, "SHELL": "/bin/sh", "LANG": "C",
               "PP_WORKSPACE_SECRET": secret, "OPEN_EDITION_TOKEN_SECRET": LEAK,
               "PP_WORKSPACE_PARENT": str(os.getpid())}
        env.update(extra_env or {})
        self.proc = subprocess.Popen([sys.executable, "-m", "proofpane_workspace", "--root", root, *args], cwd=ROOT,
                                     env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        info = os.path.join(root, "agent.json")
        deadline = time.time() + start_timeout
        while not os.path.exists(info):
            if self.proc.poll() is not None or time.time() > deadline:
                raise RuntimeError("the agent did not start: " + self._ended_saying())
            time.sleep(0.05)
        with open(info) as fh:
            self.port = json.load(fh)["port"]
        self.base = f"http://127.0.0.1:{self.port}"
        self.http = httpx.Client(base_url=self.base, headers={"Authorization": "Bearer " + secret}, timeout=30)
        for _ in range(200):
            try:
                if self.http.get("/health").status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.05)
        if word:
            # the app's word on the record of skill proposals, as the app's sender brings it within 25 seconds of a start
            # (skill_improve.py): without it every start the workspace makes unattended waits a minute, then is refused
            self.http.put("/skill-improve/record", json={"generation": 0})

    def _ended_saying(self):
        """What an agent that did not start wrote, once it is ended: one still starting is killed and waited for
        first, so the test is neither held reading its stderr until it ends by itself (it never does) nor leaves it
        running."""
        self.proc.kill()
        self.proc.wait()
        return self.proc.stderr.read().decode("utf-8", "replace")

    def terminal(self, session_id, secret=_OWN, which="terminal"):
        secret = self.secret if secret is _OWN else secret
        headers = {"Authorization": "Bearer " + secret} if secret else {}
        return connect(f"ws://127.0.0.1:{self.port}/sessions/{session_id}/{which}", additional_headers=headers,
                       open_timeout=10)

    def close(self):
        self.http.close()
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def read_until(ws, needle, timeout=15):
    """Everything the terminal sends until `needle` (bytes) appears; fails after `timeout` seconds."""
    got = b""
    deadline = time.time() + timeout
    while needle not in got:
        left = deadline - time.time()
        if left <= 0:
            raise AssertionError(f"{needle!r} did not appear; got {got[-400:]!r}")
        try:
            msg = ws.recv(timeout=left)
        except TimeoutError:
            continue
        got += msg if isinstance(msg, bytes) else msg.encode()
    return got


def next_control(ws, timeout=15):
    """The next text frame, skipping output."""
    deadline = time.time() + timeout
    while True:
        msg = ws.recv(timeout=max(0.1, deadline - time.time()))
        if isinstance(msg, str):
            return json.loads(msg)
