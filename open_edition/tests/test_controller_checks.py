"""A task's acceptance as checks, the app's side: a request's checks and protected paths as the owner writes them, kept
with the request; the packet and the mirror that carry them; a plan's choice among them; a task's rounds as the app
takes them, their journal lines and the board's words. The workspace's side is test_controller_checks_workspace.py."""

import base64
import json

import pytest

from proofpane_open import store
from proofpane_open.ade import agents, controller
from proofpane_open.ade import task_shapes as shapes
from test_controller import C, SESSION, T0, events, owner, plan, record, setup, start, take, uid, ws  # noqa: F401

CHECK = {"repo": "demo", "name": "unit tests", "command": "make test"}
REQUEST = "Make the README say hello.\n\nits first line is hello."


def post(client, h, **body):
    return client.post(C + "/requests", headers=h, json=dict({"text": REQUEST}, **body))


def kept(request_id):
    with store.connect() as db:
        row = db.execute("SELECT checks FROM ade_controller_requests WHERE id = ?", (request_id,)).fetchone()
    return None if row["checks"] is None else json.loads(row["checks"])


# ---- what the owner writes, refused and kept -------------------------------------------------------------------------

def test_a_request_s_checks_and_protected_paths_are_kept_numbered_word_for_word(client, owner):
    h, user = owner
    checks = [CHECK, {"repo": "other", "name": "lint", "command": "ruff check .\nmypy ."}]
    protected = [{"repo": "demo", "paths": ["tests/**", "Makefile", "tests/"]}]
    r = post(client, h, checks=checks, protected=protected)
    assert r.status_code == 200, r.text
    want = {"commands": [dict(CHECK, id=1), dict(checks[1], id=2)], "protected": protected}
    assert r.json()["checks"] == want
    assert kept(r.json()["id"]) == want
    # the request's digest stays its text's alone, and the journal counts the commands, never their words
    line = events(user["id"], "request")[0]
    assert line["payload"]["checks"] == 2
    assert "make test" not in json.dumps(events(user["id"])) and "unit tests" not in json.dumps(events(user["id"]))


def test_a_request_with_no_checks_keeps_none_and_journals_as_before(client, owner):
    h, user = owner
    r = post(client, h)
    assert r.status_code == 200, r.text
    assert r.json()["checks"] is None and kept(r.json()["id"]) is None
    assert events(user["id"], "request")[0]["payload"] == {"request": r.json()["id"], "n": 1, "chars": len(REQUEST)}


def test_a_key_the_request_s_body_does_not_name_is_refused_not_dropped(client, owner):
    h, _ = owner
    r = post(client, h, check=[CHECK])
    assert r.status_code == 422
    # the answer and the decision keep their own body, which still takes text alone
    assert client.post(C + "/decisions", headers=h, json={"text": "Use main.", "x": 1}).status_code == 200


