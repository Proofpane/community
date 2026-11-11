"""The ADE: the owner's answers, windows and stops, written back as signed commits, and the check a
session runs before it acts on one. GitHub is played by an in-memory repository (`FakeGitHubRepo`), never the
network."""

import base64
import json
from datetime import datetime, timezone

import pytest

from ade_fixtures import FakeGitHubRepo
from conftest import ADMIN, signed_up
from proofpane_open import signing
from proofpane_open.ade import actions, channel, check, sources
from proofpane_sign import composite, document

NOW = datetime(2026, 9, 28, 3, 0, 5, tzinfo=timezone.utc)
KEY = base64.b64encode(bytes(range(32))).decode()


@pytest.fixture
def key():
    sk = composite.generate()
    return sk, composite.public_key(sk)


def signed(sk, kind="window", text="## GO: x\nwindow: 2026-09-28T03:00Z to 2026-09-28T04:00Z\n", path="t/GO.md"):
    return text, actions.sign_file(sk, kind, "owner/repo", "main", path, text, "owner@example.org", NOW)


def test_a_signature_binds_the_exact_bytes_the_kind_and_the_place(key):
    sk, pk = key
    text, sig = signed(sk)
    ok, st = actions.check_file("window", text, sig, pk, "owner/repo", "main", "t/GO.md")
    assert ok and st["by"] == "owner@example.org" and st["signed_at"] == "2026-09-28T03:00:05Z"
    for kind, t, repo, branch, path, words in [
        ("window", text.replace("04:00Z", "05:00Z"), "owner/repo", "main", "t/GO.md", "changed after it was signed"),
        ("answers", text, "owner/repo", "main", "t/GO.md", "does not verify"),
        ("window", text, "owner/other", "main", "t/GO.md", "signed for repo"),
        ("window", text, "owner/repo", "dev", "t/GO.md", "signed for branch"),
        ("window", text, "owner/repo", "main", "u/GO.md", "signed for path"),
    ]:
        ok, why = actions.check_file(kind, t, sig, pk, repo, branch, path)
        assert not ok and words in why, (kind, why)
    other = composite.public_key(composite.generate())
    assert actions.check_file("window", text, sig, other)[0] is False


@pytest.mark.parametrize("damage", [
    lambda w: "not json",
    lambda w: json.dumps({"statement": w["statement"]}),
    lambda w: json.dumps(dict(w, extra=1)),
    lambda w: json.dumps({"statement": dict(w["statement"], note="x"), "signature": w["signature"]}),
    lambda w: json.dumps({"statement": dict(w["statement"], by="someone@else.org"), "signature": w["signature"]}),
])
def test_a_damaged_signature_file_never_holds(key, damage):
    sk, pk = key
    text, sig = signed(sk)
    assert actions.check_file("window", text, damage(json.loads(sig)), pk)[0] is False


def test_the_texts_the_ade_writes_read_back_as_written():
    answers = actions.answers_text(None, "Q-001", "yes, deploy")
    assert [(a["id"], a["text"]) for a in channel.read_answers("owner-answers.md", answers)] == [("Q-001", "yes, deploy")]
    again = actions.answers_text("header\n---\nW-1: here", "W-2", "the key is in the file")
    assert again == "header\n---\nW-1: here\nW-2: the key is in the file\n"
    start, end = datetime(2026, 9, 28, 3, 0, tzinfo=timezone.utc), datetime(2026, 9, 28, 3, 30, tzinfo=timezone.utc)
    w = actions.window_text("deploy", start, end, "owner@example.org", NOW, "deploy the fix")
    read = channel.read_window("GO.md", w)
    assert (read["name"], read["start"], read["end"], read["problems"]) == (
        "deploy", "2026-09-28T03:00Z", "2026-09-28T03:30Z", [])
    later = actions.window_changed(w, datetime(2026, 9, 28, 4, 0, tzinfo=timezone.utc), "owner@example.org", NOW,
                                   "more time", "Extended")
    reread = channel.read_window("GO.md", later)
    assert (reread["start"], reread["end"], reread["problems"]) == ("2026-09-28T03:00Z", "2026-09-28T04:00Z", [])
    assert later.startswith(w.split("window:")[0]) and later.endswith("to end 2026-09-28T04:00Z: more time\n")
    with pytest.raises(actions.ActionError):
        actions.window_changed("## GO: a\n## GO: b\n", end, "o", NOW, "x", "Extended")


