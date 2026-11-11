"""Git's places (gitplaces.py): what a run could leave in any repository of the workspace, or in git's settings outside
them, for the workspace's own git to start later; read, accepted, and checked before every git the workspace runs.
Here the reading and the record alone, in this process; through the workspace in test_git_places_workspace.py."""

import json
import os
import shutil
import subprocess
import threading
import time

import pytest

from proofpane_workspace import gitplaces
from proofpane_workspace.gitplaces import USER, Checker, Held, git_folder, places, user_places
from workspace_fixtures import git, make_repo

KEY = b"k" * 32


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home of the test's own: the user's git settings are what it writes there, and the system's are left out."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return h


@pytest.fixture
def repo(tmp_path, home):
    top = str(tmp_path / "ws" / "repos" / "demo")
    make_repo(top)
    return top, git_folder(top)[1]


class Rig:
    """A record of git's places, its suspects and its holds as the test sets them."""

    def __init__(self, tmp_path, root=True, **kw):
        self.root = str(tmp_path / "ws")
        os.makedirs(self.root, exist_ok=True)
        self.working = []                   # the agents whose runs work now, as suspects(since) gives them
        self.holds = []
        self.held_agents = threading.Event()
        self.gone = set()
        self.c = Checker(self.root if root else None, KEY, suspects=lambda since: list(self.working),
                         hold=self._hold, known=self._known, folders=[os.path.join(self.root, "repos")], **kw)

    def _hold(self, name, changes, agents, common):
        self.holds.append((name, list(changes), agents, common))
        self.held_agents.set()

    def _known(self, agents):
        return agents is None or any(a not in self.gone for a in agents)


def _set(top, *pairs):
    for key, value in pairs:
        git("config", key, value, cwd=top)


# ---- what is read ----------------------------------------------------------------------------------------------------

DANGEROUS = (("core.fsmonitor", "/tmp/x"), ("core.hooksPath", "/tmp/hooks"), ("gpg.program", "/tmp/x"),
             ("commit.gpgSign", "true"), ("filter.f.clean", "/tmp/x"), ("filter.f.smudge", "/tmp/x"),
             ("diff.d.textconv", "/tmp/x"), ("diff.external", "/tmp/x"), ("core.askPass", "/tmp/x"),
             ("credential.helper", "!/tmp/x"), ("extensions.partialClone", "origin"),
             ("remote.origin.promisor", "true"),
             ("remote.origin.url", "https://example.org/x.git"), ("url.https://evil.example/.insteadOf", "https://"),
             ("uploadpack.packObjectsHook", "/tmp/x"), ("core.sshCommand", "/tmp/x"), ("include.path", "/tmp/none"),
             ("merge.m.driver", "/tmp/x"), ("core.attributesFile", "/tmp/attrs"), ("a.new.key", "anything"))


def _as_git(key):
    """A key as git config --list gives it: its section and its name in lower case, a subsection as written."""
    first, last = key.index("."), key.rindex(".")
    sub = key[first:last + 1] if first != last else "."
    return key[:first].lower() + sub + key[last + 1:].lower()


@pytest.mark.parametrize("key,value", DANGEROUS)
def test_every_key_but_the_quiet_ones_is_a_place(repo, key, value):
    top, common = repo
    before, _ = places(common)
    _set(top, (key, value))
    after, _ = places(common)
    assert "config " + _as_git(key) in gitplaces.changed(before, after)


@pytest.mark.parametrize("key,value", [("user.name", "Ana"), ("user.email", "a@example.org"),
                                       ("branch.main.remote", "origin"), ("branch.main.merge", "refs/heads/main"),
                                       ("remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"),
                                       ("core.ignorecase", "true"), ("pull.rebase", "true"), ("color.ui", "auto")])
def test_a_quiet_key_is_no_change(repo, key, value):
    top, common = repo
    before, _ = places(common)
    _set(top, (key, value))
    assert places(common)[0] == before


