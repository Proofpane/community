"""The skills hook (activity.py --skills): each `Skill` call that reached PostToolUse or
PostToolUseFailure and each typed command's expansion that is a skill the session has appends one line to the session's own
skills.jsonl, with the skill's name, its kind, which path, whether it failed and the version it used, read as the hook
runs; never a call's arguments; bounded; and the hook ends 0 whatever it is given. The events are the ones Claude Code
gives, run through the script as Claude Code runs it."""

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time

import pytest

from conftest import ROOT, manual_or_skip
from proofpane_workspace import activity, mcp, settings, workspace as workspace_module
from skills_readme import stated as stated_in, text as file_text
from workspace_fixtures import git, make_repo

SCRIPT = os.path.join(ROOT, "proofpane_workspace", "activity.py")
PLUGIN = os.path.join(ROOT, "core_plugin")
SECRET = "sk-ant-api03-must-never-reach-the-skills-file"
FIELDS = {"id", "at", "skill", "kind", "path", "failed", "version"}
SKILL = "---\nname: {name}\ndescription: A skill of the project's own.\n---\n\nDo the work.\n"
CORE = "proofpane-core:ai-risk-assessment"


def _env(folder, config, **extra):
    env = {"PATH": os.environ["PATH"], "HOME": str(folder), "PP_SESSION_DIR": str(folder),
           "CLAUDE_CONFIG_DIR": str(config), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    env.update(extra)
    return env


def _run(event, folder, config, **extra):
    stdin = event if isinstance(event, str) else json.dumps(event)
    return subprocess.run([sys.executable, SCRIPT, "--skills"], input=stdin, capture_output=True, text=True,
                          env=_env(folder, config, **extra), timeout=60)


def _lines(folder):
    path = folder / "skills.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _called(skill, response=None, failed=False, **extra):
    """What Claude Code gives PostToolUse (or PostToolUseFailure) for a `Skill` call: the call's input, and its result."""
    event = {"hook_event_name": "PostToolUseFailure" if failed else "PostToolUse", "tool_name": "Skill",
             "session_id": "s1", "tool_use_id": "toolu_1", "tool_input": {"skill": skill, "args": SECRET}}
    if failed:
        event["error"] = SECRET
        event["is_interrupt"] = False
    else:
        event["tool_response"] = response if response is not None else {"success": True, "commandName": skill,
                                                                        "result": SECRET, "status": "inline"}
    event.update(extra)
    return event


def _typed(name, expansion="slash_command"):
    """What Claude Code gives UserPromptExpansion for a command the person typed."""
    return {"hook_event_name": "UserPromptExpansion", "expansion_type": expansion, "command_name": name,
            "command_args": SECRET, "command_source": "projectSettings", "prompt": f"/{name} {SECRET}",
            "session_id": "s1"}


def _commit(work, message="a change"):
    git("add", "-A", cwd=work)
    git("commit", "-q", "-m", message, cwd=work)
    return git("rev-parse", "HEAD", cwd=work)


def _skill(work, name, folder=None, body=None):
    """A skill of the worktree, written (not committed): its folder under .claude/skills/."""
    folder = folder or name
    path = os.path.join(work, ".claude", "skills", folder)
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "SKILL.md"), "w") as fh:
        fh.write(body if body is not None else SKILL.format(name=name))


@pytest.fixture
def session(tmp_path):
    """A session's folder with a worktree that is a git repository, and an account's folder of its own (the hook looks
    in it for a skill that Claude Code would run first)."""
    folder, config = tmp_path / "session", tmp_path / "account"
    folder.mkdir()
    config.mkdir()
    make_repo(str(folder / "work"))
    return folder, config, str(folder / "work")


# what is counted, and how a line looks


def test_a_call_claude_chose_is_one_line_and_never_its_arguments(session):
    folder, config, work = session
    _skill(work, "review")
    commit = _commit(work)
    done = _run(_called("review"), folder, config)
    assert done.returncode == 0 and done.stdout == "" and done.stderr == ""
    (line,) = _lines(folder)
    assert set(line) == FIELDS
    assert re.fullmatch(r"[0-9a-f]{16}", line["id"]) and abs(line["at"] - time.time()) < 60
    assert (line["skill"], line["kind"], line["path"], line["failed"]) == ("review", "business", "claude", False)
    assert line["version"] == commit
    assert SECRET not in (folder / "skills.jsonl").read_text()          # not its args, its result or an error
    assert not (folder / "activity.json").exists() and not (folder / "calls.jsonl").exists()


def test_a_call_that_failed_is_one_line_that_says_so(session):
    folder, config, work = session
    _skill(work, "review")
    _commit(work)
    assert _run(_called("review", failed=True), folder, config).returncode == 0
    (line,) = _lines(folder)
    assert line["failed"] is True and line["path"] == "claude" and line["kind"] == "business"
    assert SECRET not in (folder / "skills.jsonl").read_text()


def test_each_call_is_a_line_of_its_own_with_an_id_of_its_own(session):
    folder, config, work = session
    for _ in range(3):
        _run(_called("review"), folder, config)
    assert len({line["id"] for line in _lines(folder)}) == 3


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("written", [CORE, "/" + CORE, " " + CORE, f"  /{CORE}\t", "\n" + CORE + " "])
def test_the_name_is_the_model_s_own_text_as_the_tool_takes_it_whatever_the_outcome(session, written, failed):
    """Claude Code 2.1.304's Skill tool answers with `commandName` = the model's text trimmed with one leading `/` off
    (read in its code): not the name it resolved. A call that ran carries that; a call that failed carries no result,
    and its input is read the same way, so one skill is one name either way."""
    folder, config, work = session
    event = _called(written, failed=failed)
    if not failed:
        # what the tool makes of the model's text
        event["tool_response"] = {"success": True, "commandName": CORE, "status": "inline"}
    _run(event, folder, config)
    (line,) = _lines(folder)
    assert (line["skill"], line["kind"], line["failed"]) == (CORE, "core", failed)


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("bare", ["ai-risk-assessment", "ai-governance-intake"])
def test_a_core_skill_the_model_names_without_its_prefix_is_the_core_skill_under_its_full_name(session, bare, failed):
    """Both of the plugin's skills have a frontmatter name without a colon, which Claude Code 2.1.304 gives a plugin
    skill as an alias (read in its code: the plugin command's `aliases`, and the Skill tool's resolver, which finds a
    command by its name, its user-facing name or an alias): the call runs, `commandName` is the model's own text, and
    the hook counts the skill it ran, under the full name, as a typed call is (it was once counted `other` under the
    bare name, for a reason that was wrong)."""
    folder, config, work = session
    event = _called(bare, failed=failed)
    if not failed:
        event["tool_response"] = {"success": True, "commandName": bare, "status": "inline"}
    _run(event, folder, config)
    (line,) = _lines(folder)
    assert (line["skill"], line["kind"], line["path"], line["failed"]) == (
        f"proofpane-core:{bare}", "core", "claude", failed)
    assert line["version"] == activity._core_version(PLUGIN, bare)


