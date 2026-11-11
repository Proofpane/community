"""Where the ADE reads from: a clone with git, and GitHub's REST API (here through a fake opener, never the
network). Both must give the same answers, check every name before using it, and write nothing."""

import json

import pytest

from ade_fixtures import BRANCH, make_repo
from proofpane_open.ade import sources


@pytest.fixture
def demo(tmp_path):
    return make_repo(tmp_path)


def test_a_clone_is_read_at_one_commit(demo):
    path, shas = demo
    src = sources.LocalGit("demo", path)
    head = src.head(BRANCH)
    assert head == shas["merge"] and src.label == "local/demo"
    assert src.list_dir(head, "tasks/demo") == ["GO.md", "HANDOFF-TO-LOCAL-SESSION.md", "NEXT.md",
                                               "local-run-log.md", "owner-answers.md", "questions-for-owner.md",
                                               "run.sh"]
    assert src.list_dir(head, "") == ["README.md"]
    assert src.read_file(head, "tasks/demo/GO.md").startswith("## GO: demo deploy\n")
    assert src.read_file(shas["first"], "src/app.py").endswith("return 1\n")
    history = src.file_commits(head, "tasks/demo/local-run-log.md", 10)
    assert [c["sha"] for c in history] == [shas["log"]]
    assert history[0]["session"]["kind"] == "agent" and history[0]["time"] == "2026-09-27T10:20:30Z"
    handoff = src.file_commits(head, "tasks/demo/NEXT.md", 10)[0]
    assert handoff["session"]["kind"] == "cloud"
    recent = src.branch_commits(head, 30)
    assert [c["sha"] for c in recent][:2] == [shas["merge"], shas["side"]]
    assert len(recent[0]["parents"]) == 2
    diff = src.commit_diff(shas["code"], shas["log"])
    assert diff.startswith("diff --git a/src/app.py b/src/app.py\n") and "+    return 2" in diff


@pytest.mark.parametrize("call", [
    lambda s, h: s.head("no-such-branch"),
    lambda s, h: s.head("../work"),
    lambda s, h: s.head("-x"),
    lambda s, h: s.list_dir(h, "no/such/dir"),
    lambda s, h: s.list_dir(h, "../outside"),
    lambda s, h: s.list_dir(h, "/etc"),
    lambda s, h: s.list_dir("HEAD", "tasks"),
    lambda s, h: s.read_file(h, "tasks/demo/missing.md"),
    lambda s, h: s.read_file(h, "tasks/../README.md"),
    lambda s, h: s.file_commits(h, "a//b", 5),
    lambda s, h: s.commit_diff(h, "HEAD~1"),
])
def test_a_clone_refuses_what_it_cannot_or_must_not_read(demo, call):
    path, _ = demo
    src = sources.LocalGit("demo", path)
    with pytest.raises(sources.SourceError):
        call(src, src.head(BRANCH))


@pytest.mark.parametrize("name,ok", [("owner/repo", True), ("a/b.c-d_e", True), ("owner", False),
                                     ("owner/repo/extra", False), ("owner/..", False), ("https://x/y", False),
                                     ("/repo", False), ("owner/re po", False)])
def test_repository_names(name, ok):
    if ok:
        assert sources.check_repo(name) == name
    else:
        with pytest.raises(sources.SourceError):
            sources.check_repo(name)


@pytest.mark.parametrize("name,ok", [("main", True), ("claude/x-1.2", True), ("a..b", False), ("a//b", False),
                                     ("x/", False), ("x.lock", False), ("a@{1}", False), ("-f", False), ("", False)])
def test_branch_names(name, ok):
    if ok:
        assert sources.check_branch(name) == name
    else:
        with pytest.raises(sources.SourceError):
            sources.check_branch(name)


class FakeGitHub:
    """Answers GitHub's API from a table, and records every request it was asked."""

    def __init__(self, routes):
        self.routes = routes
        self.asked = []

    def __call__(self, url, headers):
        self.asked.append((url, dict(headers)))
        for prefix, answer in self.routes:
            if url.startswith(prefix):
                return answer(url, headers) if callable(answer) else answer
        return 404, {}, b'{"message": "Not Found"}'


API = "https://api.github.com/repos/owner/repo"
SHA = "a" * 40
PARENT = "b" * 40
ITEM = {"sha": SHA, "parents": [{"sha": PARENT}],
        "commit": {"author": {"name": "Claude", "email": "noreply@anthropic.com", "date": "2026-09-27T10:00:00Z"},
                   "message": "Handoff\n\nClaude-Session: https://claude.ai/code/session_X"}}


def _ok(obj, etag=None):
    return 200, ({"ETag": etag} if etag else {}), json.dumps(obj).encode() if not isinstance(obj, bytes) else obj


