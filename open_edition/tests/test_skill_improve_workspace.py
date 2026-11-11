"""The improve loop's proposals for a business skill, the workspace's half (proofpane_workspace/skill_improve.py),
with real git in temporary repositories: the guards and the object check, the record of proposals and the app's word
on it, and the bar on starts. No real claude, codex, grok or qwen is started, and no hosting service is reached."""

import asyncio
import base64
import json
import os
import shutil
import subprocess
import time
import zlib

import pytest

from proofpane_workspace import shapes
from proofpane_workspace import skill_improve as si

# these tests are of the wait for the app's word itself (conftest.the_apps_word)
WAITS_FOR_THE_APP = True
PATH = ".claude/skills/release-notes/SKILL.md"
CURRENT = "---\nname: release-notes\ndescription: Write the release notes for a version.\n---\nWrite the notes.\n"
PROPOSED = ("---\nname: release-notes\ndescription: Write the release notes for a version, one line a change.\n---\n"
            "Write the notes.\n")


def git(*args, cwd, env=None, check=True):
    # the tests' own git reads no settings of the machine's, its environment's GIT_CONFIG_ pairs among them (a container
    # may rewrite github.com's addresses there), as the workspace's git does not (program_env passes none of them)
    full = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
            and k not in ("GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS")}
    full.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, **(env or {}))
    p = subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "-c",
                        "init.defaultBranch=main", *args], cwd=cwd, env=full, capture_output=True, text=True)
    if check and p.returncode:
        raise AssertionError(f"git {args}: {p.stderr}")
    return p.stdout.strip()


def write(repo, path, text):
    full = os.path.join(repo, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def commit_all(repo, message):
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", message, cwd=repo)
    return git("rev-parse", "HEAD", cwd=repo)


def blob_id(text, fmt="sha1"):
    return shapes.object_id("blob", text.encode("utf-8"), fmt)


@pytest.fixture(autouse=True)
def own_home(tmp_path, monkeypatch):
    """A home of the test's own: the loop's sweep at each start reads the accounts' Claude Code folders."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    return home


class _TaskRuns:
    def __init__(self):
        self.path = None

    def claude(self):
        return self.path


class _Agents:
    def __init__(self):
        self.task_runs = _TaskRuns()


class _Ws:
    """The workspace's place in these tests: its root, its records key, its repositories' folder and their listing
    (a repository whose folder is a link is one added from the owner's machine), and the claudes it would start."""

    def __init__(self, root, key=b"k" * 32):
        self.root = str(root)
        self.records_key = key
        self.repos_dir = os.path.join(self.root, "repos")
        os.makedirs(self.repos_dir, exist_ok=True)
        self.claude_path = None
        self.agents = _Agents()
        from proofpane_workspace import accounts as accounting
        self.accounts = accounting.Accounts(self.root)

    def _repo_path(self, name):
        return os.path.join(self.repos_dir, name)

    def repos(self):
        out = []
        for name in sorted(os.listdir(self.repos_dir)):
            path = os.path.join(self.repos_dir, name)
            out.append({"name": name, "linked": os.path.realpath(path) if os.path.islink(path) else None})
        return out

    def _claude(self):
        return self.claude_path


@pytest.fixture
def made(tmp_path):
    """A workspace with one repository, `demo`, whose main holds a business skill: (workspace, its SkillImprove, the
    repository's folder, the base commit)."""
    ws = _Ws(tmp_path / "ws")
    repo = ws._repo_path("demo")
    os.makedirs(repo)
    git("init", "-q", "-b", "main", cwd=repo)
    write(repo, "README.md", "demo\n")
    write(repo, PATH, CURRENT)
    base = commit_all(repo, "first")
    # what a check found of an object is kept for the process's life; two tests' commits made in one second share ids
    si.forget_clean()
    si._VERIFIED.clear()
    return ws, si.SkillImprove(ws), repo, base


def proposal(blob, commits, base, pid="a" * 12, fresh=True, kind="change", ended=None, path=PATH):
    return {"id": pid, "kind": kind, "path": path, "branch": "refs/heads/main", "bases": [base], "commits": commits,
            "blob": blob, "fresh": fresh, "state": "open", "ended": ended}


def record_it(improve, repo_name, *proposals):
    improve.proposals.change(lambda r: r["repos"].setdefault(repo_name, []).extend(proposals))


def proposal_commit(repo, base, text=PROPOSED, branch="proposal"):
    """A commit on `base` changing the skill to `text`, on its own branch, made with plain git as a session could."""
    git("checkout", "-q", "-b", branch, base, cwd=repo)
    write(repo, PATH, text)
    c = commit_all(repo, "proposal")
    git("checkout", "-q", "main", cwd=repo)
    return c


def loose_path(repo, oid):
    return os.path.join(repo, ".git", "objects", oid[:2], oid[2:])


def rewrite_loose(repo, oid, kind, data):
    """The loose object file of `oid` rewritten to hold another object's bytes, as a session's program could."""
    path = loose_path(repo, oid)
    os.chmod(path, 0o644)
    with open(path, "wb") as fh:
        fh.write(zlib.compress(f"{kind} {len(data)}\0".encode() + data))


# ---- the guards and the object check ---------------------------------------------------------------------------------

def test_a_one_with_an_error_is_cannot_tell():
    assert si._yes_no(0, "") is True
    assert si._yes_no(1, "") is False
    with pytest.raises(si.CannotTell):
        si._yes_no(1, "error: Could not read 0123")
    with pytest.raises(si.CannotTell):
        si._yes_no(128, "")


def test_read_object_refuses_a_rewritten_loose_blob(made):
    ws, improve, repo, base = made
    blob = blob_id(CURRENT)
    assert si.read_object(repo, blob) == ("blob", CURRENT.encode())
    rewrite_loose(repo, blob, "blob", PROPOSED.encode())
    # plain git gives the rewritten bytes under the id; the check refuses them
    assert git("cat-file", "-p", blob, cwd=repo) == PROPOSED.rstrip("\n")
    with pytest.raises(si.CannotTell, match="does not hash"):
        si.read_object(repo, blob)


def test_read_object_reads_no_replace_ref(made):
    ws, improve, repo, base = made
    blob = blob_id(CURRENT)
    p = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo, input=PROPOSED.encode(),
                       capture_output=True, check=True)
    other = p.stdout.decode().strip()
    git("replace", "-f", blob, other, cwd=repo)
    assert git("cat-file", "-p", blob, cwd=repo) == PROPOSED.rstrip("\n")
    assert si.read_object(repo, blob) == ("blob", CURRENT.encode())


def test_a_sha256_repository_hashes_with_sha256(tmp_path):
    repo = str(tmp_path / "r256")
    os.makedirs(repo)
    git("init", "-q", "--object-format=sha256", cwd=repo)
    write(repo, PATH, CURRENT)
    commit_all(repo, "first")
    assert si.object_format(repo) == "sha256"
    blob = blob_id(CURRENT, "sha256")
    assert len(blob) == 64
    assert si.read_object(repo, blob) == ("blob", CURRENT.encode())


def test_the_hook_placed_in_the_repository_does_not_run_for_the_loops_git(made):
    ws, improve, repo, base = made
    ran = os.path.join(str(ws.root), "ran")
    hooks = os.path.join(repo, ".git", "hooks")
    with open(os.path.join(hooks, "reference-transaction"), "w") as fh:
        fh.write(f"#!/bin/sh\ntouch {ran}\n")
    os.chmod(os.path.join(hooks, "reference-transaction"), 0o755)
    code, out, err = si._git("update-ref", "refs/heads/x", base, cwd=repo)
    assert code == 0
    assert not os.path.exists(ran)


# ---- the record of proposals, and the app's word ---------------------------------------------------------------------

def test_before_the_apps_word_every_start_is_waiting(made):
    ws, improve, repo, base = made
    assert improve.check_start("demo", base) == si.WAITING_FOR_APP
    assert improve.check_start("elsewhere", base) == si.WAITING_FOR_APP
    assert improve.proposals.refused[-1]["why"] == si.WAITING_FOR_APP


def test_never_written_runs_as_today(made):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    assert improve.check_start("demo", base) is None


def test_a_start_in_a_repository_the_record_does_not_name_runs(made):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    c = proposal_commit(repo, base)
    record_it(improve, "other", proposal(blob_id(PROPOSED), [c], base))
    assert improve.check_start("demo", c) is None


def test_the_record_is_signed_and_written_whole(made):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base))
    st = os.lstat(improve.proposals.path)
    assert oct(st.st_mode & 0o777) == "0o600"
    with open(improve.proposals.path) as fh:
        got = json.load(fh)
    assert set(got) == {"record", "mac"}
    assert got["record"]["generation"] == 1
    data, record = improve.proposals.read_file()
    assert record["repos"]["demo"][0]["blob"] == blob_id(PROPOSED)


def test_a_record_removed_is_held(made):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base))
    os.unlink(improve.proposals.path)
    assert improve.check_start("demo", base) == si.RECORD_HELD
    # in every repository, until the owner restores it
    assert improve.check_start("other", base) == si.RECORD_HELD


def test_a_record_spoiled_is_held(made):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base))
    with open(improve.proposals.path) as fh:
        got = json.load(fh)
    got["record"]["repos"] = {}
    with open(improve.proposals.path, "w") as fh:
        json.dump(got, fh)
    assert improve.check_start("other", base) == si.RECORD_HELD


def test_a_record_put_back_below_a_generation_seen_is_held(made):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    improve.proposals.change(lambda r: r["switches"].update(demo={"on": True}))
    with open(improve.proposals.path, "rb") as fh:
        older = fh.read()
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base))
    with open(improve.proposals.path, "wb") as fh:
        fh.write(older)
    # its signature holds, and the workspace's memory of the generation it wrote holds it
    assert improve.proposals.read_file()[1] is not None
    assert improve.check_start("demo", base) == si.RECORD_HELD


def test_a_record_put_back_across_a_restart_below_the_apps_copy_is_held(made):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    improve.proposals.change(lambda r: r["switches"].update(demo={"on": True}))
    with open(improve.proposals.path, "rb") as fh:
        older = fh.read()
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base))
    app_generation = improve.proposals.read_file()[1]["generation"]
    with open(improve.proposals.path, "wb") as fh:
        fh.write(older)
    again = si.SkillImprove(ws)                     # the workspace started again
    assert again.check_start("demo", base) == si.WAITING_FOR_APP
    again.proposals.take_word(app_generation)
    assert again.check_start("demo", base) == si.RECORD_HELD


def test_restore_from_the_apps_copy(made):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    c = proposal_commit(repo, base)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [c], base))
    copy_b64 = base64.b64encode(improve.proposals.read_file()[0]).decode()
    os.unlink(improve.proposals.path)
    assert improve.check_start("demo", base) == si.RECORD_HELD
    got = improve.proposals.restore(copy_b64)
    assert got["generation"] == 2
    assert improve.check_start("demo", base) is None
    assert improve.check_start("demo", c).startswith("a proposed change to")


def test_restore_after_the_records_key_changed(made):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base))
    copy_b64 = base64.b64encode(improve.proposals.read_file()[0]).decode()
    ws.records_key = b"n" * 32                      # a workspace machine replaced with a new secret
    again = si.SkillImprove(ws)
    again.proposals.take_word(1)
    assert again.check_start("demo", base) == si.RECORD_HELD
    again.proposals.restore(copy_b64)
    assert again.check_start("demo", base) is None
    assert again.proposals.read_file()[1]["repos"]["demo"][0]["blob"] == blob_id(PROPOSED)


def test_the_view_says_whether_the_signature_holds(made):
    ws, improve, repo, base = made
    view = improve.proposals.view()
    assert view["word"] is False and view["file"] is None
    improve.proposals.take_word(0)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base))
    view = improve.proposals.view()
    assert view["holds"] is True and view["generation"] == 1 and view["word"] is True
    with open(improve.proposals.path, "a") as fh:
        fh.write(" ")
    view = improve.proposals.view()
    assert view["holds"] is True            # a trailing space is still the same JSON, signed as it was
    with open(improve.proposals.path, "w") as fh:
        fh.write("{}")
    view = improve.proposals.view()
    assert view["holds"] is False and view["state"] == "held"


def test_the_refused_starts_are_kept_to_fifty(made):
    ws, improve, repo, base = made
    for _ in range(si.REFUSED_KEPT + 7):
        improve.check_start("demo", base, kind="agent")
    assert len(improve.proposals.refused) == si.REFUSED_KEPT
    assert improve.proposals.refused[0] == dict(improve.proposals.refused[0], kind="agent", repo="demo")


def test_a_start_waits_for_the_word_and_goes_on_when_it_comes(made):
    ws, improve, repo, base = made

    async def run():
        loop = asyncio.get_running_loop()
        loop.call_later(0.2, improve.proposals.take_word, 0)
        began = time.monotonic()
        came = await improve.word(5)
        return came, time.monotonic() - began

    came, took = asyncio.run(run())
    assert came is True and took < 3
    assert improve.check_start("demo", base) is None


def test_a_start_is_refused_where_the_word_does_not_come(made):
    ws, improve, repo, base = made

    async def run():
        return await improve.word(0.2)

    assert asyncio.run(run()) is False
    assert improve.check_start("demo", base) == si.WAITING_FOR_APP


def test_the_wait_holds_no_thread_and_the_word_reaches_it_from_a_thread(made):
    ws, improve, repo, base = made

    async def run():
        waits = [asyncio.create_task(improve.word(5)) for _ in range(40)]
        await asyncio.sleep(0.1)
        # the route's own work runs in the pool every wait would otherwise hold
        await asyncio.to_thread(improve.proposals.take_word, 0)
        return await asyncio.gather(*waits)

    assert all(asyncio.run(run()))


# ---- the check -------------------------------------------------------------------------------------------------------

def test_a_base_the_proposals_commit_is_reachable_from_is_refused(made):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    c = proposal_commit(repo, base)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [c], base))
    assert improve.check_start("demo", base) is None
    assert improve.check_start("demo", c) == si.BARRED.format(path=PATH, id="a" * 12)
    # a later commit on it, the skill changed back: its commit is still an ancestor
    git("checkout", "-q", "proposal", cwd=repo)
    write(repo, PATH, CURRENT)
    later = commit_all(repo, "back")
    git("checkout", "-q", "main", cwd=repo)
    assert improve.check_start("demo", later).startswith("a proposed change to")
    # a merge of it into main
    git("merge", "-q", "--no-ff", "-m", "merge", "proposal", cwd=repo)
    assert improve.check_start("demo", "main").startswith("a proposed change to")


def test_a_base_holding_its_blob_by_a_cherry_pick_is_refused(made):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    c = proposal_commit(repo, base)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [c], base))
    write(repo, PATH, PROPOSED)
    picked = commit_all(repo, "the same text, another commit")
    assert improve.check_start("demo", picked).startswith("a proposed change to")


def test_a_proposal_to_go_back_leaves_its_versions_bases_open(made):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    c = proposal_commit(repo, base, text=PROPOSED)
    # a proposal to go back holds a version the branch held: not fresh, it bars by its commit alone
    record_it(improve, "demo", proposal(blob_id(CURRENT), [c], base, kind="back", fresh=False))
    assert improve.check_start("demo", base) is None
    write(repo, "OTHER.md", "x\n")
    holding = commit_all(repo, "another holding the version gone back to")
    assert improve.check_start("demo", holding) is None
    assert improve.check_start("demo", c).startswith("a proposed change to")


def test_a_rejected_and_an_expired_proposal_still_bar(made):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    c = proposal_commit(repo, base)
    p = proposal(blob_id(PROPOSED), [c], base)
    p["state"] = "rejected"
    q = proposal(blob_id(PROPOSED.replace("one line", "a line")), [], base, pid="b" * 12)
    q["state"] = "expired"
    record_it(improve, "demo", p, q)
    assert improve.check_start("demo", c).startswith("a proposed change to")


def test_a_proposal_whose_bar_ended_bars_nothing(made):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    c = proposal_commit(repo, base)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [c], base,
                                        ended={"how": "merged", "at": int(time.time())}))
    assert improve.check_start("demo", c) is None


def test_replace_refs_and_a_graft_change_nothing_the_walk_reads(made):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    c = proposal_commit(repo, base)
    git("checkout", "-q", "proposal", cwd=repo)
    write(repo, "LATER.md", "later\n")
    later = commit_all(repo, "later")
    git("checkout", "-q", "main", cwd=repo)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [c], base))
    # a replace ref on the later commit naming the base, and a graft giving it the base for its parent: the walk reads
    # the commits as their objects hold them
    git("replace", "-f", later, base, cwd=repo)
    with open(os.path.join(repo, ".git", "info", "grafts"), "w") as fh:
        fh.write(f"{later} {base}\n")
    with open(os.path.join(repo, ".git", "shallow"), "w") as fh:
        fh.write(f"{later}\n")
    assert improve.check_start("demo", later).startswith("a proposed change to")


def test_a_replace_ref_on_the_blob_and_on_the_tree_change_nothing_step_two_reads(made):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base))
    p = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo, input=PROPOSED.encode(),
                       capture_output=True, check=True)
    # the base's blob replaced by the proposal's: a checkout with replace refs read would write the proposal's text
    git("replace", "-f", blob_id(CURRENT), p.stdout.decode().strip(), cwd=repo)
    assert git("show", f"{base}:{PATH}", cwd=repo) == PROPOSED.rstrip("\n")
    assert improve.check_start("demo", base) is None
    # and the folder's tree replaced by one holding the proposal's blob
    git("replace", "-d", blob_id(CURRENT), cwd=repo)
    folder = git("rev-parse", f"{base}:.claude/skills/release-notes", cwd=repo)
    made_tree = subprocess.run(["git", "mktree"], cwd=repo, capture_output=True, check=True,
                               input=f"100644 blob {blob_id(PROPOSED)}\tSKILL.md\n".encode()).stdout.decode().strip()
    git("replace", "-f", folder, made_tree, cwd=repo)
    assert git("show", f"{base}:{PATH}", cwd=repo) == PROPOSED.rstrip("\n")
    assert improve.check_start("demo", base) is None


def test_a_rewritten_loose_object_of_the_bases_skill_is_refused_before_the_write(made):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base))
    rewrite_loose(repo, blob_id(CURRENT), "blob", PROPOSED.encode())
    got = improve.check_start("demo", base)
    assert got.startswith("the workspace could not tell") and "does not hash" in got


def test_a_git_that_cannot_tell_refuses(made):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    c = proposal_commit(repo, base)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [c], base))
    assert improve.check_start("demo", "no-such-branch").startswith("the workspace could not tell")
    # a commit whose parent's object is gone
    write(repo, "X.md", "x\n")
    top = commit_all(repo, "on main")
    os.unlink(loose_path(repo, base))
    assert improve.check_start("demo", top).startswith("the workspace could not tell")


def test_the_walk_is_kept_clean_and_forgotten_when_the_record_gains_a_commit(made):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    c = proposal_commit(repo, base)
    # a proposal not fresh and with no commit yet bars nothing, but the walk of `c` is made and kept clean
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base, fresh=False))
    assert improve.check_start("demo", c) is None
    assert c in si._CLEAN[frozenset()] and base in si._CLEAN[frozenset()]       # clean against no barred commit
    improve.proposals.change(lambda r: r["repos"]["demo"][0]["commits"].append(c))
    assert not si._CLEAN
    assert improve.check_start("demo", c).startswith("a proposed change to")


# ---- step 3: the files a start's worktree holds ------------------------------------------------------------------------

def worktree(tmp_path, name="wt"):
    work = str(tmp_path / name)
    os.makedirs(work)
    return work


def test_a_worktree_holding_a_proposals_text_is_refused(made, tmp_path):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base))
    work = worktree(tmp_path)
    write(work, PATH, PROPOSED)
    assert improve.check_start("demo", base, work=work).startswith("a proposed change to")
    write(work, PATH, CURRENT)
    assert improve.check_start("demo", base, work=work) is None


def test_a_skills_folder_linked_to_the_owners_skill_runs(made, tmp_path):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base))
    work = worktree(tmp_path)
    os.symlink(os.path.join(repo, ".claude"), os.path.join(work, ".claude"))
    assert improve.check_start("demo", base, work=work) is None
    os.unlink(os.path.join(work, ".claude"))
    os.makedirs(os.path.join(work, ".claude"))
    os.symlink(os.path.join(repo, ".claude", "skills"), os.path.join(work, ".claude", "skills"))
    assert improve.check_start("demo", base, work=work) is None


def test_a_skill_md_linked_to_a_proposals_text_is_refused(made, tmp_path):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base))
    work = worktree(tmp_path)
    elsewhere = str(tmp_path / "elsewhere.md")
    with open(elsewhere, "w") as fh:
        fh.write(PROPOSED)
    os.makedirs(os.path.join(work, os.path.dirname(PATH)))
    os.symlink(elsewhere, os.path.join(work, PATH))
    assert improve.check_start("demo", base, work=work).startswith("a proposed change to")


def test_a_link_that_leads_nowhere_runs(made, tmp_path):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base))
    work = worktree(tmp_path)
    os.symlink(str(tmp_path / "nowhere"), os.path.join(work, ".claude"))
    assert improve.check_start("demo", base, work=work) is None


def test_a_skill_md_that_is_a_pipe_is_refused_without_waiting(made, tmp_path):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base))
    work = worktree(tmp_path)
    os.makedirs(os.path.join(work, os.path.dirname(PATH)))
    os.mkfifo(os.path.join(work, PATH))
    began = time.monotonic()
    got = improve.check_start("demo", base, work=work)
    assert time.monotonic() - began < 5
    assert got.startswith("the workspace could not tell") and "not a file" in got


def test_a_base_without_the_folder_runs(made, tmp_path):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base))
    git("rm", "-q", "-r", ".claude", cwd=repo)
    without = commit_all(repo, "no skills")
    work = worktree(tmp_path)
    assert improve.check_start("demo", without, work=work) is None


def test_each_fresh_proposals_path_is_read_in_the_worktree(made, tmp_path):
    # step 3 reads the file at each fresh proposal's path, not the first
    # alone: a second fresh proposal, at another skill's path, whose text is on disk there, refuses the start
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    other_path = ".claude/skills/triage/SKILL.md"
    other_text = "---\nname: triage\ndescription: Label the new issues.\n---\nLabel them.\n"
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base),
              proposal(blob_id(other_text), [], base, pid="c" * 12, path=other_path))
    work = worktree(tmp_path)
    write(work, PATH, CURRENT)
    write(work, other_path, other_text)
    assert improve.check_start("demo", base, work=work) == si.BARRED.format(path=other_path, id="c" * 12)


def test_a_skill_md_that_cannot_be_opened_is_refused(made, tmp_path):
    # a file at a fresh proposal's path that cannot be opened (a link to
    # itself, which the system will not follow) refuses the start, as the manual says; only a name missing, a link
    # that leads nowhere or a name that is not a folder where one is needed reads as nothing there
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base))
    work = worktree(tmp_path)
    os.makedirs(os.path.join(work, os.path.dirname(PATH)))
    os.symlink("SKILL.md", os.path.join(work, PATH))
    got = improve.check_start("demo", base, work=work)
    assert got.startswith("the workspace could not tell") and "could not be opened" in got


# ---- the reads at the base -------------------------------------------------------------------------------------------

OTHER = "---\nname: quick-notes\ndescription: Write short notes.\n---\nShort.\n"


def switch_on(improve, repo_name, url=None, branch="refs/heads/main"):
    improve.proposals.change(lambda r: r["switches"].update({repo_name: {"on": True, "account": None, "model": None,
                                                                         "url": url, "branch": branch}}))


@pytest.fixture
def linked(tmp_path):
    """A repository added from the owner's machine: its folder elsewhere, a link to it in the workspace's
    repositories, its switch on and the app's word had."""
    ws = _Ws(tmp_path / "ws")
    own = str(tmp_path / "own")
    os.makedirs(own)
    git("init", "-q", "-b", "main", cwd=own)
    write(own, "README.md", "demo\n")
    write(own, PATH, CURRENT)
    base = commit_all(own, "first")
    os.symlink(own, ws._repo_path("demo"))
    si.forget_clean()
    si._VERIFIED.clear()
    improve = si.SkillImprove(ws)
    improve.proposals.take_word(0)
    switch_on(improve, "demo")
    return ws, improve, own, base


def candidate_folders(improve, repo="demo"):
    got = improve.candidates(repo)
    return {c["folder"] for c in got["candidates"]}, got


def test_a_linked_repositorys_base_is_its_checked_out_branch(linked):
    ws, improve, own, base = linked
    found, got = candidate_folders(improve)
    assert got["ok"] is True, got
    assert got["base"] == {"repo": "demo", "linked": True, "branch": "refs/heads/main", "head": base, "url": None,
                           "object_format": "sha1"}
    assert found == {"release-notes"}
    cand = got["candidates"][0]
    assert cand["blob"] == blob_id(CURRENT) and cand["mode"] == "100644" and cand["names"] == ["release-notes"]
    assert cand["current_version"] == base and cand["before_version"] is None


def test_nothing_is_read_with_the_switch_off(linked):
    ws, improve, own, base = linked
    improve.proposals.change(lambda r: r["switches"].pop("demo"))
    got = improve.candidates("demo")
    assert got == {"repo": "demo", "ok": False, "why": si.SWITCH_OFF, "candidates": [], "not": {}}


def test_a_head_on_no_branch_gets_no_proposal(linked):
    ws, improve, own, base = linked
    git("checkout", "-q", "--detach", cwd=own)
    assert improve.candidates("demo")["why"] == si.NO_BRANCH


def test_a_shallow_and_a_partial_repository_get_no_proposal(linked):
    ws, improve, own, base = linked
    with open(os.path.join(own, ".git", "shallow"), "w") as fh:
        fh.write(base + "\n")
    assert improve.candidates("demo")["why"] == si.SHALLOW
    os.unlink(os.path.join(own, ".git", "shallow"))
    git("config", "extensions.partialClone", "origin", cwd=own)
    assert improve.candidates("demo")["why"] == si.PARTIAL


def test_core_and_agent_skills_and_bad_names_are_not_candidates(linked):
    ws, improve, own, base = linked
    write(own, ".claude/skills/pp-agent/SKILL.md", CURRENT)
    write(own, ".claude/skills/Upper/SKILL.md", CURRENT)
    write(own, ".claude/skills/forked/SKILL.md", CURRENT.replace("---\nWrite", "context: fork\n---\nWrite"))
    write(own, ".claude/skills/agent-one/SKILL.md", CURRENT.replace("---\nWrite", "agent: helper\n---\nWrite"))
    write(own, ".claude/skills/runs/SKILL.md", CURRENT + "Run !`date` first.\n")
    commit_all(own, "more skills")
    found, got = candidate_folders(improve)
    assert found == {"release-notes"}
    assert "an agent's own skill" in got["not"]["pp-agent"]
    assert "is not a skill's" in got["not"]["Upper"]
    assert "context" in got["not"]["forked"] and "agent" in got["not"]["agent-one"]
    assert "a command run inline" in got["not"]["runs"]


def test_a_link_a_submodule_and_a_long_skill_are_not_candidates(linked, tmp_path):
    ws, improve, own, base = linked
    os.symlink(os.path.join(own, ".claude", "skills", "release-notes"), os.path.join(own, ".claude", "skills", "linked"))
    os.makedirs(os.path.join(own, ".claude", "skills", "linkfile"))
    os.symlink("../release-notes/SKILL.md", os.path.join(own, ".claude", "skills", "linkfile", "SKILL.md"))
    write(own, ".claude/skills/long/SKILL.md", CURRENT + "x" * (shapes.MAX_SKILL + 1))
    commit_all(own, "odd ones")
    found, got = candidate_folders(improve)
    assert found == {"release-notes"}
    assert got["not"]["linked"] == "linked is a link, not a folder"
    assert got["not"]["linkfile"] == "SKILL.md is a link, not a file"
    assert "longer than" in got["not"]["long"]