def test_a_core_skill_called_by_its_frontmatter_name_is_counted_under_its_folder_s_name(session, tmp_path):
    """The alias a plugin skill's frontmatter name gives is another spelling of the one skill, whose full name is the
    plugin's and its folder's: where the two differ, the count is under the folder's, as a typed call of it is (both
    shipped skills have the one name, so only a plugin of this test's own tells the folder from the alias)."""
    folder, config, work = session
    root = tmp_path / "plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "proofpane-core", "version": "1.0.0"}))
    (root / "skills" / "risk-x").mkdir(parents=True)
    (root / "skills" / "risk-x" / "SKILL.md").write_text("---\nname: risk-alias\n---\nOne.\n")
    assert activity.skills_record(_called("risk-alias"), str(folder), plugin=str(root), shadows=[])
    (line,) = _lines(folder)
    assert (line["skill"], line["kind"], line["path"]) == ("proofpane-core:risk-x", "core", "claude")
    assert line["version"] == activity._core_version(str(root), "risk-x")


def test_a_bare_name_that_the_worktree_holds_is_the_project_s_skill_before_the_plugin_s_alias(session, tmp_path):
    """Claude Code resolves a call by a skill of that exact name before it tries an alias, so a worktree that holds a
    skill called `ai-risk-assessment` has that one run (and counted as a business skill), not the core plugin's."""
    folder, config, work = session
    _skill(work, "ai-risk-assessment")
    commit = _commit(work)
    nothing = [str(tmp_path / "nothing" / "skills")]
    assert activity.skills_record(_called("ai-risk-assessment"), str(folder), shadows=nothing)
    assert [(l["skill"], l["kind"], l["version"]) for l in _lines(folder)] == [
        ("ai-risk-assessment", "business", commit)]


def test_a_bare_name_that_the_account_s_or_the_organisation_s_skills_hold_is_theirs_not_the_plugin_s(session, tmp_path):
    folder, config, work = session
    for where in ("account", "organisation"):
        skills = tmp_path / where / "skills"
        (skills / "ai-risk-assessment").mkdir(parents=True)
        (skills / "ai-risk-assessment" / "SKILL.md").write_text(SKILL.format(name="ai-risk-assessment"))
        assert activity.skills_record(_called("ai-risk-assessment"), str(folder), shadows=[str(skills)])
    assert [(l["skill"], l["kind"], l["version"]) for l in _lines(folder)] == [
        ("ai-risk-assessment", "other", None)] * 2


def test_a_plugin_skill_named_with_a_colon_has_no_alias_and_is_not_counted_as_core(session, tmp_path):
    """Claude Code gives a plugin skill an alias only where its frontmatter name has no colon (`!lt.includes(":")`)."""
    folder, config, work = session
    root = tmp_path / "plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "proofpane-core", "version": "1.0.0"}))
    (root / "skills" / "risk").mkdir(parents=True)
    (root / "skills" / "risk" / "SKILL.md").write_text("---\nname: other:risk\n---\nOne.\n")
    assert activity.skills_record(_called("other:risk"), str(folder), plugin=str(root), shadows=[])
    assert [(l["skill"], l["kind"], l["version"]) for l in _lines(folder)] == [("other:risk", "other", None)]


def test_a_bare_name_of_a_plugin_folder_that_could_not_be_a_core_name_is_not_counted_as_core(session, tmp_path):
    folder, config, work = session
    root = tmp_path / "plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "proofpane-core", "version": "1.0.0"}))
    (root / "skills" / "my risk").mkdir(parents=True)
    (root / "skills" / "my risk" / "SKILL.md").write_text("---\nname: risk\n---\nOne.\n")
    assert activity.skills_record(_called("risk"), str(folder), plugin=str(root), shadows=[])
    assert [(l["skill"], l["kind"], l["version"]) for l in _lines(folder)] == [("risk", "other", None)]


def test_a_typed_command_that_names_a_core_skill_without_its_prefix_is_counted_as_the_core_skill(session):
    folder, config, work = session
    _run(_typed("ai-risk-assessment"), folder, config)
    (line,) = _lines(folder)
    assert (line["skill"], line["kind"], line["path"], line["failed"]) == (CORE, "core", "typed", None)


def test_the_result_s_name_is_read_before_the_call_s_where_it_fits(session):
    """A control of the rule the manual states (`tool_response.commandName` where it fits, else the call's `skill`): in
    2.1.304 the two are the one text, so no event of that build tells them apart, and a build whose result named another
    skill would be counted by the result's name."""
    folder, config, work = session
    _skill(work, "review")
    _commit(work)
    event = _called("something-else")
    event["tool_response"] = {"success": True, "commandName": "review", "status": "inline"}
    _run(event, folder, config)
    assert [(l["skill"], l["kind"]) for l in _lines(folder)] == [("review", "business")]


@pytest.mark.parametrize("response", [None, "launched", {"success": True}, {"commandName": 5}, {"commandName": "a b"},
                                       {"commandName": ""}, {"commandName": "../x"}])
def test_without_a_name_in_the_result_the_call_s_own_is_the_name(session, response):
    folder, config, work = session
    event = _called("review")
    event["tool_response"] = response
    _run(event, folder, config)
    assert [line["skill"] for line in _lines(folder)] == ["review"]


def test_a_leading_slash_in_the_call_is_not_part_of_the_name(session):
    # the tool accepts one (tengu_skill_tool_slash_prefix): `/review` and `review` are one skill
    folder, config, work = session
    _skill(work, "review")
    event = _called("/review")
    event["tool_response"] = None
    _run(event, folder, config)
    _run(_called("review", failed=True), folder, config)
    assert [line["skill"] for line in _lines(folder)] == ["review", "review"]


def test_an_event_with_a_big_result_in_it_is_read_whole_and_counted(session):
    """A forked skill's result is in the event, and can be far past the 64 KB the state's hooks read: the count of the call
    is not lost for it."""
    folder, config, work = session
    _skill(work, "review")
    event = _called("review", {"success": True, "commandName": "review", "status": "forked", "agentId": "a1",
                               "result": "x" * 300_000})
    done = _run(event, folder, config)
    assert done.returncode == 0 and [(l["skill"], l["kind"]) for l in _lines(folder)] == [("review", "business")]


@pytest.mark.parametrize("name, kind", [
    ("review", "business"), (CORE, "core"), ("other-plugin:review", "other"), ("init", "other"),
    ("proofpane-core:nope", "other"), ("not-a-skill-of-anyone", "other")])
def test_a_call_that_failed_is_counted_by_the_name_rule_of_a_call_that_ran(session, name, kind):
    """The plan counts each Skill call that reaches PostToolUse or PostToolUseFailure. In 2.1.304 a call whose name no
    skill has is refused by the tool's own check before any hook runs (read in its code: the refusal is returned as an
    error result before the PreToolUse stage), so a failure names a skill that exists, as a success does, and there is
    no reason to count its kind of skill less: the page's `failed` count is of the calls beside it."""
    folder, config, work = session
    _skill(work, "review")
    _commit(work)
    for failed in (False, True):
        assert _run(_called(name, failed=failed), folder, config).returncode == 0
    assert [(l["skill"], l["kind"], l["path"], l["failed"]) for l in _lines(folder)] == [
        (name, kind, "claude", False), (name, kind, "claude", True)]
    assert SECRET not in (folder / "skills.jsonl").read_text()


