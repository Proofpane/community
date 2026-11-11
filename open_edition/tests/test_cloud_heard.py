"""A linked cloud session at work is not shown as silent. A cloud session
runs no hook of Community's, so its link was heard only when the session called the client, and a session that worked
for an hour without calling it read as silent, the warning meant for a session that has stopped. The client keeps such
a link heard by itself: a background heartbeat at the link's interval (ade_link.py keep), started for a cloud link where
none watches its session, ending when its session's process ends, so a session that stopped still reads as silent. The
page says how each link is heard: through its hook, or through its client alone. And a link whose recorded proxy
refuses after its container restarts takes again the one its session started with, never one its own run's
environment names (which a repository's settings could set), and says so where it cannot."""

import io
import json
import os
import re
import socket
import subprocess
import sys
import time
import types

import pytest

import ade_link
from conftest import signed_up  # noqa: F401
from proofpane_open import store
from test_ade_link import Session, at_once, paired, row
from test_ade_link import owner  # noqa: F401  the fixture
from test_ade_link_client import StandIn, link_row, pair, run, served  # noqa: F401  served is the fixture
from test_ade_link_page import CARD, NODE, read

MINUTE = 60
INTERVAL = 30 * MINUTE                   # a cloud link's interval (link.LINK_KINDS)
SESSION = (4242, 987654)                 # the session's process, as /proc gives it: its pid and its start time
AGAIN = (4343, 999999)                   # the session's process after its container restarted


REAL_SPAWN = getattr(ade_link, "_spawn", None)            # the real start, before a fixture replaces it
REAL_LOOKUP = ade_link._session_process                    # the client's own, before a fixture replaces it
REAL_WATCHED = getattr(ade_link, "_watched_session", None)


class Bound(BaseException):
    """A keeper still running six hours on: never one of the client's own errors, so nothing it catches hides it."""


class World:
    """The keeper's world in this process: one clock for the session's client and the ADE alike (time.time, which
    signs a request and checks it, and the client's clock and sleep), the session's process alive until `end_at`, each
    background start the client asks for recorded instead of made, and a callback run once the clock passes its time."""

    def __init__(self, monkeypatch):
        self.t = float(int(time.time()))
        self.session = SESSION
        self.end_at = float("inf")
        self.started = []
        self.at = []
        self.beats = []
        self.limit = self.t + 6 * 3600
        real_alive = getattr(ade_link, "_process_alive", None)

        def alive(pid, start):
            if (pid, start) in (SESSION, AGAIN):
                return (pid, start) == self.session and self.t < self.end_at
            return real_alive(pid, start)

        def spawn(argv):
            self.started.append([str(a) for a in argv])
            return types.SimpleNamespace(pid=999_000 + len(self.started))

        real_request = ade_link.Link.request

        def request(link, method, path, *args, **kwargs):
            if path == "/heartbeat":
                self.beats.append(self.t)
            return real_request(link, method, path, *args, **kwargs)

        monkeypatch.setattr(time, "time", lambda: self.t)
        monkeypatch.setattr(ade_link, "clock", lambda: self.t)
        monkeypatch.setattr(ade_link, "sleep", self.sleep)
        monkeypatch.setattr(ade_link, "_session_process", lambda *a: self.session, raising=False)
        monkeypatch.setattr(ade_link, "_watched_session", lambda: self.session, raising=False)
        monkeypatch.setattr(ade_link, "_process_alive", alive, raising=False)
        monkeypatch.setattr(ade_link, "_spawn", spawn, raising=False)
        monkeypatch.setattr(ade_link.Link, "request", request)

    def sleep(self, seconds):
        self.t += max(float(seconds), 0.001)
        for at, call in sorted(self.at, key=lambda x: x[0]):
            if self.t >= at:
                self.at.remove((at, call))
                call()
        if self.t > self.limit:
            raise Bound(f"a keeper still ran at {self.t - self.limit + 6 * 3600:.0f} s")

    def keep(self):
        """Run each keeper the client started, in this process, until it returns; their exit codes."""
        codes = []
        while self.started:
            codes.append(ade_link.main(self.started.pop(0)))
        return codes


@pytest.fixture(autouse=True)
def no_session_but_the_world_s(monkeypatch):
    # a keeper is never started here for real (the World records each start), and the client reads no process of the
    # session that runs the tests: each test that needs one names its own
    monkeypatch.delenv("ADE_LINK_KEEP", raising=False)
    monkeypatch.setattr(ade_link, "_session_process", lambda *a: None, raising=False)
    monkeypatch.setattr(ade_link, "_watched_session", lambda: None, raising=False)
    monkeypatch.setattr(ade_link, "_spawn", lambda argv: pytest.fail(f"a keeper was started for real: {argv}"),
                        raising=False)


def view(client, owner_headers, name):
    got = client.get("/api/ade/link/sessions", headers=owner_headers)
    assert got.status_code == 200, got.text
    return next(x for x in got.json()["links"] if x["name"] == name)


# ---- a cloud link at work, one whose session ended, and a local link --------------------------------------------

def test_a_cloud_link_whose_session_calls_nothing_for_90_minutes_reads_as_heard(client, owner, served, capsys,
                                                                                 tmp_path, monkeypatch):
    world = World(monkeypatch)
    t0 = world.t
    pair(client, owner, served, capsys, tmp_path / "cloud", "integrator", kind="cloud")
    t1 = world.t                                             # the keeper starts as the pairing ends
    seen = []
    world.at.append((t0 + 90 * MINUTE, lambda: seen.append(view(client, owner, "integrator"))))
    world.end_at = t0 + 90 * MINUTE + 1
    world.keep()
    world.t = max(world.t, t0 + 90 * MINUTE)
    if not seen:
        seen.append(view(client, owner, "integrator"))
    at90 = seen[0]
    assert at90["silent"] is False
    assert at90["last_heard_at"] is not None and at90["last_heard_at"] >= t0 + 60 * MINUTE
    # at the link's interval: from the keeper's start, every 30 minutes, and no more often
    assert world.beats[:4] == [t1, t1 + INTERVAL, t1 + 2 * INTERVAL, t1 + 3 * INTERVAL]
    # its beats are its presence, not the heartbeat of a watcher: the app's word on a queued command reads that alone
    assert link_row("integrator")["last_heartbeat_at"] is None


