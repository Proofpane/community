"""An image of a session's worktree, for the page to show (workspace.image): only image kinds, by name; never through
a link or outside the worktree; and from the app, sent so that nothing in it can run."""

import os

import pytest

from workspace_fixtures import Agent, make_repo

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


@pytest.fixture
def agent(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home))
    make_repo(str(tmp_path / "ws" / "repos" / "demo"))
    yield a
    a.close()


def _session(agent, tmp_path):
    s = agent.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "Look"}).json()
    return s["id"], tmp_path / "ws" / "sessions" / s["id"] / "work"


def test_an_image_is_sent_as_its_bytes_with_its_type(agent, tmp_path):
    sid, work = _session(agent, tmp_path)
    (work / "docs").mkdir()
    (work / "docs" / "Shot.PNG").write_bytes(PNG)
    (work / "logo.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
    r = agent.http.get(f"/sessions/{sid}/image", params={"path": "docs/Shot.PNG"})
    assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"
    r = agent.http.get(f"/sessions/{sid}/image", params={"path": "logo.svg"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    (work / "Report.PDF").write_bytes(b"%PDF-1.4\n%%EOF\n")
    r = agent.http.get(f"/sessions/{sid}/image", params={"path": "Report.PDF"})
    assert r.status_code == 200 and r.content == b"%PDF-1.4\n%%EOF\n" and r.headers["content-type"] == "application/pdf"


def test_only_an_image_inside_the_worktree_is_sent(agent, tmp_path):
    sid, work = _session(agent, tmp_path)
    (work / "notes.txt").write_text("not an image\n")
    outside = tmp_path / "secret.png"
    outside.write_bytes(PNG)
    os.symlink(outside, work / "link.png")
    os.symlink(tmp_path, work / "up")
    for path, status in (("notes.txt", 400), ("README.md", 400), ("link.png", 400), ("up/secret.png", 400),
                         ("../secret.png", 400), (".git/x.png", 400), ("missing.png", 404), ("", 400)):
        r = agent.http.get(f"/sessions/{sid}/image", params={"path": path})
        assert r.status_code == status, (path, r.status_code, r.text)


def test_an_image_too_large_is_refused(agent, tmp_path):
    sid, work = _session(agent, tmp_path)
    with open(work / "huge.png", "wb") as fh:
        fh.truncate(10 * 1024 * 1024 + 1)
    r = agent.http.get(f"/sessions/{sid}/image", params={"path": "huge.png"})
    assert r.status_code == 400 and "larger than 10 MB" in r.json()["detail"]