def test_a_call_the_person_interrupted_counts_as_one_that_failed(session):
    """PostToolUseFailure also fires for an interruption (`is_interrupt`); the hook reads only the event's name, so an
    interrupted call is a failed one, and the manual says so."""
    folder, config, work = session
    _skill(work, "review")
    _commit(work)
    event = _called("review", failed=True)
    event["is_interrupt"] = True
    _run(event, folder, config)
    assert [(l["skill"], l["failed"]) for l in _lines(folder)] == [("review", True)]


@pytest.mark.parametrize("name", [
    "apps/web:deploy", "a b", "", " ", "../x", "proofpane-core:../x", "x" * 81, "-lead", "x\ny", "é", "a;b", 5, None,
    ["a"], {"a": 1}, "//review", "/ review", "/"])
def test_a_name_that_does_not_fit_a_tool_s_name_is_not_counted(session, name):
    folder, config, work = session
    event = _called("x")
    event["tool_input"] = {"skill": name}
    event["tool_response"] = None
    assert _run(event, folder, config).returncode == 0
    assert _lines(folder) == []
    typed = _typed("x")
    typed["command_name"] = name
    assert _run(typed, folder, config).returncode == 0
    assert _lines(folder) == []


@pytest.mark.parametrize("event", [
    {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}},
    {"hook_event_name": "PostToolUse", "tool_name": "skill", "tool_input": {"skill": "review"}},
    {"hook_event_name": "PostToolUse", "tool_input": {"skill": "review"}},
    {"hook_event_name": "PostToolUse", "tool_name": "Skill", "tool_input": "review"},
    {"hook_event_name": "PostToolUse", "tool_name": "Skill"},
    {"hook_event_name": "PreToolUse", "tool_name": "Skill", "tool_input": {"skill": "review"}},
    {"hook_event_name": "Stop"},
    {"hook_event_name": "UserPromptSubmit", "prompt": "/review"},
    {"hook_event_name": "UserPromptExpansion", "expansion_type": "mcp_prompt", "command_name": "review"},
    {"hook_event_name": "UserPromptExpansion", "command_name": "review"},
    {"hook_event_name": "UserPromptExpansion", "expansion_type": "slash_command"},
])
def test_what_is_no_skill_call_is_not_counted(session, event):
    folder, config, work = session
    _skill(work, "review")
    _commit(work)
    assert _run(event, folder, config).returncode == 0
    assert _lines(folder) == []


@pytest.mark.parametrize("raw", ["", "not json", "[]", "null", "5", '"x"', "{", "\x00" * 50])
def test_the_hook_ends_0_whatever_it_is_given(session, raw):
    folder, config, work = session
    done = _run(raw, folder, config)
    assert done.returncode == 0 and done.stdout == "" and _lines(folder) == []


def test_a_program_a_person_started_in_a_shell_does_not_speak_for_the_session(session):
    folder, config, work = session
    assert _run(_called("review"), folder, config, PP_SHELL="1").returncode == 0
    assert _lines(folder) == []


def test_the_session_is_found_from_the_event_s_folder_where_the_variable_is_gone(session):
    # a hook whose environment was redacted: the session whose worktree the event came from, as the map finds it
    folder, config, work = session
    (folder / "session.json").write_text("{}")
    env = _env(folder, config)
    del env["PP_SESSION_DIR"]
    event = _called("review")
    event["cwd"] = work
    subprocess.run([sys.executable, SCRIPT, "--skills"], input=json.dumps(event), text=True, capture_output=True, env=env,
                   timeout=60)
    assert [line["skill"] for line in _lines(folder)] == ["review"]


def test_no_session_no_line(tmp_path):
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path)}
    done = subprocess.run([sys.executable, SCRIPT, "--skills"], input=json.dumps(_called("review")), text=True,
                          capture_output=True, env=env, timeout=60, cwd=tmp_path)
    assert done.returncode == 0 and list(tmp_path.iterdir()) == []


# the kind: a core skill, a business skill, any other


def test_a_core_skill_is_the_plugin_s_own(session):
    folder, config, work = session
    _run(_called(CORE), folder, config)
    (line,) = _lines(folder)
    assert (line["skill"], line["kind"], line["path"]) == (CORE, "core", "claude")
    assert re.fullmatch(r"1\.0\.0\+[0-9a-f]{64}", line["version"])


@pytest.mark.parametrize("name", ["proofpane-core:nope", "proofpane-core:",
                                  "proofpane-core:a:b", "proofpane-core-daemon:ai-risk-assessment",
                                  "other-plugin:ai-risk-assessment"])
def test_a_name_with_the_core_prefix_the_plugin_does_not_hold_is_not_a_core_skill(session, name):
    folder, config, work = session
    _run(_called(name, {}), folder, config)
    (line,) = _lines(folder)
    assert line["kind"] == "other" and line["version"] is None


def test_a_skill_of_the_worktree_is_a_business_skill_by_its_folder_s_name(session):
    folder, config, work = session
    _skill(work, "review")
    commit = _commit(work)
    _run(_called("review"), folder, config)
    (line,) = _lines(folder)
    assert (line["kind"], line["version"]) == ("business", commit)


def test_it_is_also_called_by_the_name_its_frontmatter_gives(session):
    # a folder `deploy-staging` whose SKILL.md says `name: deploy`: Claude Code calls it `deploy`, and its folder's name
    # also invokes it
    folder, config, work = session
    _skill(work, "deploy", folder="deploy-staging")
    commit = _commit(work)
    for called in ("deploy", "deploy-staging"):
        _run(_called(called), folder, config)
    assert [(l["skill"], l["kind"], l["version"]) for l in _lines(folder)] == [("deploy", "business", commit),
                                                                              ("deploy-staging", "business", commit)]


@pytest.mark.parametrize("front", ['name: "deploy"', "name: 'deploy'", "name:deploy", "name :  deploy  ",
                                    'name: "deploy" \t', "name: deploy\t"])
def test_a_frontmatter_name_is_read_as_a_value_with_or_without_quotes(session, front):
    folder, config, work = session
    _skill(work, "x", folder="other-folder", body=f"---\n{front}\ndescription: d\n---\nBody\n")
    _commit(work)
    _run(_called("deploy"), folder, config)
    assert [l["kind"] for l in _lines(folder)] == ["business"]


@pytest.mark.parametrize("body", [
    "name: deploy\n---\nno opening line\n",                 # no frontmatter
    "---\ndescription: d\n---\nname: deploy\n",              # the name is in the body
    "---\nmetadata:\n  name: deploy\n---\nBody\n",          # nested, not the skill's own key
    "---\nrename: deploy\n---\nBody\n",
    "---\nname: deploy\x0b\n---\nBody\n",                  # only blanks (spaces, tabs) are cut off a name, not a \\v
])
def test_a_name_that_is_not_the_frontmatter_s_own_key_names_no_skill(session, body):
    folder, config, work = session
    _skill(work, "x", folder="other-folder", body=body)
    _commit(work)
    _run(_called("deploy"), folder, config)
    assert [l["kind"] for l in _lines(folder)] == ["other"]


