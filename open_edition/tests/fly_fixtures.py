"""A stand-in for Fly's Machines API, as its reference describes it (docs.fly.io/machines/api, read 2026-09-28): one
app's volumes and machines, their states, `wait`, and their destruction (a started machine only with force=true, a
volume only once no machine holds it). It checks the token on every call and keeps a log of them.

With a boot hook (LocalMachines), starting a machine starts what the workspace image starts, on this computer, from
the files in the machine's configuration: workspace_image/Caddyfile in Caddy, and the workspace agent reading its
secret from the file Fly would write. So the provider's certificates, client certificate, secret and TLS settings
meet the real TLS front and the real agent. It needs a Caddy binary: PP_TEST_CADDY, or `caddy` on the PATH.
"""

import base64
import copy
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
_PATH = re.compile(r"^/v1/apps/([^/]+)/(volumes|machines)(?:/([^/]+))?(?:/(start|stop|wait))?$")


class FakeFly:
    def __init__(self, app, token, boot=None, halt=None):
        self.app = app
        self.expected = token if token.startswith("FlyV1 ") else "Bearer " + token
        self.volumes, self.machines = {}, {}
        self.log = []
        self.answers = []          # (status, body) to give instead of the next calls' own answers
        self.boot, self.halt = boot, halt
        # after an update, a stopped machine reads "created" this many times while its new version is made, and a
        # start then is refused, as a busy machine's is
        self.settle_reads = 2
        self.settling = {}
        self.refuse_starts = 0     # starts refused (412) even once a machine reads as stopped: Fly's own race
        self.refuse_deletes = set()    # ids of machines or volumes whose destruction is refused (500), every time
        self.other_apps = set()        # other apps of the token's, holding nothing: their lists are empty
        self.lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _answer(self, status, body):
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                if status == 429:
                    self.send_header("Retry-After", "0")
                self.end_headers()
                self.wfile.write(raw)

            def _handle(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                parts = urlsplit(self.path)
                with fake.lock:
                    fake.log.append((method, parts.path, body, dict(self.headers)))
                    if self.headers.get("Authorization") != fake.expected:
                        return self._answer(401, {"error": "unauthorized"})
                    if fake.answers:
                        return self._answer(*fake.answers.pop(0))
                    status, out = fake.route(method, parts.path, parse_qs(parts.query), body)
                self._answer(status, out)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def do_DELETE(self):
                self._handle("DELETE")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def calls(self, method=None, path_end=None):
        return [(m, p, b) for m, p, b, _ in self.log
                if (method is None or m == method) and (path_end is None or p.endswith(path_end))]

    # the API itself

    def route(self, method, path, query, body):
        m = _PATH.match(path)
        if m and m.group(1) in self.other_apps:
            listing = method == "GET" and m.group(3) is None
            return (200, []) if listing else (404, {"error": "not found"})
        if m and method == "DELETE" and m.group(3) in self.refuse_deletes:
            return 500, {"error": "could not destroy it"}
        if not m or m.group(1) != self.app:
            return 404, {"error": "not found"}
        kind, ident, verb = m.group(2), m.group(3), m.group(4)
        if kind == "volumes":
            if method == "POST" and ident is None:
                if not body or not body.get("name") or not body.get("region"):
                    return 400, {"error": "name and region are required"}
                vol = {"id": "vol_" + secrets.token_hex(8), "name": body["name"], "region": body["region"],
                       "size_gb": body.get("size_gb", 3), "encrypted": body.get("encrypted", True),
                       "state": "created", "attached_machine_id": None}
                self.volumes[vol["id"]] = vol
                return 200, copy.deepcopy(vol)
            if method == "GET" and ident is None:
                return 200, copy.deepcopy(list(self.volumes.values()))
            if method == "GET" and ident in self.volumes:
                return 200, copy.deepcopy(self.volumes[ident])
            if method == "DELETE" and ident in self.volumes and self.volumes[ident]["state"] != "destroyed":
                vol = self.volumes[ident]
                holder = self.machines.get(vol["attached_machine_id"] or "")
                if holder is not None and holder["state"] != "destroyed":
                    return 412, {"error": "volume is attached to a machine"}
                vol["state"] = "destroyed"
                return 200, copy.deepcopy(vol)
            return 404, {"error": "volume not found"}
        machine = self.machines.get(ident) if ident else None
        if method == "POST" and ident is None:
            return self._create(body or {})
        if method == "GET" and ident is None:
            return 200, copy.deepcopy(list(self.machines.values()))
        if machine is None or machine["state"] == "destroyed":
            return 404, {"error": "machine not found"}
        if method == "GET" and verb is None:
            if self.settling.get(ident):
                self.settling[ident] -= 1
                return 200, dict(copy.deepcopy(machine), state="created")
            return 200, copy.deepcopy(machine)
        if method == "POST" and verb is None:
            return self._update(machine, body or {})
        if method == "DELETE" and verb is None:
            # as Fly does: a started machine is destroyed only with force=true
            if machine["state"] == "started" and (query.get("force") or [""])[0] != "true":
                return 412, {"error": "machine is started: stop it first, or destroy it with force=true"}
            self._stop(machine)
            machine["state"] = "destroyed"
            for vol in self.volumes.values():
                if vol["attached_machine_id"] == ident:
                    vol["attached_machine_id"] = None
            return 200, {"ok": True}
        if method == "POST" and verb == "start":
            if self.settling.get(ident):
                return 412, {"error": "machine is still being updated"}
            if self.refuse_starts:
                self.refuse_starts -= 1
                return 412, {"error": "machine is not ready to start"}
            previous = machine["state"]
            if previous != "started":
                self._start(machine)
            return 200, {"previous_state": previous}
        if method == "POST" and verb == "stop":
            self._stop(machine)
            return 200, {"ok": True}
        if method == "GET" and verb == "wait":
            want = (query.get("state") or ["started"])[0]
            return (200, {"ok": True}) if machine["state"] == want else (408, {"error": "deadline exceeded"})
        return 404, {"error": "not found"}

    def _check_config(self, cfg, machine_id=None):
        if not isinstance(cfg, dict) or not cfg.get("image"):
            return "config.image is required"
        for mount in cfg.get("mounts") or []:
            vol = self.volumes.get(mount.get("volume"))
            if vol is None:
                return "volume not found"
            if vol["attached_machine_id"] not in (None, machine_id):
                return "volume is attached to another machine"
        return None

    def _create(self, body):
        cfg = body.get("config")
        problem = self._check_config(cfg)
        if problem:
            return 400, {"error": problem}
        if any(m["name"] == body.get("name") and m["state"] != "destroyed" for m in self.machines.values()):
            return 409, {"error": "a machine with this name exists"}
        machine = {"id": secrets.token_hex(7), "name": body.get("name"), "region": body.get("region"),
                   "state": "created", "instance_id": secrets.token_hex(8), "private_ip": "fdaa::" +
                   secrets.token_hex(2), "config": copy.deepcopy(cfg)}
        self.machines[machine["id"]] = machine
        for mount in cfg.get("mounts") or []:
            self.volumes[mount["volume"]]["attached_machine_id"] = machine["id"]
        if not body.get("skip_launch"):
            self._start(machine)
        return 200, copy.deepcopy(machine)

    def _update(self, machine, body):
        cfg = body.get("config")
        problem = self._check_config(cfg, machine["id"])
        if problem:
            return 400, {"error": problem}
        running = machine["state"] == "started"
        if running:
            self._stop(machine)
        machine["config"] = copy.deepcopy(cfg)
        machine["instance_id"] = secrets.token_hex(8)
        # as Fly does (seen on a workspace app's machine): a running machine restarts with its new
        # configuration; a stopped one takes it and stays stopped, "created" while its new version is made
        if running and not body.get("skip_launch"):
            self._start(machine)
        elif not running:
            machine["state"] = "stopped"
            self.settling[machine["id"]] = self.settle_reads
            return 200, dict(copy.deepcopy(machine), state="created")
        return 200, copy.deepcopy(machine)

    def _start(self, machine):
        machine["state"] = "started"
        if self.boot:
            self.boot(machine)

    def _stop(self, machine):
        if machine["state"] == "started" and self.halt:
            self.halt(machine)
        machine["state"] = "stopped"

    def destroy(self, machine_id):
        with self.lock:
            machine = self.machines[machine_id]
            self._stop(machine)
            machine["state"] = "destroyed"
            for vol in self.volumes.values():
                if vol["attached_machine_id"] == machine_id:
                    vol["attached_machine_id"] = None


def caddy_binary():
    found = os.environ.get("PP_TEST_CADDY") or shutil.which("caddy")
    return found if found and os.access(found, os.X_OK) else None


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(port, proc, what, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"{what} stopped: {proc.stderr.read().decode()[-800:] if proc.stderr else ''}")
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"{what} did not listen on {port}")