def test_the_same_link_reads_as_silent_within_two_intervals_of_its_session_ending(client, owner, served, capsys,
                                                                                   tmp_path, monkeypatch):
    # the control: a session that stopped still reads as silent, its keeper ended with it
    world = World(monkeypatch)
    t0 = world.t
    pair(client, owner, served, capsys, tmp_path / "cloud", "integrator", kind="cloud")
    end = t0 + 90 * MINUTE + 1
    world.end_at = end
    codes = world.keep()                                 # returns: a keeper that outlived its session meets Bound
    assert all(code == 0 for code in codes)
    assert all(beat < end for beat in world.beats)
    world.t = max(world.t, end) + 2 * INTERVAL + 1
    assert view(client, owner, "integrator")["silent"] is True


def test_a_local_link_with_its_hook_in_is_unchanged(client, owner, served, capsys, tmp_path, monkeypatch):
    # the control: no keeper for a local link; heard each time its hook runs, silent two of its intervals after
    world = World(monkeypatch)
    t0 = world.t
    d = pair(client, owner, served, capsys, tmp_path / "mac", "mac", kind="local")
    world.t = t0 + 10 * MINUTE
    assert hook(capsys, d) == "allow"
    assert world.started == []
    world.t = t0 + 10 * MINUTE + 29 * MINUTE
    assert view(client, owner, "mac")["silent"] is False
    world.t = t0 + 10 * MINUTE + 31 * MINUTE
    assert view(client, owner, "mac")["silent"] is True
    assert world.started == []


# ---- how a link is heard: the hook's requests marked, the owner's view, the pages ---------------------------------

def hook(capsys, d, tool="Read"):
    """The PreToolUse hook as Claude Code runs it, in this process: its decision."""
    stdin = sys.stdin
    sys.stdin = io.StringIO(json.dumps({"tool_name": tool, "tool_input": {"file_path": "/x"}}))
    try:
        code, out, _ = run(capsys, "--dir", d, "pretool")
    finally:
        sys.stdin = stdin
    assert code == 0
    return json.loads(out)["hookSpecificOutput"]["permissionDecision"] if out.strip() else "allow"


def test_a_local_link_s_hook_is_heard_as_its_hook_and_its_session_s_own_calls_are_not(client, owner, served, capsys,
                                                                                    tmp_path, monkeypatch):
    world = World(monkeypatch)
    t0 = world.t
    d = pair(client, owner, served, capsys, tmp_path / "mac", "mac", kind="local")
    world.t = t0 + 2 * MINUTE
    assert run(capsys, "--dir", d, "post", "step", "one step")[0] == 0           # the session's own call
    assert view(client, owner, "mac").get("heard_through") is None              # no hook heard yet: nothing said
    world.t = t0 + 4 * MINUTE
    assert hook(capsys, d) == "allow"
    assert view(client, owner, "mac").get("heard_through") == "hook"


def test_a_keeper_s_beat_carries_no_report(client, owner, monkeypatch):
    """A heartbeat may carry its session's report, but a keeper's beat (via=keeper) says only that the session's
    process runs, and a keeper never reads its session: one carrying a report is refused and moves nothing; one without
    is taken."""
    from proofpane_open.ade import link as server_link
    t0 = int(time.time())
    monkeypatch.setattr(server_link.time, "time", lambda: t0 + 60)
    cloud = paired(client, owner, "cloud", "cloud")
    refused = cloud.call("POST", "/heartbeat", {"reported": {}}, query={"via": "keeper"}, ts=t0 + 60)
    assert refused.status_code == 422, refused.text
    assert row(cloud.link_id)["keeper_heard_at"] is None
    taken = cloud.call("POST", "/heartbeat", {}, query={"via": "keeper"}, ts=t0 + 60)
    assert taken.status_code == 200 and taken.json() == {"interval_min": 30, "kind": "cloud"}
    assert row(cloud.link_id)["keeper_heard_at"] == t0 + 60


def test_the_owner_s_view_says_how_each_link_is_heard(client, owner, monkeypatch):
    from proofpane_open.ade import link as server_link
    t0 = int(time.time())
    clock = [t0]
    monkeypatch.setattr(server_link.time, "time", lambda: clock[0])
    mac = paired(client, owner)
    cloud = paired(client, owner, "cloud", "cloud")
    old = paired(client, owner, "old-mac")
    clock[0] = t0 + 60
    assert mac.call("GET", "/feed", query={"via": "hook"}, ts=t0 + 60).status_code == 200
    beat = cloud.call("POST", "/heartbeat", {}, query={"via": "keeper"}, ts=t0 + 60)
    assert beat.status_code == 200
    assert beat.json() == {"interval_min": 30, "kind": "cloud"}                 # what a keeper beats by
    assert old.call("GET", "/feed", ts=t0 + 60).status_code == 200              # an older client marks nothing
    views = {v["name"]: v for v in client.get("/api/ade/link/sessions", headers=owner).json()["links"]}
    assert views["mac"].get("heard_through") == "hook"
    assert views["cloud"].get("heard_through") == "keeper"
    assert views["old-mac"].get("heard_through") is None
    assert row(cloud.link_id)["last_heartbeat_at"] is None                      # a keeper's beat is not a watcher's
    # two intervals past its keeper's last beat, heard since through its session's own calls alone
    clock[0] = t0 + 60 + 2 * INTERVAL + 60
    assert cloud.call("GET", "/feed", ts=clock[0]).status_code == 200
    views = {v["name"]: v for v in client.get("/api/ade/link/sessions", headers=owner).json()["links"]}
    assert views["cloud"].get("heard_through") == "client"
    assert views["cloud"]["silent"] is False


