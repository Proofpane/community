"""Judge trust and drift (quality_stats.py), against hand computations."""

from proofpane_open.ade import quality_stats as q

# 20 labelled runs: 14 both pass, 3 both fail, 2 the judge passed and the owner did not, 1 the other way
PAIRS = [(True, True)] * 14 + [(False, False)] * 3 + [(True, False)] * 2 + [(False, True)]


def test_ac1_and_alpha_match_a_hand_computation_with_their_interval():
    # pa = 17/20; pi = (16 + 15) / 40; pe = 2 pi (1 - pi); AC1 = (pa - pe) / (1 - pe)
    pi = 31 / 40
    pe = 2 * pi * (1 - pi)
    assert abs(q.ac1(PAIRS) - (0.85 - pe) / (1 - pe)) < 1e-12
    # alpha = 1 - (N - 1) D / (n0 n1): 40 values, 3 units differ, 9 fails and 31 passes
    assert abs(q.alpha(PAIRS) - (1 - 39 * 3 / (9 * 31))) < 1e-12
    got = q.trust(PAIRS)
    assert (got["labelled"], got["agreement"], got["ac1"], got["alpha"]) == (20, 0.85, 0.7697, 0.5806)
    low, high = got["alpha_interval"]
    assert low <= 0.5806 <= high and got["band"] == "greyed"
    assert got["ac1_interval"][0] <= 0.7697 <= got["ac1_interval"][1]
    assert q.trust(PAIRS) == got                                         # seeded: the same interval each time


def test_nothing_is_said_before_twenty_labels_and_the_band_follows_alpha_s_lower_bound():
    assert q.trust(PAIRS[:19])["band"] is None and q.trust(PAIRS[:19])["alpha"] is None
    agreeing = [(True, True)] * 17 + [(False, False)] * 3
    assert q.trust(agreeing)["band"] == "trusted" and q.trust(agreeing)["alpha"] == 1.0
    assert q.alpha([(True, True)] * 20) is None                           # every value the same: undefined
    alike = q.trust([(True, True)] * 20)                                 # so the band says so, not "disagrees"
    assert (alike["band"], alike["agreement"], alike["agreement_interval"]) == ("unvaried", 1.0, (1.0, 1.0))
    # 10 both pass, 9 both fail, 1 apart: alpha = 1 - 39 * 1 / (19 * 21), high, but its interval's lower bound is not
    middling = [(True, True)] * 10 + [(False, False)] * 9 + [(True, False)]
    got = q.trust(middling)
    assert abs(got["alpha"] - round(1 - 39 / 399, 4)) < 1e-12
    assert got["alpha_interval"] == (0.688, 1.0) and got["band"] == "tentative"
    assert got["agreement_interval"][0] < got["agreement"] <= got["agreement_interval"][1]


def runs(baseline, current):
    """Judged runs' passes, oldest first: 20 baseline runs with `baseline` passes, then 20 with `current`."""
    return [True] * baseline + [False] * (20 - baseline) + [True] * current + [False] * (20 - current)


def test_drift_is_silent_until_both_windows_hold_twenty_then_warns_at_ten_points_below_eighty_five():
    assert q.drift(runs(19, 14)[1:])["level"] is None                    # 39 judged runs: nothing said
    assert q.drift(runs(19, 17)) == {"level": None, "current": 0.85, "baseline": 0.95, "drop": 0.1}   # not below 0.85
    assert q.drift(runs(19, 16))["level"] == "warning"                   # 0.80, 15 points down
    assert q.drift(runs(19, 15))["level"] == "critical"                  # 20 points down
    assert q.drift(runs(10, 9))["level"] == "critical"                   # below 0.5
    assert q.drift(runs(16, 15))["level"] is None                        # 5 points down
    longer = [False] * 30 + [True] * 54 + [False] * 6 + runs(20, 16)[20:]  # the 60 before the last 20, and older
    assert (q.drift(longer)["baseline"], q.drift(longer)["level"]) == (0.9, "warning")
