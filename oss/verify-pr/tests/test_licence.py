"""Everything under oss/ is under the Apache License 2.0: oss/LICENSE holds its text byte for byte as apache.org serves
it, oss/NOTICE names PROOFPANE LIMITED and the third-party work the tree carries, and oss/README.md says so and no
longer that no licence has been applied. Tree-wide, like test_boundary.py beside it."""

import hashlib
import os
import unittest

OSS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# The sha256 of the Apache License 2.0's text as apache.org serves it (LICENSE-2.0.txt).
APACHE_SHA256 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"


def _read(name):
    """The file's bytes, or none where it is not there (as oss/LICENSE and oss/NOTICE once were not)."""
    path = os.path.join(OSS, name)
    if not os.path.isfile(path):
        return b""
    with open(path, "rb") as fh:
        return fh.read()


class LicenceTests(unittest.TestCase):
    def test_the_licence_is_the_apache_licence_s_text(self):
        self.assertEqual(hashlib.sha256(_read("LICENSE")).hexdigest(), APACHE_SHA256)

    def test_the_notice_names_the_holder_the_licence_and_the_vectors_from_elsewhere(self):
        notice = " ".join(_read("NOTICE").decode("utf-8").split())
        self.assertIn("Copyright 2026 PROOFPANE LIMITED", notice)
        self.assertIn("Licensed under the Apache License, Version 2.0: the LICENSE file beside this one.", notice)
        self.assertIn("sign/vectors/composite/id-MLDSA65-Ed25519-SHA512.json", notice)
        self.assertIn("draft-ietf-lamps-pq-composite-sigs-19, Appendix E", notice)

    def test_the_readme_says_the_licence(self):
        readme = " ".join(_read("README.md").decode("utf-8").split())
        self.assertIn("Everything under `oss/` is under the Apache License 2.0 (`LICENSE`", readme)
        self.assertNotIn("No licence has been applied", readme)
        self.assertNotIn("Nothing here is published yet", readme)


if __name__ == "__main__":
    unittest.main()