def test_names_folding_together_on_the_path_and_within_the_folder(linked):
    ws, improve, own, base = linked
    write(own, ".claude/skills/second/SKILL.md", CURRENT.replace("release-notes", "second"))
    write(own, ".claude/skills/second/scripts/run.txt", "a\n")
    write(own, ".claude/skills/second/Scripts/run.txt", "b\n")
    write(own, ".claude/skills/release-notes/skill.md", "lower\n")
    commit_all(own, "folding names")
    found, got = candidate_folders(improve)
    assert found == set()
    assert "folds to it" in got["not"]["release-notes"]
    assert "fold to one" in got["not"]["second"]
    git("rm", "-q", "-r", "--cached", ".claude/skills/release-notes/skill.md", cwd=own)
    os.unlink(os.path.join(own, ".claude/skills/release-notes/skill.md"))
    write(own, ".Claude/other.txt", "x\n")
    commit_all(own, "a .Claude beside .claude")
    got = improve.candidates("demo")
    assert got["ok"] is False and "beside .claude folds to it" in got["why"]


def test_eol_crlf_in_the_tree_is_not_a_candidate(linked):
    ws, improve, own, base = linked
    write(own, ".gitattributes", "*.md text eol=crlf\n")
    commit_all(own, "crlf")
    found, got = candidate_folders(improve)
    assert found == set() and "other bytes" in got["not"]["release-notes"]


def test_the_same_rule_in_info_attributes_is_not_a_candidate(linked):
    ws, improve, own, base = linked
    write(own, ".git/info/attributes", "*.md text eol=crlf\n")
    found, got = candidate_folders(improve)
    assert found == set() and "other bytes" in got["not"]["release-notes"]


def test_core_autocrlf_is_not_a_candidate(linked):
    ws, improve, own, base = linked
    git("config", "core.autocrlf", "true", cwd=own)
    found, got = candidate_folders(improve)
    assert found == set() and "other bytes" in got["not"]["release-notes"]


def test_eol_crlf_in_the_bases_attributes_and_not_the_work_trees(linked):
    ws, improve, own, base = linked
    write(own, ".gitattributes", "*.md text eol=crlf\n")
    commit_all(own, "crlf in the tree")
    # the main work tree's own .gitattributes says otherwise: a checkout-index in it would read that one
    write(own, ".gitattributes", "*.txt text\n")
    found, got = candidate_folders(improve)
    assert found == set() and "other bytes" in got["not"]["release-notes"]


def test_an_untracked_attributes_file_naming_a_filter_runs_nothing(linked, tmp_path):
    ws, improve, own, base = linked
    ran = str(tmp_path / "smudged")
    git("config", "filter.evil.smudge", f"touch {ran}; cat", cwd=own)
    git("config", "filter.evil.clean", "cat", cwd=own)
    write(own, ".gitattributes", "*.md filter=evil\n")         # untracked, in the main work tree only
    found, got = candidate_folders(improve)
    assert found == {"release-notes"}
    assert not os.path.exists(ran)


def test_a_filter_in_the_base_is_not_a_candidate_and_runs_nothing(linked, tmp_path):
    ws, improve, own, base = linked
    ran = str(tmp_path / "smudged")
    git("config", "filter.evil.smudge", f"touch {ran}; cat", cwd=own)
    git("config", "filter.evil.clean", "cat", cwd=own)
    write(own, ".gitattributes", "*.md filter=evil\n")
    commit_all(own, "a filter")
    found, got = candidate_folders(improve)
    assert found == set() and got["not"]["release-notes"] == "a filter driver applies to it"
    assert not os.path.exists(ran)


def test_working_tree_encoding_is_refused_at_the_review(linked):
    ws, improve, own, base = linked
    write(own, ".gitattributes", "*.md working-tree-encoding=ISO-8859-1\n")
    commit_all(own, "an encoding")
    found, got = candidate_folders(improve)
    assert found == set() and got["not"]["release-notes"] == "a working-tree-encoding applies to it"


def test_a_base_without_the_folder(linked):
    ws, improve, own, base = linked
    git("rm", "-q", "-r", ".claude", cwd=own)
    commit_all(own, "none")
    got = improve.candidates("demo")
    assert got["ok"] is False and got["why"] == ".claude is not there"


def test_names_map_as_a_sessions_worktree_maps_them(linked, tmp_path):
    from proofpane_workspace import activity
    ws, improve, own, base = linked
    write(own, ".claude/skills/notes2/SKILL.md", OTHER)
    write(own, ".claude/skills/zz-copy/SKILL.md", OTHER.replace("Short.", "Shorter."))
    write(own, ".claude/skills/third/SKILL.md", CURRENT.replace("Write the notes.", "Third."))
    head = commit_all(own, "names")
    with si.Objects(own, "sha1") as objects:
        tree, _ = si.commit_of(objects, head)
        skills = si.tree_of(objects, si._entry(si.tree_of(objects, si._entry(si.tree_of(objects, tree), ".claude")[2]),
                                               "skills")[2])
        mapped = si.names_at(objects, skills)
    assert mapped == {"release-notes": ["release-notes"], "notes2": ["notes2", "quick-notes"], "zz-copy": ["zz-copy"],
                      "third": ["third"]}
    on_disk = os.path.join(own, ".claude", "skills")
    for name in ("release-notes", "quick-notes", "notes2", "zz-copy", "third", "nothing"):
        folder = activity._holds(on_disk, name)
        assert (name in mapped.get(folder, [])) if folder else all(name not in v for v in mapped.values())


def test_a_skill_last_changed_through_a_merge_its_version_as_business_version_gives(linked):
    from proofpane_workspace import activity
    ws, improve, own, base = linked
    git("checkout", "-q", "-b", "side", cwd=own)
    write(own, PATH, CURRENT.replace("Write the notes.", "Write the notes well."))
    changed = commit_all(own, "the skill on a side branch")
    git("checkout", "-q", "main", cwd=own)
    write(own, "README.md", "more\n")
    commit_all(own, "main moves")
    git("merge", "-q", "--no-ff", "-m", "merge side", "side", cwd=own)
    found, got = candidate_folders(improve)
    cand = got["candidates"][0]
    assert cand["current_version"] == activity._business_version(own, "release-notes") == changed
    assert cand["before_version"] == base


def test_a_sha256_repository_is_proposed_for(tmp_path):
    ws = _Ws(tmp_path / "ws")
    own = str(tmp_path / "own")
    os.makedirs(own)
    git("init", "-q", "--object-format=sha256", "-b", "main", cwd=own)
    write(own, PATH, CURRENT)
    commit_all(own, "first")
    os.symlink(own, ws._repo_path("demo"))
    si._VERIFIED.clear()
    improve = si.SkillImprove(ws)
    improve.proposals.take_word(0)
    switch_on(improve, "demo")
    found, got = candidate_folders(improve)
    assert found == {"release-notes"} and got["base"]["object_format"] == "sha256"
    assert got["candidates"][0]["blob"] == blob_id(CURRENT, "sha256")


def test_a_newer_build_than_the_newest_read_is_noted(linked, tmp_path):
    ws, improve, own, base = linked
    for name, version in (("claude-path", "2.1.311"), ("claude-task", "2.1.274")):
        path = str(tmp_path / name)
        with open(path, "w") as fh:
            fh.write(f"#!/bin/sh\necho '{version} (Claude Code)'\n")
        os.chmod(path, 0o755)
    ws.claude_path = str(tmp_path / "claude-path")
    ws.agents.task_runs.path = str(tmp_path / "claude-task")
    builds = improve.builds("demo")
    assert [(b["version"], b["newer"]) for b in builds] == [("2.1.311", True), ("2.1.274", False)]
    assert improve.candidates("demo")["builds"] == builds


# ---- a clone's base: origin's branch as the loop's own fetch reports it ------------------------------------------------

@pytest.fixture
def cloned(tmp_path):
    """A repository cloned into the workspace from an origin of its own (a bare repository on this disk), its switch
    on with origin's address and the branch recorded."""
    ws = _Ws(tmp_path / "ws")
    seed = str(tmp_path / "seed")
    os.makedirs(seed)
    git("init", "-q", "-b", "main", cwd=seed)
    write(seed, PATH, CURRENT)
    base = commit_all(seed, "first")
    origin = str(tmp_path / "origin.git")
    git("clone", "-q", "--bare", seed, origin, cwd=str(tmp_path))
    git("clone", "-q", origin, ws._repo_path("demo"), cwd=str(tmp_path))
    si.forget_clean()
    si._VERIFIED.clear()
    improve = si.SkillImprove(ws)
    improve.proposals.take_word(0)
    switch_on(improve, "demo", url=origin)
    return ws, improve, seed, origin, base


def push_to_origin(seed, origin, text):
    write(seed, PATH, text)
    c = commit_all(seed, "moved on")
    git("push", "-q", origin, "main", cwd=seed)
    return c


def test_a_clones_base_is_origins_head_as_the_fetch_reports_it(cloned):
    ws, improve, seed, origin, base = cloned
    got = improve.candidates("demo")
    assert got["ok"] is True and got["base"]["head"] == base and got["base"]["url"] == origin
    newer = push_to_origin(seed, origin, CURRENT.replace("Write the notes.", "Write the notes now."))
    got = improve.candidates("demo")
    assert got["base"]["head"] == newer
    # already at origin's head: the fetch's `=` line
    got = improve.candidates("demo")
    assert got["base"]["head"] == newer


def test_a_clones_base_is_the_fetchs_whatever_the_refs_say(cloned):
    ws, improve, seed, origin, base = cloned
    repo = ws._repo_path("demo")
    newer = push_to_origin(seed, origin, CURRENT.replace("Write the notes.", "Write the notes now."))
    git("update-ref", "refs/remotes/origin/main", base, cwd=repo)
    with open(os.path.join(repo, ".git", "FETCH_HEAD"), "w") as fh:
        fh.write(f"{base}\t\tbranch 'main' of {origin}\n")
    git("config", "remote.origin.fetch", "+refs/heads/nothing:refs/remotes/origin/main", cwd=repo)
    assert improve.candidates("demo")["base"]["head"] == newer


def test_origins_address_changed_and_an_insteadof_refuse(cloned):
    ws, improve, seed, origin, base = cloned
    repo = ws._repo_path("demo")
    git("remote", "set-url", "origin", origin + "/", cwd=repo)
    assert improve.candidates("demo")["why"] == si.ORIGIN_CHANGED
    git("remote", "set-url", "origin", origin, cwd=repo)
    assert improve.candidates("demo")["ok"] is True
    git("config", f"url.{origin}.insteadOf", "elsewhere:", cwd=repo)
    assert improve.candidates("demo")["why"] == si.ORIGIN_CHANGED


def test_a_clone_whose_head_moved_to_another_branch_gets_no_proposal(cloned):
    ws, improve, seed, origin, base = cloned
    repo = ws._repo_path("demo")
    git("checkout", "-q", "-b", "elsewhere", cwd=repo)
    why = improve.candidates("demo")["why"]
    assert "refs/heads/elsewhere" in why and "refs/heads/main" in why
    got = improve.look("demo")                      # the look names the branch recorded with the switch
    assert got["ok"] is False and "refs/heads/main" in got["why"] and "refs/heads/elsewhere" in got["why"]


# ---- the review and the critic, the commit and its ref ---------------------------------------------------------------

COUNTS = {"whole": dict.fromkeys(shapes.SKILL_COUNTS_FIELDS, 0), "current": None, "before": None}
COUNTS["whole"].update(calls=12, failed=4)


class _Turns:
    """agents.one_turn's place: each turn's folder, account, model, text and extra arguments kept, and its reply the
    next of `replies` (a probe's is "ok"). With `persist`, a turn writes a transcript where Claude Code would."""

    def __init__(self, ws, replies, persist=False):
        self.ws, self.replies, self.persist, self.calls = ws, list(replies), persist, []

    async def __call__(self, ws, folder, account_id, model, text, extra_args=None):
        from proofpane_workspace import usage
        self.calls.append({"folder": folder, "account": account_id, "model": model, "text": text,
                           "extra_args": dict(extra_args or {}), "empty": os.listdir(folder) == []})
        if self.persist:
            where = os.path.join(os.environ["HOME"], ".claude", "projects", usage.slug(os.path.realpath(folder)))
            os.makedirs(where, exist_ok=True)
            with open(os.path.join(where, "t.jsonl"), "w") as fh:
                fh.write(text)
        if text == si.PROBE_TEXT:
            return "ok", 0.0001, "stand-in"
        return self.replies.pop(0), 0.01, "stand-in"


@pytest.fixture
def turns(linked, monkeypatch, tmp_path):
    from proofpane_workspace import agents as agentry
    ws, improve, own, base = linked
    t = _Turns(ws, [])
    monkeypatch.setattr(agentry, "one_turn", t)
    # the claude whose version the builds and the probe read: a stand-in that says 2.1.274
    claude = str(tmp_path / "claude")
    with open(claude, "w") as fh:
        fh.write("#!/bin/sh\necho '2.1.274 (Claude Code)'\n")
    os.chmod(claude, 0o755)
    ws.claude_path = claude
    return t


def review(improve, pid="b" * 12, folder="release-notes", counts=COUNTS, **extra):
    return asyncio.run(improve.review(dict({"id": pid, "repo": "demo", "folder": folder, "counts": counts}, **extra)))


def proposed_reply(text=PROPOSED, hypothesis="Say what one line holds."):
    return json.dumps({"skill_md": text, "hypothesis": hypothesis})


def test_the_review_is_given_the_skill_md_and_the_counts_alone(turns, linked):
    ws, improve, own, base = linked
    turns.replies = [proposed_reply()]
    got = review(improve)
    review_turn = [c for c in turns.calls if c["text"] != si.PROBE_TEXT]
    assert len(review_turn) == 1
    call = review_turn[0]
    assert call["text"].startswith(si.SKILL_REVIEW_PROMPT)
    assert CURRENT in call["text"] and json.dumps(COUNTS, sort_keys=True) in call["text"]
    # nothing else: no commit message, no other file, no history
    assert "README" not in call["text"] and "demo\n" not in call["text"] and base not in call["text"]
    assert call["extra_args"] == {"no-session-persistence": None}
    assert call["empty"] is True and call["folder"].endswith(os.path.join("b" * 12, "review"))
    p = got["proposal"]
    assert p["blob"] == blob_id(PROPOSED) and p["current_blob"] == blob_id(CURRENT) and p["fresh"] is True
    assert p["base"] == base and p["branch"] == "refs/heads/main" and p["proposed_text"] == PROPOSED
    # the proposal recorded, no object written
    record = improve.proposals.read_file()[1]
    assert record["repos"]["demo"][0] == {"id": "b" * 12, "kind": "change", "path": PATH, "branch": "refs/heads/main",
                                          "bases": [base], "commits": [], "blob": blob_id(PROPOSED), "fresh": True,
                                          "state": "critic", "ended": None, "git": git_identity(own)}
    clone = os.path.join(ws.root, "skill-improve", "b" * 12, "clone")
    assert subprocess.run(["git", "cat-file", "-e", blob_id(PROPOSED)], cwd=clone).returncode != 0
    assert subprocess.run(["git", "cat-file", "-e", blob_id(PROPOSED)], cwd=own).returncode != 0


def test_one_turn_is_one_turn_with_no_tool_no_settings_and_no_persistence(linked, monkeypatch, tmp_path):
    import claude_agent_sdk
    from proofpane_workspace import agents as agentry
    from proofpane_workspace import chat as chats
    ws, improve, own, base = linked
    seen = []

    async def fake_query(prompt, options):
        seen.append(options)
        return
        yield

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    monkeypatch.setattr(chats, "write_wrapper", lambda *a, **k: "/bin/true")
    folder = str(tmp_path / "turn")
    asyncio.run(agentry.one_turn(ws, folder, None, None, "hello", extra_args=si.NO_PERSISTENCE))
    o = seen[0]
    assert o.max_turns == 1 and o.setting_sources == [] and o.tools == [] and o.permission_mode == "dontAsk"
    assert set(agentry.NO_TOOLS) <= set(o.disallowed_tools)
    assert o.extra_args == {"no-session-persistence": None} and o.cwd == folder


def test_a_build_whose_probe_leaves_a_transcript_gets_no_review(turns, linked):
    ws, improve, own, base = linked
    turns.persist = True
    turns.replies = [proposed_reply()]
    with pytest.raises(si.SkillImproveError, match="left a transcript"):
        review(improve)
    assert [c["text"] for c in turns.calls] == [si.PROBE_TEXT]
    # nothing left: no review folder, no transcript of the probe's
    assert not os.path.exists(os.path.join(ws.root, "skill-improve", "b" * 12))
    projects = os.path.join(os.environ["HOME"], ".claude", "projects")
    assert not any(n.startswith("-") and "skill-improve" in n for n in os.listdir(projects))


def test_each_turns_transcript_is_removed_and_those_left_at_a_start(turns, linked):
    from proofpane_workspace import usage
    ws, improve, own, base = linked
    turns.replies = [proposed_reply()]
    review(improve)
    projects = os.path.join(os.environ["HOME"], ".claude", "projects")
    assert not os.path.exists(projects) or not any("skill-improve" in n for n in os.listdir(projects))
    # a workspace that stopped during a review's turn: its folder and the turn's transcript both left
    folder = os.path.join(ws.root, "skill-improve", "x", "review")
    os.makedirs(folder)
    left = os.path.join(projects, usage.slug(os.path.realpath(folder)))
    os.makedirs(left)
    si.SkillImprove(ws)                             # the workspace started again
    assert not os.path.exists(left)
    assert not os.path.exists(os.path.dirname(folder))
    assert not os.path.exists(os.path.join(ws.root, "skill-improve", "b" * 12))


def test_a_start_where_no_review_was_cut_short_lists_no_accounts_folder(linked, monkeypatch):
    ws, improve, own, base = linked
    projects = os.path.join(os.environ["HOME"], ".claude", "projects")
    os.makedirs(projects)
    listed, real = [], os.listdir
    # the loop's folder is listed through a descriptor of its own, any other by path
    monkeypatch.setattr(si.os, "listdir", lambda path=".": listed.append(
        "the loop's folder" if isinstance(path, int) else os.fspath(path)) or real(path))
    si.SkillImprove(ws)
    assert listed == ["the loop's folder"]                              # the loop's folder alone
    os.makedirs(os.path.join(ws.root, "skill-improve", "z"))            # a review's folder, left
    listed.clear()
    si.SkillImprove(ws)
    assert listed == ["the loop's folder", projects]


def test_a_transcript_the_forget_could_not_remove_is_looked_for_at_the_next_start(linked, monkeypatch):
    from proofpane_workspace import usage
    ws, improve, own, base = linked
    projects = os.path.join(os.environ["HOME"], ".claude", "projects")
    folder = os.path.join(ws.root, "skill-improve", "y", "review")
    left = os.path.join(projects, usage.slug(os.path.realpath(folder)))
    os.makedirs(left)
    real = shutil.rmtree
    monkeypatch.setattr(si.shutil, "rmtree", lambda path, **kw: None)              # a removal that does nothing
    assert improve._forget_transcript(None, folder) is False
    monkeypatch.setattr(si.shutil, "rmtree", real)
    marks = [n for n in os.listdir(os.path.join(ws.root, "skill-improve")) if n.startswith("transcript-left-")]
    assert len(marks) == 1 and os.path.exists(left)
    si.SkillImprove(ws)                             # the workspace started again
    assert not os.path.exists(left)
    assert not any(n.startswith("transcript-left-") for n in os.listdir(os.path.join(ws.root, "skill-improve")))
    assert improve._forget_transcript(None, folder) is True             # nothing left: no mark
    assert not any(n.startswith("transcript-left-") for n in os.listdir(os.path.join(ws.root, "skill-improve")))


def test_a_none_answer_records_nothing(turns, linked):
    ws, improve, own, base = linked
    turns.replies = [json.dumps({"none": True})]
    got = review(improve)
    assert got["proposal"] is None
    assert improve.proposals.read_file()[1]["repos"] == {}


def test_a_reply_changing_another_line_is_refused(turns, linked):
    ws, improve, own, base = linked
    turns.replies = [proposed_reply(PROPOSED.replace("name: release-notes", "name: other-notes"))]
    got = review(improve)
    assert got["proposal"] is None and "not name" in got["refused"]
    assert improve.proposals.read_file()[1]["repos"] == {}


def test_a_hypothesis_of_two_lines_or_too_long_or_a_construct_is_refused(turns, linked):
    ws, improve, own, base = linked
    for i, hypothesis in enumerate(("one\ntwo", "x" * 301, "see @notes", "ultrathink it")):
        turns.replies = [proposed_reply(hypothesis=hypothesis)]
        got = review(improve, pid=f"{i:012x}")
        assert got["proposal"] is None and "hypothesis" in got["refused"], hypothesis


def test_a_repeat_of_a_proposals_text_is_refused(turns, linked):
    ws, improve, own, base = linked
    turns.replies = [proposed_reply()]
    review(improve)
    asyncio.run(asyncio.to_thread(improve.close, "b" * 12, "rejected"))
    turns.replies = [proposed_reply()]
    got = review(improve, pid="c" * 12)
    assert got["proposal"] is None and got["repeat_of"] == "b" * 12
    assert [p["id"] for p in improve.proposals.read_file()[1]["repos"]["demo"]] == ["b" * 12]


def test_no_review_where_the_switch_is_off_or_the_version_cannot_be_read(turns, linked, tmp_path):
    ws, improve, own, base = linked
    improve.proposals.change(lambda r: r["switches"]["demo"].update(on=False))
    with pytest.raises(si.SkillImproveError, match="switch is off"):
        review(improve)
    improve.proposals.change(lambda r: r["switches"]["demo"].update(on=True))
    silent = str(tmp_path / "silent")
    with open(silent, "w") as fh:
        fh.write("#!/bin/sh\nexit 0\n")
    os.chmod(silent, 0o755)
    ws.claude_path = silent
    with pytest.raises(si.SkillImproveError, match="could not be read"):
        review(improve)
    assert turns.calls == []


def made_proposal(turns, improve, pid="b" * 12):
    turns.replies = [proposed_reply()]
    got = review(improve, pid=pid)
    return got["proposal"]


def test_a_turn_paid_for_is_counted_however_its_review_ends(turns, linked, monkeypatch):
    # the probe's turn is counted with the review it ran for; a review
    # whose reply cannot be read, or whose proposal cannot be recorded, is an answer that refuses, carrying its turn's
    # cost, never an error the app counts nothing for
    ws, improve, own, base = linked
    turns.replies = ["no answer here"]
    got = review(improve, pid="b" * 12)
    assert got["proposal"] is None and "no answer of the shape" in got["refused"]
    assert got["cost"] == pytest.approx(0.0101)                    # the review's turn and the probe's, which ran now
    turns.replies = [proposed_reply()]

    def held(self, how):
        raise si.SkillImproveError(si.RECORD_HELD)

    monkeypatch.setattr(si.SkillProposals, "change", held)
    got = review(improve, pid="c" * 12)
    assert got["proposal"] is None and got["refused"] == f"its proposal could not be recorded: {si.RECORD_HELD}"
    assert got["cost"] == pytest.approx(0.01)                      # the probe's answer kept: no probe turn now
    assert [c["text"] for c in turns.calls].count(si.PROBE_TEXT) == 1


def test_a_critic_whose_reply_cannot_be_read_does_not_clear_the_proposal_and_gives_its_cost(turns, linked):
    # an answer, its cost given, that does not clear the proposal
    ws, improve, own, base = linked
    p = made_proposal(turns, improve)
    turns.replies = ["no json at all"]
    got = asyncio.run(improve.critic(p["id"], {"text": PROPOSED, "hypothesis": p["hypothesis"]}))
    assert got == {"specific": None, "why": "The critic gave no answer.", "cost": 0.01, "model": "stand-in"}


@pytest.mark.parametrize("way", ["review", "remake", "go_back"])
def test_a_link_at_the_loops_folder_has_no_folder_made_through_it(turns, linked, tmp_path, way):
    # a review, a remake and a proposal to go back make their folder in
    # the loop's folder only where it is a folder of the workspace's own; a link a program put in its place refuses
    # them, and nothing is made, or removed, where it leads (before, each made its folder through it, and the remake
    # first removed what was there)
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    if way == "go_back":
        improve.accept(p["id"])
    loop = os.path.join(ws.root, "skill-improve")
    elsewhere = str(tmp_path / "elsewhere")
    os.rename(loop, elsewhere)
    os.symlink(elsewhere, loop)
    write(os.path.join(elsewhere, p["id"]), "kept.txt", "kept\n")       # what a remake through the link would remove
    before = sorted(os.listdir(elsewhere))
    with pytest.raises(si.SkillImproveError, match="the loop's folder is not a folder of the workspace's own"):
        if way == "review":
            turns.replies = [proposed_reply()]
            review(improve, pid="c" * 12)
        elif way == "remake":
            improve.remake(p["id"])
        else:
            improve.go_back(p["id"], {"id": "d" * 12, "text": CURRENT})
    assert sorted(os.listdir(elsewhere)) == before and os.listdir(os.path.join(elsewhere, p["id"])) == ["kept.txt"]


def test_the_critic_is_given_the_proposal_and_its_hypothesis(turns, linked):
    ws, improve, own, base = linked
    p = made_proposal(turns, improve)
    turns.replies = [json.dumps({"specific": False, "why": "general"})]
    got = asyncio.run(improve.critic(p["id"], {"text": PROPOSED, "hypothesis": p["hypothesis"]}))
    assert got["specific"] is False
    call = turns.calls[-1]
    assert call["text"].startswith(si.SKILL_CRITIC_PROMPT)
    assert CURRENT in call["text"] and PROPOSED in call["text"] and p["hypothesis"] in call["text"]
    assert call["extra_args"] == {"no-session-persistence": None} and call["empty"] is True
    with pytest.raises(si.SkillImproveError, match="not the proposal's"):
        asyncio.run(improve.critic(p["id"], {"text": CURRENT, "hypothesis": p["hypothesis"]}))
    with pytest.raises(si.SkillImproveError, match="hypothesis"):
        asyncio.run(improve.critic(p["id"], {"text": PROPOSED, "hypothesis": "mention @x"}))


def test_the_objects_are_made_only_for_the_text_recorded(turns, linked):
    ws, improve, own, base = linked
    p = made_proposal(turns, improve)
    with pytest.raises(si.SkillImproveError, match="not the proposal's"):
        improve.objects(p["id"], {"text": PROPOSED + "x"})
    got = improve.objects(p["id"], {"text": PROPOSED})
    head = got["head"]
    kinds = {o["id"]: o["kind"] for o in got["objects"]}
    assert kinds[base] == "commit" and kinds[head] == "commit" and kinds[blob_id(PROPOSED)] == "blob"
    assert kinds[blob_id(CURRENT)] == "blob"
    for o in got["objects"]:
        assert shapes.object_id(o["kind"], base64.b64decode(o["data"]), "sha1") == o["id"]
    clone = os.path.join(ws.root, "skill-improve", "b" * 12, "clone")
    shown = git("cat-file", "-p", head, cwd=clone)
    assert f"author {si.LOOP_NAME} <{si.LOOP_EMAIL}>" in shown and f"committer {si.LOOP_NAME} <{si.LOOP_EMAIL}>" in shown
    assert shown.endswith(f"Proofpane: a proposed change to {PATH}\n\nProofpane-Skill-Proposal: {'b' * 12}")
    assert git("diff-tree", "--no-commit-id", "-r", "--name-only", base, head, cwd=clone) == PATH
    assert improve.proposals.read_file()[1]["repos"]["demo"][0]["commits"] == [head]
    # not in the shared repository until it is published
    assert subprocess.run(["git", "cat-file", "-e", head], cwd=own).returncode != 0


