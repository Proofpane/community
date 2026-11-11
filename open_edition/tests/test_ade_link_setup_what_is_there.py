"""What `ade_link.py setup` does with hooks of this link's
written before (an old link id's stop hook, which would hold every tool; the page's block written with $HOME; another
interpreter's copy), with another link's hooks in the same file, in a git worktree (Claude Code keeps its local settings
at the main checkout's root), with an interpreter that cannot load cryptography, with a file git does not ignore, and
with the mirror, which goes in only when asked for (--mirror). Each refusal sends nothing and writes nothing. The
file's earlier bytes are kept in the link's directory, never beside the file, where `git add -A` would take them."""

import json
import os
import shutil
import stat
import subprocess
import sys

import pytest

import ade_link
from test_ade_link_client import owner, served, ticket  # noqa: F401  fixtures and helpers
from test_ade_link_setup import (NOTHING, TEMPLATES, filled, home, lines, links_named, no_inbox,  # noqa: F401
                                 project, run, settings_of, setup)

OLD, NEW = "a" * 32, "b" * 32


def linked(home, name="mac", link_id=NEW, pinned=False):
    """A link's directory as pair leaves it, without pairing: its config.json, and its pinned keys where `pinned`."""
    d = home / ".proofpane-ade" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps({"url": "https://ade.example", "name": name, "link_id": link_id}))
    if pinned:
        (d / "owner_keys.json").write_text(json.dumps({"account": "acct", "keys": [{"fingerprint": "f" * 32,
                                                                                    "name": "key"}],
                                                       "source": "page", "pinned_at": 1}))
    return d


def block(home, template, link_id, name="mac", home_text=None):
    return filled(template, home_text or home, name, link_id)


def as_file(*blocks):
    """The settings a file holds with these blocks merged in order, as a hand merge or an earlier setup left them."""
    out = {"env": {}, "disableAllHooks": False, "hooks": {}}
    for b in blocks:
        out["env"].update(b.get("env", {}))
        for event, groups in b["hooks"].items():
            out["hooks"].setdefault(event, []).extend(groups)
    return out


def hooks_alone(capsys, name="mac", *extra):
    return run(capsys, "setup", "--name", name, "--kind", "local", *extra)


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from strings(v)


