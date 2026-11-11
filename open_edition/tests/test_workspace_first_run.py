"""Claude Code's first-run choices in a workspace machine (first_run.py: without them a person meets Claude Code's
wizard in a terminal, on a phone too). What the choices do was checked in the image with its claude 2.1.274:
test_workspace_image_docker.py checks it again there."""

import json
import os
import stat

from proofpane_workspace import first_run

ROOT = "/home/ws/workspace"


def read(home):
    return json.loads((home / ".claude.json").read_text())


def test_a_fresh_home_gets_the_choices_readable_by_its_user_only(tmp_path):
    assert first_run.seed(str(tmp_path), ROOT) is True
    assert read(tmp_path) == {"hasCompletedOnboarding": True, "projects": {ROOT: {"hasTrustDialogAccepted": True}}}
    assert stat.S_IMODE(os.stat(tmp_path / ".claude.json").st_mode) == 0o600
    assert first_run.seed(str(tmp_path), ROOT) is False            # made once: the same again writes nothing
    assert not (tmp_path / ".claude.json.proofpane-tmp").exists()


def test_what_claude_code_already_wrote_is_kept(tmp_path):
    before = {"userID": "abc", "theme": "light", "hasCompletedOnboarding": False, "oauthAccount": {"emailAddress": "e"},
              "projects": {"/elsewhere": {"hasTrustDialogAccepted": False}, ROOT: {"allowedTools": ["Bash"]}}}
    (tmp_path / ".claude.json").write_text(json.dumps(before))
    assert first_run.seed(str(tmp_path), ROOT) is True
    assert read(tmp_path) == {"userID": "abc", "theme": "light", "hasCompletedOnboarding": True,
                              "oauthAccount": {"emailAddress": "e"},
                              "projects": {"/elsewhere": {"hasTrustDialogAccepted": False},
                                           ROOT: {"allowedTools": ["Bash"], "hasTrustDialogAccepted": True}}}


def test_odd_projects_are_made_good(tmp_path):
    (tmp_path / ".claude.json").write_text(json.dumps({"projects": ["not", "a", "map"]}))
    assert first_run.seed(str(tmp_path), ROOT) is True
    assert read(tmp_path) == {"hasCompletedOnboarding": True, "projects": {ROOT: {"hasTrustDialogAccepted": True}}}


def test_the_repositories_are_trusted_with_the_root_and_one_more_when_a_session_is_made(tmp_path):
    # a session's worktree is trusted through its repository, not through the workspace's root (Claude Code takes a
    # git worktree as its main repository's; seen in the image with its claude 2.1.274)
    repos = [ROOT + "/repos/a", ROOT + "/repos/b"]
    assert first_run.seed(str(tmp_path), ROOT, repos) is True
    assert read(tmp_path)["projects"] == {p: {"hasTrustDialogAccepted": True} for p in [ROOT, *repos]}
    assert first_run.trust(str(tmp_path), ROOT + "/repos/c") is True
    assert set(read(tmp_path)["projects"]) == {ROOT, *repos, ROOT + "/repos/c"}
    assert first_run.trust(str(tmp_path), ROOT + "/repos/a") is False        # already trusted: nothing is written
    assert read(tmp_path)["hasCompletedOnboarding"] is True


def test_a_file_that_is_not_claude_code_s_json_object_is_left_alone(tmp_path):
    for text in ("not json", "[1, 2]", '"a string"'):
        (tmp_path / ".claude.json").write_text(text)
        assert first_run.seed(str(tmp_path), ROOT) is False
        assert first_run.trust(str(tmp_path), ROOT + "/repos/a") is False
        assert (tmp_path / ".claude.json").read_text() == text


def test_the_agent_makes_them_only_when_asked(tmp_path, monkeypatch):
    # start.sh passes --claude-first-run in a workspace machine; the local provider never does, since there HOME is a
    # person's own
    from proofpane_workspace import __main__ as entry

    async def no_serve(*args, **kwargs):
        return None

    monkeypatch.setattr(entry, "serve", no_serve)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    mask = os.umask(0o022)
    os.umask(mask)
    (tmp_path / "machine" / "repos" / "demo").mkdir(parents=True)        # a repository from an earlier start
    try:
        monkeypatch.setenv("PP_WORKSPACE_SECRET", "s" * 40)
        entry.main(["--root", str(tmp_path / "plain")])
        assert not (home / ".claude.json").exists()
        monkeypatch.setenv("PP_WORKSPACE_SECRET", "s" * 40)
        entry.main(["--root", str(tmp_path / "machine"), "--claude-first-run"])
    finally:
        os.umask(mask)
    machine = os.path.realpath(tmp_path / "machine")
    assert read(home)["projects"] == {machine: {"hasTrustDialogAccepted": True},
                                      machine + "/repos/demo": {"hasTrustDialogAccepted": True}}
    assert read(home)["hasCompletedOnboarding"] is True
    start = open(os.path.join(os.path.dirname(__file__), "..", "workspace_image", "start.sh")).read()
    assert start.count("--claude-first-run") == 1 and "--root /home/ws/workspace" in start


def test_a_session_in_a_repository_trusts_it_in_a_workspace_machine_only(tmp_path, monkeypatch):
    from proofpane_workspace.workspace import Workspace
    from workspace_fixtures import make_repo
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for asked in (False, True):
        root = tmp_path / f"ws-{asked}"
        ws = Workspace(str(root), claude_first_run=asked)
        make_repo(str(root / "repos" / "demo"))
        ws.prepare("demo", "shell", "t")                 # a shell too: a claude started in it asks the same
        if not asked:
            assert not (home / ".claude.json").exists()
    projects = read(home)["projects"]
    assert projects == {os.path.realpath(tmp_path / "ws-True"): {"hasTrustDialogAccepted": True},
                        os.path.realpath(tmp_path / "ws-True" / "repos" / "demo"): {"hasTrustDialogAccepted": True}}


def test_writers_at_once_lose_none_of_each_other_s_repositories(tmp_path):
    """Agents' runs start at once, each trusting its repository: every entry is kept, the file whole and private."""
    import stat
    import threading
    from proofpane_workspace import first_run
    first_run.seed(str(tmp_path), "/ws", [])
    threads = [threading.Thread(target=first_run.trust, args=(str(tmp_path), f"/ws/repos/r{n}")) for n in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    data = json.load(open(tmp_path / ".claude.json"))
    assert {f"/ws/repos/r{n}" for n in range(40)} <= set(data["projects"])
    assert stat.S_IMODE(os.stat(tmp_path / ".claude.json").st_mode) == 0o600
    assert [p for p in os.listdir(tmp_path) if p.endswith(".proofpane-tmp")] == []