def test_a_new_dangerous_key_is_named_as_git_gives_it(repo):
    top, common = repo
    _set(top, ("gpg.program", "/tmp/x"), ("filter.MyFilter.clean", "/tmp/x"))
    found, _ = places(common)
    assert json.loads(found["config gpg.program"]) == ["/tmp/x"]
    assert json.loads(found["config filter.MyFilter.clean"]) == ["/tmp/x"]     # a subsection as written


def test_hooks_info_and_alternates_are_places_and_git_s_samples_are_not(repo):
    top, common = repo
    before, _ = places(common)
    with open(os.path.join(common, "hooks", "post-checkout.sample"), "w") as fh:
        fh.write("#!/bin/sh\n")
    assert places(common)[0] == before
    for name in ("hooks/post-checkout", "info/attributes", "objects/info/alternates"):
        path = os.path.join(common, name)
        with open(path, "w") as fh:
            fh.write("x\n")
        first = places(common)[0]
        assert name in gitplaces.changed(before, first)
        with open(path, "w") as fh:
            fh.write("y\n")                         # the same file, the same size, its content changed
        assert places(common)[0][name] != first[name]


def test_a_hook_that_is_a_link_changes_with_what_it_leads_to(repo, tmp_path):
    top, common = repo
    target = tmp_path / "elsewhere.sh"
    target.write_text("#!/bin/sh\n")
    os.symlink(target, os.path.join(common, "hooks", "pre-commit"))
    before, _ = places(common)
    target.write_text("#!/bin/sh\ntouch /tmp/x\n")
    assert places(common)[0]["hooks/pre-commit"] != before["hooks/pre-commit"]


def test_a_hooks_folder_named_by_an_absolute_path_is_read_and_a_relative_one_is_not(repo, tmp_path):
    top, common = repo
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    _set(top, ("core.hooksPath", str(hooks)))
    before, _ = places(common)
    (hooks / "pre-commit").write_text("#!/bin/sh\n")
    assert "hooksPath " + str(hooks) + "/pre-commit" in places(common)[0]
    _set(top, ("core.hooksPath", ".husky/_"))
    assert not any(k.startswith("hooksPath ") for k in places(common)[0])


def test_an_include_is_read_whatever_its_condition(repo, tmp_path):
    """A conditional include git would follow only where the condition holds (another branch, another worktree's
    folder) is read here all the same, and what it includes in turn."""
    top, common = repo
    inner = tmp_path / "inner.inc"
    inner.write_text("[core]\n\tpager = less\n")
    outer = tmp_path / "outer.inc"
    outer.write_text(f"[include]\n\tpath = {inner}\n")
    _set(top, ("includeIf.onbranch:never-checked-out.path", str(outer)))
    before, paths = places(common)
    assert str(inner) in paths and str(outer) in paths
    assert json.loads(before["config core.pager"]) == ["less"]
    inner.write_text("[core]\n\tfsmonitor = /tmp/x\n")
    after, _ = places(common)
    assert "config core.fsmonitor" in gitplaces.changed(before, after)


def test_an_include_by_a_relative_path_is_read_from_the_folder_of_the_file_that_names_it(repo):
    top, common = repo
    with open(os.path.join(common, "extra.inc"), "w") as fh:
        fh.write("[core]\n\tsshCommand = /tmp/x\n")
    _set(top, ("include.path", "extra.inc"))
    assert json.loads(places(common)[0]["config core.sshcommand"]) == ["/tmp/x"]


def test_an_unreadable_configuration_is_a_place(repo):
    top, common = repo
    with open(os.path.join(common, "config"), "a") as fh:
        fh.write("[broken\n")
    found, _ = places(common)
    assert found.get("config file " + os.path.join(common, "config")) == "unreadable"


