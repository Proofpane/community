"""The folders history.json names "sensitive" (open_edition/ade_link.py): judged as an excepted folder is, by the whole
"touched" judgement (where a session worked, what its tools were given, what it said, its sub-agents, the sessions
whose entries it holds). While encryption is off, or the link has no key to seal to, a session that touched one is not
listed at all and its turns are held; its posts and checkpoints are refused, judged by the posting session where Claude
Code's own record tells it, else by the folder; a checkpoint, and a post to all or to another session, from such a
session are refused always. With a key held, the rest goes sealed. Against the stand-in of the server's side."""

import io
import json
import os
import sys
import time

import pytest

import ade_link
from e2e_link_stand_in import ACCOUNT, ENVELOPE, LINK_ID, LinkStandIn, a_link, open_envelope, slot
from test_ade_link_client import run
from test_ade_link_history import _said, _tool, _typed, _write


@pytest.fixture(autouse=True)
def quiet(monkeypatch, tmp_path):
    monkeypatch.delenv("ADE_OWNER_KEYS", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    # the test's own home, and Claude Code's records of its processes read from it, never the machine's (whose Claude
    # Code may be running these tests): the password database's home is the machine's
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(ade_link, "_take_sessions_dir",
                        lambda: os.path.join(os.environ["HOME"], ".claude", "sessions"))
    monkeypatch.setattr(ade_link, "sleep", lambda seconds: None)
    monkeypatch.setattr(ade_link, "_moves_soon", lambda link: None)
    monkeypatch.setattr(ade_link, "_history_soon", lambda link, every=None: None)


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".claude" / "projects").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.fixture
def ade():
    s = LinkStandIn()
    yield s
    s.close()


def rules(d, data):
    with open(os.path.join(d, "history.json"), "w") as fh:
        fh.write(data if isinstance(data, str) else json.dumps(data))


def machine(home):
    """Four sessions: one plain, one that read a file in ~/vault, one that began there, and a fork of the second made
    before it read the file (its transcript holds the second's first entry, by its session id, and nothing it read)."""
    work, vault = home / "work" / "app", home / "vault"
    work.mkdir(parents=True)
    vault.mkdir()
    projects = home / ".claude" / "projects"
    w = str(work)
    _write(projects / "-app" / "s-plain.jsonl", [_typed("plain work", "2026-10-03T10:00:00Z", w),
                                                 _said("Done.", "2026-10-03T10:01:00Z")])
    read = [_typed("look", "2026-10-03T11:00:00Z", w),
            _tool("Read", {"file_path": str(vault / "notes.md")}, "2026-10-03T11:01:00Z", w),
            _said("Read it.", "2026-10-03T11:02:00Z")]
    _write(projects / "-app" / "s-vault.jsonl", [dict(e, sessionId="s-vault") for e in read])
    _write(projects / "-vault" / "s-in-vault.jsonl", [_typed("in it", "2026-10-03T12:00:00Z", str(vault))])
    _write(projects / "-app" / "s-fork.jsonl", [dict(read[0], sessionId="s-vault"),
                                                dict(_typed("go on", "2026-10-03T13:00:00Z", w), sessionId="s-fork")])
    return work, vault


def listed(ade, n=-1):
    return {r["id"]: r for r in ade.histories[n]["sessions"]}


# ---- history.json ---------------------------------------------------------------------------------------------------

def test_history_json_names_sensitive_folders_written_whole_and_there(home, tmp_path, ade):
    (home / "vault").mkdir()
    d = a_link(ade, tmp_path / "mac")
    rules(d, {"projects": "all", "sensitive": ["~/vault"]})
    rule, why = ade_link._history_rule(ade_link.Link(d))
    assert why is None and rule.sensitive is not None and rule.sensitive.excepts(str(home / "vault" / "x.md"))
    assert not rule.excepts(str(home / "vault" / "x.md"))                    # sensitive is not excepted
    rules(d, {"projects": "all"})
    assert ade_link._history_rule(ade_link.Link(d))[0].sensitive is None
    for data, words in (({"projects": "all", "sensitive": "~/vault"}, "sensitive is a list of folders"),
                        ({"projects": "all", "sensitive": ["vault"]}, "not written whole"),
                        ({"projects": "all", "sensitive": ["~/vaults"]}, "a typo protects nothing")):
        rules(d, data)
        rule, why = ade_link._history_rule(ade_link.Link(d))
        assert rule is None and words in why, why


# ---- the History list -----------------------------------------------------------------------------------------------