def test_the_loops_own_write_of_an_object_a_session_rewrote_is_refused(turns, linked):
    ws, improve, own, base = linked
    p = made_proposal(turns, improve)
    clone = os.path.join(ws.root, "skill-improve", "b" * 12, "clone")
    # a session's program puts a file under the proposal's blob's id first, holding other bytes
    blob = blob_id(PROPOSED)
    os.makedirs(os.path.join(clone, ".git", "objects", blob[:2]), exist_ok=True)
    with open(os.path.join(clone, ".git", "objects", blob[:2], blob[2:]), "wb") as fh:
        fh.write(zlib.compress(b"blob 5\0other"))
    with pytest.raises(si.SkillImproveError, match="could not be made"):
        improve.objects(p["id"], {"text": PROPOSED})


def test_no_filter_runs_on_a_blob_the_loop_makes(turns, linked, tmp_path):
    ws, improve, own, base = linked
    p = made_proposal(turns, improve)
    clone = os.path.join(ws.root, "skill-improve", "b" * 12, "clone")
    ran = str(tmp_path / "cleaned")
    git("config", "filter.evil.clean", f"touch {ran}; cat", cwd=clone)
    write(clone, ".git/info/attributes", "*.md filter=evil\n")
    improve.objects(p["id"], {"text": PROPOSED})
    assert not os.path.exists(ran)


def test_publish_puts_the_commit_under_its_ref_and_close_removes_it(turns, linked):
    ws, improve, own, base = linked
    p = made_proposal(turns, improve)
    head = improve.objects(p["id"], {"text": PROPOSED})["head"]
    got = improve.publish(p["id"])
    assert got == {"ref": f"refs/proofpane/skill-proposals/{'b' * 12}", "head": head}
    assert git("rev-parse", got["ref"], cwd=own) == head
    assert not os.path.exists(os.path.join(ws.root, "skill-improve", "b" * 12))
    assert improve.proposals.read_file()[1]["repos"]["demo"][0]["state"] == "open"
    # no session lists it, as sessions list branches
    assert "skill-proposals" not in git("branch", "-a", cwd=own)
    closed = improve.close(p["id"], "rejected")
    assert closed["ref_removed"] is True
    assert subprocess.run(["git", "rev-parse", "--verify", "--quiet", got["ref"]], cwd=own).returncode != 0
    record = improve.proposals.read_file()[1]["repos"]["demo"][0]
    assert record["state"] == "rejected" and record["commits"] == [head] and record["ended"] is None
    # its bar goes on
    assert improve.check_start("demo", head).startswith("a proposed change to")


def test_close_leaves_a_ref_a_session_moved(turns, linked):
    ws, improve, own, base = linked
    p = made_proposal(turns, improve)
    improve.objects(p["id"], {"text": PROPOSED})
    got = improve.publish(p["id"])
    git("update-ref", got["ref"], base, cwd=own)
    assert improve.close(p["id"], "withdrawn")["ref_removed"] is False
    assert git("rev-parse", got["ref"], cwd=own) == base


def test_a_record_put_back_across_a_restart_before_the_apps_read_leaves_the_text_in_no_file(turns, linked):
    ws, improve, own, base = linked
    with open(improve.proposals.path, "rb") as fh:
        before = fh.read()
    p = made_proposal(turns, improve)
    # the app has not read the record holding it; the workspace starts again, and a program puts the older one back
    with open(improve.proposals.path, "wb") as fh:
        fh.write(before)
    again = si.SkillImprove(ws)
    again.proposals.take_word(1)
    with pytest.raises(si.SkillImproveError, match="No review of this proposal"):
        again.objects(p["id"], {"text": PROPOSED})
    found = subprocess.run(["grep", "-rl", "one line a change", ws.root, own], capture_output=True, text=True)
    assert found.stdout == ""


# ---- the routes, on a real workspace ---------------------------------------------------------------------------------

@pytest.fixture
def real_ws(tmp_path, monkeypatch):
    """A real workspace whose repository `demo` is added from the owner's machine (link_repos), with a stand-in claude
    that says its version."""
    from proofpane_workspace.workspace import Workspace
    bin_dir = tmp_path / "wsbin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text("#!/bin/sh\necho '2.1.274 (Claude Code)'\n")
    (bin_dir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    w = Workspace(str(tmp_path / "ws"), link_repos=True, records_key=b"r" * 32)
    own = str(tmp_path / "own")
    os.makedirs(own)
    git("init", "-q", "-b", "main", cwd=own)
    write(own, PATH, CURRENT)
    base = commit_all(own, "first")
    w.link(own)
    return w, own, base


def call(w, method, path, body=None):
    import httpx
    from proofpane_workspace import agent
    from workspace_fixtures import SECRET

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=agent.make_app(w, SECRET)),
                                     base_url="http://agent", headers={"Authorization": "Bearer " + SECRET}) as c:
            return await c.request(method, path, json=body)
    return asyncio.run(go())


def test_the_routes_of_the_record_and_the_switch(real_ws):
    w, own, base = real_ws
    got = call(w, "GET", "/skill-improve/record").json()
    assert got["word"] is False and got["file"] is None and got["state"] == "waiting"
    # before the word nothing is written
    assert call(w, "PUT", "/skill-improve/switch", {"repo": "own", "on": True}).status_code == 400
    assert call(w, "PUT", "/skill-improve/record", {"generation": 0}).json()["state"] == "record"
    on = call(w, "PUT", "/skill-improve/switch", {"repo": "own", "on": True}).json()
    assert on["branch"] == "refs/heads/main" and on["linked"] is True and on["url"] is None
    got = call(w, "GET", "/skill-improve/record").json()
    assert got["holds"] is True and got["generation"] == 1
    look = call(w, "GET", "/skill-improve/candidates?repo=own").json()
    assert look["ok"] is True and [c["folder"] for c in look["candidates"]] == ["release-notes"]
    # the app's copy restores a record a program removed
    copy_b64 = got["file"]
    os.unlink(w.skill_improve.proposals.path)
    assert call(w, "GET", "/skill-improve/record").json()["state"] == "held"
    assert call(w, "POST", "/skill-improve/record/restore", {"record": copy_b64}).json() == {"generation": 2}
    assert call(w, "GET", "/skill-improve/record").json()["state"] == "record"


def test_the_words_route_is_answered_while_a_start_waits(real_ws):
    import httpx
    from proofpane_workspace import agent
    from workspace_fixtures import SECRET
    w, own, base = real_ws

    async def go():
        waiting = asyncio.create_task(w.skill_improve.word(10))
        await asyncio.sleep(0.2)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=agent.make_app(w, SECRET)),
                                     base_url="http://agent", headers={"Authorization": "Bearer " + SECRET}) as c:
            seen = (await c.get("/skill-improve/record")).json()["word"]
            # the owner's Stop is answered while the start waits (here a session the workspace does not have)
            stopped = (await asyncio.wait_for(c.post(f"/sessions/{'0' * 12}/stop"), 2)).status_code
            answered = await c.put("/skill-improve/record", json={"generation": 0})
        return seen, stopped, answered.status_code, await asyncio.wait_for(waiting, 5)

    assert asyncio.run(go()) == (False, 404, 200, True)


def test_a_proposals_way_before_the_word_is_refused(real_ws):
    w, own, base = real_ws
    got = call(w, "POST", f"/skill-improve/proposals/{'a' * 12}/take", {})
    assert got.status_code == 400 and got.json()["detail"] == si.WAITING_FOR_APP
    assert call(w, "POST", f"/skill-improve/proposals/{'a' * 12}/nothing", {}).status_code == 404


# ---- accepting into the owner's own copy, bringing up to date, and a found proposal ----------------------------------

def published(turns, improve, pid="b" * 12, text=PROPOSED):
    turns.replies = [proposed_reply(text)]
    p = review(improve, pid=pid)["proposal"]
    head = improve.objects(pid, {"text": text})["head"]
    improve.publish(pid)
    return p, head


@pytest.mark.parametrize("how", ["git_file", "link", "holder_link", "objects_link", "refs_link", "commondir",
                                 "refs_heads_link", "refs_heads_missing", "loop_folder_link", "fanout_link",
                                 "alternates"])
def test_the_loops_git_in_its_clone_reads_and_writes_the_clones_own_git_folder(turns, linked, tmp_path, how):
    # each git the loop runs in its clone is given the clone's own git
    # folder (GIT_DIR and GIT_COMMON_DIR), once os.lstat finds it and the folders that hold it to be folders of the
    # loop's own; a program's way of sending the objects call's writes into the shared repository (a .git file or a
    # link at the clone's .git, a link at the folder that holds the clone, the git folder's objects or refs a link, a
    # commondir in it) refuses the call. The shared repository gains neither the proposal's blob nor a branch (before,
    # the objects were written there and a later publish made its branch)
    ws, improve, own, base = linked
    p = made_proposal(turns, improve)
    holder = os.path.join(ws.root, "skill-improve", p["id"])
    clone = os.path.join(holder, "clone")
    shared_git = os.path.join(own, ".git")
    if how == "git_file":
        shutil.rmtree(os.path.join(clone, ".git"))
        with open(os.path.join(clone, ".git"), "w") as fh:
            fh.write(f"gitdir: {shared_git}\n")
    elif how == "link":
        shutil.rmtree(os.path.join(clone, ".git"))
        os.symlink(shared_git, os.path.join(clone, ".git"))
    elif how == "holder_link":
        elsewhere = str(tmp_path / "elsewhere")
        os.makedirs(elsewhere)
        os.symlink(own, os.path.join(elsewhere, "clone"))
        shutil.rmtree(holder)
        os.symlink(elsewhere, holder)
    elif how in ("objects_link", "refs_link"):
        part = how.split("_")[0]
        shutil.rmtree(os.path.join(clone, ".git", part))
        os.symlink(os.path.join(shared_git, part), os.path.join(clone, ".git", part))
    elif how == "refs_heads_link":
        # the git folder's refs/heads a link, its refs a folder
        shutil.rmtree(os.path.join(clone, ".git", "refs", "heads"))
        os.symlink(os.path.join(shared_git, "refs", "heads"), os.path.join(clone, ".git", "refs", "heads"))
    elif how == "refs_heads_missing":
        # refs/heads is checked by name as well as by the walk, which finds no link where
        # it is missing; a git folder of the loop's own has one (with git 2.43, packing every ref and deleting every
        # branch each leave it)
        shutil.rmtree(os.path.join(clone, ".git", "refs", "heads"))
    elif how == "loop_folder_link":
        # the loop's folder, which holds the folder that holds the clone, a link to where it was moved
        loop = os.path.dirname(holder)
        shutil.move(loop, str(tmp_path / "moved"))
        os.symlink(str(tmp_path / "moved"), loop)
    elif how == "fanout_link":
        # the folder of the objects git writes the proposal's blob into a link to the shared repository's
        fan = blob_id(PROPOSED)[:2]
        os.makedirs(os.path.join(shared_git, "objects", fan), exist_ok=True)
        shutil.rmtree(os.path.join(clone, ".git", "objects", fan), ignore_errors=True)
        os.symlink(os.path.join(shared_git, "objects", fan), os.path.join(clone, ".git", "objects", fan))
    elif how == "alternates":
        # an alternates file, which has git read the shared repository's objects as the clone's
        with open(os.path.join(clone, ".git", "objects", "info", "alternates"), "w") as fh:
            fh.write(os.path.join(shared_git, "objects") + "\n")
    else:
        with open(os.path.join(clone, ".git", "commondir"), "w") as fh:
            fh.write(shared_git + "\n")
    with pytest.raises(si.SkillImproveError, match="not a repository of the loop's own"):
        improve.objects(p["id"], {"text": PROPOSED})
    assert subprocess.run(["git", "cat-file", "-e", blob_id(PROPOSED)], cwd=own, capture_output=True).returncode != 0
    assert git("for-each-ref", "--format=%(refname)", "refs/heads", cwd=own) == "refs/heads/main"
    assert git("rev-parse", "main", cwd=own) == base


def test_a_commondir_put_in_the_clones_git_folder_after_its_check_is_not_read(turns, linked):
    # the loop's git in its clone is given GIT_COMMON_DIR as well as
    # GIT_DIR, so that a commondir put in the clone's git folder between the check and the command is not read
    ws, improve, own, base = linked
    p = made_proposal(turns, improve)
    clone = os.path.join(ws.root, "skill-improve", p["id"], "clone")
    env = si._env(clone, "rev-parse", si.ALL_GUARDS, places=False)
    assert env["GIT_DIR"] == env["GIT_COMMON_DIR"] == os.path.join(clone, ".git")
    with open(os.path.join(clone, ".git", "commondir"), "w") as fh:
        fh.write(os.path.join(own, ".git") + "\n")
    got = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=clone, env=env,
                         capture_output=True, text=True).stdout.strip()
    assert got == os.path.join(clone, ".git")


def test_the_publishs_ref_in_the_clone_is_written_and_never_followed(turns, linked, monkeypatch):
    # update-ref --no-deref, so that a symbolic ref a program put at the
    # clone's refs/heads/proposal is replaced by the proposal's commit and the ref it names is not moved
    ws, improve, own, base = linked
    p = made_proposal(turns, improve)
    clone = os.path.join(ws.root, "skill-improve", p["id"], "clone")
    head = improve.objects(p["id"], {"text": PROPOSED})["head"]
    git("symbolic-ref", "refs/heads/proposal", "refs/heads/base", cwd=clone)
    seen, real = {}, shutil.rmtree

    def looked_at_first(path, *args, **kwargs):
        if os.path.realpath(path) == os.path.realpath(os.path.dirname(clone)):
            seen.update(base=git("rev-parse", "refs/heads/base", cwd=clone),
                        symbolic=git("symbolic-ref", "-q", "refs/heads/proposal", cwd=clone, check=False),
                        proposal=git("rev-parse", "refs/heads/proposal", cwd=clone))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", looked_at_first)
    assert improve.publish(p["id"])["head"] == head
    assert seen == {"base": base, "symbolic": "", "proposal": head}


def test_an_acceptance_of_a_commit_other_than_the_one_the_page_shows_is_refused(turns, linked):
    # the app sends the commit the page shows; the workspace merges no
    # other, whichever recorded commit the proposal's ref names
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    with pytest.raises(si.SkillImproveError, match="not the one the page shows"):
        improve.accept(p["id"], {"head": "0" * 40})
    assert git("rev-parse", "main", cwd=own) == base
    assert improve.accept(p["id"], {"head": head})["merged"] is True and git("rev-parse", "main", cwd=own) == head


def test_accept_fast_forwards_the_owners_branch_and_ends_the_bar(turns, linked):
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    assert improve.check_start("demo", head).startswith("a proposed change to")
    got = improve.accept(p["id"])
    assert got == {"merged": True, "head": head, "hooks_ran": False, "builds": improve.builds("demo")}
    assert git("rev-parse", "main", cwd=own) == head
    with open(os.path.join(own, PATH)) as fh:
        assert fh.read() == PROPOSED
    record = improve.proposals.read_file()[1]["repos"]["demo"][0]
    assert record["ended"]["how"] == "merged" and record["state"] == "merged"
    assert improve.check_start("demo", head) is None


def test_the_builds_are_read_again_at_a_look_and_at_the_acceptance(turns, linked):
    """The builds a session would start are read again at each view (the app's look) and at the
    acceptance, not only at the review."""
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    with open(ws.claude_path, "w") as fh:                   # the build the sessions start moves on after the review
        fh.write("#!/bin/sh\necho '2.1.311 (Claude Code)'\n")
    assert ("2.1.311", True) in [(b["version"], b["newer"]) for b in improve.look("demo")["builds"]]
    assert ("2.1.311", True) in [(b["version"], b["newer"]) for b in improve.accept(p["id"])["builds"]]


def test_accept_runs_no_post_merge_hook_and_no_fsmonitor(turns, linked, tmp_path):
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    hook = os.path.join(own, ".git", "hooks", "post-merge")
    with open(hook, "w") as fh:
        fh.write(f"#!/bin/sh\necho changed >> {os.path.join(own, PATH)}\ntouch {tmp_path / 'hooked'}\n")
    os.chmod(hook, 0o755)
    monitor = str(tmp_path / "monitor")
    with open(monitor, "w") as fh:
        fh.write(f"#!/bin/sh\ntouch {tmp_path / 'monitored'}\n")
    os.chmod(monitor, 0o755)
    git("config", "core.fsmonitor", monitor, cwd=own)
    assert improve.accept(p["id"])["merged"] is True
    assert not os.path.exists(tmp_path / "hooked") and not os.path.exists(tmp_path / "monitored")
    with open(os.path.join(own, PATH)) as fh:
        assert fh.read() == PROPOSED


def test_accept_is_refused_on_another_branch_with_changes_and_with_verify_signatures(turns, linked):
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    git("checkout", "-q", "-b", "other", cwd=own)
    with pytest.raises(si.SkillImproveError, match="checked out"):
        improve.accept(p["id"])
    git("checkout", "-q", "main", cwd=own)
    write(own, "README.md", "mine\n")
    with pytest.raises(si.SkillImproveError, match="changes of its own"):
        improve.accept(p["id"])
    os.unlink(os.path.join(own, "README.md"))
    git("config", "merge.verifySignatures", "true", cwd=own)
    with pytest.raises(si.SkillImproveError, match="verifySignatures"):
        improve.accept(p["id"])
    assert git("rev-parse", "main", cwd=own) == base


def test_accept_is_refused_for_an_ignored_attributes_file_in_the_work_tree(turns, linked, tmp_path):
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    ran = str(tmp_path / "smudged")
    git("config", "filter.evil.smudge", f"touch {ran}; cat", cwd=own)
    git("config", "filter.evil.clean", "cat", cwd=own)
    write(own, ".claude/skills/release-notes/.gitattributes", "SKILL.md filter=evil\n")
    write(own, ".git/info/exclude", ".gitattributes\n")            # ignored, so the copy has no changes of its own
    with pytest.raises(si.SkillImproveError, match="gitattributes"):
        improve.accept(p["id"])
    assert not os.path.exists(ran) and git("rev-parse", "main", cwd=own) == base
    write(own, ".claude/skills/release-notes/.gitattributes", "SKILL.md text eol=crlf\n")
    with pytest.raises(si.SkillImproveError, match="gitattributes"):
        improve.accept(p["id"])


def test_accept_is_refused_after_the_ref_moved_and_after_the_branch_moved(turns, linked):
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    ref = f"refs/proofpane/skill-proposals/{p['id']}"
    git("update-ref", ref, base, cwd=own)
    with pytest.raises(si.SkillImproveError, match="changed"):
        improve.accept(p["id"])
    git("update-ref", ref, head, cwd=own)
    write(own, "README.md", "moved on\n")
    commit_all(own, "the branch moves on")
    with pytest.raises(si.SkillImproveError, match="up to date"):
        improve.accept(p["id"])


def test_an_attribute_added_on_the_branch_since_the_review_is_refused_at_the_acceptance(turns, linked):
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    # the info attributes, which a checkout reads, are changed after the review
    write(own, ".git/info/attributes", "*.md text eol=crlf\n")
    with pytest.raises(si.SkillImproveError, match="would not write its text"):
        improve.accept(p["id"])


def test_an_attribute_added_on_the_branch_since_the_review_is_refused_at_a_remake(turns, linked):
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    write(own, "OTHER.md", "another path moved\n")
    git("add", "OTHER.md", cwd=own)
    git("commit", "-q", "-m", "another path", cwd=own)
    write(own, ".git/info/attributes", "*.md text eol=crlf\n")
    with pytest.raises(si.SkillImproveError, match="cannot be brought up to date"):
        improve.remake(p["id"])


def test_the_publish_passes_no_porcelain_and_every_diff_no_ext_diff_and_no_textconv(turns, linked, monkeypatch):
    """A repository added from the owner's machine needs no git 2.41: no fetch of the loop's there passes --porcelain;
    and every diff the loop asks git for runs no external diff and no textconv."""
    ws, improve, own, base = linked
    calls, real = [], si._git
    monkeypatch.setattr(si, "_git", lambda *args, **kw: calls.append(args) or real(*args, **kw))
    p, head = published(turns, improve)
    improve.accept(p["id"])
    fetches = [a for a in calls if a and a[0] == "fetch"]
    assert fetches and all("--porcelain" not in a for a in fetches)
    diffs = [a for a in calls if a and a[0].startswith("diff")]
    assert diffs and all("--no-ext-diff" in a and "--no-textconv" in a for a in diffs)


def test_a_failed_check_after_the_move_is_undone_with_reset_keep(turns, linked, monkeypatch):
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    real = improve._at_rest
    calls = []

    def fails_first(*args, **kwargs):
        calls.append(args)
        return False if len(calls) == 1 else real(*args, **kwargs)

    monkeypatch.setattr(improve, "_at_rest", fails_first)
    got = improve.accept(p["id"])
    assert got["merged"] is False and got["undone"] is True
    assert git("rev-parse", "main", cwd=own) == base
    with open(os.path.join(own, PATH)) as fh:
        assert fh.read() == CURRENT
    assert improve.proposals.read_file()[1]["repos"]["demo"][0]["ended"] is None


def test_the_undo_stops_where_the_owner_changed_the_file(turns, linked, monkeypatch):
    ws, improve, own, base = linked
    p, head = published(turns, improve)

    def owner_changes(*args, **kwargs):
        write(own, PATH, PROPOSED + "the owner's own line\n")
        return False

    monkeypatch.setattr(improve, "_at_rest", owner_changes)
    got = improve.accept(p["id"])
    assert got["merged"] is False and got["undone"] is False and got["left"]["branch"] == head
    with open(os.path.join(own, PATH)) as fh:
        assert fh.read().endswith("the owner's own line\n")


def test_a_link_at_a_proposals_own_folder_is_taken_away_before_a_remake_and_never_followed(turns, linked, tmp_path):
    # a remake removes the proposal's own folder before it makes it
    # again; a link a program put in its place is taken away, never removed through, and the remake goes on
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    write(own, "README.md", "moved on\n")
    moved = commit_all(own, "another path moves on")
    elsewhere = str(tmp_path / "elsewhere")
    write(elsewhere, "kept.txt", "kept\n")
    folder = os.path.join(ws.root, "skill-improve", p["id"])
    shutil.rmtree(folder, ignore_errors=True)
    os.symlink(elsewhere, folder)
    got = improve.remake(p["id"])
    assert got["base"] == moved and os.listdir(elsewhere) == ["kept.txt"] and not os.path.islink(folder)


def test_a_proposal_brought_up_to_date_then_merged_and_a_start_from_the_branch_runs(turns, linked):
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    write(own, "README.md", "moved on\n")
    moved = commit_all(own, "another path moves on")
    got = improve.remake(p["id"])
    head2 = got["head"]
    assert got["base"] == moved and got["replaces"] == {"base": base, "head": head}
    assert git("rev-parse", f"{head2}^", cwd=os.path.join(ws.root, "skill-improve", p["id"], "clone")) == moved
    improve.publish(p["id"])
    assert git("rev-parse", f"refs/proofpane/skill-proposals/{p['id']}", cwd=own) == head2
    record = improve.proposals.read_file()[1]["repos"]["demo"][0]
    assert record["bases"] == [base, moved] and record["commits"] == [head, head2]
    # the old commit, which a session could have fetched before, still bars
    assert improve.check_start("demo", head).startswith("a proposed change to")
    assert improve.accept(p["id"])["merged"] is True
    write(own, "README.md", "later\n")
    later = commit_all(own, "later")
    assert improve.check_start("demo", later) is None


def test_a_script_of_the_skills_folder_changed_on_the_branch_leaves_it_outdated(turns, linked):
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    write(own, ".claude/skills/release-notes/run.sh", "echo hi\n")
    commit_all(own, "a script of the skill changes")
    with pytest.raises(si.SkillImproveError, match="outdated"):
        improve.remake(p["id"])
    look = improve.look("demo")
    assert look["proposals"][p["id"]]["folder_unchanged"] is False and look["proposals"][p["id"]]["reached"] is None


def test_a_remake_whose_head_adds_a_sibling_folding_to_the_folders_name_is_refused(turns, linked):
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    write(own, ".claude/skills/Release-Notes/SKILL.md", OTHER)
    commit_all(own, "a sibling")
    with pytest.raises(si.SkillImproveError, match="folds to it"):
        improve.remake(p["id"])


def test_a_proposal_merged_by_a_session_is_found_and_take_ends_its_bar(turns, linked):
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    # a session fetched the ref and merged it, then changed the skill again in a later commit
    git("merge", "-q", "--ff-only", head, cwd=own)
    write(own, PATH, PROPOSED.replace("one line a change", "a line each"))
    later = commit_all(own, "changed again")
    look = improve.look("demo")
    seen = look["proposals"][p["id"]]
    assert seen["reached"] == "commit" and seen["change_commit"] == head
    assert look["bars_now"].startswith("a proposed change to")
    improve.take(p["id"])
    assert improve.check_start("demo", later) is None
    assert improve.look("demo")["bars_now"] is None


def test_a_squash_outside_the_app_is_known_by_its_content(turns, linked):
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    write(own, PATH, PROPOSED)
    squashed = commit_all(own, "the same text, squashed")
    look = improve.look("demo")
    assert look["proposals"][p["id"]]["reached"] == "content"
    assert look["proposals"][p["id"]]["change_commit"] == squashed
    assert look["bars_now"].startswith("a proposed change to")


def test_a_found_proposals_go_back_ends_its_bar_once_merged(turns, linked):
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    git("merge", "-q", "--ff-only", head, cwd=own)
    back = improve.go_back(p["id"], {"id": "d" * 12, "text": CURRENT})["proposal"]
    assert back["kind"] == "back" and back["fresh"] is False and back["blob"] == blob_id(CURRENT)
    improve.objects(back["id"], {"text": CURRENT})
    improve.publish(back["id"])
    assert improve.accept(back["id"])["merged"] is True
    record = {q["id"]: q for q in improve.proposals.read_file()[1]["repos"]["demo"]}
    assert record[p["id"]]["ended"]["how"] == "back" and record[back["id"]]["ended"]["how"] == "merged"
    with open(os.path.join(own, PATH)) as fh:
        assert fh.read() == CURRENT
    assert improve.check_start("demo", "main") is None


def test_a_proposal_to_go_back_for_a_merged_one_and_a_found_one_gone_back_from_once(turns, linked):
    # the watch's proposal to go back is made for a proposal the owner merged, whose bar has ended; and a
    # found proposal its proposal to go back ended is not gone back from twice
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    assert improve.accept(p["id"])["merged"] is True
    back = improve.go_back(p["id"], {"id": "d" * 12, "text": CURRENT})["proposal"]
    assert back["kind"] == "back" and back["for"] == p["id"] and back["base"] == head and back["fresh"] is False
    improve.objects(back["id"], {"text": CURRENT})
    improve.publish(back["id"])
    assert improve.accept(back["id"])["merged"] is True
    record = {q["id"]: q for q in improve.proposals.read_file()[1]["repos"]["demo"]}
    assert record[p["id"]]["ended"]["how"] == "merged" and record[back["id"]]["ended"]["how"] == "merged"
    other = PROPOSED.replace("one line a change", "a line for each change")
    found, found_head = published(turns, improve, pid="e" * 12, text=other)
    git("merge", "-q", "--ff-only", found_head, cwd=own)            # a session's merge: found, not the owner's
    first = improve.go_back(found["id"], {"id": "f" * 12, "text": CURRENT})["proposal"]
    assert first["blob"] == back["blob"]            # the text `back` proposed: a proposal to go back is no repeat
    improve.objects(first["id"], {"text": CURRENT})
    improve.publish(first["id"])
    improve.accept(first["id"])
    record = {q["id"]: q for q in improve.proposals.read_file()[1]["repos"]["demo"]}
    assert record[found["id"]]["ended"]["how"] == "back"
    with pytest.raises(si.SkillImproveError, match="gone back from already"):
        improve.go_back(found["id"], {"id": "1" * 12, "text": CURRENT})