def test_a_pipe_where_a_file_belongs_is_named_and_never_waited_on(repo):
    top, common = repo
    os.makedirs(os.path.join(common, "worktrees", "w"))
    os.mkfifo(os.path.join(common, "worktrees", "w", "commondir"))           # read as text: never waited on either
    os.mkfifo(os.path.join(common, "hooks", "pre-push"))
    with open(os.path.join(common, "config"), "a") as fh:
        fh.write("[include]\n\tpath = pipe.inc\n")
    os.mkfifo(os.path.join(common, "pipe.inc"))
    began = time.time()
    found, _ = places(common)
    assert time.time() - began < 5
    assert found["hooks/pre-push"].startswith("other ")
    assert found["config file " + os.path.join(common, "pipe.inc")] == "not a file"


def test_a_worktree_whose_git_file_points_elsewhere_is_a_change(repo, tmp_path):
    top, common = repo
    work = str(tmp_path / "work")
    git("worktree", "add", "-q", "-b", "w", work, cwd=top)
    before, _ = places(common)
    assert not any(k.startswith("worktrees/") for k in before)                  # one as git made it: nothing to name
    evil = tmp_path / "evil"
    make_repo(str(evil))
    with open(os.path.join(work, ".git"), "w") as fh:
        fh.write(f"gitdir: {evil}/.git\n")
    after, _ = places(common)
    assert [k for k in after if k.startswith("worktrees/")] == [f"worktrees/{os.path.basename(work)}: its .git"]


def test_a_git_link_to_a_git_file_is_followed_as_git_follows_it(repo, tmp_path):
    """.git a link to a .git file elsewhere: git follows the link, then the file, to the repository's real git folder,
    and so does the reader (else it would read the file's own folder, which has no places, and accept that)."""
    top, common = repo
    work = str(tmp_path / "work")
    git("worktree", "add", "-q", "-b", "w", work, cwd=top)
    os.rename(os.path.join(work, ".git"), str(tmp_path / "the-file"))
    os.symlink(str(tmp_path / "the-file"), os.path.join(work, ".git"))
    said = subprocess.run(["git", "rev-parse", "--absolute-git-dir", "--git-common-dir"], cwd=work, check=True,
                          capture_output=True, text=True).stdout.split()
    assert git_folder(work) == (os.path.realpath(said[0]), os.path.realpath(os.path.join(work, said[1])))
    assert git_folder(work)[1] == common


def test_git_folder_is_found_as_git_finds_it(repo, tmp_path):
    top, common = repo
    assert git_folder(top) == (os.path.realpath(os.path.join(top, ".git")),) * 2
    work = str(tmp_path / "work")
    git("worktree", "add", "-q", "-b", "w", work, cwd=top)
    git_dir, found = git_folder(work)
    assert found == common and git_dir == os.path.join(common, "worktrees", "work")
    assert git_folder(str(tmp_path)) is None
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    assert git_folder(str(bare)) == (str(bare.resolve()),) * 2


def test_the_user_s_settings_and_what_they_include_are_places(home, tmp_path):
    before, paths = user_places()
    assert str(home / ".gitconfig") in paths                # there or not: its making is seen
    inc = tmp_path / "mine.inc"
    inc.write_text("[core]\n\tpager = less\n")
    (home / ".gitconfig").write_text(f"[user]\n\tname = Ana\n[include]\n\tpath = {inc}\n")
    middle, _ = user_places()
    assert set(gitplaces.changed(before, middle)) == {"config include.path", "config core.pager"}
    inc.write_text("[filter \"f\"]\n\tclean = /tmp/x\n[core]\n\tpager = less\n")
    after, _ = user_places()
    assert gitplaces.changed(middle, after) == ["config filter.f.clean"]
    (home / ".config" / "git").mkdir(parents=True)
    (home / ".config" / "git" / "attributes").write_text("* filter=f\n")
    assert "attributes " + str(home / ".config" / "git" / "attributes") in user_places()[0]


# ---- the record: accepted, held, released ---------------------------------------------------------------------------

