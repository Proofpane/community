"""A repository already on this machine, added where it is (workspace.link): only where the workspace runs on the
person's own machine (the agent's --link-repos, which the local provider gives); a session's worktree and branch are
made from it as `git worktree` makes them, and nothing else in its folder changes; taking it out removes the link and
never the folder."""

import os
import subprocess

import pytest

from workspace_fixtures import Agent, git, make_repo


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def agent(tmp_path, home):
    a = Agent(str(tmp_path / "ws"), str(home), args=["--link-repos"])
    yield a
    a.close()


def _status(path):
    return git("status", "--porcelain", "--ignored", cwd=path)


def test_without_the_local_provider_s_switch_no_folder_is_added(tmp_path, home):
    make_repo(str(tmp_path / "mine"))
    a = Agent(str(tmp_path / "ws"), str(home))
    try:
        r = a.http.post("/repos/link", json={"path": str(tmp_path / "mine")})
        assert r.status_code == 400 and "Only the desktop app's workspace" in r.json()["detail"]
        assert a.http.get("/repos").json()["repos"] == []
    finally:
        a.close()


def test_a_repository_is_added_where_it_is_and_a_session_s_worktree_is_made_from_it(agent, tmp_path):
    mine = tmp_path / "code" / "parser"
    head = make_repo(str(mine), origin="git@github.com:acme/parser.git")
    (mine / "notes.local").write_text("not committed\n")
    before = _status(str(mine))
    r = agent.http.post("/repos/link", json={"path": str(mine)})
    assert r.status_code == 200, r.text
    assert r.json() == {"name": "parser", "label": "acme/parser", "linked": os.path.realpath(mine)}
    (listed,) = agent.http.get("/repos").json()["repos"]
    assert (listed["name"], listed["linked"], listed["head"], listed["branch"]) == \
        ("parser", os.path.realpath(mine), head, "main")
    s = agent.http.post("/sessions", json={"repo": "parser", "program": "shell", "task": "t"}).json()
    work = os.path.join(agent.root, "sessions", s["id"], "work")
    assert os.path.isfile(os.path.join(work, "README.md")) and not os.path.exists(os.path.join(work, "notes.local"))
    # the branch is in the person's repository, as a worktree's is; its working tree is as it was
    assert git("branch", "--list", f"pp/{s['id']}", cwd=str(mine)).strip().endswith(f"pp/{s['id']}")
    assert _status(str(mine)) == before and (mine / "notes.local").read_text() == "not committed\n"
    # added again, it is the same one
    assert agent.http.post("/repos/link", json={"path": str(mine) + "/"}).json()["name"] == "parser"
    assert len(agent.http.get("/repos").json()["repos"]) == 1


def test_a_name_is_made_from_the_folder_s_and_two_folders_of_one_name_get_two(agent, tmp_path):
    make_repo(str(tmp_path / "a" / "my app (old)"))
    make_repo(str(tmp_path / "b" / "my app (old)"))
    first = agent.http.post("/repos/link", json={"path": str(tmp_path / "a" / "my app (old)")}).json()
    second = agent.http.post("/repos/link", json={"path": str(tmp_path / "b" / "my app (old)")}).json()
    assert (first["name"], second["name"]) == ("my-app-old", "my-app-old-2")
    assert first["label"] == "workspace/my-app-old"


def test_a_link_to_the_folder_is_followed_to_the_folder_itself(agent, tmp_path):
    make_repo(str(tmp_path / "real"))
    os.symlink(tmp_path / "real", tmp_path / "alias")
    r = agent.http.post("/repos/link", json={"path": str(tmp_path / "alias")}).json()
    assert (r["name"], r["linked"]) == ("real", os.path.realpath(tmp_path / "real"))


@pytest.mark.parametrize("what,said", [
    ("relative", "full path, from /"), ("missing", "There is no folder"), ("not a repository", "not a repository's own"),
    ("inside a repository", "inside the repository at "), ("a worktree", "a worktree's .git is a file"),
    ("the workspace's own", "holds the workspace, or is in it"), ("holds the workspace", "holds the workspace"),
    ("empty", "full path"), ("a file", "is a file: give the folder of the repository it is in"),
    ("no commit yet", "has no commit yet")])
