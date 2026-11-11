"""One link per machine, every session its child: a Claude Code session whose start this machine's link
recorded is registered in its History list; a registered session's mirrored notes name it, so the page shows each
session's conversation on its own; a session's notes go once it is neither listed unwithheld nor named by id, and the
head never goes back; the list carries the repositories a session worked in and the machine's own id."""

import json
import os
import time

import pytest

import ade_link
from proofpane_open import store
from proofpane_open.ade import link as server_link
from test_ade_link import confirmed
from test_ade_link_client import link_row, owner, pair, run, served  # noqa: F401  fixtures and helpers


def _row(sid, **over):
    return dict({"id": sid, "title": f"title {sid}", "project": "~/work", "branch": "main", "client": "vscode",
                 "live": None, "started_at": 1_790_000_000, "last_at": 1_790_000_100}, **over)


def _note(mac, text, session=None, client_id=None, **extra):
    body = dict({"client_id": client_id or f"c-{time.time_ns()}", "kind": "note", "to": "owner", "mirror": "session",
                 "text": text}, **extra)
    if session is not None:
        body["session"] = session
    return server_link.post_event(mac, body)


def _tagged(link_id):
    with store.connect() as db:
        return sorted((r["session_id"], r["text"]) for r in db.execute(
            "SELECT session_id, text FROM ade_link_events WHERE link_id = ? AND session_id IS NOT NULL", (link_id,)))


@pytest.fixture
def mac(client, owner, served, capsys, tmp_path):
    pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    return link_row("mac")


# ---- the server -----------------------------------------------------------------------------------------------------

def test_a_mirrored_note_names_its_session_only_where_the_list_has_it_registered_and_not_withheld(mac):
    server_link.put_history(mac, {"sessions": [_row("reg", registered=True), _row("unreg"),
                                               _row("held", withheld=True, title=None, branch=None, registered=True)]})
    assert _note(mac, "one", "reg")["session"] == "reg"
    assert _note(mac, "two", "unreg")["session"] is None             # listed, not registered: the link's own
    assert _note(mac, "three", "unlisted")["session"] is None        # not listed: the link's own, as before
    with pytest.raises(server_link.LinkError) as e:                   # withheld: its words stay on its machine
        _note(mac, "four", "held")
    assert e.value.status == 409
    assert _tagged(mac["id"]) == [("reg", "one")]
    own = [ev["text"] for ev in server_link.timeline(mac["user_id"], mac["id"])["events"]]
    assert "one" not in own and {"two", "three"} <= set(own)
    reg = server_link.timeline(mac["user_id"], mac["id"], session="reg")["events"]
    assert [(ev["text"], ev["session"]) for ev in reg] == [("one", "reg")]
    (card,) = server_link.sessions(mac["user_id"])["links"]              # its card counts its own, as its timeline
    assert card["events"] == 2 and card["last_event"]["text"] == "three"
    again = _note(mac, "one again", "reg", client_id="same")
    assert _note(mac, "other words", "unreg", client_id="same") == again    # the first row for a repeated client_id
    with pytest.raises(server_link.LinkError) as e:
        server_link.timeline(mac["user_id"], mac["id"], session="a b")
    assert e.value.status == 422


def test_only_a_mirrored_note_names_a_session_or_when_it_was_said_and_each_is_held_to_its_form(mac):
    now = int(time.time())
    server_link.put_history(mac, {"sessions": [_row("reg", registered=True)]})
    bad = [{"session": "reg", "mirror": None}, {"said_at": now * 1000, "mirror": None}, {"session": "a b"},
           {"session": "sk-ant-api03-x"}, {"session": 5}, {"said_at": True}, {"said_at": "1"}, {"said_at": 0},
           {"said_at": (now + 400) * 1000}]
    for extra in bad:
        body = dict({"client_id": f"b-{time.time_ns()}", "kind": "note", "to": "owner", "mirror": "session",
                     "text": "x"}, **extra)
        if body["mirror"] is None:
            body.pop("mirror")
        with pytest.raises(server_link.LinkError) as e:
            server_link.post_event(mac, body)
        assert e.value.status == 422, extra
    seq = _note(mac, "said", "reg", said_at=now * 1000 - 5)["seq"]
    (ev,) = [e for e in server_link.timeline(mac["user_id"], mac["id"], session="reg")["events"] if e["seq"] == seq]
    assert ev["said_at"] == now * 1000 - 5