def _plant(common, name="post-checkout"):
    path = os.path.join(common, "hooks", name)
    with open(path, "w") as fh:
        fh.write("#!/bin/sh\ntouch /tmp/planted\n")
    os.chmod(path, 0o755)


def test_a_change_while_no_run_worked_is_the_owner_s_and_accepted(tmp_path, repo):
    top, common = repo
    rig = Rig(tmp_path)
    rig.c.first_look([top])
    _plant(common)
    rig.c.check_at(top)                                 # nothing raised: no agent's run worked since the last look
    assert rig.holds == [] and "hooks/post-checkout" in rig.c._accepted[common]["places"]
    assert rig.c._accepted[common]["how"] == "changed while no agent's run worked"


def test_a_change_while_a_run_worked_holds_the_repository_and_its_suspects_until_one_is_resumed(tmp_path, repo):
    top, common = repo
    rig = Rig(tmp_path)
    rig.c.first_look([top])
    rig.working = ["a1"]
    _plant(common)
    with pytest.raises(Held) as held:
        rig.c.check_at(top)
    assert "The git settings of the repository demo changed while an agent's run worked (hooks/post-checkout)" in \
        str(held.value)
    assert rig.held_agents.wait(5)
    assert rig.holds == [("demo", ["hooks/post-checkout"], ["a1"], common)]
    rig.working = []
    with pytest.raises(Held):                           # held, whoever works now: until an agent held for it is resumed
        rig.c.check_at(top)
    assert rig.c.release("b2") == []                    # an agent not held for it releases nothing
    with pytest.raises(Held):
        rig.c.check_at(top)
    assert rig.c.release("a1") == ["demo"]
    rig.c.check_at(top)
    assert rig.c._accepted[common]["how"] == "the owner resumed the agent a1, held for it"


def test_a_hold_is_kept_across_a_start(tmp_path, repo):
    top, common = repo
    rig = Rig(tmp_path)
    rig.c.first_look([top])
    rig.working = ["a1"]
    _plant(common)
    with pytest.raises(Held):
        rig.c.check_at(top)
    again = Rig(tmp_path)
    again.c.first_look([top])
    with pytest.raises(Held):
        again.c.check_at(top)
    assert again.c.release("a1") == ["demo"]


def test_a_hold_whose_agents_are_all_gone_is_accepted_at_the_next_look(tmp_path, repo):
    top, common = repo
    rig = Rig(tmp_path)
    rig.c.first_look([top])
    rig.working = ["a1"]
    _plant(common)
    with pytest.raises(Held):
        rig.c.check_at(top)
    rig.working = []
    rig.gone.add("a1")
    rig.c.check_at(top)
    assert rig.c.held() == {} and rig.c._accepted[common]["how"] == "every agent held for it is gone"


def test_a_record_written_by_something_else_is_not_believed(tmp_path, repo):
    top, common = repo
    rig = Rig(tmp_path)
    rig.c.first_look([top])
    path = os.path.join(rig.root, gitplaces.RECORD)
    data = json.load(open(path))
    data["accepted"][common]["places"]["hooks/post-checkout"] = "file 755 1 forged"
    with open(path, "w") as fh:
        json.dump(data, fh)                             # its seal left as it was: it no longer verifies
    _plant(common)
    again = Rig(tmp_path)
    again.c.first_look([top])
    with pytest.raises(Held) as held:
        again.c.check_at(top)
    assert gitplaces.UNTRUSTED in str(held.value)
    assert again.held_agents.wait(5) and again.holds[0][2] is None              # every agent held
    assert again.c.release("a1") == ["your git settings", "demo"]                # what it held, and what it names
    again.c.check_at(top)                               # resumed: everything there accepted as it is