LONG = "x" * (shapes.TEXT_MAX + 1)
WIDE = "\u00e9" * (shapes.TEXT_MAX // 2 + 1)           # within TEXT_MAX characters, past its bytes


@pytest.mark.parametrize("checks,protected,words", [
    ("make test", None, "The checks are a list of at most 10, each of a repository, a name and a command."),
    ([CHECK] * 11, None, "The checks are a list of at most 10, each of a repository, a name and a command."),
    (["make test"], None, "Check 1 is an object of a repository, a name and a command, and nothing else."),
    ([dict(CHECK, extra=1)], None, "Check 1 is an object of a repository, a name and a command, and nothing else."),
    ([{"repo": "demo", "name": "x"}], None,
     "Check 1 is an object of a repository, a name and a command, and nothing else."),
    ([dict(CHECK, repo="../up")], None,
     "Check 1's repository is named as the workspace names it: letters, digits, dots, dashes and underscores."),
    ([dict(CHECK, repo="")], None,
     "Check 1's repository is named as the workspace names it: letters, digits, dots, dashes and underscores."),
    ([dict(CHECK, name="")], None, "Check 1's name is text of at most 100 characters."),
    ([dict(CHECK, name="n" * 101)], None, "Check 1's name is text of at most 100 characters."),
    ([dict(CHECK, name="unit\ntests")], None, "Check 1's name is one line, with no tab."),
    ([dict(CHECK, name="unit\ttests")], None, "Check 1's name is one line, with no tab."),
    ([dict(CHECK, command="  ")], None, "Check 1's command is text of at most 2000 characters."),
    ([dict(CHECK, command=LONG)], None, "Check 1's command is text of at most 2000 characters."),
    ([dict(CHECK, command="make\x00test")], None, "Check 1's command is text of at most 2000 characters."),
    ([dict(CHECK, command=WIDE)], None, "Check 1's command is at most 2000 bytes."),
    ([CHECK], "tests/**", "The protected paths are a list of at most 10, each of a repository and its paths."),
    ([CHECK], [{"repo": "demo", "paths": ["a"]}] * 11,
     "The protected paths are a list of at most 10, each of a repository and its paths."),
    ([CHECK], [{"repo": "demo"}], "Protected group 1 is an object of a repository and its paths, and nothing else."),
    ([CHECK], [{"repo": "demo", "paths": ["a"], "x": 1}],
     "Protected group 1 is an object of a repository and its paths, and nothing else."),
    ([CHECK], [{"repo": "other", "paths": ["a"]}],
     "A protected path's repository has none of this request's checks: protected paths are judged with them."),
    ([CHECK], [{"repo": "demo", "paths": ["a"]}, {"repo": "demo", "paths": ["b"]}],
     "Protected group 2 names a repository another names already."),
    ([CHECK], [{"repo": "demo", "paths": []}], "Protected group 1's paths are a list of one or more."),
    ([CHECK], [{"repo": "demo", "paths": "a"}], "Protected group 1's paths are a list of one or more."),
    ([CHECK], [{"repo": "demo", "paths": ["p%d" % i for i in range(21)]}],
     "A request has at most 20 protected paths."),
    ([CHECK], [{"repo": "demo", "paths": ["p" * 201]}],
     "Protected group 1's path 1 is text of at most 200 characters."),
    ([CHECK], [{"repo": "demo", "paths": [""]}], "Protected group 1's path 1 is text of at most 200 characters."),
    ([CHECK], [{"repo": "demo", "paths": ["a\x01b"]}], "Protected group 1's path 1 holds a control character."),
    ([CHECK], [{"repo": "demo", "paths": ["a\nb"]}], "Protected group 1's path 1 holds a control character."),
])
def test_checks_and_protected_paths_not_of_their_form_are_refused_in_the_app_s_words(client, owner, checks, protected,
                                                                                    words):
    h, _ = owner
    body = {"checks": checks}
    if protected is not None:
        body["protected"] = protected
    r = post(client, h, **body)
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == words
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_controller_requests").fetchone()[0] == 0


@pytest.mark.parametrize("path", ["/tests", "./tests", "a//b", "a/./b", "a/../b", "..", ".", "/", "//", "tests//"])
def test_a_protected_path_that_could_protect_nothing_is_refused(client, owner, path):
    h, _ = owner
    r = post(client, h, checks=[CHECK], protected=[{"repo": "demo", "paths": [path]}])
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == ("Protected group 1's path 1 is a path inside the repository as git names one: no "
                                  "leading slash, and no part that is empty, . or ..")


@pytest.mark.parametrize("path", ["tests/", "tests", "src/*.py", "**/conftest.py", "a/b/c"])
def test_a_protected_path_as_git_names_one_or_a_folder_with_one_slash_after_it_is_taken(client, owner, path):
    h, _ = owner
    r = post(client, h, checks=[CHECK], protected=[{"repo": "demo", "paths": [path]}])
    assert r.status_code == 200, r.text
    # taken, and kept as written: not merely answered, as a body that ignored the field would answer
    assert kept(r.json()["id"])["protected"] == [{"repo": "demo", "paths": [path]}]


SECRET = "ghp_" + "a" * 36           # a GitHub token's shape


@pytest.mark.parametrize("where", ["repo", "name", "command", "path"])
def test_a_check_or_a_protected_path_shaped_like_a_secret_is_refused_naming_its_shape_never_its_value(client, owner,
                                                                                                      where):
    h, _ = owner
    check = dict(CHECK)
    protected = [{"repo": "demo", "paths": ["tests/**"]}]
    if where == "path":
        protected[0]["paths"] = [SECRET]
    elif where == "repo":
        check["repo"] = SECRET
    else:
        check[where] = "run " + SECRET if where == "command" else SECRET
    r = post(client, h, checks=[check], protected=protected)
    assert r.status_code == 400, r.text
    assert "shaped like" in r.json()["detail"] and SECRET not in r.json()["detail"]


def test_a_request_whose_checks_take_it_past_its_packet_bytes_is_refused(env):
    many = [{"repo": "demo", "name": "n%d" % i, "command": "c" * 1000} for i in range(9)]
    with pytest.raises(agents.AgentError) as refused:
        controller.add_request({"id": "u1"}, REQUEST, checks=many, now=T0)
    assert refused.value.status == 400 and "past 8192" in str(refused.value)


def test_requests_not_yet_planned_count_their_checks_in_what_a_fresh_controller_is_given(env):
    user = {"id": "u1"}
    checks = [{"repo": "demo", "name": "n%d" % i, "command": "c" * 1400} for i in range(5)]
    made = 0
    with pytest.raises(agents.AgentError) as refused:
        for _ in range(10):
            controller.add_request(user, "x", checks=checks, now=T0)
            made += 1
    assert refused.value.status == 409
    # with no checks, as many requests of one character would have been taken
    assert made < 10
    with store.connect() as db:
        packet = json.loads(controller.build_packet(db, "u1", T0)[0])
    assert len(packet["instructions"]["requests"]) == made
    assert all(r["checks"]["commands"][0]["command"] == "c" * 1400 for r in packet["instructions"]["requests"])


# ---- a plan's choice among its request's checks ----------------------------------------------------------------------

CHECKS = [CHECK, {"repo": "demo", "name": "lint", "command": "ruff check ."},
          {"repo": "other", "name": "docs", "command": "make docs"}]
PROTECTED = [{"repo": "demo", "paths": ["tests/**"]}, {"repo": "other", "paths": ["docs/conf.py"]}]


def checked_request(user, now=T0 + 5):
    return controller.add_request(user, REQUEST, checks=CHECKS, protected=PROTECTED, now=now)


def plan_of(request, **kw):
    return plan(request["id"], "Make the README say hello.", **kw)


def test_a_plan_s_chosen_checks_are_copied_from_its_request_with_its_repositories_protected_paths(env):
    user = {"id": "u1"}
    setup(user)
    request = checked_request(user)
    rid = uid()
    action = dict(plan_of(request, rid=rid), checks=[2, 1])
    assert take(user, action)["taken"] == 1
    with store.connect() as db:
        row = db.execute("SELECT checks, protected FROM ade_controller_requirements WHERE id = ?", (rid,)).fetchone()
    assert json.loads(row["checks"]) == [dict(CHECK, id=1), dict(CHECKS[1], id=2)]
    assert json.loads(row["protected"]) == [PROTECTED[0]]
    view = controller.requirement_view(user, rid)
    assert [c["id"] for c in view["checks"]] == [1, 2] and view["protected"] == [PROTECTED[0]]
    assert view["left_out"] == []
    # the request's third check, in a repository no plan named, is chosen by no requirement
    listed = controller.requests_of(user)["requests"][0]
    assert listed["unchosen"] == [3]
    assert events(user["id"], "plan")[0]["payload"]["checks"] == 2
    # the packet's requirement names the numbers chosen
    with store.connect() as db:
        packet = json.loads(controller.build_packet(db, "u1", T0 + 200)[0])
    assert packet["information"]["requirements"][0]["checks"] == [1, 2]


def test_a_requirement_shows_the_checks_of_its_request_in_its_repositories_it_left_out(env):
    user = {"id": "u1"}
    setup(user)
    request = checked_request(user)
    rid = uid()
    assert take(user, dict(plan_of(request, rid=rid), checks=[1]))["taken"] == 1
    view = controller.requirement_view(user, rid)
    assert view["left_out"] == [{"id": 2, "repo": "demo", "name": "lint"}]


@pytest.mark.parametrize("checks,why", [
    ("1", "its checks are not a list of different numbers"),
    ([1, 1], "its checks are not a list of different numbers"),
    ([True], "its checks are not a list of different numbers"),
    ([1.0], "its checks are not a list of different numbers"),
    ([4], "it names a check that is not its request's"),
    ([0], "it names a check that is not its request's"),
    ([3], "it names a check in a repository it may not touch"),
    ([], "it chose none of its request's checks in one of its repositories that has some"),
    (None, "it chose none of its request's checks in one of its repositories that has some"),
])
def test_a_plan_whose_checks_are_not_its_request_s_or_leave_a_repository_out_is_refused(env, checks, why):
    user = {"id": "u1"}
    setup(user)
    request = checked_request(user)
    action = plan_of(request)
    if checks is not None:
        action["checks"] = checks
    assert take(user, action)["refused"] == 1
    assert events(user["id"], "plan.refused")[0]["payload"]["why"] == why


def test_a_plan_of_a_request_with_no_checks_chooses_none(env):
    user = {"id": "u1"}
    setup(user)
    request = controller.add_request(user, REQUEST, now=T0 + 5)
    rid = uid()
    assert take(user, plan_of(request, rid=rid))["taken"] == 1
    view = controller.requirement_view(user, rid)
    assert (view["checks"], view["protected"], view["left_out"]) == ([], [], [])
    assert "checks" not in events(user["id"], "plan")[0]["payload"]


def test_a_plan_in_a_repository_with_none_of_its_request_s_checks_chooses_none_there(env):
    user = {"id": "u1"}
    setup(user)
    request = controller.add_request(user, REQUEST, checks=[dict(CHECK, repo="other")], now=T0 + 5)
    assert take(user, plan_of(request))["taken"] == 1


# ---- the mirror carries each request's checks ------------------------------------------------------------------------

def test_the_mirror_carries_each_request_s_checks_raw_and_null_where_it_has_none(env):
    user = {"id": "u1"}
    setup(user)
    plain = controller.add_request(user, "Hello", now=T0 + 1)
    checked = controller.add_request(user, "Bye", checks=[dict(CHECK, name="tab\u200bbed")], now=T0 + 2)
    with store.connect() as db:
        body, _, _ = controller.mirror_body(db, "u1", T0 + 10)
    by_id = {r["id"]: r for r in body["requests"]}
    assert by_id[plain["id"]]["checks"] is None
    assert by_id[checked["id"]]["checks"] == {"commands": [dict(CHECK, name="tab\u200bbed", id=1)], "protected": []}
    # the packet's copy is escaped, as the request's words are
    with store.connect() as db:
        packet = json.loads(controller.build_packet(db, "u1", T0 + 10)[0])
    given = {r["id"]: r for r in packet["instructions"]["requests"]}
    assert "checks" not in given[plain["id"]]
    assert given[checked["id"]]["checks"]["commands"][0]["name"] == "tab\\u200bbed"


def test_the_mirror_holds_every_request_the_packet_gives_with_one_check_each_at_their_smallest(env):
    """A control of MIRROR_REQUESTS_BYTES's second case: requests of one character
    with one command each at its smallest, stored past what add_request lets wait, so that the packet's budget alone
    bounds them; every one the packet gives is in the mirror, first, in the packet's order."""
    user = {"id": "u1"}
    setup(user, cap=10)
    at = 1_800_000_000
    one = json.dumps({"commands": [{"id": 1, "repo": "x", "name": "x", "command": "x"}], "protected": []})
    with store.connect() as db:
        for n in range(1, 321):
            db.execute("INSERT INTO ade_controller_requests (id, user_id, n, text, digest, created_at, checks) VALUES "
                       "(?,?,?,?,?,?,?)", (uid(), "u1", n, "x", controller._digest("x"), at + n, one))
        p = json.loads(controller.build_packet(db, "u1", at + 1000)[0])
        body, _, _ = controller.mirror_body(db, "u1", at + 1000)
    given = [r["id"] for r in p["instructions"]["requests"]]
    assert 100 < len(given) < 319
    assert [r["id"] for r in body["requests"]][:len(given)] == given


# ---- a task's round as the app takes it ------------------------------------------------------------------------------

HEAD = "a" * 40


def started(user, checks=(1, 2)):
    """A requirement planned from a request with checks, choosing `checks`, and a task of it started: (rid, tid)."""
    setup(user)
    request = checked_request(user)
    rid, tid = uid(), uid()
    assert take(user, dict(plan_of(request, rid=rid), checks=list(checks)))["taken"] == 1
    assert take(user, start(rid, tid=tid))["taken"] == 1
    return rid, tid


def state(tid, rnd=None, state="idle", why=None, checking=False, **over):
    """A task's state as the workspace's page gives it."""
    return dict({"id": tid, "state": state, "waits_until": None, "ended_at": None, "checks": rnd, "why": why,
                 "checking": checking}, **over)


def round_of(n=1, results=None, passed=False, head=HEAD):
    return {"round": n, "head": head, "at": T0 + 50, "passed": passed,
            "results": results or [{"id": 1, "outcome": "passed"}, {"id": 2, "outcome": "failed", "exit": 3}]}


def task_row(tid):
    with store.connect() as db:
        return dict(db.execute("SELECT * FROM ade_controller_tasks WHERE id = ?", (tid,)).fetchone())


def words(line):
    return controller.words_of(dict(line, payload=json.dumps(line["payload"])))


def test_a_round_is_taken_with_its_task_s_state_unchanged_kept_and_journaled_in_the_app_s_words(env):
    user = {"id": "u1"}
    rid, tid = started(user)
    take(user, tasks_=[state(tid)], now=T0 + 60)
    # the page says it passed: the app takes whether every result passed
    take(user, tasks_=[state(tid, round_of(passed=True), checking=True)], now=T0 + 70)
    row = task_row(tid)
    assert json.loads(row["checks"]) == round_of(passed=False) and (row["state"], row["checking"]) == ("idle", 1)
    [line] = events("u1", "task.checked")
    assert line["payload"] == {"task": tid, "session": SESSION, "round": 1, "passed": 1, "failed": 1, "timed out": 0,
                               "protected": 0, "not run": 0}
    assert words(line) == ("The workspace ran this session's checks (round 1): 1 passed, 1 failed, 0 timed out, 0 "
                           "failed for protected paths, 0 not run")
    # taken once: the same round again is left
    take(user, tasks_=[state(tid, round_of(passed=True))], now=T0 + 80)
    assert len(events("u1", "task.checked")) == 1 and task_row(tid)["checking"] == 0


@pytest.mark.parametrize("bad", [
    lambda r: dict(r, round=0),
    lambda r: dict(r, round=controller.CHECK_ROUNDS + 1),
    lambda r: dict(r, head="main"),
    lambda r: dict(r, extra=1),
    lambda r: {k: v for k, v in r.items() if k != "at"},
    lambda r: dict(r, results=[]),
    lambda r: dict(r, results=[{"id": 3, "outcome": "passed"}]),            # chosen by no requirement of its
    lambda r: dict(r, results=[{"id": 2, "outcome": "passed"}, {"id": 1, "outcome": "passed"}]),   # out of order
    lambda r: dict(r, results=[{"id": 1, "outcome": "passed", "tail": "printed"}]),                 # an output
    lambda r: dict(r, results=[{"id": 1, "outcome": "failed"}]),
    lambda r: dict(r, results=[{"id": 1, "outcome": "protected", "protected": 0}]),
    lambda r: dict(r, results=[{"id": 1, "outcome": "lost"}]),
])
def test_a_round_of_another_form_is_left_and_its_task_s_state_taken_all_the_same(env, bad):
    user = {"id": "u1"}
    rid, tid = started(user)
    take(user, tasks_=[state(tid, bad(round_of()), state="working")], now=T0 + 60)
    row = task_row(tid)
    assert (row["checks"], row["state"], events("u1", "task.checked")) == (None, "working", [])


def test_a_not_run_s_why_and_an_end_s_why_are_kept_only_where_they_are_the_workspace_s_words(env):
    user = {"id": "u1"}
    rid, tid = started(user)
    results = [{"id": 1, "outcome": "not run", "why": "its task was stopped"},
               {"id": 2, "outcome": "not run", "why": "a program's own words"}]
    take(user, tasks_=[state(tid, round_of(results=results), why="a program's own words")], now=T0 + 60)
    row = task_row(tid)
    assert [r["why"] for r in json.loads(row["checks"])["results"]] == ["its task was stopped", None]
    assert row["why"] is None
    take(user, tasks_=[state(tid, state="failed", why="no commit since the task's base", ended_at=T0 + 70)],
         now=T0 + 80)
    assert task_row(tid)["why"] == "no commit since the task's base"
    [ended] = events("u1", "task.ended")
    assert ended["payload"]["why"] == "no commit since the task's base"
    assert words(ended) == "The session ended (failed): no commit since the task's base"


def test_the_app_s_words_are_the_workspace_s_list_word_for_word():
    from proofpane_workspace import controller_checks
    assert controller.CHECK_WHYS == controller_checks.WHYS
    assert controller.CHECK_NOT_RUN == controller_checks.NOT_RUN_WHYS
    assert controller.CHECK_ENDS == controller_checks.END_WHYS


@pytest.mark.parametrize("brk", ["\n", "\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029", "\t"])
def test_a_check_s_name_that_is_not_one_line_is_refused(client, owner, brk):
    h, _ = owner
    r = post(client, h, checks=[dict(CHECK, name="unit" + brk + "tests")])
    assert (r.status_code, r.json()["detail"]) == (400, "Check 1's name is one line, with no tab.")


def test_a_task_s_round_reaches_its_view_the_packet_and_the_board_as_outcomes_alone(env):
    user = {"id": "u1"}
    rid, tid = started(user)
    take(user, tasks_=[state(tid, round_of(), checking=True)], now=T0 + 60)
    [task] = controller.requirement_view(user, rid)["tasks"]
    assert (task["checks"], task["why"], task["checking"]) == (round_of(), None, True)
    with store.connect() as db:
        packet = json.loads(controller.build_packet(db, "u1", T0 + 200)[0])
    [given] = packet["information"]["requirements"][0]["tasks"]
    assert given["checks"] == {"round": 1, "passed": False} and "why" not in given
    rows = controller.board_rows("u1", T0 + 200)
    [row] = [r for r in rows if r["kind"] == "session"]
    assert "The workspace ran this session's checks (round 1)" in [e["words"] for e in row["events"]][-1]


def test_a_round_numbered_past_the_last_is_taken_and_one_at_or_below_it_is_left(env):
    user = {"id": "u1"}
    rid, tid = started(user)
    take(user, tasks_=[state(tid, round_of(n=2))], now=T0 + 60)
    take(user, tasks_=[state(tid, round_of(n=1, results=[{"id": 1, "outcome": "passed"}]))], now=T0 + 70)
    take(user, tasks_=[state(tid, round_of(n=3, results=[{"id": 1, "outcome": "passed"}]))], now=T0 + 80)
    assert [e["payload"]["round"] for e in events("u1", "task.checked")] == [2, 3]
    assert json.loads(task_row(tid)["checks"])["passed"] is True


def test_a_change_of_whether_a_round_is_queued_or_running_alone_moves_no_version(env):
    user = {"id": "u1"}
    rid, tid = started(user)
    take(user, tasks_=[state(tid, checking=False)], now=T0 + 60)

    def version():
        with store.connect() as db:
            return db.execute("SELECT version FROM ade_controllers WHERE user_id = 'u1'").fetchone()[0]
    before = version()
    take(user, tasks_=[state(tid, checking=True)], now=T0 + 70)
    assert (version(), task_row(tid)["checking"]) == (before, 1)
    take(user, tasks_=[state(tid, round_of(), checking=False)], now=T0 + 80)
    assert version() == before + 1                              # a round taken moves it: the packet carries it
