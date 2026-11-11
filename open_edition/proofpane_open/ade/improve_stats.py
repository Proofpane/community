"""The arithmetic of the improve loop, stdlib only.

A proposal's sandbox gives pairs: each input run replayed once with the current target (control) and once with the
proposal (treatment), each output passing or failing. It is eligible when, on the signal inputs, the exact one-sided
sign test over the pairs whose outcomes differ gives at most P_MAX (five of five such pairs for the treatment gives
0.031, seven of eight 0.035; six of seven gives 0.0625, not enough), and, on the comparison inputs, at most
MAX_REGRESSIONS pairs have the control passing and the treatment failing. Nothing is evaluated with fewer than
MIN_SIGNAL replayable signal inputs or fewer than MIN_PAIRS pairs in all.

After a promotion, the new version's passes over its first WATCH graded runs are weighed against the previous
version's last WATCH by the exact one-sided Fisher test: at most P_MAX says the new one passes less often."""

import difflib
from math import comb

P_MAX = 0.05
MIN_SIGNAL = 5
MIN_PAIRS = 10
MAX_REGRESSIONS = 1
WATCH = 20


def sign_test(wins, losses):
    """P(at least `wins` of the wins + losses differing pairs favour the treatment | no difference): one-sided, exact."""
    n = wins + losses
    if n == 0:
        return 1.0
    return sum(comb(n, i) for i in range(wins, n + 1)) / 2 ** n


def fisher_drop(new_pass, new_n, old_pass, old_n):
    """P(the new version passes `new_pass` of `new_n` times or fewer | both pass alike, the passes fixed): the exact
    one-sided Fisher test that it passes less often than the old one's `old_pass` of `old_n`."""
    passes, total = new_pass + old_pass, new_n + old_n
    if new_n == 0 or old_n == 0:
        return 1.0
    low = max(0, passes - old_n)
    return sum(comb(passes, x) * comb(total - passes, new_n - x) for x in range(low, new_pass + 1)) / comb(total, new_n)


def tally(pairs):
    """(wins, losses) over (control passed, treatment passed) pairs: the treatment's wins and its losses."""
    wins = sum(1 for control, treatment in pairs if treatment and not control)
    losses = sum(1 for control, treatment in pairs if control and not treatment)
    return wins, losses


def too_few(signal, comparison):
    """Why `signal` signal inputs and `comparison` comparison inputs evaluate nothing, or None when they are enough."""
    if signal < MIN_SIGNAL:
        return f"fewer than {MIN_SIGNAL} replayable signal runs ({signal})"
    if signal + comparison < MIN_PAIRS:
        return f"fewer than {MIN_PAIRS} pairs in all ({signal + comparison})"
    return None


def eligibility(signal, comparison):
    """{evaluated, why, wins, losses, p, regressions, eligible} over the signal and the comparison pairs."""
    out = {"evaluated": False, "why": None, "wins": 0, "losses": 0, "p": None, "regressions": 0, "eligible": False}
    out["why"] = too_few(len(signal), len(comparison))
    if out["why"]:
        return out
    wins, losses = tally(signal)
    _, regressions = tally(comparison)
    p = sign_test(wins, losses)
    out.update(evaluated=True, wins=wins, losses=losses, p=round(p, 6), regressions=regressions,
               eligible=p <= P_MAX and regressions <= MAX_REGRESSIONS)
    if not out["eligible"]:
        out["why"] = (f"{regressions} comparison runs passed before and failed with it" if regressions > MAX_REGRESSIONS
                      else f"the signal runs' differing pairs, {wins} for it and {losses} against, give p {p:.3f}")
    return out


def edits(old, new):
    """How many places a text change touches: the changed runs of lines (a scalar's change is one)."""
    if not isinstance(old, str) or not isinstance(new, str):
        return 0 if old == new else 1
    a, b = old.splitlines(), new.splitlines()
    return sum(1 for tag, *_ in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if tag != "equal")