def test_a_byte_order_mark_hides_the_frontmatter_s_name(session):
    """The manual says the pattern is a line pattern, not YAML read: a file that begins with a byte order mark does not
    begin with `---`, so the name is not found and a call of it is `other`; the same file without the mark is found."""
    folder, config, work = session
    _skill(work, "x", folder="other-folder")
    path = os.path.join(work, ".claude", "skills", "other-folder", "SKILL.md")
    body = b"---\nname: deploy\ndescription: d\n---\nBody\n"
    for mark, kind in ((b"\xef\xbb\xbf", "other"), (b"", "business")):
        with open(path, "wb") as fh:
            fh.write(mark + body)
        _commit(work)
        _run(_called("deploy"), folder, config)
        assert _lines(folder)[-1]["kind"] == kind


def test_a_name_past_the_first_4096_bytes_of_the_file_is_not_found(session):
    folder, config, work = session
    _skill(work, "x", folder="other-folder", body="---\n# " + "x" * 4200 + "\nname: deploy\n---\nBody\n")
    _commit(work)
    _run(_called("deploy"), folder, config)
    assert [l["kind"] for l in _lines(folder)] == ["other"]
    # the control: the same file with its name inside the bound is found
    _skill(work, "x", folder="other-folder", body="---\n# " + "x" * 3000 + "\nname: deploy\n---\nBody\n")
    _commit(work)
    _run(_called("deploy"), folder, config)
    assert [l["kind"] for l in _lines(folder)] == ["other", "business"]


def test_a_skill_file_is_read_only_as_far_as_the_bound(tmp_path, monkeypatch):
    """A repository's file can be any size: the frontmatter is looked for in its first FRONT bytes, no more is read."""
    folder = tmp_path / "skills" / "f"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_bytes(b"---\n# " + b"x" * 5_000_000 + b"\nname: deploy\n---\n")
    asked = []
    real = activity._read_file
    monkeypatch.setattr(activity, "_read_file", lambda path, most: (asked.append(most), real(path, most))[1])
    assert activity._holds(str(tmp_path / "skills"), "deploy") is None
    assert asked == [activity.FRONT] and activity.FRONT == 4096
    assert len(real(str(folder / "SKILL.md"), activity.FRONT)) == activity.FRONT