HEARD = {"hook": "through its hook",
         "keeper": "through its client alone, which keeps it heard while its session runs",
         "client": "through its client alone, when its session calls it"}


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's functions")
def test_the_old_page_s_card_says_how_a_link_is_heard(tmp_path):
    script = read("web", "app.js")
    grab = lambda name: re.search(rf"function {name}\(.*?\n\}}\n", script, re.S).group(0)  # noqa: E731
    one = lambda name: re.search(rf"function {name}\([^\n]*\}}\n", script).group(0)  # noqa: E731
    probe = tmp_path / "card.js"
    probe.write_text("".join(grab(f) for f in ("el", "buttonFor", "claudeLink", "linkLine", "linkCard"))
                     + "".join(one(f) for f in ("utc", "unix", "shortLine"))
                     + re.search(r"const HIDDEN_ALL = [^\n]*\n", script).group(0) + grab("visible") + CARD)
    base = {"id": "L1", "name": "integrator", "kind": "cloud", "silent": False, "checkpoint": {"doing": "the fix"},
            "events": 0, "last_event": None, "last_heartbeat_at": None, "last_heard_at": 1790625600,
            "interval_min": 30}
    head = lambda link: json.loads(subprocess.run([NODE, str(probe), json.dumps(link)], capture_output=True,  # noqa
                                                  text=True, check=True).stdout)["lines"][0]
    heard = "integrator · cloud · last heard 2026-09-28 20:00 UTC"
    for how, words in HEARD.items():
        assert head(dict(base, heard_through=how)) == f"{heard} · {words}"
    # beside the silence (a keeper link's silence says more, below)
    assert head(dict(base, heard_through="client", silent=True)) == (
        f"{heard} · {HEARD['client']} · silent for more than two of its 30-minute intervals")
    assert head(base) == heard                                   # a server that does not say: nothing said
    assert head(dict(base, heard_through="anything else")) == heard


# ---- the keeper: where it starts, and where it ends --------------------------------------------------------------

def test_a_cloud_link_s_later_command_starts_a_keeper_once_for_its_session_and_again_after_a_restart(
        client, owner, served, capsys, tmp_path, monkeypatch):
    world = World(monkeypatch)
    t0 = world.t
    d = pair(client, owner, served, capsys, tmp_path / "cloud", "integrator", kind="cloud")
    assert len(world.started) == 1                           # the pairing's own start
    first = world.started[0]
    assert ["--watch", str(SESSION[0]), "--since", str(SESSION[1])] == first[first.index("--watch"):][:4]
    world.started.clear()
    # its keeper runs: a later command of its session finds it, and starts none
    later = []
    world.at.append((t0 + 10 * MINUTE, lambda: later.append(run(capsys, "--dir", d, "post", "step", "a step"))))
    world.end_at = t0 + 20 * MINUTE
    ade_link.main(first)
    assert later and later[0][0] == 0 and world.started == []
    # the container restarted: the session's process is another, its keeper gone with the old one; its next command
    # starts one for the new process
    world.session = AGAIN
    world.end_at = float("inf")
    world.t = t0 + 40 * MINUTE
    assert run(capsys, "--dir", d, "post", "step", "after the restart")[0] == 0
    assert len(world.started) == 1
    again = world.started[0]
    assert ["--watch", str(AGAIN[0]), "--since", str(AGAIN[1])] == again[again.index("--watch"):][:4]


def test_a_keeper_ends_when_its_link_is_refused(client, owner, served, capsys, tmp_path, monkeypatch):
    world = World(monkeypatch)
    t0 = world.t
    pair(client, owner, served, capsys, tmp_path / "cloud", "integrator", kind="cloud")
    assert len(world.started) == 1
    link_id = link_row("integrator")["id"]

    def revoke():
        with store.connect() as db:
            db.execute("UPDATE ade_links SET revoked_at = ? WHERE id = ?", (int(world.t), link_id))

    world.at.append((t0 + 10 * MINUTE, revoke))
    codes = world.keep()                                     # its session runs on; a keeper that beat on meets Bound
    assert codes and codes[0] != 0
    assert world.t < t0 + 2 * INTERVAL
    assert "pair again" in capsys.readouterr().err


def test_the_cloud_pairing_says_how_its_link_stays_heard(client, owner, served, capsys, tmp_path, monkeypatch):
    World(monkeypatch)
    with at_once():
        code, out, err = run(capsys, "pair", "--url", served, "--ticket",
                             __import__("test_ade_link_client").ticket(client, owner).lower(), "--name", "integrator",
                             "--kind", "cloud", "--dir", tmp_path / "cloud")
    assert code == 0, err
    assert ("ADE: a cloud session runs no hook of the ADE's, so this client keeps the link heard: a heartbeat at the "
            "link's interval while this session runs, ending when it ends (its log: " + str(tmp_path / "cloud")
            + os.sep + "keeper.log)") in out


def test_a_keeper_started_for_real_beats_as_the_keeper_in_a_session_of_its_own(client, owner, served, capsys,
                                                                               tmp_path, monkeypatch):
    # the one test of the real start: a process of its own, out of the command's session and its output, whose first
    # beat reaches the ADE as the keeper's; it ends when the process it watches ends
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        with open(f"/proc/{sleeper.pid}/stat") as fh:
            stat = fh.read()
        start = int(stat[stat.rindex(")") + 2:].split()[19])
        monkeypatch.setattr(ade_link, "_session_process", lambda *a: (sleeper.pid, start), raising=False)
        monkeypatch.setattr(ade_link, "_watched_session", lambda: (sleeper.pid, start), raising=False)
        if REAL_SPAWN is not None:
            monkeypatch.setattr(ade_link, "_spawn", REAL_SPAWN)
        monkeypatch.setattr(ade_link, "KEEP_CHECK", 0.2, raising=False)
        d = pair(client, owner, served, capsys, tmp_path / "cloud", "integrator", kind="cloud")
        deadline = time.time() + 20
        while time.time() < deadline and not link_row("integrator").get("keeper_heard_at"):
            time.sleep(0.1)
        assert link_row("integrator").get("keeper_heard_at")
        kept = json.loads((d / "keeper.json").read_text())
        # out of the command's session: in the one its start began (setsid), whose first process forked it and ended,
        # so neither the command's signals nor its end reach it
        assert kept["session_pid"] == sleeper.pid and os.getsid(kept["pid"]) != os.getsid(0)
        sleeper.kill()
        sleeper.wait(10)
        deadline = time.time() + 20
        while time.time() < deadline and not ended(kept["pid"]):
            time.sleep(0.1)
        assert ended(kept["pid"])
        assert "its session ended" in (d / "keeper.log").read_text()
    finally:
        if sleeper.poll() is None:
            sleeper.kill()


