"""`ade_link.py setup` links a session as `pair` does and puts the
link's hooks into the session's `.claude/settings.local.json` itself, so the owner pastes nothing. It merges the link
block (`ade_link.settings.json`) and the mirror block (`ade_link.mirror.settings.json`), filled as the manual fills
them, into what the file holds; keeps the file as it was beside it first; says what it changed; writes nothing where
all of it is there already; and refuses, before anything is sent to the ADE, a settings file it cannot merge into, a
client that is not the one the hooks run, and a path the hooks' command lines cannot carry. The stop hook goes in only
once the owner's keys are pinned, as the README says, and a cloud session, which reads no local settings file, gets
none. The file goes where Claude Code reads it: at the root of the git repository the session started in, unless that
root is the home folder, else in the folder itself (Claude Code's settings page, from 2.1.211). Against the app served
on a local port, with the tests' own software authenticator where a passkey is needed."""

import json
import os
import shutil
import sys

import pytest

import ade_link
import hook_templates
from proofpane_open import store
from test_ade_link import at_once, confirmed
from test_ade_link_client import owner, pair, served, ticket  # noqa: F401  fixtures and helpers
from test_ade_link_client_commands import device  # noqa: F401  a fixture: the owner's passkey

TEMPLATES = ("ade_link.settings.json", "ade_link.mirror.settings.json")
NOTHING = "nothing was sent and nothing written"


