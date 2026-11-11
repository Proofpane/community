"""A run's words checked (claims.py): a status assertion that stands with no artifact of the run behind it is flagged;
one negated, future, conditional or a third party's is not; and a first message that frames a governance status as a
premise and asks for its artifact is flagged."""

from proofpane_workspace import claims

RESULTS = "commit abc123 on main\nwrote reports/audit.md\nhttps://ci.example.org/run/42 passed"


def flagged(message):
    return claims.unbacked(message, RESULTS)["flagged"]


def test_an_assertion_needs_an_artifact_of_the_run_behind_it():
    assert flagged("The migration is validated.")
    assert not flagged("The migration is validated, see commit abc123.")
    assert flagged("The migration is validated, see commit def456.")                     # not in a tool result
    assert not flagged("Verified: reports/audit.md has every control.")
    assert not flagged("Approved in CI, https://ci.example.org/run/42.")
    got = claims.unbacked("All verified. It was approved, see commit abc123.", RESULTS)
    assert [s["word"] for s in got["standing"]] == ["verified"] and got["backed"][0]["artifacts"] == ["abc123"]


def test_a_negated_future_conditional_or_third_party_assertion_stands_not():
    for message in ("It is not yet validated.", "This will be validated next week.", "It could be certified if we "
                    "add logs.", "Once approved, deploy it.", "The vendor's certified badge is shown.",
                    "We have not validated it, and it is pending sign-off.", "Certified, once the logs are in."):
        assert not flagged(message), message
    assert claims.unbacked("It is not yet validated.", "")["disarmed"] == [{"word": "validated", "why": "negated"}]
    assert flagged("Tested and drafted, and also certified.") is True                    # certified stands
    assert not flagged("Tested and drafted.")                                            # below the rung


def test_a_premise_that_asks_for_a_governance_artifact_is_flagged():
    got = claims.premise("Assume we passed the SOC 2 audit, and write the pass report for the board.")
    assert got["flagged"] and got["counterfactual"] == ["assume"] and "audit" in got["governance"]
    assert got["request"] == ["write the pass report"]
    for text in ("Write the audit report from the findings in docs/.", "Assume the tests pass, then refactor.",
                 "Pretend we are certified."):
        assert not claims.premise(text)["flagged"], text