def test_a_record_taken_away_while_a_run_was_working_is_no_first_start(tmp_path, repo):
    """No record, and a run that was working when the workspace stopped: nothing is accepted for having been found
    at the start, and the first check holds what its runs could have changed."""
    top, common = repo
    rig = Rig(tmp_path)
    rig.c.first_look([top])
    os.unlink(os.path.join(rig.root, gitplaces.RECORD))
    _plant(common)
    again = Rig(tmp_path)
    again.working = ["a1"]                              # the stopped run's agent, ended at the start
    again.c.first_look([top], trusted=False)
    with pytest.raises(Held) as held:
        again.c.check_at(top)
    assert "never accepted" in str(held.value)


def test_resuming_after_a_start_with_no_record_accepts_every_place_never_accepted(tmp_path, repo):
    """No record and a run that was working at the stop: the first check holds, the owner resumes its agent, and no
    other repository is held again for the same run as each is first looked at."""
    top, common = repo
    other = str(tmp_path / "ws" / "repos" / "other")
    make_repo(other)
    rig = Rig(tmp_path)
    rig.working = ["a1"]                                # the stopped run's agent, ended at the start
    rig.c.first_look([top, other], trusted=False)
    with pytest.raises(Held):
        rig.c.check_at(top)
    assert rig.c.release("a1")
    rig.c.check_at(top)
    rig.c.check_at(other)                               # never looked at before the resume: accepted with it


def test_a_resume_accepts_no_place_another_agent_s_run_could_have_changed(tmp_path, repo):
    """After a start with no record, resuming the agent held accepts what there is no record of only where no other
    agent's run worked since: another's change in a repository not yet looked at is held for it, not accepted."""
    top, common = repo
    other = str(tmp_path / "ws" / "repos" / "other")
    make_repo(other)
    rig = Rig(tmp_path)
    rig.working = ["a1"]
    rig.c.first_look([top, other], trusted=False)
    with pytest.raises(Held) as held:
        rig.c.check_at(top)
    assert "no other agent's run could have changed" in str(held.value)
    rig.working = ["a1", "b2"]                          # another agent's run, working since the start
    assert rig.c.release("a1") == ["your git settings"]                         # what held first, and nothing more
    with pytest.raises(Held):
        rig.c.check_at(other)


def test_a_first_start_accepts_what_is_there(tmp_path, repo):
    top, common = repo
    _plant(common)
    rig = Rig(tmp_path)
    rig.working = ["a1"]
    rig.c.first_look([top])
    rig.c.check_at(top)
    assert rig.holds == []


def test_a_git_folder_swapped_for_another_is_a_change_of_its_repository(tmp_path, repo):
    top, common = repo
    rig = Rig(tmp_path)
    rig.c.first_look([top])
    evil = tmp_path / "evil"
    make_repo(str(evil))
    os.rename(os.path.join(top, ".git"), os.path.join(top, "git-was"))
    with open(os.path.join(top, ".git"), "w") as fh:
        fh.write(f"gitdir: {evil}/.git\n")
    rig.working = ["a1"]
    with pytest.raises(Held) as held:
        rig.c.check_at(top)
    assert "repos/demo: its git folder is elsewhere" in str(held.value)
    assert rig.c.release("a1") == ["demo"]
    rig.c.check_at(top)                                 # resumed: the folder's git folder is the one it has now
    assert rig.c._tops[os.path.normpath(top)]["common"] == str((evil / ".git").resolve())


def test_a_session_s_git_file_pointed_elsewhere_holds_its_repository(tmp_path, repo):
    top, common = repo
    work = str(tmp_path / "ws" / "sessions" / "s1" / "work")
    os.makedirs(os.path.dirname(work))
    git("worktree", "add", "-q", "-b", "s1", work, cwd=top)
    rig = Rig(tmp_path)
    rig.c.first_look([top, work])
    evil = tmp_path / "evil"
    make_repo(str(evil))
    rig.working = ["a1"]
    with open(os.path.join(work, ".git"), "w") as fh:
        fh.write(f"gitdir: {evil}/.git\n")
    with pytest.raises(Held):
        rig.c.check_at(work)
    with pytest.raises(Held):
        rig.c.check_at(top)                             # the repository itself: no git of the workspace's there either