@pytest.fixture(autouse=True)
def no_inbox(monkeypatch):
    # these tests may run inside a Claude Code session, whose inbox the client would otherwise really post into
    monkeypatch.delenv("CLAUDE_CODE_MESSAGING_SOCKET", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_MESSAGING_TOKEN", raising=False)
    monkeypatch.delenv("ADE_OWNER_KEYS", raising=False)
    monkeypatch.delenv("ADE_LINK_DIR", raising=False)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """The owner's home folder, with the client copied where SETUP_A.md installs it."""
    h = tmp_path / "home"
    (h / ".proofpane-ade").mkdir(parents=True)
    shutil.copy(ade_link.__file__, h / ".proofpane-ade" / "ade_link.py")
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.fixture
def project(tmp_path, monkeypatch, home):
    """The folder the session runs in: no git repository above it."""
    p = tmp_path / "project"
    p.mkdir()
    monkeypatch.chdir(p)
    return p


def run(capsys, *argv):
    """The client run as the command line runs it: an argument it does not take ends it with argparse's exit code."""
    try:
        code = ade_link.main([str(a) for a in argv])
    except SystemExit as e:
        code = e.code
    out, err = capsys.readouterr()
    return code, out, err


def settings_of(folder):
    return folder / ".claude" / "settings.local.json"


def setup(capsys, served_url, tk, name="mac", kind="local", *extra):
    argv = ["setup", "--url", served_url, "--ticket", tk, "--name", name, "--kind", kind, *extra]
    with at_once():                         # linked as it pairs: the wait is test_ade_link_pair_wait.py's
        return run(capsys, *argv)


def link_id_of(home, name="mac"):
    return json.loads((home / ".proofpane-ade" / name / "config.json").read_text())["link_id"]


def links_named(name):
    with store.connect() as db:
        return db.execute("SELECT COUNT(*) FROM ade_links WHERE name = ?", (name,)).fetchone()[0]


def filled(template, home, name, link_id):
    with open(os.path.join(hook_templates.HERE, template), encoding="utf-8") as fh:
        return json.loads(hook_templates._fill(fh.read(), sys.executable, str(home), name, link_id))


def is_stop_hook(group):
    return any("pretool" in hook.get("command", "") + " ".join(hook.get("args", [])) for hook in group["hooks"])


def expected(home, name, link_id, pinned, mirror=False):
    """The settings a new file holds after setup: the link block and then, with --mirror, the mirror block, each
    event's groups in that order, the link block's stop hook only once the owner's keys are pinned."""
    link, mirror_block = (filled(t, home, name, link_id) for t in TEMPLATES)
    hooks = {}
    for block in (link, mirror_block) if mirror else (link,):
        for event, groups in block["hooks"].items():
            for group in groups:
                if block is link and event == "PreToolUse" and is_stop_hook(group) and not pinned:
                    continue
                hooks.setdefault(event, []).append(group)
    return {"env": link["env"], "disableAllHooks": False, "hooks": hooks}


def added_line(hooks):
    return "ADE: added: " + ", ".join(f"{event} {len(groups)}" for event, groups in hooks.items())


def lines(out):
    return out.splitlines()


def fresh_ticket(client, owner):
    r = client.post("/api/ade/link/tickets", headers=owner, json={"confirmation": confirmed(client, owner)})
    assert r.status_code == 200, r.text
    return r.json()


# ---- a new settings file ----------------------------------------------------------------------------------------

def test_setup_links_the_session_and_writes_both_blocks_filled_into_a_new_settings_file(client, owner, served, capsys,
                                                                                         home, project):
    code, out, err = setup(capsys, served, ticket(client, owner).lower())
    assert code == 0, err
    said = lines(out)
    assert said[0].startswith("ADE: paired as mac (local); this session's key fingerprint is ")
    assert said[1].startswith("ADE: linked as mac (link ")
    path = settings_of(project)
    want = expected(home, "mac", link_id_of(home), pinned=False)
    assert json.loads(path.read_text()) == want
    assert f"ADE: the hooks go in {path}" in said
    assert added_line(want["hooks"]) in said
    assert f"ADE: env: {len(want['env'])} added" in said
    assert "ADE: disableAllHooks: false" in said
    assert not any(line.startswith("ADE: the file as it was is kept as ") for line in said)    # there was none
    # the stop hook waits on a pin, and the .claude folder was made now: a running session loads it once started again
    d = home / ".proofpane-ade" / "mac"
    assert (f"ADE: no owner key is pinned in {d}, so the stop hook is not added: pin the account's passkeys with the "
            "pin line from Account, then run setup again") in said
    assert (f"ADE: {project}/.claude was made now: a Claude Code session already running there loads the file only "
            "once it is started again") in said
    assert list((project / ".claude").iterdir()) == [path]                  # no backup: the file was new


def test_a_session_pinned_as_it_pairs_gets_the_stop_hook_too(client, owner, served, device, capsys, home, project):
    tk = fresh_ticket(client, owner)
    code, out, err = setup(capsys, served, tk["ticket"], "mac", "local", "--keys", ",".join(tk["keys"]))
    assert code == 0, err
    want = expected(home, "mac", link_id_of(home), pinned=True)
    assert json.loads(settings_of(project).read_text()) == want
    assert any(is_stop_hook(g) for g in want["hooks"]["PreToolUse"])        # the case is about this group
    assert added_line(want["hooks"]) in lines(out)
    assert "so the stop hook is not added" not in out


def test_a_folder_that_holds_a_dot_claude_already_takes_the_file_in_the_running_session(client, owner, served, capsys,
                                                                                        home, project):
    (project / ".claude").mkdir()
    code, out, err = setup(capsys, served, ticket(client, owner).lower())
    assert code == 0, err
    assert "ADE: a Claude Code session running there takes the change as the file changes" in lines(out)
    assert "was made now" not in out


# ---- what is there already ----------------------------------------------------------------------------------------

def test_run_again_it_finds_its_hooks_there_and_writes_nothing(client, owner, served, capsys, home, project):
    tk = ticket(client, owner).lower()
    assert setup(capsys, served, tk)[0] == 0
    path = settings_of(project)
    before = path.read_bytes()
    code, out, err = setup(capsys, served, tk)          # the same line again
    assert code == 0, err
    assert path.read_bytes() == before
    assert list((project / ".claude").iterdir()) == [path]                  # no backup either
    said = lines(out)
    assert f"ADE: {path} holds these hooks already; nothing was written" in said
    assert not any(line.startswith(("ADE: added: ", "ADE: env: ", "ADE: disableAllHooks")) for line in said)
    assert "takes the change" not in out and "was made now" not in out


def test_an_existing_file_keeps_what_it_held_and_is_kept_as_it_was_first(client, owner, served, capsys, home,
                                                                         project):
    (project / ".claude").mkdir()
    path = settings_of(project)
    mine = {"type": "command", "command": "echo mine"}
    old = {"permissions": {"allow": ["Bash(ls:*)"]}, "env": {"MY_VAR": "1"},
           "hooks": {"Stop": [{"hooks": [mine]}], "SubagentStop": [{"hooks": [mine]}]}}
    path.write_text(json.dumps(old, indent=4) + "\n")
    original = path.read_bytes()
    code, out, err = setup(capsys, served, ticket(client, owner).lower())
    assert code == 0, err
    now = json.loads(path.read_text())
    want = expected(home, "mac", link_id_of(home), pinned=False)
    assert now["permissions"] == old["permissions"]
    assert now["env"] == dict(old["env"], **want["env"])
    assert now["hooks"]["SubagentStop"] == old["hooks"]["SubagentStop"]
    assert now["hooks"]["Stop"] == [{"hooks": [mine]}] + want["hooks"]["Stop"]
    for event, groups in want["hooks"].items():
        if event != "Stop":
            assert now["hooks"][event] == groups, event
    assert list((project / ".claude").iterdir()) == [path]          # the copy goes in the link's directory
    kept = list((home / ".proofpane-ade" / "mac" / "settings-backups").iterdir())
    assert len(kept) == 1 and kept[0].name.startswith("settings.local.json.bak-")
    assert kept[0].read_bytes() == original
    assert f"ADE: the file as it was is kept as {kept[0]}" in lines(out)


def test_an_env_value_that_differs_is_set_and_named_without_its_value(client, owner, served, capsys, home, project):
    (project / ".claude").mkdir()
    path = settings_of(project)
    path.write_text(json.dumps({"env": {"LD_PRELOAD": "/tmp/hidden-value.so", "MY_VAR": "kept"}}))
    code, out, err = setup(capsys, served, ticket(client, owner).lower())
    assert code == 0, err
    env = json.loads(path.read_text())["env"]
    assert env["LD_PRELOAD"] == "" and env["MY_VAR"] == "kept"
    assert "ADE: env: changed LD_PRELOAD (each held another value)" in lines(out)
    assert "hidden-value" not in out and "hidden-value" not in err


def test_disable_all_hooks_true_is_set_to_false_and_said(client, owner, served, capsys, home, project):
    (project / ".claude").mkdir()
    path = settings_of(project)
    path.write_text(json.dumps({"disableAllHooks": True}))
    code, out, err = setup(capsys, served, ticket(client, owner).lower())
    assert code == 0, err
    assert json.loads(path.read_text())["disableAllHooks"] is False
    assert ("ADE: disableAllHooks: false (it was true: every hook the settings hold runs again, the project's own "
            "included)") in lines(out)


def test_an_earlier_mirror_hook_for_this_link_that_differs_is_left_and_named(client, owner, served, capsys, home,
                                                                             project):
    """A mirror hook written for this link before (another interpreter, say) is the owner's choice where setup adds no
    mirror (no --mirror): left as it is, and named. Hooks of this link's of the kinds setup adds are replaced
    (test_ade_link_setup_what_is_there.py)."""
    (project / ".claude").mkdir()
    path = settings_of(project)
    d = home / ".proofpane-ade" / "mac"
    older = {"type": "command", "command": f'/usr/bin/python3 -I -X utf8 "{home}/.proofpane-ade/ade_link.py" '
                                          f'--dir "{d}" mirror reply || true', "timeout": 30}
    path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [older]}]}}))
    code, out, err = setup(capsys, served, ticket(client, owner).lower())
    assert code == 0, err
    want = expected(home, "mac", link_id_of(home), pinned=False)
    assert json.loads(path.read_text())["hooks"]["Stop"] == [{"hooks": [older]}] + want["hooks"]["Stop"]
    assert "ADE: also in Stop: a hook of this link's that setup leaves as it is" in lines(out)


