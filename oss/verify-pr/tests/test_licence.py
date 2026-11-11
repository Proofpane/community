"""Everything under oss/ is under the Apache License 2.0: oss/LICENSE holds its text byte for byte as apache.org serves
it, oss/NOTICE names PROOFPANE LIMITED and the third-party work the tree carries, and oss/README.md says so and no
longer that no licence has been applied. Tree-wide, like test_boundary.py beside it."""

import hashlib
import importlib.util
import json
import os
import unittest

OSS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# The sha256 of the Apache License 2.0's text as apache.org serves it (LICENSE-2.0.txt).
APACHE_SHA256 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
# What NOTICE says of the two documents the vectors come from: each by its name, address and own copyright line, quoted,
# and the documents' own sentence on the provisions, quoted
IETF_CARRIED = (
    "sign/vectors/composite/id-MLDSA65-Ed25519-SHA512.json, from draft-ietf-lamps-pq-composite-sigs-19, Appendix E "
    "(https://www.ietf.org/archive/id/draft-ietf-lamps-pq-composite-sigs-19.txt): \"Copyright (c) 2026 IETF Trust "
    "and the persons identified as the document authors. All rights reserved.\"",
    "core/vectors/jcs/objects.json, from RFC 8785, sections 3.2.2 and 3.2.3 "
    "(https://www.rfc-editor.org/rfc/rfc8785.txt): \"Copyright (c) 2020 IETF Trust and the persons identified as the "
    "document authors. All rights reserved.\"",
    "Each of these two documents' notices says: \"This document is subject to BCP 78 and the IETF Trust's Legal "
    "Provisions Relating to IETF Documents (https://trustee.ietf.org/license-info) in effect on the date of "
    "publication of this document.\"",
)
# Section 6.d's text of the IETF Trust's Legal Provisions Relating to IETF Documents, its year 2026, word for word
SECTION_6_D = (
    "Copyright (c) 2026 IETF Trust and the persons identified as authors of the code. All rights reserved. "
    "Redistribution and use in source and binary forms, with or without modification, is permitted pursuant to, and "
    "subject to the license terms contained in, the Revised BSD License set forth in Section 4.c of the IETF Trust’s "
    "Legal Provisions Relating to IETF Documents (http://trustee.ietf.org/license-info).")


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

    def test_the_notice_attributes_each_vector_to_its_document(self):
        # each document the vectors come from, by its name, address and own copyright line, and the documents' own
        # sentence on the provisions, each quoted
        notice = " ".join(_read("NOTICE").decode("utf-8").split())
        for words in IETF_CARRIED:
            self.assertIn(words, notice)
        self.assertNotIn("under the terms its document states", notice)    # neither document states terms of its own

    def test_the_draft_s_vector_carries_the_trust_s_section_6_d_notice(self):
        # the vector's legend, and the LEGEND of the tool that writes it, are section 6.d's text, its year 2026
        with open(os.path.join(OSS, "sign", "vectors", "composite", "id-MLDSA65-Ed25519-SHA512.json"),
                  encoding="utf-8") as fh:
            self.assertEqual(json.load(fh).get("legend"), SECTION_6_D)
        spec = importlib.util.spec_from_file_location(
            "extract_composite_vector", os.path.join(OSS, "sign", "tools", "extract_composite_vector.py"))
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(getattr(tool, "LEGEND", None), SECTION_6_D)

    def test_the_readme_says_the_licence(self):
        readme = " ".join(_read("README.md").decode("utf-8").split())
        self.assertIn("Everything under `oss/` is under the Apache License 2.0 (`LICENSE`", readme)
        self.assertNotIn("No licence has been applied", readme)
        self.assertNotIn("Nothing here is published yet", readme)


if __name__ == "__main__":
    unittest.main()