def test_only_the_first_folders_by_name_are_looked_in(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    for n in (5, 2, 8, 0, 7, 3, 1, 6, 4):                  # made in no order a listing could be taken for sorted
        (skills / f"f{n}").mkdir(parents=True)
        (skills / f"f{n}" / "SKILL.md").write_text(f"---\nname: skill-{n}\n---\n")
    monkeypatch.setattr(activity, "FOLDERS", 4)
    assert [activity._holds(str(skills), f"skill-{n}") for n in range(9)] == ["f0", "f1", "f2", "f3"] + [None] * 5
    assert activity.FOLDERS == 4 and activity.ENTRIES == 2000
    monkeypatch.undo()
    assert activity.FOLDERS == 300


def test_a_directory_is_listed_only_as_far_as_its_bound(tmp_path, monkeypatch):
    """A directory with a very long listing is not read whole: only ENTRIES of its entries are taken (by name, FOLDERS
    of them looked in)."""
    skills = tmp_path / "skills"
    for n in range(25):
        (skills / f"f{n:02d}").mkdir(parents=True)
    monkeypatch.setattr(activity, "ENTRIES", 10)
    looked = []
    monkeypatch.setattr(activity, "_front_name", lambda path: looked.append(path))
    assert activity._holds(str(skills), "nothing") is None
    assert len(looked) == 10


def test_a_hostile_folder_costs_the_hook_milliseconds_not_seconds(tmp_path):
    """The name line's pattern takes linear time. 299 folders whose SKILL.md is 4 KB of blanks after `name:x` (a lazy
    tail `(.*?)[ \\t]*$` backtracked quadratically on them: 40 ms each, 12 s for 300, on every call whose name is no
    folder's: a repository the owner opens can hold them) are read in well under a second, and the real folder, the
    last by name, is still found."""
    skills = tmp_path / "skills"
    for n in range(activity.FOLDERS - 1):
        (skills / f"f{n:04d}").mkdir(parents=True)
        (skills / f"f{n:04d}" / "SKILL.md").write_text("---\nname:x" + " " * 4080 + "y\n")
    (skills / "zzzz-real").mkdir()
    (skills / "zzzz-real" / "SKILL.md").write_text(SKILL.format(name="wanted"))
    begun = time.monotonic()
    found = activity._holds(str(skills), "wanted")
    took = time.monotonic() - begun
    assert found == "zzzz-real"
    assert took < 3, f"{took:.1f} s"


def test_a_skill_the_worktree_does_not_hold_is_any_other(session):
    folder, config, work = session
    _skill(work, "review")
    _commit(work)
    _run(_called("commit"), folder, config)
    (line,) = _lines(folder)
    assert (line["skill"], line["kind"], line["version"]) == ("commit", "other", None)


def test_a_folder_without_a_skill_file_is_no_skill(session):
    folder, config, work = session
    os.makedirs(os.path.join(work, ".claude", "skills", "review"))
    (folder / "work" / ".claude" / "skills" / "review" / "notes.md").write_text("x")
    _commit(work)
    _run(_called("review"), folder, config)
    assert [l["kind"] for l in _lines(folder)] == ["other"]


def test_a_skills_path_that_is_a_file_holds_no_skill(session):
    folder, config, work = session
    os.makedirs(os.path.join(work, ".claude"))
    with open(os.path.join(work, ".claude", "skills"), "w") as fh:
        fh.write("not a folder\n")
    assert _run(_called("review"), folder, config).returncode == 0
    assert [(l["skill"], l["kind"]) for l in _lines(folder)] == [("review", "other")]


def test_a_skill_file_that_is_a_fifo_never_holds_the_hook(session):
    folder, config, work = session
    os.makedirs(os.path.join(work, ".claude", "skills", "review"))
    os.mkfifo(os.path.join(work, ".claude", "skills", "review", "SKILL.md"))
    os.makedirs(os.path.join(work, ".claude", "skills", "other"))
    os.mkfifo(os.path.join(work, ".claude", "skills", "other", "SKILL.md"))
    begun = time.time()
    done = _run(_called("review"), folder, config)
    assert done.returncode == 0 and time.time() - begun < 20
    assert [l["kind"] for l in _lines(folder)] == ["other"]


def test_a_skill_of_the_account_s_own_is_run_before_the_project_s_and_so_is_other(session):
    # "Enterprise over personal, and personal over project": a call of that name did not run the project's
    folder, config, work = session
    _skill(work, "review")
    _commit(work)
    mine = config / "skills" / "review"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text(SKILL.format(name="review"))
    _run(_called("review"), folder, config)
    (line,) = _lines(folder)
    assert (line["kind"], line["version"]) == ("other", None)


def test_the_account_s_skill_by_a_frontmatter_name_shadows_it_as_well(session):
    folder, config, work = session
    _skill(work, "review")
    _commit(work)
    mine = config / "skills" / "my-review-folder"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text(SKILL.format(name="review"))
    _run(_called("review"), folder, config)
    assert [l["kind"] for l in _lines(folder)] == ["other"]


def test_the_account_s_skill_of_another_name_shadows_nothing(session):
    folder, config, work = session
    _skill(work, "review")
    commit = _commit(work)
    mine = config / "skills" / "other"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text(SKILL.format(name="other"))
    _run(_called("review"), folder, config)
    assert [(l["kind"], l["version"]) for l in _lines(folder)] == [("business", commit)]


def test_an_organisation_s_managed_skill_is_run_first_too(session, tmp_path):
    folder, config, work = session
    _skill(work, "review")
    _commit(work)
    managed = tmp_path / "managed" / ".claude" / "skills" / "review"
    managed.mkdir(parents=True)
    (managed / "SKILL.md").write_text(SKILL.format(name="review"))
    assert activity.skills_record(_called("review"), str(folder), shadows=[str(tmp_path / "managed" / ".claude" / "skills")])
    assert [l["kind"] for l in _lines(folder)] == ["other"]
    assert activity.skills_record(_called("review"), str(folder), shadows=[str(tmp_path / "nothing" / "skills")])
    assert [l["kind"] for l in _lines(folder)] == ["other", "business"]


def test_the_places_of_a_managed_skill_and_of_the_account_s_own(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "acct"))
    managed, own = activity._shadows()
    assert own == str(tmp_path / "acct" / "skills")
    assert managed.endswith(os.path.join(".claude", "skills"))
    assert managed.startswith({"darwin": "/Library/Application Support/ClaudeCode"}.get(sys.platform, "/etc/claude-code"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert activity._shadows()[1] == str(tmp_path / "home" / ".claude" / "skills")


def test_a_session_with_no_worktree_has_no_business_skill(session):
    folder, config, work = session
    import shutil
    shutil.rmtree(work)
    _run(_called("review"), folder, config)
    _run(_called(CORE), folder, config)
    assert [(l["kind"], l["version"] is None) for l in _lines(folder)] == [("other", True), ("core", False)]


def test_a_nested_skill_of_a_subdirectory_is_not_a_business_skill(session):
    folder, config, work = session
    nested = os.path.join(work, "apps", "web", ".claude", "skills", "deploy")
    os.makedirs(nested)
    with open(os.path.join(nested, "SKILL.md"), "w") as fh:
        fh.write(SKILL.format(name="deploy"))
    _commit(work)
    _run(_called("deploy"), folder, config)
    assert [l["kind"] for l in _lines(folder)] == ["other"]


# the typed path


def test_a_typed_business_skill_is_counted_as_the_person_s(session):
    folder, config, work = session
    _skill(work, "review")
    commit = _commit(work)
    done = _run(_typed("review"), folder, config)
    assert done.returncode == 0 and done.stdout == ""
    (line,) = _lines(folder)
    assert set(line) == FIELDS
    assert (line["skill"], line["kind"], line["path"], line["failed"], line["version"]) == (
        "review", "business", "typed", None, commit)
    assert SECRET not in (folder / "skills.jsonl").read_text()          # not its arguments


def test_a_typed_core_skill_is_counted(session):
    folder, config, work = session
    _run(_typed(CORE), folder, config)
    (line,) = _lines(folder)
    assert (line["skill"], line["kind"], line["path"], line["failed"]) == (CORE, "core", "typed", None)


@pytest.mark.parametrize("name", ["init", "code-review", "my-command", "other-plugin:review", "proofpane-core:nope"])
def test_a_typed_command_that_is_no_skill_of_the_session_s_is_not_counted(session, name):
    # a custom command in .claude/commands/, a bundled skill, a plugin's: the expansion does not say which it was
    folder, config, work = session
    os.makedirs(os.path.join(work, ".claude", "commands"))
    with open(os.path.join(work, ".claude", "commands", "my-command.md"), "w") as fh:
        fh.write("do the thing\n")
    _skill(work, "review")
    _commit(work)
    _run(_typed(name), folder, config)
    assert _lines(folder) == []


def test_a_typed_skill_of_the_project_that_the_account_s_own_shadows_is_not_counted(session):
    folder, config, work = session
    _skill(work, "review")
    _commit(work)
    mine = config / "skills" / "review"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text(SKILL.format(name="review"))
    _run(_typed("review"), folder, config)
    assert _lines(folder) == []


def test_a_typed_command_and_a_call_claude_chose_are_not_one_line_twice(session):
    folder, config, work = session
    _skill(work, "review")
    _commit(work)
    _run(_typed("review"), folder, config)
    _run(_called("review"), folder, config)
    assert [(l["path"], l["failed"]) for l in _lines(folder)] == [("typed", None), ("claude", False)]


# the version, read as the hook runs


def test_the_version_is_the_commit_that_last_changed_the_skill_s_folder_not_the_head(session):
    folder, config, work = session
    _skill(work, "review")
    changed = _commit(work, "the skill")
    with open(os.path.join(work, "README.md"), "a") as fh:
        fh.write("more\n")
    head = _commit(work, "something else")
    assert head != changed
    _run(_called("review"), folder, config)
    assert [l["version"] for l in _lines(folder)] == [changed]


def test_a_call_keeps_the_commit_it_used_when_the_folder_changes_after_it(session):
    folder, config, work = session
    _skill(work, "review")
    first = _commit(work, "v1")
    _run(_called("review"), folder, config)
    _skill(work, "review", body=SKILL.format(name="review") + "\nMore.\n")
    second = _commit(work, "v2")
    _run(_called("review"), folder, config)
    assert first != second
    assert [l["version"] for l in _lines(folder)] == [first, second]        # the first line is as it was written


def test_a_folder_with_changes_not_committed_is_recorded_as_such_and_never_given_a_commit(session):
    folder, config, work = session
    _skill(work, "review")
    commit = _commit(work)
    _skill(work, "review", body=SKILL.format(name="review") + "\nAn edit.\n")          # modified
    _run(_called("review"), folder, config)
    with open(os.path.join(work, ".claude", "skills", "review", "new.md"), "w") as fh:  # and then also new files
        fh.write("x")
    _run(_called("review"), folder, config)
    git("checkout", "--", ".claude/skills/review/SKILL.md", cwd=work)
    _run(_called("review"), folder, config)                                             # only the new file now
    os.unlink(os.path.join(work, ".claude", "skills", "review", "new.md"))
    _run(_called("review"), folder, config)                                             # clean again
    assert [l["version"] for l in _lines(folder)] == ["uncommitted", "uncommitted", "uncommitted", commit]


def test_a_staged_change_is_not_committed_either(session):
    folder, config, work = session
    _skill(work, "review")
    _commit(work)
    _skill(work, "review", body=SKILL.format(name="review") + "\nStaged.\n")
    git("add", "-A", cwd=work)
    _run(_called("review"), folder, config)
    assert [l["version"] for l in _lines(folder)] == ["uncommitted"]


def test_a_skill_not_committed_at_all_is_uncommitted(session):
    folder, config, work = session
    _skill(work, "review")
    _run(_called("review"), folder, config)
    assert [(l["kind"], l["version"]) for l in _lines(folder)] == [("business", "uncommitted")]


def test_a_change_beside_the_skill_s_folder_is_not_a_change_of_it(session):
    folder, config, work = session
    _skill(work, "review")
    commit = _commit(work)
    with open(os.path.join(work, "README.md"), "a") as fh:
        fh.write("an edit outside\n")
    _skill(work, "other")
    _run(_called("review"), folder, config)
    assert [l["version"] for l in _lines(folder)] == [commit]


def test_a_folder_whose_name_holds_glob_characters_is_read_literally(session):
    folder, config, work = session
    _skill(work, "a-skill", folder="my[skill]*")
    commit = _commit(work)
    _skill(work, "other", folder="my-other")                # a folder a glob `my*` would also reach, not committed
    _run(_called("a-skill"), folder, config)
    assert [(l["kind"], l["version"]) for l in _lines(folder)] == [("business", commit)]


def test_git_that_fails_gives_the_version_unknown_and_the_hook_ends_0(session):
    folder, config, work = session
    _skill(work, "review")
    _commit(work)
    git("config", "extensions.objectformat", "nonsense", cwd=work)       # a repository git refuses to read
    done = _run(_called("review"), folder, config)
    assert done.returncode == 0
    assert [(l["kind"], l["version"]) for l in _lines(folder)] == [("business", "unknown")]


def test_a_worktree_that_is_no_repository_is_not_taken_for_the_one_around_it(session):
    # git looks no higher than the worktree's own folder (GIT_CEILING_DIRECTORIES, as workspace._git_env sets it)
    folder, config, work = session
    outer = folder.parent / "outer"
    outer.mkdir()
    git("init", "-q", "-b", "main", cwd=str(outer))
    inner = outer / "session"
    inner.mkdir()
    (inner / "work" / ".claude" / "skills" / "review").mkdir(parents=True)
    (inner / "work" / ".claude" / "skills" / "review" / "SKILL.md").write_text(SKILL.format(name="review"))
    (outer / "a.txt").write_text("x")
    git("add", "-A", cwd=str(outer))
    git("commit", "-q", "-m", "around", cwd=str(outer))
    _run(_called("review"), inner, config)
    assert [(l["kind"], l["version"]) for l in _lines(inner)] == [("business", "unknown")]


def test_git_past_its_limit_gives_the_version_unknown_and_the_hook_still_ends_0(session, tmp_path):
    folder, config, work = session
    _skill(work, "review")
    _commit(work)
    slow = tmp_path / "slow"
    slow.mkdir()
    (slow / "git").write_text("#!/bin/sh\nsleep 60\n")
    (slow / "git").chmod(0o755)
    begun = time.time()
    done = _run(_called("review"), folder, config, PATH=f"{slow}:{os.environ['PATH']}")
    took = time.time() - begun
    assert done.returncode == 0 and took < 30
    assert took >= activity.GIT_TIMEOUT - 0.5                          # it waited for git the time it is given
    assert [(l["kind"], l["version"]) for l in _lines(folder)] == [("business", "unknown")]


def test_git_is_run_as_the_workspace_s_own_reads_are_no_fsmonitor_program_of_the_repository_s_own(session, tmp_path):
    folder, config, work = session
    _skill(work, "review")
    _commit(work)
    marker = tmp_path / "ran"
    hook = tmp_path / "fsmonitor"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\nprintf '\\0'\n")
    hook.chmod(0o755)
    git("config", "core.fsmonitor", str(hook), cwd=work)
    # the control: the repository's own setting does run its program for a plain `git status`
    subprocess.run(["git", "status", "--porcelain"], cwd=work, capture_output=True, env=_env(folder, config))
    assert marker.exists(), "the repository's fsmonitor did not run for a plain git status: this test shows nothing"
    marker.unlink()
    assert _run(_called("review"), folder, config).returncode == 0
    assert not marker.exists()
    assert [l["kind"] for l in _lines(folder)] == ["business"]


def test_the_pairs_git_is_given_are_the_workspace_s_own_and_the_one_that_refuses_every_transport():
    assert activity._GIT_PAIRS == (*workspace_module._RUN_PAIRS, ("protocol.allow", "never"))


def test_the_git_of_the_hook_is_given_every_guard_the_manual_and_the_plan_name(session, tmp_path):
    """A git first on the PATH writes down each call it gets (its arguments, four of its environment variables, and
    whether its standard input is /dev/null) and hands the call to the real one, so that the hook still has its answers.
    The session's own environment carries the opposite of each variable, so a guard left out, or only defaulted, shows.
    `--no-optional-locks` is the one with a reason that matters: without it the hook's `git status` may take the index
    lock, and a commit the session makes at that moment fails."""
    folder, config, work = session
    _skill(work, "review")
    commit = _commit(work)
    real = shutil.which("git")
    spy = tmp_path / "spy"
    spy.mkdir()
    log = tmp_path / "git-calls.jsonl"
    (spy / "git").write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "names = ('GIT_TERMINAL_PROMPT', 'LC_ALL', 'GIT_NO_LAZY_FETCH', 'GIT_CEILING_DIRECTORIES')\n"
        "rec = {'argv': sys.argv[1:], 'env': {k: os.environ.get(k) for k in names},\n"
        "       'stdin_is_null': os.path.samestat(os.fstat(0), os.stat(os.devnull))}\n"
        "with open(os.environ['SPY_LOG'], 'a') as fh:\n"
        "    fh.write(json.dumps(rec) + '\\n')\n"
        f"os.execv({real!r}, [{real!r}] + sys.argv[1:])\n")
    (spy / "git").chmod(0o755)
    done = _run(_called("review"), folder, config, PATH=f"{spy}:{os.environ['PATH']}", SPY_LOG=str(log),
                GIT_TERMINAL_PROMPT="1", LC_ALL="de_DE.UTF-8", GIT_NO_LAZY_FETCH="0", GIT_CEILING_DIRECTORIES="/")
    assert done.returncode == 0
    assert [(l["kind"], l["version"]) for l in _lines(folder)] == [("business", commit)]     # the real git answered
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert [c["argv"][c["argv"].index("--literal-pathspecs") + 1] for c in calls] == ["status", "log"]
    for call in calls:
        assert "--no-optional-locks" in call["argv"] and "--literal-pathspecs" in call["argv"]
        assert call["env"] == {"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", "GIT_NO_LAZY_FETCH": "1",
                               "GIT_CEILING_DIRECTORIES": os.path.dirname(os.path.realpath(work))}
        assert call["stdin_is_null"] is True


@pytest.mark.parametrize("over, counted", [(0, 1), (1, 0)],
                         ids=["an event of the most it reads", "one character past it"])
def test_an_event_past_the_most_the_hook_reads_is_cut_and_its_call_not_counted(session, over, counted):
    """The hook reads at most MAX_MAP_EVENT characters of its input (a tool's whole result is in it): an event of more
    is cut in the middle, is no longer JSON, and its call is not counted, with no line and the hook ending 0."""
    folder, config, work = session
    event = _called("review", response={"success": True, "commandName": "review", "result": "", "status": "inline"})
    room = activity.MAX_MAP_EVENT + over - len(json.dumps(event))
    event["tool_response"]["result"] = "x" * room
    text = json.dumps(event)
    assert len(text) == activity.MAX_MAP_EVENT + over
    assert _run(text, folder, config).returncode == 0
    assert [l["skill"] for l in _lines(folder)] == ["review"] * counted


# a core skill's version


def test_a_core_skill_s_version_is_its_release_and_a_digest_of_its_folder(session):
    folder, config, work = session
    with open(os.path.join(PLUGIN, ".claude-plugin", "plugin.json")) as fh:
        release = json.load(fh)["version"]
    _run(_called(CORE), folder, config)
    (line,) = _lines(folder)
    import hashlib
    skill = os.path.join(PLUGIN, "skills", "ai-risk-assessment")
    expected = hashlib.sha256()
    for name in sorted(os.listdir(skill)):
        with open(os.path.join(skill, name), "rb") as fh:
            expected.update(name.encode() + b"\0" + fh.read() + b"\0")
    assert line["version"] == f"{release}+{expected.hexdigest()}"


def _plugin(tmp_path):
    """A copy of the core plugin's manifest and one skill, for the hook to be pointed at."""
    root = tmp_path / "plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "proofpane-core", "version": "1.0.0"}))
    skill = root / "skills" / "ai-risk-assessment"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: ai-risk-assessment\n---\nOne.\n")
    return root, skill


def _version(folder, plugin):
    activity.skills_record(_called(CORE), str(folder), plugin=str(plugin), shadows=[])
    return _lines(folder)[-1]["version"]


def test_two_releases_of_a_skill_differ_by_its_digest_though_the_release_is_one(session, tmp_path):
    folder, config, work = session
    root, skill = _plugin(tmp_path)
    first = _version(folder, root)
    (skill / "SKILL.md").write_text("---\nname: ai-risk-assessment\n---\nTwo.\n")
    second = _version(folder, root)
    assert first.startswith("1.0.0+") and second.startswith("1.0.0+") and first != second
    assert _version(folder, root) == second                              # the same bytes, the same version


def test_a_file_added_to_the_skill_s_folder_changes_its_digest_and_so_does_a_new_name(session, tmp_path):
    folder, config, work = session
    root, skill = _plugin(tmp_path)
    base = _version(folder, root)
    (skill / "references").mkdir()
    (skill / "references" / "a.md").write_text("x")
    added = _version(folder, root)
    (skill / "references" / "a.md").rename(skill / "references" / "b.md")
    renamed = _version(folder, root)
    assert len({base, added, renamed}) == 3


def test_a_file_moved_to_another_folder_of_the_skill_changes_its_digest_though_its_name_is_the_same(session, tmp_path):
    """The digest is over each file's path below the skill's folder, not its name alone."""
    folder, config, work = session
    root, skill = _plugin(tmp_path)
    (skill / "references").mkdir()
    (skill / "references" / "a.md").write_text("x")
    before = _version(folder, root)
    (skill / "scripts").mkdir()
    (skill / "references" / "a.md").rename(skill / "scripts" / "a.md")
    assert _version(folder, root) != before


def test_a_name_that_only_begins_as_the_core_plugin_s_does_is_not_a_core_skill(session):
    folder, config, work = session
    _run(_called("proofpane-core_ai-risk-assessment"), folder, config)
    _run(_called("proofpane-coreai-risk-assessment"), folder, config)
    _run(_called(CORE), folder, config)
    assert [(l["skill"], l["kind"]) for l in _lines(folder)] == [
        ("proofpane-core_ai-risk-assessment", "other"), ("proofpane-coreai-risk-assessment", "other"), (CORE, "core")]


def test_the_release_changes_the_version_and_a_manifest_without_one_says_unknown(session, tmp_path):
    folder, config, work = session
    root, skill = _plugin(tmp_path)
    digest = _version(folder, root).split("+")[1]
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "proofpane-core", "version": "2.5.1"}))
    assert _version(folder, root) == f"2.5.1+{digest}"
    for manifest in ("{}", "[]", "not json", json.dumps({"version": 5}), json.dumps({"version": "a b"}),
                     json.dumps({"version": "x" * 40})):
        (root / ".claude-plugin" / "plugin.json").write_text(manifest)
        assert _version(folder, root) == f"unknown+{digest}"
    os.unlink(root / ".claude-plugin" / "plugin.json")
    assert _version(folder, root) == f"unknown+{digest}"


