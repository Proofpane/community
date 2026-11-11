"""What the slash menu reads: each linked session's client adds to its heartbeat, as optional
fields, what it can read of its own Claude Code session (its context used of its window, its task list, its background
tasks and agents with their state), bounded in size, scanned for secrets as a checkpoint is, and sealed where
encryption is on; the owner's view of the links holds the last report and when it came, for the slash menu to read."""

import json
import os
import sqlite3
import threading
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

import ade_link
from conftest import signed_up
from proofpane_open import store
from proofpane_open.ade import e2e
from proofpane_open.ade import link as server
from test_ade_e2e import Keys, device, keys, sealed_for  # noqa: F401  (fixtures)
from test_ade_e2e_envelope import open_envelope, raw_public
from test_ade_link import paired, row
from test_ade_link_client import a_link_to, clock, run, scripted  # noqa: F401  (clock, a fixture)


@pytest.fixture
def owner(client):
    return signed_up(client)


USAGE = {"context_used": 143500, "context_window": 1000000, "model": "claude-opus-5-5"}
TASKS = [{"text": "the README", "state": "completed"}, {"text": "the cases", "state": "in_progress"}]
BACKGROUND = [{"id": "b0opa3o22", "kind": "shell", "text": "Watch the message file", "state": "running"},
              {"id": "afd4535bd", "kind": "agent", "text": "Read diff", "state": "completed"}]


def beat(s, reported=None):
    return s.call("POST", "/heartbeat", {} if reported is None else {"reported": reported})


def again(s):
    """The link's last heartbeat moved two minutes back, so that the next one is taken (one a minute)."""
    with store.connect() as db:
        db.execute("UPDATE ade_links SET last_heartbeat_at = last_heartbeat_at - 120 WHERE id = ?", (s.link_id,))


def seen(client, owner):
    r = client.get("/api/ade/link/sessions", headers=owner)
    assert r.status_code == 200, r.text
    return {lk["name"]: lk for lk in r.json()["links"]}


# the bounds, as the server names them (ade/link.py): REPORT_TASKS, REPORT_BACKGROUND, REPORT_TEXT, REPORT_TOKENS and
# REPORT_PLAIN, the words' JSON in bytes, in the clear or sealed
TASKS_MOST, BACKGROUND_MOST, TEXT_MOST = 30, 12, 160
TOKENS_MOST, PLAIN_MOST = 10_000_000, 6000


def plain_size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def link_e2e(link_id):
    """What the account's encryption view reads of a link: the key it last sealed to, and when it was last told that
    encryption is required."""
    with store.connect() as db:
        found = db.execute("SELECT key_id, last_required_at FROM ade_link_e2e WHERE link_id = ?", (link_id,)).fetchone()
    return dict(found) if found else {}


# ---- the server: what a heartbeat's report may hold, and what the owner reads of it ----------------------------------

def test_a_heartbeat_s_report_is_kept_and_the_owner_reads_it_with_when(client, owner):
    mac = paired(client, owner)
    reported = {"usage": dict(USAGE, rate_limit={"state": "limited", "resets_at": 1762650000}), "tasks": TASKS,
                "background": BACKGROUND}
    r = beat(mac, reported)
    assert r.status_code == 200, r.text
    got = seen(client, owner)["mac"]
    assert got["reported"] == reported
    assert got["reported_at"] == row(mac.link_id)["last_heartbeat_at"] is not None


def test_a_heartbeat_with_no_report_leaves_the_last_one_and_one_that_never_reported_has_none(client, owner):
    mac = paired(client, owner)
    cloud = paired(client, owner, "cloud", "cloud")
    assert beat(cloud).status_code == 200
    assert beat(mac, {"usage": USAGE}).status_code == 200
    first = seen(client, owner)["mac"]["reported_at"]
    again(mac)
    assert beat(mac).status_code == 200
    got = seen(client, owner)
    assert (got["mac"]["reported"], got["mac"]["reported_at"]) == ({"usage": USAGE}, first)
    assert (got["cloud"]["reported"], got["cloud"]["reported_at"]) == (None, None)


def test_the_report_s_bounds_hold_at_their_edge(client, owner):
    mac = paired(client, owner)

    def taken(reported):
        again(mac)
        return beat(mac, reported).status_code
    task = {"text": "t", "state": "pending"}
    shell = {"id": "b1", "kind": "shell", "text": "w", "state": "running"}
    assert taken({"tasks": [task] * TASKS_MOST}) == 200
    assert taken({"tasks": [task] * (TASKS_MOST + 1)}) == 422
    assert taken({"background": [shell] * BACKGROUND_MOST}) == 200
    assert taken({"background": [shell] * (BACKGROUND_MOST + 1)}) == 422
    assert taken({"tasks": [dict(task, text="x" * TEXT_MOST)]}) == 200
    assert taken({"tasks": [dict(task, text="x" * (TEXT_MOST + 1))]}) == 422
    assert taken({"usage": {"context_used": TOKENS_MOST}}) == 200
    assert taken({"usage": {"context_used": TOKENS_MOST + 1}}) == 422
    # the words, in the clear or sealed, at most PLAIN_MOST bytes as JSON: one byte past it is refused, each text
    # within its own bound
    words = {"tasks": [dict(task, text="x" * TEXT_MOST) for _ in range(TASKS_MOST)], "background": []}
    while plain_size(words) <= PLAIN_MOST:
        words["background"].append(dict(shell, id=f"b{len(words['background'])}", text="y" * TEXT_MOST))
    last = words["background"][-1]
    last["text"] = last["text"][:TEXT_MOST - (plain_size(words) - PLAIN_MOST)]
    assert plain_size(words) == PLAIN_MOST and taken(words) == 200
    last["text"] += "y"
    assert taken(words) == 422