def ended(pid):
    """A process gone, or ended and not yet reaped."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            stat = fh.read()
    except FileNotFoundError:
        return True
    return stat[stat.rindex(")") + 2:].startswith("Z")


# ---- the proxy after a container restart ----------------------------------------------------------------------

def dead_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def a_cloud_link(directory, proxy):
    directory.mkdir(parents=True)
    (directory / "config.json").write_text(json.dumps({"url": "http://ade.example.test", "name": "integrator",
                                                       "kind": "cloud", "proxy": proxy, "link_id": "a" * 32}))
    ade_link.Link(str(directory)).key(make=True)
    return directory


def test_a_link_whose_recorded_proxy_refuses_takes_the_one_its_session_started_with(capsys, tmp_path, monkeypatch):
    now = StandIn(200, {})                                   # the platform's proxy after the restart, on a new port
    try:
        before = f"http://127.0.0.1:{dead_port()}"
        d = a_cloud_link(tmp_path / "cloud", before)
        monkeypatch.setattr(ade_link, "_session_process", lambda *a: SESSION, raising=False)
        monkeypatch.setattr(ade_link, "_watched_session", lambda: SESSION, raising=False)
        monkeypatch.setattr(ade_link, "_start_environ",
                            lambda pid: {"HTTP_PROXY": now.url} if pid == SESSION[0] else {}, raising=False)
        monkeypatch.setattr(ade_link, "_spawn", lambda argv: types.SimpleNamespace(pid=1), raising=False)
        decoy = StandIn(200, {})                             # the run's own environment, as a repository could set it
        try:
            monkeypatch.setenv("http_proxy", decoy.url)
            monkeypatch.setenv("HTTP_PROXY", decoy.url)
            code, _, err = run(capsys, "--dir", d, "heartbeat")
            assert decoy.requests == []
        finally:
            decoy.close()
        assert code == 0, err
        assert [r[1] for r in now.requests if "/heartbeat" in r[1]] == ["http://ade.example.test/api/ade/link/heartbeat"]
        assert json.loads((d / "config.json").read_text())["proxy"] == now.url        # every later request goes so
        assert f"took the proxy its session started with, {now.url}, in place of {before}" in err
    finally:
        now.close()


@pytest.mark.parametrize("named", ["none", "another host", "credentials", "another scheme", "the same"])
def test_a_proxy_the_session_did_not_start_with_on_this_host_is_never_taken_and_it_says_so(capsys, tmp_path,
                                                                                         monkeypatch, named):
    live = StandIn(200, {})
    try:
        port = int(live.url.rsplit(":", 1)[1])
        before = f"http://127.0.0.1:{dead_port()}"
        d = a_cloud_link(tmp_path / "cloud", before)
        environ = {"none": {}, "another host": {"HTTP_PROXY": f"http://localhost:{port}"},
                   "credentials": {"HTTP_PROXY": f"http://u:p@127.0.0.1:{port}"},
                   "another scheme": {"HTTP_PROXY": f"https://127.0.0.1:{port}"},
                   "the same": {"HTTP_PROXY": before}}[named]
        monkeypatch.setattr(ade_link, "_session_process", lambda *a: SESSION, raising=False)
        monkeypatch.setattr(ade_link, "_watched_session", lambda: SESSION, raising=False)
        monkeypatch.setattr(ade_link, "_start_environ", lambda pid: environ, raising=False)
        monkeypatch.setattr(ade_link, "_spawn", lambda argv: types.SimpleNamespace(pid=1), raising=False)
        monkeypatch.setenv("http_proxy", live.url)          # the run's own: never taken
        code, _, err = run(capsys, "--dir", d, "heartbeat")
        assert code == 3
        assert live.requests == []
        assert json.loads((d / "config.json").read_text())["proxy"] == before
        assert f"the proxy this link recorded, {before}, refuses connections" in err and "pair again" in err
    finally:
        live.close()


# ---- the keeper's session and lock, older pairings and ADEs, and the proxy's words -----------------------------

def real_lookup(monkeypatch, tmp_path):
    """The client's own lookup of its session's process, with this run's CLAUDE_CONFIG_DIR and HOME a test's own
    (neither the trusted records folder, which the conftest gives each test, nor the run's)."""
    monkeypatch.setattr(ade_link, "_session_process", REAL_LOOKUP)
    if REAL_WATCHED is not None:
        monkeypatch.setattr(ade_link, "_watched_session", REAL_WATCHED)
    untrusted = tmp_path / "untrusted-config"
    (untrusted / "sessions").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(untrusted))
    monkeypatch.setenv("HOME", str(tmp_path / "untrusted-home"))
    return untrusted / "sessions"


def record_for(folder, pid, start=None):
    """Claude Code's record of a running process, as it writes one: its pid, a session id and its start."""
    os.makedirs(folder, exist_ok=True)
    start = ade_link._process_start(pid) if start is None else start
    with open(os.path.join(folder, f"{pid}.json"), "w") as fh:
        json.dump({"pid": pid, "sessionId": "a" * 8 + "-session", "procStart": start, "cwd": "/w"}, fh)


def a_cloud_dir(directory, url="http://ade.example.test", link_id="b" * 32, **more):
    directory.mkdir(parents=True)
    (directory / "config.json").write_text(json.dumps(dict({"url": url, "name": "integrator", "kind": "cloud",
                                                            "link_id": link_id}, **more)))
    ade_link.Link(str(directory)).key(make=True)
    return directory


def starts(monkeypatch):
    got = []
    monkeypatch.setattr(ade_link, "_spawn", lambda argv: got.append([str(a) for a in argv])
                        or types.SimpleNamespace(pid=1, poll=lambda: None, wait=lambda timeout=None: 0))
    return got


def test_a_record_planted_where_the_session_s_environment_points_names_no_session(tmp_path, monkeypatch):
    # a program in the session can point CLAUDE_CONFIG_DIR at a folder of its own, with a record naming this command's
    # own shell: the keeper does not watch it, and no proxy is read from its environment
    untrusted = real_lookup(monkeypatch, tmp_path)
    record_for(untrusted, os.getppid())
    got = starts(monkeypatch)
    d = a_cloud_dir(tmp_path / "cloud")
    assert ade_link._keep_heard(ade_link.Link(str(d))) != "started" and got == []
    monkeypatch.setattr(ade_link, "_start_environ", lambda pid: {"HTTP_PROXY": "http://127.0.0.1:9999"},
                        raising=False)
    link = ade_link.Link(str(d))
    assert ade_link._proxy_again(link, "http://127.0.0.1:42077")[0] is None


def test_the_keeper_watches_the_process_a_genuine_record_names_in_the_trusted_folder(tmp_path, monkeypatch):
    # the real lookup: a record in the password database's home's folder (the conftest gives each test its own),
    # naming a process that runs with that start, is the session's
    real_lookup(monkeypatch, tmp_path)
    record_for(ade_link._take_sessions_dir(), os.getppid())
    got = starts(monkeypatch)
    d = a_cloud_dir(tmp_path / "cloud")
    assert ade_link._keep_heard(ade_link.Link(str(d))) == "started"
    (argv,) = got
    assert argv[argv.index("--watch") + 1] == str(os.getppid())