def test_a_session_s_notes_go_only_when_its_list_says_it_is_withheld_the_head_kept(mac, client, owner):
    put = lambda body: server_link.put_history(mac, body)  # noqa: E731
    put({"sessions": [_row("a", registered=True), _row("b", registered=True), _row("c", registered=True)]})
    _note(mac, "a one", "a")
    _note(mac, "b one", "b")
    _note(mac, "c one", "c")
    head = _note(mac, "a two", "a")["seq"]
    assert put({"sessions": [_row("a"), _row("b"), _row("c")]})["noted"] == ["a", "b", "c"]    # no longer registered
    # a list that leaves one out (past the newest, its transcript cleaned up, a row refused), or none at all: all stay
    assert put({"sessions": [_row("b"), _row("bad", title="a\u202eb")]})["noted"] == ["a", "b", "c"]
    assert put({"sessions": []})["noted"] == ["a", "b", "c"] and len(_tagged(mac["id"])) == 4
    got = put({"sessions": [_row("a", withheld=True, title=None, branch=None), _row("b")],
               "withheld": ["c", "a b", 7, {}]})                       # a row withheld, and one named by id
    assert got["noted"] == ["b"] and _tagged(mac["id"]) == [("b", "b one")]
    feed = client.get("/api/ade/link/history", headers=owner)
    assert feed.status_code == 200
    # the head does not go back with the newest notes: a seq a session saw is never past it
    with store.connect() as db:
        assert server_link._head(db, mac["user_id"]) == head
    assert server_link.ack(mac, {"seq": head})["acked_seq"] >= head
    assert _note(mac, "later", None)["seq"] > head


def test_revoking_a_link_takes_its_sessions_notes_with_its_list(mac, client, owner):
    server_link.put_history(mac, {"sessions": [_row("a", registered=True)]})
    _note(mac, "tagged", "a")
    _note(mac, "its own")
    server_link.revoke(mac["user_id"], mac["id"], confirmed(client, owner, f"link-revoke:{mac['id']}"))
    with store.connect() as db:
        texts = [r[0] for r in db.execute("SELECT text FROM ade_link_events WHERE link_id = ?", (mac["id"],))]
    assert texts == ["its own"]


def test_a_list_carries_whether_each_session_is_registered_its_repositories_and_the_machine(mac):
    many = [f"~/work/r{i}" for i in range(server_link.HISTORY_REPOS + 5)]
    got = server_link.put_history(mac, {"sessions": [
        _row("a", registered=True, repositories=["~/work/app", "a\u202eb", "~/work/app", "~/work/lib", "sk-ant-x"]),
        _row("b", repositories=many),
        _row("held", withheld=True, title=None, branch=None, repositories=["~/work/app"]),   # refused: left out
        _row("odd", repositories="~/work/app"), _row("flag", registered="yes"),
        _row("held2", withheld=True, title=None, branch=None, registered=True)], "machine": "m-1234abcd"})
    assert (got["listed"], got["left_out"]) == (3, 3)
    listed = {s["id"]: s for s in server_link.history(mac["user_id"])["sessions"]}
    assert listed["a"]["registered"] is True and listed["b"]["registered"] is False
    assert listed["held2"]["registered"] is False                                # never a withheld one
    assert listed["a"]["repositories"] == ["~/work/app", "~/work/lib"]          # a refused one left out alone
    assert listed["b"]["repositories"] == many[:server_link.HISTORY_REPOS]
    assert {s["machine"] for s in listed.values()} == {"m-1234abcd"} and listed["a"]["notes"] == 0
    for body in ({"machine": "short"}, {"machine": "AKIA" + "A" * 16}, {"machine": 5}, {"withheld": "a"},
                 {"withheld": ["a"] * (server_link.HISTORY_WITHHELD + 1)}):
        with pytest.raises(server_link.LinkError) as e:
            server_link.put_history(mac, dict({"sessions": []}, **body))
        assert e.value.status == 422, body
    server_link.put_history(mac, {"sessions": [_row("a")]})                      # no machine said: none kept
    assert server_link.history(mac["user_id"])["sessions"][0]["machine"] is None


def test_the_timeline_route_reads_one_session_s_notes(mac, client, owner):
    server_link.put_history(mac, {"sessions": [_row("a", registered=True)]})
    _note(mac, "for a", "a")
    _note(mac, "its own")
    r = client.get(f"/api/ade/link/timeline/{mac['id']}?session=a", headers=owner)
    assert r.status_code == 200 and [e["text"] for e in r.json()["events"]] == ["for a"]
    r = client.get(f"/api/ade/link/timeline/{mac['id']}", headers=owner)
    assert "for a" not in [e["text"] for e in r.json()["events"]]


# ---- the machine's side: a session's start recorded, and sent as registered while it counts ---------------------------

class Dir:
    """A link's directory as the client reads it, without a server."""

    def __init__(self, path, paired=True):
        self.dir = str(path)
        os.makedirs(self.dir, exist_ok=True)
        if paired:
            with open(os.path.join(self.dir, "config.json"), "w") as fh:
                json.dump({"url": "https://ade.example", "name": os.path.basename(self.dir)}, fh)

    def rule(self, data):
        with open(os.path.join(self.dir, "history.json"), "w") as fh:
            json.dump(data, fh)
        return ade_link._history_rule(self)

    def json_file(self, name, default=None):
        try:
            with open(os.path.join(self.dir, name)) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return default


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".claude" / "projects").mkdir(parents=True)
    (h / ".claude" / "sessions").mkdir()
    (h / "work").mkdir()
    (h / "acme-plans").mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return h


@pytest.fixture
def started(monkeypatch):
    """The History runs a hook starts, not started: each one's arguments from the command on."""
    got = []
    monkeypatch.setattr(ade_link, "_start_history", lambda link, extra=(): got.append(["history", "--quiet", *extra]))
    return got


