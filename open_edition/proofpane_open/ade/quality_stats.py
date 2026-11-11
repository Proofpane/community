"""The arithmetic of an agent's quality, stdlib only.

Judge trust: how often the judge's pass agrees with the owner's thumbs on the runs the owner labelled, per judge
version, as raw agreement, Gwet's AC1 and Krippendorff's alpha (nominal, two raters), each with a bootstrap 95% interval
over the labelled runs; never a bare kappa, which skewed labels make read low. Nothing is said before MIN_LABELLED. The
band is read from alpha's interval's lower bound: TRUSTED and above, the judge's pass rate drives drift and the improve
loop; from TENTATIVE, it is shown and drives nothing; below, greyed.

Drift: counted in runs, since an agent's judged runs are few: the current window is its last WINDOW judged runs, the
baseline the BASELINE before them, and nothing is said until both hold WINDOW. A warning when the pass rate dropped by
WARN_DROP and the current rate is below CEILING; critical at CRITICAL_DROP, or a current rate below FLOOR."""

import random

MIN_LABELLED = 20
TRUSTED = 0.80
TENTATIVE = 0.667
RESAMPLES = 1000
WINDOW = 20
BASELINE = 60
WARN_DROP = 0.10
CRITICAL_DROP = 0.20
CEILING = 0.85
FLOOR = 0.5


def agreement(pairs):
    return sum(1 for a, b in pairs if a == b) / len(pairs) if pairs else None


def ac1(pairs):
    """Gwet's AC1 for two raters and two values: (pa - pe) / (1 - pe), pe = 2 pi (1 - pi), pi the mean pass share."""
    if not pairs:
        return None
    pa = agreement(pairs)
    pi = (sum(1 for a, _ in pairs if a) + sum(1 for _, b in pairs if b)) / (2 * len(pairs))
    pe = 2 * pi * (1 - pi)
    return 1.0 if pe == 1 else (pa - pe) / (1 - pe)


def alpha(pairs):
    """Krippendorff's alpha, nominal, two raters, no value missing: 1 - (N - 1) D / (n0 n1), N the 2n values, D the
    units the raters differ on, n0 and n1 the values of each kind; None when every value is the same."""
    if not pairs:
        return None
    values = [v for pair in pairs for v in pair]
    n1 = sum(1 for v in values if v)
    n0 = len(values) - n1
    if n0 == 0 or n1 == 0:
        return None
    differ = sum(1 for a, b in pairs if a != b)
    return 1 - (len(values) - 1) * differ / (n0 * n1)


def interval(pairs, statistic, resamples=RESAMPLES, seed=0):
    """A bootstrap 95% interval of `statistic` over the units, resampled with replacement (seeded, so a page shows
    the same interval each time); None when too few resamples give a value."""
    rng = random.Random(seed)
    got = []
    for _ in range(resamples):
        sample = [pairs[rng.randrange(len(pairs))] for _ in pairs]
        value = statistic(sample)
        if value is not None:
            got.append(value)
    if len(got) < resamples // 2:
        return None
    got.sort()
    return (round(got[int(0.025 * (len(got) - 1))], 4), round(got[int(0.975 * (len(got) - 1))], 4))


def trust(pairs):
    """{labelled, agreement, ac1, alpha, agreement_interval, ac1_interval, alpha_interval, band} over (judge pass,
    owner thumbs up) pairs; band None before MIN_LABELLED, and `unvaried` when every label and every verdict is the one
    value, which leaves alpha with nothing to weigh (it drives nothing, as tentative does not)."""
    out = {"labelled": len(pairs), "agreement": None, "ac1": None, "alpha": None, "agreement_interval": None,
           "ac1_interval": None, "alpha_interval": None, "band": None}
    if len(pairs) < MIN_LABELLED:
        return out
    round4 = lambda v: round(v, 4) if v is not None else None  # noqa: E731
    out.update(agreement=round4(agreement(pairs)), ac1=round4(ac1(pairs)), alpha=round4(alpha(pairs)),
               agreement_interval=interval(pairs, agreement), ac1_interval=interval(pairs, ac1),
               alpha_interval=interval(pairs, alpha))
    if len({v for pair in pairs for v in pair}) == 1:
        out["band"] = "unvaried"
        return out
    low = out["alpha_interval"][0] if out["alpha_interval"] else None
    out["band"] = "greyed" if low is None or low < TENTATIVE else "tentative" if low < TRUSTED else "trusted"
    return out


def drift(passes):
    """{level, current, baseline, drop} over judged runs' passes, oldest first; level None, warning or critical, and
    nothing said (level None, rates None) until both windows hold WINDOW."""
    current = passes[-WINDOW:]
    base = passes[-(WINDOW + BASELINE):-WINDOW] if len(passes) > WINDOW else []
    if len(current) < WINDOW or len(base) < WINDOW:
        return {"level": None, "current": None, "baseline": None, "drop": None}
    now, before = sum(current) / len(current), sum(base) / len(base)
    drop = round(before - now, 4)
    level = None
    if drop >= CRITICAL_DROP - 1e-9 or now < FLOOR:
        level = "critical"
    elif drop >= WARN_DROP - 1e-9 and now < CEILING:
        level = "warning"
    return {"level": level, "current": round(now, 4), "baseline": round(before, 4), "drop": drop}