def test_a_record_whose_start_is_not_its_process_s_names_no_session(tmp_path, monkeypatch):
    # a control, its untrusted folder empty: a record with another start may be planted
    real_lookup(monkeypatch, tmp_path)
    record_for(ade_link._take_sessions_dir(), os.getppid(), start="12345")
    got = starts(monkeypatch)
    assert ade_link._keep_heard(ade_link.Link(str(a_cloud_dir(tmp_path / "cloud")))) != "started" and got == []


def test_a_ps_on_path_cannot_keep_an_ended_process_running(tmp_path, monkeypatch):
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    start = ade_link._process_start(os.getpid())            # a start the fake ps will say any process has
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ps").write_text(f"#!/bin/sh\necho {start}\n")
    (bin_dir / "ps").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    assert ade_link._process_alive(child.pid, start) is False


def test_a_keeper_of_another_link_in_the_directory_is_not_this_link_s(client, owner, served, capsys, tmp_path,
                                                                     monkeypatch):
    # a re-pair in the same directory while the old link's keeper runs: the new link gets a keeper of its own
    world = World(monkeypatch)
    d = pair(client, owner, served, capsys, tmp_path / "cloud", "integrator", kind="cloud")
    world.started.clear()
    (d / "keeper.json").write_text(json.dumps({"pid": os.getpid(), "start": ade_link._process_start(os.getpid()),
                                                "session_pid": SESSION[0], "session_start": SESSION[1],
                                                "link_id": "c" * 32}))
    assert run(capsys, "--dir", d, "post", "step", "after a re-pair")[0] == 0
    assert len(world.started) == 1


def test_a_keeper_watching_another_session_is_not_this_session_s(client, owner, served, capsys, tmp_path,
                                                                monkeypatch):
    # the Claude Code process was replaced: the old keeper, alive for up to a look, watches a process that ended
    world = World(monkeypatch)
    d = pair(client, owner, served, capsys, tmp_path / "cloud", "integrator", kind="cloud")
    world.started.clear()
    link_id = json.loads((d / "config.json").read_text())["link_id"]
    (d / "keeper.json").write_text(json.dumps({"pid": os.getpid(), "start": ade_link._process_start(os.getpid()),
                                                "session_pid": AGAIN[0], "session_start": AGAIN[1],
                                                "link_id": link_id}))
    assert run(capsys, "--dir", d, "post", "step", "after a replaced process")[0] == 0
    assert len(world.started) == 1


def test_a_keeper_started_while_another_holds_the_lock_takes_over_when_it_ends(client, owner, served, capsys,
                                                                               tmp_path, monkeypatch):
    import fcntl
    world = World(monkeypatch)
    t0 = world.t
    d = pair(client, owner, served, capsys, tmp_path / "cloud", "integrator", kind="cloud")
    held = open(d / "keeper.lock", "a")
    fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def release():
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
        held.close()

    world.at.append((t0 + 20, release))                       # the earlier keeper ends at its next look
    world.end_at = t0 + 10 * MINUTE
    try:
        codes = world.keep()
    except Bound:
        codes = None
    assert codes == [0] and world.beats and world.beats[0] >= t0 + 20


def test_a_keeper_link_s_silence_says_its_container_may_have_restarted(tmp_path):
    # the old page: a session whose keeper was heard last, silent now, ended, or its container restarted and it has
    # not called its client since
    script = read("web", "app.js")
    grab = lambda name: re.search(rf"function {name}\(.*?\n\}}\n", script, re.S).group(0)  # noqa: E731
    one = lambda name: re.search(rf"function {name}\([^\n]*\}}\n", script).group(0)  # noqa: E731
    probe = tmp_path / "card.js"
    probe.write_text("".join(grab(f) for f in ("el", "buttonFor", "claudeLink", "linkLine", "linkCard"))
                     + "".join(one(f) for f in ("utc", "unix", "shortLine"))
                     + re.search(r"const HIDDEN_ALL = [^\n]*\n", script).group(0) + grab("visible") + CARD)
    link = {"id": "L1", "name": "integrator", "kind": "cloud", "silent": True, "checkpoint": None, "events": 0,
            "last_event": None, "last_heartbeat_at": None, "last_heard_at": 1790625600, "interval_min": 30,
            "heard_through": "keeper"}
    line = json.loads(subprocess.run([NODE, str(probe), json.dumps(link)], capture_output=True, text=True,
                                     check=True).stdout)["lines"][0]
    assert line.endswith(" · silent for more than two of its 30-minute intervals: its session ended, or its "
                         "container restarted and it has not called its client since")


def test_a_local_link_heard_by_its_watcher_long_after_its_hook_is_still_heard_through_its_hook(client, owner,
                                                                                                 monkeypatch):
    from proofpane_open.ade import link as server_link
    t0 = int(time.time())
    clock = [t0]
    monkeypatch.setattr(server_link.time, "time", lambda: clock[0])
    mac = paired(client, owner)
    clock[0] = t0 + 60
    assert mac.call("GET", "/feed", query={"via": "hook"}, ts=clock[0]).status_code == 200
    for minute in range(5, 40, 5):                               # its watcher's reads, unmarked
        clock[0] = t0 + 60 + minute * MINUTE
        assert mac.call("GET", "/feed", ts=clock[0]).status_code == 200
    seen = next(x for x in client.get("/api/ade/link/sessions", headers=owner).json()["links"] if x["name"] == "mac")
    assert seen.get("heard_through") == "hook"


def test_a_cloud_link_paired_before_its_kind_was_recorded_learns_it_and_is_kept_heard(client, owner, served,
                                                                                     capsys, tmp_path,
                                                                                     monkeypatch):
    world = World(monkeypatch)
    d = pair(client, owner, served, capsys, tmp_path / "cloud", "integrator", kind="cloud")
    config = json.loads((d / "config.json").read_text())
    del config["kind"]                                           # as an older release's pairing wrote it
    (d / "config.json").write_text(json.dumps(config))
    world.started.clear()
    assert run(capsys, "--dir", d, "post", "step", "a step")[0] == 0
    assert len(world.started) == 1
    assert json.loads((d / "config.json").read_text()).get("kind") == "cloud"


