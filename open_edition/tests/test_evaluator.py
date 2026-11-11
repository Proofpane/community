"""A live run's anomaly rules (evaluator.py): each at its warning and its critical level, once a level a run."""

from proofpane_workspace import evaluator


def levels(found):
    return [(a["rule"], a["level"]) for a in found]


def test_the_same_tool_and_input_warns_at_five_and_is_critical_at_eight():
    w = evaluator.Watch(started_at=0)
    fired = {n + 1: w.tool("Bash", {"command": "npm test"}, now=n) for n in range(10)}
    assert {n: levels(got) for n, got in fired.items() if got} == {5: [("tool_repetition", "warning")],
                                                                    8: [("tool_repetition", "critical")]}
    assert fired[5][0]["evidence"] == "Bash called 5 times with the same input"
    assert levels(evaluator.Watch().tool("Bash", {"command": "npm test -- a"})) == []
    other = evaluator.Watch()
    for n in range(7):
        other.tool("Read", {"file_path": f"src/{n}.py"})
    assert levels(other.tool("Read", {"file_path": "src/0.py"})) == []             # another input: counted apart


def test_calls_that_change_nothing_and_use_nothing_new_warn_at_eight_and_are_critical_at_fifteen():
    w = evaluator.Watch()
    w.tool("Read", {"file_path": "a"})                                             # new: progress
    fired = {n + 1: levels(w.tool("Read", {"file_path": f"f{n}"})) for n in range(15)}
    assert {n: got for n, got in fired.items() if got} == {8: [("no_progress", "warning")],
                                                           15: [("no_progress", "critical")]}
    reset = evaluator.Watch()
    for n in range(7):
        reset.tool("Read", {"file_path": f"f{n}"})
    reset.tool("Edit", {"file_path": "x"})                                         # a change starts it over
    assert levels(sum((reset.tool("Read", {"file_path": f"g{n}"}) for n in range(7)), [])) == []


def test_context_spend_and_time_each_at_their_levels_once():
    w = evaluator.Watch(started_at=1000, max_run_seconds=600, compaction_window=100_000, median_usd=0.10)
    assert levels(w.turn(context_tokens=79_000, cost_usd=0.25, now=1100)) == []
    assert levels(w.turn(context_tokens=80_000, cost_usd=0.30, now=1200)) == [("context_budget", "warning"),
                                                                              ("spend", "warning")]
    assert levels(w.turn(context_tokens=90_000, cost_usd=0.59, now=1300)) == []          # once a level
    assert levels(w.turn(cost_usd=0.60, now=1400)) == [("spend", "critical")]
    assert levels(w.tick(now=1479)) == [] and levels(w.tick(now=1480)) == [("time_budget", "warning")]
    assert levels(evaluator.Watch(median_usd=None).turn(cost_usd=99.0)) == []           # no median yet: nothing
