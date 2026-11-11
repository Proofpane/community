"""Where a user's workspace runs: the provider named by `OPEN_EDITION_ADE`.

Off by default. With the variable unset the hosted workspace does not exist: its routes answer that it is off and its
terminal closes. `local` starts each workspace as a child process of this app, on this machine, under this machine's
user, so it gives the account a shell here: it is for a single-user install and for the tests, and the app refuses
to start with it on the hosted site (where Fly sets FLY_APP_NAME) or to give it to an account that is not an admin.
`fly` runs one Fly Machine and volume per user (see `fly_provider.py`).

A workspace is found from the signed-in account only, never from anything the browser sends. Each one has its own
secret, made here when it starts; the app sends it to the workspace agent with every call, and the agent answers
nothing without it.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional

from .. import config

PROVIDERS = ("local", "fly")
OPEN_EDITION_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
AGENT_PASS_ENV = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "TZ",
                  "TMPDIR", "SSH_AUTH_SOCK")   # the person's ssh agent, for git over ssh, on their own machine
START_TIMEOUT = 20
# the file the local provider gives its agent its secret in, in the workspace's root: named by random bytes
SECRET_FILE_BYTES = 8
SECRET_FILE = re.compile(rf"secret-[0-9a-f]{{{2 * SECRET_FILE_BYTES}}}")


class WorkspaceAccess(Exception):
    """The hosted workspace is off, or not for this account: (HTTP status, what to show)."""

    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status


@dataclass(frozen=True)
class Endpoint:
    http: str                  # the agent's base address, http:// or https://
    ws: str                    # the same, ws:// or wss://
    secret: str
    ssl: Optional[object] = None   # an ssl.SSLContext that trusts only this workspace's certificate


def provider_name():
    name = os.environ.get("OPEN_EDITION_ADE", "").strip()
    if not name:
        return None
    if name not in PROVIDERS:
        raise RuntimeError(f"OPEN_EDITION_ADE names a provider: local or fly, not {name!r}")
    return name


def check_config():
    """Refuse to start with a provider that must not run here. Called when the app starts."""
    name = provider_name()
    if name == "local" and os.environ.get("FLY_APP_NAME"):
        raise RuntimeError("OPEN_EDITION_ADE=local gives an admin a shell on this machine; it never runs on the "
                           "hosted site")
    if name == "fly":
        from . import fly_provider
        fly_provider.check_config()


_providers = {}
_providers_lock = threading.Lock()


def provider():
    """The configured provider, or None when the hosted workspace is off."""
    name = provider_name()
    if name is None:
        return None
    with _providers_lock:
        if name not in _providers:
            if name == "local":
                _providers[name] = LocalProvider()
            else:
                from . import fly_provider
                _providers[name] = fly_provider.FlyProvider()
        return _providers[name]


def for_user(user):
    """The provider, when this account may have a workspace; otherwise WorkspaceAccess."""
    p = provider()
    if p is None:
        raise WorkspaceAccess(404, "The hosted workspace is off on this deployment.")
    p.check_user(user)
    return p


def start():
    """What the provider runs beside the app while it serves: the fly provider's spending meter. Called from the
    app's lifespan, so tests, which never enter it, start none."""
    if provider_name() == "fly":
        from . import fly_provider
        fly_provider.start_meter()


def shutdown():
    with _providers_lock:
        found = list(_providers.values())
        _providers.clear()
    for p in found:
        p.shutdown()


class _Child:
    def __init__(self, proc, endpoint):
        self.proc = proc
        self.endpoint = endpoint


def local_records_key(user_id):
    """The key a person's local workspace agent signs its runs' records with (the workspace's agents.py): the same at
    each of its starts, where its secret is new each time, and made from the app's own signing secret, so it is kept
    nowhere. It reaches the agent in its secret's file, which the agent reads and deletes."""
    return hmac.new(config.token_secret(), b"proofpane local workspace records\0" + user_id.encode("utf-8"),
                    hashlib.sha256).digest()


