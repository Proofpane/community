"""Proofpane Community is open source under the Apache License 2.0: its LICENSE is the Apache License 2.0's text,
and each place that names Community's own licence says so and calls it nothing else: NOTICE, the README, the notices
the builds write beside the app (desktop/licenses.py for its Python and Rust crates, ui/licenses.ts for its npm
packages), the Licenses dialog, and the UI package's manifest and its lock and the desktop and phone apps'
Cargo manifests, by the licence's SPDX identifier."""

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


def test_the_notice_names_the_apache_licence_and_nothing_else_for_community():
    notice = _text("NOTICE")
    assert "Licensed under the Apache License, Version 2.0: the LICENSE file beside this one" in notice
    found = NOT_OPEN.search(notice)
    assert found is None, found and found.group(0)


def test_the_readme_says_community_is_open_source_under_the_apache_licence():
    readme = re.sub(r"\s+", " ", _text("README.md"))
    assert "Proofpane Community is open source under the Apache License 2.0 (`LICENSE`" in readme
    found = NOT_OPEN.search(readme)
    assert found is None, found and found.group(0)


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