def test_without_a_key_a_session_that_touched_a_sensitive_folder_is_not_listed_at_all(home, tmp_path, ade, capsys):
    d = a_link(ade, tmp_path / "mac")
    machine(home)
    rules(d, {"projects": "all", "sensitive": ["~/vault"], "conversations": True})
    code, out, err = run(capsys, "--dir", d, "history")
    assert code == 0, out + err
    assert set(listed(ade)) == {"s-plain"}
    assert "3 not listed: they touched a folder history.json names sensitive" in out
    assert [t["session"] for t in ade.talks] == ["s-plain"]


def test_with_a_key_they_are_listed_sealed_as_every_session_is(home, tmp_path, ade, capsys):
    d = a_link(ade, tmp_path / "mac")
    machine(home)
    rules(d, {"projects": "all", "sensitive": ["~/vault"], "conversations": True})
    _, key = ade.owner.state()
    assert run(capsys, "--dir", d, "history")[0] == 0
    rows = listed(ade)
    assert set(rows) == {"s-plain", "s-vault", "s-in-vault", "s-fork"}
    assert open_envelope(rows["s-vault"]["sealed"], slot(ACCOUNT, LINK_ID, "history", "s-vault", 0), key)[
        "project"] == "~/work/app"
    assert "s-vault" in {t["session"] for t in ade.talks}
    talk = next(t for t in ade.talks if t["session"] == "s-vault")
    assert all(ENVELOPE.fullmatch(m["text"]) for m in talk["messages"])


def test_a_sensitive_session_s_conversation_never_goes_in_the_clear_where_encryption_ends_midway(home, tmp_path, ade,
                                                                                               capsys, monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    machine(home)
    rules(d, {"projects": "all", "sensitive": ["~/vault"], "conversations": True})
    ade.owner.state()
    ade.say_e2e = True
    real = ade.route

    def off_as_the_list_goes(method, path, body):
        if path.endswith("/history") and method == "PUT" and len(ade.owner.statements) == 1:
            ade.owner.state("e2e-off")                          # the answer to the list names it
        return real(method, path, body)
    monkeypatch.setattr(ade, "route", off_as_the_list_goes)
    assert run(capsys, "--dir", d, "history")[0] == 0
    assert "s-vault" in listed(ade) and "sealed" in listed(ade)["s-vault"]       # the list went sealed
    assert [t["session"] for t in ade.talks] == ["s-plain"]                         # then encryption was off
    assert ade.talks[0]["messages"][0]["text"] == "plain work"


def test_a_session_whose_sub_agent_touched_a_sensitive_folder_is_not_listed_without_a_key(home, tmp_path, ade,
                                                                                         capsys):
    d = a_link(ade, tmp_path / "mac")
    work, vault = machine(home)
    _write(home / ".claude" / "projects" / "-app" / "s-plain" / "subagents" / "agent-1.jsonl", [
        dict(_tool("Grep", {"pattern": "key", "path": str(vault)}, "2026-10-03T10:00:30Z", str(work)),
             isSidechain=True)])
    rules(d, {"projects": "all", "sensitive": ["~/vault"]})
    assert run(capsys, "--dir", d, "history")[0] == 0
    assert "s-plain" not in listed(ade)


# ---- the mirror -----------------------------------------------------------------------------------------------------

def stop(monkeypatch, capsys, d, path, session):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"transcript_path": str(path), "session_id": session})))
    return run(capsys, "--dir", d, "mirror", "reply")