def test_a_revert_alone_does_not_end_a_bar_by_commit(turns, linked):
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    git("merge", "-q", "--ff-only", head, cwd=own)
    git("revert", "--no-edit", "HEAD", cwd=own)
    assert improve.check_start("demo", "main").startswith("a proposed change to")


# ---- on GitHub: a fake ssh that serves github.com from a bare repository on this disk, and a fake gh ------------------

FAKE_SSH = '''#!{python}
import os, shlex, sys
parts = shlex.split(sys.argv[-1])
os.execvp("git", ["git", parts[0][len("git-"):], os.path.join({root!r}, parts[1].lstrip("/"))])
'''

FAKE_GH = '''#!{python}
import json, os, subprocess, sys, tempfile
STATE, BARE = {state!r}, {bare!r}
args = sys.argv[1:]
with open(STATE) as fh:
    state = json.load(fh)
state.setdefault("calls", []).append(args)

def git(*a, cwd=BARE):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()

def sha(ref):
    return git("rev-parse", ref)

def save():
    with open(STATE, "w") as fh:
        json.dump(state, fh)

out = ""
if args[:2] == ["pr", "create"]:
    head, base = args[args.index("--head") + 1], args[args.index("--base") + 1]
    if any(pr["head"] == head and pr["state"] == "open" for pr in state["prs"]):
        save()
        sys.stderr.write(f"a pull request for branch \\"{{head}}\\" into branch \\"{{base}}\\" already exists\\n")
        sys.exit(1)
    n = len(state["prs"]) + 1
    state["prs"].append({{"number": n, "head": head, "base": base, "state": "open"}})
    out = f"https://github.com/octo/demo/pull/{{n}}\\n"
elif args[0] == "api" and "/pulls?" in args[1]:
    query = dict(kv.split("=", 1) for kv in args[1].split("?", 1)[1].split("&"))
    mine = [pr for pr in state["prs"] if "octo:" + pr["head"] == query.get("head")]
    if query.get("state") == "open":
        mine = [pr for pr in mine if pr["state"] == "open"]
    out = json.dumps([{{"number": pr["number"], "state": "closed" if pr["state"] == "merged" else pr["state"],
                        "merged_at": "2026-10-26T00:00:00Z" if pr["state"] == "merged" else None,
                        "html_url": f"https://github.com/octo/demo/pull/{{pr['number']}}",
                        "head": {{"ref": pr["head"], "sha": sha("refs/heads/" + pr["head"])}},
                        "base": {{"ref": pr["base"]}}}}
                      for pr in reversed(mine)])
elif args[0] == "api" and "/pulls/" in args[1]:
    pr = state["prs"][int(args[1].rsplit("/", 1)[1]) - 1]
    view = {{"state": pr["state"], "base": {{"ref": pr["base"]}}, "head": {{"sha": sha("refs/heads/" + pr["head"])}},
            "merged": pr["state"] == "merged", "auto_merge": {{"merge_method": "squash"}} if pr.get("auto") else None,
            "merge_commit_sha": pr.get("made")}}
    view.update(state.get("pull_override", {{}}))
    out = json.dumps(view)
elif args[0] == "api" and "/compare/" in args[1]:
    a, b = args[1].split("/compare/", 1)[1].split("...")
    commits = git("rev-list", "--reverse", f"{{a}}..{{b}}").split()
    behind = git("rev-list", "--count", f"{{b}}..{{a}}")
    files = []
    for line in git("diff", "--name-status", git("merge-base", a, b), b).splitlines():
        status, name = line.split("\\t", 1)
        files.append({{"filename": name, "status": {{"M": "modified", "A": "added", "D": "removed"}}[status[0]]}})
    status = "identical" if not commits and behind == "0" else "ahead" if behind == "0" else \\
        "behind" if not commits else "diverged"
    view = {{"status": status, "commits": [{{"sha": c}} for c in commits], "files": files}}
    view.update(state.get("compare_override", {{}}).get(args[1].split("/compare/", 1)[1], {{}}))
    out = json.dumps(view)
elif args[:2] == ["pr", "merge"] and "--disable-auto" in args:
    pr = state["prs"][int(args[2].rsplit("/", 1)[1]) - 1]
    if state.get("disable_fails"):          # GitHub refused to turn its automatic merge off
        save()
        sys.exit(1)
    pr["auto"] = False
elif args[:2] == ["pr", "merge"]:
    n = int(args[2].rsplit("/", 1)[1])
    pr = state["prs"][n - 1]
    match = args[args.index("--match-head-commit") + 1]
    if "--auto" in args or sha("refs/heads/" + pr["head"]) != match:
        save()
        sys.exit(1)
    if state.get("queue"):                  # a branch whose merges go through a merge queue: queued, not merged
        save()
        sys.exit(0)
    if state.get("checks_pending"):         # its checks not yet passed: gh turned its automatic merge on, not merged
        pr["auto"] = True
        save()
        sys.exit(0)
    work = tempfile.mkdtemp()
    git("clone", "-q", BARE, work, cwd="/")
    git("checkout", "-q", pr["base"], cwd=work)
    who = ["-c", "user.name=GitHub", "-c", "user.email=noreply@github.com"]
    if "--merge" in args:                   # a merge commit, its second parent the proposal's
        git(*who, "merge", "-q", "--no-ff", "-m", "Merge pull request", "origin/" + pr["head"], cwd=work)
    else:
        git("merge", "-q", "--squash", "origin/" + pr["head"], cwd=work)
        git(*who, "commit", "-q", "-m", "squashed", cwd=work)
    git("push", "-q", "origin", pr["base"], cwd=work)
    pr["state"], pr["made"] = "merged", sha("refs/heads/" + pr["base"])
    if state.get("moved_on"):               # origin's branch moved on, by another path, before the loop's fetch
        with open(os.path.join(work, "LATER.md"), "w") as fh:
            fh.write("later\\n")
        git("add", "LATER.md", cwd=work)
        git(*who, "commit", "-q", "-m", "later", cwd=work)
        git("push", "-q", "origin", pr["base"], cwd=work)
save()
sys.stdout.write(out)
'''


@pytest.fixture
def on_github(tmp_path, monkeypatch):
    """A clone whose origin is git@github.com:octo/demo.git, served by a fake ssh from a bare repository here, and a
    fake gh that answers from a state file and that repository; its switch on."""
    import sys
    bin_dir = tmp_path / "ghbin"
    bin_dir.mkdir()
    served = tmp_path / "served"
    (served / "octo").mkdir(parents=True)
    bare = str(served / "octo" / "demo.git")
    state = str(tmp_path / "gh-state.json")
    with open(state, "w") as fh:
        json.dump({"prs": []}, fh)
    (bin_dir / "ssh").write_text(FAKE_SSH.format(python=sys.executable, root=str(served)))
    (bin_dir / "gh").write_text(FAKE_GH.format(python=sys.executable, state=state, bare=bare))
    (bin_dir / "ssh").chmod(0o755)
    (bin_dir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    seed = str(tmp_path / "seed")
    os.makedirs(seed)
    git("init", "-q", "-b", "main", cwd=seed)
    write(seed, PATH, CURRENT)
    write(seed, "README.md", "demo\n")
    base = commit_all(seed, "first")
    git("clone", "-q", "--bare", seed, bare, cwd=str(tmp_path))
    ws = _Ws(tmp_path / "ws")
    git("clone", "-q", "git@github.com:octo/demo.git", ws._repo_path("demo"), cwd=str(tmp_path))
    si.forget_clean()
    si._VERIFIED.clear()
    improve = si.SkillImprove(ws)
    improve.proposals.take_word(0)
    on = improve.set_switch({"repo": "demo", "on": True})
    assert on["url"] == "git@github.com:octo/demo.git" and on["branch"] == "refs/heads/main"
    return ws, improve, bare, state, base


@pytest.fixture
def gh_turns(on_github, monkeypatch, tmp_path):
    from proofpane_workspace import agents as agentry
    ws, improve, bare, state, base = on_github
    t = _Turns(ws, [])
    monkeypatch.setattr(agentry, "one_turn", t)
    claude = str(tmp_path / "claude")
    with open(claude, "w") as fh:
        fh.write("#!/bin/sh\necho '2.1.274 (Claude Code)'\n")
    os.chmod(claude, 0o755)
    ws.claude_path = claude
    return t


def gh_state(path):
    with open(path) as fh:
        return json.load(fh)


def test_a_pull_request_then_a_merge_on_github(gh_turns, on_github):
    ws, improve, bare, state, base = on_github
    p, head = published(gh_turns, improve)
    opened = improve.pull_request(p["id"], {"counts": COUNTS})
    assert opened["url"] == "https://github.com/octo/demo/pull/1"
    assert opened["branch"] == f"proofpane/skill-release-notes-{p['id']}"
    # nothing pushed but the proposal's branch
    refs = git("for-each-ref", "--format=%(refname)", cwd=bare).split()
    assert sorted(refs) == sorted([f"refs/heads/{opened['branch']}", "refs/heads/main"])
    assert improve.proposals.read_file()[1]["repos"]["demo"][0]["state"] == "pull_request"
    with open(ws.claude_path, "w") as fh:                   # read again at the acceptance
        fh.write("#!/bin/sh\necho '2.1.311 (Claude Code)'\n")
    got = improve.merge(p["id"], {"url": opened["url"], "method": "squash"})
    assert got["merged"] is True and got["first_parent_was_head"] is True and got["changed_to_blob"] is True
    assert got["brought_besides"] == []
    assert ("2.1.311", True) in [(b["version"], b["newer"]) for b in got["builds"]]
    merge_call = [c for c in gh_state(state)["calls"] if c[:2] == ["pr", "merge"]][0]
    assert merge_call == ["pr", "merge", opened["url"], "--squash", "--match-head-commit", head]
    assert improve.proposals.read_file()[1]["repos"]["demo"][0]["ended"]["how"] == "merged"


def test_a_merge_on_github_of_a_commit_other_than_the_one_the_page_shows_is_refused(gh_turns, on_github):
    # as an acceptance, a merge on GitHub of a commit the page does not
    # show is refused, before GitHub is asked anything
    ws, improve, bare, state, base = on_github
    p, head = published(gh_turns, improve)
    opened = improve.pull_request(p["id"], {"counts": COUNTS})
    asked = len(gh_state(state)["calls"])
    with pytest.raises(si.SkillImproveError, match="not the one the page shows"):
        improve.merge(p["id"], {"url": opened["url"], "method": "squash", "head": "0" * 40})
    assert len(gh_state(state)["calls"]) == asked and git("rev-parse", "main", cwd=bare) == base
    assert improve.merge(p["id"], {"url": opened["url"], "method": "squash", "head": head})["merged"] is True


def test_the_looks_bar_is_read_at_what_a_clone_has_checked_out_not_at_origins_head(gh_turns, on_github):
    # for a clone, origin's head is read by the look's fetch, but a start
    # that names no base begins at the clone's own HEAD (its recorded branch, which a look needs checked out), where
    # the bar is read and which the look names (bars_at)
    ws, improve, bare, state, base = on_github
    p, head = published(gh_turns, improve)
    git("push", "-q", bare, f"{head}:refs/heads/main", cwd=ws._repo_path("demo"))     # origin's main takes it
    got = improve.look("demo")
    assert got["head"] == head and got["proposals"][p["id"]]["reached"] == "commit"
    assert got["bars_at"] == {"branch": "refs/heads/main", "head": base} and got["bars_now"] is None
    git("merge", "-q", "--ff-only", head, cwd=ws._repo_path("demo"))             # the clone's main takes it too
    got = improve.look("demo")
    assert got["bars_at"] == {"branch": "refs/heads/main", "head": head} and got["bars_now"].startswith("a proposed")


def test_the_looks_bar_names_the_branch_the_owners_copy_has_checked_out(turns, linked):
    # in his copy on another branch than the proposal's, the bar is read
    # there and the branch named, for the page to name it rather than the proposal's
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    git("checkout", "-q", "-b", "side", head, cwd=own)
    got = improve.look("demo")
    assert got["bars_at"] == {"branch": "refs/heads/side", "head": head} and got["bars_now"].startswith("a proposed")


def test_the_look_reads_a_pull_requests_state_on_github(gh_turns, on_github):
    # a pull request closed on GitHub without a merge closes the proposal as rejected at the loop's next
    # look, which reads GitHub's own view of it
    ws, improve, bare, state, base = on_github
    p, head = published(gh_turns, improve)
    assert improve.look("demo")["proposals"][p["id"]]["pull_request"] is None
    improve.pull_request(p["id"], {"counts": COUNTS})
    assert improve.look("demo")["proposals"][p["id"]]["pull_request"] == {"state": "open", "merged": False}
    seen = gh_state(state)
    seen["prs"][0]["state"] = "closed"
    with open(state, "w") as fh:
        json.dump(seen, fh)
    assert improve.look("demo")["proposals"][p["id"]]["pull_request"] == {"state": "closed", "merged": False}


def test_githubs_view_refusing_another_commit_another_path_and_another_base(gh_turns, on_github):
    ws, improve, bare, state, base = on_github
    p, head = published(gh_turns, improve)
    opened = improve.pull_request(p["id"])
    s = gh_state(state)
    s["pull_override"] = {"head": {"sha": base}}
    with open(state, "w") as fh:
        json.dump(s, fh)
    with pytest.raises(si.SkillImproveError, match="not the proposal's"):
        improve.merge(p["id"], {"url": opened["url"]})
    s["pull_override"] = {"base": {"ref": "release"}}
    with open(state, "w") as fh:
        json.dump(s, fh)
    with pytest.raises(si.SkillImproveError, match="not the proposal's"):
        improve.merge(p["id"], {"url": opened["url"]})
    s["pull_override"] = {}
    s["compare_override"] = {f"{base}...{head}": {"files": [{"filename": PATH, "status": "modified"},
                                                            {"filename": "README.md", "status": "modified"}]}}
    with open(state, "w") as fh:
        json.dump(s, fh)
    with pytest.raises(si.SkillImproveError, match="one commit and the one path"):
        improve.merge(p["id"], {"url": opened["url"]})
    assert not [c for c in gh_state(state)["calls"] if c[:2] == ["pr", "merge"]]


def test_a_merge_on_a_branch_that_does_not_hold_the_base_is_refused(gh_turns, on_github):
    ws, improve, bare, state, base = on_github
    p, head = published(gh_turns, improve)
    opened = improve.pull_request(p["id"])
    s = gh_state(state)
    s["compare_override"] = {f"main...{base}": {"status": "diverged"}}
    with open(state, "w") as fh:
        json.dump(s, fh)
    with pytest.raises(si.SkillImproveError, match="does not hold"):
        improve.merge(p["id"], {"url": opened["url"]})


def test_a_squash_merged_on_github_outside_the_app_is_found(gh_turns, on_github):
    ws, improve, bare, state, base = on_github
    p, head = published(gh_turns, improve)
    # someone merged the same text on GitHub as another commit, without the app
    work = str(os.path.join(ws.root, "elsewhere"))
    git("clone", "-q", bare, work, cwd=ws.root)
    write(work, PATH, PROPOSED)
    commit_all(work, "merged elsewhere")
    git("push", "-q", "origin", "main", cwd=work)
    look = improve.look("demo")
    assert look["proposals"][p["id"]]["reached"] == "content"
    # the bar is read at what the clone has checked out: its main has not
    # taken origin's, so a start there begins from the base, which does not hold the text; once it has, it is refused
    assert look["bars_at"] == {"branch": "refs/heads/main", "head": base} and look["bars_now"] is None
    merged = git("rev-parse", "HEAD", cwd=work)
    git("merge", "-q", "--ff-only", merged, cwd=ws._repo_path("demo"))
    look = improve.look("demo")
    assert look["bars_at"] == {"branch": "refs/heads/main", "head": merged} and \
        look["bars_now"].startswith("a proposed change to")


# ---- the bar at each start -------------------------------------------------------------------------------------------

def signed_record(root, key, proposals, repo="demo"):
    """The record of skill proposals as the workspace keyed by `key` writes it, naming `repo` with `proposals`."""
    props = si.SkillProposals(root, key)
    props.has_word = True
    record = si._fresh_record()
    record["repos"][repo] = list(proposals)
    props._write(record)


@pytest.fixture
def controlled(tmp_path, monkeypatch):
    """test_controller_workspace's workspace (a stand-in claude that says its version and its help, a stand-in
    terminal), its repository `demo` holding the business skill, the app's word had and the owner's side on."""
    from proofpane_workspace import workspace as wsmod
    from proofpane_workspace.workspace import Workspace
    from test_controller_workspace import _Term, on
    from test_workspace_ask_effort import OFFERED, _no_handshake, _refreshed, _says_claude
    bin_dir = tmp_path / "wsbin"
    bin_dir.mkdir()
    says = str(tmp_path / "says")
    (bin_dir / "claude").write_text(_says_claude(says))
    (bin_dir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setattr(wsmod.terminals, "Terminal", _Term)
    monkeypatch.setattr(wsmod.claude_cli, "handshake", _no_handshake)
    _Term.seen = []
    w = Workspace(str(tmp_path / "ws"))
    repo = os.path.join(w.repos_dir, "demo")
    os.makedirs(repo)
    git("init", "-q", "-b", "main", cwd=repo)
    write(repo, "README.md", "demo\n")
    write(repo, PATH, CURRENT)
    base = commit_all(repo, "first")
    w.capabilities = lambda: dict(OFFERED)
    si.forget_clean()
    si._VERIFIED.clear()
    w.skill_improve.proposals.take_word(0)
    m = on(w)
    yield w, repo, base, m
    _refreshed()


def test_a_controllers_start_from_a_base_holding_a_proposal_is_refused_and_records_no_task(controlled):
    from test_controller_workspace import plan, refused
    w, repo, base, m = controlled
    c = proposal_commit(repo, base)
    record_it(w.skill_improve, "demo", proposal(blob_id(PROPOSED), [c], base))
    req = plan(w, m)
    why = refused(w.controller.start_task, {"requirement": req, "repo": "demo", "task": "Edit it.", "base": "proposal"})
    assert why.startswith("a proposed change to")
    assert w.controller.record["tasks"] == {} and w.sessions() == []
    assert w.skill_improve.proposals.refused[-1]["kind"] == "controller"
    # from main, which does not hold it, the start runs
    from test_controller_workspace import start
    got = start(w, req)
    assert got["task"] in w.controller.record["tasks"]


def test_a_controllers_start_whose_checkout_hook_writes_a_proposals_text_is_unmade(controlled, tmp_path):
    from test_controller_workspace import plan, refused
    w, repo, base, m = controlled
    record_it(w.skill_improve, "demo", proposal(blob_id(PROPOSED), [], base))
    text = str(tmp_path / "proposed.md")
    with open(text, "w") as fh:
        fh.write(PROPOSED)
    hook = os.path.join(repo, ".git", "hooks", "post-checkout")
    with open(hook, "w") as fh:
        fh.write(f"#!/bin/sh\ncp {text} {PATH}\n")
    os.chmod(hook, 0o755)
    why = refused(w.controller.start_task, {"requirement": plan(w, m), "repo": "demo", "task": "Edit it."})
    assert why.startswith("a proposed change to")
    assert w.controller.record["tasks"] == {} and w.sessions() == []
    assert git("worktree", "list", "--porcelain", cwd=repo).count("worktree ") == 1


def test_a_controllers_walk_is_made_before_the_start_lock(controlled, monkeypatch):
    from test_controller_workspace import plan, start
    w, repo, base, m = controlled
    record_it(w.skill_improve, "demo", proposal(blob_id(PROPOSED), [], base, fresh=False))
    held = []
    real = w.skill_improve.prewalk

    def walk(repo_name, commit):
        held.append(w.controller._start_lock().locked())
        return real(repo_name, commit)

    monkeypatch.setattr(w.skill_improve, "prewalk", walk)
    start(w, plan(w, m))
    assert held == [False]
    assert base in si._CLEAN[frozenset()]


def test_a_continue_whose_worktree_holds_a_proposals_text_is_refused_and_the_task_left_waiting(controlled):
    from test_controller_workspace import _Term, _passed, _waiting, refused
    w, repo, base, m = controlled
    got, t = _waiting(w, m, int(time.time()) + 3600)
    _passed(w, (got, t))
    record_it(w.skill_improve, "demo", proposal(blob_id(PROPOSED), [], base))
    write(w._work(got["session"]), PATH, PROPOSED)
    why = refused(w.controller.continue_task, {"task": got["task"]})
    assert why.startswith("a proposed change to")
    assert t["state"] == "waiting" and _Term.seen[-1].running is False
    assert w.skill_improve.proposals.refused[-1]["kind"] == "continue"


def test_a_start_in_a_repository_the_record_does_not_name_runs_as_today(controlled):
    from test_controller_workspace import plan, start
    w, repo, base, m = controlled
    c = proposal_commit(repo, base)
    record_it(w.skill_improve, "other", proposal(blob_id(PROPOSED), [c], base))
    got = start(w, plan(w, m), base="proposal")
    assert got["task"] in w.controller.record["tasks"]


def test_before_the_word_a_controllers_start_waits_and_goes_on_when_it_comes(controlled, monkeypatch):
    from test_controller_workspace import plan
    w, repo, base, m = controlled
    w.skill_improve.proposals.has_word = False
    req = plan(w, m)

    async def go():
        asyncio.get_running_loop().call_later(0.3, w.skill_improve.proposals.take_word, 0)
        return await w.controller.start_task({"requirement": req, "repo": "demo", "task": "Edit it."})

    got = asyncio.run(go())
    assert got["task"] in w.controller.record["tasks"]


def test_before_the_word_a_controllers_start_is_refused_when_it_does_not_come(controlled, monkeypatch):
    from test_controller_workspace import plan, refused
    w, repo, base, m = controlled
    w.skill_improve.proposals.has_word = False
    monkeypatch.setattr(si, "WORD_WAIT", 0.2)
    monkeypatch.setattr(si.SkillProposals.word, "__defaults__", (0.2,))
    why = refused(w.controller.start_task, {"requirement": plan(w, m), "repo": "demo", "task": "Edit it."})
    assert why == si.WAITING_FOR_APP


# an agent's run, in a real workspace agent with the real SDK driving the stand-in claude (test_agents_runs.py)

def agents_workspace(tmp_path, proposals):
    """test_agents_runs' workspace, its repository holding the business skill and its record of skill proposals
    holding `proposals`, signed with the agent's own key, before the agent starts."""
    from proofpane_workspace import agents as agentry
    from test_agents_runs import Workspace as AgentWorkspace
    from workspace_fixtures import SECRET
    root = tmp_path / "ws"
    repo = str(root / "repos" / "demo")
    os.makedirs(repo)
    git("init", "-q", "-b", "main", cwd=repo)
    write(repo, "README.md", "demo\n")
    write(repo, PATH, CURRENT)
    base = commit_all(repo, "first")
    signed_record(str(root), agentry.records_key(SECRET), proposals(repo, base))
    return AgentWorkspace(tmp_path), repo, base


def test_an_agents_run_whose_precheck_writes_a_proposals_text_is_refused(tmp_path):
    from test_agents_runs import put, run, until
    text = str(tmp_path / "proposed.md")
    with open(text, "w") as fh:
        fh.write(PROPOSED)
    w, repo, base = agents_workspace(tmp_path, lambda repo, base: [proposal(blob_id(PROPOSED), [], base)])
    try:
        put(w, "hello", precheck=f"cp {text} {PATH}", keep_worktree=False)
        got = until(w, run(w)["run_id"], ("failed", "done", "blocked"))
        assert (got["state"], got["cause"]) == ("failed", "start") and got["reason"].startswith("a proposed change")
        refused = w.http.get("/skill-improve/record").json()["refused"]
        assert [r["kind"] for r in refused] == ["agent"]
    finally:
        w.stop()


def test_a_kept_worktree_so_filled_refuses_later_runs_and_its_way_out(tmp_path):
    from test_agents_runs import put, run, until
    text = str(tmp_path / "proposed.md")
    with open(text, "w") as fh:
        fh.write(PROPOSED)
    w, repo, base = agents_workspace(tmp_path, lambda repo, base: [proposal(blob_id(PROPOSED), [], base)])
    try:
        put(w, "hello", precheck=f"cp {text} {PATH}", keep_worktree=True)
        first = until(w, run(w)["run_id"], ("failed", "done", "blocked"))
        assert (first["state"], first["cause"]) == ("failed", "start")
        # the precheck gone, the kept worktree still holds the text: _commit_work commits it, and the check on its
        # HEAD refuses it (step 2: the tree holds the text; the proposal has no commit for step 1 to find)
        put(w, "hello", keep_worktree=True)
        again = until(w, run(w)["run_id"], ("failed", "done", "blocked"))
        assert (again["state"], again["cause"]) == ("failed", "start") and again["reason"].startswith("a proposed")
        # the refusal names the ways out, on the run's row and among the
        # starts refused, which the page shows
        assert again["reason"].endswith(si.KEPT_WAY_OUT)
        assert w.http.get("/skill-improve/record").json()["refused"][-1]["why"].endswith(si.KEPT_WAY_OUT)
        # its way out: the kept worktree turned off, each run from its base in a worktree of its own
        put(w, "hello", keep_worktree=False)
        out = until(w, run(w)["run_id"], ("failed", "done", "blocked"))
        assert out["state"] == "done", out
    finally:
        w.stop()


def test_a_kept_worktree_with_skip_worktree_or_assume_unchanged_is_still_refused(tmp_path):
    from test_agents_runs import put, run, until
    from proofpane_workspace import agents as agentry
    w, repo, base = agents_workspace(tmp_path, lambda repo, base: [proposal(blob_id(PROPOSED), [], base)])
    try:
        put(w, "hello", keep_worktree=True)
        assert until(w, run(w)["run_id"], ("failed", "done", "blocked"))["state"] == "done"
        work = os.path.join(w.root, "agents", "a1a2a3a4a5a6", "work")
        for flag in ("--skip-worktree", "--assume-unchanged"):
            git("update-index", flag, PATH, cwd=work)
            write(work, PATH, PROPOSED)                 # left on disk, never staged by git add --all
            got = until(w, run(w)["run_id"], ("failed", "done", "blocked"))
            assert (got["state"], got["cause"]) == ("failed", "start") and got["reason"].startswith("a proposed")
            write(work, PATH, CURRENT)
            git("update-index", flag.replace("--", "--no-"), PATH, cwd=work)
    finally:
        w.stop()


def test_an_existing_branch_moved_between_the_check_and_the_add_is_refused(controlled, monkeypatch):
    w, repo, base, m = controlled
    agents = w.agents
    agents.put({"id": "a1a2a3a4a5a6", "name": "Triage", "brief": "hello", "repo": "demo", "keep_worktree": True,
                "author": {"name": "Ana", "email": "ana@example.org"}})
    agent = agents.agent("a1a2a3a4a5a6")
    first = agents.queue("a1a2a3a4a5a6")["run_id"]
    agents._prepare(first, agent)
    work = agents.work(first, agent)
    git("worktree", "remove", "--force", work, cwd=repo)       # the kept branch stays, its worktree gone
    c = proposal_commit(repo, base)
    record_it(w.skill_improve, "demo", proposal(blob_id(PROPOSED), [c], base))
    real = w.skill_improve.check_start
    branch = agents.branch(first, agent)

    def moved_after(repo_name, *args, **kwargs):
        got = real(repo_name, *args, **kwargs)
        if kwargs.get("commit") == "refs/heads/" + branch:
            git("update-ref", "refs/heads/" + branch, c, cwd=repo)     # a program moves it just after the check
        return got

    monkeypatch.setattr(w.skill_improve, "check_start", moved_after)
    second = agents.queue("a1a2a3a4a5a6")["run_id"]
    with pytest.raises(Exception, match="a proposed change to"):
        agents._prepare(second, agent)


# step 1 at each start of an agent's run beside an
# existing branch added again: a new worktree from its agent's base, a kept worktree reused, a sandbox arm and a
# replay. Each from a commit above a proposal's whose text the branch held before (fresh false), so that step 1 alone
# can find it

def above_a_proposal(repo, base):
    c = proposal_commit(repo, base)
    return [proposal(blob_id(PROPOSED), [c], base, fresh=False)]


def refused_at_start(got):
    return (got["state"], got["cause"]) == ("failed", "start") and got["reason"].startswith("a proposed change to")


def test_an_agents_run_in_a_new_worktree_from_a_base_above_a_proposals_commit_is_refused(tmp_path):
    from test_agents_runs import put, run, until
    w, repo, base = agents_workspace(tmp_path, above_a_proposal)
    try:
        put(w, "hello", keep_worktree=False, base_ref="proposal")
        assert refused_at_start(until(w, run(w)["run_id"], ("failed", "done", "blocked")))
        put(w, "hello", keep_worktree=False, base_ref="main")
        assert until(w, run(w)["run_id"], ("failed", "done", "blocked"))["state"] == "done"
    finally:
        w.stop()


def test_an_agents_kept_worktree_moved_onto_a_proposals_commit_between_runs_is_refused(tmp_path):
    from test_agents_runs import AGENT, put, run, until
    w, repo, base = agents_workspace(tmp_path, above_a_proposal)
    try:
        put(w, "hello", keep_worktree=True)
        assert until(w, run(w)["run_id"], ("failed", "done", "blocked"))["state"] == "done"
        work = os.path.join(w.root, "agents", AGENT, "work")
        git("reset", "-q", "--hard", "proposal", cwd=work)        # a program moves the kept worktree's branch there
        assert refused_at_start(until(w, run(w)["run_id"], ("failed", "done", "blocked")))
    finally:
        w.stop()


def test_a_sandbox_arm_from_a_proposals_commit_is_refused(tmp_path):
    from test_agents_improve import PROPOSAL, sandbox
    from test_agents_runs import AGENT, put, run, until
    w, repo, base = agents_workspace(tmp_path, above_a_proposal)
    try:
        put(w, "hello", keep_worktree=False)
        first = until(w, run(w)["run_id"], ("failed", "done", "blocked"))
        assert first["state"] == "done"
        c = git("rev-parse", "proposal", cwd=repo)
        for start, refused in ((c, True), (base, False)):
            r = w.http.post(f"/agents/{AGENT}/runs", json={"trigger": {
                "kind": "sandbox", "of": first["run_id"], "arm": "control", "proposal": PROPOSAL,
                "sandbox": sandbox(start, "hello")}})
            assert r.status_code == 200, r.text
            got = until(w, r.json()["run_id"], ("failed", "done", "blocked"))
            assert refused_at_start(got) is refused, got
    finally:
        w.stop()


def test_a_replay_of_a_run_made_from_a_proposals_commit_before_the_record_named_it_is_refused(tmp_path):
    from proofpane_workspace import agents as agentry
    from test_agents_runs import AGENT, put, run, until
    from workspace_fixtures import SECRET
    w, repo, base = agents_workspace(tmp_path, lambda repo, base: [])
    try:
        c = proposal_commit(repo, base)
        put(w, "hello", keep_worktree=False, base_ref="proposal")
        first = until(w, run(w)["run_id"], ("failed", "done", "blocked"))
        assert (first["state"], first["base"]) == ("done", c)
        w.stop()
        signed_record(w.root, agentry.records_key(SECRET), [proposal(blob_id(PROPOSED), [c], base, fresh=False)])
        w.start()
        r = w.http.post(f"/agents/{AGENT}/runs", json={"trigger": {"kind": "replay", "of": first["run_id"]}})
        assert r.status_code == 200, r.text
        assert refused_at_start(until(w, r.json()["run_id"], ("failed", "done", "blocked")))
    finally:
        w.stop()


# ---- a task's attempt: check 5's bar and the bar again before its work phase -----------------------------------------

def task_workspace(tmp_path, proposals):
    """test_task_attempt's workspace (a real workspace agent whose tasks run the stand-in claude), stopped while
    `proposals(repo, base)` gives its `demo` what a test needs and returns what the record of skill proposals holds,
    signed with the agent's own key, and then started again: (the workspace, demo's folder, demo's first commit)."""
    from proofpane_workspace import agents as agentry
    from test_task_attempt import TaskWorkspace
    w = TaskWorkspace(tmp_path)
    w.stop()
    repo = os.path.join(w.root, "repos", "demo")
    base = git("rev-parse", "HEAD", cwd=repo)
    signed_record(w.root, agentry.records_key(w.secret), proposals(repo, base))
    w.start()
    return w, repo, base


def blocked_by_the_bar(view):
    """Whether a task's attempt ended blocked by check 5's `worktree` with the bar's words, as a start failure the
    same on every account (task_shapes.START_FAILURES), which the app meets by holding the task for its owner."""
    task = view["task"]
    return (view["state"], view["cause"]) == ("blocked", "resume") and task["resume"]["check"] == "worktree" and \
        task["resume"]["found"]["bar"].startswith(f"a proposed change to {PATH} that the owner has not merged") and \
        task["start"]["failure"] == "same_everywhere"


def test_a_first_attempt_from_a_base_holding_a_proposals_commit_is_blocked_as_every_account_would_be(tmp_path):
    from test_task_attempt import CONFIRMED, account_file, claude_starts, put_agent, queue, task_work, until

    def merged_by_a_session(repo, base):
        write(repo, PATH, PROPOSED)
        return [proposal(blob_id(PROPOSED), [commit_all(repo, "the proposal's commit, merged by a session")], base)]

    w, repo, base = task_workspace(tmp_path, merged_by_a_session)
    try:
        account_file(w)
        put_agent(w, brief="append line.txt=one ;; complete step one")
        got = until(w, queue(w, identity=CONFIRMED)["run_id"])
        assert blocked_by_the_bar(got), (got["cause"], got.get("reason"), got["task"].get("resume"),
                                         got["task"].get("start"))
        # blocked, never `failed` with the cause `start`: _make wrote the worktree and refused nothing, at demo's head
        assert git("rev-parse", "HEAD", cwd=task_work(w)) == git("rev-parse", "HEAD", cwd=repo)
        assert claude_starts(w) == []                   # no claude started for it
        refused = w.http.get("/skill-improve/record").json()["refused"]
        assert [r["kind"] for r in refused] == ["attempt"]
    finally:
        w.stop()


def filled_after_check_5(w):
    """An attempt of the task whose frozen start is slow to read the task's state, its first message kept first, a
    program filling the worktree with the proposal's text meanwhile, check 5 passed: its run's view at its end."""
    from test_task_attempt import CONFIRMED, account_file, put_agent, queue, task_work, until
    account_file(w)
    (w.home / "stand-in-attempt.json").write_text(json.dumps({"frozen": {"delay": 5}}))
    put_agent(w, brief="append line.txt=one ;; complete step one")
    run_id = queue(w, identity=CONFIRMED)["run_id"]
    deadline = time.time() + 60
    while not list(w.home.glob("stand-in-first-*.txt")):
        assert time.time() < deadline, "the frozen start never came"
        time.sleep(0.05)
    write(task_work(w), PATH, PROPOSED)
    return until(w, run_id)


def test_an_attempt_whose_worktree_a_program_fills_after_check_5_is_refused_before_its_work_phase(tmp_path):
    from test_task_attempt import claude_starts
    w, repo, base = task_workspace(tmp_path, lambda repo, base: [proposal(blob_id(PROPOSED), [], base)])
    try:
        got = filled_after_check_5(w)
        # refused by the bar as at check 5; and the end, which commits what the worktree holds as the attempt's end
        # state, finds a file a next run loads changed, so the run's cause is the watch's, which the app reads first
        # and holds the task for as well (tasks._held_by_end)
        assert blocked_by_the_bar(dict(got, cause="resume")), (got["task"].get("resume"), got["task"].get("start"))
        assert (got["cause"], got["task"]["watch"]["changed"]) == ("watch", [PATH])
        (frozen,) = claude_starts(w)                    # the frozen start alone: the work phase never started
        assert "PP_FROZEN" in frozen["env"]
        refused = w.http.get("/skill-improve/record").json()["refused"]
        assert [r["kind"] for r in refused] == ["attempt"]
    finally:
        w.stop()


def test_a_later_attempt_is_judged_by_the_head_its_expect_names(tmp_path):
    from test_task_attempt import claude_starts, next_attempt, resume_agent, task_work
    w, repo, base = task_workspace(tmp_path, lambda repo, base: [proposal(blob_id(PROPOSED), [], base)])
    try:
        first = filled_after_check_5(w)
        end = first["task"]["end_state"]
        # what the program left, the proposal's text in the skill, committed as the attempt's end state
        assert end["committed"] and git("rev-parse", f"{end['head']}:{PATH}", cwd=task_work(w)) == blob_id(PROPOSED)
        resume_agent(w)                                 # the owner's resume, as the app asks after the watch
        starts = len(claude_starts(w))
        later = next_attempt(w, first)
        # its check 5 finds the head its packet expects, and the bar then reads that head, which holds the text
        assert blocked_by_the_bar(later), (later["cause"], later.get("reason"), later["task"].get("resume"),
                                           later["task"].get("start"))
        assert git("rev-parse", "HEAD", cwd=task_work(w)) == end["head"]
        assert len(claude_starts(w)) == starts          # no claude started for it
        refused = w.http.get("/skill-improve/record").json()["refused"]
        assert [r["kind"] for r in refused] == ["attempt", "attempt"]
    finally:
        w.stop()


# ---- a held record, restores and walks; merges and pull requests on GitHub; a path's foldings ------------------------

TEXT_X = PROPOSED.replace("one line a change", "a line for each change")
TEXT_Y = PROPOSED.replace("one line a change", "the changes in a list")


def _file(improve):
    with open(improve.proposals.path, "rb") as fh:
        return fh.read()


def _put(improve, data):
    with open(improve.proposals.path, "wb") as fh:
        fh.write(data)


def _b64(data):
    return base64.b64encode(data).decode()


def _first_lifetime(made):
    """The app has read generation 1; reviews X and Y write generations 2 and 3, which the app never reads; a program
    keeps generation 3 and puts generation 1 back, and the workspace starts again, the app's word generation 1. (the
    workspace started again, the bytes of generation 1 and of generation 3)"""
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    improve.proposals.change(lambda r: r["switches"].update(demo={"on": True}))
    first = _file(improve)
    record_it(improve, "demo", proposal(blob_id(TEXT_X), [], base, pid="c" * 12))
    second = _file(improve)
    record_it(improve, "demo", proposal(blob_id(TEXT_Y), [], base, pid="e" * 12))
    third = _file(improve)
    _put(improve, first)
    again = si.SkillImprove(ws)
    again.proposals.take_word(1, _b64(first))
    return again, first, second, third


def _z_read(again, made):
    """Review Z's proposal recorded in the workspace started again, generation 2, and the app's read of it. (Z's
    file, a commit holding Z's text, made by a session)"""
    ws, improve, repo, base = made
    record_it(again, "demo", proposal(blob_id(PROPOSED), [], base, pid="d" * 12))
    z_file = _file(again)
    again.proposals.take_word(2, _b64(z_file))
    return z_file, proposal_commit(repo, base)


def test_another_file_of_the_generation_the_app_read_put_back_after_a_restart_is_held(made):
    again, first, second, third = _first_lifetime(made)
    z_file, c = _z_read(again, made)
    assert again.check_start("demo", c).startswith("a proposed change to")
    _put(again, second)                                 # generation 2 as well, its signature good, and no Z in it
    assert again.check_start("demo", c) == si.RECORD_HELD


def test_a_later_generation_from_before_a_restart_put_back_is_held(made):
    again, first, second, third = _first_lifetime(made)
    z_file, c = _z_read(again, made)
    _put(again, third)                                  # generation 3, above any seen since the start, and no Z
    assert again.check_start("demo", c) == si.RECORD_HELD


def test_at_a_start_a_record_above_the_apps_copy_that_lacks_part_of_it_is_held(made):
    ws, improve, repo, base = made
    again, first, second, third = _first_lifetime(made)
    z_file, c = _z_read(again, made)
    _put(again, third)
    once_more = si.SkillImprove(ws)                     # started again: generation 3 is above the app's copy
    once_more.proposals.take_word(2, _b64(z_file))
    assert once_more.check_start("demo", c) == si.RECORD_HELD


def test_at_a_start_a_record_written_after_the_apps_copy_and_holding_it_is_believed(made):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    record_it(improve, "demo", proposal(blob_id(TEXT_X), [], base, pid="c" * 12))
    copy = _file(improve)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base, pid="d" * 12))
    again = si.SkillImprove(ws)
    again.proposals.take_word(1, _b64(copy))
    assert again.check_start("demo", proposal_commit(repo, base)).startswith("a proposed change to")
    assert again.proposals.state()[1]["generation"] == 2


