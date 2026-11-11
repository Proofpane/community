"""No module of the product defines a top-level function or class twice. With many changes merged into one file, a later
definition of a name silently replaces an earlier one, and the code that called the first runs the second (once
the move's `_judged` replaced the remote command guard's `_judged` in ade_link.py, which only a failing test
run showed). A name redefined on purpose belongs under one definition, so this guard has no exceptions."""

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _modules():
    yield ROOT / "ade_link.py"
    yield ROOT / "ade_hook.py"
    for package in ("proofpane_open", "proofpane_workspace"):
        yield from sorted((ROOT / package).rglob("*.py"))


def _defined_twice(path):
    """The top-level names `path` defines more than once, each with the lines of its definitions."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    lines = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            lines.setdefault(node.name, []).append(node.lineno)
    return {name: at for name, at in lines.items() if len(at) > 1}


def test_no_module_defines_a_top_level_name_twice():
    found = {str(path.relative_to(ROOT)): twice for path in _modules() if (twice := _defined_twice(path))}
    assert found == {}, f"defined more than once (module: name: lines): {found}"


def test_the_guard_sees_a_name_defined_twice(tmp_path):
    # the check itself, on a module that defines one name twice and another once
    sample = tmp_path / "sample.py"
    sample.write_text("def a():\n    pass\n\n\ndef b():\n    pass\n\n\nclass a:\n    pass\n", encoding="utf-8")
    assert _defined_twice(sample) == {"a": [1, 9]}