def _runs_this_session(home, sid, pid=None):
    """Claude Code's own record that this test's process runs session `sid`; the hook's parent is that process."""
    pid = pid or os.getpid()
    (home / ".claude" / "sessions" / f"{pid}.json").write_text(json.dumps({"pid": pid, "sessionId": sid}))


def _start(monkeypatch, capsys, d, sid, cwd, source="startup"):
    import io
    import sys
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": sid, "cwd": str(cwd), "source": source,
                                                              "hook_event_name": "SessionStart"})))
    return run(capsys, "--dir", d.dir, "session", "start")


def _records(d):
    folder = os.path.join(d.dir, ade_link.STARTS)
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


def test_a_start_is_recorded_where_history_json_lists_its_folder_printing_nothing(tmp_path, home, monkeypatch, capsys,
                                                                                  started):
    monkeypatch.setattr(os, "getppid", os.getpid)
    d = Dir(tmp_path / "links" / "mac")
    _runs_this_session(home, "s1")
    assert _start(monkeypatch, capsys, d, "s1", home / "work") == (0, "", "")
    assert _records(d) == [] and started == []                  # no history.json: nothing recorded, nothing run
    d.rule({"projects": "all", "except": ["~/acme-plans"]})
    assert _start(monkeypatch, capsys, d, "s1", home / "work") == (0, "", "")
    (name,) = _records(d)
    record = json.loads(open(os.path.join(d.dir, ade_link.STARTS, name)).read())
    assert name == f"s1.{os.getpid()}.json" and record["session"] == "s1" and record["pid"] == os.getpid()
    assert record["start"] == ade_link._process_start(os.getpid()) and abs(record["at"] - time.time()) < 5
    assert [a[-2:] for a in started] == [["--after", str(record["at"])], ["history", "--quiet"]]
    started.clear()
    # a compaction is the start it was: the record stands as it was, and no run is started for it again
    assert _start(monkeypatch, capsys, d, "s1", home / "work", source="compact") == (0, "", "")
    assert json.loads(open(os.path.join(d.dir, ade_link.STARTS, name)).read()) == record and started == []
    _runs_this_session(home, "s2")
    assert _start(monkeypatch, capsys, d, "s2", home / "acme-plans") == (0, "", "")    # an excepted folder
    assert _records(d) == [f"s1.{os.getpid()}.json"]
    assert _start(monkeypatch, capsys, d, "s2", home / "work") == (0, "", "")       # its record, beside s1's
    assert _records(d) == [f"s1.{os.getpid()}.json", f"s2.{os.getpid()}.json"]
    before = sorted(os.listdir(d.dir))
    for bad in ("a b", "", "../x", 5):                                               # not a session's id
        assert _start(monkeypatch, capsys, d, bad, home / "work") == (0, "", "")
        assert len(_records(d)) == 2 and sorted(os.listdir(d.dir)) == before, bad


def test_one_link_per_machine_records_starts_and_another_takes_over_once_it_is_gone(tmp_path, home, monkeypatch,
                                                                                    capsys, started):
    monkeypatch.setattr(os, "getppid", os.getpid)
    mac, other = Dir(tmp_path / "links" / "mac"), Dir(tmp_path / "links" / "mac2")
    for d in (mac, other):
        d.rule({"projects": "all"})
    _runs_this_session(home, "s1")
    _start(monkeypatch, capsys, mac, "s1", home / "work")
    assert (tmp_path / "links" / ade_link.REGISTRAR).read_text() == "mac"
    _start(monkeypatch, capsys, other, "s1", home / "work")
    assert len(_records(mac)) == 1 and _records(other) == []                       # one machine, one parent
    os.unlink(os.path.join(mac.dir, "config.json"))                                 # that link is gone from here
    _start(monkeypatch, capsys, other, "s1", home / "work")
    assert (tmp_path / "links" / ade_link.REGISTRAR).read_text() == "mac2" and len(_records(other)) == 1


def test_a_start_with_no_claude_process_found_is_not_recorded(tmp_path, home, monkeypatch, capsys, started):
    d = Dir(tmp_path / "links" / "mac")
    d.rule({"projects": "all"})
    monkeypatch.setattr(os, "getppid", lambda: 2 ** 22 + 12345)                     # no record of it, nor above it
    assert _start(monkeypatch, capsys, d, "s1", home / "work") == (0, "", "")
    assert _records(d) == []
    monkeypatch.setattr(os, "getppid", os.getpid)
    _runs_this_session(home, "s1")
    monkeypatch.setattr(ade_link, "_process_start", lambda pid: None)               # its start not known
    assert _start(monkeypatch, capsys, d, "s1", home / "work") == (0, "", "")
    assert _records(d) == []


