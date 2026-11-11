"""The modules both sides keep: the workspace agent and the app import nothing of each other
(test_workspace_boundary.py), so what both must hold things to is kept as two copies, byte for byte the same, each
importing nothing but the standard library."""

import ast
import os
import sys

from conftest import ROOT

PAIRS = (("proofpane_workspace/shapes.py", "proofpane_open/ade/shapes.py"),
         ("proofpane_workspace/registry.py", "proofpane_open/ade/registry.py"),
         ("proofpane_workspace/task_shapes.py", "proofpane_open/ade/task_shapes.py"))


def test_each_copy_is_the_same_bytes():
    for workspace, app in PAIRS:
        with open(os.path.join(ROOT, workspace), "rb") as a, open(os.path.join(ROOT, app), "rb") as b:
            assert a.read() == b.read(), f"{app} differs from {workspace}: change both"


def test_each_copy_imports_only_the_standard_library():
    for path in (p for pair in PAIRS for p in pair):
        tree = ast.parse(open(os.path.join(ROOT, path), encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.level == 0 and node.module.split(".")[0] in sys.stdlib_module_names, (path, node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] in sys.stdlib_module_names, (path, alias.name)


def test_the_memory_digest_is_the_folder_s_whatever_order_its_files_come_in():
    from proofpane_workspace import shapes
    first = shapes.memory_digest({"b.md": b"two", "a/x.md": b"one"})
    assert first == shapes.memory_digest({"a/x.md": b"one", "b.md": b"two"})
    assert first != shapes.memory_digest({"a/x.md": b"one", "b.md": b"too"})