def test_what_is_not_a_repository_s_own_folder_elsewhere_is_refused(agent, tmp_path, what, said):
    make_repo(str(tmp_path / "repo"))
    (tmp_path / "repo" / "src").mkdir()
    (tmp_path / "plain").mkdir()
    (tmp_path / "fresh").mkdir()
    git("init", "-q", cwd=str(tmp_path / "fresh"))
    git("worktree", "add", "-q", "-b", "side", str(tmp_path / "side"), cwd=str(tmp_path / "repo"))
    # the workspace's own repositories are in its folder; a repository holding the workspace is refused too
    make_repo(os.path.join(agent.root, "repos", "cloned"))
    path = {"relative": "repo", "missing": str(tmp_path / "nowhere"), "not a repository": str(tmp_path / "plain"),
            "inside a repository": str(tmp_path / "repo" / "src"), "a worktree": str(tmp_path / "side"),
            "the workspace's own": os.path.join(agent.root, "repos", "cloned"), "holds the workspace": str(tmp_path),
            "empty": "  ", "a file": str(tmp_path / "repo" / "README.md"), "no commit yet": str(tmp_path / "fresh")}[what]
    if what == "holds the workspace":
        git("init", "-q", cwd=str(tmp_path))
    r = agent.http.post("/repos/link", json={"path": path})
    assert r.status_code == 400, (what, r.text)
    assert said in r.json()["detail"], (what, r.json()["detail"])
    if what == "inside a repository":           # named, to choose: never added in its place
        assert os.path.realpath(tmp_path / "repo") in r.json()["detail"]
    assert [x["name"] for x in agent.http.get("/repos").json()["repos"]] == ["cloned"]


def test_taking_one_out_removes_the_link_only_and_waits_for_its_sessions(agent, tmp_path):
    mine = tmp_path / "parser"
    make_repo(str(mine))
    make_repo(os.path.join(agent.root, "repos", "cloned"))
    agent.http.post("/repos/link", json={"path": str(mine)})
    s = agent.http.post("/sessions", json={"repo": "parser", "program": "shell", "task": "t"}).json()
    r = agent.http.delete("/repos/parser")
    assert r.status_code == 400 and r.json()["detail"] == "parser has 1 session: remove it first."
    r = agent.http.delete("/repos/cloned")
    assert r.status_code == 400 and "not added from a folder on this machine" in r.json()["detail"]
    assert agent.http.delete(f"/sessions/{s['id']}").status_code == 200
    assert agent.http.delete("/repos/parser").json() == {"unlinked": "parser"}
    assert [x["name"] for x in agent.http.get("/repos").json()["repos"]] == ["cloned"]
    # the folder, its files and its history are all still there; the session's worktree is gone from its list
    assert (mine / "README.md").read_text() == "demo\n" and git("log", "--oneline", cwd=str(mine)).strip()
    assert len(git("worktree", "list", cwd=str(mine)).splitlines()) == 1
    assert agent.http.delete("/repos/parser").status_code == 404      # it is gone
    assert subprocess.run(["git", "rev-parse", "--git-dir"], cwd=mine, capture_output=True).returncode == 0


def test_removing_a_session_never_prunes_the_person_s_own_worktrees(agent, tmp_path):
    mine = tmp_path / "parser"
    make_repo(str(mine))
    away = tmp_path / "parser-feature"
    git("worktree", "add", "-q", "-b", "feature", str(away), cwd=str(mine))
    os.rename(away, tmp_path / "elsewhere")                  # its folder away: on a disk not mounted, say
    agent.http.post("/repos/link", json={"path": str(mine)})
    s = agent.http.post("/sessions", json={"repo": "parser", "program": "shell", "task": "t"}).json()
    assert sorted(os.listdir(mine / ".git" / "worktrees")) == ["parser-feature", "work"]
    assert agent.http.delete(f"/sessions/{s['id']}").status_code == 200
    # the session's own record goes, the person's stays
    assert os.listdir(mine / ".git" / "worktrees") == ["parser-feature"]


def test_a_session_whose_worktree_folder_went_first_takes_only_its_own_record(agent, tmp_path):
    import shutil as sh
    mine = tmp_path / "parser"
    make_repo(str(mine))
    git("worktree", "add", "-q", "-b", "feature", str(tmp_path / "theirs"), cwd=str(mine))
    os.rename(tmp_path / "theirs", tmp_path / "theirs-away")
    agent.http.post("/repos/link", json={"path": str(mine)})
    s = agent.http.post("/sessions", json={"repo": "parser", "program": "shell", "task": "t"}).json()
    agent.http.post(f"/sessions/{s['id']}/stop")
    sh.rmtree(os.path.join(agent.root, "sessions", s["id"], "work"))
    assert agent.http.delete(f"/sessions/{s['id']}").status_code == 200
    assert os.listdir(mine / ".git" / "worktrees") == ["theirs"]