def test_an_older_heartbeat_that_ends_last_leaves_the_newer_one_s_report(client, owner):
    mac = paired(client, owner)
    held = row(mac.link_id)                 # as two of its heartbeats in flight at once both read it
    now = int(time.time())
    server.heartbeat(dict(held, request_ts=now), {"reported": {"usage": USAGE}}, now)
    server.heartbeat(dict(held, request_ts=now - 30), {"reported": {"usage": dict(USAGE, context_used=1)}}, now)
    got = seen(client, owner)["mac"]
    assert (got["reported"], got["reported_at"]) == ({"usage": USAGE}, now)


@pytest.mark.parametrize("reported", [
    [],
    {"usage": USAGE, "notes": "x"},
    {"usage": {"context_used": -1}},
    {"usage": {"context_used": True}},
    {"usage": {"context_used": 1.5}},
    {"usage": {"context_window": 0}},
    {"usage": {"model": ""}},
    {"usage": {"model": "m" * 81}},
    {"usage": {"rate_limit": {"state": "throttled"}}},
    {"usage": {"rate_limit": {"state": "limited", "resets_at": True}}},
    {"tasks": {"text": "t", "state": "pending"}},
    {"tasks": [{"text": "t", "state": "doing"}]},
    {"tasks": [{"text": "", "state": "pending"}]},
    {"tasks": [{"text": "t", "state": "pending", "owner": "x"}]},
    {"background": [{"id": "b 1", "kind": "shell", "text": "w", "state": "running"}]},
    {"background": [{"id": "b1", "kind": "cron", "text": "w", "state": "running"}]},
    {"background": [{"id": "b1", "kind": "shell", "text": "w", "state": "paused"}]},
    {"background": [{"id": "b1", "kind": "shell", "text": None, "state": "running"}]},
    {"sealed": "e2e1.notanenvelope"},
    {"usage": {"context_used": 1, "cost": 2}},
    {"tasks": [{"text": "t"}]},
])
def test_a_report_out_of_shape_is_refused_whole_and_moves_nothing(client, owner, reported):
    mac = paired(client, owner)
    assert beat(mac, reported).status_code == 422
    assert row(mac.link_id)["last_heartbeat_at"] is None
    assert seen(client, owner)["mac"]["reported"] is None


def test_a_heartbeat_is_presence_or_its_report_and_nothing_else(client, owner):
    mac = paired(client, owner)
    assert mac.call("POST", "/heartbeat", {"reported": {"usage": USAGE}, "notes": "x"}).status_code == 422
    assert row(mac.link_id)["last_heartbeat_at"] is None
    assert mac.call("POST", "/heartbeat", {"reported": {"usage": USAGE}}).status_code == 200      # the control


def test_a_sealed_report_holds_at_most_its_bound_inside(client, owner, keys):
    mac = paired(client, owner)
    keys.turn_on()
    slot = e2e.slot(keys.account, mac.link_id, "reported")
    words = {"tasks": [{"text": "x" * TEXT_MOST, "state": "pending"} for _ in range(TASKS_MOST)], "background": []}
    while len(e2e.plaintext(words)) <= PLAIN_MOST:
        words["background"].append({"id": f"b{len(words['background'])}", "kind": "shell", "text": "y" * TEXT_MOST,
                                    "state": "running"})
    last = words["background"][-1]
    last["text"] = last["text"][:TEXT_MOST - (len(e2e.plaintext(words)) - PLAIN_MOST)]
    assert len(e2e.plaintext(words)) == PLAIN_MOST
    assert beat(mac, {"sealed": sealed_for(keys, keys.key(), slot, words)}).status_code == 200
    again(mac)
    last["text"] += "y"
    assert beat(mac, {"sealed": sealed_for(keys, keys.key(), slot, words)}).status_code == 422


def test_a_report_holding_a_secret_s_shape_is_refused_as_a_checkpoint_is(client, owner):
    mac = paired(client, owner)
    r = beat(mac, {"tasks": [{"text": "push with ghp_0123456789abcdef", "state": "pending"}]})
    assert r.status_code == 422 and "shaped like" in r.json()["detail"]
    r = beat(mac, {"background": [{"id": "b1", "kind": "shell", "text": "export ghp_xyz", "state": "running"}]})
    assert r.status_code == 422
    r = beat(mac, {"background": [{"id": "ghp_0123456789", "kind": "shell", "text": "w", "state": "running"}]})
    assert r.status_code == 422                                     # an id is scanned too, as the whole report is
    assert row(mac.link_id)["last_heartbeat_at"] is None


