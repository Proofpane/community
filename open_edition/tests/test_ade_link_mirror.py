"""The mirror (ade_link.py mirror reply): Claude Code's Stop hook puts a session's turn on the owner's timeline in the
ADE as it went. Through the app served on a local port: what reaches the timeline, to whom, once, in parts when long;
that only what the owner typed is shown as theirs; that the prompt hook, which cannot tell who sent a prompt, posts
nothing; and that a hook prints nothing and exits 0 whatever happens."""

import io
import json
import os
import re
import sys
import time

import pytest

import ade_link
from test_ade_link_client import link_row, owner, pair, run, served  # noqa: F401  fixtures and helpers


TOLD = "ADE: this session's words go to the owner's timeline as it says them"


def touched_note(session):
    """The note that a session touched a project history.json excepts, as the timeline shows it: it names the session
    by the first 8 characters of its id, as it goes in the link's own conversation, untagged."""
    return f"[mirror] Session {session[:8]}, or one whose entries it holds, touched a project history.json excepts: " \
           "its turns are not mirrored from now on."


def turn_note(session):
    """The note that a turn of a session was left out for touching a project history.json excepts, as the timeline
    shows it."""
    return f"[mirror] A turn of session {session[:8]} was not mirrored: it touched a project history.json excepts."


HELD, LEFT_OUT = touched_note("s1"), turn_note("s1")


@pytest.fixture(autouse=True)
def a_home_of_its_own(monkeypatch, own_home):
    # the Stop hook looks for a session's transcripts under $HOME/.claude/projects: the test's own home there, never
    # the run's, which may be the password database's (tests/conftest.py's guard stops a look in that one's)
    monkeypatch.setenv("HOME", own_home)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


def _hook(monkeypatch, capsys, directory, what, payload):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    return run(capsys, "--dir", directory, "mirror", what)


def _events(client, owner, name):
    link_id = link_row(name)["id"]
    r = client.get(f"/api/ade/link/timeline/{link_id}", headers=owner)
    assert r.status_code == 200
    return sorted(r.json()["events"], key=lambda e: e["seq"])                                   # oldest first


def _timeline(client, owner, name):
    """(to, text) oldest first, a mirrored note's text shown as the page shows it, after whose words it reports:
    [owner], the link's name, or [mirror] for the mirror's own word."""
    out = []
    for e in _events(client, owner, name):
        who = e.get("mirror")
        label = {"owner": "owner", "info": "mirror", "session": e["from"]}.get(who)
        number = re.match(r"\(\d+ of \d+\) ", e["text"])
        head = number.group(0) if number else ""
        out.append((e["to"], f"{head}[{label}] {e['text'][len(head):]}" if label else e["text"]))
    return out


def _transcript(path, entries):
    with open(path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def _prompt(uid, text):
    """A prompt as a transcript without origins has it (an older Claude Code): its sender is not known."""
    return {"type": "user", "uuid": uid, "message": {"role": "user", "content": text}}


def _typed(uid, text):
    """A turn's first prompt, typed by a person, as Claude Code writes one: text blocks, its origin and the turn's
    origin human."""
    return {"type": "user", "uuid": uid, "origin": {"kind": "human"}, "turnOrigin": "human",
            "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def _turn(uid, text, turn_origin, origin=None, meta=False):
    """A turn something other than the owner started: a task's notification, a peer's message, a schedule."""
    e = {"type": "user", "uuid": uid, "turnOrigin": turn_origin, "message": {"role": "user", "content": text}}
    if origin:
        e["origin"] = origin
    if meta:
        e["isMeta"] = True
    return e


def _queued(uid, text, origin=None, mode="prompt"):
    """A message that arrived while the session worked, as Claude Code records it: a queued command."""
    attachment = {"type": "queued_command", "prompt": [{"type": "text", "text": text}], "commandMode": mode}
    if origin:
        attachment["origin"] = origin
    return {"type": "attachment", "uuid": uid, "attachment": attachment}


def _said(uid, *blocks):
    return {"type": "assistant", "uuid": uid, "message": {"role": "assistant", "content": list(blocks)}}


def _text(t):
    return {"type": "text", "text": t}


def _stop(monkeypatch, capsys, directory, path, session="s1"):
    return _hook(monkeypatch, capsys, directory, "reply", {"transcript_path": path, "session_id": session})


def test_the_prompt_hook_posts_nothing_and_prints_nothing(client, owner, served, capsys, tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    for prompt in ("how is the build?", "<task-notification>a task ended</task-notification>", "x" * 200000):
        assert _hook(monkeypatch, capsys, mac, "prompt", {"prompt": prompt, "session_id": "s1"}) == (0, "", "")
    assert _timeline(client, owner, "mac") == []


def test_the_turn_reaches_the_owner_as_it_went_with_what_they_typed(client, owner, served, capsys, tmp_path,
                                                                    monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    entries = [
        _typed("u0", "an older prompt"), _said("a0", _text("an older reply")),
        _typed("u1", "how is the build?"),
        _said("a1", _text("Checking.")),
        _said("a2", {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}),
        {"type": "user", "uuid": "r1", "origin": {"kind": "human"}, "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "a tool's output"},
            _text("<system-reminder>a note the client adds beside a tool's result</system-reminder>")]}},
        _queued("q1", "and the tests?", {"kind": "human"}),
        _said("a3", _text("Green: 12 passed.")),
        _said("a4", _text("All done.")),
    ]
    _transcript(path, entries)
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert [t for _, t in _timeline(client, owner, "mac")] == [
        "[owner] how is the build?", "[mac] Checking.", "[owner] and the tests?", "[mac] Green: 12 passed.\n\nAll done."]
    assert all(to == "owner" for to, _ in _timeline(client, owner, "mac"))
    assert [e["text"] for e in _events(client, owner, "mac")][:2] == ["how is the build?", "Checking."]   # words alone
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")            # a later Stop on the same turn
    assert len(_timeline(client, owner, "mac")) == 4
    _transcript(path, entries + [_typed("u2", "next?"), _said("a5", _text("Next."))])
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert [t for _, t in _timeline(client, owner, "mac")][4:] == ["[owner] next?", "[mac] Next."]


def test_what_the_owner_did_not_type_is_never_shown_as_theirs(client, owner, served, capsys, tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    before = [_typed("u0", "start"), _said("a0", _text("Ready."))]
    _transcript(path, before)
    _stop(monkeypatch, capsys, mac, path)
    _transcript(path, before + [
        _turn("n1", "<task-notification>a build ended</task-notification>", "task_notification",
              {"kind": "task-notification"}),                                     # a turn a task's end started
        _said("a1", _text("Noted.")),
        _queued("q1", "<agent-message>a sub-agent's report</agent-message>",
                {"kind": "peer", "from": "agent-1"}),
        _queued("q2", "<task-notification>another task</task-notification>", mode="task-notification"),
        _turn("m1", "Scheduled: read the plan", "scheduled", meta=True),
        _turn("p1", "a peer session's wake", "peer", {"kind": "peer"}, meta=True),
        dict(_typed("m2", "a note the client wrote after a typed prompt"), isMeta=True),
        dict(_typed("c1", "a compaction's summary"), isCompactSummary=True),
        dict(_typed("s1", "a sub-agent's prompt"), isSidechain=True),
        _queued("q3", "no origin recorded"),
        _said("a2", _text("Done.")),
    ])
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert [t for _, t in _timeline(client, owner, "mac")][2:] == ["[mac] Noted.\n\nDone."]


def test_a_turn_a_task_s_notification_started_shows_once_where_the_page_joins_history_s_copy_and_the_notes(
        client, owner, served, capsys, tmp_path, monkeypatch):
    """The page shows History's messages before the first mirrored note's time, then the notes (sessionSaid, in
    Channel.tsx). A mirror that begins with the last turn, here one a task's notification started, mirrors that turn
    alone: History's copy must not hold its words in the message of the turn before."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [
        dict(_typed("u1", "start the build"), timestamp="2026-10-04T01:00:00Z"),
        dict(_said("a1", _text("Started.")), timestamp="2026-10-04T01:00:02Z"),
        dict(_turn("n1", "<task-notification>the build ended</task-notification>", "task_notification",
                   {"kind": "task-notification"}), timestamp="2026-10-04T01:05:00Z"),
        dict(_said("a2", _text("The build ended: green.")), timestamp="2026-10-04T01:05:03Z")])
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    notes = _events(client, owner, "mac")
    assert [e["text"] for e in notes] == ["The build ended: green."]
    st = os.stat(path)
    messages = ade_link._talk(path, st.st_size, st.st_ino)["messages"]
    cut = min(e["said_at"] for e in notes)
    shown = [m["text"] for m in messages if m["ms"] < cut] + [e["text"] for e in notes]
    assert shown == ["start the build", "Started.", "The build ended: green."]


def test_a_prompt_whose_sender_the_transcript_does_not_name_is_left_out(client, owner, served, capsys, tmp_path,
                                                                        monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    entries = [
        _prompt("u0", "an older prompt"), _said("a0", _text("an older reply")),
        _prompt("u1", "how is the build?"),
        _said("a1", {"type": "thinking", "thinking": "private"}, _text("Checking.")),
        _said("a2", {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}),
        {"type": "user", "uuid": "r1", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "a tool's output"},
            _text("<system-reminder>a note the client adds beside a tool's result</system-reminder>")]}},
        dict(_said("s1", _text("a sub-agent's words")), isSidechain=True),
        _said("a3", _text("Green: 12 passed.")),
    ]
    _transcript(path, entries)
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    mirrored = [t for to, t in _timeline(client, owner, "mac") if to == "owner"]
    assert mirrored == ["[mac] Checking.\n\nGreen: 12 passed."]               # the turn's words in one note
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert len(_timeline(client, owner, "mac")) == 1
    _transcript(path, entries + [_said("a4", _text("One more thing."))])
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert [t for _, t in _timeline(client, owner, "mac")] == mirrored + ["[mac] One more thing."]


def test_a_mirrored_note_reaches_no_other_session(client, owner, served, capsys, tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    cloud = pair(client, owner, served, capsys, str(tmp_path / "cloud"), "cloud", kind="cloud")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "do the thing"), _said("a1", _text("doing the thing"))])
    _stop(monkeypatch, capsys, mac, path)
    assert len(_timeline(client, owner, "mac")) == 2
    code, out, err = run(capsys, "--dir", cloud, "next")
    assert code == 0 and "do the thing" not in out and "doing the thing" not in out


def test_a_long_message_goes_in_numbered_parts_and_says_what_it_cut(client, owner, served, capsys, tmp_path,
                                                                    monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "x" * 20000)])
    _stop(monkeypatch, capsys, mac, path)
    events = _events(client, owner, "mac")
    texts = [e["text"] for e in events]
    assert len(texts) == ade_link.MIRROR_PARTS and {e["mirror"] for e in events} == {"owner"}
    assert all(len(t) <= ade_link.MIRROR_TEXT for t in texts)
    assert texts[0].startswith("(1 of 3) x") and texts[-1].endswith("more characters not mirrored]")
    note = texts[-1][texts[-1].index(" [... "):]
    left = int(note.split()[1])
    chunks = [t.split(") ", 1)[1] for t in texts]
    chunks[-1] = chunks[-1][:-len(note)]
    assert sum(len(c) for c in chunks) + left == 20000                          # what was shown and what was cut


def test_a_hook_that_cannot_post_prints_nothing_exits_0_and_notes_why(capsys, tmp_path, monkeypatch):
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "hi"), _said("a1", _text("hello"))])
    unpaired = str(tmp_path / "nowhere")
    assert _stop(monkeypatch, capsys, unpaired, path) == (0, "", "")
    os.makedirs(tmp_path / "gone", mode=0o700)
    with open(tmp_path / "gone" / "config.json", "w") as fh:
        json.dump({"url": "http://127.0.0.1:9", "name": "mac", "link_id": "x" * 12}, fh)     # nothing listens there
    assert _stop(monkeypatch, capsys, str(tmp_path / "gone"), path) == (0, "", "")
    with open(tmp_path / "gone" / "mirror.log", encoding="utf-8") as fh:
        assert "reply:" in fh.read()


def test_only_the_transcript_s_end_is_read_and_a_cut_line_is_left_out(tmp_path):
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "a prompt"), _said("a1", _text("first")), _said("a2", _text("second"))])
    with open(path, "rb") as fh:
        last = fh.read().split(b"\n")[-2]
    got = ade_link._turn_entries(path, window=len(last) + 10)
    assert got == [("a2", "session", "second")]
    assert ade_link._turn_entries(path) == [("u1", "owner", "a prompt"), ("a1", "session", "first"),
                                            ("a2", "session", "second")]
    assert ade_link._turn_entries(path, after="a1") == [("a2", "session", "second")]


def test_a_slow_ade_holds_the_turn_no_longer_than_the_hook_s_deadline(client, owner, served, capsys, tmp_path,
                                                                      monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "go"), _said("a1", _text("y" * 9000))])
    times = iter([0.0, 0.0, 0.0, 25.0])          # the deadline set, the prompt and the first part posted, then past it
    monkeypatch.setattr(ade_link, "_mirror_clock", lambda: next(times))
    assert len(ade_link._mirror_parts("y" * 9000)) == 3
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert [t[:9] for _, t in _timeline(client, owner, "mac")] == ["[owner] g", "(1 of 3) "]   # the rest was too late
    with open(os.path.join(mac, "mirror.log"), encoding="utf-8") as fh:
        assert "time ran out" in fh.read()


def test_a_part_waits_no_longer_than_the_hook_has_left(capsys, tmp_path, monkeypatch):
    os.makedirs(tmp_path / "mac", mode=0o700)
    with open(tmp_path / "mac" / "config.json", "w") as fh:
        json.dump({"url": "http://127.0.0.1:9", "name": "mac", "link_id": "x" * 12}, fh)
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_prompt("u1", "go"), _said("a1", _text("hi"))])
    waits = []
    # the read of the owner's encryption keys (GET /e2e) the Stop makes first is not a part
    monkeypatch.setattr(ade_link.Link, "request", lambda self, method, path, body=None, timeout=None, **k:
                        {"statements": []} if path == "/e2e" else waits.append(timeout) or {"seq": 1})
    times = iter([0.0, ade_link.MIRROR_DEADLINE - 3])                # 3 seconds left when the part goes
    monkeypatch.setattr(ade_link, "_mirror_clock", lambda: next(times))
    assert _stop(monkeypatch, capsys, str(tmp_path / "mac"), path) == (0, "", "")
    assert waits == [3]


def test_a_turn_the_owner_interrupted_goes_with_the_next(client, owner, served, capsys, tmp_path, monkeypatch):
    """Stop does not run when the owner interrupts a turn: what it held goes with the next turn's Stop."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    first = [_typed("u1", "first"), _said("a1", _text("One."))]
    _transcript(path, first)
    _stop(monkeypatch, capsys, mac, path)
    _transcript(path, first + [
        _typed("u2", "deploy to staging"), _said("a2", _text("Deploying.")),
        {"type": "user", "uuid": "i1", "message": {"role": "user", "content": [
            {"type": "text", "text": "[Request interrupted by user]"}]}},
        dict(_prompt("u3", "no, run the tests first"), origin={"kind": "human"}, turnOrigin="human"),
        _said("a3", _text("Running the tests."))])
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert [t for _, t in _timeline(client, owner, "mac")][2:] == [
        "[owner] deploy to staging", "[mac] Deploying.", "[owner] no, run the tests first", "[mac] Running the tests."]


def test_with_no_record_of_what_was_mirrored_only_the_last_turn_goes(client, owner, served, capsys, tmp_path,
                                                                     monkeypatch):
    """With nothing kept of what went before (a first Stop, a mirror.json that cannot be read), the last turn only,
    whoever started it: a schedule's and a peer's turns start one too."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "first"), _said("a1", _text("One.")),
                       _turn("m1", "Scheduled: read the plan", "scheduled", meta=True),
                       _said("a2", _text("Read it."))])
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert [t for _, t in _timeline(client, owner, "mac")] == ["[mac] Read it."]
    with open(os.path.join(mac, "mirror.json"), "w") as fh:
        fh.write("not json")
    _transcript(path, [_typed("u1", "first"), _said("a1", _text("One.")),
                       _turn("p1", "a peer's wake", "peer", {"kind": "peer"}, meta=True),
                       _said("a2", _text("Answered the peer."))])
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert [t for _, t in _timeline(client, owner, "mac")][1:] == ["[mac] Answered the peer."]


def test_a_post_that_fails_is_not_tried_again(client, owner, served, capsys, tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "hello"), _said("a1", _text("Hi."))])
    real = ade_link.Link.request
    down = [True]

    def request(self, method, where, body=None, **k):
        if down[0] and where == "/events":
            raise ade_link.Unreachable("the ADE is down")
        return real(self, method, where, body, **k)
    monkeypatch.setattr(ade_link.Link, "request", request)
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    with open(os.path.join(mac, "mirror.log"), encoding="utf-8") as fh:
        assert "reply: Unreachable" in fh.read()
    down[0] = False
    later = ade_link._mirror_now() + ade_link.MIRROR_DOWN + 1           # past the pause a failure starts
    monkeypatch.setattr(ade_link, "_mirror_now", lambda: later)
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _timeline(client, owner, "mac") == []


def test_a_stop_posts_at_most_twelve_notes_the_newest(client, owner, served, capsys, tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    entries = [_typed("u0", "go")]
    for i in range(8):
        entries += [_said(f"a{i}", _text(f"step {i}")), _queued(f"q{i}", f"and {i}?", {"kind": "human"})]
    _transcript(path, entries)                                      # 17 notes: the prompt, then 8 of each
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    texts = [t for _, t in _timeline(client, owner, "mac")]
    assert len(texts) == ade_link.MIRROR_NOTES == 12
    assert texts[0] == "[mirror] 6 earlier messages of this session not mirrored"
    assert texts[1:3] == ["[owner] and 2?", "[mac] step 3"] and texts[-1] == "[owner] and 7?"


def test_a_hook_that_names_no_directory_exits_0(capsys, monkeypatch):
    monkeypatch.delenv("ADE_LINK_DIR", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    assert run(capsys, "mirror", "reply") == (0, "", "")


def test_what_was_mirrored_is_read_and_written_under_its_lock(client, owner, served, capsys, tmp_path, monkeypatch):
    """Two sessions sharing a link's directory each keep their place: a Stop waits for the other's to be kept."""
    import fcntl
    import threading
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "hello"), _said("a1", _text("Hi."))])
    fd = os.open(os.path.join(mac, "mirror.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"transcript_path": path, "session_id": "s1"})))
    done = []
    stop = threading.Thread(target=lambda: done.append(ade_link.main(["--dir", mac, "mirror", "reply"])))
    stop.start()
    stop.join(1.0)
    assert stop.is_alive() and not os.path.exists(os.path.join(mac, "mirror.json"))
    os.close(fd)                                                     # the other hook's lock released
    stop.join(10)
    assert done == [0] and len(_timeline(client, owner, "mac")) == 2


def test_a_post_that_never_answers_holds_the_turn_no_longer_than_the_deadline(capsys, tmp_path, monkeypatch):
    """A name lookup that never answers is not bounded by a socket's timeout: the posts run in a thread the hook leaves
    behind once its time is up."""
    import threading
    import time as _time
    os.makedirs(tmp_path / "mac", mode=0o700)
    with open(tmp_path / "mac" / "config.json", "w") as fh:
        json.dump({"url": "http://127.0.0.1:9", "name": "mac", "link_id": "x" * 12}, fh)
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "hello"), _said("a1", _text("Hi."))])
    gate = threading.Event()
    # each post hangs till the test ends; the read of the owner's encryption keys the Stop makes first answers
    # (one that hangs is bounded apart, by E2E_STOP_READ)
    monkeypatch.setattr(ade_link.Link, "request", lambda self, method, path, *a, **k:
                        {"statements": []} if path == "/e2e" else gate.wait(30))
    monkeypatch.setattr(ade_link, "MIRROR_DEADLINE", 0.5)
    started = _time.monotonic()
    try:
        assert _stop(monkeypatch, capsys, str(tmp_path / "mac"), path) == (0, "", "")
        assert _time.monotonic() - started < 3
    finally:
        gate.set()
    with open(tmp_path / "mac" / "mirror.log", encoding="utf-8") as fh:
        assert "an answer never came" in fh.read()
    assert (tmp_path / "mac" / "mirror_down").exists()


def test_after_the_ade_did_not_answer_a_stop_posts_nothing_for_a_minute_and_keeps_its_place(client, owner, served,
                                                                                              capsys, tmp_path,
                                                                                              monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    first = [_typed("u1", "hello"), _said("a1", _text("Hi."))]
    _transcript(path, first)
    real = ade_link.Link.request
    down = [True]

    def request(self, method, where, body=None, **k):
        if down[0] and where == "/events":
            raise ade_link.Unreachable("the ADE is down")
        return real(self, method, where, body, **k)
    monkeypatch.setattr(ade_link.Link, "request", request)
    now = [1_000_000.0]
    monkeypatch.setattr(ade_link, "_mirror_now", lambda: now[0])
    _stop(monkeypatch, capsys, mac, path)                             # finds the ADE down: that turn is gone
    down[0] = False
    _transcript(path, first + [_typed("u2", "are you there?"), _said("a2", _text("Yes."))])
    now[0] += 30                                                      # within the pause: nothing posted, nothing kept
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _timeline(client, owner, "mac") == []
    now[0] += 31                                                      # past it: the turn said meanwhile goes now
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert [t for _, t in _timeline(client, owner, "mac")] == ["[owner] are you there?", "[mac] Yes."]
    assert not (tmp_path / "mac" / "mirror_down").exists()           # a post that went through ends the pause



def test_a_secret_cut_across_two_parts_is_left_out_whole(client, owner, served, capsys, tmp_path, monkeypatch):
    """The server refuses a part shaped like a secret; a secret cut where one part ends and the next begins would pass
    it piece by piece, so the whole text is checked before it is cut."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    cut = ade_link.MIRROR_TEXT - 80                                      # where _mirror_parts cuts
    text = "x" * (cut - 5) + " sk-ant-api03-" + "y" * 200                 # cut inside it: " sk-a" | "nt-api03-..."
    parts = ade_link._mirror_parts(text)
    assert len(parts) == 2 and any(shape.search(text) for shape in ade_link._SECRET_SHAPES)
    assert not any(shape.search(part) for part in parts for shape in ade_link._SECRET_SHAPES)
    _transcript(path, [_typed("u1", "go"), _said("a1", _text(text))])
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    texts = [t for _, t in _timeline(client, owner, "mac")]
    assert texts == ["[owner] go", "[mac] [left out: it held something shaped like a secret, which the ADE does not keep]"]


def test_a_failure_log_is_the_user_s_alone_and_kept_short(tmp_path, monkeypatch):
    where = tmp_path / "mirror.log"
    monkeypatch.setattr(ade_link, "LOG_MAX", 100)
    for i in range(20):
        ade_link._note_failure(str(where), f"reply: Unreachable: attempt {i}")
    assert where.stat().st_mode & 0o777 == 0o600
    assert where.stat().st_size <= 100 + 60 and "attempt 19" in where.read_text()


# ---- history.json's rule: which sessions the link shares, the mirror's as the History list's ----------------------

def _ruled(tmp_path, monkeypatch, directory, rule):
    """history.json as given: by default, as shipped, it asks for no participants (_participant asks for them)."""
    home = tmp_path / "home"
    (home / "work").mkdir(parents=True, exist_ok=True)
    (home / "grove").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    with open(os.path.join(directory, "history.json"), "w") as fh:
        fh.write(rule if isinstance(rule, str) else json.dumps(rule))
    started = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: started.append(a))       # the History run a Stop starts
    return home, started


def _tool_use(uid, name, tool_input):
    return _said(uid, {"type": "tool_use", "id": f"x{uid}", "name": name, "input": tool_input})


def _participant(monkeypatch, capsys, directory, session, source="startup"):
    """The session began with this link's resume, at its very start (SessionStart's input names it): told so, it knows
    it is mirrored. history.json is made to ask for participants, unless it says."""
    where = os.path.join(directory, "history.json")
    rule = json.loads(open(where).read())
    if isinstance(rule, dict) and "participants" not in rule:
        with open(where, "w") as fh:
            json.dump(dict(rule, participants=True), fh)
    # its transcript is not written yet: a path in the link's own directory, where nothing is on any machine (a folder
    # /nonexistent is there on some, and resume lets in no session whose transcript it cannot read)
    unwritten = os.path.join(directory, "transcript-not-written-yet.jsonl")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": session, "hook_event_name": "SessionStart",
                                                              "source": source, "transcript_path": unwritten})))
    code, out, _ = run(capsys, "--dir", directory, "resume")
    assert code == 0 and session in json.loads(open(os.path.join(directory, "participants.json")).read())
    assert out.startswith(TOLD) and out.splitlines()[0].endswith("~/grove.")         # first, naming the folders


