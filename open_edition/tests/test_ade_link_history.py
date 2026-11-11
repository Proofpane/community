"""The History list's session side (ade_link.py history, and resume): a linked machine lists its Claude Code sessions in
the ADE as its link's history.json says, from VS Code and the terminal alike: each with where it ran, whether it is
running, its folder and times, and, unless it touched a project history.json excepts, its title, branch and (where
conversations is true) its conversation, the owner's words and the session's, never a tool's. Through the app served on
a local port where the server is in it."""

import json
import os
import time

import pytest

import ade_link
from proofpane_open.ade import link as server_link
from test_ade_link_client import link_row, owner, pair, run, served  # noqa: F401  fixtures and helpers


def _write(path, entries, mode="w"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, mode, encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def _typed(text, at, cwd, branch="main", where="claude-vscode"):
    return {"type": "user", "uuid": f"u{at}", "origin": {"kind": "human"}, "turnOrigin": "human", "cwd": cwd,
            "gitBranch": branch, "timestamp": at, "entrypoint": where,
            "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def _said(text, at, cwd=None):
    e = {"type": "assistant", "uuid": f"a{at}", "timestamp": at,
         "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}
    if cwd:
        e["cwd"] = cwd
    return e


def _tool(name, tool_input, at, cwd):
    return {"type": "assistant", "uuid": f"t{at}", "timestamp": at, "cwd": cwd,
            "message": {"role": "assistant", "content": [{"type": "tool_use", "id": f"x{at}", "name": name,
                                                          "input": tool_input}]}}


def _result(text, at, cwd):
    return {"type": "user", "uuid": f"r{at}", "timestamp": at, "cwd": cwd,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": text}]}}


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".claude" / "projects").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    return h


class Dir:
    """A link's directory as the client reads it, without a server: enough for history.json's rule."""

    def __init__(self, path):
        self.dir = str(path)
        os.makedirs(self.dir, exist_ok=True)

    def rule(self, data):
        with open(os.path.join(self.dir, "history.json"), "w") as fh:
            fh.write(data if isinstance(data, str) else json.dumps(data))
        return ade_link._history_rule(self)


def test_history_json_is_read_whole_and_a_mistake_in_it_lists_nothing(tmp_path, home):
    _grove(home)
    d = Dir(tmp_path / "mac")
    assert ade_link._history_rule(d) == (None, None)                                     # none: nothing listed
    rule, why = d.rule({"projects": "all", "except": ["~/grove"], "conversations": True})
    assert why is None and rule.projects == "all" and rule.conversations is True and rule.participants is False
    assert d.rule({"projects": "all", "participants": True})[0].participants is True
    for data, words in (({"projects": "all", "exclude": ["~/grove"]}, "a key it does not know, exclude"),
                        ({"projects": "all", "except": ["grove"]}, "not written whole"),
                        ({"projects": ["work/app"]}, "not written whole"),
                        ({"projects": "all", "except": "~/grove"}, "except is a list"),
                        ({"projects": "all", "conversations": "yes"}, "conversations is true or false"),
                        ({"projects": "all", "participants": 1}, "participants is true or false"),
                        ({"projects": []}, "projects is"), ("[1]", "not a JSON object"), ("{", "cannot be read")):
        rule, why = d.rule(data)
        assert rule is None and words in why, (data, why)


def test_folders_compare_as_real_paths_whole_and_without_case_where_the_disk_has_none(tmp_path, home, monkeypatch):
    (home / "Work" / "Private").mkdir(parents=True)
    (home / "work" / "private").mkdir(parents=True, exist_ok=True)    # the folder as written, on a disk with case too
    os.symlink(home / "Work", home / "shortcut")
    d = Dir(tmp_path / "mac")
    monkeypatch.setattr(ade_link, "_FOLD", True)
    rule, _ = d.rule({"projects": "all", "except": ["~/work/private"]})
    assert rule.excepts(str(home / "Work" / "Private" / "notes.md"))
    assert rule.excepts(str(home / "shortcut" / "Private"))                             # through a link, as git sees it
    assert not rule.excepts(str(home / "Work" / "Private-not"))                          # whole folders, not a prefix
    monkeypatch.setattr(ade_link, "_FOLD", False)
    rule, _ = d.rule({"projects": "all", "except": ["~/work/private"]})
    assert not rule.excepts(str(home / "Work" / "Private" / "notes.md"))                 # a disk that has case


def _clone(path, url):
    os.makedirs(os.path.join(path, ".git"))
    with open(os.path.join(path, ".git", "config"), "w") as fh:
        fh.write(f'[core]\n\tbare = false\n[remote "origin"]\n\turl = {url}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n')


def _scan(home, entries, rule):
    path = home / ".claude" / "projects" / "-w" / "s1.jsonl"
    _write(path, entries)
    return ade_link._scan(str(path), None, rule)


@pytest.mark.parametrize("touch", [
    "cwd", "read", "home_var", "relative", "clone", "address", "name", "thinking", "queued", "summary"])
def test_a_session_that_touched_an_excepted_project_anywhere_is_withheld(tmp_path, home, touch):
    """A session is withheld for any trace of an excepted project in what it did or said, however long ago, and in any
    form: where it worked, a path a tool was given (absolute, from $HOME, relative to where it worked, in a clone of
    the project's repository elsewhere), the repository's address, or the project's name in its words."""
    work, private = home / "work" / "app", home / "grove"
    work.mkdir(parents=True)
    _clone(str(private), "git@github.com:owner/plans-2026.git")        # its repository's name is not its folder's
    _clone(str(home / "elsewhere" / "copy"), "https://github.com/Owner/plans-2026")
    os.symlink(private, home / "shortcut")          # a path that names neither the folder nor its name: found as real
    rule, _ = Dir(tmp_path / "mac").rule({"projects": "all", "except": ["~/grove"]})
    w = str(work)
    trace = {
        "cwd": _said("ok", "2026-10-03T10:01:00Z", cwd=str(home / "shortcut" / "notes")),
        "read": _tool("Read", {"file_path": str(home / "shortcut" / "plan.md")}, "2026-10-03T10:01:00Z", w),
        "home_var": _tool("Bash", {"command": "cat \"$HOME/shortcut/plan.md\" | head"}, "2026-10-03T10:01:00Z", w),
        "relative": _tool("Bash", {"command": "ls ../../shortcut/strategy"}, "2026-10-03T10:01:00Z", w),
        "clone": _tool("Read", {"file_path": str(home / "elsewhere" / "copy" / "x.md")}, "2026-10-03T10:01:00Z", w),
        "address": _tool("WebFetch", {"url": "https://GitHub.com/owner/plans-2026/pulls"}, "2026-10-03T10:01:00Z", w),
        "name": _said("The plan in GROVE is ready.", "2026-10-03T10:01:00Z"),
        "thinking": {"type": "assistant", "uuid": "th", "timestamp": "2026-10-03T10:01:00Z",
                     "message": {"content": [{"type": "thinking", "thinking": "read ~/grove first"}]}},
        "queued": {"type": "attachment", "uuid": "q", "attachment": {"type": "queued_command", "prompt": "see grove"}},
        "summary": {"type": "summary", "summary": "worked on grove"},
    }[touch]
    state = _scan(home, [_typed("start", "2026-10-03T10:00:00Z", w), trace,
                         _typed("later", "2026-10-03T10:05:00Z", w)], rule)
    assert state["touched"] is True and state["cwd"] == w


def test_what_claude_code_attached_or_a_tool_returned_does_not_withhold_a_session(tmp_path, home):
    """An instructions file or a tool's output naming the project is not the session working on it."""
    _grove(home)
    work = home / "work" / "app"
    work.mkdir(parents=True)
    rule, _ = Dir(tmp_path / "mac").rule({"projects": "all", "except": ["~/grove"]})
    w = str(work)
    state = _scan(home, [
        _typed("start", "2026-10-03T10:00:00Z", w),
        {"type": "attachment", "uuid": "m", "attachment": {"type": "nested_memory", "content": "see ~/grove and grove"}},
        _result("~/grove/plan.md: the plan", "2026-10-03T10:01:00Z", w),
        _said("Done.", "2026-10-03T10:02:00Z")], rule)
    assert not state.get("touched")


def test_the_scan_reads_only_what_was_added_since_and_again_when_the_rule_or_file_changed(tmp_path, home):
    _grove(home)
    (home / "elsewhere").mkdir()
    work = home / "work"
    work.mkdir()
    d = Dir(tmp_path / "mac")
    rule, _ = d.rule({"projects": "all", "except": ["~/grove"]})
    path = home / ".claude" / "projects" / "-w" / "s1.jsonl"
    _write(path, [_typed("one", "2026-10-03T10:00:00Z", str(work))])
    first = ade_link._scan(str(path), None, rule)
    assert first["offset"] == os.path.getsize(path) and not first.get("touched")
    with open(path, "a") as fh:
        fh.write('{"type": "assistant", "x": "unfinished')                               # a line still being written
    again = ade_link._scan(str(path), first, rule)
    assert again["offset"] == first["offset"]
    with open(path, "a") as fh:
        fh.write(' line"}\n' + json.dumps(_said("about grove", "2026-10-03T10:09:00Z")) + "\n")
    third = ade_link._scan(str(path), again, rule)
    assert third["offset"] == os.path.getsize(path) and third["touched"] is True
    other, _ = d.rule({"projects": "all", "except": ["~/elsewhere"]})                    # a new rule: read again whole
    assert not ade_link._scan(str(path), third, other).get("touched")
    os.unlink(path)
    _write(path, [_typed("one", "2026-10-03T10:00:00Z", str(home / "grove"))]                # another file in its place,
           + [_said("x" * 400, "2026-10-03T10:01:00Z")] * 3)                             # longer than the first read
    replaced = ade_link._scan(str(path), third, rule)
    assert replaced["offset"] == os.path.getsize(path) and replaced["cwd"] == str(home / "grove")


def test_where_a_session_ran_and_whether_it_is_running_are_claude_code_s_own_words(tmp_path, home):
    rule, _ = Dir(tmp_path / "mac").rule({"projects": "all"})
    w = str(home)
    assert _scan(home, [_typed("x", "2026-10-03T10:00:00Z", w, where="cli")], rule)["client"] == "terminal"
    assert _scan(home, [_typed("x", "2026-10-03T10:00:00Z", w, where="claude-vscode")], rule)["client"] == "vscode"
    assert _scan(home, [_typed("x", "2026-10-03T10:00:00Z", w, where="sdk-py")], rule)["client"] == "sdk"
    assert _scan(home, [_typed("x", "2026-10-03T10:00:00Z", w, where="something-new")], rule)["client"] == "other"
    sessions = home / ".claude" / "sessions"
    sessions.mkdir()
    (sessions / f"{os.getpid()}.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": "s-live",
                                                              "status": "busy"}))
    (sessions / "1.json").write_text(json.dumps({"pid": 2 ** 22 + 12345, "sessionId": "s-gone", "status": "idle"}))
    (sessions / "bad.json").write_text("{")
    assert ade_link._live_sessions() == {"s-live": "working"}


def test_a_title_is_the_session_s_own_name_then_claude_code_s_and_never_what_was_said(tmp_path, home):
    rule, _ = Dir(tmp_path / "mac").rule({"projects": "all"})
    w = str(home)
    now = int(time.time())
    plain = _scan(home, [_typed("deploy the docs\nthen tell me", "2026-10-03T10:00:00Z", w)], rule)
    assert ade_link._row("s1", plain, {}, now)["title"] == "(untitled)"                  # not the first prompt's words
    named = _scan(home, [_typed("x", "2026-10-03T10:00:00Z", w),
                         {"type": "ai-title", "aiTitle": "Sign-in fix and the\u202e rest"},
                         {"type": "custom-title", "customTitle": "my own name"},
                         {"type": "ai-title", "aiTitle": "a later title"}], rule)
    row = ade_link._row("s1", named, {"s1": "open"}, now)
    assert (row["title"], row["live"], row["withheld"]) == ("my own name", "open", False)
    named.pop("custom")
    assert ade_link._row("s1", named, {}, now)["title"] == "a later title"
    named["ai"] = "use ghp_abcdef here"
    assert ade_link._row("s1", named, {}, now)["title"] == "(untitled)"                  # a secret's shape: none
    named["touched"] = True
    row = ade_link._row("s1", named, {}, now)
    assert (row["title"], row["branch"], row["withheld"]) == (None, None, True)


def test_a_title_s_characters_are_held_as_the_server_holds_them():
    vs16 = "️"
    assert ade_link._clean("\ufeff " + vs16 + "x", 50) == "x"                           # a selector left first by a strip
    assert ade_link._clean("a❤" + vs16 + vs16 + "b", 50) == "a❤" + vs16 + "b"    # a doubled one: once
    assert ade_link._stamp("1969-12-31T23:00:00Z") is None and ade_link._stamp("2026-10-03T10:00:00Z") > 0


def _talk_entries(cwd):
    return [
        _typed("how is the build?", "2026-10-03T10:00:00Z", cwd),
        {"type": "assistant", "uuid": "th", "timestamp": "2026-10-03T10:00:30Z",
         "message": {"content": [{"type": "thinking", "thinking": "let me look"}]}},
        _said("Checking.", "2026-10-03T10:01:00Z"),
        _tool("Bash", {"command": "make test"}, "2026-10-03T10:01:30Z", cwd),
        _result("12 passed", "2026-10-03T10:02:00Z", cwd),
        _said("Green: 12 passed.", "2026-10-03T10:03:00Z"),
        {"type": "assistant", "uuid": "side", "isSidechain": True,
         "message": {"content": [{"type": "text", "text": "a sub-agent's words"}]}},
        _typed("and the key sk-ant-api03-zzzz?", "2026-10-03T10:04:00Z", cwd),
        _said("Done.", "2026-10-03T10:05:00Z"),
    ]


def _whole_talk(path):
    st = os.stat(path)
    return ade_link._talk(str(path), st.st_size, st.st_ino)


def test_the_conversation_is_the_owner_s_words_and_the_session_s_never_a_tool_s_or_a_secret(home):
    path = home / ".claude" / "projects" / "-w" / "s1.jsonl"
    _write(path, _talk_entries(str(home)))
    talk = _whole_talk(path)
    assert [(m["who"], m["text"]) for m in talk["messages"]] == [
        ("owner", "how is the build?"), ("session", "Checking.\n\nGreen: 12 passed."),
        ("owner", "[left out: it held something shaped like a secret, which the ADE does not keep]"),
        ("session", "Done.")]
    assert talk["earlier"] == 0 and talk["messages"][0]["at"] == ade_link._stamp("2026-10-03T10:00:00Z")


def _began_by(kind, text, at, cwd, meta=False):
    """A turn something other than the owner started (a task's notification, a sub-agent's report, a schedule), as
    Claude Code writes it: the turn's origin is named, and the owner's is not."""
    return dict({"type": "user", "uuid": f"n{at}", "turnOrigin": kind, "cwd": cwd, "timestamp": at,
                 "message": {"role": "user", "content": text}}, **({"isMeta": True} if meta else {}))


def test_a_message_of_the_session_s_holds_the_words_of_one_turn_at_most(home):
    """Whoever started the turn after: a task's notification, a schedule, or a prompt an earlier Claude Code wrote
    without naming its sender. The page shows History's messages before the first mirrored note's time and then the
    notes, so a message that held the words of two turns would show the later one's again in the note."""
    cwd, path = str(home), home / ".claude" / "projects" / "-w" / "s1.jsonl"
    unnamed = {"type": "user", "uuid": "p1", "timestamp": "2026-10-03T10:09:00Z", "cwd": cwd,
               "message": {"role": "user", "content": "run the nightly check"}}
    _write(path, [
        _typed("start the build", "2026-10-03T10:00:00Z", cwd),
        _said("Started.", "2026-10-03T10:00:10Z"),
        _tool("Bash", {"command": "make"}, "2026-10-03T10:00:20Z", cwd),
        _result("ok", "2026-10-03T10:00:30Z", cwd),
        _said("Still going.", "2026-10-03T10:00:40Z"),                   # the same turn: one message
        _began_by("task_notification", "<task-notification>the build ended</task-notification>",
                  "2026-10-03T10:05:00Z", cwd),
        _said("The build ended.", "2026-10-03T10:05:10Z"),
        dict(_typed("check the helper", "2026-10-03T10:05:15Z", cwd), isSidechain=True),    # a sub-agent's own prompt
        _said("Green: 12 passed.", "2026-10-03T10:05:20Z"),              # still the notification's turn
        unnamed,
        _said("Noted.", "2026-10-03T10:09:10Z"),
        _began_by("scheduled", "Scheduled: read the plan", "2026-10-03T10:20:00Z", cwd, meta=True),
        _said("Plan read.", "2026-10-03T10:20:10Z")])
    assert _whole_talk(path)["messages"] == [
        {"who": "owner", "text": "start the build", "at": 1791021600, "ms": 1791021600000},
        {"who": "session", "text": "Started.\n\nStill going.", "at": 1791021640, "ms": 1791021610000},
        {"who": "session", "text": "The build ended.\n\nGreen: 12 passed.", "at": 1791021920, "ms": 1791021910000},
        {"who": "session", "text": "Noted.", "at": 1791022150, "ms": 1791022150000},
        {"who": "session", "text": "Plan read.", "at": 1791022810, "ms": 1791022810000}]


def test_the_conversation_sent_is_its_newest_part_each_message_cut_and_saying_so(home, monkeypatch):
    monkeypatch.setattr(ade_link, "TALK_MESSAGE", 200)
    monkeypatch.setattr(ade_link, "TALK_BYTES", 600)
    path = home / ".claude" / "projects" / "-w" / "s1.jsonl"
    entries = []
    for n in range(8):
        entries += [_typed(f"question {n}", f"2026-10-03T10:{n:02d}:00Z", str(home)),
                    _said(f"answer {n} " + "y" * 300, f"2026-10-03T10:{n:02d}:30Z")]
    _write(path, entries)
    talk = _whole_talk(path)
    assert talk["messages"][-1]["text"].startswith("answer 7 ")
    assert talk["messages"][-1]["text"].endswith("more characters not sent]") and len(talk["messages"][-1]["text"]) < 260
    assert sum(len(m["text"]) for m in talk["messages"]) <= 600
    assert talk["earlier"] == 16 - len(talk["messages"]) > 0


def test_a_cut_that_would_end_a_secret_s_shape_leaves_the_message_out(home, monkeypatch):
    path = home / ".claude" / "projects" / "-w" / "s1.jsonl"
    text = "x" * 9 + " PP-ABCDEFGHZZZ"                                       # a ticket's shape only once cut after H
    _write(path, [_typed("go", "2026-10-03T10:00:00Z", str(home)), _said(text + "y" * 200, "2026-10-03T10:01:00Z")])
    monkeypatch.setattr(ade_link, "TALK_MESSAGE", 9 + 12 + 60)
    said = _whole_talk(path)["messages"][-1]["text"]
    assert not any(shape.search(said) for shape in ade_link._SECRET_SHAPES)


def test_the_client_refuses_what_the_server_refuses_by_shape():
    assert [p.pattern for p in ade_link._SECRET_SHAPES] == [p.pattern for _, p in server_link.SECRET_SHAPES]
    assert [p.flags for p in ade_link._SECRET_SHAPES] == [p.flags for _, p in server_link.SECRET_SHAPES]


# ---- through the app ------------------------------------------------------------------------------------------------

def _three(home):
    work, private = str(home / "work" / "app"), str(home / "grove")
    os.makedirs(work)
    os.makedirs(private)
    projects = home / ".claude" / "projects"
    _write(projects / "-app" / "s-new.jsonl", _talk_entries(work)[:6] + [
        {"type": "ai-title", "aiTitle": "the History list"}])
    _write(projects / "-app" / "s-term.jsonl", [dict(_typed("in a terminal", "2026-10-03T09:00:00Z", work,
                                                            where="cli"))])
    _write(projects / "-app" / "s-touched.jsonl", [_typed("start", "2026-10-03T11:00:00Z", work),
                                                   _tool("Read", {"file_path": private + "/plan.md"},
                                                         "2026-10-03T11:01:00Z", work)])
    _write(projects / "-grove" / "s-grove.jsonl", [_typed("a private plan", "2026-10-03T13:00:00Z", private)])


def test_history_lists_withholds_and_sends_the_conversations_history_json_lets_go(client, owner, served, capsys,
                                                                                   home, tmp_path):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _three(home)
    code, out, _ = run(capsys, "--dir", d, "history")
    assert code == 0 and "nothing listed" in out                                  # no history.json: nothing
    assert client.get("/api/ade/link/history", headers=owner).json() == {"sessions": []}
    with open(os.path.join(d, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove"], "conversations": True}, fh)
    code, out, _ = run(capsys, "--dir", d, "history")
    assert code == 0 and "3 of this machine's sessions listed" in out
    assert "1 withheld, their title and conversation kept here (the manual says which sessions are)" in out
    assert "2 conversation(s) sent" in out
    got = {s["id"]: s for s in client.get("/api/ade/link/history", headers=owner).json()["sessions"]}
    assert set(got) == {"s-new", "s-term", "s-touched"}                           # the excepted project's: not at all
    assert (got["s-new"]["title"], got["s-new"]["client"], got["s-new"]["withheld"]) == ("the History list", "vscode",
                                                                                         False)
    assert got["s-term"]["client"] == "terminal" and got["s-term"]["title"] == "(untitled)"
    t = got["s-touched"]
    assert (t["title"], t["branch"], t["withheld"], t["project"], t["talk_at"]) == (None, None, True, "~/work/app", None)
    talk = client.get(f"/api/ade/link/history/{got['s-new']['link_id']}/s-new", headers=owner).json()
    assert [m["who"] for m in talk["messages"]] == ["owner", "session"]
    assert "Green: 12 passed." in talk["messages"][1]["text"] and "make test" not in json.dumps(talk)
    assert client.get(f"/api/ade/link/history/{t['link_id']}/s-touched", headers=owner).status_code == 404
    code, out, _ = run(capsys, "--dir", d, "history")                               # nothing changed: nothing sent
    assert code == 0 and "conversation(s) sent" not in out


def test_a_list_is_withdrawn_when_history_json_goes_or_cannot_be_read(client, owner, served, capsys, home, tmp_path):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _three(home)
    rules = os.path.join(d, "history.json")
    with open(rules, "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove"], "conversations": True}, fh)
    assert run(capsys, "--dir", d, "history")[0] == 0
    assert len(client.get("/api/ade/link/history", headers=owner).json()["sessions"]) == 3
    with open(rules, "w") as fh:
        json.dump({"projects": "all", "exclude": ["~/grove"]}, fh)                    # a key mistyped
    code, out, _ = run(capsys, "--dir", d, "history")
    assert code == 2 and "a key it does not know, exclude" in out and "withdrawn" in out
    assert client.get("/api/ade/link/history", headers=owner).json() == {"sessions": []}
    with open(rules, "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove"]}, fh)
    assert run(capsys, "--dir", d, "history")[0] == 0
    os.unlink(rules)
    code, out, _ = run(capsys, "--dir", d, "history")
    assert code == 0 and "withdrawn" in out
    assert client.get("/api/ade/link/history", headers=owner).json() == {"sessions": []}