def test_with_encryption_on_the_report_s_words_go_sealed_and_in_the_clear_are_refused(client, owner, keys):
    mac = paired(client, owner)
    keys.turn_on()
    for clear in ({"usage": USAGE, "tasks": TASKS}, {"background": BACKGROUND}):
        r = beat(mac, clear)
        assert r.status_code == 409 and r.json()["e2e"] == "required", r.text
    assert row(mac.link_id)["last_heartbeat_at"] is None
    assert link_e2e(mac.link_id).get("last_required_at") is not None    # told, as a refused event's link is
    assert beat(mac, {"usage": USAGE}).status_code == 200            # the numbers go in the clear
    again(mac)
    sealed = sealed_for(keys, keys.key(), e2e.slot(keys.account, mac.link_id, "reported"),
                        {"tasks": TASKS, "background": BACKGROUND})
    r = beat(mac, {"usage": USAGE, "sealed": sealed})
    assert r.status_code == 200, r.text
    assert seen(client, owner)["mac"]["reported"] == {"usage": USAGE, "sealed": sealed}
    assert link_e2e(mac.link_id).get("key_id") == e2e.key_id(raw_public(keys.key()))    # the key it last sealed to


def test_sealing_in_place_as_encryption_turns_on_drops_a_report_s_clear_lists_and_keeps_its_numbers(client, owner,
                                                                                                     keys, env):
    mac = paired(client, owner)
    cloud = paired(client, owner, "cloud", "cloud")
    lists = {"tasks": [{"text": "SEALME a task", "state": "pending"}],
             "background": [{"id": "b9", "kind": "shell", "text": "SEALME a shell", "state": "running"}]}
    assert beat(mac, dict(lists, usage=USAGE)).status_code == 200
    assert beat(cloud, {"usage": USAGE}).status_code == 200
    path = os.path.join(str(env), "open_edition.db")
    watcher = sqlite3.connect(path)          # open throughout, so the log is emptied by a checkpoint, not removed
    watcher.execute("SELECT COUNT(*) FROM ade_links").fetchall()
    got, _ = keys.turn_on()
    assert got["sealed"].get("reports") == 1                        # mac's lists; cloud's report held none
    views = seen(client, owner)
    assert (views["mac"]["reported"], views["cloud"]["reported"]) == ({"usage": USAGE}, {"usage": USAGE})
    assert os.path.getsize(path + "-wal") == 0
    watcher.close()
    with open(path, "rb") as fh:
        assert b"SEALME" not in fh.read()                           # its link sends them again, sealed


def test_a_sealed_report_is_to_a_key_the_account_took(client, owner, keys):
    mac = paired(client, owner)
    stranger = X25519PrivateKey.generate()
    sealed = e2e.seal(raw_public(stranger), e2e.slot(keys.account, mac.link_id, "reported"), {"tasks": TASKS})
    assert beat(mac, {"sealed": sealed}).status_code == 422          # encryption never on: no key it took
    keys.turn_on()
    assert beat(mac, {"sealed": sealed}).status_code == 422          # on, but to a key the account never stated
    assert row(mac.link_id)["last_heartbeat_at"] is None


# ---- the client: what it reads of its own Claude Code session ------------------------------------------------------

def _line(**entry):
    return json.dumps(entry)


def transcript(path, requested="claude-opus-5-5[1m]", extra=()):
    """A session's transcript as Claude Code writes one: its lines, oldest first."""
    lines = [
        _line(type="assistant", requestedModel=requested, message={
            "model": "claude-opus-5-5", "content": [
                {"type": "tool_use", "id": "t1", "name": "TodoWrite", "input": {"todos": [
                    {"content": "an old plan", "status": "pending", "activeForm": "Planning"}]}}],
            "usage": {"input_tokens": 3, "cache_creation_input_tokens": 7, "cache_read_input_tokens": 90,
                      "output_tokens": 5}}),
        _line(type="assistant", requestedModel=requested, message={
            "model": "claude-opus-5-5", "content": [
                {"type": "tool_use", "id": "t2", "name": "TodoWrite", "input": {"todos": [
                    {"content": "the README", "status": "completed", "activeForm": "Writing the README"},
                    {"content": "the cases", "status": "in_progress", "activeForm": "Writing the cases"}]}},
                {"type": "tool_use", "id": "t3", "name": "Bash", "input": {
                    "command": "sh watch.sh", "description": "Watch the message file", "run_in_background": True}},
                {"type": "tool_use", "id": "t4", "name": "Agent", "input": {
                    "description": "Read diff", "prompt": "read it", "run_in_background": True}}],
            "usage": {"input_tokens": 10, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 142490,
                      "output_tokens": 50}}),
        _line(type="user", message={"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t3", "content": "Command running in background with ID: b1."},
            {"type": "tool_result", "tool_use_id": "t4", "content": [
                {"type": "text", "text": "Async agent launched successfully.\nagentId: a1 (internal ID)"}]}]}),
        _line(type="attachment", attachment={"type": "task_status", "taskId": "b2", "taskType": "local_bash",
                                             "description": "Sleep a while", "status": "running"}),
        _line(type="user", message={"role": "user", "content":
                                    "<task-notification>\n<task-id>a1</task-id>\n<status>completed</status>\n"
                                    "<summary>Agent finished</summary>\n</task-notification>"}),
        *extra,
    ]
    path.write_text("\n".join(lines) + "\n")
    return path


