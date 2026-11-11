"""The workspace's record of the skills its sessions called (skillcalls.py): each
session's own skills.jsonl taken into one record of the workspace's, by the file's identity and the offset read to; a cut
found, a line not yet ended left, a line out of shape dropped and counted, nothing taken twice across a restart; the
record bounded, numbered, paged and given an id of its own; the gate refusing a tool call that names it; and the agent's
GET /skills, which takes every session's new lines first."""

import json
import os
import stat
import subprocess
import sys
import threading
import time

import httpx
import pytest

from conftest import ROOT
from proofpane_workspace import activity, gate, skillcalls, workspace as workspace_module
from proofpane_workspace.skillcalls import SkillCalls
from skills_readme import stated, text as file_text
from workspace_fixtures import Agent, make_repo

SID = "0123456789ab"
SHA = "a" * 40
DIGEST = "b" * 64


def _row(n=0, **given):
    row = {"id": f"{n:016x}", "at": int(time.time()) - 5, "skill": "review", "kind": "business", "path": "claude",
           "failed": False, "version": SHA}
    row.update(given)
    return row


def _write(folder, *rows, end="\n"):
    with open(folder / "skills.jsonl", "a") as fh:
        for row in rows:
            fh.write((row if isinstance(row, str) else json.dumps(row)) + end)


def _store(tmp_path, **kw):
    return SkillCalls(str(tmp_path / "ws" / "skills"), **kw)


@pytest.fixture
def folder(tmp_path):
    f = tmp_path / "sessions" / SID
    f.mkdir(parents=True)
    return f


def _ids(store, since=0):
    return [row["id"] for row in store.page(since)["lines"]]


# taking


def test_a_session_s_lines_are_taken_once_with_where_they_were_made(tmp_path, folder):
    store = _store(tmp_path)
    _write(folder, _row(1), _row(2, skill="deploy"))
    assert store.take(SID, str(folder), "demo", "task-1") == 2
    page = store.page(0)
    assert [(r["n"], r["id"], r["skill"], r["session"], r["repo"], r["task"]) for r in page["lines"]] == [
        (1, f"{1:016x}", "review", SID, "demo", "task-1"), (2, f"{2:016x}", "deploy", SID, "demo", "task-1")]
    assert set(page["lines"][0]) == {"n", "id", "at", "skill", "kind", "path", "failed", "version", "session", "repo",
                                     "task"}
    assert store.take(SID, str(folder), "demo", "task-1") == 0
    assert _ids(store) == [f"{1:016x}", f"{2:016x}"]
    _write(folder, _row(3))
    assert store.take(SID, str(folder), "demo", "task-1") == 1
    assert _ids(store) == [f"{n:016x}" for n in (1, 2, 3)]


def test_a_repository_and_a_task_are_kept_only_where_there_is_one_and_it_is_a_name(tmp_path, folder):
    store = _store(tmp_path)
    _write(folder, _row(1), _row(2), _row(3))
    assert store.take(SID, str(folder), None, None, flush=False) == 3
    _write(folder, _row(4))
    store.take(SID, str(folder), "../x", "a b")                       # what is no name is kept as none
    _write(folder, _row(5))
    store.take(SID, str(folder), 5, ["t"])
    assert [(r["repo"], r["task"]) for r in store.page(0)["lines"]] == [(None, None)] * 5


def test_a_last_line_not_yet_ended_is_left_for_the_next_take(tmp_path, folder):
    store = _store(tmp_path)
    _write(folder, _row(1))
    half = json.dumps(_row(2))
    with open(folder / "skills.jsonl", "a") as fh:
        fh.write(half[:30])
    assert store.take(SID, str(folder)) == 1
    assert store.take(SID, str(folder)) == 0
    with open(folder / "skills.jsonl", "a") as fh:
        fh.write(half[30:] + "\n")
    assert store.take(SID, str(folder)) == 1
    assert _ids(store) == [f"{1:016x}", f"{2:016x}"] and store.page(0)["dropped"] == 0


def test_a_file_with_no_line_ended_yet_takes_nothing_and_a_blank_line_is_passed_over(tmp_path, folder):
    store = _store(tmp_path)
    with open(folder / "skills.jsonl", "a") as fh:
        fh.write(json.dumps(_row(1)))
    assert store.take(SID, str(folder)) == 0 and store.page(0)["lines"] == []
    with open(folder / "skills.jsonl", "a") as fh:
        fh.write("\n\n   \n")
    assert store.take(SID, str(folder)) == 1 and store.page(0)["dropped"] == 0     # a blank line is no line out of shape


def test_a_take_examines_at_most_so_many_lines_of_a_file_and_the_rest_wait_for_the_next(tmp_path, folder, monkeypatch):
    """A session's programs can write any number of lines: one take looks at LINES_MOST of them (a line out of shape is
    looked at and counted), and the next goes on from the line after the last one looked at: none lost, none twice."""
    monkeypatch.setattr(skillcalls, "LINES_MOST", 5)
    store = _store(tmp_path)
    _write(folder, _row(1), _row(2), _row(3), "junk", "junk", "junk", "junk", _row(4), _row(5), _row(6))     # 10 lines
    assert store.take(SID, str(folder)) == 3 and _ids(store) == [f"{n:016x}" for n in (1, 2, 3)]
    assert store.page(0)["dropped"] == 2                                  # the 4th and 5th lines, the first two junk
    assert store.take(SID, str(folder)) == 3 and store.page(0)["dropped"] == 4
    assert _ids(store) == [f"{n:016x}" for n in range(1, 7)]
    assert store.take(SID, str(folder)) == 0 and store.page(0)["dropped"] == 4


def test_a_blank_line_is_looked_at_too_so_a_file_of_them_is_taken_a_little_at_a_time(tmp_path, folder, monkeypatch):
    monkeypatch.setattr(skillcalls, "LINES_MOST", 5)
    store = _store(tmp_path)
    _write(folder, *([""] * 7), _row(1))
    assert store.take(SID, str(folder)) == 0 and store.page(0)["lines"] == []          # five blank lines looked at
    assert store.take(SID, str(folder)) == 1 and _ids(store) == [f"{1:016x}"]          # two more, then the line
    assert store.page(0)["dropped"] == 0


# a line no hook wrote


def _raw(folder, data):
    with open(folder / "skills.jsonl", "ab") as fh:
        fh.write(data)


def test_a_stretch_of_the_most_a_take_reads_with_no_newline_is_passed_over_as_one_line_out_of_shape(tmp_path, folder):
    """READ_MOST bytes with no newline can only be a line no hook wrote. A take that waited for its end took nothing of
    the file, ever, after it, and read the same megabyte at every take: it is counted once and passed over."""
    store = _store(tmp_path)
    assert skillcalls.READ_MOST == 1024 * 1024
    _raw(folder, b"x" * skillcalls.READ_MOST + b"\n")                  # a stretch of exactly the most, then its newline
    _write(folder, _row(1), _row(2), _row(3))
    assert store.take(SID, str(folder)) == 3 and _ids(store) == [f"{n:016x}" for n in (1, 2, 3)]
    assert store.page(0)["dropped"] == 1
    assert store.record["cursors"][SID]["offset"] == os.path.getsize(folder / "skills.jsonl")
    assert store.take(SID, str(folder)) == 0 and store.page(0)["dropped"] == 1        # counted once, not at each take
    _write(folder, _row(4))
    assert store.take(SID, str(folder)) == 1 and _ids(store)[-1] == f"{4:016x}"


def test_a_stretch_past_two_reads_long_is_passed_over_by_the_last_read_of_the_file(tmp_path, folder, monkeypatch):
    monkeypatch.setattr(skillcalls, "READ_MOST", 4096)
    store = _store(tmp_path)
    _raw(folder, b"x" * (3 * 4096 + 100) + b"\n")
    _write(folder, _row(1), _row(2), _row(3))
    assert store.take(SID, str(folder)) == 3 and store.page(0)["dropped"] == 1
    assert _ids(store) == [f"{n:016x}" for n in (1, 2, 3)]