def test_its_servers_approvals_go_with_it_and_never_to_the_next_folder_of_its_name(agent, tmp_path):
    from proofpane_workspace import mcp
    make_repo(str(tmp_path / "a" / "parser"))
    make_repo(str(tmp_path / "b" / "parser"))
    agent.http.post("/repos/link", json={"path": str(tmp_path / "a" / "parser")})
    mcp.save_approvals(agent.root, {"parser": [["tools", "f" * 64]], "other": [["x", "e" * 64]]})
    assert agent.http.delete("/repos/parser").status_code == 200
    assert mcp.load_approvals(agent.root) == {"other": [["x", "e" * 64]]}
    assert agent.http.post("/repos/link", json={"path": str(tmp_path / "b" / "parser")}).json()["name"] == "parser"


def test_git_never_climbs_out_of_a_linked_folder_to_one_around_it(agent, tmp_path):
    around = tmp_path / "dotfiles"
    make_repo(str(around))
    mine = around / "code" / "parser"
    make_repo(str(mine))
    agent.http.post("/repos/link", json={"path": str(mine)})
    os.unlink(mine / ".git" / "HEAD")                         # its .git broken: half an operation, say
    assert [r["name"] for r in agent.http.get("/repos").json()["repos"]] == []
    r = agent.http.post("/sessions", json={"repo": "parser", "program": "shell", "task": "t"})
    assert r.status_code in (400, 404), r.text
    assert "pp/" not in git("branch", "--list", cwd=str(around))


def test_a_name_is_taken_out_only_as_it_is_listed(agent, tmp_path):
    mine = tmp_path / "parser"
    make_repo(str(mine))
    agent.http.post("/repos/link", json={"path": str(mine)})
    s = agent.http.post("/sessions", json={"repo": "parser", "program": "shell", "task": "t"}).json()
    # a Mac's file system finds "PARSER" as "parser": the session on it is still counted, by the exact name
    assert agent.http.delete("/repos/PARSER").status_code == 404
    assert agent.http.post("/sessions", json={"repo": "PARSER", "program": "shell", "task": "t"}).status_code == 404
    assert agent.http.get(f"/sessions/{s['id']}").status_code == 200
    assert [r["name"] for r in agent.http.get("/repos").json()["repos"]] == ["parser"]


def test_a_folder_moved_away_is_listed_to_be_taken_out_and_its_name_is_never_cloned_over(agent, tmp_path):
    mine = tmp_path / "demo"
    make_repo(str(mine))
    agent.http.post("/repos/link", json={"path": str(mine)})
    os.rename(mine, tmp_path / "moved")
    listed = agent.http.get("/repos").json()["repos"]
    assert [(r["name"], r.get("missing"), r["linked"]) for r in listed] == [("demo", True, os.path.realpath(mine))]
    r = agent.http.post("/repos", json={"url": "https://example.invalid/acme/demo.git"})     # never fetched
    assert r.status_code == 400 and "already has a repository named demo" in r.json()["detail"]
    assert agent.http.delete("/repos/demo").json() == {"unlinked": "demo"}
    assert agent.http.get("/repos").json()["repos"] == []


def test_a_folder_s_name_is_made_clean_and_its_case_as_on_disk(agent, tmp_path):
    for folder in ("v1..v2", "lib.git.git"):
        make_repo(str(tmp_path / folder))
    assert agent.http.post("/repos/link", json={"path": str(tmp_path / "v1..v2")}).json()["name"] == "v1-v2"
    assert agent.http.post("/repos/link", json={"path": str(tmp_path / "lib.git.git")}).json()["name"] == "lib"
    make_repo(str(tmp_path / "Parser"))
    typed = str(tmp_path / "parser")
    if os.path.exists(typed):                                   # a file system that ignores case, as a Mac's
        r = agent.http.post("/repos/link", json={"path": typed}).json()
        assert (r["name"], r["linked"]) == ("Parser", os.path.realpath(tmp_path / "Parser"))


def test_a_repository_on_a_branch_with_no_commit_yet_beside_others_is_added_and_again(agent, tmp_path):
    # its HEAD on an orphan branch, its commits on others: a session can start from one of those
    make_repo(str(tmp_path / "repo"))
    git("checkout", "-q", "--orphan", "fresh", cwd=str(tmp_path / "repo"))
    r = agent.http.post("/repos/link", json={"path": str(tmp_path / "repo")})
    assert r.status_code == 200, r.text
    again = agent.http.post("/repos/link", json={"path": str(tmp_path / "repo")})
    assert again.status_code == 200 and again.json()["name"] == r.json()["name"]



# a finished session's branch into the person's own branch, as a fast-forward (workspace.land)