def test_a_session_s_git_file_pointed_at_another_worktree_of_its_repository_holds_it(tmp_path, repo):
    """Pointed at an agent's worktree record of the same repository, its own record taken away so that the
    repository's places show nothing: the owner's Finish there would commit on the agent's branch, and Ship push it."""
    top, common = repo
    mine = str(tmp_path / "ws" / "sessions" / "s1" / "work")
    theirs = str(tmp_path / "ws" / "agents" / "a1a2a3a4a5a6" / "work")
    for work, branch in ((mine, "s1"), (theirs, "a1")):
        os.makedirs(os.path.dirname(work), exist_ok=True)
        git("worktree", "add", "-q", "-b", branch, work, cwd=top)
    rig = Rig(tmp_path)
    rig.c.first_look([top, mine, theirs])
    before, _ = places(common)
    rig.working = ["a1"]
    record = git_folder(mine)[0]
    with open(os.path.join(mine, ".git"), "w") as fh:
        fh.write(f"gitdir: {git_folder(theirs)[0]}\n")
    shutil.rmtree(record)
    assert git_folder(mine)[1] == common and places(common)[0] == before          # nothing of the repository's says so
    with pytest.raises(Held) as held:
        rig.c.check_at(mine)
    assert "s1/work: its git folder is elsewhere" in str(held.value)


def test_a_repository_in_the_workspace_s_folders_never_accepted_is_checked_like_any(tmp_path, repo):
    top, common = repo
    rig = Rig(tmp_path)
    rig.c.first_look([top])
    other = str(tmp_path / "ws" / "repos" / "made-by-a-run")
    make_repo(other)
    rig.working = ["a1"]
    with pytest.raises(Held) as held:
        rig.c.check_at(other)
    assert "never accepted" in str(held.value)
    outside = str(tmp_path / "elsewhere")
    make_repo(outside)
    rig.c.check_at(outside)                             # one outside the workspace's folders is not the record's


def test_the_settings_outside_the_repositories_hold_every_git(tmp_path, repo, home):
    top, common = repo
    rig = Rig(tmp_path)
    rig.c.first_look([top])
    rig.working = ["a1"]
    (home / ".gitconfig").write_text("[core]\n\tfsmonitor = /tmp/x\n")
    with pytest.raises(Held) as held:
        rig.c.check_at(top)
    assert str(held.value).startswith("Your git settings outside the repositories changed while an agent's run "
                                      "worked (config core.fsmonitor)")
    with pytest.raises(Held):
        rig.c.check_at(None)                            # no repository at all: held all the same
    assert rig.c.release("a1") == ["your git settings"]
    rig.c.check_at(top)


def test_a_change_the_workspace_knows_for_harmless_is_accepted(tmp_path, repo, home):
    top, common = repo
    seen = []

    def good(c, changes, found):
        seen.append((c, changes))
        return c == USER and changes == ["config credential.https://github.com.helper"]
    rig = Rig(tmp_path, good=good)
    rig.c.first_look([top])
    rig.working = ["a1"]
    (home / ".gitconfig").write_text('[credential "https://github.com"]\n\thelper =\n'
                                     '\thelper = !gh auth git-credential\n')
    rig.c.check_at(top)
    assert rig.holds == [] and seen == [(USER, ["config credential.https://github.com.helper"])]


def test_another_reading_s_record_is_read_again_from_the_places(tmp_path, repo):
    top, common = repo
    rig = Rig(tmp_path)
    rig.c.first_look([top])
    _plant(common)
    with open(os.path.join(rig.root, gitplaces.RECORD)) as fh:
        assert json.load(fh)["basis"]["version"] == gitplaces.VERSION
    again = Rig(tmp_path)
    again.c._basis = dict(again.c._basis, version=gitplaces.VERSION + 1)        # as a later version would read it
    again.c._accepted = {}
    again.c._load()
    assert again.c.fresh
    again.working = ["a1"]
    again.c.first_look([top])
    again.c.check_at(top)
    assert again.holds == []