def test_a_record_counts_while_its_process_runs_that_session_and_goes_once_it_no_longer_can(tmp_path, home,
                                                                                            monkeypatch):
    d = Dir(tmp_path / "links" / "mac")
    (tmp_path / "links" / ade_link.REGISTRAR).write_text("mac")
    folder = os.path.join(d.dir, ade_link.STARTS)
    os.makedirs(folder)
    me, start, now = os.getpid(), ade_link._process_start(os.getpid()), int(time.time())
    _runs_this_session(home, "s1")
    records = {"ok": ("s1", me, start, now), "cleared": ("s0", me, start, now - 100),     # the process runs s1 now
               "settling": ("s9", me, start, now), "reused": ("s1", me, "Thu Jan  1 00:00:00 2026", now - 100),
               "gone": ("s1", 2 ** 22 + 12345, start, now - 100)}
    _runs_this_session(home, "s1", pid=2 ** 22 + 12345)            # Claude Code's record, left behind a crash
    for name, (sid, pid, st, at) in records.items():
        with open(os.path.join(folder, f"{name}.json"), "w") as fh:
            json.dump({"session": sid, "pid": pid, "start": st, "at": at}, fh)
    with open(os.path.join(folder, "bad.json"), "w") as fh:
        fh.write("{")
    assert ade_link._registered(d, now) == {"s1"}
    assert sorted(os.listdir(folder)) == ["ok.json", "settling.json"]   # a fresh one may yet count: Claude Code is late
    assert ade_link._registered(d, now + ade_link.START_SETTLE + 1) == {"s1"}
    assert sorted(os.listdir(folder)) == ["ok.json"]
    other = Dir(tmp_path / "links" / "mac2")
    (tmp_path / "links" / ade_link.REGISTRAR).write_text("mac2")
    assert ade_link._registered(d, now) == set() and os.listdir(folder) == []    # not the registrar: none count
    assert ade_link._registered(other, now) == set()


def test_the_machine_s_id_is_made_once_and_one_that_is_not_one_is_left_as_it_is(tmp_path):
    d = Dir(tmp_path / "links" / "mac")
    first = ade_link._machine_id(d)
    assert len(first) == 32 and ade_link._machine_id(d) == first
    assert ade_link._machine_id(Dir(tmp_path / "links" / "mac2")) == first        # the machine's, not the link's
    (tmp_path / "links" / ade_link.MACHINE_ID).write_text("x y")
    assert ade_link._machine_id(d) is None and (tmp_path / "links" / ade_link.MACHINE_ID).read_text() == "x y"


def test_the_everywhere_block_runs_session_start_which_prints_nothing_without_a_paired_link(tmp_path, monkeypatch,
                                                                                            capsys, started):
    here = os.path.join(os.path.dirname(__file__), "..")
    with open(os.path.join(here, "ade_link.everywhere.settings.json"), encoding="utf-8") as fh:
        hooks = json.load(fh)["hooks"]
    (start,) = hooks["SessionStart"]
    assert set(start["matcher"].split("|")) == {"startup", "resume", "clear", "fork", "compact"}
    (hook,) = start["hooks"]
    # the owner's client, written out from the home folder (test_ade_link_client holds every line of the block to it)
    assert hook["command"].endswith(" session start") \
        and ' "/absolute/path/to/home/.proofpane-ade/ade_link.py" ' in hook["command"]
    (tmp_path / "unpaired").mkdir()
    for d in (tmp_path / "missing", tmp_path / "unpaired"):
        assert run(capsys, "--dir", d, "session", "start") == (0, "", "")
    assert started == [] and os.listdir(tmp_path / "unpaired") == []


# ---- through the app: a History run registers, a Stop's notes name the session ------------------------------------------

def _entry(kind, uid, text, at, cwd, sid):
    if kind == "owner":
        return {"type": "user", "uuid": uid, "origin": {"kind": "human"}, "turnOrigin": "human", "cwd": cwd,
                "timestamp": at, "sessionId": sid, "entrypoint": "claude-vscode",
                "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
    return {"type": "assistant", "uuid": uid, "timestamp": at, "cwd": cwd, "sessionId": sid,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def _transcript(home, sid, entries, folder="-work"):
    path = home / ".claude" / "projects" / folder / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")
    return str(path)


def _stop(monkeypatch, capsys, d, path, sid, cwd):
    import io
    import sys
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"transcript_path": path, "session_id": sid,
                                                              "cwd": str(cwd)})))
    return run(capsys, "--dir", d, "mirror", "reply", "--listed-only")


def _session_notes(client, owner, link_id, sid):
    r = client.get(f"/api/ade/link/timeline/{link_id}?session={sid}", headers=owner)
    assert r.status_code == 200
    return [(e["mirror"], e["text"], e.get("said_at")) for e in sorted(r.json()["events"], key=lambda e: e["seq"])]


def _own_notes(client, owner, link_id):
    r = client.get(f"/api/ade/link/timeline/{link_id}", headers=owner)
    return [e["text"] for e in sorted(r.json()["events"], key=lambda e: e["seq"]) if e.get("mirror")]