def test_without_a_key_a_sensitive_session_s_turns_are_held_and_go_sealed_once_one_is_held(home, tmp_path, ade,
                                                                                          capsys, monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    machine(home)
    rules(d, {"projects": "all", "sensitive": ["~/vault"]})
    assert run(capsys, "--dir", d, "history")[0] == 0                       # the History list has read them whole
    projects = home / ".claude" / "projects" / "-app"
    assert stop(monkeypatch, capsys, d, projects / "s-plain.jsonl", "s-plain") == (0, "", "")
    assert [e["text"] for e in ade.events] == ["plain work", "Done."]        # a plain session's turn, in the clear
    assert stop(monkeypatch, capsys, d, projects / "s-vault.jsonl", "s-vault") == (0, "", "")
    assert stop(monkeypatch, capsys, d, projects / "s-fork.jsonl", "s-fork") == (0, "", "")
    assert len(ade.events) == 2
    state = json.loads(open(os.path.join(d, "mirror.json")).read())
    assert state["s-vault"] == ade_link.MIRROR_FROM_START and state["s-fork"] == ade_link.MIRROR_FROM_START
    _, key = ade.owner.state()
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    assert stop(monkeypatch, capsys, d, projects / "s-vault.jsonl", "s-vault") == (0, "", "")
    went = ade.events[2:]
    assert [open_envelope(e["text"], slot(ACCOUNT, LINK_ID, "event", "note", e["mirror"], None, e["client_id"]),
                          key)["text"] for e in went] == ["look", "Read it."]


def test_without_a_key_a_session_whose_sub_agent_touched_a_sensitive_folder_has_its_turns_held(home, tmp_path, ade,
                                                                                               capsys, monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    work, vault = machine(home)
    _write(home / ".claude" / "projects" / "-app" / "s-plain" / "subagents" / "agent-1.jsonl", [
        dict(_tool("Grep", {"pattern": "key", "path": str(vault)}, "2026-10-03T10:00:30Z", str(work)),
             isSidechain=True)])
    rules(d, {"projects": "all", "sensitive": ["~/vault"]})
    assert run(capsys, "--dir", d, "history")[0] == 0
    path = home / ".claude" / "projects" / "-app" / "s-plain.jsonl"
    assert stop(monkeypatch, capsys, d, path, "s-plain") == (0, "", "")
    assert ade.events == []                                 # its own turn touched nothing there; its sub-agent did


def test_a_turn_that_touched_a_sensitive_folder_is_sensitive_however_long_its_transcript_takes(home, tmp_path, ade):
    d = a_link(ade, tmp_path / "mac")
    work, _ = machine(home)
    rules(d, {"projects": "all", "sensitive": ["~/vault"]})
    link = ade_link.Link(d)
    rule, refused = ade_link._history_rule(link)
    assert refused is None
    path = home / ".claude" / "projects" / "-app" / "s-vault.jsonl"
    with open(path, encoding="utf-8") as fh:
        turn = [json.loads(line) for line in fh]
    late = time.monotonic() - 1                            # no time left to read a transcript
    assert ade_link._sensitive_hold(link, rule, str(path), "s-vault", str(work), turn, late) == "sensitive"
    assert ade_link._sensitive_hold(link, rule, str(path), "s-vault", str(work), [], late) == "unknown"


# ---- posts and checkpoints ------------------------------------------------------------------------------------------

def checkpoint(capsys, d, tmp_path):
    doc = tmp_path / "cp.json"
    doc.write_text(json.dumps({"doing": "the vault's plan"}))
    return run(capsys, "--dir", d, "checkpoint", doc)


@pytest.fixture
def ruled(home, tmp_path, ade, capsys, monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    machine(home)
    rules(d, {"projects": "all", "sensitive": ["~/vault"]})
    assert run(capsys, "--dir", d, "history")[0] == 0
    monkeypatch.chdir(home / "work" / "app")
    return d


def runs_for(monkeypatch, *sessions):
    """The command stands as one run for `sessions` (_posting_sessions, read from Claude Code's records: tested apart)."""
    monkeypatch.setattr(ade_link, "_posting_sessions", lambda: (set(sessions), None))


def test_a_sensitive_session_s_posts_and_checkpoints_are_refused_without_a_key(ruled, ade, capsys, tmp_path,
                                                                               monkeypatch):
    runs_for(monkeypatch, "s-vault")
    for argv in (["step", "the plan"], ["note", "the plan", "--to", "all"], ["note", "the plan", "--to", "cloud"]):
        code, _, err = run(capsys, "--dir", ruled, "post", *argv)
        assert code == 2 and "this session touched a folder history.json names sensitive" in err, argv
        assert "They stay on this machine" in err
    code, _, err = checkpoint(capsys, ruled, tmp_path)
    assert code == 2 and "a checkpoint is never sealed" in err
    assert run(capsys, "--dir", ruled, "working", "41")[0] == 0              # a fixed text carries no words
    assert [e["text"] for e in ade.events] == ["working on it"]
    assert [p for m, p, _ in ade.requests if p == "/api/ade/link/checkpoint"] == []


def test_with_a_key_a_sensitive_session_s_posts_to_the_owner_go_sealed_and_the_rest_stays_refused(ruled, ade, capsys,
                                                                                               tmp_path, monkeypatch):
    runs_for(monkeypatch, "s-vault")
    _, key = ade.owner.state()
    assert run(capsys, "--dir", ruled, "post", "step", "the plan")[0] == 0
    (e,) = ade.events
    assert open_envelope(e["text"], slot(ACCOUNT, LINK_ID, "event", "step", None, None, e["client_id"]), key)[
        "text"] == "the plan"
    code, _, err = run(capsys, "--dir", ruled, "post", "note", "the plan", "--to", "all")
    assert code == 2 and "its words go to the owner alone, sealed" in err
    assert checkpoint(capsys, ruled, tmp_path)[0] == 2 and len(ade.events) == 1


def test_without_ml_kem_a_sensitive_session_s_post_says_this_link_holds_a_key_it_cannot_seal_to(ruled, ade, capsys,
                                                                                              monkeypatch):
    """Held for a post-quantum key this machine cannot seal to, a sensitive session's
    words are refused saying so, not that this link has no key."""
    import e2e_pq_page_side as page
    runs_for(monkeypatch, "s-vault")
    ade.owner.state(public=page.public_of(os.urandom(32)), v=3, retires="all")
    monkeypatch.setitem(sys.modules, "cryptography.hazmat.primitives.asymmetric.mlkem", None)
    monkeypatch.setattr(ade_link, "_E2E_MLKEM", None)
    code, _, err = run(capsys, "--dir", ruled, "post", "step", "the plan")
    assert code == 2 and ade.events == []
    assert ("its words go to the owner only sealed, and this link holds a post-quantum key, which this machine cannot "
            f"seal to ({ade_link.E2E_NO_MLKEM})") in err
    assert "no key to seal" not in err


def test_a_plain_session_posts_as_before(ruled, ade, capsys, tmp_path, monkeypatch):
    runs_for(monkeypatch, "s-plain")
    assert run(capsys, "--dir", ruled, "post", "note", "hello", "--to", "all")[0] == 0
    assert ade.events[0]["text"] == "hello"


def test_where_no_claude_code_session_runs_the_command_the_folder_decides(ruled, ade, capsys, tmp_path, home,
                                                                         monkeypatch):
    runs_for(monkeypatch)
    assert run(capsys, "--dir", ruled, "post", "step", "plain")[0] == 0
    monkeypatch.chdir(home / "vault")
    code, _, err = run(capsys, "--dir", ruled, "post", "step", "secret")
    assert code == 2 and "this command runs in a folder history.json names sensitive" in err
    assert [e["text"] for e in ade.events] == ["plain"]


def test_a_plain_session_in_a_sensitive_folder_is_refused_too(ruled, ade, capsys, tmp_path, home, monkeypatch):
    runs_for(monkeypatch, "s-plain")
    monkeypatch.chdir(home / "vault")
    code, _, err = run(capsys, "--dir", ruled, "post", "step", "secret")
    assert code == 2 and "this command runs in a folder history.json names sensitive" in err and ade.events == []


def test_a_history_json_that_cannot_be_read_refuses_what_it_would_judge(ruled, ade, capsys, tmp_path, monkeypatch):
    rules(ruled, "{not json")
    code, _, err = run(capsys, "--dir", ruled, "post", "step", "x")
    assert code == 2 and "history.json cannot be used" in err
    assert checkpoint(capsys, ruled, tmp_path)[0] == 2 and ade.events == []


def test_the_posting_session_is_the_one_claude_code_s_own_record_names(ruled, ade, capsys, home, monkeypatch):
    # the record as the remote guard reads it: in the password database's home (the test's own here, as the guard's
    # tests point it), naming the running process and its start, which the system confirms
    sessions = home / ".claude" / "sessions"
    sessions.mkdir()
    me = os.getpid()
    monkeypatch.setattr(os, "getppid", lambda: me)                          # this process stands for the session's
    started = ade_link._process_start(me)
    (sessions / f"{me}.json").write_text(json.dumps({"pid": me, "sessionId": "s-vault", "procStart": started}))
    code, _, err = run(capsys, "--dir", ruled, "post", "step", "x")
    assert code == 2 and "this session touched a folder history.json names sensitive" in err
    (sessions / f"{me}.json").write_text(json.dumps({"pid": me, "sessionId": "s-plain", "procStart": started}))
    assert run(capsys, "--dir", ruled, "post", "step", "y")[0] == 0
    assert [e["text"] for e in ade.events] == ["y"]


def test_a_session_judged_by_its_record_reads_on_from_the_last_run(ruled, ade, capsys, tmp_path, home, monkeypatch):
    runs_for(monkeypatch, "s-plain")
    assert run(capsys, "--dir", ruled, "post", "step", "before")[0] == 0
    _write(home / ".claude" / "projects" / "-app" / "s-plain.jsonl",
           [_said(f"see {home / 'vault' / 'plan.md'}", "2026-10-03T10:05:00Z")], mode="a")
    code, _, err = run(capsys, "--dir", ruled, "post", "step", "after")     # touched since the History run read it
    assert code == 2 and "this session touched" in err


def test_a_post_whose_session_cannot_be_judged_in_time_is_refused(ruled, ade, capsys, home, monkeypatch):
    runs_for(monkeypatch, "s-plain")
    _write(home / ".claude" / "projects" / "-app" / "s-plain.jsonl",
           [_said("more plain work", "2026-10-03T10:05:00Z")], mode="a")           # written since the History run
    monkeypatch.setattr(ade_link, "POST_JUDGE", -1)                                  # and no time to read it
    code, _, err = run(capsys, "--dir", ruled, "post", "step", "x")
    assert code == 2 and "could not be told in time" in err and ade.posted() == []