def test_a_word_whose_copy_is_another_file_of_a_generation_the_workspace_wrote_holds_the_record(made):
    ws, improve, repo, base = made
    again, first, second, third = _first_lifetime(made)
    z_file, c = _z_read(again, made)
    again.proposals.take_word(2, _b64(second))          # not the generation 2 this workspace wrote
    assert again.check_start("demo", c) == si.RECORD_HELD


def test_no_object_is_made_while_the_record_is_held(turns, linked):
    ws, improve, own, base = linked
    p = made_proposal(turns, improve)
    clone = os.path.join(ws.root, "skill-improve", "b" * 12, "clone")
    os.unlink(improve.proposals.path)
    with pytest.raises(si.SkillImproveError, match="removed or changed"):
        improve.objects(p["id"], {"text": PROPOSED})
    assert subprocess.run(["git", "cat-file", "-e", blob_id(PROPOSED)], cwd=clone).returncode != 0


def test_no_object_is_made_where_the_record_does_not_hold_the_proposal(turns, linked):
    ws, improve, own, base = linked
    before = _file(improve)
    p = made_proposal(turns, improve)
    clone = os.path.join(ws.root, "skill-improve", "b" * 12, "clone")
    improve.proposals.restore(_b64(before))             # the owner's restore from a copy read before the review
    with pytest.raises(si.SkillImproveError, match="no such proposal"):
        improve.objects(p["id"], {"text": PROPOSED})
    assert subprocess.run(["git", "cat-file", "-e", blob_id(PROPOSED)], cwd=clone).returncode != 0


def test_no_commit_is_published_while_the_record_is_held(turns, linked):
    ws, improve, own, base = linked
    p = made_proposal(turns, improve)
    head = improve.objects(p["id"], {"text": PROPOSED})["head"]
    os.unlink(improve.proposals.path)
    with pytest.raises(si.SkillImproveError, match="removed or changed"):
        improve.publish(p["id"])
    ref = f"refs/proofpane/skill-proposals/{'b' * 12}"
    assert subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref], cwd=own).returncode != 0
    assert subprocess.run(["git", "cat-file", "-e", head], cwd=own).returncode != 0


def test_a_publish_whose_proposal_was_withdrawn_meanwhile_leaves_no_ref(turns, linked, monkeypatch):
    ws, improve, own, base = linked
    p = made_proposal(turns, improve)
    improve.objects(p["id"], {"text": PROPOSED})
    real = si._git

    def git_(*args, **kw):
        got = real(*args, **kw)
        if args and args[0] == "fetch":                 # the switch turned off while the fetch ran
            improve.proposals.change(lambda r: r["repos"]["demo"][0].update(state="withdrawn"))
        return got

    monkeypatch.setattr(si, "_git", git_)
    with pytest.raises(si.SkillImproveError, match="withdrawn"):
        improve.publish(p["id"])
    ref = f"refs/proofpane/skill-proposals/{'b' * 12}"
    assert subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref], cwd=own).returncode != 0


def test_a_restore_keeps_each_commit_this_workspace_recorded_for_a_proposal_of_the_copy(turns, linked):
    ws, improve, own, base = linked
    p = made_proposal(turns, improve)
    copy = _b64(_file(improve))                         # the app's copy: the proposal, with no commit yet
    head = improve.objects(p["id"], {"text": PROPOSED})["head"]
    clone = os.path.join(ws.root, "skill-improve", "b" * 12, "clone")
    git("push", "-q", own, f"{head}:refs/heads/taken", cwd=clone)    # a session takes the commit from the clone
    os.unlink(improve.proposals.path)
    improve.proposals.restore(copy)
    assert improve.proposals.read_file()[1]["repos"]["demo"][0]["commits"] == [head]
    assert improve.check_start("demo", "taken").startswith("a proposed change to")


def test_a_restore_after_a_restart_takes_the_commits_the_app_checked(turns, linked):
    ws, improve, own, base = linked
    p = made_proposal(turns, improve)
    copy = _file(improve)
    head = improve.objects(p["id"], {"text": PROPOSED})["head"]
    clone = os.path.join(ws.root, "skill-improve", "b" * 12, "clone")
    git("push", "-q", own, f"{head}:refs/heads/taken", cwd=clone)    # a session takes the commit from the clone
    os.unlink(improve.proposals.path)
    again = si.SkillImprove(ws)
    again.proposals.take_word(json.loads(copy)["record"]["generation"], _b64(copy))
    assert again.check_start("demo", base) == si.RECORD_HELD
    again.proposals.restore(_b64(copy), commits={p["id"]: [head], "f" * 12: [base]})
    got = again.proposals.read_file()[1]["repos"]["demo"]
    assert [q["commits"] for q in got] == [[head]]      # a proposal the copy does not hold gains nothing
    assert again.check_start("demo", "taken").startswith("a proposed change to")


def test_a_walk_begun_before_the_record_gained_a_commit_marks_nothing_clean(made, monkeypatch):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    x = proposal_commit(repo, base)
    git("merge", "-q", "--no-ff", "-m", "merged", x, cwd=repo)
    head = git("rev-parse", "HEAD", cwd=repo)
    # recorded, its commit not yet: its text is one the branch held, so it bars by its commits alone
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base, fresh=False))
    real = si.object_format

    def racing(path):                                   # the commit recorded between the check's read and its walk
        if not racing.done:
            racing.done = True
            improve.proposals.change(lambda r: r["repos"]["demo"][0]["commits"].append(x))
        return real(path)

    racing.done = False
    monkeypatch.setattr(si, "object_format", racing)
    improve.check_start("demo", head)
    monkeypatch.setattr(si, "object_format", real)
    assert improve.check_start("demo", head).startswith("a proposed change to")


def test_a_prewalk_begun_before_the_record_gained_a_commit_marks_nothing_clean(made, monkeypatch):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    x = proposal_commit(repo, base)
    git("merge", "-q", "--no-ff", "-m", "merged", x, cwd=repo)
    head = git("rev-parse", "HEAD", cwd=repo)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base, fresh=False))
    real = si.object_format

    def racing(path):
        if not racing.done:
            racing.done = True
            improve.proposals.change(lambda r: r["repos"]["demo"][0]["commits"].append(x))
        return real(path)

    racing.done = False
    monkeypatch.setattr(si, "object_format", racing)
    improve.prewalk("demo", head)
    monkeypatch.setattr(si, "object_format", real)
    assert improve.check_start("demo", head).startswith("a proposed change to")


def test_a_merge_on_github_after_a_file_of_the_skills_folder_changed_on_origins_branch_is_refused(
        gh_turns, on_github, tmp_path):
    ws, improve, bare, state, base = on_github
    p, head = published(gh_turns, improve)
    opened = improve.pull_request(p["id"], {"counts": COUNTS})
    work = str(tmp_path / "pusher")
    git("clone", "-q", bare, work, cwd=str(tmp_path))
    write(work, ".claude/skills/release-notes/notes.md", "kept beside the skill\n")
    commit_all(work, "a file beside the skill")
    git("push", "-q", "origin", "main", cwd=work)
    with pytest.raises(si.SkillImproveError, match="outdated"):
        improve.merge(p["id"], {"url": opened["url"], "method": "squash"})
    assert not [c for c in gh_state(state)["calls"] if c[:2] == ["pr", "merge"]]
    assert improve.proposals.read_file()[1]["repos"]["demo"][0]["ended"] is None


def test_a_merge_github_queues_rather_than_makes_leaves_the_bar(gh_turns, on_github):
    ws, improve, bare, state, base = on_github
    p, head = published(gh_turns, improve)
    opened = improve.pull_request(p["id"], {"counts": COUNTS})
    s = gh_state(state)
    s["queue"] = True
    with open(state, "w") as fh:
        json.dump(s, fh)
    with pytest.raises(si.SkillImproveError, match="not merged"):
        improve.merge(p["id"], {"url": opened["url"], "method": "squash"})
    assert improve.proposals.read_file()[1]["repos"]["demo"][0]["ended"] is None
    assert improve.check_start("demo", head).startswith("a proposed change to")


def test_a_pull_request_is_refused_where_the_repositorys_settings_rewrite_origins_address(
        gh_turns, on_github, tmp_path):
    ws, improve, bare, state, base = on_github
    p, head = published(gh_turns, improve)
    evil = str(tmp_path / "served" / "evil" / "demo.git")
    os.makedirs(os.path.dirname(evil))
    git("init", "-q", "--bare", evil, cwd=str(tmp_path))
    git("config", "url.git@github.com:evil/demo.git.pushInsteadOf", "git@github.com:octo/demo.git",
        cwd=ws._repo_path("demo"))
    with pytest.raises(si.SkillImproveError, match="origin's address changed"):
        improve.pull_request(p["id"], {"counts": COUNTS})
    assert git("for-each-ref", cwd=evil) == ""
    assert not [c for c in gh_state(state).get("calls", []) if c[:2] == ["pr", "create"]]


def test_a_pull_request_pushes_no_tag_whatever_the_repositorys_settings_say(gh_turns, on_github):
    ws, improve, bare, state, base = on_github
    repo = ws._repo_path("demo")
    git("tag", "-a", "-m", "private", "v-private", base, cwd=repo)      # a tag of the clone's own, on the base
    git("config", "push.followTags", "true", cwd=repo)
    p, head = published(gh_turns, improve)
    opened = improve.pull_request(p["id"], {"counts": COUNTS})
    refs = git("for-each-ref", "--format=%(refname)", cwd=bare).split()
    assert sorted(refs) == sorted([f"refs/heads/{opened['branch']}", "refs/heads/main"])


def test_turning_a_switch_that_is_on_on_again_keeps_origins_address_and_branch(gh_turns, on_github):
    ws, improve, bare, state, base = on_github
    repo = ws._repo_path("demo")
    git("remote", "set-url", "origin", "git@github.com:evil/demo.git", cwd=repo)
    got = improve.set_switch({"repo": "demo", "on": True, "model": "a-model"})     # Keep this model and account
    assert (got["url"], got["branch"], got["model"]) == ("git@github.com:octo/demo.git", "refs/heads/main", "a-model")
    look = improve.look("demo")
    assert look["ok"] is False and look["why"] == si.ORIGIN_CHANGED
    improve.set_switch({"repo": "demo", "on": False})                               # off and on: the new address
    assert improve.set_switch({"repo": "demo", "on": True})["url"] == "git@github.com:evil/demo.git"


def test_no_proposal_to_go_back_is_made_while_the_switch_is_off(turns, linked):
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    assert improve.accept(p["id"])["merged"] is True
    improve.set_switch({"repo": "demo", "on": False})
    with pytest.raises(si.SkillImproveError, match="switch is off"):
        improve.go_back(p["id"], {"id": "d" * 12, "text": CURRENT})
    assert [q["id"] for q in improve.proposals.read_file()[1]["repos"]["demo"]] == [p["id"]]


def test_the_texts_a_branch_held_are_read_through_checked_objects(linked, tmp_path):
    ws, improve, own, base = linked
    b = improve.read_base("demo", improve.switch_of("demo"))
    clone = si.make_clone(b, str(tmp_path / "loop"))
    assert si.held_blobs(clone, b.head, PATH) == {blob_id(CURRENT)}
    # a program writes a commit holding a text of its choosing and rewrites the base's commit to have it as a parent
    write(own, PATH, PROPOSED)
    planted = commit_all(own, "planted")
    git("fetch", "-q", own, "main", cwd=clone)
    raw = subprocess.run(["git", "cat-file", "commit", b.head], cwd=clone, capture_output=True).stdout
    tree_line, rest = raw.split(b"\n", 1)
    rewrite_loose(clone, b.head, "commit", tree_line + b"\nparent " + planted.encode() + b"\n" + rest)
    # the commit's fields as a check found them before are kept by its id; read anew, the rewritten object is refused
    assert si.held_blobs(clone, b.head, PATH) == {blob_id(CURRENT)}
    si._VERIFIED.clear()
    with pytest.raises(si.CannotTell):
        si.held_blobs(clone, b.head, PATH)


def test_a_start_whose_tree_holds_a_proposals_text_at_a_folding_of_its_path_is_refused(made):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base))
    git("rm", "-q", "-r", "--cached", ".claude", cwd=repo)
    shutil.rmtree(os.path.join(repo, ".claude"))
    write(repo, PATH.replace(".claude", ".Claude").replace("release-notes", "Release-Notes"), PROPOSED)
    c = commit_all(repo, "the skill under another case")
    assert improve.check_start("demo", c).startswith("a proposed change to")


def _restart_on_an_older_copy(made):
    """Generations 1 to 3 written (X, then Y, then the switch), generation 1 put back by a program, and the workspace
    started again on the app's copy of generation 1, which is all the app read; it then writes Z at generation 2, which
    the app has not read. (the workspace started again, the old generation 2's bytes, the old generation 3's, a commit
    holding Z's text)"""
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    record_it(improve, "demo", proposal(blob_id(TEXT_X), [], base, pid="c" * 12))
    first = _file(improve)
    record_it(improve, "demo", proposal(blob_id(TEXT_Y), [], base, pid="e" * 12))
    second = _file(improve)
    improve.proposals.change(lambda r: r["switches"].update(demo={"on": True}))
    third = _file(improve)
    _put(improve, first)
    again = si.SkillImprove(ws)
    again.proposals.take_word(1, _b64(first))
    record_it(again, "demo", proposal(blob_id(PROPOSED), [], base, pid="d" * 12))
    c = proposal_commit(repo, base)
    assert again.check_start("demo", c).startswith("a proposed change to")
    return again, second, third, c


def test_an_old_file_of_the_generation_last_written_holding_all_of_the_apps_copy_is_held(made):
    # the generation is the one this workspace last wrote and holds all the app read: its content alone tells it is not
    # the file written, which held Z
    again, second, third, c = _restart_on_an_older_copy(made)
    _put(again, second)
    assert again.check_start("demo", c) == si.RECORD_HELD