class FakeLink:
    """A link whose requests are recorded, the first refused with `status` (and the answer `answer`) where one is
    given."""

    def __init__(self, status=None, answer=None):
        self.status, self.answer, self.sent = status, answer, []
        self.link_id, self.dir = "L1", "/nowhere"

    def request(self, method, path, body=None, **kw):
        self.sent.append((method, path, body))
        if self.status and len(self.sent) == 1:
            raise ade_link.Refused(self.status, "refused", self.answer)
        return {}


SID = "0a1b2c3d-0000-4000-8000-000000000001"


def sent_report(monkeypatch, tmp_path, mode=("clear",), sensitive=(False, None), sessions=({SID}, None), judged="go",
                held=None, fresh=True, also=(), **made):
    """What a heartbeat sends of its session's report: its transcript made as `made` says under a Claude folder of the
    test's own, the session, history.json's judgements (as the usage report's, `judged`, and the mirror's, `held`) and
    the link's encryption as given (or a function in their place), the body of its one request."""
    root = tmp_path / "claude"
    (root / "projects" / "p").mkdir(parents=True, exist_ok=True)
    for sid in (SID, *also):                        # each session given its transcript, as Claude Code keeps one
        transcript(root / "projects" / "p" / f"{sid}.jsonl", **made)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)                # as Claude Code gives its commands
    monkeypatch.setattr(ade_link, "_posting_sessions", sessions if callable(sessions) else (lambda: sessions))
    monkeypatch.setattr(ade_link, "_sensitive_poster", sensitive if callable(sensitive) else (lambda lk: sensitive))
    monkeypatch.setattr(ade_link, "_e2e_mode", mode if callable(mode) else (lambda lk, **kw: mode))
    monkeypatch.setattr(ade_link, "_history_rule", lambda lk: (None, None))
    monkeypatch.setattr(ade_link, "_usage_judged", judged if callable(judged) else (lambda *a, **kw: judged))
    monkeypatch.setattr(ade_link, "_mirror_held", held if callable(held) else (lambda *a, **kw: held))
    monkeypatch.setattr(ade_link, "_e2e_fresh", fresh if callable(fresh) else (lambda lk: fresh), raising=False)
    fake = FakeLink()
    assert ade_link.cmd_heartbeat(fake, None) == 0
    assert [(m, p) for m, p, _ in fake.sent] == [("POST", "/heartbeat")]
    return fake.sent[0][2].get("reported")


def test_the_client_reads_its_session_s_report_from_its_transcript(monkeypatch, tmp_path):
    got = sent_report(monkeypatch, tmp_path) or {}
    assert got.get("usage") == {"context_used": 143500, "context_window": 1000000, "model": "claude-opus-5-5"}
    assert got.get("tasks") == TASKS                                 # the newest list, as it stands
    assert got.get("background") == [
        {"id": "b1", "kind": "shell", "text": "Watch the message file", "state": "running"},
        {"id": "a1", "kind": "agent", "text": "Read diff", "state": "completed"},
        {"id": "b2", "kind": "shell", "text": "Sleep a while", "state": "running"}]
    small = sent_report(monkeypatch, tmp_path / "small", requested="claude-sonnet-5-5") or {}
    assert (small.get("usage") or {}).get("context_window") == 200000
    # lines out of shape, each passed over: an id that is a list, a result's that is an object, a status record's
    # that is a list, a line that is not JSON and one that is no object
    odd = (_line(type="assistant", message={"content": [{"type": "tool_use", "id": ["t5"], "name": "Bash",
                                                         "input": {"description": "d", "run_in_background": True}}]}),
           _line(type="user", message={"content": [{"type": "tool_result", "tool_use_id": {"id": "t3"},
                                                    "content": "Command running in background with ID: z9."}]}),
           _line(type="attachment", attachment={"type": "task_status", "taskId": ["b9"], "status": "running"}),
           "not JSON", "[1, 2]")
    odd_got = sent_report(monkeypatch, tmp_path / "odd", extra=odd) or {}
    assert [b["id"] for b in odd_got.get("background") or []] == ["b1", "a1", "b2"]
    # an agent started by the Agent tool's older name, Task; and a shell whose id is shaped like a secret, left out
    later = {"run_in_background": True}
    more = (_line(type="assistant", message={"content": [
                {"type": "tool_use", "id": "t6", "name": "Task", "input": dict(later, description="Search")},
                {"type": "tool_use", "id": "t7", "name": "Bash", "input": dict(later, description="k")}]}),
            _line(type="user", message={"content": [
                {"type": "tool_result", "tool_use_id": "t6", "content": "agentId: a7"},
                {"type": "tool_result", "tool_use_id": "t7",
                 "content": "Command running in background with ID: AKIA1234"}]}))
    more_got = sent_report(monkeypatch, tmp_path / "more", extra=more) or {}
    assert [(b["id"], b["kind"]) for b in more_got.get("background") or []] == [
        ("b1", "shell"), ("a1", "agent"), ("b2", "shell"), ("a7", "agent")]


