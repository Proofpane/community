"""Proofpane Community is open source under the Apache License 2.0: its LICENSE is the Apache License 2.0's text,
and each place that names Community's own licence says so and calls it nothing else: NOTICE, the README, the notices
the builds write beside the app (desktop/licenses.py for its Python and Rust crates, ui/licenses.ts for its npm
packages), the Licenses dialog, and the UI package's manifest and its lock and the desktop and phone apps'
Cargo manifests, by the licence's SPDX identifier. The sign-in pages' sources hold none of the words that said
otherwise: a deployment's own terms are its operator's note (OPEN_EDITION_SIGN_IN_NOTE), not the pages'."""

import json
import os
import re
import tomllib

from conftest import ROOT

# Words that said Community was not open source: none may stay where Community's own licence is named.
NOT_OPEN = re.compile(r"proprietary|free to use|not open source|all rights reserved|offered free of charge", re.I)
SAYS_IT = "Proofpane Community is open source under the Apache License 2.0 (its LICENSE and NOTICE files)."


def _text(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def test_the_licence_is_the_apache_licence_s_text():
    licence = _text("LICENSE")
    assert licence == _text("LICENSE-APACHE-2.0")
    assert re.match(r"\s*Apache License\s+Version 2\.0, January 2004\s+http://www\.apache\.org/licenses/", licence)


# A copyright line NOTICE quotes from a document, whole: it is taken out before NOT_OPEN reads the rest, and a line of
# Community's own is still found
IETF_COPYRIGHT = re.compile(r"Copyright \(c\) \d{4} IETF Trust and the persons identified as the document authors\. "
                            r"All rights reserved\.")


def test_the_notice_names_the_apache_licence_and_nothing_else_for_community():
    notice = " ".join(_text("NOTICE").split())
    assert "Licensed under the Apache License, Version 2.0: the LICENSE file beside this one" in notice
    # the words are still found anywhere but in such a line
    assert NOT_OPEN.search(IETF_COPYRIGHT.sub("", "Copyright 2026 PROOFPANE LIMITED. All rights reserved."))
    assert not NOT_OPEN.search(IETF_COPYRIGHT.sub("", "Copyright (c) 2026 IETF Trust and the persons identified as the "
                                                      "document authors. All rights reserved."))
    found = NOT_OPEN.search(IETF_COPYRIGHT.sub("", notice))
    assert found is None, found and found.group(0)


# What NOTICE says of the two documents the tests carry from: each by its name, address and own copyright line, quoted,
# and the documents' own sentence on the provisions, quoted
IETF_CARRIED = (
    "tests/vectors/xwing_draft11.json, from draft-connolly-cfrg-xwing-kem-11, Appendix C "
    "(https://www.ietf.org/archive/id/draft-connolly-cfrg-xwing-kem-11.txt): \"Copyright (c) 2026 IETF Trust and the "
    "persons identified as the document authors. All rights reserved.\"",
    "tests/test_ade_push.py, from RFC 8291, Section 5 and Appendix A (https://www.rfc-editor.org/rfc/rfc8291.txt): "
    "\"Copyright (c) 2017 IETF Trust and the persons identified as the document authors. All rights reserved.\"",
    "Each of these two documents' notices says: \"This document is subject to BCP 78 and the IETF Trust's Legal "
    "Provisions Relating to IETF Documents (https://trustee.ietf.org/license-info) in effect on the date of "
    "publication of this document.\"",
)
# Section 6.d's text of the IETF Trust's Legal Provisions Relating to IETF Documents, its year 2017: the notice over RFC
# 8291's values in tests/test_ade_push.py, word for word
SECTION_6_D = (
    "Copyright (c) 2017 IETF Trust and the persons identified as authors of the code. All rights reserved. "
    "Redistribution and use in source and binary forms, with or without modification, is permitted pursuant to, and "
    "subject to the license terms contained in, the Revised BSD License set forth in Section 4.c of the IETF Trust’s "
    "Legal Provisions Relating to IETF Documents (http://trustee.ietf.org/license-info).")


def test_the_notice_names_each_document_the_tests_carry_from_in_its_own_words():
    notice = " ".join(_text("NOTICE").split())
    for words in IETF_CARRIED:
        assert words in notice, words
    assert "under the terms that" not in notice, "neither document states terms of its own"


def test_rfc_8291_s_values_carry_the_trust_s_section_6_d_notice():
    lines = _text("tests", "test_ade_push.py").split("\n")
    comments = " ".join(line.strip()[1:].strip() for line in lines if line.strip().startswith("#"))
    assert SECTION_6_D in " ".join(comments.split())


def test_the_readme_says_community_is_open_source_under_the_apache_licence():
    readme = re.sub(r"\s+", " ", _text("README.md"))
    assert "Proofpane Community is open source under the Apache License 2.0 (`LICENSE`" in readme
    found = NOT_OPEN.search(readme)
    assert found is None, found and found.group(0)


def test_the_sign_in_pages_sources_hold_none_of_the_words_that_said_community_was_not_open_source():
    for parts in (("web", "index.html"), ("ui", "src", "components", "SignIn.tsx")):
        found = NOT_OPEN.search(re.sub(r"\s+", " ", _text(*parts)))
        assert found is None, "%s: %s" % ("/".join(parts), found and found.group(0))


def test_the_notices_the_builds_write_say_so():
    for parts, count in ((("desktop", "licenses.py"), 2), (("ui", "licenses.ts"), 1)):
        text = _text(*parts)
        assert text.count(SAYS_IT) == count, "/".join(parts)
        found = NOT_OPEN.search(text)
        assert found is None, "%s: %s" % ("/".join(parts), found and found.group(0))


def test_the_licenses_dialog_names_the_apache_licence():
    dialog = _text("ui", "src", "components", "Licenses.tsx")
    assert '{ file: "LICENSE", label: "Proofpane Community\'s license: the Apache License 2.0" }' in dialog
    found = NOT_OPEN.search(dialog)
    assert found is None, found and found.group(0)


def test_the_ui_package_names_the_apache_licence_by_its_spdx_identifier():
    package = json.loads(_text("ui", "package.json"))
    lock = json.loads(_text("ui", "package-lock.json"))
    assert (package["license"], lock["packages"][""]["license"]) == ("Apache-2.0", "Apache-2.0")


def test_the_app_crates_name_the_apache_licence_by_its_spdx_identifier():
    for app in ("desktop", "phone"):
        package = tomllib.loads(_text(app, "src-tauri", "Cargo.toml"))["package"]
        assert (package.get("license"), "license-file" in package) == ("Apache-2.0", False), app
