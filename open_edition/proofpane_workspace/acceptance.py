"""An agent's acceptance checks, deterministic, on a run's final message: the keys
its JSON must have (`required_keys`), values at paths in it (`json_path_equals`, a path as `a.b.0.c`), the highest risk
tier it may name (`max_risk_tier`, of low, medium, high and critical, read from its `risk_tier`), and words it must not
contain (`forbidden_substrings`). The JSON is the message's fenced JSON block, else its first object. When a check fails
the run is `failed` (on_fail `fail`), waits for the owner (`hitl`), or is asked once more on the agent's fallback model
(`fallback`) and fails if that answer fails too."""

import json
import re


from .shapes import ACCEPTANCE_ON_FAIL as ON_FAIL, ACCEPTANCE_TIERS as TIERS, ShapeError as AcceptanceError  # noqa: F401
from .shapes import check_acceptance as check_spec  # noqa: F401

MAX_ITEMS = 20


_DECODER = json.JSONDecoder()


def _object_at(text, start):
    """The JSON object that begins at `start`, read as JSON reads it (a brace in a string is a string's), or None."""
    try:
        got, _ = _DECODER.raw_decode(text, start)
    except ValueError:
        return None
    return got if isinstance(got, dict) else None


def _json_of(text):
    text = text or ""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced and fenced.group(1).lstrip().startswith("{"):
        got = _object_at(fenced.group(1), fenced.group(1).index("{"))
        if got is not None:
            return got
    start, tries = text.find("{"), 0
    while start >= 0 and tries < 200:
        got = _object_at(text, start)
        if got is not None:
            return got
        start, tries = text.find("{", start + 1), tries + 1
    return None


def _same(got, want):
    """JSON values equal as JSON has them: true is not 1, nor false 0."""
    if isinstance(got, bool) or isinstance(want, bool):
        return type(got) is type(want) and got == want
    if isinstance(got, dict) and isinstance(want, dict):
        return got.keys() == want.keys() and all(_same(got[k], want[k]) for k in got)
    if isinstance(got, list) and isinstance(want, list):
        return len(got) == len(want) and all(_same(a, b) for a, b in zip(got, want))
    return got == want


def _at(data, path):
    for part in path.split("."):
        if isinstance(data, dict) and part in data:
            data = data[part]
        elif isinstance(data, list) and part.isdigit() and int(part) < len(data):
            data = data[int(part)]
        else:
            return KeyError
    return data


def check(spec, final):
    """{pass, failures} of the run's final message against the checks."""
    if not spec:
        return {"pass": True, "failures": []}
    failures = []
    data = _json_of(final) if spec["required_keys"] or spec["json_path_equals"] or spec["max_risk_tier"] else None
    needs_json = spec["required_keys"] or spec["json_path_equals"] or spec["max_risk_tier"]
    if needs_json and data is None:
        failures.append("its final message holds no JSON object")
    if data is not None:
        failures += [f"no key {k}" for k in spec["required_keys"] if k not in data]
        for path, want in spec["json_path_equals"].items():
            got = _at(data, path)
            if got is KeyError:
                failures.append(f"no {path}")
            elif not _same(got, want):
                failures.append(f"{path} is {json.dumps(got)[:60]}, not {json.dumps(want)[:60]}")
        if spec["max_risk_tier"]:
            tier = data.get("risk_tier")
            if tier not in TIERS:
                failures.append("no risk_tier of low, medium, high or critical")
            elif TIERS.index(tier) > TIERS.index(spec["max_risk_tier"]):
                failures.append(f"risk_tier {tier} is above {spec['max_risk_tier']}")
    failures += [f"it contains {w!r}" for w in spec["forbidden_substrings"] if w in (final or "")]
    return {"pass": not failures, "failures": failures[:MAX_ITEMS]}