def test_the_lines_between_a_long_stretch_and_the_file_s_last_read_are_passed_over_with_it(
        tmp_path, folder, monkeypatch):
    monkeypatch.setattr(skillcalls, "READ_MOST", 4096)
    store = _store(tmp_path)
    _raw(folder, b"x" * 5000 + b"\n")
    _write(folder, *[_row(n) for n in range(1, 201)])                    # 200 lines, about 24 KB: more than two reads
    window = (folder / "skills.jsonl").read_bytes()[-4096:]
    expected = window[window.find(b"\n") + 1:].count(b"\n")           # the whole lines after its first newline
    assert 0 < expected < 200
    assert store.take(SID, str(folder)) == expected and store.page(0)["dropped"] == 1
    assert _ids(store) == [f"{n:016x}" for n in range(201 - expected, 201)]          # the newest, none twice


def test_a_stretch_with_no_newline_in_the_last_read_either_takes_and_counts_nothing_until_one_comes(tmp_path, folder,
                                                                                                    monkeypatch):
    monkeypatch.setattr(skillcalls, "READ_MOST", 4096)
    store = _store(tmp_path)
    _raw(folder, b"x" * (3 * 4096))
    for _ in range(3):
        assert store.take(SID, str(folder)) == 0
    assert store.page(0)["dropped"] == 0 and SID not in store.record["cursors"]
    _raw(folder, b"\n")                                                 # the line ends, and no line follows it yet
    assert store.take(SID, str(folder)) == 0 and SID not in store.record["cursors"] and store.page(0)["dropped"] == 0
    _write(folder, _row(1), _row(2))
    assert store.take(SID, str(folder)) == 2 and store.page(0)["dropped"] == 1


def test_a_line_longer_than_an_offset_looks_back_over_is_found_again_as_the_last_one_taken(
        tmp_path, folder, monkeypatch):
    """A line out of shape can be longer than the bytes before an offset that are read to find the line that ends
    there (TAIL_LOOK). Taken as the last line of a take it could not be found again: the file was read as cut from its
    start at each take, which went round the same lines, counted the same line out of shape again each time, and never
    reached the lines after it (4,000 lines, then 3,999, then none for ever)."""
    monkeypatch.setattr(skillcalls, "LINES_MOST", 5)
    store = _store(tmp_path)
    _write(folder, *[_row(n) for n in range(1, 10)], "y" * (skillcalls.TAIL_LOOK + 808),
           *[_row(n) for n in range(10, 13)])                            # the long line is the last of a take's five
    taken = [store.take(SID, str(folder)) for _ in range(5)]
    assert sum(taken) == 12 and _ids(store) == [f"{n:016x}" for n in range(1, 13)]
    assert store.page(0)["dropped"] == 1
    held = store.page(0)
    assert store.take(SID, str(folder)) == 0 and store.page(0) == held


def test_the_last_line_taken_is_found_by_its_end_whatever_its_length(tmp_path):
    keep, look = skillcalls.TAIL_KEEP, skillcalls.TAIL_LOOK
    assert keep < look                                                    # a line's end is what the look holds of it
    for size in (10, keep, keep + 1, look - 1, look, look + 1, 3 * look):
        head = b"first\n"
        line = bytes(i % 251 for i in range(size)).replace(b"\n", b"n")
        path = tmp_path / f"f{size}"
        path.write_bytes(head + line + b"\n")
        fd = os.open(path, os.O_RDONLY)
        try:
            offset = len(head) + size + 1
            assert skillcalls._tail_is(fd, offset, skillcalls._digest(line)), size
            assert not skillcalls._tail_is(fd, offset, skillcalls._digest(line + b"x")), size
            assert not skillcalls._tail_is(fd, offset - 1, skillcalls._digest(line)), size    # not at a line's end
        finally:
            os.close(fd)


def test_a_take_finds_the_last_line_taken_by_reading_at_most_the_bytes_the_look_holds_before_the_offset(
        tmp_path, folder, monkeypatch):
    """Each take of a session finds the line its offset follows by reading TAIL_LOOK bytes before the offset, whatever
    the file's size: a look over all that came before made each take's work as large as the file, which a program of a
    session makes what it likes (as `look = offset` would)."""
    store = _store(tmp_path)
    _write(folder, *[_row(n) for n in range(1, 301)])
    size = os.path.getsize(folder / "skills.jsonl")
    assert size > 4 * skillcalls.TAIL_LOOK
    assert store.take(SID, str(folder)) == 300
    offset = store.record["cursors"][SID]["offset"]
    assert offset == size
    real, reads = os.pread, []

    def spy(fd, n, at):
        got = real(fd, n, at)
        reads.append((at, len(got)))
        return got

    monkeypatch.setattr(skillcalls.os, "pread", spy)
    assert store.take(SID, str(folder)) == 0
    before = sum(n for at, n in reads if at < offset)
    assert 0 < before <= skillcalls.TAIL_LOOK                           # it looked, and no farther than the look holds
    _write(folder, _row(301))
    reads.clear()
    assert store.take(SID, str(folder)) == 1
    assert sum(n for at, n in reads if at < offset) <= skillcalls.TAIL_LOOK


def test_a_file_changed_before_the_offset_in_place_is_read_as_cut_from_its_start(tmp_path, folder):
    """The line that ends at the offset is the last one taken and the byte before the offset is its newline: a file with
    another byte there is not the file that was read, though its identity, its size so far and the line's text are the
    same (the newline written over in place, and lines added after)."""
    store = _store(tmp_path)
    _write(folder, _row(1), _row(2), _row(3))
    assert store.take(SID, str(folder)) == 3
    offset = store.record["cursors"][SID]["offset"]
    with open(folder / "skills.jsonl", "r+b") as fh:
        fh.seek(offset - 1)
        assert fh.read(1) == b"\n"
        fh.seek(offset - 1)
        fh.write(b" ")
    _write(folder, _row(4), _row(5))
    assert store.take(SID, str(folder)) == 1 and _ids(store) == [f"{n:016x}" for n in (1, 2, 3, 5)]
    assert store.page(0)["dropped"] == 1                                  # the third line and the fourth, joined


def test_a_cut_file_is_found_its_last_line_taken_among_the_first_lines_and_none_is_taken_twice(tmp_path, folder,
                                                                                               monkeypatch):
    monkeypatch.setattr(skillcalls, "LINES_MOST", 5)
    store = _store(tmp_path)
    _write(folder, _row(1), _row(2), _row(3))
    assert store.take(SID, str(folder)) == 3
    (folder / "skills.jsonl").unlink()                                  # the hook's cut: a new file, the newest lines
    _write(folder, _row(3), _row(4), _row(5))
    assert store.take(SID, str(folder)) == 2 and _ids(store) == [f"{n:016x}" for n in range(1, 6)]


def test_a_cut_file_whose_last_line_taken_is_past_the_first_lines_is_read_on_from_its_start_a_take_at_a_time(
        tmp_path, folder, monkeypatch):
    """The search for the last line taken looks at LINES_MOST lines only (a file of a session's programs can hold a
    million): where it is not among them the file is read from its start, LINES_MOST lines a take, and a line the record
    holds is never taken again."""
    monkeypatch.setattr(skillcalls, "LINES_MOST", 2)
    store = _store(tmp_path)
    _write(folder, _row(1), _row(2), _row(3), _row(4))
    assert store.take(SID, str(folder)) == 2 and store.take(SID, str(folder)) == 2
    (folder / "skills.jsonl").unlink()                                  # the hook's cut: a new file, the newest lines
    _write(folder, _row(2), _row(3), _row(4), _row(5))                  # the last line taken is its third, past the two
    assert store.take(SID, str(folder)) == 0                            # not found: read from its start, two lines, both held
    assert store.take(SID, str(folder)) == 1                            # then on from there: the fourth is held, the fifth new
    assert _ids(store) == [f"{n:016x}" for n in range(1, 6)] and store.page(0)["dropped"] == 0


def test_the_lines_of_a_cut_file_that_are_passed_over_are_not_among_those_a_take_examines(tmp_path, folder, monkeypatch):
    monkeypatch.setattr(skillcalls, "LINES_MOST", 3)
    store = _store(tmp_path)
    _write(folder, _row(1), _row(2), _row(3))
    assert store.take(SID, str(folder)) == 3
    (folder / "skills.jsonl").unlink()                                  # a cut: the last line taken is the file's second
    _write(folder, _row(2), _row(3), _row(4), _row(5), _row(6))
    assert store.take(SID, str(folder)) == 3                            # two passed over, then three looked at, all new
    assert _ids(store) == [f"{n:016x}" for n in range(1, 7)]