@pytest.mark.parametrize("text", ["", "   ", "two\nlines", "carriage\rreturn", "bell\x07", "x" * 2001, None, 5])
def test_an_owner_s_words_are_one_line(text):
    with pytest.raises(actions.ActionError):
        actions.one_line(text, "answer")


def write_key(tmp_path, pk):
    path = tmp_path / "key.json"
    path.write_text(json.dumps(actions.public_key_json(pk)))
    return str(path)


def test_the_session_s_check(tmp_path, key, capsys):
    sk, pk = key
    text, sig = signed(sk)
    (tmp_path / "GO.md").write_text(text)
    (tmp_path / "GO.md.sig.json").write_text(sig)
    args = [str(tmp_path / "GO.md"), "--key", write_key(tmp_path, pk), "--repo", "owner/repo", "--branch", "main",
            "--path", "t/GO.md"]
    assert check.main(args) == 0
    assert capsys.readouterr().out.startswith("signed: t/GO.md in owner/repo on main, by owner@example.org")
    assert check.main(args + ["--key-id", document.key_id(pk)]) == 0
    assert check.main(args + ["--key-id", "sha256:" + "0" * 64]) == 1
    assert check.main(args[:-1] + ["other/GO.md"]) == 1
    (tmp_path / "GO.md").write_text(text + "- one more line\n")
    assert check.main(args) == 1 and "changed after it was signed" in capsys.readouterr().out
    (tmp_path / "GO.md.sig.json").unlink()
    assert check.main(args) == 1
    (tmp_path / "notes.md").write_text("x")
    assert check.main([str(tmp_path / "notes.md")] + args[1:]) == 2


def test_a_commit_is_made_on_the_head_and_moved_only_forward():
    repo = FakeGitHubRepo(files={"t/NEXT.md": "# next\n"})
    gh = sources.GitHub("owner/repo", token="t", opener=repo, cache=sources._Cache())
    heads = []

    def build(head):
        heads.append(head)
        return {"t/a.md": "a\n", "t/NEXT.md": None}

    sha = gh.commit_files("main", build, "Write a")
    assert repo.head == sha and repo.files() == {"t/a.md": "a\n"}
    assert repo.commits[sha]["message"] == "Write a"
    repo.race = lambda: repo.push({"t/log.md": "a session's line\n"})
    sha = gh.commit_files("main", lambda head: heads.append(head) or {"t/b.md": "b\n"}, "Write b")
    assert len(set(heads[1:])) == 2                        # built again on the head the session moved to
    assert repo.files() == {"t/a.md": "a\n", "t/log.md": "a session's line\n", "t/b.md": "b\n"}
    patches = [json.loads(d) for m, u, d in repo.requests if m == "PATCH"]
    assert len(patches) == 3 and all(p["force"] is False for p in patches)   # 1 + (1 refused, 1 taken)


def test_a_branch_that_keeps_moving_gets_nothing_written():
    repo = FakeGitHubRepo(files={"t/NEXT.md": "# next\n"})
    gh = sources.GitHub("owner/repo", token="t", opener=repo, cache=sources._Cache())

    def build(head):
        repo.race = lambda: repo.push({"t/log.md": head})
        return {"t/a.md": "a\n"}

    before = dict(repo.files())
    with pytest.raises(sources.SourceError) as e:
        gh.commit_files("main", build, "Write a")
    assert "kept moving" in str(e.value) and "t/a.md" not in repo.files() and "t/NEXT.md" in before


def test_writing_needs_a_token_and_the_right_to_write():
    with pytest.raises(sources.SourceError):
        sources.GitHub("owner/repo", opener=FakeGitHubRepo()).commit_files("main", lambda h: {}, "x")
    refuse = FakeGitHubRepo(files={"a": "a"})

    def forbidden(url, headers, method="GET", data=None):
        if method != "GET":
            return 403, {"X-RateLimit-Remaining": "4999"}, b'{"message": "Resource not accessible"}'
        return refuse(url, headers, method, data)

    with pytest.raises(sources.SourceError) as e:
        sources.GitHub("owner/repo", token="t", opener=forbidden, cache=sources._Cache()).commit_files(
            "main", lambda h: {"a": "b"}, "x")
    assert "Contents: read and write" in str(e.value)