def _in_projects(home, session, entries):
    """A transcript where Claude Code keeps it, which the History list reads."""
    path = home / ".claude" / "projects" / "-work" / f"{session}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    _transcript(str(path), entries)
    return str(path)


def _stop_in(monkeypatch, capsys, directory, path, session, cwd, *flags):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"transcript_path": path, "session_id": session,
                                                              "cwd": cwd})))
    return run(capsys, "--dir", directory, "mirror", "reply", *flags)


def test_a_session_history_json_does_not_list_is_never_mirrored(client, owner, served, capsys, tmp_path, monkeypatch):
    """Read whole by the History list first, so only where the session began decides: one of an excepted project, and
    one outside the projects history.json names, are never mirrored."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": ["~/work"], "except": ["~/grove"]})
    (home / "elsewhere").mkdir()
    sessions = {}
    for sid, cwd in (("s-grove", str(home / "grove")), ("s-else", str(home / "elsewhere"))):
        sessions[sid] = (_in_projects(home, sid, [dict(_typed("u1", "a plan"), cwd=cwd), _said("a1", _text("Noted."))]),
                         cwd)
    assert run(capsys, "--dir", mac, "history")[0] == 0
    for sid, (path, cwd) in sessions.items():
        assert _stop_in(monkeypatch, capsys, mac, path, sid, cwd) == (0, "", "")
    assert _timeline(client, owner, "mac") == []


def test_a_turn_that_touched_an_excepted_project_is_left_out_and_said_to_be(client, owner, served, capsys, tmp_path,
                                                                           monkeypatch):
    """A session that began with this link's resume, which knows its words are mirrored: only its turns that touched
    an excepted project are left out."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    _participant(monkeypatch, capsys, mac, "s1")
    turn = [dict(_typed("u1", "what does the plan say?"), cwd=str(home / "work")),
            _tool_use("a1", "Read", {"file_path": str(home / "grove" / "x.md")}), _said("a2", _text("It says: hire two."))]
    path = _in_projects(home, "s1", turn)
    payload = {"transcript_path": path, "session_id": "s1", "cwd": str(home / "work")}
    assert _hook(monkeypatch, capsys, mac, "reply", payload) == (0, "", "")
    assert [t for _, t in _timeline(client, owner, "mac")] == [LEFT_OUT]
    _transcript(path, turn + [_typed("u2", "and the build?"), _said("a3", _text("Green."))])
    assert _hook(monkeypatch, capsys, mac, "reply", payload) == (0, "", "")
    assert [t for _, t in _timeline(client, owner, "mac")][1:] == ["[owner] and the build?", "[mac] Green."]


def test_the_note_that_a_turn_was_left_out_names_its_session_by_8_characters_of_its_id(client, owner, served, capsys,
                                                                                      tmp_path, monkeypatch):
    """The note goes untagged into the link's own conversation, where "this session" would read as the paired session's
    own, though it may be about another session of the machine."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    session = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
    _participant(monkeypatch, capsys, mac, session)
    path = _in_projects(home, session, [dict(_typed("u1", "what does the plan say?"), cwd=str(home / "work")),
                                        _tool_use("a1", "Read", {"file_path": str(home / "grove" / "x.md")}),
                                        _said("a2", _text("It says: hire two."))])
    payload = {"transcript_path": path, "session_id": session, "cwd": str(home / "work")}
    assert _hook(monkeypatch, capsys, mac, "reply", payload) == (0, "", "")
    assert [t for _, t in _timeline(client, owner, "mac")] == [
        "[mirror] A turn of session 1a2b3c4d was not mirrored: it touched a project history.json excepts."]


def test_the_note_that_a_session_touched_names_it_by_8_characters_of_its_id_and_never_by_its_title(
        client, owner, served, capsys, tmp_path, monkeypatch):
    """Two sessions of one machine, whose notes read alike in the link's own conversation but for the session each
    names; a withheld session's title stays on the machine."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w, paths = str(home / "work"), {}
    for session in ("1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d", "9f8e7d6c-5b4a-4392-8170-6f5e4d3c2b1a"):
        paths[session] = _in_projects(home, session, [
            dict(_typed("u1", "what does the plan say?"), cwd=w),
            {"type": "custom-title", "customTitle": "Acme renewal"},
            _tool_use("a1", "Read", {"file_path": str(home / "grove" / "x.md")}), _said("a2", _text("Hire two."))])
    assert run(capsys, "--dir", mac, "history")[0] == 0
    for session, path in paths.items():
        _stop_in(monkeypatch, capsys, mac, path, session, w)
    got = [t for _, t in _timeline(client, owner, "mac")]
    assert got == [
        "[mirror] Session 1a2b3c4d, or one whose entries it holds, touched a project history.json excepts: its turns "
        "are not mirrored from now on.",
        "[mirror] Session 9f8e7d6c, or one whose entries it holds, touched a project history.json excepts: its turns "
        "are not mirrored from now on."]
    assert "Acme" not in json.dumps(got)


def test_the_note_for_a_hook_that_names_no_session_says_so(client, owner, served, capsys, tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    path = _in_projects(home, "s1", [dict(_typed("u1", "what does the plan say?"), cwd=w),
                                     _tool_use("a1", "Read", {"file_path": str(home / "grove" / "x.md")}),
                                     _said("a2", _text("Hire two."))])
    assert run(capsys, "--dir", mac, "history")[0] == 0
    _stop_in(monkeypatch, capsys, mac, path, "", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == [
        "[mirror] Session (no id), or one whose entries it holds, touched a project history.json excepts: its turns "
        "are not mirrored from now on."]


def test_a_history_json_that_cannot_be_read_holds_every_turn(client, owner, served, capsys, tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "exclude": ["~/grove"]})
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "hello"), _said("a1", _text("Hi."))])
    payload = {"transcript_path": path, "session_id": "s1", "cwd": str(home / "work")}
    assert _hook(monkeypatch, capsys, mac, "reply", payload) == (0, "", "")
    assert _timeline(client, owner, "mac") == []
    _transcript(path, [_typed("u1", "hello"), _said("a1", _text("Hi.")), _typed("u2", "again"),
                       _said("a2", _text("Hi."))])
    assert _hook(monkeypatch, capsys, mac, "reply", payload) == (0, "", "")
    log = open(os.path.join(mac, "mirror.log")).read()
    assert "missing" not in log                                          # one not read, not one missing
    assert log.count("held: history.json cannot be used (history.json has a key it does not know") == 1   # once


