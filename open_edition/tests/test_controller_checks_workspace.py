"""A task's acceptance as checks, the workspace's side: the owner's side's checks held to their form, a plan's choice
among them and its copy in the controller's own record, the new request's message, the first prompt's words on them, and
the rounds. The app's side is test_controller_checks.py."""

import asyncio
import json

import pytest

from proofpane_workspace import controller as ctl
from test_controller_workspace import NOW, mirror, on, request, start, take, uid, ws  # noqa: F401  fixtures

REQUEST = "Make the README say hello.\n\nIts first line is hello.\n\nKeep the rest as it is."
CHECKS = {"commands": [{"id": 1, "repo": "demo", "name": "unit tests", "command": "make test"},
                       {"id": 2, "repo": "demo", "name": "lint", "command": "ruff check ."},
                       {"id": 3, "repo": "other", "name": "docs", "command": "make docs"}],
          "protected": [{"repo": "demo", "paths": ["tests/**"]}, {"repo": "other", "paths": ["docs/conf.py"]}]}


def checked_on(w, checks=CHECKS):
    return on(w, requests=[request(checks=checks)])


def plan_checked(w, m, **over):
    args = dict({"request": m["requests"][0]["id"], "title": "Hello README", "words": "Make the README say hello.",
                 "acceptance": "Its first line is hello.", "repos": ["demo"], "checks": [1, 2]}, **over)
    return w.controller.plan_requirement(args)["requirement"]


# ---- the plan's choice, copied from the owner's side -----------------------------------------------------------------

def test_a_plan_copies_its_chosen_commands_from_the_owner_s_side_and_its_repositories_protected_paths(ws):
    m = checked_on(ws)
    rid = plan_checked(ws, m)
    plan = ws.controller.record["plans"][rid]
    assert plan["checks"] == CHECKS["commands"][:2]
    assert plan["protected"] == [CHECKS["protected"][0]]
    action = [a for a in ws.controller.record["actions"] if a["kind"] == "plan"][-1]
    assert action["checks"] == [1, 2]
    # the commands run are the owner's as the app sent them, never the controller's words: a plan action carries the
    # numbers alone
    assert "make test" not in json.dumps(action)


@pytest.mark.parametrize("over,words", [
    ({"checks": [4]}, "Check 4 is not one of this request's."),
    ({"checks": [3]}, "Check 3 runs in a repository this requirement may not touch."),
    ({"checks": []}, "The requirement chooses none of its request's checks in a repository of its that has some: "
                     "choose at least one there."),
    ({"checks": None}, "The requirement chooses none of its request's checks in a repository of its that has some: "
                       "choose at least one there."),
    ({"checks": [1, 1]}, "checks is a list of different numbers of the request's checks."),
    ({"checks": [True]}, "checks is a list of different numbers of the request's checks."),
    ({"checks": "1"}, "checks is a list of different numbers of the request's checks."),
])
def test_a_plan_whose_checks_are_not_its_request_s_or_leave_a_repository_out_is_refused(ws, over, words):
    m = checked_on(ws)
    with pytest.raises(ctl.ControllerError) as e:
        plan_checked(ws, m, **over)
    assert str(e.value) == words
    assert ws.controller.record["plans"] == {}


def test_a_plan_of_a_request_with_no_checks_chooses_none(ws):
    m = on(ws)
    rid = plan_checked(ws, m, checks=None)
    assert ws.controller.record["plans"][rid]["checks"] == [] and ws.controller.record["plans"][rid]["protected"] == []


# ---- the new request's message carries its checks, escaped -----------------------------------------------------------

def test_a_new_request_s_checks_reach_the_chat_escaped_as_the_packet_escapes_them(ws):
    checks = {"commands": [{"id": 1, "repo": "demo", "name": "zero\u200bwidth", "command": "make test"}],
              "protected": []}
    checked_on(ws, checks)
    item = [p for p in ws.controller.pending if p["kind"] == "request"][0]
    assert item["checks"]["commands"][0]["name"] == "zero\\u200bwidth"
    assert item["checks"]["commands"][0]["command"] == "make test"


# ---- a record made before plans had checks ---------------------------------------------------------------------------

def test_a_record_whose_plans_have_no_checks_is_still_sound_and_one_with_a_malformed_one_is_not(ws):
    m = on(ws)
    rid = plan_checked(ws, m, checks=None)
    record = json.loads(json.dumps(ws.controller.record))
    del record["plans"][rid]["checks"], record["plans"][rid]["protected"]
    assert ctl._sound(record)
    record["plans"][rid]["checks"] = [{"id": "1", "repo": "demo", "name": "n", "command": "c"}]
    assert not ctl._sound(record)