# ---- through the routes ---------------------------------------------------------------------------------------

@pytest.fixture
def gh(monkeypatch):
    repo = FakeGitHubRepo(files={
        "t/NEXT.md": "# next\n",
        "t/questions-for-owner.md": "WAITING FOR OWNER (Q-001): may it deploy?\n",
        "t/GO.md": "## GO: old\nwindow: 2026-01-01T00:00Z to 2026-01-01T01:00Z\n",
    })
    monkeypatch.setattr(sources, "_default_opener", repo)
    monkeypatch.setattr(sources, "_SHARED_CACHE", sources._Cache())
    monkeypatch.setenv("OPEN_EDITION_KEYSTORE_KEY", KEY)
    return repo


def connected(client, headers, token="github_pat_x", dirs=("t",)):
    r = client.post("/api/ade/repos", headers=headers, json={"repo": "owner/repo", "branch": "main",
                                                             "dirs": list(dirs), "token": token})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_an_answer_is_one_signed_commit_the_session_accepts(client, gh, tmp_path):
    admin = signed_up(client, ADMIN)
    rid = connected(client, admin)
    r = client.post(f"/api/ade/repos/{rid}/answer", headers=admin, json={"dir": "t", "id": "Q-001", "text": "yes"})
    assert r.status_code == 200, r.text
    files = gh.files()
    assert files["t/owner-answers.md"].endswith("---\nQ-001: yes\n")
    message = gh.commits[gh.head]["message"]
    assert message.startswith("Answer Q-001\n\nWritten through the ADE by admin@example.com at ")
    assert message.rstrip().endswith("Proofpane-ADE: " + document.key_id(signing.public_key()))
    ok, st = actions.check_file("answers", files["t/owner-answers.md"], files["t/owner-answers.md.sig.json"],
                                signing.public_key(), "owner/repo", "main", "t/owner-answers.md")
    assert ok and st["by"] == "admin@example.com"
    board = client.get(f"/api/ade/repos/{rid}/board", headers=admin).json()
    (ch,) = board["channels"]
    assert board["writable"] is True
    assert ch["answers_signatures"]["owner-answers.md"]["state"] == "signed"
    assert [q["open"] for q in ch["questions"]] == [False]
    client.post(f"/api/ade/repos/{rid}/answer", headers=admin, json={"dir": "t", "id": "Q-001", "text": "no, wait"})
    assert gh.files()["t/owner-answers.md"].endswith("Q-001: yes\nQ-001: no, wait\n")


def test_windows_open_extend_and_close(client, gh):
    admin = signed_up(client, ADMIN)
    rid = connected(client, admin)
    body = {"dir": "t", "text": "deploy the fix", "name": "deploy", "minutes": 30}
    assert client.post(f"/api/ade/repos/{rid}/window", headers=admin, json=body).status_code == 200
    new = channel.read_window("GO-2.md", gh.files()["t/GO-2.md"])     # GO.md was taken
    assert new["name"] == "deploy" and new["problems"] == []
    length = channel.parse_time(new["end"]) - channel.parse_time(new["start"])
    assert length.total_seconds() == 30 * 60
    ext = {"dir": "t", "text": "more", "file": "GO-2.md", "minutes": 15}
    assert client.post(f"/api/ade/repos/{rid}/window", headers=admin, json=ext).status_code == 200
    longer = channel.read_window("GO-2.md", gh.files()["t/GO-2.md"])
    assert (channel.parse_time(longer["end"]) - channel.parse_time(new["end"])).total_seconds() == 15 * 60
    close = {"dir": "t", "text": "done", "file": "GO-2.md", "close": True}
    assert client.post(f"/api/ade/repos/{rid}/window", headers=admin, json=close).status_code == 200
    board = client.get(f"/api/ade/repos/{rid}/board", headers=admin).json()
    w = {x["file"]: x for x in board["channels"][0]["windows"]}
    # times are to the minute, so a window closed in the minute it opened ends where it starts: empty, as withdrawn
    assert w["GO-2.md"]["status"] in ("ended", "withdrawn") and w["GO-2.md"]["signature"]["state"] == "signed"
    assert channel.parse_time(w["GO-2.md"]["end"]) <= datetime.now(timezone.utc)
    assert w["GO.md"]["signature"]["state"] == "unsigned"
    r = client.post(f"/api/ade/repos/{rid}/window", headers=admin, json=dict(close, file="GO.md"))
    assert r.status_code == 400 and "has ended" in r.json()["detail"]


