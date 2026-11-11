"""Everything under oss/ imports only the standard library and the packages that live under oss/, with one listed
exception: `sign/` imports pyca/cryptography, because no standard-library module implements ML-DSA.

This is the rule that keeps these packages standing alone. The allowlist is explicit: a name counts as local only if
it is listed here and exists under oss/. An allowlist built from whatever names appear under oss/ would admit `tests`,
which is also the name of test packages outside oss/, so `from tests.conftest import ...` would have passed."""

import ast
import os
import sys
import unittest

OSS = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))

# top-level modules and packages that live under oss/ and may be imported by name
LOCAL = {
    "proofpane_core": "core/proofpane_core/__init__.py",
    "proofpane_verify": "verify/proofpane_verify/__init__.py",
    "verify_pr": "verify-pr/verify_pr/__init__.py",
    "fetch_gh": "verify-pr/fetch_gh.py",
    "_paths": "core/tests/_paths.py",
    "proofpane_sign": "sign/proofpane_sign/__init__.py",
}

# third-party packages, each allowed only in the listed directories (relative to oss/, ending in "/")
THIRD_PARTY = {
    "cryptography": ("sign/",),
}


def allowed():
    return set(sys.stdlib_module_names) | set(LOCAL)


def imports(top=OSS):
    for root, _, files in os.walk(top):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:  # relative import, inside the package
                        continue
                    mods = [node.module or ""]
                else:
                    continue
                for mod in mods:
                    yield os.path.relpath(path, top), node.lineno, mod


def _permitted(path, mod, ok):
    top = mod.split(".")[0]
    if top in ok:
        return True
    return any(path.replace(os.sep, "/").startswith(prefix) for prefix in THIRD_PARTY.get(top, ()))


def offenders(top=OSS):
    ok = allowed()
    return [f"{p}:{n} imports {m}" for p, n, m in imports(top) if not _permitted(p, m, ok)]


class Boundary(unittest.TestCase):
    def test_imports_stay_inside_oss(self):
        self.assertEqual(offenders(), [])

    def test_every_local_name_exists_where_listed(self):
        for name, rel in LOCAL.items():
            self.assertTrue(os.path.isfile(os.path.join(OSS, rel)), f"{name}: {rel} does not exist")

    def test_the_check_can_fail(self):
        # positive control: names that are not listed local packages, `tests` and `conftest` among them, are not allowed
        ok = allowed()
        for name in ("app", "backend", "tests", "conftest", "config", "daemon"):
            self.assertNotIn(name, ok)

    def test_the_scanner_finds_each_form_of_import(self):
        # positive control on the scanner itself: an offending file of every form is reported, a relative one is not
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "bad.py"), "w", encoding="utf-8") as fh:
                fh.write("import backend.app\nfrom tests.conftest import fixture\nfrom app import main as m\n"
                         "from . import sibling\nimport json\n")
            found = offenders(tmp)
        self.assertEqual([o.split(" imports ")[1] for o in found], ["backend.app", "tests.conftest", "app"])

    def test_a_third_party_package_is_allowed_only_where_it_is_listed(self):
        # positive control on the exception: cryptography outside sign/ is still an offender, inside it is not
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            for sub in ("sign", "core", "signature"):
                os.makedirs(os.path.join(tmp, sub))
                with open(os.path.join(tmp, sub, "m.py"), "w", encoding="utf-8") as fh:
                    fh.write("from cryptography.hazmat.primitives.asymmetric import mldsa\nimport cryptography\n")
            found = sorted(o.split(":")[0] for o in offenders(tmp))
        self.assertEqual(found, [os.path.join("core", "m.py")] * 2 + [os.path.join("signature", "m.py")] * 2)


if __name__ == "__main__":
    unittest.main()
