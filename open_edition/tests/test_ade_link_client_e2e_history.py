"""End-to-end encryption, the link client's History list (open_edition/ade_link.py history): each row sealed in place
of its title, project, branch and repositories (a withheld row its project alone; a listed one dropping repositories
from the end until it fits), the link's pins reported with the list, each message of a conversation sealed and the
conversation cut by its sealed size, sent again once the key changes; and a run that holds sends no list at all.
Against the stand-in of the server's side (e2e_link_stand_in)."""

import hashlib
import json
import os

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

import ade_link
from e2e_link_stand_in import (ACCOUNT, ENVELOPE, LINK_ID, LinkStandIn, NotOpened, a_link, key_id, open_envelope,
                               owner_key, raw_public, slot)
from test_ade_link_client import run
from test_ade_link_history import _said, _talk_entries, _tool, _typed, _write


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.delenv("ADE_OWNER_KEYS", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(ade_link, "sleep", lambda seconds: None)
    monkeypatch.setattr(ade_link, "_moves_soon", lambda link: None)        # no look for moves after a run


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
        json.dump(data, fh)


def sessions(home):
    """A session listed, with its conversation, and one that touched an excepted project."""
    work, private = home / "work" / "app", home / "grove"
    work.mkdir(parents=True)
    private.mkdir()
    projects = home / ".claude" / "projects"
    _write(projects / "-app" / "s-new.jsonl", _talk_entries(str(work))[:6] + [
        {"type": "ai-title", "aiTitle": "the History list"}])
    _write(projects / "-app" / "s-touched.jsonl", [_typed("start", "2026-10-03T11:00:00Z", str(work)),
                                                   _tool("Read", {"file_path": str(private / "plan.md")},
                                                         "2026-10-03T11:01:00Z", str(work))])
    return work


def talk_slot(talk, i, m):
    return slot(ACCOUNT, LINK_ID, "talk", talk["session"], i, len(talk["messages"]), talk["earlier"], m["who"],
                m.get("at"), m.get("ms"))


def puts(ade):
    return [p for m, p, _ in ade.requests if m == "PUT" and p == "/api/ade/link/history"]


def test_each_row_goes_sealed_a_withheld_one_its_project_alone_and_the_pins_go_with_the_list(ade, capsys, tmp_path,
                                                                                             home):
    d = a_link(ade, tmp_path / "mac")
    sessions(home)
    rules(d, {"projects": "all", "except": ["~/grove"], "conversations": True})
    _, key = ade.owner.state()
    ade.require = True
    code, out, err = run(capsys, "--dir", d, "history")
    assert code == 0, out + err
    assert "2 of this machine's sessions listed" in out and "1 conversation(s) sent" in out
    (body,) = ade.histories
    assert body["pins"] == [owner_key(ade.owner.device)["fingerprint"]]
    rows = {r["id"]: r for r in body["sessions"]}
    for r in rows.values():
        assert not {"title", "project", "branch", "repositories"} & set(r) and ENVELOPE.fullmatch(r["sealed"])
    assert open_envelope(rows["s-new"]["sealed"], slot(ACCOUNT, LINK_ID, "history", "s-new", 0), key) == {
        "title": "the History list", "project": "~/work/app", "branch": "main", "repositories": []}
    assert rows["s-touched"]["withheld"] is True
    assert open_envelope(rows["s-touched"]["sealed"], slot(ACCOUNT, LINK_ID, "history", "s-touched", 1), key) == {
        "project": "~/work/app"}
    with pytest.raises(NotOpened):                       # the slot says which: withheld or not
        open_envelope(rows["s-touched"]["sealed"], slot(ACCOUNT, LINK_ID, "history", "s-touched", 0), key)
    (talk,) = ade.talks
    assert talk["session"] == "s-new" and talk["earlier"] == 0
    assert [open_envelope(m["text"], talk_slot(talk, i, m), key) for i, m in enumerate(talk["messages"])] == [
        {"text": "how is the build?"}, {"text": "Checking.\n\nGreen: 12 passed."}]
    assert [m["who"] for m in talk["messages"]] == ["owner", "session"] and all(m.get("at") for m in talk["messages"])


def test_the_pins_go_with_a_list_in_the_clear_too(ade, capsys, tmp_path, home):
    d = a_link(ade, tmp_path / "mac")
    sessions(home)
    rules(d, {"projects": "all"})
    assert run(capsys, "--dir", d, "history")[0] == 0
    (body,) = ade.histories
    assert body["pins"] == [owner_key(ade.owner.device)["fingerprint"]]
    assert {r["id"]: r["title"] for r in body["sessions"]}["s-new"] == "the History list"     # in the clear
    with open(os.path.join(d, "owner_keys.json"), "w") as fh:
        fh.write("{cut")
    assert run(capsys, "--dir", d, "history")[0] == 0 and ade.histories[1]["pins"] == []


def test_a_row_drops_repositories_from_the_end_until_it_fits(tmp_path, monkeypatch):
    os.makedirs(tmp_path / "mac")
    with open(tmp_path / "mac" / "config.json", "w") as fh:
        json.dump({"url": "https://ade.example", "name": "mac", "link_id": LINK_ID}, fh)
    link = ade_link.Link(str(tmp_path / "mac"))
    ek = X25519PrivateKey.generate()
    mode = ("seal", raw_public(ek), "k" * 32, ACCOUNT)
    repos = [f"~/r{n:02d}/" + "x" * 290 for n in range(20)]
    row = {"id": "s1", "title": "t", "withheld": False, "project": "~/p", "branch": None, "client": "vscode",
           "live": None, "started_at": 1, "last_at": 2, "registered": True, "repositories": repos}
    sealed = ade_link._e2e_row(link, row, mode)
    assert sealed["registered"] is True and sealed["client"] == "vscode" and "repositories" not in sealed
    value = open_envelope(sealed["sealed"], slot(ACCOUNT, LINK_ID, "history", "s1", 0), ek)
    kept = value["repositories"]
    assert 0 < len(kept) < 20 and kept == repos[:len(kept)]
    assert len(json.dumps(value, separators=(",", ":"))) <= ade_link.E2E_ROW_MOST
    assert len(json.dumps(dict(value, repositories=repos[:len(kept) + 1]), separators=(",", ":"))) > \
        ade_link.E2E_ROW_MOST
    monkeypatch.setattr(ade_link, "E2E_WITHHELD_MOST", 10)
    assert ade_link._e2e_row(link, dict(row, withheld=True, title=None, repositories=[]), mode) is None
    assert ade_link._e2e_row(link, dict(row, repositories=[]), mode) is not None


def test_a_run_that_holds_sends_no_list_and_leaves_the_ade_s_as_it_was(ade, capsys, tmp_path, home):
    d = a_link(ade, tmp_path / "mac")
    sessions(home)
    rules(d, {"projects": "all", "except": ["~/grove"], "conversations": True})
    assert run(capsys, "--dir", d, "history")[0] == 0 and len(puts(ade)) == 1
    ade.owner.state(device=ade.owner.other)              # on, by a passkey this link has not pinned
    for _ in range(2):
        code, out, _ = run(capsys, "--dir", d, "history")
        assert code == 2 and "nothing listed: encryption may be on for this account" in out
        assert "the list the ADE holds stands" in out
    assert len(puts(ade)) == 1 and len(ade.talks) == 1                    # an empty list would take its rows away
    log = open(os.path.join(d, "mirror.log")).read()
    assert log.count("held: encryption") == 1


def test_a_quiet_run_that_holds_says_so_in_the_mirror_s_log_once(ade, capsys, tmp_path, home):
    d = a_link(ade, tmp_path / "mac")
    sessions(home)
    rules(d, {"projects": "all"})
    ade.owner.state(device=ade.owner.other)
    assert run(capsys, "--dir", d, "history", "--quiet") == (0, "", "")
    assert puts(ade) == [] and "held: encryption" in open(os.path.join(d, "mirror.log")).read()


def test_the_ade_s_409_has_the_list_sealed_and_sent_again(ade, capsys, tmp_path, home, monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    sessions(home)
    rules(d, {"projects": "all"})
    real, key = ade.route, []

    def turned_on_meanwhile(method, path, body):
        if path.endswith("/history") and method == "PUT" and not key:
            key.append(ade.owner.state()[1])             # the owner turns encryption on between the read and the list
            ade.require = True
        return real(method, path, body)
    monkeypatch.setattr(ade, "route", turned_on_meanwhile)
    code, out, err = run(capsys, "--dir", d, "history")
    assert code == 0, out + err
    assert len(puts(ade)) == 2
    (body,) = ade.histories
    sealed = {r["id"]: r["sealed"] for r in body["sessions"]}
    assert open_envelope(sealed["s-new"], slot(ACCOUNT, LINK_ID, "history", "s-new", 0), key[0])["title"] == \
        "the History list"


def test_a_409_whose_statements_this_link_cannot_take_holds_and_no_list_goes_after_it(ade, capsys, tmp_path, home,
                                                                                     monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    sessions(home)
    rules(d, {"projects": "all"})
    real, turned = ade.route, []

    def turned_on_meanwhile(method, path, body):
        if path.endswith("/history") and method == "PUT" and not turned:
            turned.append(ade.owner.state(device=ade.owner.other))   # on, by a passkey this link has not pinned
            ade.require = True
        return real(method, path, body)
    monkeypatch.setattr(ade, "route", turned_on_meanwhile)
    code, out, _ = run(capsys, "--dir", d, "history")
    assert code == 2 and "nothing listed: encryption may be on for this account" in out
    assert len(puts(ade)) == 1 and ade.histories == []           # the one in the clear, refused, and none after it


def test_a_sealed_message_s_slot_counts_the_messages_left_on_the_machine(ade, capsys, tmp_path, home, monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    sessions(home)
    rules(d, {"projects": "all", "except": ["~/grove"], "conversations": True})
    _, key = ade.owner.state()
    monkeypatch.setattr(ade_link, "TALK_MESSAGES", 1)               # the newest message goes, one before it stays
    assert run(capsys, "--dir", d, "history")[0] == 0
    (talk,) = ade.talks
    assert talk["earlier"] == 1 and len(talk["messages"]) == 1
    m = talk["messages"][0]
    assert open_envelope(m["text"], talk_slot(talk, 0, m), key) == {"text": "Checking.\n\nGreen: 12 passed."}


def test_a_list_refused_again_after_the_statements_are_read_is_held(ade, capsys, tmp_path, home):
    d = a_link(ade, tmp_path / "mac")
    sessions(home)
    rules(d, {"projects": "all"})
    _, key = ade.owner.state()
    ade.require = True
    ade.retired.add(key_id(raw_public(key)))
    code, out, _ = run(capsys, "--dir", d, "history")
    assert code == 2 and f"the ADE refused the list sealed to key {key_id(raw_public(key))} too" in out
    assert len(puts(ade)) == 2 and ade.histories == []


def test_a_conversation_kept_before_a_key_was_held_is_sent_again_sealed_and_again_under_a_new_key(ade, capsys,
                                                                                                  tmp_path, home):
    d = a_link(ade, tmp_path / "mac")
    sessions(home)
    rules(d, {"projects": "all", "except": ["~/grove"], "conversations": True})
    assert run(capsys, "--dir", d, "history")[0] == 0
    assert ade.talks[0]["messages"][0]["text"] == "how is the build?"
    cache = json.loads(open(os.path.join(d, "history_cache.json")).read())["sessions"]
    p, kept = next((p, s) for p, s in cache.items() if "talk" in s)
    clear = ade_link._talk(p, kept["offset"], kept["ino"], kept["head"], kept["head_len"])
    assert kept["talk"] == hashlib.sha256(json.dumps(clear, sort_keys=True).encode()).hexdigest()   # as before
    assert run(capsys, "--dir", d, "history")[0] == 0 and len(ade.talks) == 1             # nothing changed
    _, first = ade.owner.state()
    assert run(capsys, "--dir", d, "history")[0] == 0 and len(ade.talks) == 2
    assert open_envelope(ade.talks[1]["messages"][0]["text"], talk_slot(ade.talks[1], 0, ade.talks[1]["messages"][0]),
                         first) == {"text": "how is the build?"}
    assert run(capsys, "--dir", d, "history")[0] == 0 and len(ade.talks) == 2             # the same key: not again
    _, second = ade.owner.state()
    assert run(capsys, "--dir", d, "history")[0] == 0 and len(ade.talks) == 3
    m = ade.talks[2]["messages"][1]
    assert open_envelope(m["text"], talk_slot(ade.talks[2], 1, m), second) == {"text": "Checking.\n\nGreen: 12 passed."}


def test_a_message_is_cut_to_what_an_envelope_holds_and_a_conversation_by_its_sealed_size(home, monkeypatch):
    path = home / ".claude" / "projects" / "-w" / "s1.jsonl"
    w = str(home)
    _write(path, [_typed("one", "2026-10-03T10:00:00Z", w), _said("\x01" * 3990, "2026-10-03T10:01:00Z"),
                  _typed("two", "2026-10-03T10:02:00Z", w), _said("three", "2026-10-03T10:03:00Z")])
    st = os.stat(path)
    clear = ade_link._talk(str(path), st.st_size, st.st_ino)
    assert clear["messages"][1]["text"] == "\x01" * 3990                        # in the clear: as it was said
    sealed = ade_link._talk(str(path), st.st_size, st.st_ino, sealed=True)
    cut = sealed["messages"][1]["text"]
    plain = len(json.dumps({"text": cut}, separators=(",", ":")))
    assert cut.endswith("more characters not sent]") and plain <= ade_link.E2E_TALK_MOST
    assert plain > ade_link.E2E_TALK_MOST - 100                                  # cut no shorter than it must be
    assert [m["text"] for m in sealed["messages"][2:]] == ["two", "three"]
    # the conversation: its newest messages while their envelopes come to the total
    monkeypatch.setattr(ade_link, "E2E_TALK_TOTAL", 3 * ade_link.E2E_OVERHEAD + 40)
    few = ade_link._talk(str(path), st.st_size, st.st_ino, sealed=True)
    assert [m["text"] for m in few["messages"]] == ["two", "three"] and few["earlier"] == 2
    assert ade_link._talk(str(path), st.st_size, st.st_ino)["earlier"] == 0     # the clear one is not cut so


def test_a_run_with_a_key_sends_a_message_cut_to_what_its_envelope_holds(ade, capsys, tmp_path, home):
    d = a_link(ade, tmp_path / "mac")
    work = home / "work"
    work.mkdir()
    _write(home / ".claude" / "projects" / "-work" / "s1.jsonl",
           [_typed("one", "2026-10-03T10:00:00Z", str(work)), _said("\x01" * 3990, "2026-10-03T10:01:00Z")])
    rules(d, {"projects": "all", "conversations": True})
    _, key = ade.owner.state()
    assert run(capsys, "--dir", d, "history")[0] == 0
    (talk,) = ade.talks
    m = talk["messages"][1]
    text = open_envelope(m["text"], talk_slot(talk, 1, m), key)["text"]
    assert text.endswith("more characters not sent]") and text.startswith("\x01" * 100)
    assert len(json.dumps({"text": text}, separators=(",", ":"))) <= ade_link.E2E_TALK_MOST


def test_each_history_run_reads_the_statements_again(ade, capsys, tmp_path, home):
    d = a_link(ade, tmp_path / "mac")
    sessions(home)
    rules(d, {"projects": "all"})
    for _ in range(2):
        assert run(capsys, "--dir", d, "history")[0] == 0
    assert len([1 for m, p, _ in ade.requests if m == "GET" and p == "/api/ade/link/e2e"]) == 2


def test_a_list_goes_in_the_clear_again_once_an_e2e_off_is_verified(ade, capsys, tmp_path, home):
    d = a_link(ade, tmp_path / "mac")
    sessions(home)
    rules(d, {"projects": "all", "except": ["~/grove"], "conversations": True})
    ade.owner.state()
    assert run(capsys, "--dir", d, "history")[0] == 0 and "sealed" in ade.histories[0]["sessions"][0]
    ade.owner.state("e2e-off")
    assert run(capsys, "--dir", d, "history")[0] == 0
    assert {r["id"]: r.get("title") for r in ade.histories[1]["sessions"]}["s-new"] == "the History list"
    assert ade.talks[-1]["messages"][0]["text"] == "how is the build?"           # and its conversation again