def test_a_stop_starts_the_history_list_at_most_every_few_minutes(client, owner, served, capsys, tmp_path,
                                                                  monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, started = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    _participant(monkeypatch, capsys, mac, "s1")
    path = str(tmp_path / "t.jsonl")
    entries = [_typed("u1", "one"), _said("a1", _text("One."))]
    _transcript(path, entries)
    payload = {"transcript_path": path, "session_id": "s1", "cwd": str(home / "work")}
    _hook(monkeypatch, capsys, mac, "reply", payload)
    _transcript(path, entries + [_typed("u2", "two"), _said("a2", _text("Two."))])
    _hook(monkeypatch, capsys, mac, "reply", payload)
    assert len(started) == 1 and started[0][0][-2:] == ["history", "--quiet"]
    monkeypatch.setattr(ade_link, "HISTORY_EVERY", 0)
    _transcript(path, entries + [_typed("u2", "two"), _said("a2", _text("Two.")), _typed("u3", "three"),
                                 _said("a3", _text("Three."))])
    _hook(monkeypatch, capsys, mac, "reply", payload)
    assert len(started) == 2


def test_a_secret_s_shape_made_by_taking_a_nul_out_or_by_a_cut_is_never_posted():
    """The NULs go before the whole text is checked, and each part is checked again once cut and numbered."""
    joined = ade_link._unsecret("x sk\x00-ant-api03-y".replace("\x00", ""))
    assert joined.startswith("[left out")
    assert all(not any(shape.search(p) for shape in ade_link._SECRET_SHAPES)
               for p in ade_link._mirror_parts("x sk\x00-ant-api03-y"))
    size = ade_link.MIRROR_TEXT - 80
    text = "y" * (size - 12) + " PP-ABCDEFGH" + "Z" * 10 + "y" * size     # a ticket only once the cut ends it
    assert not any(shape.search(text) for shape in ade_link._SECRET_SHAPES)
    parts = ade_link._mirror_parts(text)
    assert not any(shape.search(p) for p in parts for shape in ade_link._SECRET_SHAPES)


def test_the_everywhere_mirror_posts_nothing_without_a_history_json(client, owner, served, capsys, tmp_path,
                                                                     monkeypatch):
    """--listed-only, the block for every session in any folder: no history.json, no turn mirrored; one that lists the
    session, its turns as ever."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home = tmp_path / "home"
    (home / "work").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: None)
    entries = [_typed("u1", "hello"), _said("a1", _text("Hi."))]
    path = _in_projects(home, "s1", [dict(entries[0], cwd=str(home / "work")), entries[1]])
    assert _stop_in(monkeypatch, capsys, mac, path, "s1", str(home / "work"), "--listed-only") == (0, "", "")
    assert _timeline(client, owner, "mac") == []
    kept = os.path.join(mac, "mirror.json")
    assert json.loads(open(kept).read()).get("s1") == ade_link.MIRROR_FROM_START     # its place kept, not taken
    with open(os.path.join(mac, "history.json"), "w") as fh:
        json.dump({"projects": "all"}, fh)
    assert run(capsys, "--dir", mac, "history")[0] == 0                # the session read whole, as a Stop waits for
    _transcript(path, [dict(entries[0], cwd=str(home / "work")), entries[1], _typed("u2", "again"),
                       _said("a2", _text("Again."))])
    assert _stop_in(monkeypatch, capsys, mac, path, "s1", str(home / "work"), "--listed-only") == (0, "", "")
    assert [t for _, t in _timeline(client, owner, "mac")] == ["[owner] again", "[mac] Again."]


def test_a_session_that_touched_an_excepted_project_is_mirrored_no_more_from_then_on(client, owner, served, capsys,
                                                                                    tmp_path, monkeypatch):
    """Any session but one that began with this link's resume: once it touched an excepted project, none of its turns
    goes, one note saying so the first time; what it read can colour what it says long after, unnamed."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    turn1 = [dict(_typed("u1", "what does the plan say?"), cwd=w),
             _tool_use("a1", "Read", {"file_path": str(home / "grove" / "x.md")}), _said("a2", _text("Hire two."))]
    path = _in_projects(home, "s1", turn1)
    assert run(capsys, "--dir", mac, "history")[0] == 0
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == [
        HELD]
    _transcript(path, turn1 + [_typed("u2", "so what next?"), _said("a3", _text("Hire the second one first."))])
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert len(_timeline(client, owner, "mac")) == 1                    # nothing more, not even the note again


