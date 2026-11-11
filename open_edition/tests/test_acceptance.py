"""An agent's acceptance checks on a run's final message (acceptance.py): its JSON's keys, values at paths, the
highest risk tier it may name, and words it must not contain; the JSON found fenced or not."""

from proofpane_workspace import acceptance

SPEC = {"required_keys": [], "json_path_equals": {}, "max_risk_tier": None, "forbidden_substrings": [],
        "on_fail": "fail", "fallback_model": None}


def check(final, **over):
    return acceptance.check(dict(SPEC, **over), final)


def test_no_checks_pass_anything():
    assert acceptance.check(None, "anything") == {"pass": True, "failures": []}


def test_the_keys_and_the_values_at_paths_of_its_first_json_object():
    final = 'Done.\n```json\n{"labels": ["bug"], "issues": [{"state": "open"}, {"state": "labelled"}]}\n```'
    assert check(final, required_keys=["labels", "issues"])["pass"]
    assert check(final, required_keys=["labels", "summary"])["failures"] == ["no key summary"]
    assert check(final, json_path_equals={"issues.1.state": "labelled", "labels.0": "bug"})["pass"]
    assert check(final, json_path_equals={"issues.0.state": "labelled"})["failures"] == \
        ['issues.0.state is "open", not "labelled"']
    assert check(final, json_path_equals={"issues.5.state": "x"})["failures"] == ["no issues.5.state"]
    assert check('Here {"a": 1} and then {"b": 2}', required_keys=["a"])["pass"]          # the first object
    fenced = 'A draft was {"labels": []}.\n```json\n{"labels": ["bug"], "done": true}\n```'
    assert check(fenced, required_keys=["done"])["pass"]                                   # a fenced block first
    assert check("no JSON at all", required_keys=["a"])["failures"] == ["its final message holds no JSON object"]
    assert check('{"a": 1', required_keys=["a"])["failures"] == ["its final message holds no JSON object"]


def test_the_risk_tier_it_names_may_be_at_most_the_highest_allowed():
    assert check('{"risk_tier": "medium"}', max_risk_tier="medium")["pass"]                 # as high: allowed
    assert check('{"risk_tier": "low"}', max_risk_tier="medium")["pass"]
    assert check('{"risk_tier": "high"}', max_risk_tier="medium")["failures"] == ["risk_tier high is above medium"]
    assert check('{"risk_tier": "unknown"}', max_risk_tier="high")["failures"] == \
        ["no risk_tier of low, medium, high or critical"]


def test_words_it_must_not_contain_need_no_json():
    assert check("All done. TODO: the rest", forbidden_substrings=["TODO", "FIXME"])["failures"] == ["it contains 'TODO'"]
    assert check("All done.", forbidden_substrings=["TODO"])["pass"]


def test_a_brace_in_a_string_is_the_string_s_and_true_is_not_one():
    from proofpane_workspace import acceptance
    spec = {"required_keys": ["summary"], "json_path_equals": {"approved": True}, "max_risk_tier": None,
            "forbidden_substrings": [], "on_fail": "fail", "fallback_model": None}
    got = acceptance.check(spec, 'Here: {"summary": "closed the stray } in app.js", "approved": true}')
    assert got == {"pass": True, "failures": []}
    one = acceptance.check(spec, '{"summary": "x", "approved": 1}')
    assert one["pass"] is False and one["failures"] == ["approved is 1, not true"]
    fenced = acceptance.check(spec, '```json\n{"summary": {"nested": "}"}, "approved": true}\n```')
    assert fenced["pass"] is True