def test_a_skill_folder_past_the_bound_says_so_and_is_not_read_whole(session, tmp_path):
    folder, config, work = session
    root, skill = _plugin(tmp_path)
    (skill / "big.bin").write_bytes(b"x" * (activity.DIGEST_MOST + 1))
    assert _version(folder, root) == "1.0.0+cut"
    (skill / "big.bin").write_bytes(b"x" * 1000)
    assert re.fullmatch(r"1\.0\.0\+[0-9a-f]{64}", _version(folder, root))
    for n in range(activity.DIGEST_FILES):
        (skill / f"f{n}.md").write_text("x")
    assert _version(folder, root) == "1.0.0+cut"                         # past DIGEST_FILES files


def test_the_bounds_of_a_skill_folder_are_the_most_it_reads_not_one_less(session, tmp_path):
    """Exactly DIGEST_FILES files, or exactly DIGEST_MOST bytes, is read whole; one more of either is cut."""
    folder, config, work = session
    root, skill = _plugin(tmp_path)
    files = [f for f in skill.rglob("*") if f.is_file()]
    for n in range(activity.DIGEST_FILES - len(files)):
        (skill / f"f{n}.md").write_text("x")
    assert len([f for f in skill.rglob("*") if f.is_file()]) == activity.DIGEST_FILES
    assert re.fullmatch(r"1\.0\.0\+[0-9a-f]{64}", _version(folder, root))     # the most files: read whole
    (skill / "one-more.md").write_text("x")
    assert _version(folder, root) == "1.0.0+cut"
    (skill / "one-more.md").unlink()
    held = sum(f.stat().st_size for f in skill.rglob("*") if f.is_file())
    (skill / "f0.md").write_bytes(b"x" * (activity.DIGEST_MOST - held + 1))        # f0.md held 1 byte: now the most bytes
    assert sum(f.stat().st_size for f in skill.rglob("*") if f.is_file()) == activity.DIGEST_MOST
    assert re.fullmatch(r"1\.0\.0\+[0-9a-f]{64}", _version(folder, root))
    (skill / "f0.md").write_bytes(b"x" * (activity.DIGEST_MOST - held + 2))
    assert _version(folder, root) == "1.0.0+cut"