def test_a_session_not_yet_read_whole_waits_with_its_turns_kept(client, owner, served, capsys, tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, started = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    path = _in_projects(home, "s1", [dict(_typed("u1", "hello"), cwd=w), _said("a1", _text("Hi."))])
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert _timeline(client, owner, "mac") == [] and len(started) == 1   # a run started to read it
    assert run(capsys, "--dir", mac, "history")[0] == 0
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == ["[owner] hello", "[mac] Hi."]


def test_a_session_that_began_in_an_excepted_project_is_never_mirrored_wherever_it_went(client, owner, served,
                                                                                       capsys, tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    _participant(monkeypatch, capsys, mac, "s1")
    path = _in_projects(home, "s1", [dict(_typed("u1", "start"), cwd=str(home / "grove")),
                                     dict(_typed("u2", "now elsewhere"), cwd=str(home / "work")),
                                     _said("a1", _text("Done."))])
    _stop_in(monkeypatch, capsys, mac, path, "s1", str(home / "work"))
    assert _timeline(client, owner, "mac") == []


def test_a_participant_s_turn_read_only_in_part_is_judged_with_the_session(client, owner, served, capsys, tmp_path,
                                                                          monkeypatch):
    """What lies before the window (a sub-agent's entries among it) cannot be judged on its own: the turn waits for the
    History list's whole reading of the session, and goes by that, as any session's."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    _participant(monkeypatch, capsys, mac, "s1")
    monkeypatch.setattr(ade_link, "MIRROR_WINDOW", 600)
    w = str(home / "work")
    path = _in_projects(home, "s1", [dict(_typed("u1", "go"), cwd=w),
                                     _tool_use("a1", "Read", {"file_path": str(home / "grove" / "x.md")})]
                        + [_said(f"b{n}", _text("y" * 200)) for n in range(6)])
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert _timeline(client, owner, "mac") == []                        # not read whole yet: it waits
    assert run(capsys, "--dir", mac, "history")[0] == 0
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == [
        HELD]


def test_a_participant_s_clean_turn_read_only_in_part_goes_and_it_stays_one(client, owner, served, capsys, tmp_path,
                                                                           monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    _participant(monkeypatch, capsys, mac, "s1")
    monkeypatch.setattr(ade_link, "MIRROR_WINDOW", 600)
    w = str(home / "work")
    path = _in_projects(home, "s1", [dict(_typed("u1", "go"), cwd=w)]
                        + [_said(f"b{n}", _text(str(n) * 200)) for n in range(6)])
    assert run(capsys, "--dir", mac, "history")[0] == 0
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    got = [t for _, t in _timeline(client, owner, "mac")]
    assert got and all(t.startswith("[mac] ") for t in got) and "5" * 200 in got[-1]
    assert "s1" in json.loads(open(os.path.join(mac, "participants.json")).read())


def test_a_participant_whose_sub_agent_touched_an_excepted_project_is_held_as_any_other(client, owner, served, capsys,
                                                                                        tmp_path, monkeypatch):
    """A sub-agent's own file, or its entries in the session's (an earlier Claude Code's): what it read reaches the
    session only in its report, so the session is held as any other from then on."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    _participant(monkeypatch, capsys, mac, "s1")
    _participant(monkeypatch, capsys, mac, "s2")
    w = str(home / "work")
    now = "2026-10-03T10:00:00Z"                                      # before the sub-agent wrote
    turn = [dict(_typed("u1", "find it"), cwd=w, timestamp=now), dict(_said("a1", _text("Found it.")), timestamp=now)]
    path = _in_projects(home, "s1", turn)
    agent = home / ".claude" / "projects" / "-work" / "s1" / "subagents" / "agent-1.jsonl"
    agent.parent.mkdir(parents=True)
    _transcript(str(agent), [_tool_use("x1", "Read", {"file_path": str(home / "grove" / "x.md")})])
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    side = {"type": "assistant", "uuid": "sc", "isSidechain": True,
            "message": {"content": [{"type": "tool_use", "id": "t", "name": "Read",
                                     "input": {"file_path": str(home / "grove" / "y")}}]}}
    path = _in_projects(home, "s2", [dict(_typed("v1", "again"), cwd=w), side, _said("b1", _text("Again."))])
    _stop_in(monkeypatch, capsys, mac, path, "s2", w)
    notes = [touched_note("s1"), touched_note("s2")]                                  # each names its own session
    assert [t for _, t in _timeline(client, owner, "mac")] == notes
    assert json.loads(open(os.path.join(mac, "participants.json")).read()) == {}
    later = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 120))
    _transcript(path, [dict(_typed("v1", "again"), cwd=w), side, _said("b1", _text("Again.")),
                       dict(_typed("v2", "and now?"), cwd=w, timestamp=later),
                       dict(_said("b2", _text("Revenue is up.")), timestamp=later)])
    _stop_in(monkeypatch, capsys, mac, path, "s2", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == notes                  # its later turns held too


def test_resume_keeps_the_session_it_runs_for_as_one_that_knows_it_is_mirrored(client, owner, served, capsys,
                                                                                 tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    for sid in ("s1", "s2", "s1"):                                       # the third: one already in, told again
        _participant(monkeypatch, capsys, mac, sid)
    assert list(json.loads(open(os.path.join(mac, "participants.json")).read())) == ["s1", "s2"]
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert run(capsys, "--dir", mac, "resume")[0] == 0                 # whatever stdin holds, resume goes on



# ---- a session that knows it is mirrored, and what a Stop holds or posts ---------------------------------------------

def _resume_with(monkeypatch, capsys, directory, hook):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(hook)))
    return run(capsys, "--dir", directory, "resume")


def test_only_a_session_s_very_start_makes_it_one_that_knows_it_is_mirrored(client, owner, served, capsys, tmp_path,
                                                                           monkeypatch):
    """Resumed, compacted or forked, a session has said things before; one with a turn already in its transcript too;
    and one this link withheld once never becomes one: each would have its old words judged a turn at a time."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"], "participants": True})
    said = _in_projects(home, "s-said", [dict(_typed("u1", "an old turn"), cwd=str(home / "work"))])
    _in_projects(home, "s-found", [dict(_typed("u1", "an old turn"), cwd=str(home / "work"))])   # found by its id
    with open(os.path.join(mac, "history_withheld.json"), "w") as fh:
        json.dump(["s-held"], fh)
    for hook in ({"session_id": "s1", "source": "resume"}, {"session_id": "s1", "source": "compact"},
                 {"session_id": "s1", "source": "fork"}, {"session_id": "s-said", "source": "startup",
                                                           "transcript_path": said},
                 {"session_id": "s-held", "source": "startup"}, {"session_id": "s-found", "source": "startup"}):
        code, out, _ = _resume_with(monkeypatch, capsys, mac, hook)
        assert code == 0 and TOLD not in out, hook
    assert not os.path.exists(os.path.join(mac, "participants.json"))
    code, out, _ = _resume_with(monkeypatch, capsys, mac, {"session_id": "s-new", "source": "clear"})
    assert TOLD in out
    assert list(json.loads(open(os.path.join(mac, "participants.json")).read())) == ["s-new"]


def test_resume_never_waits_on_a_stop_holding_the_mirror(client, owner, served, capsys, tmp_path, monkeypatch):
    import fcntl
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"], "participants": True})
    fd = os.open(os.path.join(mac, "mirror.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)                                       # a Stop of another session, reading
    try:
        began = time.monotonic()
        code, out, _ = _resume_with(monkeypatch, capsys, mac, {"session_id": "s1", "source": "startup"})
        assert code == 0 and time.monotonic() - began < 5 and TOLD in out
        assert "s1" in json.loads(open(os.path.join(mac, "participants.json")).read())   # kept, under its own lock
    finally:
        os.close(fd)


def test_a_new_session_s_turns_said_before_it_was_read_whole_all_go(client, owner, served, capsys, tmp_path,
                                                                    monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    path = _in_projects(home, "s1", [dict(_typed("u1", "first"), cwd=w), _said("a1", _text("One.")),
                                     _typed("u2", "second"), _said("a2", _text("Two."))])
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert _timeline(client, owner, "mac") == []                         # not read whole yet: kept
    assert run(capsys, "--dir", mac, "history")[0] == 0
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == ["[owner] first", "[mac] One.", "[owner] second",
                                                               "[mac] Two."]


def test_a_stop_that_cannot_judge_in_time_keeps_the_turn(client, owner, served, capsys, tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    path = _in_projects(home, "s1", [dict(_typed("u1", "hello"), cwd=w), _said("a1", _text("Hi."))])
    assert run(capsys, "--dir", mac, "history")[0] == 0
    with open(path, "a") as fh:                                          # more to read since the History run
        fh.write(json.dumps(_said("a2", _text("more"))) + "\n")
    held = ade_link._mirror_held(ade_link.Link(mac), path, "s1", w, [], True, False, time.monotonic() - 1)
    assert held == "unknown"
    assert ade_link._mirror_held(ade_link.Link(mac), path, "s1", w, [], True, False, time.monotonic() + 30) is None


def test_a_session_whose_copy_in_another_project_folder_touched_is_held(client, owner, served, capsys, tmp_path,
                                                                       monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    path = _in_projects(home, "s1", [dict(_typed("u1", "hello"), cwd=w), _said("a1", _text("Hi."))])
    other = home / ".claude" / "projects" / "-elsewhere" / "s1.jsonl"
    other.parent.mkdir(parents=True)
    _transcript(str(other), [dict(_typed("u0", "earlier"), cwd=w)])
    assert run(capsys, "--dir", mac, "history")[0] == 0                # both copies read, both clean then
    with open(other, "a") as fh:                                         # the other copy touched since
        fh.write(json.dumps(_tool_use("a0", "Read", {"file_path": str(home / "grove" / "x.md")})) + "\n")
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == [
        HELD]


def test_with_no_history_json_the_project_s_own_mirror_still_posts_what_the_everywhere_one_held(
        client, owner, served, capsys, tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home = tmp_path / "home"
    (home / "work").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: None)
    w = str(home / "work")
    path = _in_projects(home, "s1", [dict(_typed("u1", "hello"), cwd=w), _said("a1", _text("Hi."))])
    _stop_in(monkeypatch, capsys, mac, path, "s1", w, "--listed-only")    # the everywhere block's: nothing
    assert _timeline(client, owner, "mac") == []
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)                    # the project's own, as an older release's
    assert [t for _, t in _timeline(client, owner, "mac")] == ["[owner] hello", "[mac] Hi."]


def test_a_transcript_outside_claude_code_s_projects_is_never_mirrored_and_starts_no_run_each_stop(
        client, owner, served, capsys, tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, started = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    path = str(tmp_path / "elsewhere.jsonl")
    entries = [dict(_typed("u1", "hello"), cwd=str(home / "work")), _said("a1", _text("Hi."))]
    _transcript(path, entries)
    for n in range(3):
        _transcript(path, entries + [_typed(f"v{n}", "more"), _said(f"b{n}", _text("More."))])
        _stop_in(monkeypatch, capsys, mac, path, "s1", str(home / "work"))
    assert _timeline(client, owner, "mac") == [] and len(started) <= 1
    assert json.loads(open(os.path.join(mac, "mirror.json")).read())["s1"] == "b2"   # passed over, not waited on


def test_a_session_the_history_list_withheld_once_is_held_by_the_mirror_too(client, owner, served, capsys, tmp_path,
                                                                           monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    path = _in_projects(home, "s1", [dict(_typed("u1", "hello"), cwd=w), _said("a1", _text("Hi."))])
    assert run(capsys, "--dir", mac, "history")[0] == 0
    with open(os.path.join(mac, "history_withheld.json"), "w") as fh:
        json.dump(["s1"], fh)                                            # withheld once, under a rule since changed
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == [
        HELD]



# ---- participants: told, locked, held, and their first Stop ----------------------------------------------------------

def test_a_participant_s_first_stop_judges_a_hook_s_output_before_its_first_prompt(client, owner, served, capsys,
                                                                                  tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    _participant(monkeypatch, capsys, mac, "s1")
    w = str(home / "work")
    path = _in_projects(home, "s1", [
        {"type": "attachment", "uuid": "h", "cwd": w, "attachment": {"type": "hook_success", "command": "today.sh",
                                                                     "stdout": "today: ~/grove/today.md says hire two"}},
        dict(_typed("u1", "what's on today?"), cwd=w), _said("a1", _text("Hire two."))])
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == [LEFT_OUT]


def test_a_participant_is_told_again_at_every_start_and_let_go_when_resume_fails(client, owner, served, capsys,
                                                                                tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    _participant(monkeypatch, capsys, mac, "s1")
    code, out, _ = _resume_with(monkeypatch, capsys, mac, {"session_id": "s1", "source": "compact"})
    assert code == 0 and out.startswith(TOLD)                          # told again after a compaction, first
    real = ade_link.Link.request
    monkeypatch.setattr(ade_link.Link, "request", lambda self, m, path, *a, **k: (_ for _ in ()).throw(
        ade_link.Unreachable("down")) if path == "/resume" else real(self, m, path, *a, **k))
    code, out, _ = _resume_with(monkeypatch, capsys, mac, {"session_id": "s1", "source": "resume"})
    assert code == 0 and "resume failed" in out and TOLD not in out
    assert "s1" not in json.loads(open(os.path.join(mac, "participants.json")).read())


def test_a_participants_lock_held_lets_no_one_in_and_holds_resume_a_second_at_most(client, owner, served, capsys,
                                                                                 tmp_path, monkeypatch):
    import fcntl
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"], "participants": True})
    fd = os.open(os.path.join(mac, "participants.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        began = time.monotonic()
        code, _, _ = _resume_with(monkeypatch, capsys, mac, {"session_id": "s1", "source": "startup"})
        assert code == 0 and time.monotonic() - began < 5
    finally:
        os.close(fd)
    assert not os.path.exists(os.path.join(mac, "participants.json"))


def test_a_session_told_it_is_held_or_where_no_rule_stands_never_joins(client, owner, served, capsys, tmp_path,
                                                                       monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    code, out, _ = _resume_with(monkeypatch, capsys, mac, {"session_id": "s1", "source": "startup"})
    assert code == 0 and TOLD not in out                                 # no history.json: nothing to be told
    _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"], "participants": True})
    with open(os.path.join(mac, "mirror_told.json"), "w") as fh:
        json.dump({"s2": 1}, fh)
    code, out, _ = _resume_with(monkeypatch, capsys, mac, {"session_id": "s2", "source": "startup"})
    assert TOLD not in out and not os.path.exists(os.path.join(mac, "participants.json"))
    code, out, _ = _resume_with(monkeypatch, capsys, mac, {"session_id": "s3", "source": "startup"})
    assert out.startswith(TOLD)                                           # one not told it holds, under that rule


def test_a_stop_whose_entries_another_stop_took_meanwhile_posts_nothing(client, owner, served, capsys, tmp_path,
                                                                       monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "hello"), _said("a1", _text("Hi."))])
    real = ade_link._mirror_held

    def meanwhile(link, *a, **k):
        with open(os.path.join(mac, "mirror.json"), "w") as fh:
            json.dump({"s1": "a1"}, fh)                                 # another Stop posted these and moved on
        return real(link, *a, **k)
    monkeypatch.setattr(ade_link, "_mirror_held", meanwhile)
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert _timeline(client, owner, "mac") == []


def test_a_first_stop_beside_one_that_held_for_want_of_a_rule_still_posts(client, owner, served, capsys, tmp_path,
                                                                         monkeypatch):
    """No history.json, the block for every folder and a project's own mirror at a session's first Stop at once: the
    first, holding, keeps the place from the start; the second, which may post, does."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    _transcript(path, [_typed("u1", "hello"), _said("a1", _text("Hi."))])
    real = ade_link._mirror_held

    def meanwhile(link, *a, **k):
        with open(os.path.join(mac, "mirror.json"), "w") as fh:
            json.dump({"s1": ade_link.MIRROR_FROM_START}, fh)          # the holding Stop committed first
        return real(link, *a, **k)
    monkeypatch.setattr(ade_link, "_mirror_held", meanwhile)
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert [t for _, t in _timeline(client, owner, "mac")] == ["[owner] hello", "[mac] Hi."]


def test_a_participant_s_transcript_outside_claude_code_s_projects_is_not_mirrored(client, owner, served, capsys,
                                                                                  tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    _participant(monkeypatch, capsys, mac, "s1")
    path = str(tmp_path / "elsewhere.jsonl")
    _transcript(path, [dict(_typed("u1", "hello"), cwd=str(home / "work")), _said("a1", _text("Hi."))])
    _stop_in(monkeypatch, capsys, mac, path, "s1", str(home / "work"))
    assert _timeline(client, owner, "mac") == []



# ---- a participant taken out: its sub-agents, its rule, and the told line --------------------------------------------

def test_a_participant_s_sub_agent_written_since_its_last_stop_takes_it_out(client, owner, served, capsys, tmp_path,
                                                                           monkeypatch):
    """A background sub-agent that read an excepted project after the last Stop judged, whose report a later turn
    relays without the source: judged by the time that Stop judged, not the later turn's own, and the session, which
    could not keep out what it never saw the source of, is held as any other from then on."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    _participant(monkeypatch, capsys, mac, "s1")
    w = str(home / "work")
    turn1 = [dict(_typed("u1", "start the agent"), cwd=w), _said("a1", _text("Started."))]
    path = _in_projects(home, "s1", turn1)
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert len(_timeline(client, owner, "mac")) == 2
    agent = home / ".claude" / "projects" / "-work" / "s1" / "subagents" / "agent-1.jsonl"
    agent.parent.mkdir(parents=True)
    _transcript(str(agent), [_tool_use("x1", "Read", {"file_path": str(home / "grove" / "x.md")})])
    later = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 120))     # after the agent last wrote
    _transcript(path, turn1 + [dict(_typed("u2", "what did it find?"), timestamp=later),
                               dict(_said("a2", _text("Revenue is up.")), timestamp=later)])
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert [t for _, t in _timeline(client, owner, "mac")][2:] == [
        HELD]
    assert "s1" not in json.loads(open(os.path.join(mac, "participants.json")).read())


def test_a_participant_is_taken_out_by_a_rule_it_was_not_told_or_a_start_with_no_rule(client, owner, served, capsys,
                                                                                     tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    _participant(monkeypatch, capsys, mac, "s1")
    _participant(monkeypatch, capsys, mac, "s2")
    (home / "other").mkdir()
    with open(os.path.join(mac, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove", "~/other"], "participants": True}, fh)   # one not told
    rule = ade_link._history_rule(ade_link.Link(mac))[0]
    assert not ade_link._participant(ade_link.Link(mac), "s1", rule)
    code, out, _ = _resume_with(monkeypatch, capsys, mac, {"session_id": "s1", "source": "compact"})
    assert code == 0 and TOLD not in out
    os.unlink(os.path.join(mac, "history.json"))
    code, out, _ = _resume_with(monkeypatch, capsys, mac, {"session_id": "s2", "source": "compact"})
    assert code == 0 and TOLD not in out
    assert json.loads(open(os.path.join(mac, "participants.json")).read()) == {}


def test_a_first_stop_while_the_ade_does_not_answer_keeps_the_session_s_place_from_the_start(
        client, owner, served, capsys, tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    entries = [_typed("u1", "first"), _said("a1", _text("One."))]
    _transcript(path, entries)
    with open(os.path.join(mac, "mirror_down"), "w") as fh:
        fh.write(str(int(time.time())))
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert json.loads(open(os.path.join(mac, "mirror.json")).read())["s1"] == ade_link.MIRROR_FROM_START
    os.unlink(os.path.join(mac, "mirror_down"))
    _transcript(path, entries + [_typed("u2", "second"), _said("a2", _text("Two."))])
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert [t for _, t in _timeline(client, owner, "mac")] == ["[owner] first", "[mac] One.", "[owner] second",
                                                               "[mac] Two."]


def test_the_told_line_writes_a_folder_s_name_as_text_this_client_prints(client, owner, served, capsys, tmp_path,
                                                                        monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    odd = "~/g" + chr(0x202E) + "tm"
    (home / ("g" + chr(0x202E) + "tm")).mkdir()
    with open(os.path.join(mac, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": [odd], "participants": True}, fh)
    code, out, _ = _resume_with(monkeypatch, capsys, mac, {"session_id": "s9", "source": "startup"})
    first = out.splitlines()[0]
    assert code == 0 and first.startswith(TOLD) and chr(0x202E) not in first and "\\u202e" in first


def test_a_participant_s_earlier_style_sub_agent_takes_it_out_and_another_session_s_does_not(client, owner, served,
                                                                                             capsys, tmp_path,
                                                                                             monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    _participant(monkeypatch, capsys, mac, "s1")
    w = str(home / "work")
    turn1 = [dict(_typed("u1", "go"), cwd=w), _said("a1", _text("One."))]
    path = _in_projects(home, "s1", turn1)
    folder = home / ".claude" / "projects" / "-work"
    read = dict(_tool_use("x1", "Read", {"file_path": str(home / "grove" / "x.md")}), isSidechain=True, cwd=w)
    _transcript(str(folder / "agent-b1.jsonl"), [dict(read, sessionId="s2")])        # another session's
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == ["[owner] go", "[mac] One."]
    _transcript(str(folder / "agent-a1.jsonl"), [dict(read, sessionId="s1")])        # its own, written since
    _transcript(path, turn1 + [dict(_typed("u2", "more"), cwd=w), _said("a2", _text("Two."))])
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert [t for _, t in _timeline(client, owner, "mac")][2:] == [
        HELD]
    assert "s1" not in json.loads(open(os.path.join(mac, "participants.json")).read())


def test_a_session_s_earlier_style_sub_agent_holds_its_turn_whenever_it_was_written(client, owner, served, capsys,
                                                                                   tmp_path, monkeypatch):
    """Not a participant: the session is judged whole, its earlier Claude Code's sub-agents among it, the one written
    before the turn began and before the History list read it too."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w, now = str(home / "work"), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path = _in_projects(home, "s1", [dict(_typed("u1", "go"), cwd=w, timestamp=now),
                                     dict(_said("a1", _text("One.")), timestamp=now)])
    assert run(capsys, "--dir", mac, "history")[0] == 0                  # read before the sub-agent's file was there
    agent = home / ".claude" / "projects" / "-work" / "agent-a1.jsonl"
    _transcript(str(agent), [dict(_tool_use("x1", "Read", {"file_path": str(home / "grove" / "x.md")}), sessionId="s1",
                                  isSidechain=True, cwd=w)])
    old = time.time() - 3600
    os.utime(agent, (old, old))                                          # written an hour before the turn
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    held = HELD
    assert [t for _, t in _timeline(client, owner, "mac")] == [held]
    agent.unlink()                                                       # Claude Code's clean-up: only the mark is left
    later = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 120))
    _transcript(path, [dict(_typed("u1", "go"), cwd=w, timestamp=now), dict(_said("a1", _text("One.")), timestamp=now),
                       dict(_typed("u2", "and now?"), cwd=w, timestamp=later),
                       dict(_said("a2", _text("Revenue is up.")), timestamp=later)])
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == [held]      # told it holds, it stays held