def test_a_hook_that_names_this_link_s_folder_but_runs_another_program_is_not_named(client, owner, served, capsys,
                                                                                     home, project):
    """Only a group that runs a link client on this link's directory is taken for an earlier copy of the block: a
    command that names the directory and runs something else is left, and not named."""
    (project / ".claude").mkdir()
    path = settings_of(project)
    d = home / ".proofpane-ade" / "mac"
    theirs = {"type": "command", "command": f'ls "{d}"', "timeout": 30}
    path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [theirs]}]}}))
    code, out, err = setup(capsys, served, ticket(client, owner).lower())
    assert code == 0, err
    assert json.loads(path.read_text())["hooks"]["Stop"][0] == {"hooks": [theirs]}
    assert not any(line.startswith("ADE: also in") for line in lines(out))

# ---- refused before anything is sent -------------------------------------------------------------------------------

@pytest.mark.parametrize("text, words", [
    ("{not json", "is not JSON"),
    ("[]", "does not hold a JSON object"),
    ('{"hooks": []}', '"hooks" is not an object'),
    ('{"hooks": {"Stop": {}}}', '"hooks"."Stop" is not a list'),
    ('{"env": "x"}', '"env" is not an object'),
    ('{"disableAllHooks": "no"}', '"disableAllHooks" is not true or false'),
])
def test_a_settings_file_setup_cannot_merge_into_is_refused_before_anything_is_sent(client, owner, served, capsys,
                                                                                   home, project, text, words):
    (project / ".claude").mkdir()
    path = settings_of(project)
    path.write_text(text)
    code, out, err = setup(capsys, served, ticket(client, owner).lower())
    assert code == 2
    assert words in err and NOTHING in err and str(path) in err
    assert path.read_text() == text and list((project / ".claude").iterdir()) == [path]
    assert links_named("mac") == 0 and not (home / ".proofpane-ade" / "mac").exists()