def test_a_later_generation_holding_all_of_the_apps_copy_is_held(made):
    # above every generation this workspace wrote since its start, and holding all the app read: that it is unknown
    # alone tells it is not one written since
    again, second, third, c = _restart_on_an_older_copy(made)
    _put(again, third)
    assert again.check_start("demo", c) == si.RECORD_HELD


def test_a_text_the_branch_held_at_a_folding_of_its_path_is_held(linked, tmp_path):
    # held_blobs follows every entry whose name folds to the path's, as a start's step 2 reads the tree
    ws, improve, own, base = linked
    write(own, PATH.replace(".claude", ".Claude").replace("release-notes", "Release-Notes"), PROPOSED)
    commit_all(own, "the text under another case")
    b = improve.read_base("demo", improve.switch_of("demo"))
    clone = si.make_clone(b, str(tmp_path / "loop"))
    assert si.held_blobs(clone, b.head, PATH) == {blob_id(CURRENT), blob_id(PROPOSED)}


def test_no_pattern_takes_a_name_followed_by_a_newline():
    # `^...$` with .match takes a newline after the last character; each pattern now ends at \Z
    for pattern, good in ((si._ID, "a" * 12), (si._OID["sha1"], "a" * 40), (si._OID["sha256"], "a" * 64),
                          (si._ANY_OID, "a" * 40), (si._ANY_OID, "a" * 64), (si._REPO, "demo"),
                          (si._PATH, PATH), (si._REF, "refs/heads/main")):
        assert pattern.match(good) and not pattern.match(good + "\n"), pattern.pattern
    with pytest.raises(si.SkillImproveError):
        si.check_id("a" * 12 + "\n")
    proposal = {"id": "a" * 12, "kind": "change", "path": PATH, "branch": "refs/heads/main", "blob": "b" * 40,
                "fresh": True, "state": "open", "bases": ["c" * 40], "commits": [], "ended": None}
    record = {"version": si.RECORD_VERSION, "generation": 1, "switches": {}, "repos": {"demo": [proposal]}}
    assert si._sound(record)
    for key, value in (("id", "a" * 12 + "\n"), ("path", PATH + "\n"), ("branch", "refs/heads/main\n"),
                       ("blob", "b" * 40 + "\n"), ("bases", ["c" * 40 + "\n"])):
        assert not si._sound(dict(record, repos={"demo": [dict(proposal, **{key: value})]})), key
    assert not si._sound(dict(record, repos={"demo\n": [proposal]}))


# ---- starts with a replace ref or a graft, and a start of each kind before the word ----------------------------------

def replaced(repo, base, how):
    """A session's program's replace ref or graft in `repo`: the base's skill blob, or its skill folder's tree, read as
    the proposal's by anything that honours replace refs; or a graft giving the base a parent it does not have."""
    proposed = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo, input=PROPOSED.encode(),
                              capture_output=True, check=True).stdout.decode().strip()
    if how == "blob":
        git("replace", "-f", blob_id(CURRENT), proposed, cwd=repo)
    elif how == "tree":
        folder = git("rev-parse", f"{base}:.claude/skills/release-notes", cwd=repo)
        made_tree = subprocess.run(["git", "mktree"], cwd=repo, capture_output=True, check=True,
                                   input=f"100644 blob {proposed}\tSKILL.md\n".encode()).stdout.decode().strip()
        git("replace", "-f", folder, made_tree, cwd=repo)
    else:
        tree = git("rev-parse", f"{base}^{{tree}}", cwd=repo)
        other = git("commit-tree", tree, "-m", "a commit the base never had for its parent", cwd=repo)
        with open(os.path.join(repo, ".git", "info", "grafts"), "w") as fh:
            fh.write(f"{base} {other}\n")
    if how != "graft":
        assert git("show", f"{base}:{PATH}", cwd=repo) == PROPOSED.rstrip("\n")     # what a checkout would write


@pytest.mark.parametrize("how", ["blob", "tree", "graft"])
def test_a_controllers_start_writes_the_owners_skill_whatever_a_replace_ref_or_a_graft_says(controlled, how):
    # a proposal whose text the branch held before is barred by its commit alone: a checkout that honoured a replace
    # ref would write its text and start, the bar never asked
    from test_controller_workspace import plan, start
    w, repo, base, m = controlled
    c = proposal_commit(repo, base)
    record_it(w.skill_improve, "demo", proposal(blob_id(PROPOSED), [c], base, fresh=False))
    replaced(repo, base, how)
    got = start(w, plan(w, m))
    with open(os.path.join(w._work(got["session"]), PATH)) as fh:
        assert fh.read() == CURRENT


def test_an_agents_run_writes_the_owners_skill_whatever_a_replace_ref_says(tmp_path):
    # the agents' run's worktree written with the HISTORY guards, as its precheck reads it
    from test_agents_runs import put, run, until
    seen = str(tmp_path / "seen.md")

    def proposals(repo, base):
        c = proposal_commit(repo, base)
        replaced(repo, base, "blob")
        return [proposal(blob_id(PROPOSED), [c], base, fresh=False)]

    w, repo, base = agents_workspace(tmp_path, proposals)
    try:
        put(w, "hello", precheck=f"cp {PATH} {seen}", keep_worktree=False)
        got = until(w, run(w)["run_id"], ("failed", "done", "blocked"))
        with open(seen) as fh:
            assert fh.read() == CURRENT, got
    finally:
        w.stop()


def test_a_tasks_first_attempt_writes_the_owners_skill_whatever_a_replace_ref_says(tmp_path):
    # a task's first attempt's worktree written with the HISTORY guards
    from test_task_attempt import CONFIRMED, account_file, put_agent, queue, task_work, until

    def proposals(repo, base):
        write(repo, PATH, CURRENT)                  # demo's base holding the owner's skill
        base = commit_all(repo, "the owner's skill")
        c = proposal_commit(repo, base)
        replaced(repo, base, "blob")
        return [proposal(blob_id(PROPOSED), [c], base, fresh=False)]

    w, repo, base = task_workspace(tmp_path, proposals)
    try:
        account_file(w)
        put_agent(w, brief="append line.txt=one ;; complete step one")
        got = until(w, queue(w, identity=CONFIRMED)["run_id"])
        with open(os.path.join(task_work(w), PATH)) as fh:
            assert fh.read() == CURRENT, (got["state"], got.get("cause"), got.get("reason"))
    finally:
        w.stop()


def no_word_at_start(monkeypatch):
    """Workspace agents started in a process of their own without the app's word (workspace_fixtures.Agent)."""
    import functools
    import test_agents_runs
    import workspace_fixtures
    agent = functools.partial(workspace_fixtures.Agent, word=False)
    monkeypatch.setattr(workspace_fixtures, "Agent", agent)
    monkeypatch.setattr(test_agents_runs, "Agent", agent)


def test_before_the_word_an_agents_run_waits_and_goes_on_when_it_comes(tmp_path, monkeypatch):
    # the wait in the agents' start, not only in the controller's
    from test_agents_runs import put, run, until, view
    no_word_at_start(monkeypatch)
    w, repo, base = agents_workspace(tmp_path, lambda repo, base: [])
    try:
        put(w, "hello", keep_worktree=False)
        run_id = run(w)["run_id"]
        time.sleep(1)
        assert view(w, run_id)["state"] not in ("failed", "done", "blocked")
        assert w.http.put("/skill-improve/record", json={"generation": 0}).status_code == 200
        assert until(w, run_id, ("failed", "done", "blocked"))["state"] == "done"
    finally:
        w.stop()


def test_before_the_word_a_tasks_attempt_waits_and_goes_on_when_it_comes(tmp_path, monkeypatch):
    # the wait in a task's attempt's start
    from test_task_attempt import CONFIRMED, account_file, put_agent, queue, run_view, task_work, until
    no_word_at_start(monkeypatch)
    w, repo, base = task_workspace(tmp_path, lambda repo, base: [])
    try:
        account_file(w)
        put_agent(w, brief="append line.txt=one ;; complete step one")
        run_id = queue(w, identity=CONFIRMED)["run_id"]
        deadline = time.time() + 3
        while time.time() < deadline and not os.path.exists(task_work(w)):
            time.sleep(0.1)
        # nothing of the task read or made before the word: its worktree, made at its first attempt, is not there
        assert not os.path.exists(task_work(w))
        assert run_view(w, run_id)["state"] not in ("done", "failed", "blocked", "stopped", "skipped")
        assert w.http.put("/skill-improve/record", json={"generation": 0}).status_code == 200
        assert until(w, run_id)["state"] == "done"
    finally:
        w.stop()


def test_before_the_word_a_continue_waits_and_goes_on_when_it_comes(controlled):
    # the wait in a continue, before its pacing
    from test_controller_workspace import _passed, _waiting
    w, repo, base, m = controlled
    got, t = _waiting(w, m, int(time.time()) + 3600)
    _passed(w, (got, t))
    w.skill_improve.proposals.has_word = False

    async def go():
        asyncio.get_running_loop().call_later(0.3, w.skill_improve.proposals.take_word, 0)
        return await w.controller.continue_task({"task": got["task"]})

    out = asyncio.run(go())
    assert out["sent"] and t["state"] == "working"


# ---- restores, step 1 at each kind of start, a repository the record does not name, GitHub's automatic merge ---------

def _spoilt_after(improve):
    """A program spoils the record before the app reads it after a tap: held, so the app's copy stays the one before."""
    _put(improve, b"{}")
    assert improve.proposals.view()["state"] == "held"


def test_a_restore_keeps_the_owners_merge_that_a_copy_read_before_it_lacks(turns, linked):
    # the owner's merge, the file spoilt before the app's read, the restore from the copy before the merge
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    before = _file(improve)
    improve.proposals.take_word(improve.proposals.read_file()[1]["generation"], _b64(before))
    assert improve.accept(p["id"])["merged"] is True
    ended = improve.proposals.read_file()[1]["repos"]["demo"][0]["ended"]
    _spoilt_after(improve)
    improve.proposals.restore(_b64(before), commits={p["id"]: [head]}, ends={p["id"]: ended})
    got = improve.proposals.read_file()[1]["repos"]["demo"][0]
    assert (got["ended"], got["state"], got["commits"]) == (ended, "merged", [head])
    assert git("rev-parse", "main", cwd=own) == head
    assert improve.check_start("demo", "refs/heads/main") is None


def test_a_restore_keeps_a_take_the_copy_lacks(turns, linked):
    # the same for a found proposal the owner took as his
    ws, improve, own, base = linked
    found, found_head = published(turns, improve, pid="e" * 12)
    git("merge", "-q", "--ff-only", found_head, cwd=own)           # a session's merge: found, not the owner's
    before = _file(improve)
    improve.take(found["id"])
    ended = improve.proposals.read_file()[1]["repos"]["demo"][0]["ended"]
    assert ended["how"] == "taken"
    _spoilt_after(improve)
    improve.proposals.restore(_b64(before), ends={found["id"]: ended})
    assert improve.proposals.read_file()[1]["repos"]["demo"][0]["ended"] == ended
    assert improve.check_start("demo", "refs/heads/main") is None


def test_a_restore_of_a_merged_proposal_to_go_back_ends_its_found_one_too(turns, linked):
    # a merged proposal to go back ends the found one it was made for, at a restore as at the merge
    ws, improve, own, base = linked
    other = PROPOSED.replace("one line a change", "a line for each change")
    found, found_head = published(turns, improve, pid="e" * 12, text=other)
    git("merge", "-q", "--ff-only", found_head, cwd=own)           # a session's merge: found, not the owner's
    back = improve.go_back(found["id"], {"id": "f" * 12, "text": CURRENT})["proposal"]
    improve.objects(back["id"], {"text": CURRENT})
    improve.publish(back["id"])
    before = _file(improve)                                         # both on their way, neither ended
    assert improve.accept(back["id"])["merged"] is True
    ended = {q["id"]: q["ended"] for q in improve.proposals.read_file()[1]["repos"]["demo"]}
    assert ended[found["id"]]["how"] == "back"
    _spoilt_after(improve)
    improve.proposals.restore(_b64(before), ends={back["id"]: ended[back["id"]]})     # the app's end of the merge alone
    got = {q["id"]: q for q in improve.proposals.read_file()[1]["repos"]["demo"]}
    assert got[back["id"]]["ended"] == ended[back["id"]] and got[back["id"]]["state"] == "merged"
    assert got[found["id"]]["ended"] == {"how": "back", "at": ended[back["id"]]["at"]}
    assert improve.check_start("demo", "refs/heads/main") is None


def test_a_restore_keeps_the_copys_own_end_and_refuses_ends_of_another_shape(made):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    mine = {"how": "taken", "at": 5}
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base, ended=mine))
    copy = _b64(_file(improve))
    improve.proposals.restore(copy, ends={"a" * 12: {"how": "merged", "at": 9}, "f" * 12: {"how": "merged", "at": 9}})
    (p,) = improve.proposals.read_file()[1]["repos"]["demo"]
    assert (p["ended"], p["state"]) == (mine, "open")                # the copy's own end, as the copy has it
    for bad in ([], {"a" * 12: {"how": "gone", "at": 1}}, {"a" * 12: {"how": "merged", "at": True}},
                {"a" * 12: {"how": "merged"}}, {"xyz": {"how": "merged", "at": 1}},
                {"a" * 12: {"how": "merged", "at": -1}}, {"a" * 12: ["merged", 1]}):
        with pytest.raises(si.SkillImproveError, match="ends the app recorded"):
            improve.proposals.restore(copy, ends=bad)


def test_the_view_says_of_the_file_it_gives_what_the_workspace_believes_of_that_file(made, monkeypatch):
    # a program that puts the file the workspace believes back just after each read of the view's
    again, first, second, third = _first_lifetime(made)
    z_file, c = _z_read(again, made)
    real = si.SkillProposals.read_file

    def read_file(self):
        got = real(self)
        _put(again, z_file)
        return got

    _put(again, third)                                  # generation 3 of the lifetime before, no Z in it
    monkeypatch.setattr(si.SkillProposals, "read_file", read_file)
    view = again.proposals.view()
    monkeypatch.setattr(si.SkillProposals, "read_file", real)
    assert base64.b64decode(view["file"]) == third
    assert (view["holds"], view["generation"], view["state"]) == (True, 3, "held")
    assert "which this workspace did not write since it started" in view["held"]


def _changed_back(repo, base):
    """A branch `proposal` holding the proposal's commit, its text then changed back to the owner's by a later commit:
    the proposal's commit, which only step 1 finds."""
    c = proposal_commit(repo, base)
    git("checkout", "-q", "proposal", cwd=repo)
    write(repo, PATH, CURRENT)
    commit_all(repo, "changed back")
    git("checkout", "-q", "main", cwd=repo)
    return c


BY_THE_COMMIT_ALONE = {
    # a proposal whose text the branch held before (not fresh), on a branch holding its commit
    "not_fresh": lambda repo, base: (proposal_commit(repo, base), False),
    # a fresh proposal on a branch whose later commit changed its text back
    "changed_back": lambda repo, base: (_changed_back(repo, base), True),
}


@pytest.mark.parametrize("how", sorted(BY_THE_COMMIT_ALONE))
def test_a_controllers_start_from_a_base_reaching_a_proposals_commit_is_refused_by_step_1(controlled, how):
    # prepare's own check (steps 1 and 2 before the add), which step 3 does not stand in for here
    from test_controller_workspace import plan, refused
    w, repo, base, m = controlled
    c, fresh = BY_THE_COMMIT_ALONE[how](repo, base)
    record_it(w.skill_improve, "demo", proposal(blob_id(PROPOSED), [c], base, fresh=fresh))
    why = refused(w.controller.start_task, {"requirement": plan(w, m), "repo": "demo", "task": "Edit it.",
                                            "base": "proposal"})
    assert why.startswith("a proposed change to")
    assert w.controller.record["tasks"] == {} and w.sessions() == []
    assert git("worktree", "list", "--porcelain", cwd=repo).count("worktree ") == 1


@pytest.mark.parametrize("how", sorted(BY_THE_COMMIT_ALONE))
def test_a_continue_whose_worktrees_head_reaches_a_proposals_commit_is_refused_by_step_1(controlled, how):
    # the continue's check on its worktree's HEAD
    from test_controller_workspace import _Term, _passed, _waiting, refused
    w, repo, base, m = controlled
    got, t = _waiting(w, m, int(time.time()) + 3600)
    _passed(w, (got, t))
    c, fresh = BY_THE_COMMIT_ALONE[how](repo, base)
    work = w._work(got["session"])
    git("merge", "-q", "--no-edit", "proposal", cwd=work)          # the session's work merged the branch holding it
    with open(os.path.join(work, PATH)) as fh:
        assert fh.read() == (CURRENT if how == "changed_back" else PROPOSED)
    record_it(w.skill_improve, "demo", proposal(blob_id(PROPOSED), [c], base, fresh=fresh))
    why = refused(w.controller.continue_task, {"task": got["task"]})
    assert why.startswith("a proposed change to")
    assert t["state"] == "waiting" and _Term.seen[-1].running is False
    assert w.skill_improve.proposals.refused[-1]["kind"] == "continue"


@pytest.mark.parametrize("how", sorted(BY_THE_COMMIT_ALONE))
def test_a_first_attempt_from_a_base_reaching_a_proposals_commit_is_blocked_by_step_1(tmp_path, how):
    # the attempt's bar on the revision it reads, which step 3 does not stand in for here
    from test_task_attempt import CONFIRMED, account_file, claude_starts, put_agent, queue, until

    def merged_by_a_session(repo, base):
        c, fresh = BY_THE_COMMIT_ALONE[how](repo, base)
        git("merge", "-q", "--no-edit", "proposal", cwd=repo)      # demo's branch, the attempt's base, holds it
        return [proposal(blob_id(PROPOSED), [c], base, fresh=fresh)]

    w, repo, base = task_workspace(tmp_path, merged_by_a_session)
    try:
        account_file(w)
        put_agent(w, brief="append line.txt=one ;; complete step one")
        got = until(w, queue(w, identity=CONFIRMED)["run_id"])
        assert blocked_by_the_bar(got), (got["cause"], got.get("reason"), got["task"].get("resume"))
        assert claude_starts(w) == []
    finally:
        w.stop()


def test_a_later_attempt_whose_expected_head_reaches_a_proposals_commit_is_blocked_by_step_1(tmp_path):
    # a later attempt's bar reads the head its packet expects (the attempt before's end state), whose
    # commit a proposal recorded since then names; its text, one the branch held, bars nothing by itself
    from proofpane_workspace import agents as agentry
    from test_task_attempt import attempt, claude_starts, expect_of, next_attempt, task_work
    w, repo, base = task_workspace(tmp_path, lambda repo, base: [])
    try:
        first = attempt(w, "append line.txt=one ;; complete step one")
        head = expect_of(first)["head"]
        w.stop()
        signed_record(w.root, agentry.records_key(w.secret), [proposal(blob_id(CURRENT), [head], base, fresh=False)])
        w.start()
        starts = len(claude_starts(w))
        later = next_attempt(w, first)
        assert blocked_by_the_bar(later), (later["cause"], later.get("reason"), later["task"].get("resume"))
        assert git("rev-parse", "HEAD", cwd=task_work(w)) == head
        assert len(claude_starts(w)) == starts
    finally:
        w.stop()


def _racing_state(monkeypatch, then):
    """SkillProposals.state, which does `then(proposals)` once, just after the first read it is asked for returns."""
    real = si.SkillProposals.state
    done = []

    def racing(self, *args, **kwargs):
        got = real(self, *args, **kwargs)
        if not done:
            done.append(True)
            then(self)
        return got

    monkeypatch.setattr(si.SkillProposals, "state", racing)
    return real


@pytest.mark.parametrize("walk", ["check_start", "prewalk"])
def test_a_commit_recorded_just_after_a_walks_read_of_the_record_is_not_kept_clean(made, monkeypatch, walk):
    # the walk's epoch is read before the record, so that a commit the record gains in between keeps the
    # commits that walk found from being kept clean, however soon after the read it is recorded
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    x = proposal_commit(repo, base)
    git("merge", "-q", "--no-ff", "-m", "merged", x, cwd=repo)
    head = git("rev-parse", "HEAD", cwd=repo)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base, fresh=False))
    real = _racing_state(monkeypatch, lambda proposals: proposals.change(
        lambda r: r["repos"]["demo"][0]["commits"].append(x)))
    if walk == "check_start":
        assert improve.check_start("demo", head) is None             # it read the record before x was in it
    else:
        improve.prewalk("demo", head)
    monkeypatch.setattr(si.SkillProposals, "state", real)
    assert not any(x in kept for kept in si._CLEAN.values())
    assert improve.check_start("demo", head).startswith("a proposed change to")


def _owners_replacement(repo):
    """The owner's own replace ref in his repository, on the skill's blob: today his checkouts write its text."""
    other = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo, input=PROPOSED.encode(),
                           capture_output=True, check=True).stdout.decode().strip()
    git("replace", "-f", blob_id(CURRENT), other, cwd=repo)


def test_a_controllers_start_in_a_repository_the_record_does_not_name_writes_its_worktree_as_today(controlled):
    # no guard where the record names no proposal in the repository, as an owner's New session there
    from test_controller_workspace import plan, start
    w, repo, base, m = controlled
    record_it(w.skill_improve, "other", proposal(blob_id(PROPOSED), [], base))
    _owners_replacement(repo)
    got = start(w, plan(w, m))
    with open(os.path.join(w._work(got["session"]), PATH)) as fh:
        assert fh.read() == PROPOSED
    rec, _ = w.prepare("demo", "claude", "look")                    # the owner's own New session, as today
    with open(os.path.join(w._work(rec["id"]), PATH)) as fh:
        assert fh.read() == PROPOSED


def test_an_agents_run_in_a_repository_the_record_does_not_name_writes_its_worktree_as_today(tmp_path):
    # the agents' run
    from test_agents_runs import put, run, until
    seen = str(tmp_path / "seen.md")

    def proposals(repo, base):
        _owners_replacement(repo)
        return []

    w, repo, base = agents_workspace(tmp_path, proposals)
    try:
        put(w, "hello", precheck=f"cp {PATH} {seen}", keep_worktree=False)
        got = until(w, run(w)["run_id"], ("failed", "done", "blocked"))
        with open(seen) as fh:
            assert fh.read() == PROPOSED, got
    finally:
        w.stop()


def test_a_tasks_first_attempt_in_a_repository_the_record_does_not_name_writes_its_worktree_as_today(tmp_path):
    # a task's first attempt
    from test_task_attempt import CONFIRMED, account_file, put_agent, queue, task_work, until

    def proposals(repo, base):
        write(repo, PATH, CURRENT)
        commit_all(repo, "the owner's skill")
        _owners_replacement(repo)
        return []

    w, repo, base = task_workspace(tmp_path, proposals)
    try:
        account_file(w)
        put_agent(w, brief="append line.txt=one ;; complete step one")
        got = until(w, queue(w, identity=CONFIRMED)["run_id"])
        with open(os.path.join(task_work(w), PATH)) as fh:
            assert fh.read() == PROPOSED, (got["state"], got.get("cause"), got.get("reason"))
    finally:
        w.stop()


def test_the_guards_are_asked_of_the_record_as_it_stands(made):
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    assert improve.guards("demo") is False                          # never written: no repository named
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base, ended={"how": "merged", "at": 1}))
    assert improve.guards("demo") is True and improve.guards("other") is False      # named, its proposal ended or not
    improve.proposals.has_word = False
    assert improve.guards("other") is True                          # before the word it cannot tell
    improve.proposals.has_word = True
    _put(improve, b"{}")
    assert improve.guards("other") is True                          # nor while the record is held


def _gh_set(state, **values):
    s = gh_state(state)
    s.update(values)
    with open(state, "w") as fh:
        json.dump(s, fh)


@pytest.mark.parametrize("mode", ["queue", "checks_pending"])
def test_a_merge_github_did_not_make_has_gh_turn_its_automatic_merge_off_and_says_what_github_then_says(
        gh_turns, on_github, mode):
    # the turning off asked of gh, its exit code and GitHub's view after it said; a merge queue said unread
    ws, improve, bare, state, base = on_github
    p, head = published(gh_turns, improve)
    opened = improve.pull_request(p["id"], {"counts": COUNTS})
    _gh_set(state, **{mode: True})
    with pytest.raises(si.SkillImproveError) as e:
        improve.merge(p["id"], {"url": opened["url"], "method": "squash"})
    words = str(e.value)
    assert "not merged and its bar stays" in words and "(gh exited 0)" in words
    assert "GitHub's view says its automatic merge is off" in words and "merge queue is not read" in words
    assert ["pr", "merge", opened["url"], "--disable-auto"] in gh_state(state)["calls"]
    assert improve.proposals.read_file()[1]["repos"]["demo"][0]["ended"] is None
    assert improve.check_start("demo", head).startswith("a proposed change to")


def test_an_automatic_merge_gh_did_not_turn_off_is_said_to_be_on(gh_turns, on_github):
    ws, improve, bare, state, base = on_github
    p, head = published(gh_turns, improve)
    opened = improve.pull_request(p["id"], {"counts": COUNTS})
    _gh_set(state, checks_pending=True, disable_fails=True)
    with pytest.raises(si.SkillImproveError) as e:
        improve.merge(p["id"], {"url": opened["url"], "method": "squash"})
    words = str(e.value)
    assert "(gh exited 1)" in words and "automatic merge is still on: turn it off on GitHub" in words
    assert improve.check_start("demo", head).startswith("a proposed change to")


def test_the_words_when_githubs_view_cannot_be_read_again_or_gives_no_word_of_its_automatic_merge():
    assert "GitHub's view could not be read again" in si._not_merged_now(0, None)
    assert "gives no word of its automatic merge" in si._not_merged_now(0, {"merged": False})
    assert "automatic merge is off" in si._not_merged_now(2, {"merged": False, "auto_merge": None})
    assert "(gh exited 2)" in si._not_merged_now(2, {"merged": False, "auto_merge": None})


def test_no_critic_runs_while_the_controllers_pacing_holds_its_accounts_window(turns, linked, monkeypatch):
    # the critic reads the window again, the review's read being a round or more before
    ws, improve, own, base = linked
    p = made_proposal(turns, improve)
    calls = len(turns.calls)
    monkeypatch.setattr(improve, "_window_held", lambda account_id: "its window is held until 12:00 UTC")
    with pytest.raises(si.WindowHeld, match="No critic runs now: its window is held until 12:00 UTC"):
        asyncio.run(improve.critic(p["id"], {"text": PROPOSED, "hypothesis": p["hypothesis"]}))
    assert len(turns.calls) == calls                             # no turn, the probe's among them
    monkeypatch.setattr(improve, "_window_held", lambda account_id: None)
    turns.replies = [json.dumps({"specific": False, "why": "general"})]
    assert asyncio.run(improve.critic(p["id"], {"text": PROPOSED, "hypothesis": p["hypothesis"]}))["specific"] is False


def test_a_held_window_is_answered_503_which_the_app_takes_as_a_try_again():
    # the route's answer, not a refusal (the app's _transient reads any status but 400, 404, 409 and 412)
    from starlette.testclient import TestClient
    from proofpane_workspace import agent

    class Held:
        async def critic(self, pid, body):
            raise si.WindowHeld("No critic runs now: its window is held")

    class Stub:
        skill_improve = Held()

        def touch(self):
            pass

        def page(self):
            pass

    secret = "s" * 32
    client = TestClient(agent.make_app(Stub(), secret))             # no lifespan: the route alone
    r = client.post(f"/skill-improve/reviews/{'b' * 12}/critic", json={"text": "x", "hypothesis": "y"},
                    headers={"Authorization": "Bearer " + secret})
    assert (r.status_code, r.json()["detail"]) == (503, "No critic runs now: its window is held")