def test_the_client_s_report_is_bounded_and_keeps_nothing_of_a_secret_s_shape(monkeypatch, tmp_path):
    todos = [{"content": f"step {i}", "status": "pending", "activeForm": "x"} for i in range(60)]
    todos[0]["content"] = "x" * 300
    todos[1]["content"] = "log in with ghp_0123456789"
    todos[2]["status"] = "deleted"
    shells = [{"type": "tool_use", "id": f"s{i}", "name": "Bash", "input": {
        "command": "c", "description": f"shell {i}", "run_in_background": True}} for i in range(25)]
    results = [{"type": "tool_result", "tool_use_id": f"s{i}", "content": f"Command running in background with ID: "
                f"x{i}"} for i in range(25)]
    extra = (_line(type="assistant", message={"model": "m", "content": [
                 {"type": "tool_use", "id": "t9", "name": "TodoWrite", "input": {"todos": todos}}, *shells]}),
             _line(type="user", message={"role": "user", "content": results}))
    got = sent_report(monkeypatch, tmp_path, extra=extra) or {}
    tasks, background = got.get("tasks") or [], got.get("background") or []
    assert len(tasks) == TASKS_MOST
    assert tasks[0]["text"] == "x" * (TEXT_MOST - 3) + "..."
    assert all("ghp_" not in t["text"] for t in tasks)
    assert [t["text"] for t in tasks[1:3]] == ["step 3", "step 4"]                   # step 1's secret, step 2's state
    assert len(background) == BACKGROUND_MOST and background[-1]["id"] == "x24"     # the newest kept
    assert plain_size({"tasks": tasks, "background": background}) <= PLAIN_MOST


def test_a_heartbeat_sends_the_report_and_after_a_refusal_once_more_presence_alone(monkeypatch, tmp_path):
    report = sent_report(monkeypatch, tmp_path)
    assert report is not None
    refused = FakeLink(422)
    assert ade_link.cmd_heartbeat(refused, None) == 0
    assert refused.sent == [("POST", "/heartbeat", {"reported": report}), ("POST", "/heartbeat", {})]
    failed = FakeLink(503)                          # a server's error, which the report may have caused
    assert ade_link.cmd_heartbeat(failed, None) == 0
    assert failed.sent == [("POST", "/heartbeat", {"reported": report}), ("POST", "/heartbeat", {})]
    busy = FakeLink(429)                            # not for its report: none is sent again, the next one comes anyway
    assert ade_link.cmd_heartbeat(busy, None) == 3
    assert busy.sent == [("POST", "/heartbeat", {"reported": report})]


def test_the_client_s_lists_fit_their_bytes_the_oldest_background_task_left_out_first_then_the_last_tasks(
        monkeypatch, tmp_path):
    def made(todos, shells):
        uses = [{"type": "tool_use", "id": f"s{i}", "name": "Bash", "input": {
            "description": text, "run_in_background": True}} for i, text in enumerate(shells)]
        results = [{"type": "tool_result", "tool_use_id": f"s{i}",
                    "content": f"Command running in background with ID: x{i}"} for i in range(len(shells))]
        return (_line(type="assistant", message={"model": "m", "content": [
                    {"type": "tool_use", "id": "t9", "name": "TodoWrite", "input": {"todos": [
                        {"content": t, "status": "pending", "activeForm": "x"} for t in todos]}}, *uses]}),
                _line(type="user", message={"role": "user", "content": results}))

    def pending(texts):
        return [{"text": t, "state": "pending"} for t in texts]

    # the tasks fit: the background tasks are left out from the oldest until the two lists do
    todos = [f"{i:02d}" + "t" * 148 for i in range(TASKS_MOST)]
    shells = [f"{i:02d}" + "s" * 148 for i in range(BACKGROUND_MOST)]
    got = sent_report(monkeypatch, tmp_path, extra=made(todos, shells)) or {}
    tasks, background = got.get("tasks") or [], got.get("background") or []
    assert tasks == pending(todos) and 0 < len(background) < BACKGROUND_MOST
    assert [b["id"] for b in background] == [f"x{i}" for i in range(BACKGROUND_MOST - len(background), BACKGROUND_MOST)]
    older = {"id": f"x{BACKGROUND_MOST - len(background) - 1}", "kind": "shell",
             "text": shells[BACKGROUND_MOST - len(background) - 1], "state": "running"}
    assert plain_size({"tasks": tasks, "background": background}) <= PLAIN_MOST \
        < plain_size({"tasks": tasks, "background": [older, *background]})
    # the tasks alone do not: every background task goes, then the tasks from the last until they fit
    wide = [f"{i:02d}" + "\u00e9" * 158 for i in range(TASKS_MOST)]
    got = sent_report(monkeypatch, tmp_path / "wide", extra=made(wide, shells)) or {}
    tasks = got.get("tasks") or []
    assert "background" not in got and 0 < len(tasks) < TASKS_MOST and tasks == pending(wide[:len(tasks)])
    assert plain_size({"tasks": tasks, "background": []}) <= PLAIN_MOST \
        < plain_size({"tasks": pending(wide[:len(tasks) + 1]), "background": []})