def test_a_look_at_a_run_s_end_names_what_was_held_for_its_agent(tmp_path, repo):
    top, common = repo
    rig = Rig(tmp_path)
    other = str(tmp_path / "ws" / "repos" / "other")
    make_repo(other)
    other_common = git_folder(other)[1]
    rig.c.first_look([top, other])
    rig.working = ["a1"]
    _plant(other_common, "pre-push")
    assert rig.c.look("a1") == [(other_common, "other", ["hooks/pre-push"])]
    assert rig.c.look("b2") == []                       # held, but not for b2's run: b2 was not working


def test_a_look_at_a_run_s_end_checks_the_folders_it_is_given_that_the_record_does_not_know(tmp_path, repo):
    # a repository a run made is in no record; the look at the
    # run's end checks it when the workspace names it among every folder git runs in, holding it for the run's agent
    # while the run is a suspect, and the record keeps it so
    top, common = repo
    rig = Rig(tmp_path)
    rig.c.first_look([top])
    rig.working = ["a1"]
    made = str(tmp_path / "ws" / "repos" / "made")
    make_repo(made)
    made_common = git_folder(made)[1]
    assert rig.c.look("a1") == []                       # the control: the record's own folders alone, as before
    assert rig.c.look("a1", [made, top]) == [(made_common, "made", [gitplaces.NEVER])]
    assert rig.c.look("a1", [made]) == [(made_common, "made", [gitplaces.NEVER])]      # held: named again
    assert [h[2] for h in rig.holds] == [["a1"]]
    assert Rig(tmp_path).c.held()[made_common]["agents"] == ["a1"]


def test_a_hold_is_reported_to_each_agent_once_by_its_end_s_look_or_after_it(tmp_path, repo):
    # a hold the look at a run's end names goes to the app with that end's record, and is marked reported once that
    # record is written, not by the look (a stop between the two would leave it marked and carried by no record); one
    # the look does not name (found by another git, after the end) is unreported until a record of the agent's runs
    # carries it (agents._report_late); the record keeps who it was reported to across a start
    top, common = repo
    rig = Rig(tmp_path)
    other = str(tmp_path / "ws" / "repos" / "other")
    make_repo(other)
    other_common = git_folder(other)[1]
    rig.c.first_look([top, other])
    rig.working = ["a1", "b2"]
    _plant(other_common, "pre-push")
    with pytest.raises(Held):
        rig.c.check_at(other)
    held = [(other_common, "other", ["hooks/pre-push"])]
    assert rig.c.unreported("a1") == rig.c.unreported("b2") == held and rig.c.unreported("c3") == []
    assert rig.c.look("a1") == held                     # a1's run ends: its look names it
    assert rig.c.unreported("a1") == held               # not reported until its end's record is written
    assert Rig(tmp_path).c.unreported("a1") == held     # nor after a stop before that record
    rig.c.reported("a1", [other_common])                # its end's record is written: it carries it
    assert rig.c.unreported("a1") == [] and rig.c.unreported("b2") == held
    rig.c.reported("b2", [other_common, "/no/such/place"])   # b2's latest run's record carries it; a place not held
    assert rig.c.unreported("b2") == []                      # is no hold to mark
    again = Rig(tmp_path)
    assert again.c.unreported("a1") == again.c.unreported("b2") == []
    _plant(common, "pre-commit")
    with pytest.raises(Held):
        rig.c.check_at(top)
    assert rig.c.unreported("a1") == [(common, "demo", ["hooks/pre-commit"])]          # a new hold: unreported again
    # every place held for it now, reported or not, which a late hold's words name
    assert rig.c.holding("a1") == sorted([(common, "demo", ["hooks/pre-commit"]), held[0]])
    assert rig.c.holding("c3") == []
    # both read under one hold of the record's lock, as a report reads them
    assert rig.c.for_report("a1") == ([(common, "demo", ["hooks/pre-commit"])], rig.c.holding("a1"))
    assert rig.c.for_report("c3") == ([], [])