def test_a_continues_walk_is_made_before_the_start_lock(controlled, monkeypatch):
    # the walk of its worktree's HEAD before the lock, so that the check under it stops at the commits walked
    from test_controller_workspace import _passed, _waiting
    w, repo, base, m = controlled
    got, t = _waiting(w, m, int(time.time()) + 3600)
    _passed(w, (got, t))
    record_it(w.skill_improve, "demo", proposal(blob_id(PROPOSED), [], base, fresh=False))
    held = []
    real = w.skill_improve.prewalk

    def walk(repo_name, commit, where=None):
        held.append((w.controller._start_lock().locked(), commit, where))
        return real(repo_name, commit, where)

    monkeypatch.setattr(w.skill_improve, "prewalk", walk)
    out = asyncio.run(w.controller.continue_task({"task": got["task"]}))
    work = w._work(got["session"])
    assert out["sent"] and held == [(False, "HEAD", work)]
    assert git("rev-parse", "HEAD", cwd=work) in si._CLEAN[frozenset()]


def test_a_merge_on_github_whose_compare_after_fails_is_said_unchecked(gh_turns, on_github, monkeypatch):
    # a diff-tree that fails says nothing was checked, never that nothing came besides
    ws, improve, bare, state, base = on_github
    p, head = published(gh_turns, improve)
    opened = improve.pull_request(p["id"], {"counts": COUNTS})
    real = si._git

    def git_(*args, **kwargs):
        if args and args[0] == "diff-tree":
            return 128, b"", b"fatal: bad object"
        return real(*args, **kwargs)

    monkeypatch.setattr(si, "_git", git_)
    got = improve.merge(p["id"], {"url": opened["url"], "method": "squash"})
    assert got["merged"] is True and got["checked"] is False and "could not compare" in got["why"]
    assert "brought_besides" not in got and "first_parent_was_head" not in got
    assert improve.proposals.read_file()[1]["repos"]["demo"][0]["ended"]["how"] == "merged"


def test_a_record_covers_the_apps_copy_only_with_each_of_its_proposals_commits_bases_and_ends():
    # each clause of _covers, which only a file put back at two starts in turn reaches through state()
    p = proposal(blob_id(PROPOSED), ["1" * 40, "2" * 40], "3" * 40)
    p["bases"].append("4" * 40)
    ended = dict(p, ended={"how": "taken", "at": 7})
    copy = {"repos": {"demo": [ended]}}

    def record(**over):
        return {"repos": {"demo": [dict(ended, **over)]}}

    assert si._covers(record(), copy) is True
    assert si._covers(record(commits=["2" * 40, "1" * 40, "5" * 40]), copy) is True          # more, in any order
    assert si._covers(record(commits=["1" * 40]), copy) is False                            # a commit lacking
    assert si._covers(record(bases=["3" * 40]), copy) is False                              # a base lacking
    assert si._covers(record(ended=None), copy) is False                                    # its end lacking
    assert si._covers(record(ended={"how": "merged", "at": 9}), copy) is True               # an end of its own
    for key, value in (("kind", "back"), ("path", ".claude/skills/other/SKILL.md"), ("branch", "refs/heads/x"),
                       ("blob", "6" * 40), ("fresh", False)):
        assert si._covers(record(**{key: value}), copy) is False, key
    assert si._covers({"repos": {"demo": []}}, copy) is False                               # the proposal lacking
    assert si._covers({"repos": {"other": [ended]}}, copy) is False                         # in another repository
    assert si._covers({"repos": {}}, {"repos": {}}) is True


# ---- a repository under a second name, the checks after the move, answers lost and asked again, the look -------------

def git_identity(repo):
    """[device, inode] of a repository's git folder, as the record keeps a proposal's repository."""
    st = os.stat(os.path.join(repo, ".git"))
    return [st.st_dev, st.st_ino]


def another_repository(path):
    """A repository of its own at `path`, holding none of the proposal's commits."""
    os.makedirs(path)
    git("init", "-q", "-b", "main", cwd=path)
    write(path, "README.md", "another\n")
    commit_all(path, "another")


@pytest.mark.parametrize("how", ["link", "git_file", "worktree", "renamed", "git_file_two_spaces",
                                 "git_file_spaces_after", "git_file_bytes", "new_workdir"])
def test_the_same_repository_under_a_second_name_is_barred_and_guarded(made, how):
    # a link to its folder, a .git file naming its git folder, a worktree of it, or its folder renamed with
    # another repository made under its old name, in the repositories' folder: the same repository, which the bar and
    # the guards hold under the second name as under its own. A .git file is read as
    # git reads it, where its path begins or ends with a space or holds bytes that are not UTF-8 (before, its path was
    # read with the spaces at its ends taken off, and its bytes decoded, so that a second name either led to a third
    # repository or to none); and git-new-workdir's layout, a git folder of its own whose refs are a link into the
    # repository's, is the repository (a branch made there is made in its refs)
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    c = proposal_commit(repo, base)
    record_it(improve, "demo", dict(proposal(blob_id(PROPOSED), [c], base), git=git_identity(repo)))
    second = os.path.join(ws.repos_dir, "demo-2")
    if how == "link":
        os.symlink(repo, second)
    elif how == "git_file":
        os.makedirs(second)
        with open(os.path.join(second, ".git"), "w") as fh:
            fh.write(f"gitdir: {os.path.join(repo, '.git')}\n")
    elif how in ("git_file_two_spaces", "git_file_spaces_after", "git_file_bytes"):
        other = os.path.join(ws.repos_dir, "other")
        another_repository(other)                     # what the path read the other way leads to
        os.makedirs(second)
        name = {"git_file_two_spaces": b" x", "git_file_spaces_after": b"x ", "git_file_bytes": b"x\xff"}[how]
        with open(os.path.join(second, ".git"), "wb") as fh:
            fh.write(b"gitdir: " + name + b"\n")
        os.symlink(os.path.join(repo, ".git"), os.path.join(os.fsencode(second), name))
        os.symlink(os.path.join(other, ".git"), os.path.join(second, "x"))
        assert improve.guards("other") is False and improve.check_start("other", "HEAD") is None
    elif how == "new_workdir":
        os.makedirs(os.path.join(second, ".git"))
        for part in ("objects", "refs", "config", "info", "hooks", "logs", "packed-refs"):
            if os.path.exists(os.path.join(repo, ".git", part)):
                os.symlink(os.path.join(repo, ".git", part), os.path.join(second, ".git", part))
        shutil.copy(os.path.join(repo, ".git", "HEAD"), os.path.join(second, ".git", "HEAD"))
        assert git("rev-parse", "proposal", cwd=second) == c
    elif how == "worktree":
        git("worktree", "add", "-q", "--detach", second, base, cwd=repo)
    else:
        os.rename(repo, second)
        os.makedirs(repo)
        git("init", "-q", "-b", "main", cwd=repo)
        write(repo, "README.md", "another\n")
        commit_all(repo, "another")
    assert improve.guards("demo-2") is True
    assert improve.check_start("demo-2", c) == si.BARRED.format(path=PATH, id="a" * 12)
    assert improve.check_start("demo-2", base) is None


def test_a_copy_of_a_barred_repository_is_another_repository(made):
    # the boundary: a copy of its folder has a git folder of its own, so it is another repository, which the bar
    # does not cover, as one the record does not name
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    c = proposal_commit(repo, base)
    record_it(improve, "demo", dict(proposal(blob_id(PROPOSED), [c], base), git=git_identity(repo)))
    shutil.copytree(repo, os.path.join(ws.repos_dir, "demo-copy"), symlinks=True)
    assert improve.guards("demo-copy") is False
    assert improve.check_start("demo-copy", c) is None


@pytest.mark.parametrize("how", ["renamed", "git_file"])
def test_a_name_that_comes_to_lead_to_a_barred_repository_keeps_no_commit_found_clean_before(made, how):
    # commits found clean are kept by the set of barred commits they were
    # found clean against, not by the name's folder. A name that comes to lead to another repository (its folder swapped
    # for that repository's, or its .git made a file naming that repository's git folder) is barred by that
    # repository's commits too with no change to the record, and walks again: a commit M above demo's proposal, found
    # clean in lib against lib's own proposal, is refused once lib's name leads to demo's repository (before, the walk
    # stopped at M, kept clean under lib's folder, until the record next gained a commit)
    ws, improve, demo, base = made
    improve.proposals.take_word(0)
    cd = proposal_commit(demo, base)
    lib = ws._repo_path("lib")
    another_repository(lib)
    lbase = git("rev-parse", "HEAD", cwd=lib)
    git("checkout", "-q", "-b", "lp", cwd=lib)
    write(lib, "LIB.md", "lib's proposal\n")
    cl = commit_all(lib, "lib's proposal")
    git("checkout", "-q", "main", cwd=lib)
    record_it(improve, "demo", dict(proposal(blob_id(PROPOSED), [cd], base), git=git_identity(demo)))
    record_it(improve, "lib", dict(proposal(blob_id("lib's proposal\n"), [cl], lbase, pid="b" * 12,
                                            path=".claude/skills/other/SKILL.md"), git=git_identity(lib)))
    # in lib, M above demo's proposal commit, its tree holding the owner's text again, so that only step 1 finds it
    git("fetch", "-q", demo, "proposal:fromdemo", cwd=lib)
    git("checkout", "-q", "fromdemo", cwd=lib)
    write(lib, PATH, CURRENT)
    m = commit_all(lib, "M: the owner's text again")
    git("checkout", "-q", "main", cwd=lib)
    assert improve.check_start("lib", m) is None                    # lib is another repository: demo's bar is not its
    assert m in si._CLEAN[frozenset({cl})] and cd in si._CLEAN[frozenset({cl})]
    if how == "renamed":
        os.rename(lib, ws._repo_path("lib-old"))
        os.rename(demo, lib)
        git("fetch", "-q", ws._repo_path("lib-old"), f"{m}:refs/heads/m", cwd=lib)
    else:
        git("fetch", "-q", lib, f"{m}:refs/heads/m", cwd=demo)
        os.rename(os.path.join(lib, ".git"), os.path.join(ws.root, "lib-git-away"))
        with open(os.path.join(lib, ".git"), "w") as fh:
            fh.write(f"gitdir: {os.path.join(demo, '.git')}\n")
    assert improve.check_start("lib", m) == si.BARRED.format(path=PATH, id="a" * 12)


def test_where_git_cannot_tell_whether_a_repository_holds_a_proposals_base_the_bar_is_kept(made, monkeypatch):
    # only git's own word that the base is not there, a commit it reads
    # no object of, takes a recorded identity off a repository; any other error of git keeps the bar
    ws, improve, repo, base = made
    real = si._git

    def git_(*args, **kwargs):
        if args[:2] == ("cat-file", "-e"):
            return 128, b"", "fatal: unable to read the object store\n"
        return real(*args, **kwargs)

    monkeypatch.setattr(si, "_git", git_)
    assert improve._holds(repo, base) is True
    monkeypatch.setattr(si, "_git", real)
    assert improve._holds(repo, base) is True and improve._holds(repo, "0" * 40) is False


def test_a_check_after_a_worktrees_add_reads_the_repository_the_worktree_belongs_to(made, tmp_path):
    # a check given a start's worktree reads the repository that worktree
    # belongs to as well as the one its name leads to now, so that a name made to lead elsewhere after the add keeps
    # the bar of the repository the worktree was written in (here step 3, the proposal's text in the worktree)
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    record_it(improve, "demo", dict(proposal(blob_id(PROPOSED), [], base), git=git_identity(repo)))
    work = str(tmp_path / "wt")
    git("worktree", "add", "-q", "--detach", work, base, cwd=repo)
    write(work, PATH, PROPOSED)
    another_repository(ws._repo_path("lib"))                      # the name the start was asked for leads here now
    assert improve.check_start("lib", "HEAD") is None
    assert improve.check_start("lib", work=work) == si.BARRED.format(path=PATH, id="a" * 12)


@pytest.mark.parametrize("held", [True, False])
def test_a_recorded_identity_bars_a_repository_only_where_it_holds_the_proposals_base(made, held):
    # an identity is a device and an inode, which a git folder made after
    # the barred repository's was deleted can be given again. A repository with the recorded identity is the
    # proposal's where it holds the proposal's first base (as the barred repository cloned again does), and another
    # where it does not, which the bar and the guards leave alone ("and to no other"). The inode's reuse is stood in
    # for by recording the proposal with the identity of the repository under the second name
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    c = proposal_commit(repo, base)
    second = ws._repo_path("second")
    if held:
        git("clone", "-q", "--no-local", repo, second, cwd=ws.root)
    else:
        another_repository(second)
    record_it(improve, "demo", dict(proposal(blob_id(PROPOSED), [c], base), git=git_identity(second)))
    assert improve.guards("second") is held
    if held:
        assert improve.check_start("second", c) == si.BARRED.format(path=PATH, id="a" * 12)
    assert improve.check_start("second", "HEAD") is None


def test_a_proposal_is_recorded_with_its_repositorys_identity_which_holds_its_folder_moved(turns, linked, tmp_path):
    # a review and a proposal to go back record the repository's identity, so that the owner's folder moved
    # and added again under another name, where its old name leads nowhere, is still barred and guarded
    ws, improve, own, base = linked
    ident = git_identity(own)
    p, head = published(turns, improve)
    git("merge", "-q", "--ff-only", head, cwd=own)
    back = improve.go_back(p["id"], {"id": "d" * 12, "text": CURRENT})["proposal"]
    entries = {q["id"]: q for q in improve.proposals.read_file()[1]["repos"]["demo"]}
    assert entries[p["id"]]["git"] == ident and entries[back["id"]]["git"] == ident
    moved = str(tmp_path / "moved")
    os.rename(own, moved)
    os.symlink(moved, ws._repo_path("demo-2"))
    assert improve.guards("demo-2") is True
    assert improve.check_start("demo-2", head) == si.BARRED.format(path=PATH, id=p["id"])


def test_a_proposals_identity_is_two_whole_numbers_or_none():
    # the record's shape takes a proposal's identity only as [device, inode]
    p = proposal(blob_id(PROPOSED), [], "3" * 40)
    assert si._sound_proposal(p) is True
    assert si._sound_proposal(dict(p, git=[1, 2])) is True
    for bad in ([1], [1, 2, 3], ["1", 2], [True, 2], [-1, 2], [1.0, 2], {"dev": 1}, None, "1:2"):
        assert si._sound_proposal(dict(p, git=bad)) is False, bad


def test_the_word_and_the_restore_carry_a_record_past_the_routes_usual_limit(real_ws, tmp_path):
    # the record only grows; its word and its restore are read up to three times the record's own limit, so
    # that after a restart a record of 250 proposals (68,144 bytes, its word 90,891, past MAX_BODY's 64 KiB) has the
    # word, and a restore
    from proofpane_workspace import agent
    from proofpane_workspace.workspace import Workspace
    w, own, base = real_ws
    assert agent.MAX_RECORD_BODY >= 2 * si.RECORD_MAX                 # a record at its own limit, in base64, fits
    assert call(w, "PUT", "/skill-improve/record", {"generation": 0}).status_code == 200
    many = [dict(proposal(blob_id(PROPOSED), [], base, pid=f"{i:012x}"), state="refused") for i in range(250)]
    w.skill_improve.proposals.change(lambda r: r["repos"].update(own=many))
    got = call(w, "GET", "/skill-improve/record").json()
    word = {"generation": got["generation"], "record": got["file"]}
    assert len(json.dumps(word)) > agent.MAX_BODY
    again = Workspace(str(tmp_path / "ws"), link_repos=True, records_key=b"r" * 32)
    answered = call(again, "PUT", "/skill-improve/record", word)
    assert answered.status_code == 200 and answered.json()["state"] == "record"
    assert again.skill_improve.check_start("own", base) is None
    os.unlink(again.skill_improve.proposals.path)
    assert call(again, "GET", "/skill-improve/record").json()["state"] == "held"
    restored = call(again, "POST", "/skill-improve/record/restore", {"record": got["file"]})
    assert restored.status_code == 200 and restored.json() == {"generation": got["generation"] + 1}
    assert call(again, "GET", "/skill-improve/record").json()["state"] == "record"


def after_the_move(monkeypatch, then):
    """`then()` run right after the acceptance's `git merge`, as a session's program could run between the move and the
    checks after it."""
    real = si._git

    def git_(*args, **kwargs):
        got = real(*args, **kwargs)
        if "merge" in args and "--ff-only" in args:
            then()
        return got

    monkeypatch.setattr(si, "_git", git_)


def test_other_bytes_on_disk_after_the_move_are_no_merge(turns, linked, monkeypatch):
    # the check after the move reads the file on disk itself; the index still holding the proposal's blob is
    # not enough
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    after_the_move(monkeypatch, lambda: write(own, PATH, PROPOSED + "a program's line\n"))
    got = improve.accept(p["id"])
    assert got["merged"] is False
    assert improve.proposals.read_file()[1]["repos"]["demo"][0]["ended"] is None


def test_another_index_entry_after_the_move_is_no_merge(turns, linked, monkeypatch):
    # the check after the move reads the index entry, its mode with its blob; one git accepts is not enough
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    after_the_move(monkeypatch, lambda: git("update-index", "--chmod=+x", PATH, cwd=own))
    got = improve.accept(p["id"])
    assert got["merged"] is False
    assert improve.proposals.read_file()[1]["repos"]["demo"][0]["ended"] is None


def test_a_proposals_object_rewritten_after_the_move_is_no_merge(turns, linked, monkeypatch):
    # the check after the move reads the proposal's objects again
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    after_the_move(monkeypatch, lambda: rewrite_loose(own, p["blob"], "blob", b"other text\n"))
    got = improve.accept(p["id"])
    assert got["merged"] is False
    assert improve.proposals.read_file()[1]["repos"]["demo"][0]["ended"] is None


def test_a_filter_planted_after_the_move_keeps_the_undo_from_running_it(turns, linked, monkeypatch, tmp_path):
    # where a check after the move fails, the undo runs only once the work tree's attributes are the base's
    # again; a filter a program named in an ignored .gitattributes after the move is never run by it.
    # The check that fails is the objects' (the proposal's blob rewritten), the file being left as the move wrote
    # it, so that git itself would make the reset, and run the filter
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    ran = str(tmp_path / "smudged")

    def plant():
        rewrite_loose(own, p["blob"], "blob", b"other text\n")
        git("config", "filter.evil.smudge", f"touch {ran}; cat", cwd=own)
        git("config", "filter.evil.clean", "cat", cwd=own)
        write(own, ".claude/skills/release-notes/.gitattributes", "SKILL.md filter=evil\n")
        write(own, ".git/info/exclude", ".gitattributes\n")

    after_the_move(monkeypatch, plant)
    got = improve.accept(p["id"])
    assert got["merged"] is False and got["undone"] is False and got["left"]["branch"] == head
    assert not os.path.exists(ran)


def test_the_sweep_takes_away_a_link_in_the_loops_folders_place_and_nothing_it_leads_to(linked, tmp_path):
    # a link at the loop's folder is taken away itself; what it leads to, and the accounts' folders named
    # after that, are left as they are
    from proofpane_workspace import usage
    ws, improve, own, base = linked
    parent = os.path.join(ws.root, "skill-improve")
    target = str(tmp_path / "target")
    os.makedirs(os.path.join(target, "kept-folder"))
    write(target, "a-file.txt", "kept\n")
    projects = os.path.join(os.environ["HOME"], ".claude", "projects")
    named = os.path.join(projects, usage.slug(os.path.realpath(target)) + "-x-review")
    os.makedirs(named)
    shutil.rmtree(parent)
    os.symlink(target, parent)
    si.SkillImprove(ws)                             # the workspace started again
    assert sorted(os.listdir(target)) == ["a-file.txt", "kept-folder"]
    assert os.path.isdir(named)
    assert not os.path.lexists(parent)


def test_a_folder_at_the_records_name_holds_it_until_the_owners_restore_writes_over_it(made):
    # a folder a program made at the record's name holds the record, and the owner's Restore takes it away
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base))
    copy_b64 = improve.proposals.view()["file"]
    path = improve.proposals.path
    os.unlink(path)
    os.makedirs(os.path.join(path, "inside"))
    assert improve.check_start("demo", base) == si.RECORD_HELD
    assert improve.proposals.restore(copy_b64) == {"generation": 2}
    assert os.path.isfile(path) and improve.proposals.state()[0] == "record"


def test_a_folder_at_the_records_temporary_name_stops_no_write(made):
    # a folder a program made at the name the record is written under first is taken away by the write
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    os.makedirs(os.path.join(improve.proposals.path + ".tmp", "inside"))
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base))
    assert [p["id"] for p in improve.proposals.read_file()[1]["repos"]["demo"]] == ["a" * 12]
    assert not os.path.exists(improve.proposals.path + ".tmp")


def test_an_acceptance_records_its_merge_past_a_folder_at_the_records_temporary_name(turns, linked):
    # the owner's merge, once made, is recorded, the folder notwithstanding
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    os.makedirs(os.path.join(improve.proposals.path + ".tmp", "inside"))
    assert improve.accept(p["id"])["merged"] is True
    assert improve.proposals.read_file()[1]["repos"]["demo"][0]["ended"]["how"] == "merged"
    assert improve.check_start("demo", head) is None


def test_a_link_in_the_loops_folders_place_is_taken_away_by_the_owners_restore(made, tmp_path):
    # a link a program put in the loop's folder's place is never written through; the owner's Restore takes
    # it away and writes the record in a folder of the workspace's own
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base))
    copy_b64 = improve.proposals.view()["file"]
    target = str(tmp_path / "elsewhere")
    os.makedirs(target)
    shutil.rmtree(improve.proposals.dir)
    os.symlink(target, improve.proposals.dir)
    assert improve.check_start("demo", base) == si.RECORD_HELD
    improve.proposals.restore(copy_b64)
    assert not os.path.islink(improve.proposals.dir) and os.listdir(target) == []
    assert improve.proposals.state()[0] == "record"


def test_an_acceptance_whose_merge_the_record_cannot_record_says_the_branch_moved(turns, linked, monkeypatch):
    # where the record cannot say so, the owner is told that his branch moved, and how its bar ends
    ws, improve, own, base = linked
    p, head = published(turns, improve)

    def held(*args):
        raise si.SkillImproveError(si.RECORD_HELD)

    monkeypatch.setattr(improve, "_end", held)
    with pytest.raises(si.SkillImproveError) as e:
        improve.accept(p["id"])
    assert f"refs/heads/main in your copy now names the proposal's commit {head}" in str(e.value)
    assert si.RECORD_HELD in str(e.value) and "Take it as mine" in str(e.value)
    assert git("rev-parse", "main", cwd=own) == head


def test_a_merge_on_github_the_record_cannot_record_says_github_merged_it(gh_turns, on_github, monkeypatch):
    # the same for a merge on GitHub
    ws, improve, bare, state, base = on_github
    p, head = published(gh_turns, improve)
    opened = improve.pull_request(p["id"], {"counts": COUNTS})

    def held(*args):
        raise si.SkillImproveError(si.RECORD_HELD)

    monkeypatch.setattr(improve, "_end", held)
    with pytest.raises(si.SkillImproveError) as e:
        improve.merge(p["id"], {"url": opened["url"], "method": "squash"})
    assert str(e.value).startswith("GitHub merged the pull request, but the workspace could not record the merge")
    assert si.RECORD_HELD in str(e.value) and "Take it as mine" in str(e.value)


def test_a_publish_asked_for_again_after_its_answer_was_lost_answers_again(turns, linked):
    # the publish went through and its answer was lost on the way; asked again, the workspace answers as it
    # did, where the record has the proposal open and its ref names its commit, and only there
    ws, improve, own, base = linked
    turns.replies = [proposed_reply()]
    p = review(improve)["proposal"]
    head = improve.objects(p["id"], {"text": PROPOSED})["head"]
    ref = f"refs/proofpane/skill-proposals/{p['id']}"
    first = improve.publish(p["id"])
    assert first == {"ref": ref, "head": head} and improve.publish(p["id"]) == first
    git("update-ref", ref, base, cwd=own)                               # its ref changed by something else
    with pytest.raises(si.SkillImproveError, match="No review of this proposal"):
        improve.publish(p["id"])
    turns.replies = [proposed_reply(PROPOSED + "One more line.\n")]
    q = review(improve, pid="c" * 12)["proposal"]
    improve.objects(q["id"], {"text": PROPOSED + "One more line.\n"})
    again = si.SkillImprove(ws)                     # the workspace started again before the publish
    again.proposals.take_word(0)
    with pytest.raises(si.SkillImproveError, match="No review of this proposal"):
        again.publish(q["id"])


def test_a_remake_not_published_is_brought_up_to_date_again_published_and_merged(turns, linked):
    # after a remake whose publish did not go through, the ref names the commit before; bringing it up to
    # date again, its publish and the merge go on from there
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    write(own, "README.md", "moved on\n")
    commit_all(own, "another path moves on")
    improve.remake(p["id"])
    ref = f"refs/proofpane/skill-proposals/{p['id']}"
    assert git("rev-parse", ref, cwd=own) == head
    again = improve.remake(p["id"])
    assert again["replaces"] == {"base": base, "head": head}
    improve.publish(p["id"])
    assert git("rev-parse", ref, cwd=own) == again["head"]
    assert improve.accept(p["id"]) == {"merged": True, "head": again["head"], "hooks_ran": False,
                                       "builds": improve.builds("demo")}


def test_a_merge_after_a_remake_not_published_is_told_to_bring_it_up_to_date_and_a_close_removes_its_ref(
        turns, linked):
    # the page's row stays on the commit before; Merge is told the branch moved on, not that the ref was
    # changed, and a close removes the ref that names that commit
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    write(own, "README.md", "moved on\n")
    commit_all(own, "another path moves on")
    improve.remake(p["id"])
    with pytest.raises(si.SkillImproveError, match="bring it up to date first"):
        improve.accept(p["id"])
    assert improve.close(p["id"], "expired")["ref_removed"] is True
    assert git("for-each-ref", "refs/proofpane/", cwd=own) == ""


def test_a_critic_asked_again_while_its_turn_runs_or_after_its_caller_stopped_waiting_runs_no_turn_of_its_own(
        turns, linked, monkeypatch):
    # one critic's turn for a proposal at a time, its answer kept for a call asked again
    from proofpane_workspace import agents as agentry
    ws, improve, own, base = linked
    p = made_proposal(turns, improve)
    body = {"text": PROPOSED, "hypothesis": p["hypothesis"]}
    turns.replies = [json.dumps({"specific": False, "why": "general"})]

    started = []

    async def go():
        gate = asyncio.Event()

        async def slow(ws_, folder, account_id, model, text, extra_args=None):
            if text.startswith(si.SKILL_CRITIC_PROMPT):
                started.append(folder)
                await gate.wait()
            return await turns(ws_, folder, account_id, model, text, extra_args)

        monkeypatch.setattr(agentry, "one_turn", slow)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(improve.critic(p["id"], body), 0.3)      # its caller stops waiting
        together = asyncio.gather(improve.critic(p["id"], body), improve.critic(p["id"], body))
        await asyncio.sleep(0.1)
        gate.set()
        both = await together
        return both, await improve.critic(p["id"], body)                  # after the turn ended

    (one, two), three = asyncio.run(go())
    assert one == two == three and one["specific"] is False
    assert len(started) == 1                        # the turn its first caller stopped waiting for went on, alone
    assert len([c for c in turns.calls if c["text"].startswith(si.SKILL_CRITIC_PROMPT)]) == 1