def test_a_link_or_a_fifo_in_a_skill_s_folder_is_not_followed_or_waited_on(session, tmp_path):
    folder, config, work = session
    root, skill = _plugin(tmp_path)
    plain = _version(folder, root)
    os.symlink("/etc/hostname", skill / "link.md")
    os.mkfifo(skill / "pipe")
    begun = time.time()
    assert _version(folder, root) == plain                               # neither is part of the digest
    assert time.time() - begun < 20


def test_the_digest_of_a_folder_that_is_gone_is_unknown(tmp_path):
    assert activity._digest(str(tmp_path / "gone")) == "unknown"


# the file


def test_the_file_is_cut_to_its_newest_half_past_its_bound(session, monkeypatch):
    folder, config, work = session
    monkeypatch.setattr(activity, "MAX_SKILLS", 4000)
    for _ in range(60):
        assert activity.skills_record(_called("review"), str(folder), shadows=[])
    size = os.path.getsize(folder / "skills.jsonl")
    assert size <= 4000
    kept = _lines(folder)
    assert 0 < len(kept) < 60 and len({line["id"] for line in kept}) == len(kept)


def test_a_link_where_the_file_goes_is_not_followed_and_a_fifo_is_not_waited_on(session, tmp_path):
    folder, config, work = session
    target = tmp_path / "elsewhere.txt"
    target.write_text("keep\n")
    os.symlink(target, folder / "skills.jsonl")
    assert _run(_called("review"), folder, config).returncode == 0
    assert target.read_text() == "keep\n"
    os.unlink(folder / "skills.jsonl")
    os.mkfifo(folder / "skills.jsonl")
    begun = time.time()
    assert _run(_called("review"), folder, config).returncode == 0
    assert time.time() - begun < 20