def test_a_registered_session_s_turns_go_under_it_and_one_still_to_register_waits_for_its_run(
        client, owner, served, capsys, tmp_path, home, monkeypatch, started):
    monkeypatch.setattr(os, "getppid", os.getpid)
    d = pair(client, owner, served, capsys, str(tmp_path / "links" / "mac"), "mac")
    link_id = link_row("mac")["id"]
    Dir(d, paired=False).rule({"projects": "all"})
    work = str(home / "work")
    _runs_this_session(home, "s1")
    assert _start(monkeypatch, capsys, Dir(d, paired=False), "s1", work) == (0, "", "")
    path = _transcript(home, "s1", [_entry("owner", "u1", "first words", "2026-10-04T01:00:00.250Z", work, "s1"),
                                    _entry("session", "a1", "first reply", "2026-10-04T01:00:02Z", work, "s1")])
    # its start was recorded after the last list (there is none): the turn waits for the run that registers it
    assert _stop(monkeypatch, capsys, d, path, "s1", work) == (0, "", "")
    assert _session_notes(client, owner, link_id, "s1") == [] and _own_notes(client, owner, link_id) == []
    time.sleep(1.1)                                     # the run begins after the second the start was recorded in
    assert run(capsys, "--dir", d, "history")[0] == 0
    put = json.loads(open(os.path.join(d, ade_link.HISTORY_PUT)).read())
    assert put["registered"] == ["s1"] and put["noted"] == []
    (row,) = server_link.history(link_row("mac")["user_id"])["sessions"]
    made = (tmp_path / "links" / ade_link.MACHINE_ID).read_text()
    import hashlib
    sent = hashlib.sha256(f"{made}\n{served}".encode()).hexdigest()[:32]          # joined to the server's address
    assert row["registered"] is True and row["machine"] == sent and made not in json.dumps(row)
    _transcript(home, "s1", [_entry("owner", "u2", "second words", "2026-10-04T01:01:00Z", work, "s1"),
                             _entry("session", "a2", "second reply", "2026-10-04T01:01:01Z", work, "s1"),
                             _entry("session", "a3", "and more", "2026-10-04T01:01:30Z", work, "s1")])
    assert _stop(monkeypatch, capsys, d, path, "s1", work) == (0, "", "")
    assert _session_notes(client, owner, link_id, "s1") == [
        ("owner", "first words", 1_791_075_600_250), ("session", "first reply", 1_791_075_602_000),
        ("owner", "second words", 1_791_075_660_000), ("session", "second reply\n\nand more", 1_791_075_661_000)]
    # a time ahead of this machine's clock is not sent (the ADE would refuse the note): the words go without it
    _transcript(home, "s1", [_entry("owner", "u3", "from the future", "2099-01-01T00:00:00Z", work, "s1")])
    assert _stop(monkeypatch, capsys, d, path, "s1", work) == (0, "", "")
    assert _session_notes(client, owner, link_id, "s1")[-1] == ("owner", "from the future", None)
    assert _own_notes(client, owner, link_id) == []     # none of it in the link's own conversation


def test_a_session_never_registered_posts_as_before_and_a_fork_never_posts_its_source_s_words(
        client, owner, served, capsys, tmp_path, home, monkeypatch, started):
    monkeypatch.setattr(os, "getppid", os.getpid)
    d = pair(client, owner, served, capsys, str(tmp_path / "links" / "mac"), "mac")
    link_id = link_row("mac")["id"]
    Dir(d, paired=False).rule({"projects": "all"})
    work = str(home / "work")
    # a session that started before this client, with no start record: its notes are the link's own, as before
    path = _transcript(home, "old", [_entry("owner", "o1", "old words", "2026-10-04T01:00:00Z", work, "old"),
                                     _entry("session", "o2", "old reply", "2026-10-04T01:00:01Z", work, "old")])
    assert run(capsys, "--dir", d, "history")[0] == 0
    assert _stop(monkeypatch, capsys, d, path, "old", work) == (0, "", "")
    assert _own_notes(client, owner, link_id) == ["old words", "old reply"]
    # a fork of it: the entries copied from its source keep their session's id, and only its own words go
    _runs_this_session(home, "fork")
    _start(monkeypatch, capsys, Dir(d, paired=False), "fork", work, source="fork")
    fork = _transcript(home, "fork", [_entry("owner", "o1", "old words", "2026-10-04T01:00:00Z", work, "old"),
                                      _entry("session", "o2", "old reply", "2026-10-04T01:00:01Z", work, "old"),
                                      _entry("owner", "f1", "fork words", "2026-10-04T01:02:00Z", work, "fork"),
                                      _entry("session", "f2", "fork reply", "2026-10-04T01:02:01Z", work, "fork")])
    assert _stop(monkeypatch, capsys, d, fork, "fork", work) == (0, "", "")      # waits for its registration
    time.sleep(1.1)
    assert run(capsys, "--dir", d, "history")[0] == 0
    _transcript(home, "fork", [_entry("owner", "f3", "fork again", "2026-10-04T01:03:00Z", work, "fork")])
    assert _stop(monkeypatch, capsys, d, fork, "fork", work) == (0, "", "")
    assert [t for _, t, _ in _session_notes(client, owner, link_id, "fork")] == ["fork words", "fork reply",
                                                                                 "fork again"]
    assert _own_notes(client, owner, link_id) == ["old words", "old reply"]