def test_a_later_window_closed_before_it_opens_is_withdrawn(client, gh):
    admin = signed_up(client, ADMIN)
    rid = connected(client, admin)
    body = {"dir": "t", "text": "tonight", "name": "later", "minutes": 30, "start_in": 120}
    client.post(f"/api/ade/repos/{rid}/window", headers=admin, json=body)
    client.post(f"/api/ade/repos/{rid}/window", headers=admin,
                json={"dir": "t", "text": "not tonight", "file": "GO-2.md", "close": True})
    w = channel.read_window("GO-2.md", gh.files()["t/GO-2.md"])
    assert w["start"] == w["end"] and w["problems"] == []


def test_stop_and_go_on(client, gh):
    admin = signed_up(client, ADMIN)
    rid = connected(client, admin)
    assert client.post(f"/api/ade/repos/{rid}/stop", headers=admin, json={"dir": "t", "text": "halt"}).status_code == 200
    assert gh.files()["t/STOP.md"].endswith("halt\n") and "t/STOP.md.sig.json" in gh.files()
    again = client.post(f"/api/ade/repos/{rid}/stop", headers=admin, json={"dir": "t", "text": "halt"})
    assert again.status_code == 400
    board = client.get(f"/api/ade/repos/{rid}/board", headers=admin).json()
    assert board["channels"][0]["stop"]["signature"]["state"] == "signed"
    assert client.post(f"/api/ade/repos/{rid}/resume", headers=admin, json={"dir": "t", "text": "go on"}).status_code == 200
    assert "t/STOP.md" not in gh.files() and "t/STOP.md.sig.json" not in gh.files()


@pytest.mark.parametrize("path,body,words", [
    ("answer", {"dir": "elsewhere", "id": "Q-001", "text": "yes"}, "not one of this connection"),
    ("answer", {"dir": "t", "id": "q1", "text": "yes"}, "like W-1 or Q-003"),
    ("answer", {"dir": "t", "id": "Q-001", "text": "two\nlines"}, "one line"),
    ("window", {"dir": "t", "text": "x", "name": "n", "minutes": 0}, "1 to 1440 minutes"),
    ("window", {"dir": "t", "text": "x", "file": "../GO.md"}, "GO.md or GO-<n>.md"),
    ("window", {"dir": "t", "text": "x", "file": "GO-9.md"}, "does not exist"),
    ("window", {"dir": "t", "text": "x"}, "window's name"),
    ("resume", {"dir": "t", "text": "x"}, "has no STOP.md"),
])
def test_actions_refuse_what_is_not_right(client, gh, path, body, words):
    admin = signed_up(client, ADMIN)
    rid = connected(client, admin)
    before = gh.head
    r = client.post(f"/api/ade/repos/{rid}/{path}", headers=admin, json=body)
    assert r.status_code == 400 and words in r.json()["detail"], r.text
    assert gh.head == before


def test_only_a_writable_connection_of_one_s_own_takes_actions(client, gh, monkeypatch):
    admin = signed_up(client, ADMIN)
    member = signed_up(client, "member@example.org")
    rid = connected(client, admin)
    body = {"dir": "t", "id": "Q-001", "text": "yes"}
    assert client.post(f"/api/ade/repos/{rid}/answer", headers=member, json=body).status_code == 404
    public = client.post("/api/ade/repos", headers=member, json={"repo": "owner/repo", "branch": "main", "dirs": ["t"]})
    assert public.status_code == 200
    r = client.post(f"/api/ade/repos/{public.json()['id']}/answer", headers=member, json=body)
    assert r.status_code == 400 and "connected with a token" in r.json()["detail"]
    assert client.get(f"/api/ade/repos/{public.json()['id']}/board", headers=member).json()["writable"] is False