class LocalProvider:
    name = "local"

    def __init__(self):
        self._children = {}
        self._lock = threading.Lock()

    def check_user(self, user):
        if os.environ.get("FLY_APP_NAME"):
            raise WorkspaceAccess(403, "The local workspace never runs on the hosted site.")
        if not user.get("admin"):
            raise WorkspaceAccess(403, "On this deployment a workspace is a shell on the app's own machine, so only "
                                       "an admin has one.")

    def root(self, user_id):
        return os.path.join(config.data_dir(), "workspaces", user_id)

    def _live(self, user_id):
        child = self._children.get(user_id)
        return child if child is not None and child.proc.poll() is None else None

    def status(self, user):
        # made: started before, so its folder is there (ensure makes it), with whatever its sessions left in it
        return {"provider": self.name, "running": self._live(user["id"]) is not None,
                "made": os.path.isdir(self.root(user["id"]))}

    def endpoint(self, user):
        child = self._live(user["id"])
        return child.endpoint if child else None

    def touch(self, user):
        pass

    def ensure(self, user):
        from . import storage
        storage.may_start(user)         # none for an account whose work lives elsewhere, as it chose (storage.py)
        with self._lock:
            child = self._live(user["id"])
            if child is not None:
                return child.endpoint
            root = self.root(user["id"])
            os.makedirs(root, mode=0o700, exist_ok=True)
            info = os.path.join(root, "agent.json")
            if os.path.exists(info):
                os.unlink(info)
            # a secret file no agent read (the app and its agent both ended before the agent could): it holds the key
            # the runs' records are signed with, the same at every start, so it goes before a new one is written
            for name in os.listdir(root):
                if SECRET_FILE.fullmatch(name):
                    try:
                        os.unlink(os.path.join(root, name))
                    except FileNotFoundError:
                        pass
            secret = secrets.token_urlsafe(32)
            env = {k: os.environ[k] for k in AGENT_PASS_ENV if k in os.environ}
            # this app's own process: the agent stops by itself once the app is gone without stopping it (killed
            # outright, say), rather than run on and be joined on the same root by the one the app's next start starts
            env["PP_WORKSPACE_PARENT"] = str(os.getpid())
            settings = local_gate_settings()
            if settings:
                env["PP_WORKSPACE_CLAUDE_SETTINGS"] = settings
            gemini = local_gate_settings(gemini=True)
            if gemini:
                env["PP_WORKSPACE_GEMINI_SETTINGS"] = gemini
            qwen = local_gate_settings(qwen=True)
            if qwen:
                env["PP_WORKSPACE_QWEN_SETTINGS"] = qwen
            # the secret goes in a file the agent reads and deletes as it starts, not in its environment: the programs
            # its sessions run, as this user, could read the environment it was started with (ps -E on a Mac, or
            # /proc/PID/environ on Linux), even after the agent removes it from its own
            secret_file = os.path.join(root, f"secret-{secrets.token_hex(SECRET_FILE_BYTES)}")
            fd = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(secret + "\nrecords-key=" + local_records_key(user["id"]).hex())
            try:
                log = open(os.path.join(root, "agent.log"), "ab")
                try:
                    # --link-repos: on this machine, a repository the person already has may be added where it is
                    proc = subprocess.Popen([sys.executable, "-m", "proofpane_workspace", "--root", root,
                                             "--secret-file", secret_file, "--link-repos"], cwd=OPEN_EDITION_ROOT,
                                            env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                            start_new_session=True)
                finally:
                    log.close()
                port = _wait_for_port(proc, info)
            finally:
                try:
                    os.unlink(secret_file)          # the agent has; this is for one that did not start
                except FileNotFoundError:
                    pass
            endpoint = Endpoint(f"http://127.0.0.1:{port}", f"ws://127.0.0.1:{port}", secret)
            self._children[user["id"]] = _Child(proc, endpoint)
            return endpoint

    def month_hours(self, user, now=None):
        """None: a machine of the person's own meters nothing (triggers.py holds a user's schedules to a share of the
        fly provider's cap)."""
        return None

    def stop(self, user):
        with self._lock:
            child = self._children.pop(user["id"], None)
        if child is not None:
            _end(child.proc)
        return {"provider": self.name, "running": False}

    def shutdown(self):
        with self._lock:
            children = list(self._children.values())
            self._children.clear()
        for child in children:
            _end(child.proc)


def _wait_for_port(proc, info):
    deadline = time.time() + START_TIMEOUT
    while time.time() < deadline:
        if proc.poll() is not None:
            raise WorkspaceAccess(502, "The workspace did not start; its log is agent.log in its directory.")
        try:
            with open(info, encoding="utf-8") as fh:
                found = json.load(fh)
            if found.get("pid") == proc.pid and isinstance(found.get("port"), int):
                return found["port"]
        except (FileNotFoundError, ValueError):
            pass
        time.sleep(0.05)
    _end(proc)
    raise WorkspaceAccess(502, "The workspace did not start in time.")


def _end(proc):
    """Stop the agent, which hangs up its sessions' programs as it goes."""
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(5)


WORKSPACE_PACKAGE = os.path.join(OPEN_EDITION_ROOT, "proofpane_workspace")


def local_gate_settings(gemini=False, qwen=False):
    """The settings file the local provider starts every `claude` session with (`--settings`): the first gates, one
    PreToolUse hook on every tool that runs `proofpane_workspace/gate.py` with this app's Python, and the status line
    that records the quota; with `gemini`, Gemini CLI's system settings, the same gate and activity hooks, which the
    agent names to each `gemini` session; with `qwen`, Qwen Code's, named to each `qwen` session the same way. It is written by `python -m proofpane_workspace.settings`, the same code
    that makes the image's managed settings, run as a process (the app never imports the agent's package), again each
    time a workspace starts, for this user only, so it names the scripts where this install has them. What the gate
    refuses, and what it cannot, is in gate.py."""
    name = "workspace-gemini-settings.json" if gemini else "workspace-qwen-settings.json" if qwen else \
        "workspace-gate-settings.json"
    path = os.path.join(config.data_dir(), name)
    env = {k: os.environ[k] for k in AGENT_PASS_ENV if k in os.environ}
    try:
        done = subprocess.run([sys.executable, "-m", "proofpane_workspace.settings", "--python", sys.executable,
                               "--dir", WORKSPACE_PACKAGE, "--out", path,
                               *(["--gemini"] if gemini else ["--qwen"] if qwen else [])],
                              cwd=OPEN_EDITION_ROOT, env=env,
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        done = None
    if done is None or done.returncode != 0:
        raise WorkspaceAccess(502, "The workspace's settings could not be written.")
    return path