# ---- the first prompt's words on the checks; the base the workspace resolved -----------------------------------------

def _prompt():
    from test_controller_workspace import _Term
    return _Term.seen[-1].argv[-1]


def test_a_task_s_first_prompt_gives_its_repository_s_chosen_checks_each_one_line_and_its_protected_paths(ws):
    checks = {"commands": [{"id": 1, "repo": "demo", "name": "unit\ttests", "command": "make test\nmake lint"},
                           {"id": 2, "repo": "other", "name": "docs", "command": "make docs"}],
              "protected": [{"repo": "demo", "paths": ["tests/**", "Makefile"]}]}
    m = checked_on(ws, checks)
    rid = plan_checked(ws, m, repos=["demo", "other"], checks=[1, 2])
    start(ws, rid)
    prompt = _prompt()
    words = prompt.rsplit("Its first line is hello.\n\n", 1)[1]          # after Part 3's acceptance
    assert words.startswith("The owner's checks of this repository, which the workspace runs on this session's branch")
    assert "each for at most 600 seconds, in at most 10 rounds." in words
    assert words.endswith("so that each is one line:\nCheck 1, named: unit\\x09tests\nIts command: make test\\x0amake "
                          "lint\nProtected paths: tests/**  Makefile")
    # the other repository's check is not this task's
    assert "make docs" not in prompt


def test_a_task_in_a_repository_its_plan_chose_no_check_of_is_told_none_judges_it(ws):
    m = checked_on(ws, {"commands": [{"id": 1, "repo": "other", "name": "docs", "command": "make docs"}],
                        "protected": []})
    rid = plan_checked(ws, m, checks=[])
    start(ws, rid)
    assert _prompt().endswith("The owner gave no check of this repository: nothing the workspace runs judges this "
                              "task's commits.")


def test_a_task_keeps_the_base_the_workspace_resolved_not_the_ref_the_tool_named(ws):
    from proofpane_workspace.workspace import git
    m = on(ws)
    rid = plan_checked(ws, m, checks=None)
    got = start(ws, rid, base="main")
    path = ws.repos_dir + "/demo"
    sha = git("rev-parse", "main^{commit}", cwd=path)[1].strip()
    task = ws.controller.record["tasks"][got["task"]]
    assert task["base"] == "main" and task["base_commit"] == sha


# ---- the gate refuses a session's call that names the checks' folder -------------------------------------------------

@pytest.mark.parametrize("name,given", [
    ("Read", {"file_path": "/home/ws/workspace/controller-checks/round-x/clone/README.md"}),
    ("Bash", {"command": "ls controller-checks"}),
    ("Bash", {"command": "ls Controller-Checks/"}),           # another case: the same folder on a Mac's volume
    ("Bash", {"command": "ls controller-''checks"}),           # as the shell reads its words
    ("Write", {"file_path": "/home/ws/workspace/controller-checks/x", "content": "x"}),
])
def test_the_gate_refuses_a_session_s_call_that_names_the_checks_folder(tmp_path, name, given):
    from proofpane_workspace import gate
    assert gate.refusal({"tool_name": name, "tool_input": given}, str(tmp_path), {}) == gate.CHECKS_FOLDER


@pytest.mark.parametrize("command", ["cat controller-checksum.txt", "ls my-controller-checks", "ls controller-checks.d",
                                     "ls x.controller-checks"])
def test_a_name_the_folder_s_name_is_only_a_part_of_is_not_refused(tmp_path, command):
    from proofpane_workspace import gate
    assert gate.refusal({"tool_name": "Bash", "tool_input": {"command": command}}, str(tmp_path), {}) is None


def test_the_gate_s_rule_names_the_folder_the_controller_makes():
    from proofpane_workspace import gate
    assert gate._CHECKS_NAMED.fullmatch(ctl.CHECKS_FOLDER)


def test_a_mirror_whose_protected_group_names_no_repository_by_a_string_is_refused_naming_the_field(ws):
    checks = {"commands": [{"id": 1, "repo": "demo", "name": "n", "command": "c"}],
              "protected": [{"repo": ["demo"], "paths": ["tests/**"]}]}
    with pytest.raises(ctl.ControllerError) as e:
        ctl.mirror_of(mirror(requests=[request(checks=checks)]))
    assert "requests[0].checks" in str(e.value)