def test_a_session_past_the_newest_keeps_its_notes_and_loses_them_once_withheld_named_by_id(
        client, owner, served, capsys, tmp_path, home, monkeypatch, started):
    monkeypatch.setattr(os, "getppid", os.getpid)
    d = pair(client, owner, served, capsys, str(tmp_path / "links" / "mac"), "mac")
    mac = link_row("mac")
    Dir(d, paired=False).rule({"projects": "all", "except": ["~/acme-plans"]})
    work = str(home / "work")
    _runs_this_session(home, "s1")
    _start(monkeypatch, capsys, Dir(d, paired=False), "s1", work)
    _transcript(home, "s1", [_entry("owner", "u1", "words", "2026-10-04T01:00:00Z", work, "s1")])
    time.sleep(1.1)
    assert run(capsys, "--dir", d, "history")[0] == 0
    server_link.post_event(mac, {"client_id": "n1", "kind": "note", "to": "owner", "mirror": "owner", "text": "kept",
                                 "session": "s1"})
    assert run(capsys, "--dir", d, "history")[0] == 0
    assert json.loads(open(os.path.join(d, ade_link.HISTORY_PUT)).read())["noted"] == ["s1"]
    _transcript(home, "s2", [_entry("owner", "v1", "newer", "2026-10-04T02:00:00Z", work, "s2")])
    monkeypatch.setattr(ade_link, "HISTORY_MAX", 1)                       # s1 is past the newest now
    assert run(capsys, "--dir", d, "history")[0] == 0
    assert [s["id"] for s in server_link.history(mac["user_id"])["sessions"]] == ["s2"]
    assert _tagged(mac["id"]) == [("s1", "kept")]                         # left out, not withheld: its notes stay
    # it touched an excepted project, still past the newest: no longer named, its notes go
    _transcript(home, "s1", [_entry("owner", "u2", "now in ~/acme-plans/notes", "2026-10-04T01:30:00Z", work, "s1")])
    assert run(capsys, "--dir", d, "history")[0] == 0
    assert [s["id"] for s in server_link.history(mac["user_id"])["sessions"]] == ["s2"]
    assert _tagged(mac["id"]) == []


def _tool_entry(uid, at, cwd, sid, **tool_input):
    return {"type": "assistant", "uuid": uid, "timestamp": at, "cwd": cwd, "sessionId": sid,
            "message": {"role": "assistant", "content": [{"type": "tool_use", "id": uid, "name": "Edit",
                                                          "input": tool_input}]}}


def test_a_row_names_the_repositories_its_session_worked_in_that_history_json_lists(
        client, owner, served, capsys, tmp_path, home, monkeypatch, started):
    for repo in ("work/app", "work/lib", "other/elsewhere"):
        (home / repo / ".git").mkdir(parents=True)
    # a worktree of app: its .git a file naming its git directory, which names the repository's own
    (home / "work" / "app" / ".git" / "worktrees" / "wt").mkdir(parents=True)
    (home / "work" / "app" / ".git" / "worktrees" / "wt" / "commondir").write_text("../..\n")
    (home / "work" / "app-wt").mkdir()
    (home / "work" / "app-wt" / ".git").write_text(f"gitdir: {home}/work/app/.git/worktrees/wt\n")
    d = pair(client, owner, served, capsys, str(tmp_path / "links" / "mac"), "mac")
    Dir(d, paired=False).rule({"projects": ["~/work"]})
    wt = str(home / "work" / "app-wt")
    _transcript(home, "s1", [
        _entry("owner", "u1", "go", "2026-10-04T01:00:00Z", wt, "s1"),
        _tool_entry("t1", "2026-10-04T01:00:01Z", wt, "s1", file_path=str(home / "work" / "lib" / "x.py")),
        _tool_entry("t2", "2026-10-04T01:00:02Z", wt, "s1", file_path=str(home / "other" / "elsewhere" / "y")),
        _tool_entry("t3", "2026-10-04T01:00:03Z", wt, "s1", file_path="README.md")])       # its own, again
    assert run(capsys, "--dir", d, "history")[0] == 0
    (row,) = server_link.history(link_row("mac")["user_id"])["sessions"]
    # first where it began, a worktree shown as the repository its worktrees share; not one outside ~/work
    assert row["repositories"] == ["~/work/app", "~/work/lib"]
    Dir(d, paired=False).rule({"projects": ["~/work/app-wt"]})                    # narrowed: what it lists alone
    assert run(capsys, "--dir", d, "history")[0] == 0
    (row,) = server_link.history(link_row("mac")["user_id"])["sessions"]
    assert row["repositories"] == ["~/work/app-wt"]


def test_a_retried_run_says_what_it_did_not_what_the_one_before_did(tmp_path, home):
    """`history` retries a run the ADE did not answer with the same report: one that then withdraws the list (its
    history.json gone meanwhile) does not say the first's count of sessions listed."""
    d = Dir(tmp_path / "links" / "mac")
    report = {"listed": 5, "withheld": 1, "talks": 2}               # what the attempt before had said
    ade_link._history_run(ade_link.Link(d.dir), report)
    assert "listed" not in report and "talks" not in report and report["holding"] is False


def test_a_conversation_s_messages_keep_when_each_began_in_milliseconds(mac):
    now = int(time.time())
    server_link.put_history(mac, {"sessions": [_row("a")], "conversations": True})
    for bad in (True, "1", 0, (now + 400) * 1000):
        with pytest.raises(server_link.LinkError) as e:
            server_link.put_talk(mac, {"session": "a", "messages": [{"who": "owner", "text": "x", "at": now, "ms": bad}]})
        assert e.value.status == 422, bad
    server_link.put_talk(mac, {"session": "a", "messages": [{"who": "owner", "text": "x", "at": now, "ms": now * 1000 + 5},
                                                            {"who": "session", "text": "y", "at": now}]})
    assert server_link.talk(mac["user_id"], mac["id"], "a")["messages"] == [
        {"who": "owner", "text": "x", "at": now, "ms": now * 1000 + 5}, {"who": "session", "text": "y", "at": now}]