def test_conversations_turned_off_takes_those_kept_away(client, owner, served, capsys, home, tmp_path):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _three(home)
    rules = os.path.join(d, "history.json")
    with open(rules, "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove"], "conversations": True}, fh)
    run(capsys, "--dir", d, "history")
    link_id = link_row("mac")["id"]
    assert client.get(f"/api/ade/link/history/{link_id}/s-new", headers=owner).status_code == 200
    with open(rules, "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove"]}, fh)
    run(capsys, "--dir", d, "history")
    assert client.get(f"/api/ade/link/history/{link_id}/s-new", headers=owner).status_code == 404
    assert client.get("/api/ade/link/history", headers=owner).json()["sessions"][0]["talk_at"] is None


def test_resume_prints_its_document_and_lists_in_a_process_of_its_own(client, owner, served, capsys, home, tmp_path,
                                                                      monkeypatch):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _three(home)
    with open(os.path.join(d, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove"]}, fh)
    monkeypatch.setattr(ade_link, "_history_run", lambda *a, **k: time.sleep(10))    # in this process only
    began = time.monotonic()
    code, out, _ = run(capsys, "--dir", d, "resume")
    assert code == 0 and "sessions listed" not in out and out.rstrip("\n").endswith(ade_link.RULE)
    assert time.monotonic() - began < 5                                              # it waited for no list
    deadline = time.time() + 30
    while len(client.get("/api/ade/link/history", headers=owner).json()["sessions"]) != 3:
        assert time.time() < deadline, open(os.path.join(d, "history.log")).read() \
            if os.path.exists(os.path.join(d, "history.log")) else "no list"
        time.sleep(0.2)
    assert os.path.exists(os.path.join(d, "history_started"))


def test_a_run_started_while_another_works_leaves_it_to_that_one(tmp_path, home):
    d = Dir(tmp_path / "mac")
    held = ade_link._try_lock(d, "history.lock")
    assert held is not None
    try:
        assert ade_link._try_lock(d, "history.lock") is None
    finally:
        os.close(held)
    again = ade_link._try_lock(d, "history.lock")
    assert again is not None
    os.close(again)


def test_revoking_a_link_takes_its_list_and_conversations_with_it(client, owner, served, capsys, home, tmp_path):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _three(home)
    with open(os.path.join(d, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove"], "conversations": True}, fh)
    run(capsys, "--dir", d, "history")
    link_id = link_row("mac")["id"]
    confirmation = client.post("/api/account/confirm", headers=owner,
                               json={"password": "correct horse battery",
                                     "change": f"link-revoke:{link_id}"}).json()["confirmation"]
    r = client.request("DELETE", f"/api/ade/link/links/{link_id}", headers=owner, json={"confirmation": confirmation})
    assert r.status_code == 200, r.text
    assert client.get("/api/ade/link/history", headers=owner).json() == {"sessions": []}
    from proofpane_open import store
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_link_history WHERE link_id = ?", (link_id,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM ade_link_history_talks WHERE link_id = ?",
                          (link_id,)).fetchone()[0] == 0


# ---- the server's own rules -----------------------------------------------------------------------------------------

def _row(sid, **over):
    return dict({"id": sid, "title": f"title {sid}", "project": "~/work", "branch": "main", "client": "vscode",
                 "live": None, "started_at": 1_790_000_000, "last_at": 1_790_000_100}, **over)


def _talk(sid, n=2, text="words"):
    return {"session": sid, "messages": [{"who": "owner" if i % 2 == 0 else "session", "text": f"{text} {i}",
                                          "at": 1_790_000_000 + i} for i in range(n)], "earlier": 0}


def test_the_server_leaves_out_a_row_it_refuses_not_the_whole_list(client, owner, served, capsys, tmp_path):
    pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    mac = link_row("mac")
    got = server_link.put_history(mac, {"sessions": [
        _row("ok"), _row("hidden", title="a\u202eb"), _row("past", started_at=-5), _row("ok"),
        _row("held", withheld=True, title=None, branch=None), _row("held-title", withheld=True, title=None, branch="x"),
        _row("where", client="phone"), _row("key", title="sk-ant-api03-x"),
        _row("doubled", title="a\u2764\ufe0f\ufe0fb")]})                    # a selector after a selector hides
    assert (got["listed"], got["left_out"]) == (2, 7)
    listed = {s["id"]: s for s in server_link.history(mac["user_id"])["sessions"]}
    assert set(listed) == {"ok", "held"} and listed["held"]["title"] is None and listed["held"]["withheld"] is True


def test_a_conversation_is_kept_only_for_a_listed_session_not_withheld_while_conversations_go(client, owner, served,
                                                                                               capsys, tmp_path):
    pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    mac = link_row("mac")
    server_link.put_history(mac, {"sessions": [_row("a"), _row("w", withheld=True, title=None, branch=None)]})
    for sid in ("a", "w", "unlisted"):                                      # conversations not let go
        with pytest.raises(server_link.LinkError) as e:
            server_link.put_talk(mac, _talk(sid))
        assert e.value.status == 409
    server_link.put_history(mac, {"sessions": [_row("a"), _row("w", withheld=True, title=None, branch=None)],
                                  "conversations": True})
    assert server_link.put_talk(mac, _talk("a"))["kept"] == "a"
    for sid in ("w", "unlisted"):
        with pytest.raises(server_link.LinkError) as e:
            server_link.put_talk(mac, _talk(sid))
        assert e.value.status == 409
    for bad in ({"session": "a", "messages": [{"who": "tool", "text": "x"}]},
                {"session": "a", "messages": [{"who": "owner", "text": "use ghp_x"}]},
                {"session": "a", "messages": [{"who": "owner", "text": "x" * (server_link.TALK_TEXT + 1)}]},
                {"session": "a", "messages": [{"who": "owner", "text": "x\x00"}]},
                {"session": "a", "messages": [{"who": "owner", "text": "x", "at": True}]},
                {"session": "a", "messages": "x"}, {"session": "a b", "messages": []}):
        with pytest.raises(server_link.LinkError) as e:
            server_link.put_talk(mac, bad)
        assert e.value.status == 422, bad
    big = {"session": "a", "messages": [{"who": "owner", "text": "x" * 4000}] * 21}
    with pytest.raises(server_link.LinkError):
        server_link.put_talk(mac, big)
    assert server_link.talk(mac["user_id"], mac["id"], "a")["messages"][0]["text"] == "words 0"
    with pytest.raises(server_link.LinkError) as e:
        server_link.talk("someone-else", mac["id"], "a")
    assert e.value.status == 404
    server_link.put_history(mac, {"sessions": [_row("w", withheld=True, title=None, branch=None)],
                                  "conversations": True})
    with pytest.raises(server_link.LinkError):                          # no longer listed: its conversation went
        server_link.talk(mac["user_id"], mac["id"], "a")


def test_a_link_keeps_the_conversations_it_sent_last_and_sends_a_bounded_number_an_hour(client, owner, served,
                                                                                          capsys, tmp_path,
                                                                                          monkeypatch):
    pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    mac = link_row("mac")
    monkeypatch.setattr(server_link, "HISTORY_TALKS", 2)
    t = 1_790_001_000
    server_link.put_history(mac, {"sessions": [_row("a"), _row("b"), _row("c")], "conversations": True}, now=t)
    for n, sid in enumerate(("a", "b", "c")):
        server_link.put_talk(mac, _talk(sid), now=t + 1 + n)
    kept = {s["id"]: s["talk_at"] for s in server_link.history(mac["user_id"])["sessions"]}
    assert kept == {"a": None, "b": t + 2, "c": t + 3}
    monkeypatch.setattr(server_link, "HISTORY_PUTS_PER_HOUR", 5)
    server_link.put_talk(mac, _talk("a"), now=t + 4)                     # the fifth within the hour
    with pytest.raises(server_link.LinkError) as e:
        server_link.put_talk(mac, _talk("a"), now=t + 10)
    assert e.value.status == 429
    assert server_link.put_talk(mac, _talk("a"), now=t + 3602)["kept"] == "a"         # an hour on: taken again


def test_history_soon_lists_at_each_start_printing_nothing_for_a_block_set_up_before_session_start(tmp_path, home,
                                                                                                       capsys,
                                                                                                       monkeypatch):
    """A block for every session set up before `session start` (test_ade_link_machine) runs `history --soon` at each
    start: it starts the History list, prints nothing into the session and never fails it, a link's directory missing
    or not paired included."""
    here = os.path.join(os.path.dirname(__file__), "..")
    with open(os.path.join(here, "ade_link.everywhere.settings.json"), encoding="utf-8") as fh:
        hooks = json.load(fh)["hooks"]
    (stop,) = hooks["Stop"]
    assert stop["hooks"][0]["command"].endswith(" mirror reply --listed-only || true")
    started = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: started.append(a))
    for d in (tmp_path / "missing", tmp_path / "unpaired"):
        assert run(capsys, "--dir", d, "history", "--soon") == (0, "", "")
    (tmp_path / "unpaired").mkdir()
    assert run(capsys, "--dir", tmp_path / "unpaired", "history", "--soon") == (0, "", "")
    assert started == []
    linked = tmp_path / "linked"                                       # a link's directory, history.json in it
    linked.mkdir()
    (linked / "config.json").write_text(json.dumps({"url": "https://ade.example", "name": "mac"}))
    (linked / "history.json").write_text(json.dumps({"projects": "all"}))
    assert run(capsys, "--dir", linked, "history", "--soon") == (0, "", "")
    assert run(capsys, "--dir", linked, "history", "--soon") == (0, "", "")    # within HISTORY_EVERY: not again
    assert len(started) == 1 and started[0][0][-4:] == ["--dir", str(linked), "history", "--quiet"]


# ---- what a scan withholds, and how a run sends the rest ------------------------------------------------------------

def _grove(home):
    (home / "grove").mkdir(exist_ok=True)
    return str(home / "grove")


def test_a_conversation_is_read_only_as_far_as_the_scan_that_judged_its_session(home, tmp_path):
    """A turn written after the scan (one that reads the excepted project) never goes with the conversation that scan
    let go; a file not the one scanned gives none."""
    _grove(home)
    rule, _ = Dir(tmp_path / "mac").rule({"projects": "all", "except": ["~/grove"], "conversations": True})
    path = home / ".claude" / "projects" / "-w" / "s1.jsonl"
    _write(path, _talk_entries(str(home))[:6])
    state = ade_link._scan(str(path), None, rule)
    assert not state.get("touched")
    _write(path, [_typed("now read ~/grove/plan.md", "2026-10-03T12:00:00Z", str(home)),
                  _said("The plan says: hire two.", "2026-10-03T12:01:00Z")], mode="a")
    talk = ade_link._talk(str(path), state["offset"], state["ino"])
    assert "hire two" not in json.dumps(talk) and "grove" not in json.dumps(talk)
    assert ade_link._talk(str(path), state["offset"], state["ino"] + 1) is None


def test_a_session_whose_sub_agent_touched_an_excepted_project_is_withheld(home, tmp_path):
    """A sub-agent's or a workflow's transcript (beside the session's own) counts as the session's: what it read
    reaches the session's words only through its report, a tool's output the scan does not read."""
    _grove(home)
    rule, _ = Dir(tmp_path / "mac").rule({"projects": "all", "except": ["~/grove"]})
    path = home / ".claude" / "projects" / "-w" / "s1.jsonl"
    _write(path, [_typed("find the invoice export", "2026-10-03T10:00:00Z", str(home)),
                  _said("Found it.", "2026-10-03T10:05:00Z")])
    clean = ade_link._session_state(str(path), {}, rule, {})
    assert not clean.get("touched")
    agent = home / ".claude" / "projects" / "-w" / "s1" / "subagents" / "workflows" / "wf_1" / "agent-a.jsonl"
    _write(agent, [_tool("Read", {"file_path": str(home / "grove" / "x.md")}, "2026-10-03T10:01:00Z", str(home))])
    kept = {}
    assert ade_link._session_state(str(path), {}, rule, kept)["touched"] is True
    assert str(agent) in kept                                           # its reading kept, for the next run
    agent.unlink()
    os.mkfifo(agent)                                                   # a sub-agent's transcript that cannot be read
    assert ade_link._session_state(str(path), {}, rule, {})["touched"] is True


@pytest.mark.parametrize("entry", ["custom", "ai", "branch", "selection", "file"])
def test_a_title_a_branch_or_what_the_editor_sent_naming_an_excepted_project_withholds_the_session(home, tmp_path,
                                                                                                  entry):
    _grove(home)
    rule, _ = Dir(tmp_path / "mac").rule({"projects": "all", "except": ["~/grove"]})
    w = str(home)
    e = {"custom": {"type": "custom-title", "customTitle": "GROVE invoices"},
         "ai": {"type": "ai-title", "aiTitle": "Reading the grove plan"},
         "branch": _said("ok", "2026-10-03T10:01:00Z") | {"gitBranch": "grove-plan"},
         "selection": {"type": "attachment", "uuid": "s", "attachment": {"type": "selected_lines_in_ide",
                                                                          "filename": str(home / "grove" / "a.md"),
                                                                          "content": "lines"}},
         "file": {"type": "attachment", "uuid": "f", "attachment": {"type": "opened_file_in_ide",
                                                                     "filename": "~/grove/b.md"}}}[entry]
    assert _scan(home, [_typed("start", "2026-10-03T10:00:00Z", w), e], rule)["touched"] is True


def test_claude_code_s_own_attachments_do_not_withhold_a_session(home, tmp_path):
    _grove(home)
    rule, _ = Dir(tmp_path / "mac").rule({"projects": "all", "except": ["~/grove"]})
    own = [{"type": "attachment", "uuid": f"o{n}", "attachment": {"type": kind, "content": "see ~/grove and grove"}}
           for n, kind in enumerate(sorted(ade_link._OWN_ATTACHMENTS))]
    assert not _scan(home, [_typed("start", "2026-10-03T10:00:00Z", str(home))] + own, rule).get("touched")


def _worktree(repo, where, name="w1"):
    """A worktree of `repo` at `where`, as git lays one out: a .git file there pointing into the repository's own."""
    gitdir = os.path.join(repo, ".git", "worktrees", name)
    os.makedirs(gitdir)
    with open(os.path.join(gitdir, "commondir"), "w") as fh:
        fh.write("../..\n")
    os.makedirs(where)
    with open(os.path.join(where, ".git"), "w") as fh:
        fh.write(f"gitdir: {gitdir}\n")


def test_a_local_clone_or_a_worktree_of_an_excepted_repository_is_excepted(home, tmp_path):
    private = home / "grove"
    os.makedirs(private / ".git")
    (private / ".git" / "config").write_text("[core]\n\tbare = false\n")             # no remote at all
    _clone(str(home / "copy"), str(private))                                         # cloned from the folder itself
    _worktree(str(private), str(home / "elsewhere" / "wt"))
    rule, _ = Dir(tmp_path / "mac").rule({"projects": "all", "except": ["~/grove"]})
    assert rule.excepts(str(home / "copy" / "plan.md"))
    assert rule.excepts(str(home / "elsewhere" / "wt" / "plan.md"))
    assert not rule.excepts(str(home / "elsewhere" / "other.md"))


def test_a_folder_inside_a_larger_repository_excepts_that_folder_not_the_repository(home, tmp_path):
    big = home / "big"
    _clone(str(big), "git@github.com:owner/big.git")
    (big / "private").mkdir()
    _clone(str(home / "copy"), "https://github.com/owner/big")
    rule, _ = Dir(tmp_path / "mac").rule({"projects": "all", "except": ["~/big/private"]})
    assert rule.excepts(str(big / "private" / "x.md"))
    assert not rule.excepts(str(big / "other" / "x.md")) and not rule.excepts(str(home / "copy" / "x.md"))


def test_a_repository_s_name_is_a_word_its_address_and_a_tool_s_input_name(home, tmp_path):
    _clone(str(home / "grove"), "git@github.com:owner/plans-2026.git")
    rule, _ = Dir(tmp_path / "mac").rule({"projects": "all", "except": ["~/grove"]})
    for text in ("gh api repos/owner/plans-2026/pulls", '{"owner": "owner", "repo": "plans-2026"}',
                 "see plans-2026-notes", "x" + chr(0x770B) + "plans-2026" + chr(0x7684)):
        assert rule.touches(text), text
    assert not rule.touches("plans-20267 and xplans-2026")


def test_history_json_refuses_a_key_twice_and_an_excepted_folder_that_is_not_there(home, tmp_path):
    d = Dir(tmp_path / "mac")
    rule, why = d.rule('{"projects": "all", "except": ["~/grove"], "except": []}')
    assert rule is None and "twice" in why
    rule, why = d.rule({"projects": "all", "except": ["~/no-such-folder"]})
    assert rule is None and "not there" in why


def test_a_folder_name_compares_composed_or_decomposed():
    composed, decomposed = "/tmp/caf" + chr(0xE9), "/tmp/cafe" + chr(0x301)
    assert ade_link._key(composed) == ade_link._key(decomposed)


def test_a_path_that_cannot_be_looked_at_counts_as_excepted(home, tmp_path):
    _grove(home)
    rule, _ = Dir(tmp_path / "mac").rule({"projects": "all", "except": ["~/grove"]})
    assert rule.touches("cat /tmp/a\x00b") is True


def test_the_cache_unreadable_or_large_costs_a_whole_read_never_a_failure(tmp_path):
    d = Dir(tmp_path / "mac")
    with open(os.path.join(d.dir, "history_cache.json"), "w") as fh:
        fh.write("{not json")
    assert ade_link._history_cache(d) == {}
    big = {"sessions": {f"/p/{n:06d}.jsonl": {"offset": n, "pad": "x" * 600} for n in range(8000)}}
    with open(os.path.join(d.dir, "history_cache.json"), "w") as fh:
        json.dump(big, fh)
    assert os.path.getsize(os.path.join(d.dir, "history_cache.json")) > 4 << 20
    assert len(ade_link._history_cache(d)) == 8000


def test_one_session_id_in_two_project_folders_is_withheld_if_either_touched(client, owner, served, capsys, home,
                                                                            tmp_path):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _grove(home)
    os.makedirs(home / "work")
    with open(os.path.join(d, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove"]}, fh)
    projects = home / ".claude" / "projects"
    _write(projects / "-a" / "same.jsonl", [_typed("newer and clean", "2026-10-03T12:00:00Z", str(home / "work"))])
    _write(projects / "-b" / "same.jsonl", [_typed("older", "2026-10-03T08:00:00Z", str(home / "work")),
                                            _tool("Read", {"file_path": str(home / "grove" / "x")},
                                                  "2026-10-03T08:01:00Z", str(home / "work"))])
    assert run(capsys, "--dir", d, "history")[0] == 0
    (row,) = client.get("/api/ade/link/history", headers=owner).json()["sessions"]
    assert row["id"] == "same" and row["withheld"] is True and row["title"] is None


def test_a_conversation_the_ade_refuses_is_skipped_and_the_others_still_go(client, owner, served, capsys, home,
                                                                          tmp_path, monkeypatch):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _three(home)
    with open(os.path.join(d, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove"], "conversations": True}, fh)
    real = ade_link.Link.request

    def refusing(self, method, path, body=None, **k):
        if path == "/history/talk" and body.get("session") == "s-new":
            raise ade_link.Refused(409, "not this one")
        return real(self, method, path, body, **k)
    monkeypatch.setattr(ade_link.Link, "request", refusing)
    code, out, _ = run(capsys, "--dir", d, "history")
    assert code == 0 and "1 conversation(s) sent" in out
    assert "talk s-new: HTTP 409" in open(os.path.join(d, "history.log")).read()
    link_id = link_row("mac")["id"]
    assert client.get(f"/api/ade/link/history/{link_id}/s-term", headers=owner).status_code == 200
    cache = json.load(open(os.path.join(d, "history_cache.json")))["sessions"]
    assert all(s.get("offset") for s in cache.values())                 # the reading kept for the next run


def test_a_conversation_goes_with_at_most_the_server_s_count_of_messages(home, monkeypatch):
    monkeypatch.setattr(ade_link, "TALK_MESSAGES", 5)
    path = home / ".claude" / "projects" / "-w" / "s1.jsonl"
    _write(path, [_typed(f"q{n}", f"2026-10-03T10:{n:02d}:00Z", str(home)) for n in range(12)])
    st = os.stat(path)
    talk = ade_link._talk(str(path), st.st_size, st.st_ino)
    assert [m["text"] for m in talk["messages"]] == [f"q{n}" for n in range(7, 12)] and talk["earlier"] == 7


def test_a_manual_run_waits_for_one_under_way(client, owner, served, capsys, home, tmp_path):
    import threading
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    with open(os.path.join(d, "history.json"), "w") as fh:
        json.dump({"projects": "all"}, fh)
    held = ade_link._try_lock(Dir(d), "history.lock")
    done = []
    t = threading.Thread(target=lambda: done.append(ade_link.main(["--dir", d, "history"])))
    t.start()
    time.sleep(0.5)
    assert t.is_alive() and not done                                    # waiting for the run under way
    os.close(held)
    t.join(20)
    assert done == [0]


def test_a_process_record_whose_start_is_not_the_process_s_is_not_running(home, monkeypatch):
    import subprocess
    sessions = home / ".claude" / "sessions"
    sessions.mkdir()
    # as Claude Code records it on macOS: ps's lstart in UTC and the C locale, whatever the machine's own
    started = subprocess.run(["ps", "-o", "lstart=", "-p", str(os.getpid())], capture_output=True, text=True,
                             env={**os.environ, "TZ": "UTC0", "LC_ALL": "C"}).stdout
    monkeypatch.setenv("TZ", "NZST-12NZDT,M9.5.0,M4.1.0/3")
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    (sessions / "a.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": "s-me", "status": "idle",
                                                 "procStart": started}))
    (sessions / "b.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": "s-reused", "status": "busy",
                                                 "procStart": "Thu Jan  1 00:00:00 2026"}))
    (sessions / "c.json").write_text(json.dumps({"pid": 0, "sessionId": "s-zero", "status": "busy"}))
    assert ade_link._live_sessions() == {"s-me": "open"}


def test_a_linux_record_is_checked_against_the_start_proc_gives(home, tmp_path, monkeypatch):
    # on Linux Claude Code records a process's start as /proc gives it: the clock ticks after boot, stat's 22nd field
    sessions = home / ".claude" / "sessions"
    sessions.mkdir()
    proc = tmp_path / "proc"
    (proc / str(os.getpid())).mkdir(parents=True)
    fields = ["S"] + ["0"] * 18 + ["8109579"] + ["0"] * 10           # the state, fields 4 to 21, then the start
    (proc / str(os.getpid()) / "stat").write_text(f"{os.getpid()} (claude (x) 1 2) " + " ".join(fields))
    monkeypatch.setattr(ade_link, "PROC", str(proc))
    records = (("a", os.getpid(), "s-me", "8109579"), ("b", os.getpid(), "s-reused", "8109580"),
               ("c", os.getppid(), "s-unread", "8109579"))           # no stat to read: the process is there, so it runs
    for name, pid, sid, started in records:
        (sessions / f"{name}.json").write_text(json.dumps({"pid": pid, "sessionId": sid, "status": "busy",
                                                           "procStart": started}))
    assert ade_link._live_sessions() == {"s-me": "working", "s-unread": "working"}


def test_a_refused_conversation_counts_toward_the_hour_as_any_does(client, owner, served, capsys, tmp_path,
                                                                    monkeypatch):
    pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    mac = link_row("mac")
    monkeypatch.setattr(server_link, "HISTORY_PUTS_PER_HOUR", 2)
    server_link.put_history(mac, {"sessions": [_row("a")]})
    with pytest.raises(server_link.LinkError) as e:
        server_link.put_talk(mac, _talk("a"))                           # conversations not let go: 409, and counted
    assert e.value.status == 409
    with pytest.raises(server_link.LinkError) as e:
        server_link.put_talk(mac, _talk("a"))
    assert e.value.status == 429


# ---- remotes, sub-agents' folders, searches and readings kept by version --------------------------------------------

def test_a_path_remote_names_its_folder_however_written_and_a_bare_one_is_found_by_its_path(home, tmp_path):
    assert ade_link._identity("/u/grove/.git") == ade_link._identity("/u/grove") == ade_link._identity("/u/grove.git/")
    bare = tmp_path / "usb" / "plans.git"
    bare.mkdir(parents=True)
    _clone(str(home / "grove"), str(bare))                               # the excepted repository's remote: a bare one
    rule, _ = Dir(tmp_path / "mac").rule({"projects": "all", "except": ["~/grove"]})
    assert rule.touches(f"git --git-dir={bare} show HEAD:plan.md")
    assert rule.touches("git clone /somewhere/else/plans.git x")       # its name, as a word


def test_a_sub_agents_folder_that_cannot_be_read_or_is_a_link_withholds_the_session(home, tmp_path):
    _grove(home)
    rule, _ = Dir(tmp_path / "mac").rule({"projects": "all", "except": ["~/grove"]})
    path = home / ".claude" / "projects" / "-w" / "s1.jsonl"
    _write(path, [_typed("go", "2026-10-03T10:00:00Z", str(home))])
    locked = home / ".claude" / "projects" / "-w" / "s1" / "subagents"
    locked.mkdir(parents=True)
    locked.chmod(0)
    try:
        if not os.access(locked, os.R_OK):          # a user that reads every folder (root) has none it cannot read
            assert ade_link._session_state(str(path), {}, rule, {})["touched"] is True
    finally:
        locked.chmod(0o755)
    os.rmdir(locked)
    os.rmdir(locked.parent)
    os.symlink(tmp_path, home / ".claude" / "projects" / "-w" / "s1")
    assert ade_link._session_state(str(path), {}, rule, {})["touched"] is True


def test_a_user_s_hook_output_counts_and_the_link_s_own_does_not(home, tmp_path):
    _grove(home)
    rule, _ = Dir(tmp_path / "mac").rule({"projects": "all", "except": ["~/grove"]})
    w = str(home)

    def hook(command):
        return {"type": "attachment", "uuid": "h", "attachment": {"type": "hook_success", "command": command,
                                                                   "stdout": "today: ~/grove/today.md says hire two"}}
    assert _scan(home, [_typed("x", "2026-10-03T10:00:00Z", w), hook("cat ~/notes/today")], rule)["touched"] is True
    assert not _scan(home, [_typed("x", "2026-10-03T10:00:00Z", w),
                            hook('/v/python "$HOME/.proofpane-ade/ade_link.py" --dir d resume')], rule).get("touched")


@pytest.mark.parametrize("tool,given,touched", [("Grep", None, True), ("Glob", "~", True), ("Grep", "~/work", False),
                                                ("Grep", "..", True)])
def test_a_search_of_a_folder_that_holds_an_excepted_one_withholds_the_session(home, tmp_path, tool, given, touched):
    _grove(home)
    (home / "work").mkdir()
    rule, _ = Dir(tmp_path / "mac").rule({"projects": "all", "except": ["~/grove"]})
    where = str(home) if given != ".." else str(home / "work")
    found = _scan(home, [_typed("x", "2026-10-03T10:00:00Z", where),
                         _tool(tool, dict({"pattern": "key"}, **({"path": given} if given else {})),
                               "2026-10-03T10:01:00Z", where)], rule)
    assert bool(found.get("touched")) is touched


def test_a_session_withheld_once_stays_withheld_when_its_excepted_folder_is_renamed(client, owner, served, capsys,
                                                                                 home, tmp_path):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _three(home)
    with open(os.path.join(d, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove"]}, fh)
    assert run(capsys, "--dir", d, "history")[0] == 0
    os.rename(home / "grove", home / "plans")
    with open(os.path.join(d, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": ["~/plans"]}, fh)
    assert run(capsys, "--dir", d, "history")[0] == 0
    got = {s["id"]: s for s in client.get("/api/ade/link/history", headers=owner).json()["sessions"]}
    assert got["s-touched"]["withheld"] is True and got["s-touched"]["title"] is None
    assert got["s-grove"]["withheld"] is True                              # left out once: now at most listed withheld


def test_a_scan_s_version_is_part_of_what_its_reading_is_kept_under(tmp_path, monkeypatch):
    d = Dir(tmp_path / "mac")
    first = d.rule({"projects": "all"})[0].fingerprint
    monkeypatch.setattr(ade_link, "SCAN_VERSION", ade_link.SCAN_VERSION + 1)
    assert d.rule({"projects": "all"})[0].fingerprint != first


def test_a_run_cut_short_keeps_what_it_read_so_far(client, owner, served, capsys, home, tmp_path, monkeypatch):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _three(home)
    with open(os.path.join(d, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove"]}, fh)
    monkeypatch.setattr(ade_link, "SAVE_EVERY", -1)
    real, seen = ade_link._session_state, []

    def cut(path, *a, **k):
        seen.append(path)
        if len(seen) == 3:
            raise KeyboardInterrupt("the run's time ran out")
        return real(path, *a, **k)
    monkeypatch.setattr(ade_link, "_session_state", cut)
    with pytest.raises(KeyboardInterrupt):
        ade_link._history_run(ade_link.Link(d))
    cache = json.load(open(os.path.join(d, "history_cache.json")))["sessions"]
    assert set(seen[:2]) <= set(cache)                                   # the two read before the cut


def test_a_conversation_from_a_file_rewritten_in_place_is_not_sent(home, tmp_path):
    rule, _ = Dir(tmp_path / "mac").rule({"projects": "all"})
    path = home / ".claude" / "projects" / "-w" / "s1.jsonl"
    _write(path, _talk_entries(str(home))[:6])
    state = ade_link._scan(str(path), None, rule)
    with open(path, "r+b") as fh:                                        # the same file number, other bytes
        fh.write(b'{"type": "user", "x": 1}\n' + b" " * 20)
    assert ade_link._talk(str(path), state["offset"], state["ino"], state["head"], state["head_len"]) is None


def test_a_small_transcript_growing_is_still_the_same_file(home, tmp_path, monkeypatch):
    """Under HEAD bytes as it grows, it is read on from where the last scan stopped, not again from its start."""
    rule, _ = Dir(tmp_path / "mac").rule({"projects": "all"})
    path = home / ".claude" / "projects" / "-w" / "s1.jsonl"
    _write(path, [_typed("x", "2026-10-03T10:00:00Z", str(home))])
    state = ade_link._scan(str(path), None, rule)
    _write(path, [_said("y", "2026-10-03T10:01:00Z")], mode="a")
    taken, real = [], ade_link._take
    monkeypatch.setattr(ade_link, "_take", lambda st, entry, r: (taken.append(entry), real(st, entry, r)))
    again = ade_link._scan(str(path), state, rule)
    assert len(taken) == 1 and again["offset"] == os.path.getsize(path)          # the new line alone


def test_where_a_session_began_is_found_after_a_long_first_prompt(home):
    path = home / ".claude" / "projects" / "-w" / "s1.jsonl"
    _write(path, [{"type": "user", "uuid": "u", "message": {"content": "x" * (600 << 10)}},
                  _typed("then", "2026-10-03T10:00:00Z", str(home / "work"))])
    assert ade_link._first_cwd(str(path)) == str(home / "work")


def test_the_projects_folder_is_claude_code_s_own_where_it_is_set_elsewhere(home, monkeypatch, tmp_path):
    assert ade_link._projects_root() == str(home / ".claude" / "projects")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    assert ade_link._projects_root() == str(tmp_path / "cfg" / "projects")


def test_the_client_defines_each_name_once():
    """A second definition of a name silently replaces the first (resume read the stop hook's blocking stdin reader,
    named as its own was): every top-level function, class and constant of ade_link.py is defined once."""
    import ast
    tree = ast.parse(open(ade_link.__file__, encoding="utf-8").read())
    names = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    names += [t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)
              and t.id.isupper()]
    twice = sorted({n for n in names if names.count(n) > 1})
    assert twice == []



# ---- unreadable values and lists, any search tool, and what stays withheld ------------------------------------------

def test_a_path_remote_s_name_is_found_whatever_its_case(home, tmp_path, monkeypatch):
    monkeypatch.setattr(ade_link, "_FOLD", False)                       # a disk that has case
    bare = tmp_path / "usb" / "Plans.git"
    bare.mkdir(parents=True)
    _clone(str(home / "grove"), str(bare))
    rule, _ = Dir(tmp_path / "mac").rule({"projects": "all", "except": ["~/grove"]})
    assert rule.touches("git clone /mnt/x/Plans.git copy")


def test_a_value_nested_too_deep_or_a_line_that_cannot_be_read_counts_as_touched(home, tmp_path):
    _grove(home)
    rule, _ = Dir(tmp_path / "mac").rule({"projects": "all", "except": ["~/grove"]})
    deep = "/tmp/x"
    for _ in range(50):
        deep = {"in": deep}
    assert _scan(home, [_typed("x", "2026-10-03T10:00:00Z", str(home)),
                        _tool("Tool", {"a": deep}, "2026-10-03T10:01:00Z", str(home))], rule)["touched"] is True
    path = home / ".claude" / "projects" / "-w" / "s2.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_typed("x", "2026-10-03T10:00:00Z", str(home))) + "\n{not json at all}\n")
    assert ade_link._scan(str(path), None, rule)["touched"] is True


@pytest.mark.parametrize("tool,tool_input,touched", [
    ("mcp__proofpane__grep", {"pattern": "key", "path": "~"}, True),
    ("mcp__fs__search_files", {"path": "~/work", "pattern": "x"}, False),
    ("mcp__fs__directory_tree", {"path": "~"}, True),
    ("Glob", {"pattern": "HOME/**/*.md"}, True),
    ("ReadFile", {"path": "~"}, False),
])
def test_any_search_tool_over_a_folder_holding_an_excepted_one_counts(home, tmp_path, tool, tool_input, touched):
    _grove(home)
    (home / "work").mkdir()
    rule, _ = Dir(tmp_path / "mac").rule({"projects": "all", "except": ["~/grove"]})
    tool_input = {k: v.replace("HOME", str(home)) for k, v in tool_input.items()}
    w = str(home / "work")                                              # a folder that holds no excepted one
    found = _scan(home, [_typed("x", "2026-10-03T10:00:00Z", w), _tool(tool, tool_input, "2026-10-03T10:01:00Z", w)],
                  rule)
    assert bool(found.get("touched")) is touched


def test_a_withheld_list_that_cannot_be_read_refuses_the_run(client, owner, served, capsys, home, tmp_path):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _three(home)
    with open(os.path.join(d, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove"]}, fh)
    assert run(capsys, "--dir", d, "history")[0] == 0
    before = client.get("/api/ade/link/history", headers=owner).json()
    with open(os.path.join(d, "history_withheld.json"), "w") as fh:
        fh.write('["s-touched", ')                                     # cut short
    code, out, _ = run(capsys, "--dir", d, "history")
    assert code == 2 and "history_withheld.json cannot be read" in out
    assert client.get("/api/ade/link/history", headers=owner).json() == before


def test_a_session_left_out_only_for_its_folder_is_listed_once_its_folder_is_named(client, owner, served, capsys,
                                                                                  home, tmp_path):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _three(home)
    with open(os.path.join(d, "history.json"), "w") as fh:
        json.dump({"projects": ["~/elsewhere"], "except": ["~/grove"]}, fh)
    os.makedirs(home / "elsewhere")
    assert run(capsys, "--dir", d, "history")[0] == 0
    with open(os.path.join(d, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove"]}, fh)
    assert run(capsys, "--dir", d, "history")[0] == 0
    got = {s["id"]: s for s in client.get("/api/ade/link/history", headers=owner).json()["sessions"]}
    assert got["s-new"]["withheld"] is False and got["s-new"]["title"] == "the History list"


def test_what_an_earlier_reading_found_touched_stays_withheld_with_the_list_deleted(client, owner, served, capsys,
                                                                                   home, tmp_path):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _three(home)
    with open(os.path.join(d, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove"]}, fh)
    assert run(capsys, "--dir", d, "history")[0] == 0
    os.unlink(os.path.join(d, "history_withheld.json"))
    os.rename(home / "grove", home / "plans")
    with open(os.path.join(d, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": ["~/plans"]}, fh)
    assert run(capsys, "--dir", d, "history")[0] == 0
    got = {s["id"]: s for s in client.get("/api/ade/link/history", headers=owner).json()["sessions"]}
    assert got["s-touched"]["withheld"] is True


def test_a_transcript_s_head_grows_with_it_from_empty(home, tmp_path):
    rule, _ = Dir(tmp_path / "mac").rule({"projects": "all"})
    path = home / ".claude" / "projects" / "-w" / "s1.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("")
    state = ade_link._scan(str(path), None, rule)
    assert state["head_len"] == 0
    _write(path, [_typed("x", "2026-10-03T10:00:00Z", str(home))], mode="a")
    grown = ade_link._scan(str(path), state, rule)
    assert grown["head_len"] == os.path.getsize(path) > 0



def test_a_copy_of_a_session_that_cannot_be_read_withholds_it(client, owner, served, capsys, home, tmp_path):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _three(home)
    with open(os.path.join(d, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove"]}, fh)
    other = home / ".claude" / "projects" / "-other"
    other.mkdir()
    os.mkfifo(other / "s-new.jsonl")                                   # a second copy of s-new, not a readable file
    assert run(capsys, "--dir", d, "history")[0] == 0
    got = {s["id"]: s for s in client.get("/api/ade/link/history", headers=owner).json()["sessions"]}
    assert got["s-new"]["withheld"] is True


# ---- the scan's version, earlier sub-agents, the turn reader and a FIFO ---------------------------------------------

# what the scan's code was when SCAN_VERSION was last bumped: a change to what a scan finds without a new version keeps
# old readings that a stricter scan would have withheld. Version 4 is the first a release reads with (no
# earlier release kept a History cache); from it on, a change records a new version, never a new mark for an old one
SCAN_CODE = {4: "8abd52afa3582d80", 5: "697bdcaa53794c43", 6: "b98c2dae931f93d8", 7: "2951833613838c02"}


def _scan_code():
    import inspect
    parts = [inspect.getsource(f) for f in (ade_link._take, ade_link._words_and_actions, ade_link._strings,
                                            ade_link._content, ade_link._scan, ade_link._search_root,
                                            ade_link._identity, ade_link._repo_identities, ade_link._key,
                                            ade_link._inside, ade_link.HistoryRule)]
    parts += [repr(sorted(ade_link._OWN_ATTACHMENTS)), repr(sorted(ade_link._HOOK_ATTACHMENTS)),
              ade_link._SEARCH_NAMES.pattern, ade_link._GLOB_CHARS.pattern, ade_link._TOKENS.pattern,
              ade_link._HOME_VAR.pattern]
    import hashlib
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def test_what_a_scan_finds_changes_only_with_its_version():
    assert ade_link.SCAN_VERSION in SCAN_CODE, f"a new SCAN_VERSION: record {_scan_code()} for it here"
    assert _scan_code() == SCAN_CODE[ade_link.SCAN_VERSION], (
        "the scan's code changed: bump SCAN_VERSION in ade_link.py and record " + _scan_code() + " for it here")


def test_a_session_in_a_folder_that_names_an_excepted_project_is_not_listed(client, owner, served, capsys, home,
                                                                           tmp_path):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _three(home)
    copy = home / "Desktop" / "grove-copy"                                # a copy without its .git, under its name
    copy.mkdir(parents=True)
    _write(home / ".claude" / "projects" / "-copy" / "s-copy.jsonl", [_typed("plans", "2026-10-03T15:00:00Z",
                                                                             str(copy))])
    with open(os.path.join(d, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove"]}, fh)
    assert run(capsys, "--dir", d, "history")[0] == 0
    assert "s-copy" not in {s["id"] for s in client.get("/api/ade/link/history", headers=owner).json()["sessions"]}


def test_an_earlier_claude_code_s_sub_agent_beside_its_session_counts_as_its_session_s(client, owner, served, capsys,
                                                                                     home, tmp_path):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _three(home)
    agent = home / ".claude" / "projects" / "-app" / "agent-a1.jsonl"
    _write(agent, [dict(_tool("Read", {"file_path": str(home / "grove" / "x.md")}, "2026-10-03T12:30:00Z",
                              str(home / "work" / "app")), sessionId="s-new", isSidechain=True)])
    with open(os.path.join(d, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove"]}, fh)
    assert run(capsys, "--dir", d, "history")[0] == 0
    got = {s["id"]: s for s in client.get("/api/ade/link/history", headers=owner).json()["sessions"]}
    assert got["s-new"]["withheld"] is True and "agent-a1" not in got


def test_an_earlier_sub_agent_withholds_the_session_it_names_and_is_read_once_a_run(client, owner, served, capsys,
                                                                                    home, tmp_path, monkeypatch):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _three(home)
    folder, work = home / ".claude" / "projects" / "-app", str(home / "work" / "app")
    read = _tool("Read", {"file_path": str(home / "grove" / "x.md")}, "2026-10-03T12:30:00Z", work)
    _write(folder / "agent-a1.jsonl", [dict(read, sessionId="s-term", isSidechain=True)])
    for n in range(2, 5):                                                # another session's, touching nothing
        _write(folder / f"agent-a{n}.jsonl", [dict(_said("fine", "2026-10-03T12:31:00Z", work), sessionId="s-new",
                                                   isSidechain=True)])
    opened, real = [], ade_link._open_transcript
    monkeypatch.setattr(ade_link, "_open_transcript", lambda p: opened.append(os.path.basename(p)) or real(p))
    with open(os.path.join(d, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove"]}, fh)
    assert run(capsys, "--dir", d, "history")[0] == 0
    got = {s["id"]: s for s in client.get("/api/ade/link/history", headers=owner).json()["sessions"]}
    assert got["s-term"]["withheld"] is True and got["s-new"]["withheld"] is False
    assert not [s for s in got if s.startswith("agent-")]
    assert [opened.count(f"agent-a{n}.jsonl") for n in range(1, 5)] == [1, 1, 1, 1]   # once a run, not once a session


def test_an_earlier_sub_agent_that_names_no_session_or_cannot_be_read_withholds_each_session_of_its_folder(
        client, owner, served, capsys, home, tmp_path):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _three(home)
    projects, work = home / ".claude" / "projects", str(home / "work" / "app")
    for name in ("lib", "docs"):
        os.makedirs(home / "work" / name)
        _write(projects / f"-{name}" / f"s-{name}.jsonl", [_typed(name, f"2026-10-03T14:00:0{len(name)}Z",
                                                                  str(home / "work" / name))])
    _write(projects / "-app" / "agent-a1.jsonl", [dict(_tool("Read", {"file_path": str(home / "grove" / "x.md")},
                                                             "2026-10-03T12:30:00Z", work), isSidechain=True)])
    os.mkfifo(projects / "-lib" / "agent-a2.jsonl")                      # one that cannot be read
    with open(os.path.join(d, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove"]}, fh)
    assert run(capsys, "--dir", d, "history")[0] == 0
    got = {s["id"]: s["withheld"] for s in client.get("/api/ade/link/history", headers=owner).json()["sessions"]}
    assert got == {"s-new": True, "s-term": True, "s-touched": True, "s-lib": True, "s-docs": False}


def test_an_earlier_sub_agent_beside_a_session_is_judged_as_its_own_when_it_names_it_or_no_session(home, tmp_path):
    folder, grove = home / ".claude" / "projects" / "-w", home / "grove"
    grove.mkdir()
    read = dict(_tool("Read", {"file_path": str(grove / "x.md")}, "2026-10-03T12:30:00Z", str(home)), isSidechain=True)
    _write(folder / "s1.jsonl", [_typed("go", "2026-10-03T12:00:00Z", str(home))])
    rule, s1 = Dir(tmp_path / "mac").rule({"projects": "all", "except": ["~/grove"]})[0], str(folder / "s1.jsonl")
    _write(folder / "agent-b.jsonl", [dict(read, sessionId="s2")])
    assert ade_link._subagents_touched(s1, {"s1"}, {}, rule) is False           # another session's
    assert ade_link._subagents_touched(s1, {"s1", "s2"}, {}, rule) is True      # one whose entries s1's hold
    _write(folder / "agent-c.jsonl", [read])
    assert ade_link._subagents_touched(s1, {"s1"}, {}, rule) is True            # one that names no session
    os.unlink(folder / "agent-c.jsonl")
    _write(folder / "agent-a.jsonl", [dict(read, sessionId="s1")])
    assert ade_link._subagents_touched(s1, {"s1"}, {}, rule) is True
    os.unlink(folder / "agent-a.jsonl")
    os.mkfifo(folder / "agent-d.jsonl")
    assert ade_link._subagents_touched(s1, {"s1"}, {}, rule) is True            # one that cannot be read


def test_the_turn_reader_counts_a_bad_line_only_where_it_judges_and_not_one_still_being_written(tmp_path):
    path = tmp_path / "t.jsonl"
    go = {"type": "user", "uuid": "u1", "origin": {"kind": "human"}, "turnOrigin": "human",
          "message": {"content": [{"type": "text", "text": "go"}]}}
    one = {"type": "assistant", "uuid": "a1", "message": {"content": [{"type": "text", "text": "One."}]}}
    two = {"type": "assistant", "uuid": "a2", "message": {"content": [{"type": "text", "text": "Two."}]}}
    with open(path, "w") as fh:
        fh.write(json.dumps(go) + "\n{not json}\n" + json.dumps(one) + "\n" + json.dumps(two) + "\n")
        fh.write('{"type": "assi')                                       # a line still being written
    raw, whole = [], []
    got = ade_link._turn_entries(str(path), "a1", raw=raw, whole=whole)
    assert whole == [True] and [e["uuid"] for e in raw] == ["a2"] and [g[0] for g in got] == ["a2"]
    raw, whole = [], []
    ade_link._turn_entries(str(path), ade_link.MIRROR_FROM_START, raw=raw, whole=whole)
    assert whole == [False]                                              # from the start, the bad line is judged


def test_a_fifo_in_the_link_s_directory_holds_up_no_reader(tmp_path):
    import threading
    d = Dir(tmp_path / "mac")
    os.mkfifo(os.path.join(d.dir, "history_cache.json"))
    os.mkfifo(os.path.join(d.dir, "history_withheld.json"))
    got = {}

    def read():
        got["cache"] = ade_link._history_cache(d)
        try:
            ade_link._withheld(d)
        except ade_link.WithheldUnreadable:
            got["withheld"] = "refused"
    t = threading.Thread(target=read, daemon=True)
    t.start()
    t.join(5)
    assert not t.is_alive() and got == {"cache": {}, "withheld": "refused"}


def test_the_turn_reader_judges_what_follows_its_place_and_a_line_it_cannot_read(tmp_path):
    path = tmp_path / "t.jsonl"
    entries = [{"type": "user", "uuid": "u1", "origin": {"kind": "human"}, "turnOrigin": "human",
                "message": {"content": [{"type": "text", "text": "go"}]}},
               {"type": "assistant", "uuid": "a1", "message": {"content": [{"type": "text", "text": "One."}]}},
               {"type": "assistant", "uuid": "sc", "isSidechain": True, "message": {"content": []}},
               {"type": "user", "uuid": "u2", "origin": {"kind": "human"}, "turnOrigin": "human",
                "message": {"content": [{"type": "text", "text": "two"}]}}]
    _write(path, entries)
    raw, whole = [], []
    ade_link._turn_entries(str(path), "a1", raw=raw, whole=whole)
    assert [e["uuid"] for e in raw] == ["sc", "u2"] and whole == [True]   # the sub-agent's entry right after
    with open(path, "a") as fh:
        fh.write("{not json}\n")
    raw, whole = [], []
    ade_link._turn_entries(str(path), "a1", raw=raw, whole=whole)
    assert whole == [False]


def test_where_a_session_began_is_looked_for_within_the_stop_s_time(home):
    path = home / ".claude" / "projects" / "-w" / "s1.jsonl"
    _write(path, [_typed("x", "2026-10-03T10:00:00Z", str(home))])
    with pytest.raises(ade_link.OverBudget):
        ade_link._first_cwd(str(path), None, time.monotonic() - 1)
    assert ade_link._first_cwd(str(path), None, time.monotonic() + 5) == str(home)


# ---- files gone during a run, links never followed, and what a participant is told ----------------------------------

def test_a_history_run_reads_every_session_before_an_earlier_claude_code_s_sub_agent_files(client, owner, served,
                                                                                           capsys, home, tmp_path,
                                                                                           monkeypatch):
    """A sub-agent read before the session that relays its report could be read clean, the report then taken in."""
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _three(home)
    folder, work = home / ".claude" / "projects" / "-app", str(home / "work" / "app")
    _write(folder / "agent-a1.jsonl", [dict(_said("fine", "2026-10-03T12:31:00Z", work), sessionId="s-term",
                                            isSidechain=True)])
    _write(folder / "zz-late.jsonl", [_typed("late", "2026-10-03T12:40:00Z", work)])   # sorts after the agent's
    order, real = [], ade_link._session_state
    monkeypatch.setattr(ade_link, "_session_state",
                        lambda p, *a, **k: order.append(os.path.basename(p)) or real(p, *a, **k))
    with open(os.path.join(d, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove"]}, fh)
    assert run(capsys, "--dir", d, "history")[0] == 0
    assert "zz-late.jsonl" in order and order[-1] == "agent-a1.jsonl"


def test_a_transcript_gone_during_a_history_run_withholds_nothing(client, owner, served, capsys, home, tmp_path,
                                                                  monkeypatch):
    """Claude Code's clean-up deletes old transcripts as a session starts, when resume starts a run: one gone between
    the listing and its reading is not there, not one that cannot be read."""
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _three(home)
    projects, work = home / ".claude" / "projects", str(home / "work" / "app")
    gone = [projects / "-app" / "agent-a1.jsonl", projects / "-app" / "s-new" / "subagents" / "agent-b1.jsonl"]
    for p in gone:
        _write(p, [dict(_said("fine", "2026-10-03T12:31:00Z", work), sessionId="s-new", isSidechain=True)])
    real = ade_link._scan

    def cleaned(path, *a, **k):
        if any(os.path.realpath(str(p)) == path for p in gone) and os.path.exists(path):
            os.unlink(path)
        return real(path, *a, **k)
    monkeypatch.setattr(ade_link, "_scan", cleaned)
    with open(os.path.join(d, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove"]}, fh)
    assert run(capsys, "--dir", d, "history")[0] == 0
    got = {s["id"]: s["withheld"] for s in client.get("/api/ade/link/history", headers=owner).json()["sessions"]}
    assert got["s-new"] is False and got["s-term"] is False


def test_the_mirror_takes_a_sub_agent_file_gone_since_it_was_listed_for_not_there(home, tmp_path, monkeypatch):
    import errno
    folder = home / ".claude" / "projects" / "-w"
    (home / "grove").mkdir()
    _write(folder / "s1.jsonl", [_typed("go", "2026-10-03T12:00:00Z", str(home))])
    _write(folder / "agent-a.jsonl", [dict(_said("fine", "2026-10-03T12:01:00Z", str(home)), sessionId="s1",
                                           isSidechain=True)])
    rule, s1 = Dir(tmp_path / "mac").rule({"projects": "all", "except": ["~/grove"]})[0], str(folder / "s1.jsonl")
    real = ade_link._scan

    def cleaned(path, *a, **k):
        if os.path.exists(path) and os.path.basename(path).startswith("agent-"):
            os.unlink(path)
        return real(path, *a, **k)
    monkeypatch.setattr(ade_link, "_scan", cleaned)
    assert ade_link._subagents_touched(s1, {"s1"}, {}, rule) is False
    (folder / "s1" / "subagents").mkdir(parents=True)

    def walk(top, onerror=None, **k):
        onerror(FileNotFoundError(errno.ENOENT, "gone", top))
        return iter(())
    monkeypatch.setattr(os, "walk", walk)
    assert ade_link._beside(s1) == ([], True)

    def walk_denied(top, onerror=None, **k):
        onerror(PermissionError(errno.EACCES, "denied", top))
        return iter(())
    monkeypatch.setattr(os, "walk", walk_denied)
    assert ade_link._beside(s1) == ([], False)


def test_the_cache_and_the_withheld_list_are_never_read_through_a_link(tmp_path):
    d = Dir(tmp_path / "mac")
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text(json.dumps({"sessions": {"/x/s1.jsonl": {"touched": False}}}))
    os.symlink(elsewhere, os.path.join(d.dir, "history_cache.json"))
    assert ade_link._history_cache(d) == {}
    listed = tmp_path / "listed.json"
    listed.write_text(json.dumps(["s1"]))
    os.symlink(listed, os.path.join(d.dir, "history_withheld.json"))
    with pytest.raises(ade_link.WithheldUnreadable):
        ade_link._withheld(d)


def test_what_a_participant_is_told_and_what_it_is_not(home, tmp_path):
    os.makedirs(home / "grove")
    _clone(str(home / "grove"), "https://github.com/acme/secret-plans.git")
    _clone(str(home / "src" / "copy"), "https://github.com/acme/secret-plans.git")
    os.symlink(home / "grove", home / "alias")
    rule = Dir(tmp_path / "mac").rule({"projects": "all", "except": ["~/grove"]})[0]
    told = [str(home / "grove" / "a.md"), "~/grove/a.md", "notes on grove today", os.path.realpath(home / "grove") + "/b.md"]
    untold = [str(home / "src" / "copy" / "a.md"), str(home / "alias" / "a.md"), "the secret-plans repository"]
    assert all(rule.touches(t) and not rule.untold(t) for t in told)
    assert all(rule.touches(t) and rule.untold(t) for t in untold)
    assert rule.in_written(str(home / "grove")) and not rule.in_written(str(home / "alias"))
    assert not rule.untold("docs/a.md", str(home / "grove")) and rule.untold("docs/a.md", str(home / "src" / "copy"))


def test_a_participant_is_not_told_where_an_excepted_link_leads(home, tmp_path):
    """The told line shows the folder as history.json writes it: a path through where a link there leads, or that
    folder's own name, is a way it was not told."""
    os.makedirs(home / "real-grove")
    os.symlink(home / "real-grove", home / "grove")
    rule = Dir(tmp_path / "mac").rule({"projects": "all", "except": ["~/grove"]})[0]
    real = os.path.realpath(home / "real-grove")
    assert rule.in_written(str(home / "grove" / "a.md")) and not rule.in_written(real + "/a.md")
    assert rule.touches(real + "/a.md") and rule.untold(real + "/a.md")
    assert rule.touches("the real-grove folder") and rule.untold("the real-grove folder")
    assert not rule.untold(str(home / "grove" / "a.md")) and not rule.untold("notes on grove")


def test_an_empty_mirror_told_json_or_withheld_list_is_one_that_cannot_be_read(tmp_path):
    d = Dir(tmp_path / "mac")
    open(os.path.join(d.dir, "mirror_told.json"), "w").close()
    assert ade_link._told_marks(d) is None
    open(os.path.join(d.dir, "history_withheld.json"), "w").close()
    with pytest.raises(ade_link.WithheldUnreadable):
        ade_link._withheld(d)


def test_the_mirror_s_look_at_earlier_sub_agents_stops_at_its_time_however_little_changed(home, tmp_path):
    folder = home / ".claude" / "projects" / "-w"
    (home / "grove").mkdir()
    _write(folder / "s1.jsonl", [_typed("go", "2026-10-03T12:00:00Z", str(home))])
    agent = folder / "agent-a.jsonl"
    _write(agent, [dict(_said("fine", "2026-10-03T12:01:00Z", str(home)), sessionId="s1", isSidechain=True)])
    rule, s1 = Dir(tmp_path / "mac").rule({"projects": "all", "except": ["~/grove"]})[0], str(folder / "s1.jsonl")
    cache = {str(agent): ade_link._scan(str(agent), None, rule)}         # read before: nothing new in it
    with pytest.raises(ade_link.OverBudget):
        ade_link._subagents_touched(s1, {"s1"}, cache, rule, deadline=time.monotonic() - 1)


# ---- forks of a withheld session, and when a reading stands ---------------------------------------------------------

@pytest.mark.parametrize("how", ["ids", "ids_only", "first_entry"])
def test_a_fork_of_a_withheld_session_is_withheld(client, owner, served, capsys, home, tmp_path, how):
    """s1 is withheld for its sub-agent's reading; its own words only repeat what that found. A fork of it (by its
    entries' session ids, or rewritten but for the same first entry) carries those words: withheld too, its conversation
    kept on the machine."""
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    work, grove = str(home / "work"), home / "grove"
    os.makedirs(work)
    grove.mkdir()
    projects = home / ".claude" / "projects" / "-work"
    source = [dict(_typed("what does the plan say?", "2026-10-03T10:00:00Z", work), sessionId="s1"),
              dict(_said("Hire two.", "2026-10-03T10:01:00Z", work), sessionId="s1")]
    _write(projects / "s1.jsonl", source)
    _write(projects / "s1" / "subagents" / "agent-x.jsonl", [_tool("Read", {"file_path": str(grove / "plan.md")},
                                                                   "2026-10-03T10:00:30Z", work)])
    copied = {"ids": source, "ids_only": [dict(e, uuid=e["uuid"] + "-copy") for e in source],
              "first_entry": [dict(e, sessionId="s2") for e in source]}[how]
    _write(projects / "s2.jsonl", copied + [dict(_typed("and then?", "2026-10-03T11:00:00Z", work), sessionId="s2"),
                                            dict(_said("Hire two more.", "2026-10-03T11:01:00Z", work), sessionId="s2")])
    _write(projects / "s3.jsonl", [dict(_typed("unrelated", "2026-10-03T12:00:00Z", work), sessionId="s3")])
    with open(os.path.join(d, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove"], "conversations": True}, fh)
    assert run(capsys, "--dir", d, "history")[0] == 0
    got = {s["id"]: s for s in client.get("/api/ade/link/history", headers=owner).json()["sessions"]}
    assert got["s1"]["withheld"] is True and got["s2"]["withheld"] is True and got["s3"]["withheld"] is False
    assert got["s2"]["talk_at"] is None
    assert "s2" in json.loads(open(os.path.join(d, "history_withheld.json")).read())


def test_the_history_list_s_reading_of_a_file_stands_for_it_only_while_it_is_unchanged(home, tmp_path):
    (home / "grove").mkdir()
    folder = home / ".claude" / "projects" / "-w"
    _write(folder / "s1.jsonl", [_typed("go", "2026-10-03T12:00:00Z", str(home))])
    agent = folder / "agent-a.jsonl"
    _write(agent, [dict(_said("fine", "2026-10-03T12:01:00Z", str(home)), sessionId="s1", isSidechain=True)])
    rule, s1 = Dir(tmp_path / "mac").rule({"projects": "all", "except": ["~/grove"]})[0], str(folder / "s1.jsonl")
    kept = ade_link._scan(str(agent), None, rule)
    assert ade_link._unchanged(str(agent), kept, rule)
    lied = dict(kept, touched=True)                                      # a reading it trusts, the file not opened
    scanned, real_scan = [], ade_link._scan
    ade_link._scan = lambda p, *a, **k: scanned.append(p) or real_scan(p, *a, **k)
    try:
        assert ade_link._subagents_touched(s1, {"s1"}, {str(agent): lied}, rule) is True and scanned == []
    finally:
        ade_link._scan = real_scan
    _write(agent, [_tool("Read", {"file_path": str(home / "grove" / "x.md")}, "2026-10-03T12:02:00Z", str(home))], "a")
    assert not ade_link._unchanged(str(agent), kept, rule)              # written since: read on from it
    assert ade_link._subagents_touched(s1, {"s1"}, {str(agent): kept}, rule) is True
    other = Dir(tmp_path / "other").rule({"projects": "all"})[0]
    assert not ade_link._unchanged(str(agent), ade_link._scan(str(agent), None, other), rule)   # another rule's


def test_a_history_run_s_reading_is_whole_only_once_it_kept_what_it_withholds(client, owner, served, capsys, home,
                                                                             tmp_path, monkeypatch):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _three(home)
    with open(os.path.join(d, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove"]}, fh)
    seen, real = [], ade_link._keep_withheld
    monkeypatch.setattr(ade_link, "SAVE_EVERY", -1)                      # saved as it goes, at every transcript

    def keeping(link, ids):
        info = {}
        ade_link._history_cache(link, info)
        seen.append(info["whole"])                                       # the cache as it stands when it is kept
        return real(link, ids)
    monkeypatch.setattr(ade_link, "_keep_withheld", keeping)
    assert run(capsys, "--dir", d, "history")[0] == 0
    info = {}
    ade_link._history_cache(ade_link.Link(d), info)
    assert len(seen) > 1 and not any(seen) and info["whole"] is True


# ---- forks read twice or not at all, and a file rewritten within a second -------------------------------------------

def _forked(home, work, grove):
    """s1, withheld for its sub-agent's reading; its own words repeat what that found."""
    projects = home / ".claude" / "projects" / "-work"
    source = [dict(_typed("what does the plan say?", "2026-10-03T10:00:00Z", work), sessionId="s1"),
              dict(_said("Hire two.", "2026-10-03T10:01:00Z", work), sessionId="s1")]
    _write(projects / "s1.jsonl", source)
    _write(projects / "s1" / "subagents" / "agent-x.jsonl", [_tool("Read", {"file_path": str(grove / "plan.md")},
                                                                   "2026-10-03T10:00:30Z", work)])
    return projects, source


def _withheld_now(client, owner, capsys, d):
    assert run(capsys, "--dir", d, "history")[0] == 0
    return {s["id"]: s["withheld"] for s in client.get("/api/ade/link/history", headers=owner).json()["sessions"]}


def test_a_fork_read_in_two_project_folders_is_withheld_by_either_copy(client, owner, served, capsys, home, tmp_path):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    work, grove = str(home / "work"), home / "grove"
    os.makedirs(work)
    grove.mkdir()
    projects, source = _forked(home, work, grove)
    _write(projects / "s2.jsonl", source + [dict(_typed("and then?", "2026-10-03T11:00:00Z", work), sessionId="s2")])
    _write(home / ".claude" / "projects" / "-other" / "s2.jsonl",       # it went on in another folder, newer
           [dict(_typed("go on", "2026-10-03T12:00:00Z", work), sessionId="s2")])
    with open(os.path.join(d, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove"]}, fh)
    assert _withheld_now(client, owner, capsys, d)["s2"] is True


def test_a_fork_of_a_session_none_of_whose_copies_can_be_read_is_withheld(client, owner, served, capsys, home, tmp_path):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    work = str(home / "work")
    os.makedirs(work)
    (home / "grove").mkdir()
    projects = home / ".claude" / "projects" / "-work"
    projects.mkdir(parents=True)
    os.mkfifo(projects / "s1.jsonl")                                     # s1: cannot be read
    _write(projects / "s2.jsonl", [dict(_typed("go", "2026-10-03T10:00:00Z", work), sessionId="s1"),
                                   dict(_typed("more", "2026-10-03T11:00:00Z", work), sessionId="s2")])
    with open(os.path.join(d, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove"]}, fh)
    assert _withheld_now(client, owner, capsys, d)["s2"] is True
    assert "s1" in json.loads(open(os.path.join(d, "history_withheld.json")).read())


def test_a_fork_that_reads_an_excepted_project_after_it_was_made_does_not_withhold_its_source(client, owner, served,
                                                                                             capsys, home, tmp_path):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    work, grove = str(home / "work"), home / "grove"
    os.makedirs(work)
    grove.mkdir()
    projects = home / ".claude" / "projects" / "-work"
    source = [dict(_typed("go", "2026-10-03T10:00:00Z", work), sessionId="s1"),
              dict(_said("Fine.", "2026-10-03T10:01:00Z", work), sessionId="s1")]
    _write(projects / "s1.jsonl", source)
    _write(projects / "s2.jsonl", source + [dict(_tool("Read", {"file_path": str(grove / "plan.md")},
                                                       "2026-10-03T11:00:00Z", work), sessionId="s2")])
    with open(os.path.join(d, "history.json"), "w") as fh:
        json.dump({"projects": "all", "except": ["~/grove"]}, fh)
    got = _withheld_now(client, owner, capsys, d)
    assert got["s2"] is True and got["s1"] is False


def test_the_reading_of_a_file_rewritten_in_place_within_the_same_second_does_not_stand(home, tmp_path):
    rule = Dir(tmp_path / "mac").rule({"projects": "all"})[0]
    path = home / ".claude" / "projects" / "-w" / "agent-a.jsonl"
    _write(path, [dict(_said("fine", "2026-10-03T12:01:00Z", str(home)), sessionId="s1", isSidechain=True)])
    at = 1_700_000_000_500_000_000
    os.utime(path, ns=(at, at))
    kept = ade_link._scan(str(path), None, rule)
    assert ade_link._unchanged(str(path), kept, rule)
    with open(path, "r+b") as fh:                                         # the same size, the same file, other words
        fh.write(b"{\"x\"")
    os.utime(path, ns=(at + 1, at + 1))                                  # within the same second
    assert not ade_link._unchanged(str(path), kept, rule)


# ---- history.json saved or gone, copies and their session ids, and two forks ----------------------------------------

def test_an_excepted_folder_s_path_counts_only_whole_as_its_name_does(tmp_path, home):
    (home / "vault").mkdir()
    (home / "vault2").mkdir()
    rule = Dir(tmp_path / "mac").rule({"projects": "all", "except": ["~/vault"]})[0]
    for text in ("cat ~/vault/plan.md", "ls ~/vault", "it is in ~/vault.", "cd " + str(home / "vault"),
                 "ls ~/vault-old"):                                     # its name, as a word, as ever
        assert rule.touches(text), text
    for text in ("ls ~/vault2/notes", "cd " + str(home / "vault2"), "ls ~/vaulted"):
        assert not rule.touches(text), text                             # another folder, not a path in this one
    assert not rule.excludes(str(home / "vault2"))


def _rules(d, text):
    with open(os.path.join(d, "history.json"), "w") as fh:
        fh.write(text)


def test_a_history_json_being_saved_is_read_once_more_and_the_list_stands(client, owner, served, capsys, home,
                                                                          tmp_path, monkeypatch):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _three(home)
    rule = json.dumps({"projects": "all", "except": ["~/grove"]})
    _rules(d, rule)
    assert run(capsys, "--dir", d, "history")[0] == 0
    paused = []
    monkeypatch.setattr(ade_link, "_history_pause", lambda: paused.append(1) or _rules(d, rule))    # saved by then
    for meanwhile in (lambda: os.unlink(os.path.join(d, "history.json")),           # renamed away, to come back
                      lambda: _rules(d, rule[:9])):                                 # cut, to be written again
        meanwhile()
        code, out, _ = run(capsys, "--dir", d, "history")
        assert code == 0 and "3 of this machine's sessions listed" in out and "withdrawn" not in out
    assert len(paused) == 2
    assert len(client.get("/api/ade/link/history", headers=owner).json()["sessions"]) == 3
    other = pair(client, owner, served, capsys, str(tmp_path / "pc"), "pc")
    monkeypatch.setattr(ade_link, "_history_pause", lambda: pytest.fail("a link that never had one waits for none"))
    assert run(capsys, "--dir", other, "history")[0] == 0


def test_with_history_json_gone_its_reading_is_kept_and_the_mirror_is_said_to_hold(client, owner, served, capsys,
                                                                                   home, tmp_path, monkeypatch):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _three(home)
    _rules(d, json.dumps({"projects": "all", "except": ["~/grove"]}))
    assert run(capsys, "--dir", d, "history")[0] == 0
    cache = open(os.path.join(d, "history_cache.json")).read()
    os.unlink(os.path.join(d, "history.json"))
    monkeypatch.setattr(ade_link, "_history_pause", lambda: None)
    code, out, _ = run(capsys, "--dir", d, "history")
    assert code == 0 and "withdrawn" in out and "holds every turn while it is missing" in out
    assert "the manual says how each hold ends" in out and "delet" not in out    # how it ends, the manual's alone
    assert open(os.path.join(d, "history_cache.json")).read() == cache
    os.unlink(os.path.join(d, "history_seen"))                           # mirroring every session again
    code, out, _ = run(capsys, "--dir", d, "history")
    assert code == 0 and "holds every turn" not in out
    assert os.path.exists(os.path.join(d, "history_withheld.json"))      # what it withheld stays withheld


def _cut_ids(work):
    """Entries of 64 sessions, a long line of resumes: a transcript that holds them keeps no more ids (KIN_MAX)."""
    return [dict(_typed(f"step {i}", f"2026-10-03T{8 + i // 60:02d}:{i % 60:02d}:00Z", work), sessionId=f"c{i}")
            for i in range(ade_link.KIN_MAX)]


def test_a_copy_whose_session_ids_were_cut_is_withheld_with_the_session_it_holds(client, owner, served, capsys, home,
                                                                                 tmp_path):
    """s66 holds s65's entries, but its ids, like s65's, stop at the 64 sessions before, and its last ones are of the
    eight after: both name those 64, neither the other, so their first entry relates them, as it did before forks of
    one source were told apart."""
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    work, grove = str(home / "work"), home / "grove"
    os.makedirs(work)
    grove.mkdir()
    projects = home / ".claude" / "projects" / "-work"
    line = _cut_ids(work) + [dict(_said("Hire two.", "2026-10-03T11:00:00Z", work), sessionId="s65")]
    _write(projects / "s65.jsonl", line)
    _write(projects / "s65" / "subagents" / "agent-x.jsonl", [_tool("Read", {"file_path": str(grove / "plan.md")},
                                                                    "2026-10-03T10:59:00Z", work)])
    after = [dict(_said(f"then {i}", f"2026-10-03T11:3{i}:00Z", work), sessionId=f"d{i}") for i in range(8)]
    _write(projects / "s66.jsonl", line + after + [dict(_typed("and?", "2026-10-03T12:00:00Z", work), sessionId="s66")])
    _rules(d, json.dumps({"projects": "all", "except": ["~/grove"]}))
    got = _withheld_now(client, owner, capsys, d)
    assert (got["s65"], got["s66"]) == (True, True)


def test_a_copy_at_the_end_of_a_long_line_of_resumes_names_its_source_whose_transcript_is_gone(client, owner, served,
                                                                                                capsys, home, tmp_path):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    work = str(home / "work")
    os.makedirs(work)
    (home / "grove").mkdir()
    projects = home / ".claude" / "projects" / "-work"
    line = _cut_ids(work) + [dict(_said("Hire two.", "2026-10-03T11:00:00Z", work), sessionId="s65")]
    _write(projects / "s66.jsonl", line + [dict(_typed("and?", "2026-10-03T12:00:00Z", work), sessionId="s66")])
    with open(os.path.join(d, "history_withheld.json"), "w") as fh:
        json.dump(["s65"], fh)                                           # withheld once, its transcript gone since
    _rules(d, json.dumps({"projects": "all", "except": ["~/grove"]}))
    assert _withheld_now(client, owner, capsys, d)["s66"] is True


def test_a_session_that_holds_ten_sessions_entries_is_withheld_with_the_first(client, owner, served, capsys, home,
                                                                              tmp_path):
    """The first of ten, withheld, named among the first ids a reading keeps, not the last."""
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    work = str(home / "work")
    os.makedirs(work)
    (home / "grove").mkdir()
    chain = [dict(_typed(f"step {i}", f"2026-10-03T09:{i:02d}:00Z", work), sessionId=f"t{i}") for i in range(10)]
    _write(home / ".claude" / "projects" / "-work" / "t9.jsonl", chain)
    with open(os.path.join(d, "history_withheld.json"), "w") as fh:
        json.dump(["t0"], fh)
    _rules(d, json.dumps({"projects": "all", "except": ["~/grove"]}))
    assert _withheld_now(client, owner, capsys, d)["t9"] is True


def test_a_reading_keeps_the_last_session_ids_its_entries_named_eight_of_them(home, tmp_path):
    rule = Dir(tmp_path / "mac").rule({"projects": "all"})[0]
    named = [dict(_typed("x", f"2026-10-03T10:{i:02d}:00Z", str(home)), sessionId=f"n{i}") for i in range(20)]
    again = [dict(_said("again", f"2026-10-03T11:0{i}:00Z"), sessionId=x) for i, x in enumerate(("n3", "n15"))]
    state = _scan(home, named + again, rule)
    assert state["tail"] == ["n13", "n14", "n16", "n17", "n18", "n19", "n3", "n15"]   # one named again moves to the end
    assert len(state["sids"]) == 20 and state["sids"][0] == "n0"


def test_the_last_ids_of_a_session_read_in_two_project_folders_count_from_either(client, owner, served, capsys, home,
                                                                                 tmp_path):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    work = str(home / "work")
    os.makedirs(work)
    (home / "grove").mkdir()
    line = _cut_ids(work) + [dict(_said("Hire two.", "2026-10-03T11:00:00Z", work), sessionId="s65")]
    _write(home / ".claude" / "projects" / "-work" / "s66.jsonl",
           line + [dict(_typed("and?", "2026-10-03T12:00:00Z", work), sessionId="s66")])
    _write(home / ".claude" / "projects" / "-other" / "s66.jsonl",       # it went on in another folder, newer
           [dict(_typed("go on", "2026-10-03T13:00:00Z", work), sessionId="s66")])
    with open(os.path.join(d, "history_withheld.json"), "w") as fh:
        json.dump(["s65"], fh)                                           # withheld once, its transcript gone since
    _rules(d, json.dumps({"projects": "all", "except": ["~/grove"]}))
    assert _withheld_now(client, owner, capsys, d)["s66"] is True


def test_with_no_history_json_a_kept_reading_that_cannot_be_read_is_said(client, owner, served, capsys, home,
                                                                         tmp_path, monkeypatch):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _three(home)
    _rules(d, json.dumps({"projects": "all", "except": ["~/grove"]}))
    assert run(capsys, "--dir", d, "history")[0] == 0
    os.unlink(os.path.join(d, "history.json"))
    os.unlink(os.path.join(d, "history_seen"))
    with open(os.path.join(d, "history_cache.json"), "w") as fh:
        fh.write("{cut")
    monkeypatch.setattr(ade_link, "_history_pause", lambda: None)
    code, out, _ = run(capsys, "--dir", d, "history")
    assert code == 0 and "history_cache.json cannot be read, so a project's own mirror holds every turn" in out
    assert "delete" not in out                                           # how each hold ends is the manual's to say
    with open(os.path.join(d, "history_seen"), "w") as fh:
        fh.write("1")                                                    # and a link that read one: both are said
    code, out, _ = run(capsys, "--dir", d, "history")
    assert "holds every turn while it is missing" in out and "history_cache.json cannot be read" in out


def test_where_history_seen_set_cannot_be_kept_a_deleted_history_seen_comes_back(
        client, owner, served, capsys, home, tmp_path, monkeypatch):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _three(home)
    real = ade_link.Link.keep
    monkeypatch.setattr(ade_link.Link, "keep", lambda self, name, value:
                        (_ for _ in ()).throw(OSError(28, "No space left on device")) if name == "history_seen_set"
                        else real(self, name, value))
    _rules(d, json.dumps({"projects": "all", "except": ["~/grove"]}))
    assert run(capsys, "--dir", d, "history")[0] == 0
    os.unlink(os.path.join(d, "history.json"))
    monkeypatch.setattr(ade_link, "_history_pause", lambda: None)
    code, out, _ = run(capsys, "--dir", d, "history")
    assert "holds every turn while it is missing" in out
    os.unlink(os.path.join(d, "history_seen"))                           # set up again from the withheld list
    code, out, _ = run(capsys, "--dir", d, "history")
    assert "holds every turn while it is missing" in out and os.path.exists(os.path.join(d, "history_seen"))


def test_with_no_history_json_history_says_which_of_the_link_s_files_cannot_be_read(client, owner, served, capsys,
                                                                                    home, tmp_path, monkeypatch):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    _three(home)
    _rules(d, json.dumps({"projects": "all", "except": ["~/grove"]}))
    assert run(capsys, "--dir", d, "history")[0] == 0
    os.unlink(os.path.join(d, "history.json"))
    for name in ("history_withheld.json", "mirror_told.json"):
        with open(os.path.join(d, name), "w") as fh:
            fh.write("{cut")
    monkeypatch.setattr(ade_link, "_history_pause", lambda: None)
    code, out, _ = run(capsys, "--dir", d, "history")
    assert "history_withheld.json, mirror_told.json cannot be read, so a project's own mirror holds every turn" in out
    assert "deleting history_seen has it post" not in out


def test_two_forks_of_one_session_do_not_withhold_each_other(client, owner, served, capsys, home, tmp_path):
    d = pair(client, owner, served, capsys, str(tmp_path / "mac"), "mac")
    work, grove = str(home / "work"), home / "grove"
    os.makedirs(work)
    grove.mkdir()
    projects = home / ".claude" / "projects" / "-work"
    source = [dict(_typed("go", "2026-10-03T10:00:00Z", work), sessionId="s1"),
              dict(_said("Fine.", "2026-10-03T10:01:00Z", work), sessionId="s1")]
    _write(projects / "s1.jsonl", source)
    _write(projects / "s2.jsonl", source + [dict(_typed("on", "2026-10-03T11:00:00Z", work), sessionId="s2")])
    _write(projects / "s3.jsonl", source + [dict(_tool("Read", {"file_path": str(grove / "plan.md")},
                                                       "2026-10-03T12:00:00Z", work), sessionId="s3")])
    _rules(d, json.dumps({"projects": "all", "except": ["~/grove"]}))
    got = _withheld_now(client, owner, capsys, d)
    assert (got["s1"], got["s2"], got["s3"]) == (False, False, True)