def test_a_keeper_whose_ade_names_no_kind_ends_and_no_command_starts_another(client, owner, served, capsys,
                                                                             tmp_path, monkeypatch):
    # an older ADE answers a beat with {} and reads it as a watcher's: the keeper ends, and says why
    world = World(monkeypatch)
    d = pair(client, owner, served, capsys, tmp_path / "cloud", "integrator", kind="cloud")
    beat = ade_link.Link.request

    def older(link, method, path, *args, **kwargs):
        if path == "/heartbeat":
            world.beats.append(world.t)
            return {}
        return beat(link, method, path, *args, **kwargs)

    monkeypatch.setattr(ade_link.Link, "request", older)
    try:
        codes = world.keep()
    except Bound:
        codes = None
    assert codes is not None and len(world.beats) == 1
    world.started.clear()
    assert run(capsys, "--dir", d, "post", "step", "a step")[0] == 0
    assert world.started == []


def test_a_keeper_record_that_cannot_be_read_stops_no_command(client, owner, served, capsys, tmp_path,
                                                               monkeypatch):
    World(monkeypatch)
    d = pair(client, owner, served, capsys, tmp_path / "cloud", "integrator", kind="cloud")
    (d / "keeper.json").mkdir()
    assert run(capsys, "--dir", d, "post", "step", "a step")[0] == 0
    assert run(capsys, "--dir", d, "resume")[0] == 0


def test_setup_of_a_linked_cloud_session_does_not_say_nothing_was_sent(client, owner, served, capsys, tmp_path,
                                                                        monkeypatch):
    World(monkeypatch)
    d = pair(client, owner, served, capsys, tmp_path / "cloud", "integrator", kind="cloud")
    code, out, err = run(capsys, "setup", "--name", "integrator", "--kind", "cloud", "--dir", d)
    assert code == 0, err
    assert "nothing was sent" not in out


def test_the_reason_a_proxy_is_not_taken_is_said_whole(capsys, tmp_path, monkeypatch):
    live = StandIn(200, {})
    try:
        port = int(live.url.rsplit(":", 1)[1])
        before = f"http://127.0.0.1:{dead_port()}"
        d = a_cloud_link(tmp_path / "cloud", before)
        monkeypatch.setattr(ade_link, "_session_process", lambda *a: SESSION, raising=False)
        monkeypatch.setattr(ade_link, "_watched_session", lambda: SESSION, raising=False)
        monkeypatch.setattr(ade_link, "_start_environ", lambda pid: {"HTTP_PROXY": f"http://localhost:{port}"},
                            raising=False)
        monkeypatch.setattr(ade_link, "_spawn", lambda argv: types.SimpleNamespace(pid=1), raising=False)
        code, _, err = run(capsys, "--dir", d, "heartbeat")
        assert code == 3
        assert (f"the one its session started with, http://localhost:{port}, is not another port of the same proxy, "
                "the only one this client takes in its place") in err
    finally:
        live.close()


@pytest.mark.parametrize("named", ["taken", "refused"])
def test_a_proxy_s_credentials_are_never_printed(capsys, tmp_path, monkeypatch, named):
    live = StandIn(200, {})
    try:
        port = int(live.url.rsplit(":", 1)[1])
        before = f"http://u:recorded-pass@127.0.0.1:{dead_port()}"
        d = a_cloud_link(tmp_path / "cloud", before)
        given = {"taken": f"http://u:recorded-pass@127.0.0.1:{port}",
                 "refused": f"http://u:other-pass@127.0.0.1:{port}"}[named]
        monkeypatch.setattr(ade_link, "_session_process", lambda *a: SESSION, raising=False)
        monkeypatch.setattr(ade_link, "_watched_session", lambda: SESSION, raising=False)
        monkeypatch.setattr(ade_link, "_start_environ", lambda pid: {"HTTP_PROXY": given}, raising=False)
        monkeypatch.setattr(ade_link, "_spawn", lambda argv: types.SimpleNamespace(pid=1), raising=False)
        code, out, err = run(capsys, "--dir", d, "heartbeat")
        assert code == (0 if named == "taken" else 3)
        assert "recorded-pass" not in out + err and "other-pass" not in out + err
    finally:
        live.close()


def test_a_keeper_started_for_real_is_not_its_command_s_child_and_runs_isolated(client, owner, served, capsys,
                                                                                tmp_path, monkeypatch):
    # stopping a command stops the processes under it (measured here: a setsid child of a command stopped by the
    # harness ended with it), so the keeper is no child of the command that starts it; and Python runs it isolated
    # (-I), as the page's line and the hooks run the client
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    kept = None
    try:
        start = ade_link._process_start(sleeper.pid)
        session = (sleeper.pid, int(start) if str(start).isdigit() else start)
        monkeypatch.setattr(ade_link, "_session_process", lambda *a: session, raising=False)
        monkeypatch.setattr(ade_link, "_watched_session", lambda: session, raising=False)
        if REAL_SPAWN is not None:
            monkeypatch.setattr(ade_link, "_spawn", REAL_SPAWN)
        monkeypatch.setattr(ade_link, "KEEP_CHECK", 0.2, raising=False)
        d = pair(client, owner, served, capsys, tmp_path / "cloud", "integrator", kind="cloud")
        deadline = time.time() + 20
        while time.time() < deadline and not (d / "keeper.json").exists():
            time.sleep(0.1)
        kept = json.loads((d / "keeper.json").read_text())
        with open(f"/proc/{kept['pid']}/stat") as fh:
            stat = fh.read()
        assert int(stat[stat.rindex(")") + 2:].split()[1]) != os.getpid()
        with open(f"/proc/{kept['pid']}/cmdline", "rb") as fh:
            assert b"-I" in fh.read().split(b"\0")
    finally:
        sleeper.kill()
        sleeper.wait(10)
        # it ends at its next look, the process it watches gone: waited for, as it is no child of this test's
        deadline = time.time() + 20
        while kept and time.time() < deadline and not ended(kept["pid"]):
            time.sleep(0.1)
    assert kept and ended(kept["pid"])


# ---- more of the keeper, the session's process, the marks, the old page and the proxy -----------------------------

def test_a_local_link_s_own_commands_start_no_keeper(client, owner, served, capsys, tmp_path, monkeypatch):
    # a local link is heard through its hook: its session's own commands, which start a cloud link's keeper, start none
    world = World(monkeypatch)
    d = pair(client, owner, served, capsys, tmp_path / "mac", "mac", kind="local")
    world.t += 2 * MINUTE
    assert run(capsys, "--dir", d, "post", "step", "a step")[0] == 0
    assert world.started == []


