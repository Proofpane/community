"""The improve loop's arithmetic (improve_stats.py) and what it may change (shapes.check_change, check_skill)."""

from math import comb

import pytest

from proofpane_open.ade import improve_stats as s
from proofpane_workspace import shapes

PASS, FAIL = True, False


def pairs(wins=0, losses=0, both=0, neither=0):
    """(control passed, treatment passed) pairs."""
    return [(FAIL, PASS)] * wins + [(PASS, FAIL)] * losses + [(PASS, PASS)] * both + [(FAIL, FAIL)] * neither


def test_five_of_five_differing_pairs_are_eligible_and_six_of_seven_are_not():
    assert s.sign_test(5, 0) == 1 / 32 and s.sign_test(7, 1) == 9 / 256 and s.sign_test(6, 1) == 8 / 128
    comparison = pairs(both=5)
    assert s.eligibility(pairs(wins=5, neither=1), comparison)["eligible"] is True
    six = s.eligibility(pairs(wins=6, losses=1), comparison)
    assert (six["evaluated"], six["eligible"], six["p"]) == (True, False, 0.0625)
    assert s.eligibility(pairs(wins=7, losses=1), comparison)["eligible"] is True


def test_a_change_that_does_nothing_is_not_eligible():
    alike = s.eligibility(pairs(both=4, neither=4), pairs(both=6))           # both arms pass alike
    assert (alike["evaluated"], alike["wins"], alike["losses"], alike["p"], alike["eligible"]) == (True, 0, 0, 1.0, False)


def test_one_comparison_regression_is_allowed_and_two_are_not():
    signal = pairs(wins=5)
    assert s.eligibility(signal, pairs(losses=1, both=4))["eligible"] is True
    two = s.eligibility(signal, pairs(losses=2, both=3))
    assert (two["regressions"], two["eligible"]) == (2, False) and "2 comparison runs" in two["why"]


def test_too_few_replayable_signal_runs_or_pairs_evaluate_nothing():
    few = s.eligibility(pairs(wins=4), pairs(both=8))
    assert (few["evaluated"], few["eligible"]) == (False, False) and "fewer than 5" in few["why"]
    short = s.eligibility(pairs(wins=5), pairs(both=4))
    assert (short["evaluated"], short["eligible"]) == (False, False) and "fewer than 10 pairs" in short["why"]
    assert s.eligibility(pairs(wins=5), pairs(both=5))["evaluated"] is True


def test_the_fisher_test_of_a_drop_after_a_promotion():
    # 10 of 20 against 19 of 20: P(X <= 10), X hypergeometric with 29 passes in 40, 20 drawn
    hand = sum(comb(29, x) * comb(11, 20 - x) for x in (9, 10)) / comb(40, 20)
    assert s.fisher_drop(10, 20, 19, 20) == hand and hand < 0.05
    assert s.fisher_drop(16, 20, 19, 20) > 0.05                              # 3 fewer: not significant
    assert s.fisher_drop(20, 20, 10, 20) == 1.0 and s.fisher_drop(0, 0, 10, 20) == 1.0


def test_edits_count_the_places_a_change_touches():
    assert s.edits("a\nb\nc", "a\nB\nc\nd") == 2 and s.edits("a", "a") == 0
    assert s.edits("high", "max") == 1 and s.edits(200000, 300000) == 1 and s.edits(3, 3) == 0


@pytest.mark.parametrize("given", [
    {"target": "allow", "value": ["Bash(*)"]}, {"target": "permission_mode", "value": "bypassPermissions"},
    {"target": "mcp", "value": []}, {"target": "code", "value": "print(1)"}, {"target": "src/app.py", "value": "x"},
    {"target": "limits", "value": {"max_steps": 999}}, {"target": "memory", "value": "MEMORY.md"},
    {"target": "knowledge", "value": "knowledge/a.md"}, "brief"])
def test_a_change_to_permissions_tools_code_limits_memory_or_knowledge_is_refused(given):
    with pytest.raises(shapes.ShapeError):
        shapes.check_change(dict(given, hypothesis="h") if isinstance(given, dict) else given)


def test_a_change_the_loop_may_propose_is_held_to_its_shape():
    got = shapes.check_change({"target": "brief", "value": "Label the issues.", "hypothesis": "Clearer.",
                               "evidence": {"runs": ["a" * 12, "a" * 12], "metric": "judge task_fit"}})
    assert got == {"target": "brief", "name": None, "value": "Label the issues.", "hypothesis": "Clearer.",
                   "evidence": {"runs": ["a" * 12], "metric": "judge task_fit"}}
    for bad in ({"target": "effort", "value": "ultra", "hypothesis": "h"},
                {"target": "autocompact_window", "value": 50_000, "hypothesis": "h"},
                {"target": "model", "value": "Opus 4", "hypothesis": "h"},
                {"target": "brief", "value": "x" * 4001, "hypothesis": "h"},
                {"target": "brief", "value": "ok", "hypothesis": "two\nlines"},
                {"target": "brief", "value": "ok", "hypothesis": "h", "evidence": {"runs": ["../x"]}},
                {"target": "brief", "value": "ok", "hypothesis": "h", "allow": ["Bash(*)"]}):
        with pytest.raises(shapes.ShapeError):
            shapes.check_change(bad)


@pytest.mark.parametrize("frontmatter", ["allowed-tools: Bash(*)", "hooks:\n  PreToolUse: []", "model: opus",
                                         "shell: bash", "agent: x", '"allowed-tools": Bash', "{hooks: x}",
                                         "allowed-tools : Bash"])
def test_a_skill_whose_frontmatter_has_more_than_its_name_description_and_when_is_refused(frontmatter):
    with pytest.raises(shapes.ShapeError):
        shapes.check_skill("labels", f"---\nname: labels\ndescription: Label the issues.\n{frontmatter}\n---\nDo it.\n")


def test_a_skill_with_its_name_description_and_when_is_kept_with_its_line_ends_made_plain():
    text = "---\r\nname: labels\r\ndescription: >\r\n  Label the issues: by area.\r\nwhen_to_use: new issues\r\n---\r\nDo it.\r\n"
    assert shapes.check_skill("labels", text) == text.replace("\r\n", "\n")
    for name, bad in (("Labels", "---\ndescription: d\n---\n"), ("labels", "no frontmatter"),
                      ("labels", "---\nname: labels\n---\n"), ("labels", "---\ndescription: d\n---\n" + "x" * 17000)):
        with pytest.raises(shapes.ShapeError):
            shapes.check_skill(name, bad)
    assert shapes.check_skills({"b": "---\ndescription: d\n---\n", "a": "---\ndescription: e\n---\n"}) == \
        {"a": "---\ndescription: e\n---\n", "b": "---\ndescription: d\n---\n"}
    with pytest.raises(shapes.ShapeError):
        shapes.check_skills({f"s{n}": "---\ndescription: d\n---\n" for n in range(21)})