def test_the_watch_s_heartbeat_carries_the_report_too(capsys, tmp_path, monkeypatch, clock):
    # as the client's watch cases: no Claude Code session's inbox is posted into
    monkeypatch.delenv("CLAUDE_CODE_MESSAGING_SOCKET", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_MESSAGING_TOKEN", raising=False)
    d = a_link_to("https://open.example.test", tmp_path / "mac")
    scripted(monkeypatch, clock, lambda method, path, query:
             {} if path == "/heartbeat" else {"events": [], "through": 0, "head": 0})
    beats = []
    monkeypatch.setattr(ade_link, "_beat", lambda lk: beats.append(lk) or {}, raising=False)
    code, _, _ = run(capsys, "--dir", d, "watch", "--max", 60)
    assert code == 0 and len(beats) == 1


def test_a_refusal_for_encryption_is_kept_as_the_ade_s_word_before_presence_goes_alone(monkeypatch, tmp_path):
    report = sent_report(monkeypatch, tmp_path)
    assert report is not None and "tasks" in report                 # the control: its lists in the clear
    kept = []
    monkeypatch.setattr(ade_link, "_e2e_refused", lambda lk, e, mode, **kw: kept.append((e.status, e.body, mode)))
    word = {"e2e": "required", "seq": 4, "key_id": "c" * 32}

    class Kept(FakeLink):
        def request(self, method, path, body=None, **kw):
            kept.append("presence" if body == {} else "report")
            return FakeLink.request(self, method, path, body, **kw)

    refused = Kept(409, word)
    assert ade_link.cmd_heartbeat(refused, None) == 0
    assert refused.sent == [("POST", "/heartbeat", {"reported": report}), ("POST", "/heartbeat", {})]
    # so the next one's lists go sealed, or stay; read again once presence has gone
    assert kept == ["report", "presence", (409, word, ("clear",))]


def test_what_cannot_be_read_leaves_the_report_out_and_never_presence(monkeypatch, tmp_path):
    def locked(lk, **kw):
        raise ade_link.E2ELocked("e2e.lock is held")

    got = sent_report(monkeypatch, tmp_path, mode=locked) or {}
    assert set(got) == {"usage"} and got["usage"]["context_used"] == 143500       # the numbers alone

    def broken(lk):
        raise RuntimeError("history.json could not be read")

    assert sent_report(monkeypatch, tmp_path / "b", sensitive=broken) is None      # no report; presence went


def test_a_session_that_touched_a_sensitive_folder_or_cannot_be_told_reports_nothing(monkeypatch, tmp_path):
    assert sent_report(monkeypatch, tmp_path) is not None                          # the control
    assert sent_report(monkeypatch, tmp_path / "a", sensitive=(True, "it touched ~/private")) is None
    assert sent_report(monkeypatch, tmp_path / "b", sessions=(None, "it cannot be told")) is None
    other = "0a1b2c3d-0000-4000-8000-000000000002"
    assert sent_report(monkeypatch, tmp_path / "c", sessions=({SID, other}, None), also=(other,)) is None


def test_a_heartbeat_where_no_claude_code_session_names_itself_reads_none_of_its_records(monkeypatch, tmp_path):
    assert sent_report(monkeypatch, tmp_path) is not None                          # the control
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    asked = []
    monkeypatch.setattr(ade_link, "_posting_sessions", lambda: asked.append(1) or ({SID}, None))
    monkeypatch.setattr(ade_link, "_sensitive_poster", lambda lk: asked.append(2) or (False, None))
    fake = FakeLink()
    assert ade_link.cmd_heartbeat(fake, None) == 0
    assert fake.sent == [("POST", "/heartbeat", {})] and asked == []      # presence alone, no record read


def test_with_encryption_on_the_client_seals_the_report_s_words(monkeypatch, tmp_path):
    private = X25519PrivateKey.generate()
    got = sent_report(monkeypatch, tmp_path, mode=("seal", raw_public(private), "k" * 32, "acct")) or {}
    assert set(got) == {"usage", "sealed"} and got["usage"]["context_used"] == 143500
    assert e2e.slot("acct", "L1", "reported") == ade_link.e2e_slot("acct", "L1", "reported") \
        == "proofpane-e2e/1|acct|L1|reported"                       # the page's reportedSlot too (its case)
    opened = open_envelope(private, got["sealed"], e2e.slot("acct", "L1", "reported"))
    words = json.loads(opened) if isinstance(opened, (bytes, str)) else opened
    assert words["tasks"] == TASKS and [b["id"] for b in words["background"]] == ["b1", "a1", "b2"]
    # where encryption may be on but the link has no key to seal to, the numbers alone
    held = sent_report(monkeypatch, tmp_path / "held", mode=("hold", "no key yet")) or {}
    assert set(held) == {"usage"}


# ---- the client's report: its lists, its bounds and its reads --------------------------------------------------------

def test_the_plan_list_is_read_from_the_task_tools_too_as_each_task_stands(monkeypatch, tmp_path):
    def create(i, subject):
        return (_line(type="assistant", message={"content": [{"type": "tool_use", "id": f"c{i}", "name": "TaskCreate",
                                                              "input": {"subject": subject, "description": "d"}}]}),
                _line(type="user", toolUseResult={"task": {"id": str(i), "subject": subject}}, message={"content": [
                    {"type": "tool_result", "tool_use_id": f"c{i}",
                     "content": f"Task #{i} created successfully: {subject}"}]}))

    def update(n, i, **given):
        return (_line(type="assistant", message={"content": [{"type": "tool_use", "id": f"u{n}", "name": "TaskUpdate",
                                                              "input": dict(given, taskId=str(i))}]}),)
    extra = (*create(1, "the cases"), *create(2, "the code"), *create(3, "a step dropped"),
             *update(1, 1, status="in_progress"), *update(2, 2, status="completed"), *update(3, 3, status="deleted"),
             *update(4, 2, subject="the code, a first pass"), *update(5, 9, status="completed"))
    got = sent_report(monkeypatch, tmp_path, extra=extra) or {}
    assert got.get("tasks") == [{"text": "the cases", "state": "in_progress"},
                                {"text": "the code, a first pass", "state": "completed"}]
    # a turn's plan as Claude's desktop app keeps one (update_plan): a whole list, as TodoWrite's is
    plan = (_line(type="assistant", message={"content": [{"type": "tool_use", "id": "p1",
                                                          "name": "mcp__ccd_turn__update_plan", "input": {
        "items": [{"content": "read it", "status": "completed"}, {"content": "fix it", "status": "pending"}],
        "scope": "this_turn"}}]}),)
    got = sent_report(monkeypatch, tmp_path / "plan", extra=plan) or {}
    assert got.get("tasks") == [{"text": "read it", "state": "completed"}, {"text": "fix it", "state": "pending"}]


def test_the_context_used_is_the_session_s_own_newest_answer(monkeypatch, tmp_path):
    # the synthetic answer counts tokens here, so its name is what holds it out
    later = (_line(type="assistant", message={"model": "<synthetic>", "content": [],
                                              "usage": {"input_tokens": 5, "output_tokens": 0}}),
             _line(type="assistant", isSidechain=True, message={"model": "claude-haiku-5-5", "content": [],
                                                                "usage": {"input_tokens": 900}}),
             _line(type="assistant", sessionId="0a1b2c3d-0000-4000-8000-000000000009", message={
                 "model": "claude-sonnet-5-5", "content": [], "usage": {"input_tokens": 700}}),
             _line(type="assistant", message={"model": "claude-opus-5-5", "content": [],
                                              "usage": {"input_tokens": 0, "cache_read_input_tokens": 0}}))
    got = sent_report(monkeypatch, tmp_path, extra=later) or {}
    assert got.get("usage") == {"context_used": 143500, "context_window": 1000000, "model": "claude-opus-5-5"}


def test_a_secret_s_shape_across_two_texts_leaves_the_lists_out_and_a_cut_character_is_mended(monkeypatch, tmp_path):
    split = (_line(type="assistant", message={"content": [{"type": "tool_use", "id": "t8", "name": "TodoWrite",
                                                           "input": {"todos": [
        {"content": "Strip -----BEGIN markers", "status": "pending"},
        {"content": "Rotate PRIVATE KEY handling", "status": "pending"}]}}]}),)
    got = sent_report(monkeypatch, tmp_path, extra=split) or {}
    assert set(got) == {"usage"}                    # the server scans the clear part whole, and would refuse it all
    cut = (_line(type="assistant", message={"content": [{"type": "tool_use", "id": "t8", "name": "TodoWrite",
                                                         "input": {"todos": [
        {"content": "cut \ud83d here", "status": "pending"}]}}]}),)
    got = sent_report(monkeypatch, tmp_path / "cut", extra=cut) or {}
    assert got.get("tasks") == [{"text": "cut \ufffd here", "state": "pending"}]


def test_what_is_left_out_past_the_bounds_is_what_is_done_first(monkeypatch, tmp_path):
    todos = [{"content": "done first", "status": "completed"}] + [
        {"content": f"open {i}", "status": "pending"} for i in range(TASKS_MOST)]
    uses = [{"type": "tool_use", "id": f"s{i}", "name": "Bash", "input": {
        "description": f"shell {i}", "run_in_background": True}} for i in range(BACKGROUND_MOST + 1)]
    results = [{"type": "tool_result", "tool_use_id": f"s{i}",
                "content": f"Command running in background with ID: x{i}"} for i in range(BACKGROUND_MOST + 1)]
    extra = (_line(type="assistant", message={"content": [
                 {"type": "tool_use", "id": "t9", "name": "TodoWrite", "input": {"todos": todos}}, *uses]}),
             _line(type="user", message={"content": results}),
             _line(type="user", message={"content": "<task-notification>\n<task-id>x1</task-id>\n"
                                                    "<status>completed</status>\n</task-notification>"}))
    got = sent_report(monkeypatch, tmp_path, extra=extra) or {}
    assert [t["text"] for t in got.get("tasks") or []] == [f"open {i}" for i in range(TASKS_MOST)]
    assert [b["id"] for b in got.get("background") or []] == ["x0"] + [f"x{i}" for i in range(2, BACKGROUND_MOST + 1)]


def test_a_notice_counts_only_as_claude_code_writes_one_and_an_id_or_model_past_its_bound_is_not_taken(
        monkeypatch, tmp_path):
    said = (_line(type="assistant", message={"content": [{"type": "text", "text":
        "quoting one: <task-id>a1</task-id> ... <status>failed</status>"}]}),)
    got = sent_report(monkeypatch, tmp_path, extra=said) or {}
    assert [(b["id"], b["state"]) for b in got.get("background") or []][1] == ("a1", "completed")

    def started(n, sid):
        use = {"type": "tool_use", "id": f"w{n}", "name": "Bash",
               "input": {"description": "w", "run_in_background": True}}
        return (_line(type="assistant", message={"content": [use]}),
                _line(type="user", message={"content": [{"type": "tool_result", "tool_use_id": f"w{n}",
                                                         "content": f"Command running in background with ID: {sid}"}]}))
    ids = (*started(1, "y" * 64), *started(2, "z" * 65))
    got = sent_report(monkeypatch, tmp_path / "ids", extra=ids) or {}
    assert [b["id"] for b in got.get("background") or []][3:] == ["y" * 64]
    for model, kept in (("m" * 80, True), ("m" * 81, False)):
        line = (_line(type="assistant", message={"model": model, "content": [], "usage": {"input_tokens": 5}}),)
        got = sent_report(monkeypatch, tmp_path / model[:2] / str(len(model)), extra=line) or {}
        assert ("model" in got.get("usage", {})) is kept


def test_history_json_s_rules_hold_the_report_as_they_hold_usage_and_the_mirror(monkeypatch, tmp_path):
    asked = []

    def judged(link, path, cwd, rule, refused, listed_only, deadline):
        asked.append(("usage", os.path.basename(path), listed_only))
        return "go"

    def held(link, path, session, cwd, entries, whole, **kw):
        asked.append(("mirror", session, whole))
        return None

    assert sent_report(monkeypatch, tmp_path, judged=judged, held=held) is not None      # the control
    assert ("usage", f"{SID}.jsonl", False) in asked and ("mirror", SID, False) in asked
    for verdict in ("never", "skip", "wait"):                       # as no usage goes, none of the report does
        assert sent_report(monkeypatch, tmp_path / verdict, judged=verdict) is None
    touched = sent_report(monkeypatch, tmp_path / "touched", held="touched") or {}
    assert set(touched) == {"usage"}                # its words held, as the mirror holds its turns; its numbers go


def test_the_lists_go_in_the_clear_only_on_a_fresh_read_of_the_statements(monkeypatch, tmp_path):
    calls = []
    got = sent_report(monkeypatch, tmp_path, mode=lambda lk, **kw: calls.append(kw) or ("clear",)) or {}
    assert "tasks" in got and calls and calls[0].get("refresh", True) is not False \
        and 0 < (calls[0].get("timeout") or 0) <= 5                 # read again where older than a minute, bounded
    stale = sent_report(monkeypatch, tmp_path / "stale", fresh=False) or {}
    assert set(stale) == {"usage"}                  # a read that did not come: the numbers alone


def test_a_report_slow_to_read_is_left_out_and_presence_goes_at_once(monkeypatch, tmp_path):
    assert sent_report(monkeypatch, tmp_path) is not None                          # the control
    monkeypatch.setattr(ade_link, "REPORT_WITHIN", 0.3, raising=False)
    release, done = threading.Event(), threading.Event()

    def slow(lk, **kw):                     # the report's last read, held until the case lets it end
        release.wait(10)
        done.set()
        return ("clear",)

    monkeypatch.setattr(ade_link, "_e2e_mode", slow)
    fake = FakeLink()
    began = time.monotonic()
    try:
        assert ade_link.cmd_heartbeat(fake, None) == 0
        assert fake.sent == [("POST", "/heartbeat", {})] and time.monotonic() - began < 2
    finally:
        release.set()
        assert done.wait(10) or not release.is_set()    # the read ended inside the case, its stand-ins in place


def test_only_the_transcript_s_last_four_mib_are_read(monkeypatch, tmp_path):
    filler = tuple(_line(type="user", message={"role": "user", "content": "x" * 1000}) for _ in range(4400))
    late = (_line(type="assistant", message={"model": "claude-opus-5-5", "content": [],
                                             "usage": {"input_tokens": 42}}),)
    got = sent_report(monkeypatch, tmp_path, extra=filler + late) or {}
    assert (got.get("usage") or {}).get("context_used") == 42 and "tasks" not in got and "background" not in got