def test_a_proxy_that_refuses_too_is_not_recorded(capsys, tmp_path, monkeypatch):
    # the one the session started with, another port of the same proxy, refuses too: config.json keeps the one
    # recorded, and the request fails saying both refuse
    before = f"http://127.0.0.1:{dead_port()}"
    after = before
    while after == before:
        after = f"http://127.0.0.1:{dead_port()}"
    d = a_cloud_link(tmp_path / "cloud", before)
    monkeypatch.setattr(ade_link, "_watched_session", lambda: SESSION, raising=False)
    monkeypatch.setattr(ade_link, "_start_environ", lambda pid: {"HTTP_PROXY": after} if pid == SESSION[0] else {},
                        raising=False)
    monkeypatch.setattr(ade_link, "_spawn", lambda argv: types.SimpleNamespace(pid=1), raising=False)
    code, _, err = run(capsys, "--dir", d, "heartbeat")
    assert code != 0
    assert json.loads((d / "config.json").read_text())["proxy"] == before
    assert f"and so does the one its session started with, {after}" in err


def test_a_record_whose_own_pid_is_another_process_s_names_no_session(tmp_path, monkeypatch):
    # a record under one process's name holding another's pid (copied, or left from before) is not that process's
    real_lookup(monkeypatch, tmp_path)
    folder = ade_link._take_sessions_dir()
    record_for(folder, os.getppid())
    path = os.path.join(folder, f"{os.getppid()}.json")
    with open(path) as fh:
        data = json.load(fh)
    data["pid"] = os.getppid() + 1
    with open(path, "w") as fh:
        json.dump(data, fh)
    got = starts(monkeypatch)
    assert ade_link._keep_heard(ade_link.Link(str(a_cloud_dir(tmp_path / "cloud")))) != "started" and got == []


def test_a_keeper_on_a_filesystem_that_takes_no_lock_runs_and_says_so(client, owner, served, capsys, tmp_path,
                                                                      monkeypatch):
    import errno
    import fcntl
    world = World(monkeypatch)
    t0 = world.t
    d = pair(client, owner, served, capsys, tmp_path / "cloud", "integrator", kind="cloud")
    capsys.readouterr()

    def no_lock(fd, operation):
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(fcntl, "flock", no_lock)
    world.end_at = t0 + 5 * MINUTE
    assert world.keep() == [0] and world.beats
    assert "takes no lock" in capsys.readouterr().err
    assert d.exists()


def test_a_keeper_waiting_for_the_lock_ends_when_its_session_ends(client, owner, served, capsys, tmp_path,
                                                                 monkeypatch):
    # the lock is never given up, and the session ends five seconds on: the keeper waiting for it ends then, not at
    # the end of its wait
    import fcntl
    world = World(monkeypatch)
    t0 = world.t
    d = pair(client, owner, served, capsys, tmp_path / "cloud", "integrator", kind="cloud")
    held = open(d / "keeper.lock", "a")
    fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        world.end_at = t0 + 5
        assert world.keep() == [0] and world.beats == [] and world.t < t0 + 10
    finally:
        held.close()


@pytest.mark.parametrize("argv, stand_in, mark", [
    (["pretool"], "cmd_pretool", "hook"),
    (["mirror", "flush"], "mirror_quietly", "hook"),
    (["session", "start"], "session_quietly", "hook"),
    (["turn", "end"], "turn_quietly", "hook"),
    (["permission"], "permission_quietly", "hook"),
    (["history", "--soon"], "_history_soon", "hook"),
    (["history"], "cmd_history", None),
    (["post", "step", "a step"], "cmd_post", None),
])
def test_each_hook_command_marks_its_requests_as_the_hook_s_and_no_other_command_does(argv, stand_in, mark,
                                                                                     tmp_path, monkeypatch):
    # each command Claude Code runs as a hook marks its
    # requests, and the session's own commands mark none, and no mark outlives its run
    d = a_cloud_dir(tmp_path / "link", kind="local")
    seen = []
    monkeypatch.setattr(ade_link, stand_in, lambda *a, **k: seen.append(ade_link.VIA) or 0)
    ade_link.main(["--dir", str(d)] + argv)
    assert seen == [mark] and ade_link.VIA is None


def test_a_keeper_beat_two_intervals_before_the_time_heard_is_the_keeper_s_and_a_second_more_is_not():
    # the window's edge, two of the link's intervals exactly
    from proofpane_open.ade import link as server_link
    heard, window = 1_800_000_000, 2 * 30 * 60
    row = {"kind": "cloud", "interval_min": 30, "last_seen_at": heard, "last_heartbeat_at": None, "hook_heard_at": None}
    assert server_link.heard_through(dict(row, keeper_heard_at=heard - window)) == "keeper"
    assert server_link.heard_through(dict(row, keeper_heard_at=heard - window - 1)) == "client"


def test_the_process_that_forks_the_keeper_is_reaped_before_the_command_goes_on(tmp_path):
    # the process _spawn starts forks the keeper and ends at once: it is waited for, so it is never left a zombie of a
    # long command (a waiting pair, a watch); the keeper it forked watches a process that does not run, and ends
    d = a_cloud_dir(tmp_path / "cloud")
    started = REAL_SPAWN(["--dir", str(d), "keep", "--watch", 2 ** 22 + 7, "--since", "1", "--check", 0.2])
    assert started.returncode == 0
    deadline = time.time() + 20
    while time.time() < deadline and "its session ended" not in (d / "keeper.log").read_text():
        time.sleep(0.1)
    assert "its session ended" in (d / "keeper.log").read_text()


def test_an_ade_that_says_no_kind_is_asked_once_not_at_every_command(client, owner, served, capsys, tmp_path,
                                                                    monkeypatch):
    # a link paired before the kind was recorded, against an ADE whose state says none: it
    # asks once, records that none was said, and its later commands ask no more and start no keeper
    from proofpane_open.ade import link_commands
    world = World(monkeypatch)
    d = pair(client, owner, served, capsys, tmp_path / "cloud", "integrator", kind="cloud")
    config = json.loads((d / "config.json").read_text())
    del config["kind"]
    (d / "config.json").write_text(json.dumps(config))
    real_state = link_commands.state
    monkeypatch.setattr(link_commands, "state", lambda *a, **k: {key: value for key, value in real_state(*a, **k).items()
                                                                if key not in ("kind", "interval_min")})
    asked, inner = [], ade_link.Link.request

    def counting(link, method, path, *args, **kwargs):
        if path == "/state":
            asked.append(path)
        return inner(link, method, path, *args, **kwargs)

    monkeypatch.setattr(ade_link.Link, "request", counting)
    world.started.clear()
    for n in range(3):
        world.t += MINUTE
        assert run(capsys, "--dir", d, "post", "step", f"step {n}")[0] == 0
    assert asked == ["/state"] and world.started == []