def test_a_settings_file_that_is_a_symbolic_link_is_refused_before_anything_is_sent(client, owner, served, capsys,
                                                                                   home, project, tmp_path):
    (project / ".claude").mkdir()
    target = tmp_path / "elsewhere.json"
    target.write_text("{}")
    path = settings_of(project)
    path.symlink_to(target)
    code, out, err = setup(capsys, served, ticket(client, owner).lower())
    assert code == 2
    assert "is a symbolic link" in err and NOTHING in err
    assert target.read_text() == "{}" and path.is_symlink()
    assert links_named("mac") == 0


def test_the_hooks_run_the_installed_client_which_must_be_there_and_be_this_one(client, owner, served, capsys, home,
                                                                                 project):
    installed = home / ".proofpane-ade" / "ade_link.py"
    installed.unlink()
    code, out, err = setup(capsys, served, ticket(client, owner).lower())
    assert code == 2 and "which is not there" in err and str(installed) in err and NOTHING in err
    installed.write_text("print('another client')\n")
    code, out, err = setup(capsys, served, ticket(client, owner).lower())
    assert code == 2 and "is not this client" in err and NOTHING in err
    assert links_named("mac") == 0 and not settings_of(project).exists()


def test_a_home_folder_the_hook_lines_cannot_carry_is_refused(client, owner, served, capsys, tmp_path, monkeypatch):
    h = tmp_path / 'ho"me'
    (h / ".proofpane-ade").mkdir(parents=True)
    shutil.copy(ade_link.__file__, h / ".proofpane-ade" / "ade_link.py")
    monkeypatch.setenv("HOME", str(h))
    p = tmp_path / "project"
    p.mkdir()
    monkeypatch.chdir(p)
    code, out, err = setup(capsys, served, ticket(client, owner).lower())
    assert code == 2 and "cannot carry" in err and NOTHING in err
    assert links_named("mac") == 0 and not settings_of(p).exists()


def test_an_interpreter_path_with_a_space_is_refused(client, owner, served, capsys, home, project, monkeypatch):
    monkeypatch.setattr(sys, "executable", "/opt/my python/bin/python")
    code, out, err = setup(capsys, served, ticket(client, owner).lower())
    assert code == 2 and "cannot carry" in err and "/opt/my python/bin/python" in err and NOTHING in err
    assert links_named("mac") == 0 and not settings_of(project).exists()