def test_a_cut_file_with_nothing_new_in_it_is_taken_to_its_end_all_the_same(tmp_path, folder):
    store = _store(tmp_path)
    _write(folder, _row(1), _row(2), _row(3))
    assert store.take(SID, str(folder)) == 3
    (folder / "skills.jsonl").unlink()
    _write(folder, _row(2), _row(3))                                    # a cut that kept lines already taken, and no other
    assert store.take(SID, str(folder)) == 0
    _write(folder, _row(4))
    assert store.take(SID, str(folder)) == 1 and _ids(store) == [f"{n:016x}" for n in range(1, 5)]


def test_a_file_of_blank_lines_alone_is_taken_to_its_end_and_a_line_after_it_is_taken(tmp_path, folder):
    store = _store(tmp_path)
    with open(folder / "skills.jsonl", "a") as fh:
        fh.write("\n")
    assert store.take(SID, str(folder)) == 0 and store.page(0)["lines"] == [] and store.page(0)["dropped"] == 0
    _write(folder, _row(1))
    assert store.take(SID, str(folder)) == 1 and _ids(store) == [f"{1:016x}"]


def test_the_most_lines_one_take_examines_is_four_thousand():
    assert skillcalls.LINES_MOST == 4000


BAD = [
    ("not json", "not json"), ("an array", "[1, 2]"), ("null", "null"), ("a string", '"x"'),
    ("a missing field", {k: v for k, v in _row(1).items() if k != "version"}),
    ("a field more", dict(_row(1), arguments="x")), ("a field renamed", {**{k: v for k, v in _row(1).items() if k != "skill"}, "name": "review"}),
    ("an id of the wrong length", _row(1, id="abc")), ("an id not hex", _row(1, id="z" * 16)),
    ("an id upper-case", _row(1, id="A" * 16)), ("an id a number", _row(1, id=5)),
    ("at a string", _row(1, at="5")), ("at a float", _row(1, at=1.5)), ("at zero", _row(1, at=0)),
    ("at negative", _row(1, at=-5)), ("at a bool", _row(1, at=True)),
    ("at a year ahead", _row(1, at=int(time.time()) + 365 * 86400)),
    ("a name with a space", _row(1, skill="a b")), ("a name with a slash", _row(1, skill="a/b")),
    ("a name empty", _row(1, skill="")), ("a name too long", _row(1, skill="x" * 81)),
    ("a name a number", _row(1, skill=5)), ("a name first a dash", _row(1, skill="-a")),
    ("a kind unknown", _row(1, kind="builtin")), ("a kind a list", _row(1, kind=["core"])),
    ("a path unknown", _row(1, path="auto")),
    ("a typed call that failed", _row(1, path="typed", failed=True)),
    ("a typed call that did not", _row(1, path="typed", failed=False)),
    ("a call Claude chose with no outcome", _row(1, path="claude", failed=None)),
    ("an outcome a string", _row(1, failed="no")), ("an outcome a number", _row(1, failed=0)),
    ("a typed other", _row(1, kind="other", path="typed", failed=None, version=None)),
    ("a business version of the wrong form", _row(1, version="main")), ("a business version short", _row(1, version="a" * 39)),
    ("a business version upper", _row(1, version="A" * 40)), ("a business version none", _row(1, version=None)),
    ("a business version a core's", _row(1, version=f"1.0.0+{DIGEST}")),
    ("a core version a commit", _row(1, kind="core", skill="proofpane-core:x", version=SHA)),
    ("a core version without a release", _row(1, kind="core", skill="proofpane-core:x", version=f"+{DIGEST}")),
    ("a core version with a short digest", _row(1, kind="core", skill="proofpane-core:x", version="1.0.0+abc")),
    ("a core version none", _row(1, kind="core", skill="proofpane-core:x", version=None)),
    ("an other that has a version", _row(1, kind="other", version=SHA)),
    ("a line too long", _row(1, skill="x" * 80, version=SHA) | {"id": "0" * 16} | {"pad": "p" * 3000}),
]


@pytest.mark.parametrize("what, bad", BAD, ids=[b[0] for b in BAD])
def test_a_line_out_of_shape_is_dropped_and_counted_and_the_others_taken(tmp_path, folder, what, bad):
    store = _store(tmp_path)
    _write(folder, _row(1), bad, _row(2))
    assert store.take(SID, str(folder)) == 2
    page = store.page(0)
    assert [r["id"] for r in page["lines"]] == [f"{1:016x}", f"{2:016x}"] and page["dropped"] == 1


def test_a_line_longer_than_the_most_is_out_of_shape_though_its_json_is_valid_and_one_of_exactly_the_most_is_taken(
        tmp_path, folder):
    """The BAD list's long line also has a field more, so it is out of shape without the bound: a line with the hook's
    fields alone, padded past LINE_MOST bytes with white space, is valid JSON the hook never wrote (with the bound
    removed, it would be taken)."""
    store = _store(tmp_path)

    def padded(n, size):
        text = json.dumps(_row(n))
        assert len(text) < size
        return text + " " * (size - len(text))

    most = skillcalls.LINE_MOST
    _write(folder, padded(1, most), padded(2, most + 1), padded(3, 10 * most), _row(4))
    assert store.take(SID, str(folder)) == 2
    page = store.page(0)
    assert [r["id"] for r in page["lines"]] == [f"{1:016x}", f"{4:016x}"] and page["dropped"] == 2


def test_a_line_that_is_not_utf_8_is_dropped_too(tmp_path, folder):
    store = _store(tmp_path)
    with open(folder / "skills.jsonl", "ab") as fh:
        fh.write(b'{"id": "\xff\xfe"}\n' + json.dumps(_row(1)).encode() + b"\n")
    assert store.take(SID, str(folder)) == 1 and store.page(0)["dropped"] == 1


@pytest.mark.parametrize("given", [
    {}, {"failed": True}, {"version": "unknown"}, {"version": "uncommitted"}, {"version": "c" * 64},
    {"kind": "core", "skill": "proofpane-core:x", "version": f"1.0.0+{DIGEST}"},
    {"kind": "core", "skill": "proofpane-core:x", "version": "1.0.0+cut"},
    {"kind": "core", "skill": "proofpane-core:x", "version": "2.10.3-rc.1+unknown"},
    {"kind": "core", "skill": "proofpane-core:x", "path": "typed", "failed": None, "version": f"1.0.0+{DIGEST}"},
    {"path": "typed", "failed": None},
    {"kind": "other", "skill": "commit", "version": None},
    {"kind": "other", "skill": "plugin:thing", "version": None, "failed": True},
])
def test_the_lines_the_hook_writes_are_all_in_shape(tmp_path, folder, given):
    store = _store(tmp_path)
    _write(folder, _row(1, **given))
    assert store.take(SID, str(folder)) == 1 and store.page(0)["dropped"] == 0


def test_a_line_the_hook_just_wrote_is_in_shape(tmp_path, folder):
    # the shape is the hook's: a line written by the real script
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "PP_SESSION_DIR": str(folder), "CLAUDE_CONFIG_DIR": str(tmp_path)}

    def hook(name, skill):
        event = {"hook_event_name": name, "tool_name": "Skill", "tool_input": {"skill": skill}}
        subprocess.run([sys.executable, os.path.join(ROOT, "proofpane_workspace", "activity.py"), "--skills"],
                       input=json.dumps(event), text=True, env=env, timeout=60)

    hook("PostToolUse", "proofpane-core:ai-risk-assessment")
    hook("PostToolUse", "somewhere")
    hook("PostToolUseFailure", "proofpane-core:ai-risk-assessment")
    hook("PostToolUseFailure", "somewhere")                     # a failed call of any skill is counted too
    store = _store(tmp_path)
    assert store.take(SID, str(folder)) == 4 and store.page(0)["dropped"] == 0
    assert [(r["kind"], r["failed"]) for r in store.page(0)["lines"]] == [
        ("core", False), ("other", False), ("core", True), ("other", True)]


def test_a_line_seen_again_is_never_taken_twice(tmp_path, folder):
    store = _store(tmp_path)
    _write(folder, _row(1), _row(1), _row(2))                          # a program wrote one line twice
    assert store.take(SID, str(folder)) == 2
    assert _ids(store) == [f"{1:016x}", f"{2:016x}"]