def test_github_is_read_through_the_api():
    fake = FakeGitHub([
        (f"{API}/git/ref/heads/claude/x", _ok({"object": {"sha": SHA}})),
        (f"{API}/contents/tasks/demo?", _ok([{"name": "GO.md", "type": "file"}, {"name": "sub", "type": "dir"}])),
        (f"{API}/contents/tasks/demo/GO.md?", _ok(b"## GO: x\n")),
        (f"{API}/commits?", _ok([ITEM])),
        (f"{API}/compare/{PARENT}...{SHA}", _ok(b"diff --git a/x b/x\n")),
    ])
    gh = sources.GitHub("owner/repo", opener=fake, cache=sources._Cache())
    assert gh.head("claude/x") == SHA
    assert gh.list_dir(SHA, "tasks/demo") == ["GO.md"]
    assert gh.read_file(SHA, "tasks/demo/GO.md") == "## GO: x\n"
    assert gh.file_commits(SHA, "tasks/demo/GO.md", 10)[0]["session"]["kind"] == "cloud"
    assert gh.commit_diff(SHA, PARENT) == "diff --git a/x b/x\n"
    urls = [u for u, _ in fake.asked]
    assert f"{API}/contents/tasks/demo/GO.md?ref={SHA}" in urls
    assert f"{API}/commits?sha={SHA}&path=tasks%2Fdemo%2FGO.md&per_page=10" in urls
    raw = [h for u, h in fake.asked if "/contents/tasks/demo/GO.md" in u][0]
    assert raw["Accept"] == "application/vnd.github.raw"
    assert all("Authorization" not in h for _, h in fake.asked)


def test_a_token_is_sent_only_to_github_and_only_as_a_bearer():
    fake = FakeGitHub([(f"{API}/git/ref/heads/main", _ok({"object": {"sha": SHA}}))])
    sources.GitHub("owner/repo", token="github_pat_x", opener=fake, cache=sources._Cache()).head("main")
    (url, headers), = fake.asked
    assert url.startswith("https://api.github.com/") and headers["Authorization"] == "Bearer github_pat_x"


def test_an_unchanged_answer_is_asked_for_with_its_etag():
    calls = []

    def contents(url, headers):
        calls.append(headers.get("If-None-Match"))
        if headers.get("If-None-Match") == '"v1"':
            return 304, {}, b""
        return 200, {"ETag": '"v1"'}, b"text"

    fake = FakeGitHub([(f"{API}/contents/", contents)])
    gh = sources.GitHub("owner/repo", opener=fake, cache=sources._Cache())
    assert gh.read_file(SHA, "a.md") == "text" and gh.read_file(SHA, "a.md") == "text"
    assert calls == [None, '"v1"']
    other = sources.GitHub("owner/repo", token="t", opener=fake, cache=gh.cache)
    other.read_file(SHA, "a.md")
    assert calls[-1] is None  # another token does not share the cached answer


@pytest.mark.parametrize("status,headers,token,words", [
    (404, {}, None, "a private repository needs a token"),
    (404, {}, "t", "the token cannot read it."),
    (401, {}, "t", "refused the token"),
    (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1790000000"}, None, "raises it from 60 to 5,000"),
    (500, {}, None, "GitHub answered 500."),
])
def test_github_errors_say_what_to_do(status, headers, token, words):
    gh = sources.GitHub("owner/repo", token=token, opener=lambda u, h: (status, headers, b"{}"),
                        cache=sources._Cache())
    with pytest.raises(sources.SourceError) as e:
        gh.head("main")
    assert words in str(e.value)


def test_paths_are_checked_before_github_is_asked():
    fake = FakeGitHub([])
    gh = sources.GitHub("owner/repo", opener=fake, cache=sources._Cache())
    for bad in ("../x", "/etc/passwd", "a/../../b", "a//b"):
        with pytest.raises(sources.SourceError):
            gh.read_file(SHA, bad)
    with pytest.raises(sources.SourceError):
        gh.read_file("main", "a.md")
    assert fake.asked == []


def test_local_clones_come_only_from_the_admin_setting(monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_ADE_LOCAL_REPOS", "notes=/abs/notes, product = /abs/p ,bad name=/x,rel=relative,=/y")
    assert sources.local_repos() == {"notes": "/abs/notes", "product": "/abs/p"}
    monkeypatch.delenv("OPEN_EDITION_ADE_LOCAL_REPOS")
    assert sources.local_repos() == {}


@pytest.mark.parametrize("value,ok", [("", True), ("https://github.example.com/api/v3", True),
                                      ("http://127.0.0.1:9000", True), ("http://localhost:9000", True),
                                      ("http://github.example.com", False), ("http://10.0.0.1:80", False),
                                      ("ftp://x", False), ("https://x/../y?z", False), ("https://x/../y", False)])
def test_the_api_base_is_https_or_this_machine(monkeypatch, value, ok):
    monkeypatch.setenv("OPEN_EDITION_GITHUB_API", value)
    if ok:
        assert sources.GitHub("owner/repo").API == (value.rstrip("/") or "https://api.github.com")
    else:
        with pytest.raises(sources.SourceError):
            sources.GitHub("owner/repo")


def test_a_rate_limit_says_how_long_until_it_resets_not_a_utc_time():
    """The server knows no reader's time zone: a clock time in UTC reads wrong for a person in New Zealand."""
    import time as clock
    from proofpane_open.ade import sources as src
    github = src.GitHub("acme/demo")
    said = github._error(403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(clock.time()) + 23 * 60 + 5)})
    assert "It resets in 23 minutes." in said and "UTC" not in said
    soon = github._error(429, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(clock.time()) + 10)})
    assert "It resets in 1 minute." in soon