def test_a_participant_held_once_is_no_participant_though_its_mark_could_not_be_taken_out(client, owner, served,
                                                                                         capsys, tmp_path, monkeypatch):
    """participants.json's lock held elsewhere when the Stop takes the session out: told its turns are no longer
    mirrored, it is judged as any other session after, whatever participants.json still says."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    _participant(monkeypatch, capsys, mac, "s1")
    w, now = str(home / "work"), "2026-10-03T10:00:00Z"                  # before the sub-agent wrote
    turn1 = [dict(_typed("u1", "find it"), cwd=w, timestamp=now), dict(_said("a1", _text("Found it.")), timestamp=now)]
    path = _in_projects(home, "s1", turn1)
    agent = home / ".claude" / "projects" / "-work" / "s1" / "subagents" / "agent-1.jsonl"
    agent.parent.mkdir(parents=True)
    _transcript(str(agent), [_tool_use("x1", "Read", {"file_path": str(home / "grove" / "x.md")})])
    old = time.time() - 3600
    os.utime(agent, (old, old))                                          # so a later Stop does not judge it again
    monkeypatch.setattr(ade_link, "_note_participant", lambda link, sid, keep=True, told=None: False)
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    note = HELD
    assert [t for _, t in _timeline(client, owner, "mac")] == [note]
    assert "s1" in json.loads(open(os.path.join(mac, "participants.json")).read())     # its mark stayed
    agent.unlink()                                                       # Claude Code's clean-up, since
    assert run(capsys, "--dir", mac, "history")[0] == 0
    later = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 120))         # after the sub-agent wrote
    _transcript(path, turn1 + [dict(_typed("u2", "and now?"), cwd=w, timestamp=later),
                               dict(_said("a2", _text("Revenue is up.")), timestamp=later)])
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == [note]


# ---- the told line, the marks, and sub-agent files that change or go -------------------------------------------------



def test_a_told_line_that_cannot_name_every_excepted_folder_makes_no_participant(client, owner, served, capsys,
                                                                                 tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    many = ["~/grove"] + [f"~/clients/acme-industries-confidential-{n:02d}" for n in range(16)]
    for f in many[1:]:
        (home / f[2:]).mkdir(parents=True)
    with open(os.path.join(mac, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": many, "participants": True}, fh)
    assert len(", ".join(many)) > ade_link.TOLD_MOST
    code, out, _ = _resume_with(monkeypatch, capsys, mac, {"session_id": "s1", "source": "startup"})
    assert code == 0 and TOLD not in out
    people = os.path.join(mac, "participants.json")
    assert "s1" not in (json.loads(open(people).read()) if os.path.exists(people) else {})
    link = ade_link.Link(mac)
    rule = ade_link._history_rule(link)[0]
    with open(people, "w") as fh:                                    # a mark for every folder, however it came
        json.dump({"s3": {"at": 0, "told": ade_link._told_mark(rule)}}, fh)
    assert not ade_link._participant(link, "s3", rule)


def test_a_participant_s_first_stop_judges_every_sub_agent_of_its_session(client, owner, served, capsys, tmp_path,
                                                                         monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    _participant(monkeypatch, capsys, mac, "s1")
    w, now = str(home / "work"), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path = _in_projects(home, "s1", [dict(_typed("u1", "go"), cwd=w, timestamp=now),
                                     dict(_said("a1", _text("Done.")), timestamp=now)])
    agent = home / ".claude" / "projects" / "-work" / "s1" / "subagents" / "agent-1.jsonl"
    agent.parent.mkdir(parents=True)
    _transcript(str(agent), [_tool_use("x1", "Read", {"file_path": str(home / "grove" / "x.md")})])
    old = time.time() - 3600
    os.utime(agent, (old, old))                                          # older than anything the Stop reads
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == [HELD]


@pytest.mark.parametrize("how", ["clone", "moved_into_clone", "link", "search_above", "told"])
def test_a_participant_that_touches_an_excepted_project_by_a_way_it_was_not_told_is_taken_out(
        client, owner, served, capsys, tmp_path, monkeypatch, how):
    from test_ade_link_history import _clone
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    _clone(str(home / "grove"), "https://github.com/acme/grove.git")
    _clone(str(home / "src" / "q3-notes"), "https://github.com/acme/grove.git")       # a clone by another name
    (home / "alias").symlink_to(home / "grove")
    _participant(monkeypatch, capsys, mac, "s1")
    w = str(home / "work")
    action = {"clone": _tool_use("a1", "Read", {"file_path": str(home / "src" / "q3-notes" / "plan.md")}),
              "moved_into_clone": dict(_said("a1", _text("Working there.")), cwd=str(home / "src" / "q3-notes")),
              "link": _tool_use("a1", "Read", {"file_path": str(home / "alias" / "plan.md")}),
              "search_above": _tool_use("a1", "Grep", {"pattern": "revenue", "path": str(home)}),
              "told": _tool_use("a1", "Grep", {"pattern": "revenue", "path": str(home / "grove")})}[how]
    path = _in_projects(home, "s1", [dict(_typed("u1", "look"), cwd=w), action, _said("a2", _text("Looked."))])
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    people = json.loads(open(os.path.join(mac, "participants.json")).read())
    if how == "told":                                                    # a folder it was told: that turn alone
        assert [t for _, t in _timeline(client, owner, "mac")] == [LEFT_OUT]
        assert "s1" in people
    else:
        assert [t for _, t in _timeline(client, owner, "mac")] == [HELD] and "s1" not in people


def test_an_unreadable_mirror_told_json_holds_every_session_and_is_never_written_over(client, owner, served, capsys,
                                                                                     tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    _participant(monkeypatch, capsys, mac, "s1")
    told = os.path.join(mac, "mirror_told.json")
    with open(told, "w") as fh:
        fh.write('{"s0": 1')                                             # cut
    link = ade_link.Link(mac)
    rule = ade_link._history_rule(link)[0]
    assert ade_link._told_marks(link) is None and not ade_link._participant(link, "s1", rule)
    assert not ade_link._may_join(link, {"session_id": "s9", "source": "startup"}, rule)
    w = str(home / "work")
    os.unlink(told)
    clean = _in_projects(home, "s2", [dict(_typed("u1", "go"), cwd=w), _said("a1", _text("Hi."))])
    assert run(capsys, "--dir", mac, "history")[0] == 0                  # read whole while the file was readable
    with open(told, "w") as fh:
        fh.write('{"s0": 1')
    _stop_in(monkeypatch, capsys, mac, clean, "s2", w)
    assert _timeline(client, owner, "mac") == [] and open(told).read() == '{"s0": 1'
    code, out, _ = run(capsys, "--dir", mac, "history")
    assert code == 2 and "mirror_told.json cannot be read" in out
    os.unlink(told)
    with open(os.path.join(mac, "elsewhere.json"), "w") as fh:
        fh.write("{}")
    os.symlink(os.path.join(mac, "elsewhere.json"), told)               # a link is not followed
    assert ade_link._told_marks(link) is None


def test_a_mark_is_kept_before_the_place_so_a_stopped_hook_leaves_the_session_told(client, owner, served, capsys,
                                                                                   tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    path = _in_projects(home, "s1", [dict(_typed("u1", "go"), cwd=w),
                                     _tool_use("a1", "Read", {"file_path": str(home / "grove" / "x.md")})])
    assert run(capsys, "--dir", mac, "history")[0] == 0
    real, seen = ade_link.Link.keep, []

    def stopped(self, name, value):
        if name in ("mirror.json", "mirror_told.json"):
            seen.append(name)
            if len(seen) == 2:
                raise KeyboardInterrupt("the hook stopped between its two writes")
        return real(self, name, value)
    monkeypatch.setattr(ade_link.Link, "keep", stopped)
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert seen == ["mirror_told.json", "mirror.json"]
    assert "s1" in json.loads(open(os.path.join(mac, "mirror_told.json")).read())
    _transcript(path, [dict(_typed("u1", "go"), cwd=w),
                       _tool_use("a1", "Read", {"file_path": str(home / "grove" / "x.md")}),
                       dict(_typed("u2", "and now?"), cwd=w), _said("a2", _text("Revenue is up."))])
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)                   # the next Stop: held, told, nothing more
    assert _timeline(client, owner, "mac") == []


def test_a_first_stop_while_the_ade_does_not_answer_keeps_its_place_before_the_turn_not_the_session_s_start(
        client, owner, served, capsys, tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    path = str(tmp_path / "t.jsonl")
    earlier = [_typed("u1", "first"), _said("a1", _text("One.")), _typed("u2", "second"), _said("a2", _text("Two."))]
    _transcript(path, earlier)
    with open(os.path.join(mac, "mirror_down"), "w") as fh:
        fh.write(str(int(time.time())))
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert json.loads(open(os.path.join(mac, "mirror.json")).read())["s1"] == "a1"
    os.unlink(os.path.join(mac, "mirror_down"))
    _transcript(path, earlier + [_typed("u3", "third"), _said("a3", _text("Three."))])
    assert _stop(monkeypatch, capsys, mac, path) == (0, "", "")
    assert [t for _, t in _timeline(client, owner, "mac")] == ["[owner] second", "[mac] Two.", "[owner] third",
                                                               "[mac] Three."]


def test_a_stop_opens_no_earlier_sub_agent_file_the_history_list_read_as_it_is(client, owner, served, capsys,
                                                                                       tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    turn1 = [dict(_typed("u1", "go"), cwd=w), _said("a1", _text("One."))]
    path = _in_projects(home, "s1", turn1)
    folder = home / ".claude" / "projects" / "-work"
    old = time.time() - 3600
    for n in range(5):
        _transcript(str(folder / f"agent-{n}.jsonl"), [dict(_said(f"x{n}", _text("fine")), sessionId="s1",
                                                            isSidechain=True, cwd=w)])
        os.utime(folder / f"agent-{n}.jsonl", (old, old))
    assert run(capsys, "--dir", mac, "history")[0] == 0
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)                   # the first judging reads every one
    assert len(_timeline(client, owner, "mac")) == 2
    opened, real = [], ade_link._open_transcript
    monkeypatch.setattr(ade_link, "_open_transcript", lambda p: opened.append(os.path.basename(p)) or real(p))
    _transcript(path, turn1 + [dict(_typed("u2", "more"), cwd=w), _said("a2", _text("Two."))])
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert len(_timeline(client, owner, "mac")) == 4 and not [p for p in opened if p.startswith("agent-")]


def test_a_session_begun_in_a_folder_whose_path_names_an_excepted_project_is_never_mirrored(client, owner, served,
                                                                                           capsys, tmp_path,
                                                                                           monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    copy = home / "Desktop" / "grove-copy"                                 # a copy without its .git, under its name
    copy.mkdir(parents=True)
    path = _in_projects(home, "s1", [dict(_typed("u1", "plans"), cwd=str(copy)), _said("a1", _text("Here."))])
    assert run(capsys, "--dir", mac, "history")[0] == 0
    _stop_in(monkeypatch, capsys, mac, path, "s1", str(copy))
    assert _timeline(client, owner, "mac") == []


def test_a_mark_file_that_cannot_be_read_when_a_mark_is_kept_is_never_written_over(client, owner, served, capsys,
                                                                                    tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    path = _in_projects(home, "s1", [dict(_typed("u1", "go"), cwd=w),
                                     _tool_use("a1", "Read", {"file_path": str(home / "grove" / "x.md")})])
    assert run(capsys, "--dir", mac, "history")[0] == 0
    told = os.path.join(mac, "mirror_told.json")
    real, calls = ade_link._told_marks, []

    def flaky(link):
        calls.append(1)
        if len(calls) == 1:
            return real(link)                                            # readable while the turn is judged
        with open(told, "w") as fh:
            fh.write('{"s0": 1')                                         # cut by the time the mark is kept
        return real(link)
    monkeypatch.setattr(ade_link, "_told_marks", flaky)
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert open(told).read() == '{"s0": 1' and len(calls) >= 2


def test_a_sub_agent_file_gone_while_a_stop_judges_is_not_one_that_touched(client, owner, served, capsys, tmp_path,
                                                                           monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    _participant(monkeypatch, capsys, mac, "s1")
    w = str(home / "work")
    path = _in_projects(home, "s1", [dict(_typed("u1", "go"), cwd=w), _said("a1", _text("Done."))])
    agent = home / ".claude" / "projects" / "-work" / "s1" / "subagents" / "agent-1.jsonl"
    agent.parent.mkdir(parents=True)
    _transcript(str(agent), [_said("x1", _text("fine"))])
    real = ade_link._scan

    def cleaned(p, *a, **k):
        if p.endswith("agent-1.jsonl") and os.path.exists(p):
            os.unlink(p)                                                 # Claude Code's clean-up, mid-judging
        return real(p, *a, **k)
    monkeypatch.setattr(ade_link, "_scan", cleaned)
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == ["[owner] go", "[mac] Done."]


# ---- the participant exception asked for by history.json -----------------------------------------------------------

def test_without_participants_in_history_json_a_session_s_very_start_makes_it_none(client, owner, served, capsys,
                                                                                    tmp_path, monkeypatch):
    """The exception rests on the session's care, not on what this client can read: by default a session that read an
    excepted project is held from then on, however it began."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"], "participants": False})
    code, out, _ = _resume_with(monkeypatch, capsys, mac, {"session_id": "s1", "source": "startup"})
    assert code == 0 and TOLD not in out
    people = os.path.join(mac, "participants.json")
    assert "s1" not in (json.loads(open(people).read()) if os.path.exists(people) else {})
    w = str(home / "work")
    turn1 = [dict(_typed("u1", "go"), cwd=w), _tool_use("a1", "Read", {"file_path": str(home / "grove" / "x.md")}),
             _said("a2", _text("Read it."))]
    path = _in_projects(home, "s1", turn1)
    assert run(capsys, "--dir", mac, "history")[0] == 0
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    _transcript(path, turn1 + [dict(_typed("u2", "something else"), cwd=w), _said("a3", _text("Sure."))])
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == [HELD]