class LocalMachines:
    """Boots a machine as the workspace image would, on this computer: the files from its configuration into a boot
    directory, the agent with --secret-file and --idle-exit as start.sh gives them, and Caddy with the image's own
    Caddyfile. A volume is a directory that outlives its machine. `address` is what the provider should use."""

    def __init__(self, base):
        self.base = base
        self.running = {}          # machine id -> (caddy, agent)
        self.ports = {}            # machine id -> Caddy's port
        self.caddy = caddy_binary()

    def address(self, machine_id, app):
        return "localhost", self.ports[machine_id]

    def boot(self, machine):
        cfg = machine["config"]
        boot = os.path.join(self.base, "boot", machine["id"])
        os.makedirs(boot, exist_ok=True)
        for f in cfg["files"]:
            with open(os.path.join(boot, os.path.basename(f["guest_path"])), "wb") as fh:
                fh.write(base64.b64decode(f["raw_value"]))
        (volume,) = [m["volume"] for m in cfg["mounts"]]
        home = os.path.join(self.base, "volumes", volume)
        os.makedirs(home, exist_ok=True)
        agent_port = free_port()
        port = self.ports.setdefault(machine["id"], free_port())
        agent = subprocess.Popen(
            [sys.executable, "-m", "proofpane_workspace", "--root", os.path.join(home, "workspace"),
             "--port", str(agent_port), "--secret-file", os.path.join(boot, "secret"),
             "--idle-exit", cfg["env"]["PP_WORKSPACE_IDLE_SECONDS"]],
            cwd=ROOT, env={"PATH": os.environ["PATH"], "HOME": home, "SHELL": "/bin/sh", "LANG": "C"},
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        caddy = subprocess.Popen(
            [self.caddy, "run", "--config", os.path.join(ROOT, "workspace_image", "Caddyfile"), "--adapter",
             "caddyfile"],
            env={"PATH": os.environ["PATH"], "HOME": boot, "XDG_DATA_HOME": boot, "XDG_CONFIG_HOME": boot,
                 "PP_TLS_DIR": boot, "PP_LISTEN_PORT": str(port), "PP_AGENT_PORT": str(agent_port)},
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.running[machine["id"]] = (caddy, agent)
        _wait_port(agent_port, agent, "the agent")
        _wait_port(port, caddy, "Caddy")

    def halt(self, machine):
        for proc in self.running.pop(machine["id"], ()):
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()

    def close(self):
        for mid in list(self.running):
            self.halt({"id": mid})