def test_a_critics_turn_that_failed_is_run_anew(turns, linked):
    # a turn that failed keeps no answer, and the next call runs one of its own
    ws, improve, own, base = linked
    p = made_proposal(turns, improve)
    body = {"text": PROPOSED, "hypothesis": p["hypothesis"]}
    turns.replies = []                                                  # the stand-in has no answer: the turn fails
    with pytest.raises(IndexError):
        asyncio.run(improve.critic(p["id"], body))
    turns.replies = [json.dumps({"specific": True, "why": "it names a file"})]
    assert asyncio.run(improve.critic(p["id"], body))["specific"] is True
    assert len([c for c in turns.calls if c["text"].startswith(si.SKILL_CRITIC_PROMPT)]) == 2


def test_the_look_reads_a_proposals_own_branch_whichever_the_owners_copy_has_checked_out(turns, linked):
    # the owner's copy on another branch, whose skill's folder differs: the proposal on main is neither
    # outdated nor found by what that branch holds
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    git("checkout", "-q", "-b", "side", cwd=own)
    write(own, PATH, CURRENT + "A line of side's own.\n")
    commit_all(own, "side changes the skill")
    seen = improve.look("demo")["proposals"][p["id"]]
    assert seen["head"] == base and seen["folder_unchanged"] is True and seen["reached"] is None
    git("merge", "-q", "--no-ff", "-m", "merge", head, cwd=own, check=False)
    git("checkout", "-q", "--theirs", "--", PATH, cwd=own, check=False)
    git("commit", "-q", "-am", "the proposal merged into side", cwd=own, check=False)
    seen = improve.look("demo")["proposals"][p["id"]]
    assert seen["reached"] is None and seen["head"] == base
    git("checkout", "-q", "main", cwd=own)
    git("merge", "-q", "--ff-only", head, cwd=own)
    seen = improve.look("demo")["proposals"][p["id"]]
    assert seen["reached"] == "commit" and seen["head"] == head


def test_the_look_says_nothing_of_a_ref_git_cannot_read_and_none_of_one_gone(turns, linked, monkeypatch):
    # a ref git cannot read is no word, never a ref removed; one removed reads as none
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    ref = f"refs/proofpane/skill-proposals/{p['id']}"
    real = si._git

    def git_(*args, **kwargs):
        if args[:1] == ("rev-parse",) and any(a.startswith(ref) for a in args):
            return 128, b"", "fatal: unable to read it"
        return real(*args, **kwargs)

    monkeypatch.setattr(si, "_git", git_)
    seen = improve.look("demo")["proposals"][p["id"]]
    assert "ref" not in seen and "ref_names_head" not in seen
    monkeypatch.setattr(si, "_git", real)
    git("update-ref", "-d", ref, cwd=own)
    seen = improve.look("demo")["proposals"][p["id"]]
    assert "ref" in seen and seen["ref"] is None


def test_the_look_walks_no_branch_for_a_proposal_whose_bar_ended(turns, linked, monkeypatch):
    # a proposal merged into the owner's copy, taken or gone back from is not walked; the commit his merge
    # moved his branch to is said as the change's
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    assert improve.accept(p["id"])["merged"] is True
    walked = []
    real_hit, real_since = si.walk_hit, si.since
    monkeypatch.setattr(si, "walk_hit", lambda objects, start, targets, **kw: walked.append(targets) or
                        real_hit(objects, start, targets, **kw))
    monkeypatch.setattr(si, "since", lambda objects, head_, base_, **kw: walked.append(base_) or
                        real_since(objects, head_, base_, **kw))
    seen = improve.look("demo")["proposals"][p["id"]]
    assert walked == [] and seen["ended"]["how"] == "merged" and seen["change_commit"] == head


def test_the_objects_checked_and_the_commits_kept_clean_are_bounded(made, monkeypatch):
    # what a check found is kept up to VERIFIED_MOST objects, the older half then forgotten; the commits
    # kept clean against one set of barred commits up to CLEAN_MOST, then forgotten
    ws, improve, repo, base = made
    monkeypatch.setattr(si, "VERIFIED_MOST", 4)
    monkeypatch.setattr(si, "CLEAN_MOST", 3)
    commits = [base]
    for i in range(6):
        write(repo, "README.md", f"line {i}\n")
        commits.append(commit_all(repo, f"commit {i}"))
    with si.Objects(repo, "sha1") as objects:
        for c in commits:
            si.commit_of(objects, c)
            assert len(si._VERIFIED) <= 4
        assert list(si._VERIFIED)[-1] == commits[-1]
        key = frozenset({"0" * 40})
        si.forget_clean()
        assert si.reaches(objects, commits[1], {"0" * 40}, si.clean_epoch()) is None
        assert len(si._CLEAN[key]) == 2
        assert si.reaches(objects, commits[-1], {"0" * 40}, si.clean_epoch()) is None
        assert key not in si._CLEAN                                # 7 found clean, past 3: its own forgotten
        # kept by the set of barred commits, up to CLEAN_KEYS_MOST sets, the set
        # kept longest then let go; a walk against a set already kept lets none go
        monkeypatch.setattr(si, "CLEAN_KEYS_MOST", 2)
        sets = [frozenset({c * 40}) for c in "123"]
        for barred in sets:
            assert si.reaches(objects, commits[1], set(barred), si.clean_epoch()) is None
        assert list(si._CLEAN) == sets[1:]
        assert si.reaches(objects, commits[2], set(sets[2]), si.clean_epoch()) is None
        assert list(si._CLEAN) == sets[1:] and commits[2] in si._CLEAN[sets[2]]


@pytest.mark.parametrize("level", ["", ".claude", ".claude/skills", ".claude/skills/release-notes"])
def test_accept_is_refused_for_an_ignored_attributes_file_at_each_level_of_the_path(turns, linked, tmp_path, level):
    # a .gitattributes a program put at the root, in .claude, in .claude/skills or in the skill's folder,
    # ignored so that the copy has no changes of its own, is read at each of the four levels before the move
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    ran = str(tmp_path / "smudged")
    git("config", "filter.evil.smudge", f"touch {ran}; cat", cwd=own)
    git("config", "filter.evil.clean", "cat", cwd=own)
    write(own, os.path.join(level, ".gitattributes"), "SKILL.md filter=evil\n")
    write(own, ".git/info/exclude", ".gitattributes\n")
    with pytest.raises(si.SkillImproveError, match="gitattributes"):
        improve.accept(p["id"])
    assert not os.path.exists(ran) and git("rev-parse", "main", cwd=own) == base


def test_a_pull_request_asked_for_again_after_its_answer_was_lost_gives_the_one_opened(gh_turns, on_github):
    # the second tap gets the pull request the first opened, where GitHub refuses a second for the branch
    ws, improve, bare, state, base = on_github
    p, head = published(gh_turns, improve)
    first = improve.pull_request(p["id"], {"counts": COUNTS})
    assert improve.pull_request(p["id"], {"counts": COUNTS}) == first
    assert len(gh_state(state)["prs"]) == 1
    assert improve.merge(p["id"], {"url": first["url"], "method": "squash"})["merged"] is True


@pytest.mark.parametrize("fails", ["the_clone", "the_fetch"])
def test_a_merge_on_github_whose_check_after_cannot_tell_still_gives_the_version_after_it(
        gh_turns, on_github, monkeypatch, fails):
    # the head after the merge, where the loop's fetch gave it, else the commit GitHub's view says the
    # merge made, for the app's watch to read
    ws, improve, bare, state, base = on_github
    p, head = published(gh_turns, improve)
    opened = improve.pull_request(p["id"], {"counts": COUNTS})
    if fails == "the_clone":
        _gh_set(state, pull_override={"merge_commit_sha": None})    # the head must come from the loop's own fetch

        def no_clone(*args, **kwargs):
            raise si.CannotTell("no clone")
        monkeypatch.setattr(si, "make_clone", no_clone)
    else:
        real, calls = si.fetch_head, []

        def fetch(*args, **kwargs):
            calls.append(args)
            if len(calls) > 1:                  # the fetch before the merge goes through, the one after it does not
                raise si.CannotTell("no fetch")
            return real(*args, **kwargs)
        monkeypatch.setattr(si, "fetch_head", fetch)
    got = improve.merge(p["id"], {"url": opened["url"], "method": "squash"})
    assert got["merged"] is True and got["checked"] is False and got["head"] == git("rev-parse", "main", cwd=bare)
    # the version not read from origin's branch is GitHub's view's
    made = gh_state(state)["prs"][0]["made"]
    assert (got["version"], got["version_read"]) == (made, False)


@pytest.mark.parametrize("method, moved_on", [("squash", False), ("merge", False), ("squash", True), ("merge", True)])
def test_the_version_after_a_merge_on_github_is_the_skill_folders_last_changing_commit(
        gh_turns, on_github, method, moved_on):
    # the version the app's watch counts calls at is the one a session's
    # call names, the last commit from origin's head that changed the skill's folder: for a merge
    # commit, the proposal's own commit (git's history simplification passes the merge, which changes nothing of the
    # folder that commit did not); for a squash, the squash's commit; and so where origin's branch moved on, by
    # another path, before the loop's fetch (before, origin's head was given, which neither is)
    ws, improve, bare, state, base = on_github
    p, head = published(gh_turns, improve)
    opened = improve.pull_request(p["id"], {"counts": COUNTS})
    if moved_on:
        _gh_set(state, moved_on=True)
    got = improve.merge(p["id"], {"url": opened["url"], "method": method})
    made, origin = gh_state(state)["prs"][0]["made"], git("rev-parse", "main", cwd=bare)
    assert got["merged"] is True and got["head"] == origin and (origin != made) is moved_on
    assert (got["version"], got["version_read"]) == (head if method == "merge" else made, True)
    assert git("log", "-1", "--format=%H", origin, "--", ".claude/skills/release-notes", cwd=bare) == got["version"]


@pytest.mark.parametrize("method", ["squash", "merge"])
def test_a_merge_on_github_not_fetched_after_gives_the_version_githubs_view_shows_for_its_method(
        gh_turns, on_github, monkeypatch, method):
    # origin's branch not fetched after the merge, the version is taken
    # from GitHub's view and the method, and said not read: the proposal's commit for a merge commit, else the commit
    # GitHub made
    ws, improve, bare, state, base = on_github
    p, head = published(gh_turns, improve)
    opened = improve.pull_request(p["id"], {"counts": COUNTS})
    real, calls = si.fetch_head, []

    def fetch(*args, **kwargs):
        calls.append(args)
        if len(calls) > 1:
            raise si.CannotTell("no fetch")
        return real(*args, **kwargs)

    monkeypatch.setattr(si, "fetch_head", fetch)
    got = improve.merge(p["id"], {"url": opened["url"], "method": method})
    made = gh_state(state)["prs"][0]["made"]
    assert got["head"] == made and (got["version"], got["version_read"]) == (head if method == "merge" else made, False)


def test_a_merge_on_github_is_refused_where_origins_address_changed_since_the_switch(gh_turns, on_github):
    # the merge reads origin's address again, as the pull request does, before it asks GitHub to merge
    ws, improve, bare, state, base = on_github
    p, head = published(gh_turns, improve)
    opened = improve.pull_request(p["id"], {"counts": COUNTS})
    git("remote", "set-url", "origin", "git@github.com:evil/demo.git", cwd=ws._repo_path("demo"))
    with pytest.raises(si.SkillImproveError, match="The merge was not made: " + si.ORIGIN_CHANGED[:30]):
        improve.merge(p["id"], {"url": opened["url"], "method": "squash"})
    assert [pr["state"] for pr in gh_state(state)["prs"]] == ["open"]


def test_of_two_words_crossing_the_newer_copy_is_kept(made):
    # a word whose copy is older than one already taken (the sender's and a call's crossing) keeps the
    # newer, which every later read of the record must hold all of; a white-box test of the choice, which a sequential
    # test meets no other way
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base))
    older = improve.proposals.view()["file"]
    record_it(improve, "demo", proposal(blob_id(PROPOSED + "x\n"), [], base, pid="b" * 12))
    newer = improve.proposals.view()["file"]
    improve.proposals.take_word(2, newer)
    improve.proposals.take_word(1, older)
    assert improve.proposals._copy["generation"] == 2
    assert [p["id"] for p in improve.proposals._copy["repos"]["demo"]] == ["a" * 12, "b" * 12]


def test_a_repository_cloned_again_under_its_name_and_given_a_second_name_is_barred(made, tmp_path):
    # the identity a proposal was recorded with is its repository's git folder then; one made again under the
    # record's name (another git folder, the same objects) and given a second name is matched by the name that leads to
    # it now
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    c = proposal_commit(repo, base)
    recorded = git_identity(repo)
    record_it(improve, "demo", dict(proposal(blob_id(PROPOSED), [c], base), git=recorded))
    again = str(tmp_path / "again")
    git("clone", "-q", "--no-local", repo, again, cwd=str(tmp_path))
    shutil.rmtree(repo)
    os.rename(again, repo)
    assert git_identity(repo) != recorded                               # another git folder under the record's name
    os.symlink(repo, os.path.join(ws.repos_dir, "demo-2"))
    assert improve.guards("demo-2") is True
    assert improve.check_start("demo-2", c) == si.BARRED.format(path=PATH, id="a" * 12)


def test_the_look_still_finds_the_version_of_a_merge_on_github_whose_answer_the_app_lost(gh_turns, on_github):
    # a merge on GitHub keeps no commit with its end, so the look still reads the branch for it, for an app
    # whose call of the merge was lost
    ws, improve, bare, state, base = on_github
    p, head = published(gh_turns, improve)
    opened = improve.pull_request(p["id"], {"counts": COUNTS})
    improve.merge(p["id"], {"url": opened["url"], "method": "squash"})
    seen = improve.look("demo")["proposals"][p["id"]]
    assert seen["ended"]["how"] == "merged" and "commit" not in seen["ended"]
    assert seen["reached"] == "content" and seen["change_commit"] == git("rev-parse", "main", cwd=bare)


def test_the_pull_request_found_again_is_one_whose_head_is_the_proposals_commit(gh_turns, on_github):
    # only an open pull request from the proposal's branch into its own, with the proposal's commit as its
    # head, is taken as the one the first tap opened
    ws, improve, bare, state, base = on_github
    p, head = published(gh_turns, improve)
    opened = improve.pull_request(p["id"], {"counts": COUNTS})
    path = ws._repo_path("demo")
    assert improve._open_pull_request("octo/demo", opened["branch"], head, "refs/heads/main", path) == opened["url"]
    assert improve._open_pull_request("octo/demo", opened["branch"], base, "refs/heads/main", path) is None
    assert improve._open_pull_request("octo/demo", opened["branch"], head, "refs/heads/other", path) is None


def test_the_sweep_never_lists_through_a_link_put_there_after_its_check(linked, tmp_path, monkeypatch):
    # a link put in the loop's folder's place between the sweep's check and its listing is not followed, the
    # folder being opened without following a link; the swap is made by a check that sees the folder the link leads to
    ws, improve, own, base = linked
    parent = os.path.join(ws.root, "skill-improve")
    target = str(tmp_path / "target")
    os.makedirs(os.path.join(target, "kept-folder"))
    shutil.rmtree(parent)
    os.symlink(target, parent)
    real = os.lstat

    def lstat(path, *args, **kwargs):
        if not kwargs and not args and os.fspath(path) == parent:
            return real(target)                         # the check saw a folder: the link came after it
        return real(path, *args, **kwargs)

    monkeypatch.setattr(si.os, "lstat", lstat)
    si.SkillImprove(ws)                                 # the workspace started again
    monkeypatch.setattr(si.os, "lstat", real)
    assert os.listdir(target) == ["kept-folder"]


# ---- the app's word, the record's write, the publish's reflog and a refusal's ways out -------------------------------

def test_the_apps_own_copy_put_back_after_a_commit_it_checked_is_held_by_its_word(made):
    # a proposal whose text the branch held before bars by its commits alone; its commit, recorded
    # after the app's last read, is in the word the app sends at the workspace's next start (the commits it
    # checked), so that the app's own copy put back by a session's program is held, not believed; the owner's restore
    # gives the commit back
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base, fresh=False))     # recorded
    copy = _file(improve)                                                                # the app reads it
    gen = improve.proposals.read_file()[1]["generation"]
    improve.proposals.take_word(gen, _b64(copy))
    c = proposal_commit(repo, base)
    improve.proposals.change(lambda r: r["repos"]["demo"][0]["commits"].append(c))       # its commit, and its answer
    assert improve.check_start("demo", c, note=False).startswith("a proposed change to")
    _put(improve, copy)                                 # the app's own copy put back, and the workspace started again
    again = si.SkillImprove(ws)
    again.proposals.take_word(gen, _b64(copy), {"a" * 12: [c]})
    assert again.check_start("demo", c, note=False) == si.RECORD_HELD
    assert again.proposals.held == "the record's file lacks a commit the app checked for proposal aaaaaaaaaaaa"
    again.proposals.restore(_b64(copy), {"a" * 12: [c]})
    assert again.check_start("demo", c, note=False).startswith("a proposed change to")


def test_the_apps_own_copy_put_back_after_an_end_it_recorded_is_held_by_its_word(turns, linked):
    # the owner's merge, recorded by the workspace and then by the app, after the app's last read of the
    # record; the app's word at the workspace's next start carries the end it recorded, so that its own copy put back is
    # held (the page then offers the restore, which gives the end back) rather than believed with no end, which barred
    # every start from his branch with nothing on the page to end it
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    copy = _file(improve)
    gen = improve.proposals.read_file()[1]["generation"]
    improve.proposals.take_word(gen, _b64(copy))
    assert improve.accept(p["id"], {"head": head})["merged"] is True
    end = improve.proposals.read_file()[1]["repos"]["demo"][0]["ended"]
    assert improve.check_start("demo", "refs/heads/main", note=False) is None
    _put(improve, copy)
    again = si.SkillImprove(ws)
    ends = {p["id"]: {"how": end["how"], "at": end["at"]}}
    again.proposals.take_word(gen, _b64(copy), None, ends)
    assert again.check_start("demo", "refs/heads/main", note=False) == si.RECORD_HELD
    assert again.proposals.held == f"the record's file lacks the end the app recorded for proposal {p['id']}"
    again.proposals.restore(_b64(copy), None, ends)
    assert again.check_start("demo", "refs/heads/main", note=False) is None


def test_a_word_whose_commits_and_ends_the_record_holds_is_believed(turns, linked):
    # a control: the record the workspace wrote holds each commit and end the app recorded from its answers,
    # and a proposal the record does not hold at all is the copy's to require, not the word's
    ws, improve, own, base = linked
    p, head = published(turns, improve)
    improve.accept(p["id"], {"head": head})
    now = improve.proposals.read_file()[1]
    end = now["repos"]["demo"][0]["ended"]
    again = si.SkillImprove(ws)
    again.proposals.take_word(now["generation"], _b64(_file(improve)), {p["id"]: [head], "f" * 12: [base]},
                              {p["id"]: {"how": end["how"], "at": end["at"]}, "e" * 12: {"how": "taken", "at": 1}})
    assert again.proposals.state()[0] == "record" and again.proposals.held is None


def test_the_word_refuses_commits_and_ends_that_are_not_such(made):
    ws, improve, repo, base = made
    with pytest.raises(si.SkillImproveError, match="lists of commits' ids"):
        improve.proposals.take_word(0, None, {"a" * 12: ["not an id"]})
    with pytest.raises(si.SkillImproveError, match="ends the app recorded"):
        improve.proposals.take_word(0, None, None, {"a" * 12: {"how": "lost", "at": 1}})
    assert improve.proposals.has_word is False


def test_the_record_is_on_the_disk_before_its_write_returns(made, monkeypatch):
    # the file flushed before it takes the record's name, and then the folder, so that a machine that stops
    # once a write returns does not come back to the file before it
    ws, improve, repo, base = made
    improve.proposals.take_word(0)
    synced, real = [], os.fsync

    def fsync(fd):
        synced.append(os.fstat(fd).st_ino)
        real(fd)

    monkeypatch.setattr(si.os, "fsync", fsync)
    improve.proposals.change(lambda r: r["switches"].update(demo={"on": True}))
    assert synced == [os.stat(improve.proposals.path).st_ino, os.stat(improve.proposals.dir).st_ino]


@pytest.mark.parametrize("how", ["reflog_link", "reflog_hard_link", "refs_heads_link"])
def test_the_publishs_ref_and_its_reflog_are_written_in_the_clone_alone(turns, linked, how):
    # a link at the clone's reflog of its proposal branch, or a second name of the shared repository's
    # reflog there, would have the publish's update-ref append its line to the shared repository's reflog of its main
    # branch, so that main@{0} named the proposal's commit; and refs/heads a link. Each refuses the publish
    ws, improve, own, base = linked
    p = made_proposal(turns, improve)
    clone = os.path.join(ws.root, "skill-improve", p["id"], "clone")
    improve.objects(p["id"], {"text": PROPOSED})
    theirs = os.path.join(own, ".git", "logs", "refs", "heads", "main")
    with open(theirs, "rb") as fh:
        before = fh.read()
    if how == "refs_heads_link":
        heads = os.path.join(clone, ".git", "refs", "heads")
        shutil.rmtree(heads)
        os.symlink(os.path.join(own, ".git", "refs", "heads"), heads)
    else:
        mine = os.path.join(clone, ".git", "logs", "refs", "heads", "proposal")
        os.makedirs(os.path.dirname(mine), exist_ok=True)
        if os.path.lexists(mine):
            os.remove(mine)
        (os.symlink if how == "reflog_link" else os.link)(theirs, mine)
    with pytest.raises(si.SkillImproveError, match="not a repository of the loop's own"):
        improve.publish(p["id"])
    with open(theirs, "rb") as fh:
        assert fh.read() == before
    assert git("for-each-ref", "--format=%(refname)", "refs/heads", cwd=own) == "refs/heads/main"


def test_a_clone_whose_git_folder_holds_more_than_the_walk_reads_cannot_tell(turns, linked, monkeypatch):
    # the check of the clone's git folder reads at most GIT_FOLDER_MOST entries, past which it cannot tell
    ws, improve, own, base = linked
    p = made_proposal(turns, improve)
    clone = os.path.join(ws.root, "skill-improve", p["id"], "clone")
    count = sum(len(d) + len(f) for _, d, f in os.walk(os.path.join(clone, ".git")))
    monkeypatch.setattr(si, "GIT_FOLDER_MOST", count)
    assert si.own_git_folder(clone) == os.path.join(clone, ".git")
    monkeypatch.setattr(si, "GIT_FOLDER_MOST", count - 1)
    with pytest.raises(si.CannotTell, match="not a repository of the loop's own"):
        si.own_git_folder(clone)


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() != 0,
                    reason="the repository is given to another user with chown, which only root may do")
def test_a_repository_git_reads_only_through_the_users_safe_directory_is_read_by_the_bar(made, own_home,
                                                                                             monkeypatch):
    # a repository in a folder another user owns, which git reads only where the user's own safe.directory
    # allows it, as on a mounted volume, is read by the bar as the workspace's own gits read it, his settings kept: with
    # the record naming a proposal in another repository alone, a start in it runs and its worktree is not guarded,
    # where before the bar read no repository there and refused every start, as one that cannot tell
    ws, improve, repo, base = made
    other = ws._repo_path("other")
    os.makedirs(other)
    git("init", "-q", "-b", "main", cwd=other)
    write(other, "README.md", "other\n")
    commit_all(other, "first")
    with open(own_home / ".gitconfig", "w") as fh:
        fh.write("[safe]\n\tdirectory = *\n")
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    subprocess.run(["chown", "-R", "nobody", other], check=True)
    improve.proposals.take_word(0)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base))
    assert improve.check_start("other", "HEAD", note=False) is None
    assert improve.guards("other") is False


@pytest.mark.parametrize("what", ["commits", "ends"])
def test_the_words_route_takes_the_commits_and_the_ends_the_app_recorded(tmp_path, monkeypatch, what):
    # PUT /skill-improve/record passes the word's commits and ends on, which the record must hold
    no_word_at_start(monkeypatch)
    w, repo, base = agents_workspace(tmp_path, lambda repo, base: [proposal(blob_id(PROPOSED), [], base)])
    try:
        body = {"generation": 0, what: {"a" * 12: ["c" * 40] if what == "commits" else {"how": "merged", "at": 1}}}
        got = w.http.put("/skill-improve/record", json=body)
        assert got.status_code == 200 and got.json()["state"] == "held", got.text
        assert w.http.get("/skill-improve/record").json()["held"].endswith("for proposal aaaaaaaaaaaa")
    finally:
        w.stop()


def test_a_refusal_other_than_by_a_proposals_text_or_commit_names_no_way_out(made):
    # the kept worktree's ways out end a refusal by the bar's steps alone, never a refusal for the word, the
    # record held or a git that cannot tell, whose way out is another
    ws, improve, repo, base = made
    assert improve.check_start("demo", "HEAD", note=False, then=si.KEPT_WAY_OUT) == si.WAITING_FOR_APP
    improve.proposals.take_word(0)
    record_it(improve, "demo", proposal(blob_id(PROPOSED), [], base))
    c = proposal_commit(repo, base)
    assert improve.check_start("demo", c, note=False, then=si.KEPT_WAY_OUT).endswith(si.KEPT_WAY_OUT)
    assert improve.check_start("demo", c, note=False) == si.BARRED.format(path=PATH, id="a" * 12)
    os.unlink(improve.proposals.path)
    assert improve.check_start("demo", c, note=False, then=si.KEPT_WAY_OUT) == si.RECORD_HELD


def test_a_runs_own_worktree_so_filled_is_refused_with_no_kept_worktrees_way_out(tmp_path):
    # the kept worktree's ways out end a refusal where the kept worktree holds the text; a run that works
    # apart from it (a replay here, or an improve loop's run) whose own worktree is reused, as a start the workspace
    # stopped during would leave it, is refused without them, since neither way would change its start
    from test_agents_runs import AGENT, put, run, until, view
    w, repo, base = agents_workspace(tmp_path, lambda repo, base: [proposal(blob_id(PROPOSED), [], base)])
    try:
        put(w, "hello", keep_worktree=True)
        first = until(w, run(w)["run_id"], ("failed", "done", "blocked"))
        assert first["state"] == "done", first
        put(w, "ask Which branch should I base this on?", keep_worktree=True)
        asking = run(w)["run_id"]
        until(w, asking, ("waiting",))                 # the agent busy: the replay waits, queued
        r = w.http.post(f"/agents/{AGENT}/runs", json={"trigger": {"kind": "replay", "of": first["run_id"]}})
        assert r.status_code == 200, r.text
        replay = r.json()["run_id"]
        assert view(w, replay)["state"] == "queued"
        work = os.path.join(w.root, "agents", AGENT, "runs", replay, "work")
        git("worktree", "add", "-q", "-b", f"pp/run-{replay}", work, base, cwd=repo)
        write(work, PATH, PROPOSED)
        sent = w.http.post(f"/agents/runs/{asking}/message", json={"text": "main"})
        assert sent.status_code == 200, sent.text
        got = until(w, replay, ("failed", "done", "blocked"))
        assert (got["state"], got["cause"]) == ("failed", "start") and got["reason"].startswith("a proposed"), got
        assert not got["reason"].endswith(si.KEPT_WAY_OUT)
        assert not w.http.get("/skill-improve/record").json()["refused"][-1]["why"].endswith(si.KEPT_WAY_OUT)
    finally:
        w.stop()