def test_a_conversation_read_for_history_gives_each_message_its_first_entry_s_time(tmp_path):
    path = tmp_path / "t.jsonl"
    with open(path, "w") as fh:
        for e in (_entry("owner", "u1", "go", "2026-10-04T01:00:00.250Z", "/w", "s1"),
                  _entry("session", "a1", "one", "2026-10-04T01:00:02Z", "/w", "s1"),
                  _entry("session", "a2", "two", "2026-10-04T01:00:09Z", "/w", "s1")):
            fh.write(json.dumps(e) + "\n")
    st = os.stat(path)
    got = ade_link._talk(str(path), st.st_size, st.st_ino)["messages"]
    assert got == [{"who": "owner", "text": "go", "at": 1_791_075_600, "ms": 1_791_075_600_250},
                   {"who": "session", "text": "one\n\ntwo", "at": 1_791_075_609, "ms": 1_791_075_602_000}]


def test_the_run_a_start_schedules_runs_only_where_no_run_since_has_sent_a_list(tmp_path, home, monkeypatch, capsys):
    d = Dir(tmp_path / "links" / "mac")
    monkeypatch.setattr(ade_link, "REGISTER_DELAY", 0)
    ran = []
    monkeypatch.setattr(ade_link, "_history_run", lambda link, report=None: ran.append(1) or {})
    with open(os.path.join(d.dir, ade_link.HISTORY_PUT), "w") as fh:
        json.dump({"at": 1000, "registered": [], "noted": []}, fh)
    settled = 1000 - ade_link.START_SETTLE
    assert run(capsys, "--dir", d.dir, "history", "--quiet", "--after", str(settled - 1)) == (0, "", "")
    assert ran == []                                                 # a run that began once the start had settled
    assert run(capsys, "--dir", d.dir, "history", "--quiet", "--after", str(settled)) == (0, "", "")
    assert ran == [1]                    # one before it had: Claude Code names a session resumed in a process late


def test_a_turn_before_the_list_that_registers_its_session_goes_in_the_link_s_own_conversation(
        client, owner, served, capsys, tmp_path, home, monkeypatch, started):
    """A turn is never held for a registration still to come: a session that ended first (print mode, a resume for one
    question) would never post it."""
    monkeypatch.setattr(os, "getppid", os.getpid)
    d = pair(client, owner, served, capsys, str(tmp_path / "links" / "mac"), "mac")
    link_id = link_row("mac")["id"]
    Dir(d, paired=False).rule({"projects": "all"})
    work = str(home / "work")
    path = _transcript(home, "s1", [_entry("owner", "u1", "before", "2026-10-04T01:00:00Z", work, "s1")])
    assert run(capsys, "--dir", d, "history")[0] == 0                 # read whole, not registered: no start recorded
    assert _stop(monkeypatch, capsys, d, path, "s1", work) == (0, "", "")
    _runs_this_session(home, "s1")
    _start(monkeypatch, capsys, Dir(d, paired=False), "s1", work, source="resume")
    _transcript(home, "s1", [_entry("owner", "u2", "after its start", "2026-10-04T01:05:00Z", work, "s1")])
    assert _stop(monkeypatch, capsys, d, path, "s1", work) == (0, "", "")
    assert _own_notes(client, owner, link_id) == ["before", "after its start"]
    time.sleep(1.1)
    assert run(capsys, "--dir", d, "history")[0] == 0
    _transcript(home, "s1", [_entry("owner", "u3", "registered", "2026-10-04T01:06:00Z", work, "s1")])
    assert _stop(monkeypatch, capsys, d, path, "s1", work) == (0, "", "")
    assert [t for _, t, _ in _session_notes(client, owner, link_id, "s1")] == ["registered"]
    assert _own_notes(client, owner, link_id) == ["before", "after its start"]


def test_a_session_its_run_did_not_register_posts_as_the_link_s_own_and_does_not_wait_for_good(
        client, owner, served, capsys, tmp_path, home, monkeypatch, started):
    monkeypatch.setattr(os, "getppid", os.getpid)
    d = pair(client, owner, served, capsys, str(tmp_path / "links" / "mac"), "mac")
    link_id = link_row("mac")["id"]
    Dir(d, paired=False).rule({"projects": "all"})
    work = str(home / "work")
    _runs_this_session(home, "s1")
    _start(monkeypatch, capsys, Dir(d, paired=False), "s1", work)
    _runs_this_session(home, "other")                                # its process runs another session since
    path = _transcript(home, "s1", [_entry("owner", "u1", "words", "2026-10-04T01:00:00Z", work, "s1")])
    time.sleep(1.1)
    assert run(capsys, "--dir", d, "history")[0] == 0
    assert json.loads(open(os.path.join(d, ade_link.HISTORY_PUT)).read())["registered"] == []
    assert _stop(monkeypatch, capsys, d, path, "s1", work) == (0, "", "")
    assert _own_notes(client, owner, link_id) == ["words"]