# a cut


def _trim(folder, keep):
    activity._trim(str(folder / "skills.jsonl"), keep)


def test_a_cut_file_is_read_from_the_line_after_the_last_one_taken_and_none_is_taken_twice(tmp_path, folder):
    store = _store(tmp_path)
    _write(folder, *[_row(n) for n in range(1, 11)])
    assert store.take(SID, str(folder)) == 10
    size = os.path.getsize(folder / "skills.jsonl")
    inode = os.stat(folder / "skills.jsonl").st_ino
    _trim(folder, size // 2)                                           # the newest half: lines 6 to 10, taken before
    assert os.stat(folder / "skills.jsonl").st_ino != inode             # a new file in its place: a cut is a new identity
    _write(folder, _row(11), _row(12))
    assert store.take(SID, str(folder)) == 2
    assert _ids(store) == [f"{n:016x}" for n in range(1, 13)]


def test_a_cut_that_took_away_lines_not_yet_read_loses_those_and_none_that_it_kept(tmp_path, folder):
    store = _store(tmp_path)
    _write(folder, *[_row(n) for n in range(1, 6)])
    assert store.take(SID, str(folder)) == 5
    _write(folder, *[_row(n) for n in range(6, 13)])                    # lines 6 to 12, not yet read
    size = os.path.getsize(folder / "skills.jsonl")
    _trim(folder, int(size * 0.45))                                     # keeps only the newest of them
    kept = [json.loads(line)["id"] for line in (folder / "skills.jsonl").read_text().splitlines()]
    assert 0 < len(kept) < 7 and f"{5:016x}" not in kept and f"{12:016x}" in kept
    _write(folder, _row(13))
    assert store.take(SID, str(folder)) == len(kept) + 1
    assert _ids(store)[5:] == kept + [f"{13:016x}"]


def test_a_cut_that_kept_lines_already_taken_takes_only_the_new_ones(tmp_path, folder):
    store = _store(tmp_path)
    _write(folder, *[_row(n) for n in range(1, 9)])
    assert store.take(SID, str(folder)) == 8
    _trim(folder, os.path.getsize(folder / "skills.jsonl") // 2)
    assert 2 < len((folder / "skills.jsonl").read_text().splitlines()) < 8
    _write(folder, _row(9))
    assert store.take(SID, str(folder)) == 1
    assert _ids(store) == [f"{n:016x}" for n in range(1, 10)]


def test_a_file_shorter_than_the_offset_read_to_is_a_cut_though_it_is_the_same_file(tmp_path, folder):
    store = _store(tmp_path)
    _write(folder, *[_row(n) for n in range(1, 6)])
    assert store.take(SID, str(folder)) == 5
    inode = os.stat(folder / "skills.jsonl").st_ino
    with open(folder / "skills.jsonl", "r+") as fh:                     # emptied in place and written to again
        fh.truncate(0)
        fh.write(json.dumps(_row(20)) + "\n")
    assert os.stat(folder / "skills.jsonl").st_ino == inode
    assert os.path.getsize(folder / "skills.jsonl") < store.record["cursors"][SID]["offset"]
    assert store.take(SID, str(folder)) == 1
    assert _ids(store)[-1] == f"{20:016x}" and len(_ids(store)) == 6


def test_a_file_whose_line_at_the_offset_is_not_the_last_taken_is_a_cut_though_it_is_not_shorter(tmp_path, folder):
    store = _store(tmp_path)
    _write(folder, *[_row(n) for n in range(1, 4)])
    assert store.take(SID, str(folder)) == 3
    inode = os.stat(folder / "skills.jsonl").st_ino
    rows = [_row(n) for n in range(30, 36)]                              # the same file written over, and longer
    with open(folder / "skills.jsonl", "r+") as fh:
        fh.truncate(0)
        fh.write("".join(json.dumps(r) + "\n" for r in rows))
    assert os.stat(folder / "skills.jsonl").st_ino == inode
    assert os.path.getsize(folder / "skills.jsonl") >= store.record["cursors"][SID]["offset"]
    assert store.take(SID, str(folder)) == 6
    assert _ids(store)[3:] == [f"{n:016x}" for n in range(30, 36)]


def test_a_file_replaced_by_another_that_holds_the_last_line_taken_goes_on_after_it(tmp_path, folder):
    store = _store(tmp_path)
    rows = [_row(n) for n in range(1, 5)]
    _write(folder, *rows)
    assert store.take(SID, str(folder)) == 4
    os.unlink(folder / "skills.jsonl")                                  # another file, with the last lines and a new one
    _write(folder, *rows[2:], _row(5))
    assert store.take(SID, str(folder)) == 1
    assert _ids(store) == [f"{n:016x}" for n in range(1, 6)]


def test_a_line_out_of_shape_as_the_last_is_found_as_well(tmp_path, folder):
    store = _store(tmp_path)
    _write(folder, _row(1), "garbage with no id")
    assert store.take(SID, str(folder)) == 1 and store.page(0)["dropped"] == 1
    _trim(folder, os.path.getsize(folder / "skills.jsonl") // 2 + 5)
    assert (folder / "skills.jsonl").read_text().splitlines() == ["garbage with no id"]
    _write(folder, _row(2))
    assert store.take(SID, str(folder)) == 1 and store.page(0)["dropped"] == 1     # the garbage is not counted twice


def test_a_file_that_goes_and_comes_back_is_read_again_from_its_start(tmp_path, folder):
    store = _store(tmp_path)
    _write(folder, _row(1), _row(2))
    assert store.take(SID, str(folder)) == 2
    os.unlink(folder / "skills.jsonl")
    assert store.take(SID, str(folder)) == 0
    _write(folder, _row(3), _row(4))
    assert store.take(SID, str(folder)) == 2
    assert _ids(store) == [f"{n:016x}" for n in (1, 2, 3, 4)]


def test_a_file_is_taken_a_part_at_a_time_when_it_is_larger_than_one_read(tmp_path, folder, monkeypatch):
    monkeypatch.setattr(skillcalls, "READ_MOST", 2000)
    store = _store(tmp_path)
    _write(folder, *[_row(n) for n in range(1, 41)])
    assert os.path.getsize(folder / "skills.jsonl") > 3 * 2000
    got, takes = 0, 0
    while got < 40:
        added = store.take(SID, str(folder))
        assert added > 0
        got += added
        takes += 1
        assert takes < 20
    assert takes > 1 and _ids(store) == [f"{n:016x}" for n in range(1, 41)]
    assert store.take(SID, str(folder)) == 0


# across a restart, and what is on the volume


def test_a_restart_takes_no_line_twice_and_the_record_keeps_its_id(tmp_path, folder):
    store = _store(tmp_path)
    _write(folder, _row(1), _row(2))
    store.take(SID, str(folder), "demo")
    again = _store(tmp_path)
    assert again.record["store"] == store.record["store"] and _ids(again) == _ids(store)
    assert again.take(SID, str(folder), "demo") == 0
    _write(folder, _row(3))
    assert again.take(SID, str(folder), "demo") == 1
    assert [r["n"] for r in again.page(0)["lines"]] == [1, 2, 3]


def test_a_restart_between_a_take_and_its_record_takes_no_line_twice_by_its_id(tmp_path, folder):
    store = _store(tmp_path)
    _write(folder, _row(1), _row(2))
    store.take(SID, str(folder), flush=False)                          # the record never written
    store.record["cursors"].clear()                                      # as if the cursor was lost with it
    store._dirty = False
    _write(folder, _row(3))
    assert store.take(SID, str(folder)) == 1                            # lines 1 and 2 are held already, by their ids
    assert _ids(store) == [f"{n:016x}" for n in (1, 2, 3)]


def test_a_record_made_anew_has_an_id_of_its_own_and_takes_the_files_again(tmp_path, folder):
    store = _store(tmp_path)
    _write(folder, _row(1))
    store.take(SID, str(folder))
    os.unlink(tmp_path / "ws" / "skills" / skillcalls.FILE)
    fresh = _store(tmp_path)
    assert fresh.record["store"] != store.record["store"] and fresh.page(0)["lines"] == []
    assert fresh.take(SID, str(folder)) == 1 and [r["n"] for r in fresh.page(0)["lines"]] == [1]


@pytest.mark.parametrize("content", [b"{not json", b"[]", b'{"v": 2}', b'{"v": 1, "store": "x"}', b"\xff\xfe", b"",
                                      json.dumps({"v": 1, "store": "a" * 32, "next": 5, "dropped": 0, "cursors": {},
                                                  "lines": [dict(n=1, session=SID, repo=None, task=None,
                                                                 **{k: _row(1)[k] for k in skillcalls.FIELDS}),
                                                            dict(n=1, session=SID, repo=None, task=None,
                                                                 **{k: _row(2)[k] for k in skillcalls.FIELDS})]}).encode(),
                                      json.dumps({"v": 1, "store": "a" * 32, "next": 2, "dropped": 0, "cursors": {},
                                                  "lines": [dict(n=1, session=SID, repo=None, task=None,
                                                                 **dict({k: _row(1)[k] for k in skillcalls.FIELDS},
                                                                        kind="builtin"))]}).encode(),
                                      json.dumps({"v": 1, "store": "a" * 32, "next": 2, "dropped": 0,
                                                  "cursors": {"zz": {"dev": 1, "ino": 1, "offset": 5, "tail": "a" * 32}},
                                                  "lines": []}).encode()])
def test_a_record_that_is_not_one_the_workspace_wrote_is_put_aside_and_a_fresh_one_made(tmp_path, content):
    folder_ = tmp_path / "ws" / "skills"
    folder_.mkdir(parents=True)
    (folder_ / skillcalls.FILE).write_bytes(content)
    store = SkillCalls(str(folder_))
    assert store.page(0)["lines"] == [] and store.record["next"] == 1
    assert (folder_ / (skillcalls.FILE + ".unread")).read_bytes() == content


def test_a_record_too_large_or_no_file_is_put_aside_not_read(tmp_path, monkeypatch):
    folder_ = tmp_path / "ws" / "skills"
    folder_.mkdir(parents=True)
    (folder_ / "elsewhere").write_text("x")
    os.symlink(folder_ / "elsewhere", folder_ / skillcalls.FILE)
    store = SkillCalls(str(folder_))
    assert store.record["lines"] == [] and not os.path.islink(folder_ / skillcalls.FILE)
    assert (folder_ / "elsewhere").read_text() == "x"


def test_a_link_put_where_the_record_is_after_its_check_is_put_aside_and_never_read_through(
        tmp_path, folder, monkeypatch):
    """The record's name is checked (a regular file, not too large) and then opened: two calls, and a program of a
    session can put a link at the name between them. The open does not follow one (O_NOFOLLOW), so a record is not read
    through a link. Here the check is made to say 'a regular file' for the link, as it would have a moment before the
    link came."""
    other = SkillCalls(str(tmp_path / "other"))                         # a record the workspace wrote, elsewhere
    _write(folder, _row(1))
    assert other.take(SID, str(folder)) == 1
    target = tmp_path / "other" / skillcalls.FILE
    assert target.is_file() and other.record["lines"]
    held = target.read_bytes()
    where = tmp_path / "ws" / "skills"
    where.mkdir(parents=True)
    link = str(where / skillcalls.FILE)
    os.symlink(target, link)
    real = os.lstat
    monkeypatch.setattr(skillcalls.os, "lstat",
                        lambda path, *a, **kw: real(target) if path == link else real(path, *a, **kw))
    store = SkillCalls(str(where))
    monkeypatch.undo()                                                  # the lstat that lied would answer for these too
    assert store.record["store"] != other.record["store"] and store.record["lines"] == []
    assert os.path.islink(link + ".unread") and not os.path.lexists(link)      # the link renamed, never followed
    assert target.read_bytes() == held


def test_the_record_is_written_private_in_a_folder_of_its_own_and_never_through_a_link(tmp_path, folder):
    store = _store(tmp_path)
    _write(folder, _row(1))
    store.take(SID, str(folder))
    path = tmp_path / "ws" / "skills" / skillcalls.FILE
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(tmp_path / "ws" / "skills").st_mode) == 0o700
    assert not list((tmp_path / "ws" / "skills").glob("*.tmp"))
    # a link put where the folder goes: the record is not written through it, and the record in memory goes on
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link = tmp_path / "ws2" / "skills"
    link.parent.mkdir()
    os.symlink(elsewhere, link)
    store = SkillCalls(str(link))
    _write(folder, _row(2))
    assert store.take(SID, str(folder)) == 2
    assert list(elsewhere.iterdir()) == [] and len(store.page(0)["lines"]) == 2


def test_a_link_or_a_fifo_where_a_session_s_file_goes_is_not_followed_or_waited_on(tmp_path, folder):
    store = _store(tmp_path)
    target = tmp_path / "other.jsonl"
    target.write_text(json.dumps(_row(1)) + "\n")
    os.symlink(target, folder / "skills.jsonl")
    assert store.take(SID, str(folder)) == 0
    os.unlink(folder / "skills.jsonl")
    os.mkfifo(folder / "skills.jsonl")
    begun = time.time()
    assert store.take(SID, str(folder)) == 0 and time.time() - begun < 10
    assert store.page(0)["lines"] == []


@pytest.mark.parametrize("sid", ["", "x", "0123456789AB", "0123456789abc", "../0123456789", None, 5])
def test_a_session_whose_id_is_not_a_session_s_is_not_taken_from(tmp_path, folder, sid):
    store = _store(tmp_path)
    _write(folder, _row(1))
    assert store.take(sid, str(folder)) == 0 and store.page(0)["lines"] == []


def test_two_takes_at_once_take_each_line_once(tmp_path, folder):
    store = _store(tmp_path)
    _write(folder, *[_row(n) for n in range(1, 301)])
    results = []
    threads = [threading.Thread(target=lambda: results.append(store.take(SID, str(folder)))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(results) == 300 and _ids(store) == [f"{n:016x}" for n in range(1, 301)]


# bounds and pages


def test_the_oldest_lines_go_first_past_the_most_it_keeps_and_the_numbers_go_on(tmp_path, folder, monkeypatch):
    monkeypatch.setattr(skillcalls, "MAX_LINES", 5)
    store = _store(tmp_path)
    _write(folder, *[_row(n) for n in range(1, 4)])
    store.take(SID, str(folder))
    _write(folder, *[_row(n) for n in range(4, 9)])
    store.take(SID, str(folder))
    page = store.page(0)
    assert [r["n"] for r in page["lines"]] == [4, 5, 6, 7, 8] and page["first"] == 4 and page["seq"] == 8
    _write(folder, _row(9))
    store.take(SID, str(folder))
    assert [r["n"] for r in store.page(0)["lines"]] == [5, 6, 7, 8, 9]       # a number is never used again


def test_a_line_older_than_the_days_it_keeps_goes_and_is_not_one_out_of_shape(tmp_path, folder):
    clock = [time.time()]
    store = _store(tmp_path, now=lambda: clock[0])
    old = int(clock[0]) - (skillcalls.KEPT_DAYS * 86400 + 60)
    fresh = int(clock[0]) - (skillcalls.KEPT_DAYS * 86400 - 60)
    _write(folder, _row(1, at=old), _row(2, at=fresh), _row(3))
    store.take(SID, str(folder))
    assert [r["id"] for r in store.page(0)["lines"]] == [f"{2:016x}", f"{3:016x}"] and store.page(0)["dropped"] == 0
    clock[0] += 2 * 60                                                      # the second one is now past its days
    _write(folder, _row(4, at=int(clock[0])))
    store.take(SID, str(folder))
    assert [r["id"] for r in store.page(0)["lines"]] == [f"{3:016x}", f"{4:016x}"]


def test_a_line_from_the_future_is_not_taken_past_a_day_s_slack(tmp_path, folder):
    store = _store(tmp_path)
    _write(folder, _row(1, at=int(time.time()) + skillcalls.SLACK - 100), _row(2, at=int(time.time()) + skillcalls.SLACK + 100))
    assert store.take(SID, str(folder)) == 1 and store.page(0)["dropped"] == 1


def test_a_line_exactly_as_old_as_the_days_it_keeps_stays_and_one_second_older_goes(tmp_path, folder):
    clock = [1_800_000_000.0]
    store = _store(tmp_path, now=lambda: clock[0])
    floor = int(clock[0]) - skillcalls.KEPT_DAYS * 86400
    _write(folder, _row(1, at=floor - 1), _row(2, at=floor))
    store.take(SID, str(folder))
    assert _ids(store) == [f"{2:016x}"] and store.page(0)["dropped"] == 0


def test_a_line_may_be_dated_a_day_ahead_of_the_workspace_s_clock_and_not_a_second_more(tmp_path, folder):
    clock = [1_800_000_000.0]
    store = _store(tmp_path, now=lambda: clock[0])
    now = int(clock[0])
    _write(folder, _row(1, at=now + 86400), _row(2, at=now + 86400 + 1))
    assert store.take(SID, str(folder)) == 1 and _ids(store) == [f"{1:016x}"] and store.page(0)["dropped"] == 1
    assert skillcalls.SLACK == 86400


def test_the_numbers_the_manual_and_the_page_state_of_the_record_are_the_code_s():
    """The manual says how much the record keeps, pages and reads at once, and how often it takes; the page says what it
    can miss past the same bounds: each number is read back from the code (these once moved, in twelve tests' sight,
    with every test of the record still passing)."""
    assert stated(r"keeps at most ([0-9,]+) lines and [0-9]+ days") == skillcalls.MAX_LINES
    assert stated(r"keeps at most [0-9,]+ lines and ([0-9]+) days") == skillcalls.KEPT_DAYS
    assert stated(r"at most ([0-9,]+) lines numbered after N") == skillcalls.PAGE
    assert stated(r"past the first ([0-9,]+) a take looks at") == skillcalls.LINES_MOST
    assert stated(r"digest of its last ([0-9,]+) bytes") == skillcalls.TAIL_KEEP
    assert stated(r"A stretch of ([0-9]+) MB \(`skillcalls.READ_MOST`\)") * 1024 * 1024 == skillcalls.READ_MOST
    assert stated(r"at most every (two) seconds") == workspace_module.SKILLS_EVERY
    assert stated(r"dated up to (a) day ahead") * 86400 == skillcalls.SLACK
    page = file_text("ui", "src", "components", "Skills.tsx")
    assert stated(r"past ([0-9]+) days or [0-9,]+ lines", page) == skillcalls.KEPT_DAYS
    assert stated(r"past [0-9]+ days or ([0-9,]+) lines", page) == skillcalls.MAX_LINES


def test_a_page_is_at_most_a_thousand_lines_with_whether_more_follow(tmp_path, folder):
    store = _store(tmp_path)
    _write(folder, *[_row(n) for n in range(1, 2502)])
    for _ in range(4):
        store.take(SID, str(folder))
    assert skillcalls.PAGE == 1000
    first = store.page(0)
    assert len(first["lines"]) == 1000 and first["more"] is True and first["seq"] == 1000 and first["first"] == 1
    second = store.page(first["seq"])
    assert [r["n"] for r in second["lines"]][:2] == [1001, 1002] and len(second["lines"]) == 1000 and second["more"] is True
    third = store.page(second["seq"])
    assert len(third["lines"]) == 501 and third["more"] is False and third["seq"] == 2501
    assert store.page(third["seq"])["lines"] == [] and store.page(third["seq"])["more"] is False
    assert {p["store"] for p in (first, second, third)} == {store.record["store"]}


def test_a_page_after_the_last_gives_where_the_numbers_are_and_nothing_else(tmp_path, folder):
    store = _store(tmp_path)
    assert store.page(0) == {"store": store.record["store"], "seq": 0, "more": False, "first": None, "dropped": 0,
                             "lines": []}
    _write(folder, _row(1), _row(2))
    store.take(SID, str(folder))
    got = store.page(99)
    assert got["lines"] == [] and got["seq"] == 2 and got["first"] == 1 and got["more"] is False


def test_a_page_s_lines_are_copies_no_caller_changes_the_record_through(tmp_path, folder):
    store = _store(tmp_path)
    _write(folder, _row(1))
    store.take(SID, str(folder))
    store.page(0)["lines"][0]["skill"] = "changed"
    assert store.page(0)["lines"][0]["skill"] == "review"


def test_a_session_gone_has_its_place_let_go_and_its_lines_stay_with_where_they_were_made(tmp_path, folder):
    store = _store(tmp_path)
    _write(folder, _row(1))
    store.take(SID, str(folder), "demo", "task-1")
    assert SID in store.record["cursors"]
    store.forget({"ffffffffffff"})
    assert store.record["cursors"] == {}
    assert [(r["session"], r["repo"], r["task"]) for r in store.page(0)["lines"]] == [(SID, "demo", "task-1")]
    again = _store(tmp_path)
    assert again.record["cursors"] == {} and len(again.page(0)["lines"]) == 1


# the gate


@pytest.mark.parametrize("call", [
    {"tool_name": "Read", "tool_input": {"file_path": "/home/u/ws/skills/skill-calls.json"}},
    {"tool_name": "Write", "tool_input": {"file_path": "skill-calls.json", "content": "{}"}},
    {"tool_name": "Edit", "tool_input": {"file_path": "/w/skill-calls.json", "old_string": "a", "new_string": "b"}},
    {"tool_name": "Bash", "tool_input": {"command": "cat ../../skills/skill-calls.json"}},
    {"tool_name": "Bash", "tool_input": {"command": "python3 -c \"open('skill-calls.json','w').write('{}')\""}},
    {"tool_name": "Bash", "tool_input": {"command": "cp x skill-calls.json.unread"}},
    {"tool_name": "Bash", "tool_input": {"command": "ls skills/skill-calls.json.tmp"}},
    {"tool_name": "Grep", "tool_input": {"pattern": "x", "path": "/ws/skills/skill-calls.json"}},
    {"tool_name": "mcp__fs__read", "tool_input": {"path": "/ws/skills/skill-calls.json"}},
    # spelled as a shell reads it, not as it is written: a quote inside the word, a backslash, a variable set for the
    # command, in a word list, and in a field another tool types a command in (the gate's `read` of each command)
    {"tool_name": "Bash", "tool_input": {"command": "cat skill-'calls.json'"}},
    {"tool_name": "Bash", "tool_input": {"command": 'cat "skill-calls".json'}},
    {"tool_name": "Bash", "tool_input": {"command": 'cat skill-ca""lls.json'}},
    {"tool_name": "Bash", "tool_input": {"command": "cat skill-calls.js\\on"}},
    {"tool_name": "Bash", "tool_input": {"command": "X=skill-calls.json cat $X"}},
    {"tool_name": "Bash", "tool_input": {"command": "cat /ws/sk'ill-calls.json'"}},
    {"tool_name": "Bash", "tool_input": {"command": ["cat", "skill-'calls.json'"]}},
    {"tool_name": "Write", "tool_input": {"cmd": "cat skill-'calls.json'"}},
    # in another case: a volume that does not tell cases apart (macOS by default) names the same file by any spelling
    {"tool_name": "Write", "tool_input": {"file_path": "/ws/skills/SKILL-CALLS.json"}},
    {"tool_name": "Bash", "tool_input": {"command": "echo x > /ws/skills/Skill-Calls.JSON"}},
    {"tool_name": "Edit", "tool_input": {"file_path": "/w/a", "new_string": "see skill-Calls.json"}},
    {"tool_name": "Bash", "tool_input": {"command": "cat Skill-'Calls.json'"}},
])
def test_a_tool_call_that_names_the_record_is_refused(call):
    assert gate.refusal(call) == gate.SKILLS_RECORD


@pytest.mark.parametrize("call", [
    {"tool_name": "Read", "tool_input": {"file_path": "/ws/sessions/0123456789ab/skills.jsonl"}},
    {"tool_name": "Bash", "tool_input": {"command": "cat skills.jsonl"}},
    {"tool_name": "Bash", "tool_input": {"command": "cat skill-calls.txt"}},
    {"tool_name": "Bash", "tool_input": {"command": "ls skills/"}},
    {"tool_name": "Read", "tool_input": {"file_path": "/w/skill_calls.json"}},
    {"tool_name": "Read", "tool_input": {"file_path": "/w/skill-callsXjson"}},
])
def test_what_is_not_the_record_is_not_refused_for_it(call):
    assert gate.refusal(call) is None


def test_the_gate_says_what_it_refuses_in_the_record_s_words():
    assert "skills" in gate.SKILLS_RECORD and "session" in gate.SKILLS_RECORD
    assert gate.SKILLS_RECORD not in (gate.PLACES_RECORD, gate.CONTROLLER_RECORD)
    assert skillcalls.FILE == "skill-calls.json" and gate._SKILLS_NAMED.search(skillcalls.FILE)


# through a real agent


@pytest.fixture
def agent(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home), {})
    make_repo(str(tmp_path / "ws" / "repos" / "demo"))
    a.sessions = tmp_path / "ws" / "sessions"
    try:
        yield a
    finally:
        a.close()


def _shell(agent, task="A shell"):
    r = agent.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": task})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_get_skills_takes_every_session_s_new_lines_and_gives_them_with_where_they_were_made(agent):
    one, two = _shell(agent, "One"), _shell(agent, "Two")
    _write(agent.sessions / one, _row(1), _row(2))
    _write(agent.sessions / two, _row(3, skill="deploy"))
    got = agent.http.get("/skills").json()
    assert set(got) == {"store", "seq", "more", "first", "dropped", "lines"}
    assert got["more"] is False and got["first"] == 1 and got["seq"] == 3 and got["dropped"] == 0
    by = {(r["session"], r["id"]): r for r in got["lines"]}
    assert {k for k in by} == {(one, f"{1:016x}"), (one, f"{2:016x}"), (two, f"{3:016x}")}
    assert all(r["repo"] == "demo" and r["task"] is None for r in got["lines"])
    _write(agent.sessions / two, _row(4))
    after = agent.http.get(f"/skills?since={got['seq']}").json()
    assert [r["id"] for r in after["lines"]] == [f"{4:016x}"] and after["store"] == got["store"] and after["seq"] == 4


@pytest.mark.parametrize("since", ["abc", "-1", "1.5", "", "12345678901234567", "1e3", "0x10", " 1"])
def test_get_skills_asks_for_a_whole_number(agent, since):
    assert agent.http.get("/skills", params={"since": since}).status_code == 400


def test_get_skills_without_since_is_from_the_start_and_needs_the_workspace_s_secret(agent):
    one = _shell(agent)
    _write(agent.sessions / one, _row(1))
    assert len(agent.http.get("/skills").json()["lines"]) == 1
    assert httpx.get(agent.base + "/skills").status_code == 401
    assert httpx.get(agent.base + "/skills", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_a_read_of_the_list_starts_the_take_without_waiting_for_it(agent):
    one = _shell(agent)
    _write(agent.sessions / one, _row(1))
    assert agent.http.get("/sessions").status_code == 200
    record = agent.sessions.parent / "skills" / skillcalls.FILE
    deadline = time.time() + 20
    while True:
        if record.exists() and json.loads(record.read_text())["lines"]:
            break
        assert time.time() < deadline, "the read of the list did not start the take"
        time.sleep(0.1)
    assert json.loads(record.read_text())["lines"][0]["session"] == one


def test_the_lines_of_a_session_removed_are_taken_before_its_folder_goes_and_stay(agent):
    one, keep = _shell(agent, "One"), _shell(agent, "Keep")
    _write(agent.sessions / one, _row(1))
    assert agent.http.delete(f"/sessions/{one}").status_code == 200
    assert not (agent.sessions / one).exists()
    got = agent.http.get("/skills").json()
    assert [(r["session"], r["repo"]) for r in got["lines"]] == [(one, "demo")]
    _write(agent.sessions / keep, _row(2))
    assert [r["session"] for r in agent.http.get(f"/skills?since={got['seq']}").json()["lines"]] == [keep]


def _in_process(tmp_path, monkeypatch):
    """A workspace of the test's own, in this process, whose own takes (the poke of a read) are off, so that the take a
    test asks about is the only one: the agent above races its own."""
    from proofpane_workspace import workspace as ws_module
    make_repo(str(tmp_path / "ws" / "repos" / "demo"))
    w = ws_module.Workspace(str(tmp_path / "ws"))
    monkeypatch.setattr(w, "_skills_poke", lambda: None)
    return w


def test_a_session_removed_has_its_lines_taken_by_the_removal_itself_with_where_they_were_made(tmp_path, monkeypatch):
    import asyncio
    import pathlib
    w = _in_process(tmp_path, monkeypatch)
    record, _ = w.prepare("demo", "shell", "Gone")
    monkeypatch.setattr(w.controller, "task_id_of", lambda sid: "task-9" if sid == record["id"] else None)
    _write(pathlib.Path(w._dir(record["id"])), _row(1))
    assert w.skill_calls.page(0)["lines"] == []                          # nothing took it before the removal
    asyncio.run(w.remove(record["id"]))
    assert not os.path.exists(w._dir(record["id"]))
    assert [(r["session"], r["repo"], r["task"]) for r in w.skill_calls.page(0)["lines"]] == [
        (record["id"], "demo", "task-9")]


def test_a_removal_goes_on_though_the_take_before_it_fails(tmp_path, monkeypatch):
    """The take of a session's last lines before its folder goes is for the count's sake: a failure of it never stops
    the removal (the manual: "a failure of that never stops the removal")."""
    import asyncio
    w = _in_process(tmp_path, monkeypatch)
    record, _ = w.prepare("demo", "shell", "Gone")

    def broken(*args, **kwargs):
        raise OSError("the record cannot be written")
    monkeypatch.setattr(w.skill_calls, "take", broken)
    result = asyncio.run(w.remove(record["id"]))
    assert result["removed"] == record["id"] and not os.path.exists(w._dir(record["id"]))


def test_a_read_of_one_session_asks_for_the_take_as_a_read_of_the_list_does(tmp_path, monkeypatch):
    """The manual: the workspace takes the lines "when it reads that session or the list"."""
    w = _in_process(tmp_path, monkeypatch)
    record, _ = w.prepare("demo", "shell", "One")
    asked = []
    monkeypatch.setattr(w, "_skills_poke", lambda: asked.append("asked"))
    w.session(record["id"])
    assert asked == ["asked"]
    w.sessions()
    assert asked == ["asked", "asked"]


def test_a_session_s_lines_are_taken_with_the_controller_s_task_for_it_where_it_has_one(tmp_path, monkeypatch):
    import pathlib
    w = _in_process(tmp_path, monkeypatch)
    one, _ = w.prepare("demo", "shell", "A task's")
    two, _ = w.prepare("demo", "shell", "Not a task's")
    monkeypatch.setattr(w.controller, "task_id_of", lambda sid: "task-9" if sid == one["id"] else None)
    _write(pathlib.Path(w._dir(one["id"])), _row(1))
    _write(pathlib.Path(w._dir(two["id"])), _row(2))
    assert w.take_skill_calls() == 2
    assert {r["session"]: (r["repo"], r["task"]) for r in w.skill_calls.page(0)["lines"]} == {
        one["id"]: ("demo", "task-9"), two["id"]: ("demo", None)}


def test_the_controller_gives_the_id_of_the_task_it_made_for_a_session_and_none_for_any_other(tmp_path, monkeypatch):
    w = _in_process(tmp_path, monkeypatch)
    one, two = "a" * 32, "b" * 32
    w.controller.record["tasks"][one] = {"id": one, "session": "s-one"}
    w.controller.record["tasks"][two] = {"id": two, "session": "s-two"}
    assert w.controller.task_id_of("s-one") == one
    assert w.controller.task_id_of("s-two") == two
    assert w.controller.task_id_of("s-three") is None
    assert w.controller.task_id_of("") is None


def test_the_task_id_is_read_under_the_controller_s_lock(tmp_path, monkeypatch):
    """The workspace takes a session's lines in a thread of its own, while the controller changes its record in the
    loop's: a read of the record's tasks outside the lock can meet a dictionary that changed size."""
    w = _in_process(tmp_path, monkeypatch)
    one = "a" * 32
    w.controller.record["tasks"][one] = {"id": one, "session": "s-one"}
    got = []
    reader = threading.Thread(target=lambda: got.append(w.controller.task_id_of("s-one")))
    with w.controller._lock:
        reader.start()
        reader.join(0.5)
        assert got == []                                    # held while the lock is held
    reader.join(10)
    assert got == [one]


def test_a_session_that_is_gone_has_its_place_let_go_by_the_workspace_s_take_and_its_lines_stay(tmp_path, monkeypatch):
    import pathlib
    import shutil
    w = _in_process(tmp_path, monkeypatch)
    one, _ = w.prepare("demo", "shell", "Gone")
    two, _ = w.prepare("demo", "shell", "Staying")
    _write(pathlib.Path(w._dir(one["id"])), _row(1))
    _write(pathlib.Path(w._dir(two["id"])), _row(2))
    assert w.take_skill_calls() == 2 and set(w.skill_calls.record["cursors"]) == {one["id"], two["id"]}
    shutil.rmtree(w._dir(one["id"]))
    w.take_skill_calls()
    assert set(w.skill_calls.record["cursors"]) == {two["id"]}
    assert sorted(r["session"] for r in w.skill_calls.page(0)["lines"]) == sorted([one["id"], two["id"]])


def test_two_passes_of_the_take_are_one_at_a_time(tmp_path, monkeypatch):
    """The thread's pass and the route's would otherwise run inside one another: a pass that listed
    the sessions before the other began would let go the place of a session the other took. A pass held inside the
    take of a session keeps a second one out of it."""
    w = _in_process(tmp_path, monkeypatch)
    w.prepare("demo", "shell", "One")
    entered, inside, release = [], threading.Event(), threading.Event()
    real = w.skill_calls.take

    def take(*args, **kwargs):
        entered.append(threading.current_thread().name)
        if len(entered) == 1:
            inside.set()
            release.wait(10)
        return real(*args, **kwargs)
    monkeypatch.setattr(w.skill_calls, "take", take)
    first = threading.Thread(target=w.take_skill_calls, name="first")
    second = threading.Thread(target=w.take_skill_calls, name="second")
    first.start()
    assert inside.wait(5)
    second.start()
    second.join(0.5)
    assert entered == ["first"] and second.is_alive()                  # the second waits for the first's pass
    release.set()
    first.join(10)
    second.join(10)
    assert entered == ["first", "second"] and not first.is_alive() and not second.is_alive()


# the thread that takes after a read of the list or of a session (Workspace._skills_poke)


def _polled(tmp_path, monkeypatch, every=0.4):
    """A workspace of the test's own, in this process, whose take is a stand-in that notes when it ran (and what `act`
    makes it do), and whose seconds between takes are `every`."""
    from proofpane_workspace import workspace as ws_module
    make_repo(str(tmp_path / "ws" / "repos" / "demo"))
    monkeypatch.setattr(ws_module, "SKILLS_EVERY", every)      # before the workspace is made: it dates its first take
    w = ws_module.Workspace(str(tmp_path / "ws"))
    ran, act = [], []

    def take():
        ran.append(time.monotonic())
        if act:
            act.pop(0)()
        return 0
    monkeypatch.setattr(w, "take_skill_calls", take)
    return w, ran, act


def _until(condition, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def test_the_first_read_is_taken_at_once_whatever_the_wait(tmp_path, monkeypatch):
    """The wait is between takes, not before the first: with 30 seconds of it, the first take still comes at once. (No
    bound on how soon: a loaded machine can be late, and only a wait that is waited before the first take, 30 seconds
    here, misses the five the test allows.)"""
    w, ran, act = _polled(tmp_path, monkeypatch, every=30)
    w._skills_poke()
    assert _until(lambda: len(ran) == 1)


def test_reads_inside_the_wait_are_taken_once_after_it_and_never_before(tmp_path, monkeypatch):
    """Only orders and lower bounds are asserted, so that a loaded machine, which is only ever late, cannot fail it: a
    take that came early, or one per read, or one that asks again by itself, can."""
    every = 1.0
    w, ran, act = _polled(tmp_path, monkeypatch, every=every)
    w._skills_poke()
    assert _until(lambda: len(ran) == 1)
    for _ in range(5):                                   # five reads inside the wait
        w._skills_poke()
    assert _until(lambda: len(ran) == 2)
    assert ran[1] - ran[0] >= every * 0.9                # not before the wait is over
    assert _until(lambda: not w._skills_running)         # the five were one take: a take that asks again never ends
    assert len(ran) == 2
    w._skills_poke()                                     # a read after the wait starts a take of its own
    assert _until(lambda: len(ran) == 3) and ran[2] - ran[1] >= every * 0.9


def test_a_read_that_comes_while_a_take_runs_is_taken_after_it(tmp_path, monkeypatch):
    w, ran, act = _polled(tmp_path, monkeypatch, every=0.05)
    release, begun = threading.Event(), threading.Event()
    act.append(lambda: (begun.set(), release.wait(10)))
    w._skills_poke()
    assert begun.wait(5)
    w._skills_poke()                                     # the take is running: this read must not be lost
    release.set()
    assert _until(lambda: len(ran) == 2)


def test_a_thread_that_cannot_be_started_loses_one_poke_and_not_the_taking(tmp_path, monkeypatch, capsys):
    """The system can refuse a thread (RuntimeError): the read that asked is not failed by it, the poke is lost and said
    once on stderr, and the flag that says a take is running is let go, so a later read starts one (a flag left set
    would leave no take to run again in the process)."""
    w, ran, act = _polled(tmp_path, monkeypatch, every=0.05)
    real, refused = threading.Thread.start, []

    def start(self):
        if self.name == "pp-skills" and not refused:
            refused.append(1)
            raise RuntimeError("can't start new thread")
        return real(self)
    monkeypatch.setattr(threading.Thread, "start", start)
    w._skills_poke()                                     # no exception reaches the read that asked
    assert refused == [1] and not w._skills_running and ran == []
    assert capsys.readouterr().err.count("skills: session '-' left out: RuntimeError") == 1
    w._skills_poke()                                     # the next read starts the take as before
    assert _until(lambda: len(ran) == 1)


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_take_that_ends_the_thread_does_not_leave_the_workspace_with_no_taking(tmp_path, monkeypatch):
    w, ran, act = _polled(tmp_path, monkeypatch, every=0.05)
    act.append(lambda: sys.exit(1))                                    # a take that is not an error: it ends the thread
    w._skills_poke()
    assert _until(lambda: len(ran) == 1 and not w._skills_running)
    w._skills_poke()
    assert _until(lambda: len(ran) == 2)


def test_a_session_s_programs_cannot_change_the_record_through_the_gate(agent):
    one = _shell(agent)
    _write(agent.sessions / one, _row(1))
    agent.http.get("/skills")
    record = agent.sessions.parent / "skills" / skillcalls.FILE
    assert record.exists() and gate.refusal({"tool_name": "Write", "tool_input": {"file_path": str(record)}}) == \
        gate.SKILLS_RECORD


def test_a_workspace_restarted_takes_no_line_twice_and_keeps_its_id(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    root = str(tmp_path / "ws")
    a = Agent(root, str(home), {})
    make_repo(os.path.join(root, "repos", "demo"))
    try:
        sid = _shell(a)
        _write(tmp_path / "ws" / "sessions" / sid, _row(1), _row(2))
        first = a.http.get("/skills").json()
    finally:
        a.close()
    os.unlink(os.path.join(root, "agent.json"))                      # the first agent's port, which the next waits to replace
    b = Agent(root, str(home), {})
    try:
        again = b.http.get("/skills").json()
        assert again["store"] == first["store"] and [r["n"] for r in again["lines"]] == [1, 2]
        _write(tmp_path / "ws" / "sessions" / sid, _row(3))
        assert [r["n"] for r in b.http.get(f"/skills?since={again['seq']}").json()["lines"]] == [3]
    finally:
        b.close()


def test_a_session_whose_record_cannot_be_read_is_not_taken_from_but_its_place_is_kept(agent):
    one, two = _shell(agent, "One"), _shell(agent, "Two")
    _write(agent.sessions / one, _row(1))
    agent.http.get("/skills")
    (agent.sessions / one / "session.json").write_text("{not json")
    _write(agent.sessions / one, _row(2))
    _write(agent.sessions / two, _row(3))
    got = agent.http.get("/skills").json()
    assert sorted(r["id"] for r in got["lines"]) == [f"{1:016x}", f"{3:016x}"]
    record = json.loads((agent.sessions.parent / "skills" / skillcalls.FILE).read_text())
    assert one in record["cursors"]