def _landing(agent, tmp_path, name="mine"):
    mine = tmp_path / name
    make_repo(str(mine))
    agent.http.post("/repos/link", json={"path": str(mine)})
    s = agent.http.post("/sessions", json={"repo": name, "program": "shell", "task": "Add notes"}).json()
    work = tmp_path / "ws" / "sessions" / s["id"] / "work"
    (work / "notes.txt").write_text("notes\n")
    r = agent.http.post(f"/sessions/{s['id']}/finish", json={"author_name": "O", "author_email": "o@example.com"})
    assert r.status_code == 200, r.text
    return mine, s, work


def test_a_finished_session_is_merged_into_the_branch_it_started_from_as_a_fast_forward(agent, tmp_path):
    mine, s, work = _landing(agent, tmp_path)
    r = agent.http.post(f"/sessions/{s['id']}/land")
    assert r.status_code == 200, r.text
    assert r.json() == {"into": "main", "head": git("rev-parse", "HEAD", cwd=str(work)), "commits": 1}
    # main moved, and the person's own files with it (main is checked out in their folder); no merge commit
    assert git("rev-parse", "main", cwd=str(mine)) == git("rev-parse", "HEAD", cwd=str(work))
    assert (mine / "notes.txt").read_text() == "notes\n" and git("status", "--porcelain", cwd=str(mine)) == ""
    assert git("rev-list", "--merges", "--count", "main", cwd=str(mine)) == "0"
    assert agent.http.get(f"/sessions/{s['id']}").json()["landed"]["into"] == "main"
    again = agent.http.post(f"/sessions/{s['id']}/land")
    assert again.status_code == 200 and again.json()["commits"] == 0          # in it already


def test_a_branch_checked_out_nowhere_moves_alone(agent, tmp_path):
    mine, s, work = _landing(agent, tmp_path)
    git("checkout", "-q", "-b", "elsewhere", cwd=str(mine))                    # the person works on another branch
    r = agent.http.post(f"/sessions/{s['id']}/land")
    assert r.status_code == 200, r.text
    assert git("rev-parse", "main", cwd=str(mine)) == git("rev-parse", "HEAD", cwd=str(work))
    assert not (mine / "notes.txt").exists()                                  # their own branch's files untouched


@pytest.mark.parametrize("what,said", [
    ("not committed", "changes not committed yet: Finish it first"),
    ("committed since", "commits since its last Finish, which were not verified: Finish it again first"),
    ("moved on", "main has moved on since this session started: bring the session up to date first"),
    ("their changes", "has main checked out, with changes of its own"),
])
def test_what_is_refused_and_why(agent, tmp_path, what, said):
    mine, s, work = _landing(agent, tmp_path)
    if what == "not committed":
        (work / "more.txt").write_text("more\n")
    elif what == "committed since":
        # its agent, still at work after the Finish, commits something of its own
        (work / "later.txt").write_text("later\n")
        git("add", "later.txt", cwd=str(work))
        git("commit", "-q", "-m", "after the finish", cwd=str(work))
    elif what == "moved on":
        (mine / "theirs.txt").write_text("theirs\n")
        git("add", "theirs.txt", cwd=str(mine))
        git("commit", "-q", "-m", "their own", cwd=str(mine))
    else:
        (mine / "README.md").write_text("their edit\n")
    before = git("rev-parse", "main", cwd=str(mine))
    r = agent.http.post(f"/sessions/{s['id']}/land")
    assert r.status_code == 400 and said in r.json()["detail"], r.text
    assert git("rev-parse", "main", cwd=str(mine)) == before                  # nothing moved


def test_a_repository_cloned_into_the_workspace_is_shipped_not_merged_here(agent, tmp_path):
    make_repo(str(tmp_path / "ws" / "repos" / "cloned"))
    s = agent.http.post("/sessions", json={"repo": "cloned", "program": "shell", "task": "t"}).json()
    r = agent.http.post(f"/sessions/{s['id']}/land")
    assert r.status_code == 400 and "shipped, as a pull request" in r.json()["detail"]


def test_a_session_never_finished_is_not_merged(agent, tmp_path):
    mine = tmp_path / "mine"
    make_repo(str(mine))
    agent.http.post("/repos/link", json={"path": str(mine)})
    s = agent.http.post("/sessions", json={"repo": "mine", "program": "shell", "task": "t"}).json()
    work = tmp_path / "ws" / "sessions" / s["id"] / "work"
    (work / "x.txt").write_text("x\n")
    git("add", "x.txt", cwd=str(work))
    git("commit", "-q", "-m", "its own commit, never finished", cwd=str(work))
    r = agent.http.post(f"/sessions/{s['id']}/land")
    assert r.status_code == 400 and "not finished yet: Finish it first" in r.json()["detail"]