@pytest.mark.parametrize("ch", list("&;|()<>*?[]{}"))
def test_an_interpreter_path_with_a_character_the_shell_reads_is_refused(client, owner, served, capsys, home, project,
                                                                        monkeypatch, ch):
    """The interpreter starts each hook line unquoted, so a character the shell reads there (a separator, a
    redirection, a group, a glob or a brace) would split the line or change the path it runs: refused, as a space
    is, before anything is sent."""
    path = f"/opt/py{ch}3/bin/python"
    monkeypatch.setattr(sys, "executable", path)
    code, out, err = setup(capsys, served, ticket(client, owner).lower())
    assert code == 2 and "cannot carry" in err and path in err and NOTHING in err
    assert links_named("mac") == 0 and not settings_of(project).exists()


def test_an_interpreter_path_with_a_space_of_another_kind_is_refused(client, owner, served, capsys, home, project,
                                                                    monkeypatch):
    """A space the shell does not split on (a no-break space) is refused in the interpreter's path as well: the line
    would run, but a path that reads as two words is taken for a mistake, and stopped before it is written."""
    path = "/opt/my\u00a0python/bin/python"
    monkeypatch.setattr(sys, "executable", path)
    code, out, err = setup(capsys, served, ticket(client, owner).lower())
    assert code == 2 and "cannot carry" in err and repr(path) in err and NOTHING in err
    assert links_named("mac") == 0 and not settings_of(project).exists()

# ---- kinds and outcomes -----------------------------------------------------------------------------------------

def test_a_cloud_session_is_linked_and_no_hook_is_written(client, owner, served, capsys, home, project):
    # a session in the cloud runs the client from a checkout: no installed copy is asked for
    (home / ".proofpane-ade" / "ade_link.py").unlink()
    code, out, err = setup(capsys, served, ticket(client, owner).lower(), "web", "cloud")
    assert code == 0, err
    assert links_named("web") == 1
    assert "ADE: a cloud session reads no .claude/settings.local.json, so no hook was written" in lines(out)
    assert not (project / ".claude").exists()


def test_a_ticket_the_ade_refuses_writes_nothing(client, owner, served, capsys, home, project):
    code, out, err = setup(capsys, served, "PP-ZZZZZZZZ")
    assert code == 2 and "ADE: refused (" in err                       # pair's own refusal, as pair says it
    assert not (project / ".claude").exists() and "the hooks go in" not in out


def test_url_and_ticket_come_together_or_not_at_all(client, owner, served, capsys, home, project):
    """The line the page gives carries both; the hooks alone take neither. One without the other is a mistyped line:
    refused before anything is sent."""
    for given in (["--url", served], ["--ticket", ticket(client, owner).lower()]):
        code, out, err = run(capsys, "setup", *given, "--name", "mac", "--kind", "local")
        assert code == 2 and "setup takes --url and --ticket together" in err and NOTHING in err
    assert links_named("mac") == 0 and not (project / ".claude").exists()


def test_a_settings_file_that_changes_while_it_pairs_is_checked_again_and_left(client, owner, served, capsys, home,
                                                                              project, monkeypatch):
    """A pairing waits on its hold, and the file may change meanwhile: it is read again before anything is written
    to it, and one setup cannot merge into then is left as it is, the link standing."""
    real = ade_link.cmd_pair

    def pairs_and_the_file_changes(args):
        code = real(args)
        (project / ".claude").mkdir()
        settings_of(project).write_text("{not json")
        return code
    monkeypatch.setattr(ade_link, "cmd_pair", pairs_and_the_file_changes)
    code, out, err = setup(capsys, served, ticket(client, owner).lower())
    assert code == 2 and "is not JSON" in err and "the link stands" in err
    assert settings_of(project).read_text() == "{not json" and links_named("mac") == 1
    assert list((project / ".claude").iterdir()) == [settings_of(project)]          # no copy, no other file

# ---- the hooks alone --------------------------------------------------------------------------------------------

def test_the_hooks_alone_for_a_session_linked_already_send_nothing(client, owner, served, capsys, home, project,
                                                                   monkeypatch):
    d = pair(client, owner, served, capsys, home / ".proofpane-ade" / "mac", "mac")

    def no_request(*a, **k):
        raise AssertionError("setup sent a request")
    monkeypatch.setattr(ade_link.Link, "request", no_request)
    code, out, err = run(capsys, "setup", "--name", "mac", "--kind", "local")
    assert code == 0, err
    link_id = link_id_of(home)
    assert f"ADE: mac is linked already (link {link_id}); nothing was sent" in lines(out)
    assert json.loads(settings_of(project).read_text()) == expected(home, "mac", link_id, pinned=False)
    assert d.exists()