def test_the_hold_callback_runs_on_a_thread_of_its_own_holding_no_lock_of_the_record(tmp_path, repo):
    # agents.hold_for_places takes the agents' _late_lock, then this record's lock. The
    # record calls it on a thread it starts for it, never the thread that decided the hold under its lock: the check
    # that decided goes on while the callback waits (as it waits for _late_lock while a report holds it), and this
    # record's lock is free meanwhile, for the report that holds _late_lock to take
    top, common = repo
    rig = Rig(tmp_path)
    rig.c.first_look([top])
    rig.working = ["a1"]
    seen, go, done = [], threading.Event(), threading.Event()

    def hold(name, changes, agents, common_):
        seen.append((threading.current_thread() is decider, rig.c._lock._is_owned()))
        go.wait(10)                                     # as a report holding _late_lock makes it wait
        seen.append(rig.c.unreported("a1"))             # this record's lock, taken on its own thread
        done.set()
    rig.c._hold = hold
    decider = threading.current_thread()
    _plant(common, "pre-push")
    with pytest.raises(Held):
        rig.c.check_at(top)                             # returns while the callback still waits
    assert not done.is_set() and seen == [(False, False)]
    assert rig.c.unreported("a1") == [(common, "demo", ["hooks/pre-push"])]     # the lock is free meanwhile
    go.set()
    assert done.wait(10) and seen[1] == [(common, "demo", ["hooks/pre-push"])]


# ---- reading again only when a file read changed ---------------------------------------------------------------------

def test_the_places_are_read_again_only_when_a_file_read_changed(tmp_path, repo, monkeypatch):
    top, common = repo
    rig = Rig(tmp_path)
    rig.c.first_look([top])
    monkeypatch.setattr(gitplaces, "_racy", lambda sig, since: False)
    reads = []
    real = gitplaces.places
    monkeypatch.setattr(gitplaces, "places", lambda c, *a: reads.append(c) or real(c, *a))
    rig.c.check(common)
    rig.c.check(common)
    rig.c.check(common)
    assert reads == [common]
    with open(os.path.join(common, "config"), "a") as fh:
        fh.write("[user]\n\tname = Ana\n")              # quiet, but a file read changed: read again, and accepted
    rig.c.check(common)
    assert reads == [common, common]


def test_a_read_too_near_a_change_is_not_kept_so_a_change_in_the_same_tick_is_found(tmp_path, repo, monkeypatch):
    """Where a file's times move by the tick (a coarse clock), a change just after a read can leave every stat as it
    was: a read that near a change is not kept as matched, and the next check reads the places again."""
    top, common = repo
    rig = Rig(tmp_path)
    rig.c.first_look([top])
    monkeypatch.setattr(gitplaces, "signature", lambda paths: ("stats that did not move",))
    monkeypatch.setattr(gitplaces, "_racy", lambda sig, since: True)
    rig.c.check(common)
    rig.working = ["a1"]
    _plant(common)
    with pytest.raises(Held):
        rig.c.check(common)


def test_a_read_too_near_a_change_is_not_taken_as_matched_next_time(tmp_path, repo):
    top, common = repo
    _, paths = places(common)
    path = os.path.join(common, "config")
    with open(path, "a") as fh:
        fh.write("\n")
    assert gitplaces._racy(gitplaces.signature([path]), time.time())
    assert not gitplaces._racy(gitplaces.signature([path]), time.time() + 2 * gitplaces.RACY)


def test_a_record_in_memory_alone_writes_no_file(tmp_path, repo):
    top, common = repo
    rig = Rig(tmp_path, root=False)
    rig.c.first_look([top])
    assert not os.path.exists(os.path.join(rig.root, gitplaces.RECORD))