def test_the_file_is_the_session_s_and_private(session):
    folder, config, work = session
    _run(_called("review"), folder, config)
    assert stat.S_IMODE(os.stat(folder / "skills.jsonl").st_mode) == 0o600


def test_the_map_s_own_files_are_left_as_they_were(session):
    folder, config, work = session
    _run(_called("review"), folder, config)
    assert sorted(p.name for p in folder.iterdir() if p.is_file()) == ["skills.jsonl"]


# the numbers the manual states


def _readme_section():
    manual_or_skip()
    with open(os.path.join(ROOT, "MANUAL.md"), encoding="utf-8") as fh:
        text = fh.read()
    start = text.index("## The skills the sessions called")
    return re.sub(r"\s+", " ", text[start:text.index("\n## ", start + 10)])


def test_the_numbers_the_manual_states_of_the_hook_are_the_code_s():
    """The manual's section says how much the hook reads and keeps; each number is read back from the code, so that a
    bound the manual names and no test holds cannot move."""
    readme = _readme_section()

    def stated(pattern):
        found = re.findall(pattern, readme)
        assert len(found) == 1, (pattern, found)
        return int(found[0].replace(",", ""))
    assert stated(r"bounded at ([0-9,]+) KB") * 1024 == activity.MAX_SKILLS
    assert stated(r"read whole, up to ([0-9,]+) characters") == activity.MAX_MAP_EVENT
    assert stated(r"in the first ([0-9,]+) bytes of a `SKILL.md`") == activity.FRONT
    assert stated(r"among the first ([0-9,]+) folders by name") == activity.FOLDERS
    assert stated(r"of the first ([0-9,]+) entries the directory lists") == activity.ENTRIES
    assert stated(r"holds more than ([0-9,]+) files or [0-9,]+ KB") == activity.DIGEST_FILES
    assert stated(r"holds more than [0-9,]+ files or ([0-9,]+) KB") * 1024 == activity.DIGEST_MOST
    assert stated(r"does not answer within ([0-9,]+) seconds") == activity.GIT_TIMEOUT
    longest = stated(r"at most ([0-9,]+) characters, no more than letters")
    assert activity.NAME.fullmatch("a" * longest) and not activity.NAME.fullmatch("a" * (longest + 1))


def test_the_numbers_the_page_states_of_what_the_hook_cannot_read_are_the_code_s():
    """The page's warning names three bounds of what the hook counts: the largest result it reads, the largest settings
    of a chat it is added to, and the first Claude Code with a background hook; each is read back from the code."""
    page = file_text("ui", "src", "components", "Skills.tsx")
    assert stated_in(r"a call whose result is past ([0-9]+) MB", page) * 1024 * 1024 == activity.MAX_MAP_EVENT
    assert stated_in(r"a chat whose own settings are past ([0-9]+) KB", page) * 1024 == mcp.MERGED_MOST
    found = re.findall(r"older than ([0-9]+)\.([0-9]+)\.([0-9]+) or began before", page)
    assert [tuple(int(n) for n in one) for one in found] == [settings.ASYNC_HOOKS_FROM]


# what the script loads

PROBE = (
    "import runpy, sys\n"
    "sys.argv = ['activity.py'] + sys.argv[1:]\n"
    "try:\n"
    f"    runpy.run_path({SCRIPT!r}, run_name='__main__')\n"
    "except SystemExit:\n"
    "    pass\n"
    "print(','.join(name for name in ('hashlib', 'secrets', 'subprocess') if name in sys.modules))\n")


def _loaded(args, event, folder, config):
    """Which of hashlib, secrets and subprocess the interpreter holds once the script has run with `args` on `event`
    (isolated, so nothing of the machine's own start-up counts)."""
    done = subprocess.run([sys.executable, "-I", "-c", PROBE, *args], input=json.dumps(event), capture_output=True,
                          text=True, env=_env(folder, config), timeout=60)
    assert done.returncode == 0, done.stderr
    return set(filter(None, done.stdout.splitlines()[-1].split(",")))


@pytest.mark.parametrize("args, event, written", [
    (["working"], {"hook_event_name": "UserPromptSubmit", "session_id": "s1"}, "activity.json"),
    (["idle"], {"hook_event_name": "Stop", "session_id": "s1"}, "activity.json"),
    (["--map"], {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"},
                 "session_id": "s1"}, "calls.jsonl"),
    (["--limit"], {"hook_event_name": "StopFailure", "error": "rate_limit", "session_id": "s1"}, "limits.jsonl")])
def test_the_hooks_that_read_no_version_load_none_of_the_modules_a_version_needs(session, args, event, written):
    """Every hook of the workspace runs this script on a tool call's path: importing the three at the top of it cost
    about 13 ms of each run (measured, 40 runs), which the modes that never read a version or write a skills line
    should not pay."""
    folder, config, work = session
    assert _loaded(args, event, folder, config) == set()
    assert (folder / written).exists()                                    # the mode ran: it is not a hook that did nothing


@pytest.mark.parametrize("make, event_of, held, not_held, counted", [
    pytest.param(lambda work: (_skill(work, "review"), _commit(work)), lambda: _called("review"),
                 {"subprocess", "secrets"}, set(), 1, id="a skill of the project: git for its version, a token for the line"),
    pytest.param(lambda work: None, lambda: _called(CORE), {"secrets"}, {"subprocess"}, 1,
                 id="a core skill: a digest of its folder and a token for the line, no git"),
    pytest.param(lambda work: None, lambda: _called("review"), {"secrets"}, {"subprocess"}, 1,
                 id="any other skill: a version of none, only the line's token"),
    pytest.param(lambda work: None, lambda: {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "s1",
                                              "tool_input": {"command": "ls"}},
                 set(), {"hashlib", "secrets", "subprocess"}, 0, id="a call of another tool: no line, nothing loaded"),
    pytest.param(lambda work: None, lambda: _typed("init"), set(), {"hashlib", "secrets", "subprocess"}, 0,
                 id="a typed command that is no skill: no line, nothing loaded")])
def test_the_skills_hook_loads_a_module_only_where_it_needs_it(session, make, event_of, held, not_held, counted):
    """git is run only for a project's skill, a token made only for a line that is written. (hashlib is not named
    here: `secrets` loads `random`, which loads it, for every line; the digest of a core skill's folder is pinned by
    the version tests.)"""
    folder, config, work = session
    make(work)
    loaded = _loaded(["--skills"], event_of(), folder, config)
    assert held <= loaded and not (not_held & loaded)
    assert len(_lines(folder)) == counted