def test_the_hooks_alone_need_a_linked_directory(capsys, home, project):
    code, out, err = run(capsys, "setup", "--name", "mac", "--kind", "local")
    assert code == 2 and "holds no link" in err and NOTHING in err
    assert not (project / ".claude").exists()


def test_the_stop_hook_follows_a_pin_made_after_setup(client, owner, served, device, capsys, home, project):
    assert setup(capsys, served, ticket(client, owner).lower())[0] == 0
    d = home / ".proofpane-ade" / "mac"
    code, out, err = run(capsys, "--dir", d, "pin")
    assert code == 0, err
    code, out, err = run(capsys, "setup", "--name", "mac", "--kind", "local")
    assert code == 0, err
    want = expected(home, "mac", link_id_of(home), pinned=True)
    assert json.loads(settings_of(project).read_text())["hooks"]["PreToolUse"] \
        == [g for g in want["hooks"]["PreToolUse"] if not is_stop_hook(g)] \
        + [g for g in want["hooks"]["PreToolUse"] if is_stop_hook(g)]       # appended after what was there
    assert "ADE: added: PreToolUse 1" in lines(out)
    assert "so the stop hook is not added" not in out


# ---- where the file goes ----------------------------------------------------------------------------------------

def test_in_a_git_repository_the_file_goes_at_its_root(client, owner, served, capsys, home, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    sub = repo / "pkg" / "sub"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    code, out, err = setup(capsys, served, ticket(client, owner).lower())
    assert code == 0, err
    assert settings_of(repo).exists() and not (sub / ".claude").exists()
    assert f"ADE: the hooks go in {settings_of(repo)}" in lines(out)


def test_a_repository_whose_root_is_the_home_folder_keeps_the_file_in_the_folder_started_in(client, owner, served,
                                                                                           capsys, home, tmp_path,
                                                                                           monkeypatch):
    (home / ".git").mkdir()
    proj = home / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    code, out, err = setup(capsys, served, ticket(client, owner).lower())
    assert code == 0, err
    assert settings_of(proj).exists() and not (home / ".claude").exists()


def test_folder_names_where_the_file_goes(client, owner, served, capsys, home, project, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    code, out, err = setup(capsys, served, ticket(client, owner).lower(), "mac", "local", "--folder", str(other))
    assert code == 0, err
    assert settings_of(other).exists() and not (project / ".claude").exists()


def test_a_repository_root_that_is_not_the_user_s_own_keeps_the_file_in_the_folder_started_in(client, owner, served,
                                                                                             capsys, home, tmp_path,
                                                                                             monkeypatch):
    """Claude Code keeps the file where the session started when the repository's root, its .git or its .claude is
    not the user's own; so does setup. Here the user is another one than the owner of those entries."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    sub = repo / "sub"
    sub.mkdir()
    monkeypatch.chdir(sub)
    theirs = os.lstat(repo / ".git").st_uid
    monkeypatch.setattr(ade_link.os, "getuid", lambda: theirs + 1)
    code, out, err = setup(capsys, served, ticket(client, owner).lower())
    assert code == 0, err
    assert settings_of(sub).exists() and not (repo / ".claude").exists()



def test_a_second_copy_in_the_same_second_takes_a_number_and_leaves_the_first(tmp_path, monkeypatch):
    """The file as it was is kept under a name of its own, in the link's directory: a copy made in the same second as
    another takes a number, and never writes over the first."""
    monkeypatch.setattr(ade_link.time, "strftime", lambda fmt, t=None: "20261107T120000Z")
    link_dir = tmp_path / "mac"
    first = ade_link._keep_as_it_was(b'{"a": 1}', str(link_dir))
    second = ade_link._keep_as_it_was(b'{"a": 2}', str(link_dir))
    kept = link_dir / "settings-backups" / "settings.local.json.bak-20261107T120000Z"
    assert first == str(kept) and second == f"{kept}-2"
    assert open(first).read() == '{"a": 1}' and open(second).read() == '{"a": 2}'