def test_a_repository_s_folder_under_a_home_reached_through_a_link_is_shortened(tmp_path, monkeypatch):
    real = tmp_path / "real-home"
    (real / "work" / "app" / ".git").mkdir(parents=True)
    os.symlink(real, tmp_path / "home")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    rule, _ = Dir(tmp_path / "links" / "mac").rule({"projects": "all"})
    top = rule.top(str(tmp_path / "home" / "work" / "app" / "x.py"))
    assert top[0] == os.path.realpath(real / "work" / "app")             # the folders are real paths
    row = ade_link._row("s1", {"cwd": str(tmp_path / "home" / "work" / "app"), "tops": [top, ["bad"]]}, {},
                        2_000_000_000, rule=rule)
    assert row["repositories"] == ["~/work/app"] and row["project"] == "~/work/app"


def test_history_names_the_link_that_registers_the_machine_where_it_is_another(client, owner, served, capsys, tmp_path,
                                                                                home):
    d = pair(client, owner, served, capsys, str(tmp_path / "links" / "mac"), "mac")
    Dir(d, paired=False).rule({"projects": "all"})
    _transcript(home, "s1", [_entry("owner", "u1", "words", "2026-10-04T01:00:00Z", str(home / "work"), "s1")])
    code, out, _ = run(capsys, "--dir", d, "history")
    assert code == 0 and "registered by its link" not in out
    (tmp_path / "links" / ade_link.REGISTRAR).write_text("old-mac")
    code, out, _ = run(capsys, "--dir", d, "history")
    assert code == 0 and "this machine's sessions are registered by its link old-mac" in out and ".registrar" in out


def test_a_withheld_session_is_never_sent_registered(tmp_path, home):
    rule, _ = Dir(tmp_path / "links" / "mac").rule({"projects": "all"})
    state = {"cwd": str(home / "work"), "last": 1_790_000_000}
    assert ade_link._row("s1", state, {}, 2_000_000_000, {"s1"}, rule)["registered"] is True
    assert ade_link._row("s1", dict(state, touched=True), {}, 2_000_000_000, {"s1"}, rule)["registered"] is False


def test_a_start_finds_the_claude_process_above_the_shell_that_runs_its_hook(tmp_path, home, monkeypatch):
    """As Claude Code runs a hook: through `sh -c`, so the hook's parent is a shell and its grandparent the process
    that runs the session; nothing in the test stands in for the climb."""
    import subprocess
    import sys
    d = Dir(tmp_path / "links" / "mac")
    d.rule({"projects": "all"})
    _runs_this_session(home, "s1")                                    # this test's process runs the session
    hook = json.dumps({"session_id": "s1", "cwd": str(home / "work"), "source": "startup"})
    client = os.path.join(os.path.dirname(__file__), "..", "ade_link.py")
    env = dict(os.environ, HOME=str(home), OPEN_EDITION_TOKEN_SECRET="test-only-not-a-secret-0123456789abcdef")
    try:
        p = subprocess.run(["/bin/sh", "-c", f"'{sys.executable}' '{client}' --dir '{d.dir}' session start; true"],
                           input=hook, capture_output=True, text=True, env=env, timeout=60)
    finally:
        # the start runs History in processes of its own, which outlive it (the one that registers the session waits
        # a minute first): this test started them, so it ends them
        import signal
        from conftest import processes_naming
        for pid, _ in processes_naming(d.dir):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    assert (p.returncode, p.stdout) == (0, "")
    (name,) = _records(d)
    record = json.loads(open(os.path.join(d.dir, ade_link.STARTS, name)).read())
    assert record["pid"] == os.getpid() and record["session"] == "s1"


def test_without_history_json_a_note_names_its_session_and_the_server_keeps_it_untagged(client, owner, served, capsys,
                                                                                         tmp_path, home, monkeypatch,
                                                                                         started):
    """A project's own mirror, on a link that never read a history.json, posts every turn as before, each note naming
    its session as every mirrored note does: no list registered it, so the server keeps the session beside the note,
    never shown, in the link's own conversation, and lets the note go once a list says that session is withheld."""
    import io
    import sys
    d = pair(client, owner, served, capsys, str(tmp_path / "links" / "mac"), "mac")
    work = str(home / "work")
    path = _transcript(home, "s9", [_entry("owner", "u1", "plain words", "2026-10-04T01:00:00Z", work, "s9")])
    sent, real = [], ade_link.Link.request
    monkeypatch.setattr(ade_link.Link, "request", lambda self, method, where, body=None, **k: (
        sent.append(body) if where == "/events" else None) or real(self, method, where, body, **k))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"transcript_path": path, "session_id": "s9", "cwd": work})))
    assert run(capsys, "--dir", d, "mirror", "reply") == (0, "", "")
    assert [(b["text"], b.get("session")) for b in sent] == [("plain words", "s9")]
    with store.connect() as db:
        (row,) = db.execute("SELECT session_id, untagged_session FROM ade_link_events WHERE text = ?",
                            ("plain words",)).fetchall()
    assert (row[0], row[1]) == (None, "s9")