def test_a_participant_s_mark_holds_only_while_history_json_asks_for_participants(client, owner, served, capsys,
                                                                                   tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    _participant(monkeypatch, capsys, mac, "s1")
    with open(os.path.join(mac, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove"]}, fh)
    link = ade_link.Link(mac)
    assert not ade_link._participant(link, "s1", ade_link._history_rule(link)[0])
    code, out, _ = _resume_with(monkeypatch, capsys, mac, {"session_id": "s1", "source": "compact"})
    assert code == 0 and TOLD not in out
    assert "s1" not in json.loads(open(os.path.join(mac, "participants.json")).read())


# ---- a session outside the projects listed, and a first Stop while the ADE does not answer ---------------------------

def test_a_participant_s_first_stop_while_the_ade_does_not_answer_still_judges_all_it_read(client, owner, served,
                                                                                           capsys, tmp_path,
                                                                                           monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    _participant(monkeypatch, capsys, mac, "s1")
    w = str(home / "work")
    first = [{"type": "attachment", "uuid": "h", "cwd": w, "attachment": {"type": "hook_success", "command": "today.sh",
                                                                          "stdout": "today: ~/grove/today.md says hire two"}},
             dict(_typed("u1", "what's on today?"), cwd=w), _said("a1", _text("Hire two."))]
    path = _in_projects(home, "s1", first)
    with open(os.path.join(mac, "mirror_down"), "w") as fh:
        fh.write(str(int(time.time())))
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert json.loads(open(os.path.join(mac, "mirror.json")).read())["s1"] == ade_link.MIRROR_FROM_START
    os.unlink(os.path.join(mac, "mirror_down"))
    _transcript(path, first + [dict(_typed("u2", "and tomorrow?"), cwd=w), _said("a2", _text("Same."))])
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == [LEFT_OUT]


@pytest.mark.parametrize("how", ["sub_agent", "own_entry"])
def test_a_participant_outside_the_projects_listed_is_judged_whole_once_listed(client, owner, served, capsys, tmp_path,
                                                                              monkeypatch, how):
    """Held, not mirrored, while history.json does not list where it works: its place stays, and every Stop reads every
    sub-agent, so once listed nothing it did meanwhile is passed over."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": ["~/work"], "except": ["~/grove"]})
    (home / "scratch").mkdir()
    _participant(monkeypatch, capsys, mac, "s1")
    sc = str(home / "scratch")
    if how == "own_entry":                                               # a search over the home folder: untold
        turn1 = [dict(_typed("u1", "find it"), cwd=sc), _tool_use("a1", "Grep", {"pattern": "plan", "path": str(home)}),
                 _said("a1b", _text("Found it."))]
    else:
        turn1 = [dict(_typed("u1", "find it"), cwd=sc), _said("a1", _text("Asked a helper."))]
        agent = home / ".claude" / "projects" / "-work" / "s1" / "subagents" / "agent-1.jsonl"
        agent.parent.mkdir(parents=True)
        _transcript(str(agent), [_tool_use("x1", "Read", {"file_path": str(home / "grove" / "plan.md")})])
        old = time.time() - 3600
        os.utime(agent, (old, old))
    path = _in_projects(home, "s1", turn1)
    _stop_in(monkeypatch, capsys, mac, path, "s1", sc)
    assert _timeline(client, owner, "mac") == []                        # not where history.json lists
    with open(os.path.join(mac, "history.json"), "w") as fh:
        json.dump({"projects": ["~/work", "~/scratch"], "except": ["~/grove"], "participants": True}, fh)
    _transcript(path, turn1 + [dict(_typed("u2", "and?"), cwd=sc), _said("a2", _text("Hire two."))])
    _stop_in(monkeypatch, capsys, mac, path, "s1", sc)
    assert [t for _, t in _timeline(client, owner, "mac")] == [HELD]


def test_a_session_outside_the_projects_listed_has_its_earlier_sub_agents_read_once_listed(client, owner, served,
                                                                                          capsys, tmp_path,
                                                                                          monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": ["~/work"], "except": ["~/grove"], "participants": False})
    (home / "scratch").mkdir()
    sc = str(home / "scratch")
    turn1 = [dict(_typed("u1", "go"), cwd=sc), _said("a1", _text("One."))]
    path = _in_projects(home, "s1", turn1)
    assert run(capsys, "--dir", mac, "history")[0] == 0
    agent = home / ".claude" / "projects" / "-work" / "agent-a1.jsonl"
    _transcript(str(agent), [dict(_tool_use("x1", "Read", {"file_path": str(home / "grove" / "plan.md")}),
                                  sessionId="s1", isSidechain=True, cwd=sc)])
    old = time.time() - 3600
    os.utime(agent, (old, old))
    _stop_in(monkeypatch, capsys, mac, path, "s1", sc)
    assert _timeline(client, owner, "mac") == []
    with open(os.path.join(mac, "history.json"), "w") as fh:
        json.dump({"projects": ["~/work", "~/scratch"], "except": ["~/grove"]}, fh)
    _transcript(path, turn1 + [dict(_typed("u2", "and?"), cwd=sc), _said("a2", _text("Hire two."))])
    _stop_in(monkeypatch, capsys, mac, path, "s1", sc)
    assert [t for _, t in _timeline(client, owner, "mac")] == [HELD]


# ---- the history list's reading, a rule that changes, and forks ------------------------------------------------------

def test_a_stop_trusts_the_history_list_s_reading_only_from_a_run_that_kept_what_it_withholds(client, owner, served,
                                                                                             capsys, tmp_path,
                                                                                             monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    path = _in_projects(home, "s1", [dict(_typed("u1", "go"), cwd=w), _said("a1", _text("One."))])
    assert run(capsys, "--dir", mac, "history")[0] == 0
    where = os.path.join(mac, "history_cache.json")
    data = json.loads(open(where).read())
    assert data["whole"] is True
    with open(where, "w") as fh:                                         # as a run under way keeps it
        json.dump(dict(data, whole=False), fh)
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert _timeline(client, owner, "mac") == []
    assert run(capsys, "--dir", mac, "history")[0] == 0
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == ["[owner] go", "[mac] One."]


def test_an_earlier_sub_agent_read_under_one_rule_is_read_again_under_the_next(client, owner, served, capsys, tmp_path,
                                                                             monkeypatch):
    """A folder excepted after a Stop read the sub-agent clean: the next Stop reads it again under the new rule, even
    while a History run under way has kept the session's own transcript under it, before what it withholds."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    (home / "clients" / "acme").mkdir(parents=True)
    w = str(home / "work")
    turn1 = [dict(_typed("u1", "go"), cwd=w), _said("a1", _text("One."))]
    path = _in_projects(home, "s1", turn1)
    agent = home / ".claude" / "projects" / "-work" / "agent-a1.jsonl"
    _transcript(str(agent), [dict(_tool_use("x1", "Read", {"file_path": str(home / "clients" / "acme" / "plan.md")}),
                                  sessionId="s1", isSidechain=True, cwd=w)])
    old = time.time() - 3600
    os.utime(agent, (old, old))
    assert run(capsys, "--dir", mac, "history")[0] == 0
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)                   # nothing excepted it touched yet
    assert [t for _, t in _timeline(client, owner, "mac")] == ["[owner] go", "[mac] One."]
    old_rule = ade_link._history_rule(ade_link.Link(mac))[0]
    with open(os.path.join(mac, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove", "~/clients/acme"]}, fh)
    rule, real = ade_link._history_rule(ade_link.Link(mac))[0], os.path.realpath(path)
    _transcript(path, turn1 + [dict(_typed("u2", "and?"), cwd=w), _said("a2", _text("Two."))])
    under_way = {real: ade_link._scan(real, None, rule),                 # the session read under the new rule
                 os.path.realpath(agent): ade_link._scan(str(agent), None, old_rule)}   # its sub-agent, the old one
    with open(os.path.join(mac, "history_cache.json"), "w") as fh:
        json.dump({"sessions": under_way, "whole": False}, fh)
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert [t for _, t in _timeline(client, owner, "mac")][2:] == [HELD]


@pytest.mark.parametrize("how", ["in_window", "cache_only", "first_entry"])
def test_a_session_whose_transcript_holds_a_held_session_s_entries_is_held_with_it(client, owner, served, capsys,
                                                                                   tmp_path, monkeypatch, how):
    """A fork, or a copy a resume made, carries what its source said: told its turns are no longer mirrored, the source
    holds the fork's too, though no History run has read either since: known by the source's id in what the Stop reads,
    or in the History list's reading of the fork, or by their same first entry."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    source = [dict(_typed("u1", "what does it say?"), cwd=w, sessionId="s1"),
              dict(_said("a1", _text("Hire two.")), sessionId="s1")]
    _in_projects(home, "s1", source)
    copied = {"in_window": source, "cache_only": [dict(e, uuid=e["uuid"] + "-copy") for e in source],
              "first_entry": [dict(e, sessionId="s2") for e in source]}[how]
    turns = copied + [dict(_typed("u2", "and then?"), cwd=w, sessionId="s2"),
                      dict(_said("a2", _text("Hire two more.")), sessionId="s2")]
    if how == "cache_only":
        monkeypatch.setattr(ade_link, "MIRROR_WINDOW", 400)              # what the Stop reads holds s2's alone
    if how != "in_window":
        fork = _in_projects(home, "s2", turns)
    assert run(capsys, "--dir", mac, "history")[0] == 0
    if how == "in_window":
        fork = _in_projects(home, "s2", [dict(e, uuid=e["uuid"] + "-copy") for e in copied] + turns[2:])
    with open(os.path.join(mac, "mirror_told.json"), "w") as fh:
        json.dump({"s1": int(time.time())}, fh)                          # s1 held since, by a Stop of its own
    _stop_in(monkeypatch, capsys, mac, fork, "s2", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == [touched_note("s2")]


def test_with_no_history_json_a_session_withheld_or_told_stays_held(client, owner, served, capsys, tmp_path,
                                                                    monkeypatch):
    """With no history.json, one held for good stays held (and a link that had one holds every other turn too, below)."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    path = _in_projects(home, "s1", [dict(_typed("u1", "go"), cwd=w),
                                     _tool_use("a1", "Read", {"file_path": str(home / "grove" / "x.md")}),
                                     _said("a2", _text("Hire two."))])
    assert run(capsys, "--dir", mac, "history")[0] == 0
    os.unlink(os.path.join(mac, "history.json"))
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == [HELD]
    with open(os.path.join(mac, "mirror_told.json"), "w") as fh:
        json.dump({"s1": 1, "s7": 1}, fh)
    told = _in_projects(home, "s7", [dict(_typed("v1", "go"), cwd=w), _said("b1", _text("Fine."))])
    _stop_in(monkeypatch, capsys, mac, told, "s7", w)                    # told it holds, under some rule before
    assert json.loads(open(os.path.join(mac, "mirror.json")).read())["s7"] == "b1"   # held as told, not waiting
    fork = _in_projects(home, "s8", [dict(_typed("w1", "go"), cwd=w, sessionId="s1"),
                                     dict(_said("c1", _text("Hire two.")), sessionId="s1"),
                                     dict(_typed("w2", "more"), cwd=w, sessionId="s8"),
                                     dict(_said("c2", _text("More.")), sessionId="s8")])
    monkeypatch.setattr(ade_link, "MIRROR_WINDOW", 300)                  # what the Stop reads is s8's own
    _stop_in(monkeypatch, capsys, mac, fork, "s8", w)                    # a fork of a held session
    assert [t for _, t in _timeline(client, owner, "mac")] == [HELD, touched_note("s8")]


def test_a_participant_s_mark_is_of_the_rule_it_was_told_under(client, owner, served, capsys, tmp_path, monkeypatch):
    from test_ade_link_history import _clone
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    _participant(monkeypatch, capsys, mac, "s1")
    link = ade_link.Link(mac)
    assert ade_link._participant(link, "s1", ade_link._history_rule(link)[0])
    _clone(str(home / "grove"), "https://github.com/acme/plans-2026.git")   # a remote, and with it a name, added
    assert not ade_link._participant(link, "s1", ade_link._history_rule(link)[0])


def test_a_session_marked_a_participant_keeps_its_place_from_the_start_while_the_ade_is_down(client, owner, served,
                                                                                            capsys, tmp_path,
                                                                                            monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    _participant(monkeypatch, capsys, mac, "s1")
    w = str(home / "work")
    path = _in_projects(home, "s1", [dict(_typed("u0", "zero"), cwd=w), _said("a0", _text("Zero.")),
                                     dict(_typed("u1", "one"), cwd=w), _said("a1", _text("One."))])
    with open(os.path.join(mac, "mirror_told.json"), "w") as fh:
        fh.write("{cut")                                                 # what makes a participant cannot be read now
    with open(os.path.join(mac, "mirror_down"), "w") as fh:
        fh.write(str(int(time.time())))
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert json.loads(open(os.path.join(mac, "mirror.json")).read())["s1"] == ade_link.MIRROR_FROM_START


def test_a_participant_s_transcript_outside_claude_code_s_projects_does_not_keep_its_place(client, owner, served,
                                                                                          capsys, tmp_path,
                                                                                          monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    _participant(monkeypatch, capsys, mac, "s1")
    path = str(tmp_path / "elsewhere.jsonl")
    _transcript(path, [dict(_typed("u1", "hello"), cwd=str(home / "work")), _said("a1", _text("Hi."))])
    _stop_in(monkeypatch, capsys, mac, path, "s1", str(home / "work"))
    assert _timeline(client, owner, "mac") == []
    assert json.loads(open(os.path.join(mac, "mirror.json")).read())["s1"] == "a1"   # never read: not kept for later


def test_a_fork_s_stop_reads_the_sub_agents_of_the_session_it_came_from(client, owner, served, capsys, tmp_path,
                                                                        monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    source = [dict(_typed("u1", "summarise the plan"), cwd=w, sessionId="s1"),
              dict(_said("a1", _text("Asked a helper.")), sessionId="s1")]
    _in_projects(home, "s1", source)
    fork = _in_projects(home, "s2", source + [dict(_typed("u2", "and?"), cwd=w, sessionId="s2"),
                                              dict(_said("a2", _text("Hire two.")), sessionId="s2")])
    assert run(capsys, "--dir", mac, "history")[0] == 0
    agent = home / ".claude" / "projects" / "-work" / "s1" / "subagents" / "agent-1.jsonl"
    agent.parent.mkdir(parents=True)                                     # the source's helper, read since
    _transcript(str(agent), [_tool_use("x1", "Read", {"file_path": str(home / "grove" / "plan.md")})])
    _stop_in(monkeypatch, capsys, mac, fork, "s2", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == [touched_note("s2")]


def test_a_participant_whose_sub_agents_folder_is_a_link_is_held_as_any_other(client, owner, served, capsys, tmp_path,
                                                                             monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    _participant(monkeypatch, capsys, mac, "s1")
    w = str(home / "work")
    path = _in_projects(home, "s1", [dict(_typed("u1", "go"), cwd=w), _said("a1", _text("Done."))])
    (tmp_path / "elsewhere").mkdir()
    os.symlink(tmp_path / "elsewhere", home / ".claude" / "projects" / "-work" / "s1")   # what is there cannot be checked
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == [HELD]


def test_a_session_the_last_whole_run_did_not_read_waits_for_the_next(client, owner, served, capsys, tmp_path,
                                                                      monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    _in_projects(home, "s0", [dict(_typed("u0", "earlier"), cwd=w)])
    assert run(capsys, "--dir", mac, "history")[0] == 0                  # a whole run, before s1 was there
    path = _in_projects(home, "s1", [dict(_typed("u1", "go"), cwd=w), _said("a1", _text("One."))])
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert _timeline(client, owner, "mac") == []
    assert run(capsys, "--dir", mac, "history")[0] == 0
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == ["[owner] go", "[mac] One."]


# ---- forks of a held session, and earlier sub-agents read once -------------------------------------------------------

def test_a_session_two_forks_from_a_held_one_is_held_with_it(client, owner, served, capsys, tmp_path, monkeypatch):
    """s2 is s1 rewritten but for the same first entry, and s3 holds s2's entries by its id: told since that it holds,
    s1 holds s3 too, though no History run has read any of them since."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    one = [dict(_typed("u1", "start"), cwd=w, sessionId="s1"), dict(_said("a1", _text("Started.")), sessionId="s1")]
    _in_projects(home, "s1", one)
    two = [dict(e, sessionId="s2") for e in one] + [dict(_typed("u2", "next"), cwd=w, sessionId="s2")]
    _in_projects(home, "s2", two)
    three = _in_projects(home, "s3", [dict(e, uuid=e["uuid"] + "-3") for e in two]
                         + [dict(_typed("u3", "and?"), cwd=w, sessionId="s3"),
                            dict(_said("a3", _text("Hire two.")), sessionId="s3")])
    assert run(capsys, "--dir", mac, "history")[0] == 0
    with open(os.path.join(mac, "mirror_told.json"), "w") as fh:
        json.dump({"s1": int(time.time())}, fh)
    _stop_in(monkeypatch, capsys, mac, three, "s3", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == [touched_note("s3")]


def test_a_stop_reads_a_project_folder_s_earlier_sub_agents_once(client, owner, served, capsys, tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    source = [dict(_typed("u1", "go"), cwd=w, sessionId="s1"), dict(_said("a1", _text("One.")), sessionId="s1")]
    _in_projects(home, "s1", source)
    fork = _in_projects(home, "s2", source + [dict(_typed("u2", "more"), cwd=w, sessionId="s2"),
                                              dict(_said("a2", _text("Two.")), sessionId="s2")])
    assert run(capsys, "--dir", mac, "history")[0] == 0
    looked, real = [], ade_link._old_agents
    monkeypatch.setattr(ade_link, "_old_agents", lambda p: looked.append(os.path.dirname(p)) or real(p))
    _stop_in(monkeypatch, capsys, mac, fork, "s2", w)
    assert looked and len(looked) == len(set(looked))                    # s2's folder and s1's are one: read once


def test_a_participant_outside_the_projects_listed_keeps_its_place_whatever_can_be_read_just_now(client, owner, served,
                                                                                               capsys, tmp_path,
                                                                                               monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": ["~/work"], "except": ["~/grove"]})
    (home / "scratch").mkdir()
    _participant(monkeypatch, capsys, mac, "s1")
    sc = str(home / "scratch")
    path = _in_projects(home, "s1", [dict(_typed("u1", "go"), cwd=sc), _said("a1", _text("One."))])
    with open(os.path.join(mac, "mirror_told.json"), "w") as fh:
        fh.write("{cut")
    _stop_in(monkeypatch, capsys, mac, path, "s1", sc)
    assert json.loads(open(os.path.join(mac, "mirror.json")).read())["s1"] == ade_link.MIRROR_FROM_START


def test_a_session_is_not_held_for_what_a_fork_of_it_read_after_it_was_made(client, owner, served, capsys, tmp_path,
                                                                           monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    source = [dict(_typed("u1", "go"), cwd=w, sessionId="s1"), dict(_said("a1", _text("One.")), sessionId="s1")]
    path = _in_projects(home, "s1", source)
    _in_projects(home, "s2", source + [dict(_tool_use("a2", "Read", {"file_path": str(home / "grove" / "x.md")}),
                                            sessionId="s2")])
    assert run(capsys, "--dir", mac, "history")[0] == 0
    with open(os.path.join(mac, "mirror_told.json"), "w") as fh:
        json.dump({"s2": int(time.time())}, fh)                          # the fork, held since by a Stop of its own
    _transcript(path, source + [dict(_typed("u3", "more"), cwd=w, sessionId="s1"),
                                dict(_said("a3", _text("Two.")), sessionId="s1")])
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == ["[owner] more", "[mac] Two."]


# ---- history.json gone a while, and forks and copies of a held session -----------------------------------------------

def test_once_a_link_had_a_history_json_turns_said_while_it_is_gone_wait_for_it(client, owner, served, capsys,
                                                                                tmp_path, monkeypatch):
    """history.json gone a moment (an editor saving it): the turns said meanwhile wait, their place kept, and are judged
    by it once it is back, as ever, at once."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    first = {sid: [dict(_typed(f"u{sid}", "go"), cwd=w), _said(f"a{sid}", _text("One."))] for sid in ("s1", "s2")}
    paths = {sid: _in_projects(home, sid, first[sid]) for sid in first}
    assert run(capsys, "--dir", mac, "history")[0] == 0
    for sid in paths:
        _stop_in(monkeypatch, capsys, mac, paths[sid], sid, w)
    assert len(_timeline(client, owner, "mac")) == 4
    rules = os.path.join(mac, "history.json")
    kept = open(rules).read()
    os.unlink(rules)
    monkeypatch.setattr(ade_link, "_history_pause", lambda: None)
    assert run(capsys, "--dir", mac, "history")[0] == 0                  # a run meanwhile: the list withdrawn
    _transcript(paths["s1"], first["s1"] + [dict(_typed("v1", "more"), cwd=w), _said("b1", _text("More."))])
    _transcript(paths["s2"], first["s2"] + [dict(_typed("v2", "what does the plan say?"), cwd=w),
                                            _tool_use("b2", "Read", {"file_path": str(home / "grove" / "plan.md")}),
                                            _said("b3", _text("Hire two."))])
    for sid in paths:
        _stop_in(monkeypatch, capsys, mac, paths[sid], sid, w)
    assert len(_timeline(client, owner, "mac")) == 4                     # nothing posted, their places kept
    with open(rules, "w") as fh:
        fh.write(kept)
    for sid in paths:
        _stop_in(monkeypatch, capsys, mac, paths[sid], sid, w)
    assert [t for _, t in _timeline(client, owner, "mac")][4:] == ["[owner] more", "[mac] More.", touched_note("s2")]


def test_deleting_history_seen_with_history_json_gone_mirrors_every_session_but_one_held(client, owner, served,
                                                                                          capsys, tmp_path,
                                                                                          monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    path = _in_projects(home, "s1", [dict(_typed("u1", "go"), cwd=w), _said("a1", _text("One."))])
    held = _in_projects(home, "s2", [dict(_typed("v1", "plans"), cwd=w),
                                     _tool_use("b1", "Read", {"file_path": str(home / "grove" / "x.md")}),
                                     _said("b2", _text("Hire two."))])
    assert run(capsys, "--dir", mac, "history")[0] == 0                  # s2 withheld
    with open(os.path.join(mac, "history_withheld.json"), "w") as fh:
        json.dump([], fh)                                                # lost from the list, the reading holds it
    os.unlink(os.path.join(mac, "history.json"))
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert _timeline(client, owner, "mac") == []                         # a link that read one holds every turn
    os.unlink(os.path.join(mac, "history_seen"))
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    _stop_in(monkeypatch, capsys, mac, held, "s2", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == ["[owner] go", "[mac] One.", touched_note("s2")]


@pytest.mark.parametrize("why", ["withheld", "told", "its_helper_since"])
def test_a_fork_the_history_list_read_before_it_was_written_is_held_with_its_source(client, owner, served, capsys,
                                                                                   tmp_path, monkeypatch, why):
    """s1 held, withheld for its helper's reading (or told it holds, or its helper reading since): s2, its fork, which
    the History list read while it was still empty, is held with it at its first Stop."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    source = [dict(_typed("u1", "summarise the plan"), cwd=w, sessionId="s1"),
              dict(_said("a1", _text("Hire two.")), sessionId="s1")]
    _in_projects(home, "s1", source)
    agent = home / ".claude" / "projects" / "-work" / "s1" / "subagents" / "agent-1.jsonl"

    def helper():
        agent.parent.mkdir(parents=True)
        _transcript(str(agent), [_tool_use("x1", "Read", {"file_path": str(home / "grove" / "plan.md")})])
    if why == "withheld":
        helper()
    fork = _in_projects(home, "s2", [])                                  # begun, nothing in it yet
    assert run(capsys, "--dir", mac, "history")[0] == 0
    if why == "told":
        with open(os.path.join(mac, "mirror_told.json"), "w") as fh:
            json.dump({"s1": int(time.time())}, fh)
    elif why == "its_helper_since":
        helper()
    _transcript(fork, source + [dict(_typed("u2", "and?"), cwd=w, sessionId="s2"),
                                dict(_said("a2", _text("As it said: two.")), sessionId="s2")])
    monkeypatch.setattr(ade_link, "MIRROR_WINDOW", 300)                  # what the Stop reads is s2's own
    _stop_in(monkeypatch, capsys, mac, fork, "s2", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == [touched_note("s2")]


def test_a_fork_is_not_held_for_what_another_fork_of_its_source_read(client, owner, served, capsys, tmp_path,
                                                                     monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    source = [dict(_typed("u1", "go"), cwd=w, sessionId="s1"), dict(_said("a1", _text("One.")), sessionId="s1")]
    _in_projects(home, "s1", source)
    fork = _in_projects(home, "s2", source + [dict(_typed("u2", "on"), cwd=w, sessionId="s2")])
    _in_projects(home, "s3", source + [dict(_tool_use("a3", "Read", {"file_path": str(home / "grove" / "x.md")}),
                                            sessionId="s3")])
    assert run(capsys, "--dir", mac, "history")[0] == 0
    _transcript(fork, source + [dict(_typed("u2", "on"), cwd=w, sessionId="s2"),
                                dict(_said("a2", _text("Two.")), sessionId="s2")])
    _stop_in(monkeypatch, capsys, mac, fork, "s2", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == ["[owner] on", "[mac] Two."]


def test_a_link_that_read_any_history_json_holds_every_turn_while_it_is_missing(client, owner, served, capsys,
                                                                                tmp_path, monkeypatch):
    """One it could not read is one it had: an editor saving it, the moment it is gone lets nothing go, though no
    History run kept anything yet; the hold is said in mirror.log, and a Stop starts the run that withdraws a list."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, started = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "exclude": ["~/grove"]})   # a key mistyped
    w = str(home / "work")
    path = _in_projects(home, "s1", [dict(_typed("u1", "go"), cwd=w), _said("a1", _text("One."))])
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    os.unlink(os.path.join(mac, "history.json"))
    with open(os.path.join(mac, "history_started"), "w") as fh:
        fh.write("0")                                                    # no run started lately
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert _timeline(client, owner, "mac") == []
    assert "held: history.json is missing" in open(os.path.join(mac, "mirror.log")).read()
    with open(os.path.join(mac, "history_sent"), "w") as fh:
        fh.write("1")                                                    # a list sent before, to withdraw
    with open(os.path.join(mac, "history_started"), "w") as fh:
        fh.write("0")
    started.clear()
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert len(started) == 1 and "history" in started[0][0]
    assert open(os.path.join(mac, "mirror.log")).read().count("held: history.json is missing") == 1   # once a hold


def test_a_session_whose_ids_were_cut_is_held_with_the_session_it_holds_at_a_stop(client, owner, served, capsys,
                                                                                 tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    line = [dict(_typed(f"c{i}", "step"), cwd=w, sessionId=f"c{i}") for i in range(ade_link.KIN_MAX)] + \
        [dict(_said("a65", _text("Hire two.")), sessionId="s65")]
    _in_projects(home, "s65", line)
    after = [dict(_said(f"d{i}", _text("Then.")), sessionId=f"d{i}") for i in range(8)]   # its last ids: the eight after
    copy = _in_projects(home, "s66", line + after + [dict(_typed("u66", "and?"), cwd=w, sessionId="s66")])
    assert run(capsys, "--dir", mac, "history")[0] == 0
    with open(os.path.join(mac, "mirror_told.json"), "w") as fh:
        json.dump({"s65": int(time.time())}, fh)                         # held since by a Stop of its own
    _transcript(copy, line + after + [dict(_typed("u66", "and?"), cwd=w, sessionId="s66"),
                                      dict(_said("a66", _text("Two, as it said.")), sessionId="s66")])
    monkeypatch.setattr(ade_link, "MIRROR_WINDOW", 300)                  # what the Stop reads is s66's own
    _stop_in(monkeypatch, capsys, mac, copy, "s66", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == [touched_note("s66")]


def test_with_both_hooks_the_turns_held_while_history_json_is_missing_are_not_posted(client, owner, served, capsys,
                                                                                     tmp_path, monkeypatch):
    """The project's own Stop and the block for every folder's at one Stop, the project's first to keep the place: the
    block's own mark is kept as well, so the turns held meanwhile are not posted when history.json is back."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    first = [dict(_typed("u1", "go"), cwd=w), _said("a1", _text("One."))]
    path = _in_projects(home, "s1", first)
    assert run(capsys, "--dir", mac, "history")[0] == 0
    rules = os.path.join(mac, "history.json")
    kept = open(rules).read()
    os.unlink(rules)
    real = ade_link._mirror_held

    def meanwhile(link, *a, **k):
        with open(os.path.join(mac, "mirror.json"), "w") as fh:
            json.dump({"s1": ade_link.MIRROR_FROM_START}, fh)          # the project's own Stop held first
        return real(link, *a, **k)
    monkeypatch.setattr(ade_link, "_mirror_held", meanwhile)
    _stop_in(monkeypatch, capsys, mac, path, "s1", w, "--listed-only")
    monkeypatch.setattr(ade_link, "_mirror_held", real)
    with open(rules, "w") as fh:
        fh.write(kept)
    _transcript(path, first + [dict(_typed("u2", "next"), cwd=w), _said("a2", _text("Two."))])
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == ["[owner] next", "[mac] Two."]


def test_a_copy_at_the_end_of_a_long_line_of_resumes_is_held_with_its_source_at_a_stop(client, owner, served, capsys,
                                                                                      tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    line = [dict(_typed(f"c{i}", "step"), cwd=w, sessionId=f"c{i}") for i in range(ade_link.KIN_MAX)] + \
        [dict(_said("a65", _text("Hire two.")), sessionId="s65")]
    copy = _in_projects(home, "s66", line + [dict(_typed("u66", "and?"), cwd=w, sessionId="s66")])
    assert run(capsys, "--dir", mac, "history")[0] == 0
    with open(os.path.join(mac, "mirror_told.json"), "w") as fh:
        json.dump({"s65": int(time.time())}, fh)                         # held, its transcript cleaned up since
    _transcript(copy, line + [dict(_typed("u66", "and?"), cwd=w, sessionId="s66"),
                              dict(_said("a66", _text("Two, as it said.")), sessionId="s66")])
    monkeypatch.setattr(ade_link, "MIRROR_WINDOW", 300)                  # what the Stop reads is s66's own
    _stop_in(monkeypatch, capsys, mac, copy, "s66", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == [touched_note("s66")]


# ---- with history.json missing: what holds, and what is noted --------------------------------------------------------

def test_a_link_from_before_history_seen_holds_by_its_withheld_list_while_history_json_is_missing(client, owner, served,
                                                                                                capsys, tmp_path,
                                                                                                monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home = tmp_path / "home"
    (home / "work").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: None)
    with open(os.path.join(mac, "history_withheld.json"), "w") as fh:
        json.dump(["s9"], fh)                                            # kept by an earlier release's History run
    w = str(home / "work")
    path = _in_projects(home, "s1", [dict(_typed("u1", "go"), cwd=w), _said("a1", _text("One."))])
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert _timeline(client, owner, "mac") == []                         # it had one, as its list says
    assert os.path.exists(os.path.join(mac, "history_seen"))
    os.unlink(os.path.join(mac, "history_seen"))                         # the way back, which holds
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == ["[owner] go", "[mac] One."]


def _without_rule(mac):
    """history.json gone, and history_seen deleted: the project's own mirror posts every session again."""
    os.unlink(os.path.join(mac, "history.json"))
    os.unlink(os.path.join(mac, "history_seen"))


def test_with_no_history_json_a_kept_reading_that_cannot_be_read_holds_every_turn(client, owner, served, capsys,
                                                                                  tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    path = _in_projects(home, "s1", [dict(_typed("u1", "go"), cwd=w), _said("a1", _text("One."))])
    assert run(capsys, "--dir", mac, "history")[0] == 0
    _without_rule(mac)
    with open(os.path.join(mac, "history_cache.json"), "w") as fh:
        fh.write("{cut")
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert _timeline(client, owner, "mac") == []
    assert json.loads(open(os.path.join(mac, "mirror.json")).read())["s1"] == ade_link.MIRROR_FROM_START


def test_with_no_history_json_a_fork_a_kept_reading_relates_to_a_held_session_is_held(client, owner, served, capsys,
                                                                                      tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    source = [dict(_typed("u1", "go"), cwd=w, sessionId="s1"), dict(_said("a1", _text("One.")), sessionId="s1")]
    _in_projects(home, "s1", source)
    fork = _in_projects(home, "s2", source + [dict(_typed("u2", "on"), cwd=w, sessionId="s2"),
                                              dict(_said("a2", _text("Two.")), sessionId="s2")])
    assert run(capsys, "--dir", mac, "history")[0] == 0                  # s2's reading names s1
    _without_rule(mac)
    with open(os.path.join(mac, "mirror_told.json"), "w") as fh:
        json.dump({"s1": int(time.time())}, fh)                          # s1 held since
    monkeypatch.setattr(ade_link, "_head_ids", lambda path, most=1 << 20: set())   # its start names none (an image)
    monkeypatch.setattr(ade_link, "MIRROR_WINDOW", 300)                  # what the Stop reads is s2's own
    _stop_in(monkeypatch, capsys, mac, fork, "s2", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == [touched_note("s2")]


def test_a_hold_for_a_missing_history_json_and_its_end_are_each_noted_once(client, owner, served, capsys, tmp_path,
                                                                           monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    turns = [dict(_typed("u1", "go"), cwd=w), _said("a1", _text("One."))]
    path = _in_projects(home, "s1", turns)
    assert run(capsys, "--dir", mac, "history")[0] == 0
    rules = os.path.join(mac, "history.json")
    kept = open(rules).read()
    for n in range(2):                                                   # gone, back, gone again, back
        os.unlink(rules)
        turns += [dict(_typed(f"g{n}", "more"), cwd=w), _said(f"h{n}", _text("More."))]
        _transcript(path, turns)
        _stop_in(monkeypatch, capsys, mac, path, "s1", w)
        _stop_in(monkeypatch, capsys, mac, path, "s1", w)
        with open(rules, "w") as fh:
            fh.write(kept)
        turns += [dict(_typed(f"k{n}", "again"), cwd=w), _said(f"m{n}", _text("Again."))]
        _transcript(path, turns)
        _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    log = open(os.path.join(mac, "mirror.log")).read()
    assert log.count("held: history.json is missing") == 2 and log.count("no longer held") == 2


def test_a_skip_mark_another_stop_of_the_gap_kept_stays_where_this_one_read_less(client, owner, served, capsys,
                                                                                tmp_path, monkeypatch):
    """Two Stops of the block for every folder at one Stop of a session: the one that read less comes to the lock
    second and leaves the other's mark, so the turns held meanwhile are not posted when history.json is back."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    first = [dict(_typed("u1", "go"), cwd=w), _said("a1", _text("One."))]
    more = first + [dict(_typed("u2", "on"), cwd=w), _said("a2", _text("Two."))]
    path = _in_projects(home, "s1", more)
    assert run(capsys, "--dir", mac, "history")[0] == 0
    rules = os.path.join(mac, "history.json")
    kept = open(rules).read()
    os.unlink(rules)
    _stop_in(monkeypatch, capsys, mac, path, "s1", w, "--listed-only")   # read to a2: its mark to a2
    _transcript(path, first)                                             # the other, which read to a1 only
    _stop_in(monkeypatch, capsys, mac, path, "s1", w, "--listed-only")
    assert json.loads(open(os.path.join(mac, "mirror.json")).read())["skip:s1"]["to"] == "a2"
    with open(rules, "w") as fh:
        fh.write(kept)
    _transcript(path, more + [dict(_typed("u3", "next"), cwd=w), _said("a3", _text("Three."))])
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == ["[owner] next", "[mac] Three."]


# ---- skip marks, and the block for every folder ----------------------------------------------------------------------

def test_a_skip_mark_moves_on_where_a_stop_could_not_read_back_to_the_place(client, owner, served, capsys, tmp_path,
                                                                           monkeypatch):
    """The block for every folder, history.json missing: a turn so long that the place falls out of what the next Stop
    reads. That Stop cannot tell which mark reaches further, so it keeps its own, and the turns held meanwhile are not
    posted when history.json is back."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    turns = [dict(_typed("u0", "go"), cwd=w), _said("a0", _text("Zero."))]
    path = _in_projects(home, "s1", turns)
    assert run(capsys, "--dir", mac, "history")[0] == 0
    _stop_in(monkeypatch, capsys, mac, path, "s1", w, "--listed-only")      # posted: its place a0
    rules = os.path.join(mac, "history.json")
    kept = open(rules).read()
    os.unlink(rules)
    turns += [dict(_typed("u1", "one?"), cwd=w), _said("a1", _text("One."))]
    _transcript(path, turns)
    _stop_in(monkeypatch, capsys, mac, path, "s1", w, "--listed-only")      # its mark: from a0 to a1
    turns += [dict(_typed("u2", "two?"), cwd=w), _said("a2", _text("Two. " + "x" * 2000))]
    _transcript(path, turns)
    data = open(path, "rb").read()
    start = data.rfind(b"\n", 0, data.index(b'"u2"')) + 1                 # the last turn alone is read: a0, a1 not
    monkeypatch.setattr(ade_link, "MIRROR_WINDOW", len(data) - start + 3)
    _stop_in(monkeypatch, capsys, mac, path, "s1", w, "--listed-only")
    monkeypatch.setattr(ade_link, "MIRROR_WINDOW", 4 << 20)
    assert json.loads(open(os.path.join(mac, "mirror.json")).read())["skip:s1"]["to"] == "a2"
    with open(rules, "w") as fh:
        fh.write(kept)
    turns += [dict(_typed("u3", "three?"), cwd=w), _said("a3", _text("Three."))]
    _transcript(path, turns)
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert [t for _, t in _timeline(client, owner, "mac")] == ["[owner] go", "[mac] Zero.", "[owner] three?",
                                                               "[mac] Three."]


def test_a_stop_with_no_place_judges_a_mark_by_every_entry_it_read(client, owner, served, capsys, tmp_path,
                                                                   monkeypatch):
    """Two Stops of the block for every folder at a session's first Stop, history.json missing: the one that read
    further comes to the lock second and keeps its own mark, by all it read, not only the turn it would post."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    turns = [dict(_typed("u1", "one?"), cwd=w), _said("a1", _text("One.")), dict(_typed("u2", "two?"), cwd=w),
             _said("a2", _text("Two."))]
    path = _in_projects(home, "s1", turns)
    assert run(capsys, "--dir", mac, "history")[0] == 0
    os.unlink(os.path.join(mac, "history.json"))
    real = ade_link._mirror_held

    def meanwhile(link, *a, **k):
        with open(os.path.join(mac, "mirror.json"), "w") as fh:          # the other, which read to a1, first
            json.dump({"s1": ade_link.MIRROR_FROM_START,
                       "skip:s1": {"from": ade_link.MIRROR_FROM_START, "to": "a1"}}, fh)
        return real(link, *a, **k)
    monkeypatch.setattr(ade_link, "_mirror_held", meanwhile)
    _stop_in(monkeypatch, capsys, mac, path, "s1", w, "--listed-only")
    assert json.loads(open(os.path.join(mac, "mirror.json")).read())["skip:s1"]["to"] == "a2"


def test_the_block_for_every_folder_holding_by_design_ends_no_hold_in_the_log(client, owner, served, capsys, tmp_path,
                                                                             monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    turns = [dict(_typed("u1", "go"), cwd=w), _said("a1", _text("One."))]
    path = _in_projects(home, "s1", turns)
    assert run(capsys, "--dir", mac, "history")[0] == 0
    os.unlink(os.path.join(mac, "history.json"))
    for n in range(2):
        turns += [dict(_typed(f"g{n}", "more"), cwd=w), _said(f"h{n}", _text("More."))]
        _transcript(path, turns)
        _stop_in(monkeypatch, capsys, mac, path, "s1", w)                # the project's own: held, missing
        _stop_in(monkeypatch, capsys, mac, path, "s1", w, "--listed-only")   # the block's: held by design
    log = open(os.path.join(mac, "mirror.log")).read()
    assert log.count("held: history.json is missing") == 1 and "no longer held" not in log


# ---- when a hold for the link ends -----------------------------------------------------------------------------------

def test_a_hold_for_the_link_ends_only_when_its_cause_is_gone_whichever_session_stops(client, owner, served, capsys,
                                                                                    tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    turns = {sid: [dict(_typed(f"u{sid}", "go"), cwd=w), _said(f"a{sid}", _text("One."))] for sid in ("s1", "s2")}
    paths = {sid: _in_projects(home, sid, turns[sid]) for sid in turns}
    assert run(capsys, "--dir", mac, "history")[0] == 0
    with open(os.path.join(mac, "mirror_told.json"), "w") as fh:
        json.dump({"s2": int(time.time())}, fh)                          # s2 held as told; s1 not
    rules = os.path.join(mac, "history.json")
    kept = open(rules).read()
    os.unlink(rules)
    for n in range(2):
        for sid in ("s1", "s2"):                                         # missing for s1, touched for s2
            turns[sid] += [dict(_typed(f"g{sid}{n}", "more"), cwd=w), _said(f"h{sid}{n}", _text("More."))]
            _transcript(paths[sid], turns[sid])
            _stop_in(monkeypatch, capsys, mac, paths[sid], sid, w)
    log = open(os.path.join(mac, "mirror.log")).read()
    assert log.count("held: history.json is missing") == 1 and "no longer held" not in log
    with open(rules, "w") as fh:
        fh.write(kept)
    turns["s2"] += [dict(_typed("gs2x", "again"), cwd=w), _said("hs2x", _text("Again."))]
    _transcript(paths["s2"], turns["s2"])
    _stop_in(monkeypatch, capsys, mac, paths["s2"], "s2", w)            # held still, but history.json is back
    assert open(os.path.join(mac, "mirror.log")).read().count("no longer held") == 1


def test_a_history_seen_that_cannot_be_kept_is_said(client, owner, served, capsys, tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    real = ade_link.Link.keep

    def keep(self, name, value):
        if name == "history_seen":
            raise OSError(28, "No space left on device")
        return real(self, name, value)
    monkeypatch.setattr(ade_link.Link, "keep", keep)
    w = str(home / "work")
    path = _in_projects(home, "s1", [dict(_typed("u1", "go"), cwd=w), _said("a1", _text("One."))])
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    _transcript(path, [dict(_typed("u1", "go"), cwd=w), _said("a1", _text("One.")), dict(_typed("u2", "on"), cwd=w),
                       _said("a2", _text("Two."))])
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    log = open(os.path.join(mac, "mirror.log")).read()
    assert log.count("history_seen cannot be kept (OSError): a missing history.json may then not hold the mirror") == 1
    assert not os.path.exists(os.path.join(mac, "history_seen_set"))     # never the second without the first
    monkeypatch.setattr(ade_link.Link, "keep", real)
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)                    # both kept: the next failure is said again
    for name in ("history_seen", "history_seen_set"):
        os.unlink(os.path.join(mac, name))
    monkeypatch.setattr(ade_link.Link, "keep", keep)
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert open(os.path.join(mac, "mirror.log")).read().count("history_seen cannot be kept (OSError)") == 2

    def keep_set(self, name, value):
        if name == "history_seen_set":
            raise OSError(28, "No space left on device")
        return real(self, name, value)
    monkeypatch.setattr(ade_link.Link, "keep", keep_set)
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    line = [x for x in open(os.path.join(mac, "mirror.log")).read().splitlines() if "history_seen_set" in x]
    assert line and line[-1].endswith("history_seen_set cannot be kept (OSError)")


def test_a_hold_for_a_file_that_cannot_be_read_ends_only_when_a_stop_posts(client, owner, served, capsys, tmp_path,
                                                                           monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w, g = str(home / "work"), str(home / "grove")
    path = _in_projects(home, "s1", [dict(_typed("u1", "go"), cwd=w), _said("a1", _text("One."))])
    other = _in_projects(home, "s9", [dict(_typed("v1", "plans"), cwd=g), _said("b1", _text("Noted."))])
    assert run(capsys, "--dir", mac, "history")[0] == 0
    with open(os.path.join(mac, "mirror_told.json"), "w") as fh:
        fh.write("{cut")
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)                    # held: the file cannot be read
    _stop_in(monkeypatch, capsys, mac, other, "s9", g)                   # an excepted project's: never mirrored
    log = open(os.path.join(mac, "mirror.log")).read()
    assert log.count("held: a file this link keeps cannot be read") == 1 and "no longer held" not in log


# ---- a hold's notes and ends, and a history_seen that cannot be kept -------------------------------------------------

def test_a_link_whose_history_seen_cannot_be_kept_holds_by_its_withheld_list(client, owner, served, capsys, tmp_path,
                                                                             monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    real = ade_link.Link.keep
    monkeypatch.setattr(ade_link.Link, "keep", lambda self, name, value:
                        (_ for _ in ()).throw(OSError(5, "I/O error")) if name == "history_seen" else
                        real(self, name, value))
    w = str(home / "work")
    path = _in_projects(home, "s1", [dict(_typed("u1", "go"), cwd=w), _said("a1", _text("One."))])
    assert run(capsys, "--dir", mac, "history")[0] == 0                  # its withheld list kept, history_seen not
    os.unlink(os.path.join(mac, "history.json"))
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert _timeline(client, owner, "mac") == []


@pytest.mark.parametrize("posts", [False, True])
def test_a_hold_s_end_is_judged_as_it_is_noted_not_as_the_stop_began(client, owner, served, capsys, tmp_path,
                                                                     monkeypatch, posts):
    """A Stop that read history.json at its start, which is gone again as it notes: it ends the hold only if it posted
    (judged by the rule it read), never because the file was there when it began."""
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    turns = [dict(_typed("u1", "go"), cwd=w), _said("a1", _text("One."))]
    path = _in_projects(home, "s1", turns)
    assert run(capsys, "--dir", mac, "history")[0] == 0
    rules = os.path.join(mac, "history.json")
    kept = open(rules).read()
    os.unlink(rules)
    turns += [dict(_typed("u2", "more"), cwd=w), _said("a2", _text("More."))]
    _transcript(path, turns)
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)                    # held: missing, noted
    if not posts:
        with open(os.path.join(mac, "mirror_told.json"), "w") as fh:
            json.dump({"s1": int(time.time())}, fh)                      # held as told since: it posts nothing
    with open(rules, "w") as fh:
        fh.write(kept)
    real = ade_link._mirror_held

    def meanwhile(link, *a, **k):
        held = real(link, *a, **k)
        os.unlink(rules)                                                 # gone again as this Stop judged
        return held
    monkeypatch.setattr(ade_link, "_mirror_held", meanwhile)
    turns += [dict(_typed("u3", "again"), cwd=w), _said("a3", _text("Again."))]
    _transcript(path, turns)
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert ("no longer held" in open(os.path.join(mac, "mirror.log")).read()) is posts


def test_a_hold_is_noted_under_the_mirror_s_lock(client, owner, served, capsys, tmp_path, monkeypatch):
    import fcntl
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    path = _in_projects(home, "s1", [dict(_typed("u1", "go"), cwd=w), _said("a1", _text("One."))])
    assert run(capsys, "--dir", mac, "history")[0] == 0
    os.unlink(os.path.join(mac, "history.json"))
    real, locked = ade_link._note_hold, []

    def noting(link, *a, **k):
        fd = os.open(os.path.join(mac, "mirror.lock"), os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked.append(False)
        except OSError:
            locked.append(True)                                          # the Stop holds it
        finally:
            os.close(fd)
        return real(link, *a, **k)
    monkeypatch.setattr(ade_link, "_note_hold", noting)
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert locked == [True]


def test_a_hold_for_a_missing_history_json_ends_when_history_seen_is_deleted(client, owner, served, capsys,
                                                                             tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    turns = [dict(_typed("u1", "go"), cwd=w), _said("a1", _text("One."))]
    path = _in_projects(home, "s1", turns)
    assert run(capsys, "--dir", mac, "history")[0] == 0
    os.unlink(os.path.join(mac, "history.json"))
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    os.unlink(os.path.join(mac, "history_seen"))
    turns += [dict(_typed("u2", "more"), cwd=w), _said("a2", _text("More."))]
    _transcript(path, turns)
    with open(os.path.join(mac, "mirror_told.json"), "w") as fh:
        json.dump({"s1": int(time.time())}, fh)                          # it posts nothing: the deletion ends it
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    log = open(os.path.join(mac, "mirror.log")).read()
    assert log.count("no longer held") == 1 and log.rstrip("\n").endswith(" no longer held") and "unjudged" not in log


# ---- a failure said once, and a hold an earlier build noted ----------------------------------------------------------

def test_a_failure_to_keep_history_seen_is_said_once_a_stop_where_its_mark_cannot_be_kept_either(
        client, owner, served, capsys, tmp_path, monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    real = ade_link.Link.keep

    def keep(self, name, value):
        if name in ("history_seen", "mirror_seen_noted"):
            raise OSError(28, "No space left on device")
        return real(self, name, value)
    monkeypatch.setattr(ade_link.Link, "keep", keep)
    w = str(home / "work")
    path = _in_projects(home, "s1", [dict(_typed("u1", "go"), cwd=w), _said("a1", _text("One."))])
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    assert open(os.path.join(mac, "mirror.log")).read().count("history_seen cannot be kept") == 1
    assert json.loads(open(os.path.join(mac, "mirror.json")).read())["s1"] == ade_link.MIRROR_FROM_START   # went on


def test_a_hold_an_earlier_build_noted_in_other_words_is_still_known_as_missing(client, owner, served, capsys, tmp_path,
                                                                                 monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    turns = [dict(_typed("u1", "go"), cwd=w), _said("a1", _text("One."))]
    path = _in_projects(home, "s1", turns)
    assert run(capsys, "--dir", mac, "history")[0] == 0
    with open(os.path.join(mac, "mirror_hold"), "w") as fh:
        fh.write("history.json is missing, and this link has read one: turns wait for it, or for history_seen to be "
                 "deleted, which posts them as they are, unjudged")
    with open(os.path.join(mac, "mirror_told.json"), "w") as fh:
        json.dump({"s1": int(time.time())}, fh)                          # a Stop that posts nothing
    turns += [dict(_typed("u2", "more"), cwd=w), _said("a2", _text("More."))]
    _transcript(path, turns)
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)                    # history.json is there: the hold ended
    assert "no longer held" in open(os.path.join(mac, "mirror.log")).read()


# ---- a hold noted in other words -------------------------------------------------------------------------------------

def test_a_hold_noted_in_other_words_is_not_noted_again_while_it_lasts(client, owner, served, capsys, tmp_path,
                                                                        monkeypatch):
    mac = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    home, _ = _ruled(tmp_path, monkeypatch, mac, {"projects": "all", "except": ["~/grove"]})
    w = str(home / "work")
    path = _in_projects(home, "s1", [dict(_typed("u1", "go"), cwd=w), _said("a1", _text("One."))])
    assert run(capsys, "--dir", mac, "history")[0] == 0
    with open(os.path.join(mac, "mirror_hold"), "w") as fh:
        fh.write("history.json is missing, and this link has read one: turns wait for it, or for history_seen to be "
                 "deleted, which posts them as they are, unjudged")     # as an earlier build noted it
    os.unlink(os.path.join(mac, "history.json"))
    _stop_in(monkeypatch, capsys, mac, path, "s1", w)
    log = os.path.join(mac, "mirror.log")
    assert "held:" not in (open(log).read() if os.path.exists(log) else "")