def no_request(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("setup sent a request")
    monkeypatch.setattr(ade_link.Link, "request", refuse)


# ---- hooks of this link's written before ------------------------------------------------------

def test_a_relinked_directory_s_old_stop_hook_is_removed_and_said(capsys, home, project, monkeypatch):
    """Linked again, the directory's link id is new, and the stop hook an earlier setup wrote for the old id would hold
    every tool (`pretool --link OLD` finds the link gone): setup removes it as it adds the new one, and says so. The
    file as it was keeps it, in the link's directory."""
    d = linked(home, pinned=True)
    (project / ".claude").mkdir()
    path = settings_of(project)
    path.write_text(json.dumps(as_file(block(home, TEMPLATES[0], OLD)), indent=2))
    no_request(monkeypatch)
    code, out, err = hooks_alone(capsys)
    assert code == 0, err
    now = json.loads(path.read_text())
    assert not any(OLD in s for s in strings(now)), "the old link's hooks are gone"
    stops = [g for g in now["hooks"]["PreToolUse"] if any("pretool" in s for s in strings(g))]
    assert len(stops) == 1 and any(NEW in s for s in strings(stops[0]))
    said = lines(out)
    assert any(line.startswith("ADE: replaced in PreToolUse: ") for line in said), out
    assert f"ADE: one of them was the stop hook of link {OLD}, which holds every tool now that the link is {NEW}" \
        in said
    kept = list((d / "settings-backups").iterdir())
    assert len(kept) == 1 and OLD in kept[0].read_text()


def test_an_old_stop_hook_is_removed_though_no_key_is_pinned(capsys, home, project, monkeypatch):
    """No key pinned, setup adds no stop hook of its own; the old link's stop hook still goes, since it holds every
    tool whatever is pinned."""
    linked(home, pinned=False)
    (project / ".claude").mkdir()
    path = settings_of(project)
    path.write_text(json.dumps(as_file(block(home, TEMPLATES[0], OLD))))
    no_request(monkeypatch)
    code, out, err = hooks_alone(capsys)
    assert code == 0, err
    now = json.loads(path.read_text())
    assert not any(OLD in s for s in strings(now))
    assert not any("pretool" in s for s in strings(now.get("hooks", {}).get("PreToolUse", [])))
    assert "so the stop hook is not added" in out


def test_an_event_an_earlier_copy_alone_used_goes_with_its_hooks(capsys, home, project, monkeypatch):
    """A hook of this link's written before under an event the blocks no longer use (an earlier version's
    SubagentStop, say) is replaced as any earlier copy is, and the event it leaves empty goes with it, not left in the
    file as an empty list (without this case, a faulty version of the code passes)."""
    linked(home, pinned=True)
    (project / ".claude").mkdir()
    path = settings_of(project)
    old = block(home, TEMPLATES[0], NEW)
    old["hooks"]["SubagentStop"] = old["hooks"].pop("Stop")
    path.write_text(json.dumps(as_file(old)))
    no_request(monkeypatch)
    code, out, err = hooks_alone(capsys)
    assert code == 0, err
    now = json.loads(path.read_text())
    assert "SubagentStop" not in now["hooks"], now["hooks"].get("SubagentStop")
    assert now["hooks"]["Stop"] == block(home, TEMPLATES[0], NEW)["hooks"]["Stop"]
    assert any(line.startswith("ADE: replaced in SubagentStop: ") for line in lines(out)), out


def test_the_block_the_page_gives_written_with_home_is_this_link_s_and_is_replaced(capsys, home, project, monkeypatch):
    """The manual's and the page's block name the link's directory as "$HOME/.proofpane-ade/NAME": that is this
    link's, an earlier copy of setup's own, so it is replaced rather than run beside setup's (each hook twice)."""
    linked(home, pinned=True)
    (project / ".claude").mkdir()
    path = settings_of(project)
    path.write_text(json.dumps(as_file(block(home, TEMPLATES[0], NEW, home_text="$HOME"))))
    no_request(monkeypatch)
    code, out, err = hooks_alone(capsys)
    assert code == 0, err
    now = json.loads(path.read_text())
    assert not any("$HOME" in s for s in strings(now)), "the $HOME copies are replaced"
    assert sum(" resume" in s for s in strings(now)) == 1
    assert any(line.startswith("ADE: replaced in ") for line in lines(out))


def test_another_interpreter_s_copy_is_replaced_and_the_owner_s_own_hook_beside_it_stays(capsys, home, project,
                                                                                       monkeypatch):
    """An earlier copy of this link's hook run by another interpreter is replaced; a hook of the owner's own in the
    same group stays there, in its group."""
    d = linked(home)
    (project / ".claude").mkdir()
    path = settings_of(project)
    older = {"type": "command", "timeout": 20,
             "command": f'/usr/bin/python3 -I -X utf8 "{home}/.proofpane-ade/ade_link.py" --dir "{d}" resume'}
    mine = {"type": "command", "command": "echo mine"}
    path.write_text(json.dumps({"hooks": {"SessionStart": [{"matcher": "startup", "hooks": [older, mine]}]}}))
    no_request(monkeypatch)
    code, out, err = hooks_alone(capsys)
    assert code == 0, err
    groups = json.loads(path.read_text())["hooks"]["SessionStart"]
    assert {"matcher": "startup", "hooks": [mine]} in groups
    assert not any("/usr/bin/python3" in s for s in strings(groups))
    assert any(line.startswith("ADE: replaced in SessionStart: ") for line in lines(out))


def test_an_earlier_mirror_hook_is_left_where_setup_adds_no_mirror(capsys, home, project, monkeypatch):
    """Without --mirror setup adds no mirror, so a mirror hook of this link's in the file is the owner's choice: left
    as it is, and said."""
    linked(home)
    (project / ".claude").mkdir()
    path = settings_of(project)
    mirror = block(home, TEMPLATES[1], NEW)
    path.write_text(json.dumps(as_file(mirror)))
    no_request(monkeypatch)
    code, out, err = hooks_alone(capsys)
    assert code == 0, err
    now = json.loads(path.read_text())
    assert now["hooks"]["Stop"][0] == mirror["hooks"]["Stop"][0]
    assert "ADE: also in Stop: a hook of this link's that setup leaves as it is" in lines(out)


# ---- the mirror only when asked for ---------------------------------------------------------------------

def test_the_mirror_goes_in_only_with_mirror(capsys, home, project, monkeypatch):
    """The manual's mirror is the owner's choice: setup adds the link's block, and the mirror's only with --mirror,
    saying what it would send."""
    linked(home)
    no_request(monkeypatch)
    code, out, err = hooks_alone(capsys)
    assert code == 0, err
    now = json.loads(settings_of(project).read_text())
    assert not any(" mirror " in s for s in strings(now))
    assert ("ADE: the mirror is not added: setup --mirror adds it (each turn's words and each tool call's line go to "
            "this link's conversation on the page, sealed once Encryption is on)") in lines(out)
    code, out, err = hooks_alone(capsys, "mac", "--mirror")
    assert code == 0, err
    now = json.loads(settings_of(project).read_text())
    assert sum("mirror reply" in s for s in strings(now)) == 1 and sum("mirror live" in s for s in strings(now)) == 1
    assert "the mirror is not added" not in out


# ---- another link's hooks in the same file ---------------------------------------------------------------

def test_another_link_s_hooks_in_the_file_refuse_before_anything_is_sent(client, owner, served, capsys, home, project):
    """A block of another link's in the file runs that link's hooks in every session started there: a session
    linked here as well would run both, its permission questions asked twice. Refused before anything is sent."""
    (project / ".claude").mkdir()
    path = settings_of(project)
    other = home / ".proofpane-ade" / "mac2"
    theirs = {"type": "command", "command": f'/usr/bin/python3 -I -X utf8 "{home}/.proofpane-ade/ade_link.py" '
                                           f'--dir "{other}" resume', "timeout": 20}
    text = json.dumps({"hooks": {"SessionStart": [{"hooks": [theirs]}]}})
    path.write_text(text)
    code, out, err = setup(capsys, served, ticket(client, owner).lower())
    assert code == 2 and NOTHING in err
    assert f"holds hooks of another link ({other})" in err and "would run both links' hooks" in err
    assert path.read_text() == text and links_named("mac") == 0


# ---- a git worktree -------------------------------------------------------------------------------------

def a_worktree(tmp_path, commondir="../..", back=True):
    """A repository's main checkout and a worktree of it, as git worktree add lays them out (no git run)."""
    main, wt = tmp_path / "main", tmp_path / "wt"
    admin = main / ".git" / "worktrees" / "wt"
    admin.mkdir(parents=True)
    (main / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {admin}\n")
    if commondir is not None:
        (admin / "commondir").write_text(commondir + "\n")
    (admin / "gitdir").write_text(f"{wt / '.git'}\n" if back else f"{tmp_path / 'elsewhere' / '.git'}\n")
    (wt / "sub").mkdir()
    return main, wt


def test_in_a_git_worktree_the_file_is_the_main_checkout_s(tmp_path, home):
    """Claude Code keeps a worktree's local settings at the main checkout's root, which its .git file's gitdir and
    that directory's commondir lead to: setup writes there, from the worktree's root or a folder in it."""
    main, wt = a_worktree(tmp_path)
    assert ade_link.settings_folder(cwd=str(wt / "sub")) == str(main)
    assert ade_link.settings_folder(cwd=str(wt)) == str(main)


@pytest.mark.parametrize("commondir, back", [(None, True), ("../../elsewhere", True), ("../..", False)])
def test_a_worktree_whose_files_do_not_read_as_git_writes_them_keeps_its_own_root(tmp_path, home, commondir, back):
    """Where the worktree's files do not lead back as git writes them (no commondir, one naming another directory, a
    gitdir file naming another worktree), Claude Code keeps the worktree's own root, and so does setup."""
    main, wt = a_worktree(tmp_path, commondir, back)
    assert ade_link.settings_folder(cwd=str(wt / "sub")) == str(wt)


def test_setup_in_a_worktree_says_every_worktree_reads_the_file(capsys, home, tmp_path, monkeypatch):
    main, wt = a_worktree(tmp_path)
    linked(home)
    monkeypatch.chdir(wt / "sub")
    no_request(monkeypatch)
    code, out, err = hooks_alone(capsys)
    assert code == 0, err
    assert settings_of(main).exists() and not (wt / ".claude").exists() and not (wt / "sub" / ".claude").exists()
    assert (f"ADE: {settings_of(main)} is the one every worktree of this repository reads: a session in any of them "
            "runs these hooks") in lines(out)


# ---- what is checked before anything is sent -------------------------------

def an_interpreter(tmp_path, can):
    """An interpreter as the hook lines run it: here a script that loads what is asked of it, or fails to."""
    exe = tmp_path / "py" / "bin" / "python"
    exe.parent.mkdir(parents=True)
    exe.write_text(f"#!/bin/sh\nexec {sys.executable} \"$@\"\n" if can else "#!/bin/sh\nexit 1\n")
    exe.chmod(0o755)
    return str(exe)


def test_an_interpreter_that_cannot_load_cryptography_is_refused(client, owner, served, capsys, home, project,
                                                                  tmp_path, monkeypatch):
    """The stop hook signs its requests to the ADE, which needs cryptography in the interpreter the hook lines run,
    as they run it: one that cannot load it is refused before anything is sent, the hooks alone included."""
    monkeypatch.setattr(sys, "executable", an_interpreter(tmp_path, can=False))
    code, out, err = setup(capsys, served, ticket(client, owner).lower())
    assert code == 2 and "cannot load cryptography" in err and NOTHING in err
    assert links_named("mac") == 0 and not settings_of(project).exists()
    linked(home)
    code, out, err = hooks_alone(capsys)
    assert code == 2 and "cannot load cryptography" in err and NOTHING in err
    assert not settings_of(project).exists()


def test_an_interpreter_that_loads_cryptography_as_the_lines_run_it_is_taken(capsys, home, project, tmp_path,
                                                                            monkeypatch):
    """A control: an interpreter that loads it, run as the hook lines run it, is taken."""
    monkeypatch.setattr(sys, "executable", an_interpreter(tmp_path, can=True))
    linked(home)
    no_request(monkeypatch)
    code, out, err = hooks_alone(capsys)
    assert code == 0, err


def test_a_key_given_twice_is_refused(capsys, home, project, monkeypatch):
    """JSON keeps the last of a key given twice, and a hand merge can leave one: the first "hooks" would be dropped
    without a word. Refused, before anything is sent."""
    linked(home)
    (project / ".claude").mkdir()
    path = settings_of(project)
    text = '{"hooks": {"Stop": []}, "env": {}, "hooks": {"SubagentStop": []}}'
    path.write_text(text)
    no_request(monkeypatch)
    code, out, err = hooks_alone(capsys)
    assert code == 2 and "holds the key 'hooks' twice" in err and NOTHING in err
    assert path.read_text() == text


def test_text_that_is_not_ascii_is_kept_as_it_is(capsys, home, project, monkeypatch):
    linked(home)
    (project / ".claude").mkdir()
    path = settings_of(project)
    path.write_text(json.dumps({"env": {"GREETING": "h\u00e9llo \u00fcber"}}, ensure_ascii=False), encoding="utf-8")
    no_request(monkeypatch)
    code, out, err = hooks_alone(capsys)
    assert code == 0, err
    text = path.read_text(encoding="utf-8")
    assert "h\u00e9llo \u00fcber" in text and "\\u00e9" not in text


def test_a_folder_that_is_not_there_is_refused_and_not_made(capsys, home, project, tmp_path, monkeypatch):
    """--folder names the folder the session runs in, which is there: one that is not is a mistyped path, refused, and
    nothing is made."""
    linked(home)
    no_request(monkeypatch)
    gone = tmp_path / "typo" / "deep" / "proj"
    code, out, err = hooks_alone(capsys, "mac", "--folder", str(gone))
    assert code == 2 and "is not a folder" in err and NOTHING in err
    assert not (tmp_path / "typo").exists()


def test_a_link_id_that_is_not_hex_is_refused(capsys, home, project, monkeypatch):
    """The link's id goes into the stop hook's command line: one that is not the 32 hex digits pair keeps is refused,
    the hooks alone included."""
    d = linked(home, link_id="x; touch MARK #")
    no_request(monkeypatch)
    code, out, err = hooks_alone(capsys)
    assert code == 2 and "not 32 hex digits" in err and NOTHING in err
    assert not settings_of(project).exists() and not (project / "MARK").exists()
    assert "touch MARK" not in out


def test_a_folder_that_cannot_be_written_is_refused_before_anything_is_sent(client, owner, served, capsys, home,
                                                                            project, monkeypatch):
    real = os.access

    def unwritable(path, mode, *a, **k):
        if os.path.abspath(path) in (str(project), str(project / ".claude")) and mode & os.W_OK:
            return False
        return real(path, mode, *a, **k)
    monkeypatch.setattr(ade_link.os, "access", unwritable)
    code, out, err = setup(capsys, served, ticket(client, owner).lower())
    assert code == 2 and "cannot be written" in err and NOTHING in err
    assert links_named("mac") == 0


def test_a_write_that_fails_after_the_pairing_says_the_link_stands(client, owner, served, capsys, home, project,
                                                                   monkeypatch):
    def fails(*a, **k):
        raise PermissionError("denied")
    real = ade_link.cmd_pair

    def pairs_then_writes_fail(args):
        code = real(args)
        monkeypatch.setattr(ade_link, "_write", fails)
        return code
    monkeypatch.setattr(ade_link, "cmd_pair", pairs_then_writes_fail)
    code, out, err = setup(capsys, served, ticket(client, owner).lower())
    assert code == 2 and "the link stands" in err and "nothing more was done" not in err
    assert links_named("mac") == 1


def test_the_file_is_read_once_and_never_through_a_link(capsys, home, project, tmp_path, monkeypatch):
    """The file is checked, then read: a link put in its place between the two is not followed, and the file it points
    to is neither written nor copied."""
    linked(home)
    (project / ".claude").mkdir()
    target = tmp_path / "theirs.json"
    target.write_text('{"env": {"SECRET": "s3"}}')
    path = settings_of(project)
    path.write_text("{}")
    real = ade_link._pinned

    def swapped_then_pinned(link_dir):
        # the file checked, the stop hook's pin is read next: a link put in the file's place now must not be followed
        if not os.path.islink(path):
            os.unlink(path)
            os.symlink(target, path)
        return real(link_dir)
    monkeypatch.setattr(ade_link, "_pinned", swapped_then_pinned)
    no_request(monkeypatch)
    hooks_alone(capsys)
    assert target.read_text() == '{"env": {"SECRET": "s3"}}'
    for folder in (project / ".claude", home / ".proofpane-ade" / "mac"):
        for p in folder.rglob("*"):
            if p.is_file() and not p.is_symlink() and p != target:
                assert "s3" not in p.read_text(errors="replace"), p


def test_windows_is_refused_in_words(capsys, home, project, monkeypatch):
    """The hook lines are a POSIX shell's: setup runs on macOS and Linux, and on Windows says so before anything is
    sent."""
    linked(home)
    no_request(monkeypatch)
    with monkeypatch.context() as m:
        m.setattr(ade_link.os, "name", "nt")
        code, out, err = hooks_alone(capsys)
    assert code == 2 and "Windows" in err and NOTHING in err
    assert not settings_of(project).exists()


# ---- the copy of the file and git -------------------------------------------------------------------

def test_the_copy_of_the_file_as_it_was_goes_in_the_link_s_directory_read_by_the_owner_alone(capsys, home, project,
                                                                                           monkeypatch):
    """The file as it was holds what its env held, which setup never prints: the copy goes in the link's directory,
    readable by the owner alone, never beside the file where `git add -A` would take it."""
    d = linked(home)
    (project / ".claude").mkdir()
    path = settings_of(project)
    path.write_text(json.dumps({"env": {"TOKEN": "t0ken"}}))
    original = path.read_bytes()
    no_request(monkeypatch)
    code, out, err = hooks_alone(capsys)
    assert code == 0, err
    assert list((project / ".claude").iterdir()) == [path]
    kept = list((d / "settings-backups").iterdir())
    assert len(kept) == 1 and kept[0].read_bytes() == original
    assert stat.S_IMODE(kept[0].stat().st_mode) == 0o600
    assert f"ADE: the file as it was is kept as {kept[0]}" in lines(out)


def a_repository(folder):
    subprocess.run(["git", "init", "-q", str(folder)], check=True)
    subprocess.run(["git", "-C", str(folder), "config", "core.excludesFile", "/dev/null"], check=True)
    return folder


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_file_setup_writes_in_a_git_repository_is_ignored_by_git(capsys, home, tmp_path, monkeypatch):
    """Claude Code ignores the file in git as it writes it; a file setup writes is ignored too: where git would take
    it, the repository's own exclude file (never committed) gains the rule, and setup says so."""
    repo = a_repository(tmp_path / "repo")
    monkeypatch.chdir(repo)
    linked(home)
    no_request(monkeypatch)
    code, out, err = hooks_alone(capsys)
    assert code == 0, err
    check = subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", ".claude/settings.local.json"])
    assert check.returncode == 0
    exclude = repo / ".git" / "info" / "exclude"
    assert "/.claude/settings.local.json" in exclude.read_text()
    assert f"ADE: git did not ignore {settings_of(repo)}: {exclude} ignores it now" in lines(out)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_file_git_ignores_already_adds_no_rule(capsys, home, tmp_path, monkeypatch):
    repo = a_repository(tmp_path / "repo")
    (repo / ".gitignore").write_text(".claude/\n")
    monkeypatch.chdir(repo)
    linked(home)
    no_request(monkeypatch)
    exclude = repo / ".git" / "info" / "exclude"
    before = exclude.read_text() if exclude.exists() else None
    code, out, err = hooks_alone(capsys)
    assert code == 0, err
    assert (exclude.read_text() if exclude.exists() else None) == before
    assert "ignores it now" not in out


# ---- the words ---------------------------------------------------------------------------------------

def test_the_env_words_say_the_values_apply_to_every_command(capsys, home, project, monkeypatch):
    linked(home)
    no_request(monkeypatch)
    code, out, err = hooks_alone(capsys)
    assert code == 0, err
    assert ("ADE: the env values apply to every command the session runs, over what the project's settings and the "
            "environment Claude Code starts with give") in lines(out)


# ---- a folder that is a link, quoting, whose root it is, and a file that changes while it pairs ------

def test_a_dot_claude_that_is_a_link_is_refused(capsys, home, project, tmp_path, monkeypatch):
    linked(home)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (project / ".claude").symlink_to(elsewhere)
    no_request(monkeypatch)
    code, out, err = hooks_alone(capsys)
    assert code == 2 and "is not a folder" in err and NOTHING in err
    assert list(elsewhere.iterdir()) == []


@pytest.mark.parametrize("ch", ["$", "`", "\\", "\x01", '"'])
def test_a_link_directory_the_quoted_paths_cannot_carry_is_refused(capsys, home, project, monkeypatch, ch):
    """The client's path and the link's directory go in double quotes: a quote, a $, a backquote, a backslash or a
    control character there would end the quote, substitute or escape."""
    d = home / ".proofpane-ade" / f"m{ch}c"
    linked(home, name=f"m{ch}c")
    no_request(monkeypatch)
    code, out, err = run(capsys, "setup", "--name", f"m{ch}c", "--kind", "local", "--dir", str(d))
    assert code == 2 and "cannot carry" in err and NOTHING in err
    assert not settings_of(project).exists()


@pytest.mark.parametrize("entry", [".git", ".claude"])
def test_one_entry_of_the_repository_s_root_not_the_user_s_own_keeps_the_folder_started_in(capsys, home, tmp_path,
                                                                                          monkeypatch, entry):
    """Claude Code checks the root, its .git and its .claude each: one of them not the user's own keeps the file in the
    folder the session started in."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".claude").mkdir()
    sub = repo / "sub"
    sub.mkdir()
    theirs = str(repo / entry)
    real = os.lstat

    def lstat(p, *a, **k):
        st = real(p, *a, **k)
        if os.path.abspath(p) == theirs:
            fields = list(st)
            fields[stat.ST_UID] = st.st_uid + 1
            return os.stat_result(fields)
        return st
    monkeypatch.setattr(ade_link.os, "lstat", lstat)
    assert ade_link.settings_folder(cwd=str(sub)) == str(sub)


def test_a_settings_file_that_stops_holding_an_object_of_lists_while_it_pairs_is_left(client, owner, served, capsys,
                                                                                      home, project, monkeypatch):
    """The check after the pairing is the whole check, not JSON alone: a file whose hooks became a list meanwhile is
    left, the link standing."""
    real = ade_link.cmd_pair

    def pairs_and_the_file_changes(args):
        code = real(args)
        (project / ".claude").mkdir()
        settings_of(project).write_text('{"hooks": []}')
        return code
    monkeypatch.setattr(ade_link, "cmd_pair", pairs_and_the_file_changes)
    code, out, err = setup(capsys, served, ticket(client, owner).lower())
    assert code == 2 and '"hooks" is not an object' in err and "the link stands" in err
    assert settings_of(project).read_text() == '{"hooks": []}' and links_named("mac") == 1