def test_a_keeper_ends_at_its_next_look_once_its_directory_holds_another_link(client, owner, served, capsys,
                                                                              tmp_path, monkeypatch):
    world = World(monkeypatch)
    t0 = world.t
    d = pair(client, owner, served, capsys, tmp_path / "cloud", "integrator", kind="cloud")

    def paired_again():
        config = json.loads((d / "config.json").read_text())
        config["link_id"] = "c" * 32
        (d / "config.json").write_text(json.dumps(config))

    world.at.append((t0 + 5 * MINUTE, paired_again))
    assert world.keep() == [0]
    assert world.t <= t0 + 5 * MINUTE + 2 * ade_link.KEEP_CHECK


def test_a_keeper_that_ends_drops_the_record_naming_it(client, owner, served, capsys, tmp_path, monkeypatch):
    world = World(monkeypatch)
    d = pair(client, owner, served, capsys, tmp_path / "cloud", "integrator", kind="cloud")
    world.end_at = world.t + 5 * MINUTE
    assert world.keep() == [0]
    assert not (d / "keeper.json").exists()


def test_a_keeper_beats_at_the_interval_the_ade_answers(client, owner, served, capsys, tmp_path, monkeypatch):
    # an ADE whose cloud links beat every 20 minutes, not the keeper's default of 30
    world = World(monkeypatch)
    t0 = world.t
    pair(client, owner, served, capsys, tmp_path / "cloud", "integrator", kind="cloud")
    inner = ade_link.Link.request

    def every_20(link, method, path, *args, **kwargs):
        answer = inner(link, method, path, *args, **kwargs)
        return dict(answer, interval_min=20) if path == "/heartbeat" and isinstance(answer, dict) else answer

    monkeypatch.setattr(ade_link.Link, "request", every_20)
    world.end_at = t0 + 50 * MINUTE
    assert world.keep() == [0]
    assert len(world.beats) >= 2 and world.beats[1] - world.beats[0] == 20 * MINUTE


@pytest.mark.skipif(not os.path.exists("/proc/self/stat"), reason="a zombie is read from /proc")
def test_a_process_that_ended_and_waits_to_be_reaped_does_not_run():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        deadline = time.time() + 20
        while time.time() < deadline and not ended(child.pid):
            time.sleep(0.05)
        assert ended(child.pid) and os.path.exists(f"/proc/{child.pid}")        # ended, not yet reaped
        start = ade_link._process_start(child.pid)
        assert start is not None and ade_link._process_alive(child.pid, start) is False
    finally:
        child.wait(10)


def old_page_head(tmp_path, link):
    """The first line of the old page's card for `link`, as node renders it."""
    script = read("web", "app.js")
    grab = lambda name: re.search(rf"function {name}\(.*?\n\}}\n", script, re.S).group(0)  # noqa: E731
    one = lambda name: re.search(rf"function {name}\([^\n]*\}}\n", script).group(0)  # noqa: E731
    probe = tmp_path / "card.js"
    probe.write_text("".join(grab(f) for f in ("el", "buttonFor", "claudeLink", "linkLine", "linkCard"))
                     + "".join(one(f) for f in ("utc", "unix", "shortLine"))
                     + re.search(r"const HIDDEN_ALL = [^\n]*\n", script).group(0) + grab("visible") + CARD)
    return json.loads(subprocess.run([NODE, str(probe), json.dumps(link)], capture_output=True, text=True,
                                     check=True).stdout)["lines"][0]


def test_the_old_page_says_nothing_of_how_for_a_link_not_heard_nor_a_word_it_does_not_hold(tmp_path):
    base = {"id": "L1", "name": "integrator", "kind": "cloud", "silent": False, "checkpoint": None, "events": 0,
            "last_event": None, "last_heartbeat_at": None, "interval_min": 30}
    assert old_page_head(tmp_path, dict(base, last_heard_at=None, heard_through="hook")) == (
        "integrator · cloud · not heard from yet")
    heard = dict(base, last_heard_at=1790625600)
    for word in ("constructor", "toString", "__proto__"):
        assert old_page_head(tmp_path, dict(heard, heard_through=word)) == (
            "integrator · cloud · last heard 2026-09-28 20:00 UTC")


def test_a_request_marked_twice_is_marked_by_neither():
    # the client marks a request once; one marked twice, alike or not, counts as unmarked
    from proofpane_open.ade import link as server_link
    assert server_link._via("via=hook") == "hook" and server_link._via("via=keeper") == "keeper"
    assert server_link._via("via=hook&via=hook") is None
    assert server_link._via("via=keeper&via=hook") is None
    assert server_link._via("via=other") is None and server_link._via("") is None


def test_a_recorded_proxy_that_resets_the_connection_is_not_replaced(capsys, tmp_path, monkeypatch):
    # only a refused connection (nothing listens at the recorded port, a container's restart) takes the session's
    # proxy in its place: one that accepts and resets is the recorded proxy answering, and is kept
    import struct
    import threading
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)

    def resets():
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            conn.close()

    threading.Thread(target=resets, daemon=True).start()
    now = StandIn(200, {})
    try:
        before = f"http://127.0.0.1:{listener.getsockname()[1]}"
        d = a_cloud_link(tmp_path / "cloud", before)
        monkeypatch.setattr(ade_link, "_watched_session", lambda: SESSION, raising=False)
        monkeypatch.setattr(ade_link, "_start_environ",
                            lambda pid: {"HTTP_PROXY": now.url} if pid == SESSION[0] else {}, raising=False)
        monkeypatch.setattr(ade_link, "_spawn", lambda argv: types.SimpleNamespace(pid=1), raising=False)
        code, _, _ = run(capsys, "--dir", d, "heartbeat")
        assert code != 0 and now.requests == []
        assert json.loads((d / "config.json").read_text())["proxy"] == before
    finally:
        now.close()
        listener.close()
