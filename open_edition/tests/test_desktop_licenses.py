"""The third-party licenses the desktop and phone apps carry besides their pages (desktop/licenses.py, NOTICE): a
license expression is read as SPDX reads it; each Python package, library and crate is named with its license and the
license files it ships, or for a crate that ships none, a text supplied from what it declares; anything under a
license the apps do not take, or with no text, is named, so the build stops on it; the record kept of what
python-build-standalone links into its Python is the pinned release's; and the builds write the notices where the apps
carry them."""

import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile

import pytest

from conftest import ROOT

DESKTOP = os.path.join(ROOT, "desktop")


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(DESKTOP, file))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _licenses():
    return _load("desktop_licenses", "licenses.py")


def _bundle():
    return _load("desktop_bundle", "bundle.py")


def _sha(text):
    return hashlib.sha256(text.encode() if isinstance(text, str) else text).hexdigest()


# --- SPDX expressions ---

@pytest.mark.parametrize("expression, met", [
    ("MIT", True), ("mit", True), ("MIT OR GPL-3.0-only", True), ("GPL-3.0-only OR MIT", True),
    ("GPL-3.0-only", False), ("MIT AND GPL-3.0-only", False), ("(MIT OR Apache-2.0) AND Unicode-3.0", True),
    ("(MIT OR GPL-3.0-only) AND (LGPL-2.1-only OR Zlib)", True), ("(MIT OR GPL-3.0-only) AND LGPL-2.1-only", False),
    ("MIT/Apache-2.0", True), ("Apache-2.0 / MIT", True), ("GPL-2.0-only/LGPL-2.1-only", False),
    ("Apache-2.0 WITH LLVM-exception", True), ("GPL-2.0-only WITH Classpath-exception-2.0", False),
    ("MIT or GPL-3.0-only", True), ("MIT and GPL-3.0-only", False), ("MPL-2.0", False),
    ("", False), (None, False), ("MIT OR", False), ("OR MIT", False), ("(MIT", False), ("MIT)", False),
    ("MIT Apache-2.0", False), ("MIT WITH", False), ("WITH MIT", False), ("MIT WITH (x)", False),
    ("MIT WITH OR", False), ("()", False), ("MIT AND", False), ("MIT OR OR", False), ("MIT OR AND", False),
    ("MIT OR )", False), ("MIT OR WITH", False),
])
def test_an_expression_is_met_as_spdx_reads_it(expression, met):
    assert _licenses().allowed(expression) is met


def test_what_one_license_alone_meets():
    lic = _licenses()
    assert lic.allowed("Zlib OR Apache-2.0 OR MIT", {"Apache-2.0"})
    assert not lic.allowed("(MIT OR Apache-2.0) AND Unicode-3.0", {"Apache-2.0"})
    assert lic.allowed("MPL-2.0", lic.ALLOWED | lic.SOURCE_NAMED) and not lic.allowed("MPL-2.0")
    # every license the Python's libraries are read as is one the app takes
    assert all(lic.allowed(license) for _name, license, _file in lic.LIBRARIES.values())


@pytest.mark.parametrize("rel, inside", [
    ("LICENSE", True), ("docs/AUTHORS", True), ("../LICENSE", False), ("a/../../LICENSE", False),
    ("/etc/passwd", False), ("\\x", False), ("C:/x", False), ("", False),
])
def test_a_path_in_metadata_stays_inside(rel, inside):
    assert _licenses()._inside(rel) is inside


# --- the notice ---

def test_a_text_given_once_is_pointed_to_after_and_the_about_is_wrapped_whole_words():
    lic = _licenses()
    about = "word " * 21 + "aarch64-apple-darwin " + "word " * 40      # the triple would cross the line's end
    text = lic.notice("Title", about, [
        {"title": "a 1 (MIT)", "who": "a 1", "notes": ["a note"], "files": [("LICENSE", "same\n"), ("NOTICE", "a's")]},
        {"title": "b 2 (MIT)", "who": "b 2", "files": [("COPYING", "same  \n\n")], "after": ["after b"]},
        {"title": "c 3 (MIT)", "files": [("LICENSE", "Same"), ("NOTICE", "same")]},
    ])
    assert text.count("\nsame\n") == 1 and "\nSame\n" in text
    assert "--- COPYING\n\n(The same text as LICENSE of a 1, above.)\n" in text
    assert text.count("(The same text as LICENSE of a 1, above.)") == 2      # each points to the first
    assert text.startswith("Title\n\nword word")
    assert "aarch64-apple-darwin" in text and all(len(line) <= 116 for line in text.splitlines())
    assert "a 1 (MIT)\n" + lic.RULE + "\n\na note\n\n--- LICENSE\n\nsame\n" in text
    assert "after b" in text
    # a note is wrapped as the about is, a word longer than a line (an address) kept whole on its own
    url = "https://example.org/" + "x" * 120
    text = lic.notice("Title", "about", [{"title": "d 4 (MIT)", "notes": ["word " * 30 + f"from {url}, checked."]}])
    lines = text.split(lic.RULE + "\n")[2].strip().splitlines()
    assert lines == ["word " * 22 + "word", "word word word word word word word from", url + ",", "checked."]


def test_the_mit_text_is_the_mit_license_word_for_word_with_no_holder():
    lic = _licenses()
    assert lic.MIT_TEXT.startswith("MIT License\n\nPermission is hereby granted") and "Copyright" not in lic.MIT_TEXT
    assert all(len(line) <= 79 for line in lic.MIT_TEXT.splitlines())
    body = lic.MIT_TEXT.split()[2:]
    with open(os.path.join(ROOT, "web", "vendor", "xterm", "LICENSE"), encoding="utf-8") as fh:
        xterm = fh.read().split()
    assert xterm[xterm.index("Permission"):] == body
    react = os.path.join(ROOT, "ui", "node_modules", "react", "LICENSE")
    if os.path.exists(react):
        with open(react, encoding="utf-8") as fh:
            theirs = fh.read().split()
        assert theirs[theirs.index("Permission"):] == body


def test_the_supplied_texts_are_the_licenses_they_name():
    lic = _licenses()
    with open(lic.APACHE_TEXT, encoding="utf-8") as fh:
        apache = fh.read()
    assert "Apache License" in apache and "Version 2.0, January 2004" in apache
    assert "END OF TERMS AND CONDITIONS" in apache
    with open(lic.MPL_TEXT, encoding="utf-8") as fh:
        mpl = fh.read()
    assert mpl.startswith("Mozilla Public License Version 2.0\n")
    assert "Exhibit A - Source Code Form License Notice" in mpl and "3.2. Distribution of Executable Form" in mpl
    for cargo_home in (os.environ.get("CARGO_HOME") or os.path.expanduser("~/.cargo"),):
        for index in sorted(os.listdir(os.path.join(cargo_home, "registry", "src"))) if os.path.isdir(
                os.path.join(cargo_home, "registry", "src")) else ():
            shipped = os.path.join(cargo_home, "registry", "src", index, "cssparser-0.37.0", "LICENSE")
            if os.path.exists(shipped):
                with open(shipped, "rb") as a, open(lic.MPL_TEXT, "rb") as b:
                    assert a.read() == b.read()          # the copy is the one cssparser 0.37.0 ships


# --- the Python packages ---

def _dist(site, folder, metadata, files=None):
    """A .dist-info folder in `site`: its METADATA and other files (path: text or bytes)."""
    info = site / folder
    info.mkdir(parents=True)
    (info / "METADATA").write_text(metadata)
    for rel, data in (files or {}).items():
        path = info / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode())


def _meta(name, version, *lines):
    return "\n".join(["Metadata-Version: 2.4", f"Name: {name}", f"Version: {version}", *lines, "", "Its description."])


def test_each_package_is_named_with_its_license_and_the_license_files_it_ships(tmp_path):
    lic = _licenses()
    site = tmp_path / "site-packages"
    _dist(site, "good-1.0.dist-info", _meta("good", "1.0", "License-Expression: MIT", "License-File: LICENSE",
                                             "License-File: docs/AUTHORS") + "\nLicense: GPL-3.0-only, in the text\n",
          {"licenses/LICENSE": "good's license", "licenses/docs/AUTHORS": "good's authors", "RECORD": "x"})
    _dist(site, "Old-2.0.dist-info", _meta("Old", "2.0", "License: MIT OR Apache-2.0", "License-File: LICENSE.MIT"),
          {"LICENSE.MIT": "old's MIT"})
    _dist(site, "bare-3.0.dist-info", _meta("bare", "3.0", "License: BSD-3-Clause"),
          {"COPYING": "bare's", "README": "not a license", "licenses/LICENSE-APACHE": "bare's other"})
    _dist(site, "latin-1.0.dist-info", _meta("latin", "1.0", "License-Expression: ISC", "License-File: LICENSE"),
          {"licenses/LICENSE": b"Copyright \xe9"})
    # License-Expression over the old License field, and licenses/ over the folder's top
    _dist(site, "pref-1.0.dist-info", _meta("pref", "1.0", "License-Expression: MIT", "License: GPL-3.0-only",
                                            "License-File: LICENSE"),
          {"licenses/LICENSE": "pref's own", "LICENSE": "a stray one"})
    (site / "latin").mkdir()                                  # a package's own folder is not a .dist-info
    entries, problems = lic.distributions(str(site))
    assert problems == []
    assert [e["title"] for e in entries] == ["bare 3.0 (BSD-3-Clause)", "good 1.0 (MIT)", "latin 1.0 (ISC)",
                                             "Old 2.0 (MIT OR Apache-2.0)", "pref 1.0 (MIT)"]
    assert [e["files"] for e in entries if e["who"] == "pref 1.0"] == [[("pref-1.0.dist-info/licenses/LICENSE",
                                                                         "pref's own")]]
    files = {e["who"]: e["files"] for e in entries}
    assert files["good 1.0"] == [("good-1.0.dist-info/licenses/LICENSE", "good's license"),
                                 ("good-1.0.dist-info/licenses/docs/AUTHORS", "good's authors")]
    assert files["Old 2.0"] == [("Old-2.0.dist-info/LICENSE.MIT", "old's MIT")]
    assert files["bare 3.0"] == [("bare-3.0.dist-info/COPYING", "bare's"),
                                 ("bare-3.0.dist-info/licenses/LICENSE-APACHE", "bare's other")]
    assert files["latin 1.0"] == [("latin-1.0.dist-info/licenses/LICENSE", "Copyright " + chr(0xE9))]


def test_a_package_the_app_cannot_carry_is_named_with_why(tmp_path):
    lic = _licenses()
    site = tmp_path / "site-packages"
    _dist(site, "good-1.0.dist-info", _meta("good", "1.0", "License-Expression: MIT", "License-File: LICENSE"),
          {"licenses/LICENSE": "good's"})
    _dist(site, "gpl-1.0.dist-info", _meta("gpl", "1.0", "License-Expression: GPL-3.0-only", "License-File: COPYING"),
          {"licenses/COPYING": "gpl"})
    _dist(site, "text-1.0.dist-info", "Metadata-Version: 2.1\nName: text\nVersion: 1.0\nLicense: Copyright 2020 Someone\n"
                                      "  Permission is hereby granted\n", {"LICENSE": "x"})
    _dist(site, "words-1.0.dist-info", _meta("words", "1.0", "License: BSD License"), {"LICENSE": "x"})
    _dist(site, "unnamed-1.0.dist-info", _meta("unnamed", "1.0"), {"LICENSE": "x"})
    _dist(site, "missing-1.0.dist-info", _meta("missing", "1.0", "License-Expression: MIT", "License-File: LICENSE"))
    _dist(site, "escape-1.0.dist-info", _meta("escape", "1.0", "License-Expression: MIT",
                                              "License-File: ../good-1.0.dist-info/licenses/LICENSE"))
    _dist(site, "none-1.0.dist-info", _meta("none", "1.0", "License-Expression: MIT"), {"README": "no license"})
    (site / "nometa-1.0.dist-info").mkdir()
    entries, problems = lic.distributions(str(site))
    assert sorted(problems) == sorted([
        "gpl 1.0: its license, GPL-3.0-only, is not one the app takes",
        "text 1.0: its metadata names no license this build reads",
        "words 1.0: its metadata names no license this build reads",
        "unnamed 1.0: its metadata names no license this build reads",
        "missing 1.0: its metadata names the license file LICENSE, which it does not ship",
        "escape 1.0: its metadata names the license file ../good-1.0.dist-info/licenses/LICENSE, which it does not "
        "ship",
        "none 1.0: it ships no license file",
        "nometa-1.0.dist-info: it has no METADATA",
    ])
    assert "unnamed 1.0 (no license named)" in [e["title"] for e in entries]


def _tar(path, top, files):
    """A .tar.gz (a .crate is one) of `files` (path: text) under the folder `top`; returns its SHA-256."""
    with tarfile.open(path, "w:gz") as tar:
        for rel, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(f"{top}/{rel}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    with open(path, "rb") as fh:
        return _sha(fh.read())


REGISTRY = "registry+https://github.com/rust-lang/crates.io-index"


def _component(name, version, license=None, sha256="", ref=None, purl=None, **more):
    c = {"type": "library", "name": name, "version": version,
         "bom-ref": ref if ref is not None else f"{REGISTRY}#{name}@{version}",
         "purl": purl if purl is not None else f"pkg:cargo/{name}@{version}"}
    if license:
        c["licenses"] = [{"expression": license}]
    if sha256:
        c["hashes"] = [{"alg": "SHA-256", "content": sha256}]
    c.update(more)
    return c


def test_what_a_packages_sboms_list_is_named_with_the_license_files_of_its_own_source(tmp_path):
    lic = _licenses()
    archives, cargo_home = tmp_path / "archives", tmp_path / "cargo"
    archives.mkdir()
    cache = cargo_home / "registry" / "cache" / "index.crates.io-0"
    cache.mkdir(parents=True)
    # a in cargo's own cache; b there too but not the one the SBOM names, so it is fetched
    a = _tar(cache / "a-1.0.0.crate", "a-1.0.0", {"LICENSE-MIT": "a's MIT", "src/data/LICENSE-DATA": "a's data",
                                                  "src/copying.rs": "code"})
    _tar(cache / "b-2.0.0.crate", "b-2.0.0", {"LICENSE": "another b"})
    b = _tar(archives / "b-2.0.0.crate", "b-2.0.0", {"COPYING": "b's GPL"})
    nofile = _tar(archives / "nofile-1.0.0.crate", "nofile-1.0.0", {"README.md": "no license here"})
    apa = _tar(archives / "apa-1.0.0.crate", "apa-1.0.0", {"README.md": "no license here either"})
    sib = _tar(archives / "sib-1.0.0.crate", "sib-1.0.0", {"src/lib.rs": "x"})
    sib_files = _tar(archives / "sib-files-1.0.0.crate", "sib-files-1.0.0", {"LICENSE": "the pair's license"})
    openssl = _tar(archives / "openssl-4.0.2.tar.gz", "openssl-4.0.2",
                   {"LICENSE.txt": "Apache License text", "test/LICENSE": "a test's, not taken"})
    old = _tar(archives / "openssl-1.1.1.tar.gz", "openssl-1.1.1", {"LICENSE": "an older license"})
    pair = [{"type": "vcs", "url": "https://example.org/pair"}]
    sbom = {"bomFormat": "CycloneDX", "components": [
        _component("a", "1.0.0", "MIT OR Apache-2.0", a),
        _component("apa", "1.0.0", "MIT OR Apache-2.0", apa),
        _component("b", "2.0.0", "GPL-3.0-only", b, components=[_component("nofile", "1.0.0", "MIT", nofile,
                                                                           author="Ann")]),
        _component("sib", "1.0.0", "BSD-3-Clause", sib, externalReferences=pair),
        _component("sib-files", "1.0.0", "BSD-3-Clause", sib_files, externalReferences=pair),
        _component("openssl", "4.0.2", None, openssl, ref="openssl", purl="pkg:generic/openssl@4.0.2?download_url="
                   "https%3A%2F%2Fexample.org%2Fopenssl-4.0.2.tar.gz"),
        _component("openssl", "1.1.1", None, old, ref="old", purl="pkg:generic/openssl@1.1.1",
                   externalReferences=[{"type": "distribution", "url": "https://example.org/openssl-1.1.1.tar.gz"}]),
        _component("x-part", "1.0", "MIT", ref="path+file:///build/x#x-part@1.0"),
        _component("x-other", "1.0", "Apache-2.0", ref="path+file:///build/x#x-other@1.0"),
        _component("nosource", "1.0"),
        _component("../evil", "1.0.0", "MIT", a),
        "not a component",
    ]}
    site = tmp_path / "site-packages"
    _dist(site, "x-1.0.dist-info", _meta("x", "1.0", "License-Expression: MIT", "License-File: LICENSE"),
          {"licenses/LICENSE": "x's", "sboms/x.cyclonedx.json": json.dumps(sbom), "sboms/broken.json": "{",
           "sboms/spdx.json": json.dumps({"spdxVersion": "SPDX-2.3", "packages": []}),
           "sboms/empty.json": json.dumps({"bomFormat": "CycloneDX", "components": []})})
    _dist(site, "y-2.0.dist-info", _meta("y", "2.0", "License-Expression: MIT", "License-File: LICENSE"),
          {"licenses/LICENSE": "y's", "sboms/y.cyclonedx.json": json.dumps(
              {"bomFormat": "CycloneDX", "components": [_component("a", "1.0.0", "MIT OR Apache-2.0", a)]})})
    fetched = []

    def fetch(url, name, digest):
        fetched.append(url)
        path = archives / name
        assert _sha(path.read_bytes()) == digest
        return str(path)

    entries, problems = lic.distributions(str(site), fetch, str(cargo_home))
    assert sorted(problems) == sorted([
        "x 1.0: its SBOM x-1.0.dist-info/sboms/broken.json is not a CycloneDX document this build reads",
        "x 1.0: its SBOM x-1.0.dist-info/sboms/spdx.json is not a CycloneDX document this build reads",
        "x 1.0: its SBOM x-1.0.dist-info/sboms/x.cyclonedx.json lists x-other 1.0, part of it, under Apache-2.0, not "
        "its own license",
        "b 2.0.0: the SBOM of x 1.0 names its license, GPL-3.0-only, which is not one the app takes",
        "openssl 1.1.1: the SBOM of x 1.0 names no license for it",
        "nosource 1.0: the SBOM of x 1.0 gives no source this build can take its license text from (a crates.io "
        "crate, or a release's https address, with its SHA-256)",
        "../evil 1.0.0: the SBOM of x 1.0 gives no source this build can take its license text from (a crates.io "
        "crate, or a release's https address, with its SHA-256)",
        "nosource 1.0: the SBOM of x 1.0 names no license for it",
        # MIT, and no file: no holder is made up for it, from its SBOM's author or anything else
        "nofile 1.0.0: its source ships no license file, and none can be supplied for it",
    ])
    assert sorted(fetched) == sorted(f"https://{where}" for where in (
        "static.crates.io/crates/b/b-2.0.0.crate", "static.crates.io/crates/nofile/nofile-1.0.0.crate",
        "static.crates.io/crates/apa/apa-1.0.0.crate",
        "static.crates.io/crates/sib/sib-1.0.0.crate", "static.crates.io/crates/sib-files/sib-files-1.0.0.crate",
        "example.org/openssl-4.0.2.tar.gz", "example.org/openssl-1.1.1.tar.gz"))       # a was cargo's own
    by = {e["who"]: e for e in entries}
    assert [e["who"] for e in entries] == ["x 1.0", "y 2.0", "../evil 1.0.0", "a 1.0.0", "apa 1.0.0", "b 2.0.0",
                                           "nofile 1.0.0", "nosource 1.0", "openssl 1.1.1", "openssl 4.0.2",
                                           "sib 1.0.0", "sib-files 1.0.0"]
    assert by["a 1.0.0"]["files"] == [("LICENSE-MIT", "a's MIT"), ("src/data/LICENSE-DATA", "a's data")]
    assert by["a 1.0.0"]["notes"] == ["In the wheel of x 1.0 and of y 2.0, as its SBOM lists it; its license files "
                                      "are those of its source, from crates.io, checked against the SHA-256 the SBOM "
                                      "gives."]
    assert by["b 2.0.0"]["files"] == [("COPYING", "b's GPL")]
    assert by["nofile 1.0.0"]["files"] == [] and "Ann" not in json.dumps(by["nofile 1.0.0"])
    with open(lic.APACHE_TEXT, encoding="utf-8") as fh:
        assert by["apa 1.0.0"]["files"] == [("Apache-2.0", fh.read())]
    assert by["apa 1.0.0"]["notes"][1] == ("(It ships no license file. Its SBOM names MIT OR Apache-2.0; below is the "
                                           "text of Apache-2.0, which it offers and which names no holder, as "
                                           "LICENSE-APACHE-2.0 beside Proofpane Community's LICENSE holds it.)")
    assert by["sib 1.0.0"]["files"] == [("LICENSE", "the pair's license")]
    assert "sib-files 1.0.0, published from the same repository" in by["sib 1.0.0"]["notes"][1]
    assert by["openssl 4.0.2"]["title"] == "openssl 4.0.2 (Apache-2.0)"
    assert by["openssl 4.0.2"]["files"] == [("LICENSE.txt", "Apache License text")]
    assert by["openssl 4.0.2"]["notes"] == [
        "In the wheel of x 1.0, as its SBOM lists it; its license files are those of its source, from "
        "https://example.org/openssl-4.0.2.tar.gz, checked against the SHA-256 the SBOM gives.",
        "Its SBOM names no license; openssl has been under Apache-2.0 since version 3."]
    x = by["x 1.0"]["after"]
    assert " ".join(x).startswith("Its SBOM x-1.0.dist-info/sboms/x.cyclonedx.json lists these as built into it, each "
                                  "named below, after the packages: a 1.0.0, apa 1.0.0, b 2.0.0, nofile 1.0.0, "
                                  "sib 1.0.0,")
    assert " ".join(x).endswith("It lists these as parts of x itself, under its license above: x-part 1.0, x-other 1.0.")
    assert all(len(line) <= 116 for line in x)
    # with nothing to fetch with, what it lists is named and nothing is taken
    _e, problems = lic.distributions(str(site))
    assert "a 1.0.0: the SBOM of x 1.0 and of y 2.0 lists it, and this build fetches no sources" in problems


# --- the Python's notice ---

TRIPLE = "aarch64-apple-darwin"
STORED = {"license.rst": "cpython's license", "NOTICE.cpython-c.txt": "its c source's notices",
          "LICENSE.hacl.txt": "hacl's MIT", "NOTICE.krml.txt": "krml's notice",
          "LICENSE.openssl-3.txt": "openssl's license", "LICENSE.bzip2.txt": "bzip2's license"}
CHANGES = ["python/include, python/share", "pip"]


def _record(folder, files=STORED, **changes):
    """A desktop/third-party/python/ folder: the files and their record."""
    folder.mkdir(parents=True)
    for name, text in files.items():
        (folder / name).write_text(text)
    record = {
        "release": "20260929", "python": "3.13.15",
        "cpython": {"licenses": ["Python-2.0", "CNRI-Python"],
                    "files": ["license.rst", "NOTICE.cpython-c.txt", "LICENSE.hacl.txt", "NOTICE.krml.txt"],
                    "source": {"archive": "Python-3.13.15.tar.xz", "sha256": "1" * 64, "url": "https://x/y.tar.xz"}},
        "builds": {TRIPLE: {"archive": "a.tar.zst", "sha256": "0" * 64, "openssl": "OpenSSL 3.5.9 29 Sep 2026",
                            "extensions": {
                                "_bz2": {"shared": None, "static": ["bz2"]},
                                "_hashlib": {"shared": None, "static": ["crypto"]},
                                "_ssl": {"shared": None, "static": ["crypto", "ssl"]},
                                "_tkinter": {"shared": "lib/python3.13/lib-dynload/_tkinter.so",
                                             "static": ["tclstub", "tkstub"]}}}},
        "files": {name: _sha(text) for name, text in files.items()},
    }
    record.update(changes)
    (folder / "sources.json").write_text(json.dumps(record))
    return folder


def _python(tmp_path):
    """A Python laid out as the app carries it, with one package."""
    python = tmp_path / "python"
    (python / "lib" / "python3.13" / "lib-dynload").mkdir(parents=True)
    _dist(python / "lib" / "python3.13" / "site-packages", "one-1.0.dist-info",
          _meta("one", "1.0", "License-Expression: MIT", "License-File: LICENSE"), {"licenses/LICENSE": "one's"})
    return python


def test_the_pythons_notice_names_cpython_the_libraries_built_into_it_and_each_package(tmp_path):
    lic = _licenses()
    sources, python = _record(tmp_path / "sources"), _python(tmp_path)
    text, problems = lic.python_notice(str(python), TRIPLE, "3.13.15", "20260929", CHANGES, sources=str(sources))
    assert problems == []
    titles = [line for line in text.splitlines() if re.match(r"^(CPython|OpenSSL|bzip2|one) ", line)]
    assert titles == ["CPython 3.13.15 (Python-2.0 AND CNRI-Python)", "bzip2 (bzip2-1.0.6), built into _bz2",
                      "OpenSSL 3.5.9 (Apache-2.0), built into _hashlib, _ssl", "one 1.0 (MIT)"]
    words = " ".join(text.split())
    for said in ("cpython's license", "its c source's notices", "hacl's MIT", "krml's notice", "openssl's license",
                 "bzip2's license", "one's", "release 20260929", f"for {TRIPLE}"):
        assert said in words
    # the changes made to Python, which its licenses ask the app to summarize
    assert ("this app's build (desktop/bundle.py) then leaves out python/include, python/share; pip; installs the "
            "packages below into it;") in words
    # in the record's order, then the Apache License's text, which KaRaMeL's notice names
    cpython = text.split(lic.RULE + "\n")[2]
    assert cpython.index("--- license.rst") < cpython.index("--- NOTICE.cpython-c.txt") \
        < cpython.index("--- LICENSE.hacl.txt") < cpython.index("--- NOTICE.krml.txt") < cpython.index("--- Apache-2.0")
    with open(lic.APACHE_TEXT, encoding="utf-8") as fh:
        assert f"--- Apache-2.0\n\n{fh.read().rstrip()}\n" in cpython
    notes = cpython.split("\n--- ")[0].splitlines()                # wrapped
    assert len(notes) > 6 and all(len(line) <= 116 for line in notes)
    assert "tcl" not in text and "built here" not in text      # _tkinter was left out of the app
    # with OpenSSL built here, for cryptography
    text, problems = lic.python_notice(str(python), TRIPLE, "3.13.15", "20260929", CHANGES,
                                       ("3.5.9", "openssl's own text"), sources=str(sources))
    assert problems == [] and "OpenSSL 3.5.9 (Apache-2.0), built here into cryptography's extension module" in text
    assert "--- LICENSE.txt\n\nopenssl's own text\n" in text
    # with no summary of the changes made to it
    _t, problems = lic.python_notice(str(python), TRIPLE, "3.13.15", "20260929", [], sources=str(sources))
    assert problems == ["CPython 3.13.15: no summary of the changes made to it, which its licenses ask for"]


def test_a_module_the_app_carries_with_a_library_of_no_known_license_stops_the_build(tmp_path):
    lic = _licenses()
    sources, python = _record(tmp_path / "sources"), _python(tmp_path)
    (python / "lib" / "python3.13" / "lib-dynload" / "_tkinter.so").write_bytes(b"")
    _text, problems = lic.python_notice(str(python), TRIPLE, "3.13.15", "20260929", CHANGES, sources=str(sources))
    assert problems == [
        f"python-build-standalone builds {lib} into _tkinter, which the app carries, and desktop/licenses.py names "
        "no license for it (LIBRARIES)" for lib in ("tclstub", "tkstub")]


def test_a_record_not_of_this_release_or_build_or_changed_by_hand_stops_the_build(tmp_path):
    lic = _licenses()
    python = _python(tmp_path)
    sources = _record(tmp_path / "sources")
    _t, problems = lic.python_notice(str(python), TRIPLE, "3.13.16", "20260929", CHANGES, sources=str(sources))
    assert problems == ["desktop/third-party/python/ was written for CPython 3.13.15 from release 20260929, not "
                        "3.13.16 from 20260929: run python3 desktop/licenses.py python-sources"]
    _t, problems = lic.python_notice(str(python), TRIPLE, "3.13.15", "20261001", CHANGES, sources=str(sources))
    assert len(problems) == 1 and "not 3.13.15 from 20261001" in problems[0]
    _t, problems = lic.python_notice(str(python), "x86_64-apple-darwin", "3.13.15", "20260929", CHANGES,
                                     sources=str(sources))
    assert problems == ["desktop/third-party/python/ has no record of the build for x86_64-apple-darwin"]
    (sources / "LICENSE.bzip2.txt").write_text("edited")
    os.unlink(sources / "LICENSE.openssl-3.txt")
    (sources / "license.rst").write_text("edited too")
    _t, problems = lic.python_notice(str(python), TRIPLE, "3.13.15", "20260929", CHANGES, sources=str(sources))
    assert sorted(problems) == [f"desktop/third-party/python/{name} is not the file python-sources wrote"
                                for name in ("LICENSE.bzip2.txt", "LICENSE.openssl-3.txt", "license.rst")]
    other = _record(tmp_path / "other", cpython={"licenses": ["GPL-3.0-only"], "files": ["license.rst"]})
    _t, problems = lic.python_notice(str(python), TRIPLE, "3.13.15", "20260929", CHANGES, sources=str(other))
    assert problems == ["CPython 3.13.15: its license, GPL-3.0-only, is not one the app takes"]


def test_the_kept_record_is_the_pinned_releases_and_covers_what_the_app_carries(tmp_path):
    lic, bundle = _licenses(), _bundle()
    with open(os.path.join(lic.PYTHON_SOURCES, "sources.json"), encoding="utf-8") as fh:
        record = json.load(fh)
    assert (record["release"], record["python"]) == (bundle.RELEASE, bundle.PYTHON)
    assert record["cpython"]["source"] == {
        "archive": f"Python-{bundle.PYTHON}.tar.xz", "sha256": bundle.CPYTHON_SOURCE,
        "url": f"https://www.python.org/ftp/python/{bundle.PYTHON}/Python-{bundle.PYTHON}.tar.xz"}
    assert record["cpython"]["files"] == ["license.rst", "NOTICE.cpython-c.txt", "LICENSE.hacl.txt", "NOTICE.krml.txt"]
    triples = {triple for triple, _digest in bundle.BUILDS.values()}
    assert set(record["builds"]) == triples
    for triple, build in record["builds"].items():
        assert build["archive"] == f"cpython-{bundle.PYTHON}+{bundle.RELEASE}-{triple}-pgo+lto-full.tar.zst"
        assert re.fullmatch(r"[0-9a-f]{64}", build["sha256"])
    assert set(os.listdir(lic.PYTHON_SOURCES)) == set(record["files"]) | {"sources.json"}
    for name, digest in record["files"].items():
        with open(os.path.join(lic.PYTHON_SOURCES, name), "rb") as fh:
            assert _sha(fh.read()) == digest, name
    with open(os.path.join(lic.PYTHON_SOURCES, "license.rst"), encoding="utf-8") as fh:
        rst = fh.read()
    for section in ("PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2", "Licenses and Acknowledgements for Incorporated "
                    "Software", "mimalloc", "Global Unbounded Sequences (GUS)", "Unicode Character Database"):
        assert section in rst
    with open(os.path.join(lic.PYTHON_SOURCES, "NOTICE.cpython-c.txt"), encoding="utf-8") as fh:
        notices = fh.read()
    for holder, where in (("David Gottner", "Python/getopt.c"), ("Lance Ellinghouse", "Modules/syslogmodule.c"),
                          ("Martin von Loewis", "Modules/_localemodule.c"), ("Oliver Andrich", "Modules/_cursesmodule.c"),
                          ("Secret Labs AB", "Objects/unicodeobject.c"), ("Stefan Krah", "Modules/_decimal/_decimal.c")):
        assert f"In {where}" in notices and holder in notices.split(f"In {where}")[1].split("\nIn ")[0]
    with open(os.path.join(lic.PYTHON_SOURCES, "LICENSE.hacl.txt"), encoding="utf-8") as fh:
        assert fh.read().startswith("MIT License\n\nCopyright (c) 2016-2022 INRIA, CMU and Microsoft Corporation\n")
    # each build, laid out as bundle.trim leaves it, carries nothing of no known license
    for triple, build in record["builds"].items():
        python = tmp_path / triple / "python"
        for folder in ("bin", "lib/python3.13/site-packages", "lib/python3.13/lib-dynload"):
            (python / folder).mkdir(parents=True)
        for ext in build["extensions"].values():
            if ext["shared"]:
                (python / ext["shared"]).parent.mkdir(parents=True, exist_ok=True)
                (python / ext["shared"]).write_bytes(b"")
        changes = bundle.python_changes()
        _t, problems = lic.python_notice(str(python), triple, bundle.PYTHON, bundle.RELEASE, changes)
        assert problems and all("_tkinter" in p for p in problems)     # before trim, the app would carry Tk
        bundle.trim(str(python))
        _t, problems = lic.python_notice(str(python), triple, bundle.PYTHON, bundle.RELEASE, changes)
        assert problems == []


def test_the_summary_of_the_changes_names_what_trim_leaves_out():
    bundle = _bundle()
    said = " ".join(bundle.python_changes())
    for part in bundle.LEAVE_OUT_OF_PYTHON:
        assert f"python/{part}" in said
    for also in ("python/lib/python3.13/lib-dynload/_tkinter", "python/lib/python3.13/site-packages/pip",
                 "python/bin/"):
        assert also in said


# --- python-build-standalone's record ---

HELD = {"LICENSE.cpython.txt": b"its copy of python's", "LICENSE.bzip2.txt": b"bzip2",
        "LICENSE.openssl-3.txt": b"apache", "LICENSE.openssl-1.1.txt": b"old", "LICENSE.tcl.txt": b"tcl"}
CPYTHON = {"archive": "Python-3.13.15.tar.xz", "sha256": "1" * 64, "url": "https://x/y.tar.xz",
           "texts": {"license.rst": b"rst", "NOTICE.cpython-c.txt": b"c", "LICENSE.hacl.txt": b"hacl",
                     "NOTICE.krml.txt": b"krml"}}


def _pbs(python="3.13.15", extensions=None, licenses=("Python-2.0", "CNRI-Python")):
    """A PYTHON.json, read."""
    if extensions is None:
        extensions = {
            "_bz2": [{"links": [{"name": "bz2", "path_static": "build/lib/libbz2.a"}],
                      "license_paths": ["licenses/LICENSE.bzip2.txt"]}],
            "_ssl": [{"links": [{"name": "crypto", "path_static": "c"}, {"name": "ssl", "path_static": "s"},
                                {"name": "dl", "system": True}],
                      "license_paths": ["licenses/LICENSE.openssl-1.1.txt", "licenses/LICENSE.openssl-3.txt"]}],
            "_tkinter": [{"links": [{"name": "tclstub", "path_static": "t"}, {"name": "tcl9.0", "system": True},
                                    {"name": "CoreFoundation", "framework": True}],
                          "license_paths": ["licenses/LICENSE.tcl.txt"],
                          "shared_lib": "install/lib/python3.13/lib-dynload/_tkinter.so"}],
            "_json": [{"links": []}],
            "_curses": [{"links": [{"name": "ncurses", "system": True}], "license_paths": ["licenses/x.txt"]}],
        }
    return {"python_version": python, "licenses": list(licenses), "license_path": "licenses/LICENSE.cpython.txt",
            "build_info": {"extensions": extensions}}


def _build(triple, pbs=None, held=None, openssl="OpenSSL 3.5.9 29 Sep 2026"):
    return {"archive": f"{triple}.tar.zst", "sha256": "a" * 64, "python_json": pbs or _pbs(),
            "licenses": dict(HELD if held is None else held), "openssl": openssl}


def test_the_record_keeps_what_each_build_links_in_and_only_the_files_read_for_it():
    lic = _licenses()
    record, files = lic.sources_record("20260929", "3.13.15", {"x86_64-apple-darwin": _build("x86_64-apple-darwin"),
                                                               TRIPLE: _build(TRIPLE)}, CPYTHON)
    # CPython's own texts, from its source, not python-build-standalone's copy of its license
    assert files == {"license.rst": b"rst", "NOTICE.cpython-c.txt": b"c", "LICENSE.hacl.txt": b"hacl",
                     "NOTICE.krml.txt": b"krml", "LICENSE.bzip2.txt": b"bzip2", "LICENSE.openssl-3.txt": b"apache"}
    assert record["files"] == {name: _sha(data) for name, data in files.items()}
    assert record["cpython"] == {
        "licenses": ["Python-2.0", "CNRI-Python"],
        "files": ["license.rst", "NOTICE.cpython-c.txt", "LICENSE.hacl.txt", "NOTICE.krml.txt"],
        "source": {"archive": "Python-3.13.15.tar.xz", "sha256": "1" * 64, "url": "https://x/y.tar.xz"}}
    build = record["builds"][TRIPLE]
    assert build == {"archive": f"{TRIPLE}.tar.zst", "sha256": "a" * 64, "openssl": "OpenSSL 3.5.9 29 Sep 2026",
                     "extensions": {"_bz2": {"shared": None, "static": ["bz2"]},
                                    "_ssl": {"shared": None, "static": ["crypto", "ssl"]},
                                    "_tkinter": {"shared": "lib/python3.13/lib-dynload/_tkinter.so",
                                                 "static": ["tclstub"]}}}
    # no OpenSSL linked, none recorded
    pbs = _pbs(extensions={"_bz2": _pbs()["build_info"]["extensions"]["_bz2"]})
    record, _files = lic.sources_record("20260929", "3.13.15", {TRIPLE: _build(TRIPLE, pbs=pbs, openssl=None)},
                                        CPYTHON)
    assert record["builds"][TRIPLE]["openssl"] is None


@pytest.mark.parametrize("change, said", [
    (dict(pbs=_pbs(python="3.13.14")), "holds CPython 3.13.14, not 3.13.15"),
    (dict(pbs=_pbs(extensions={"_bz2": [{"links": [{"name": "bz2", "path_static": "b"}],
                                         "license_paths": ["licenses/LICENSE.other.txt"]}]})),
     "names ['LICENSE.other.txt'] for _bz2, not LICENSE.bzip2.txt, which desktop/licenses.py reads for bz2"),
    (dict(held={k: v for k, v in HELD.items() if k != "LICENSE.bzip2.txt"}), "does not hold LICENSE.bzip2.txt"),
    (dict(openssl="OpenSSL 1.1.1w  11 Sep 2023"), "not version 3"),
    (dict(openssl=None), "its OpenSSL is None, not version 3"),
    (dict(pbs=_pbs(extensions={"_t": [{"links": [{"name": "t", "path_static": "t"}], "shared_lib": "lib/t.so"}]})),
     "_t is a file outside the install (lib/t.so)"),
])
def test_the_record_refuses_what_licenses_py_would_read_wrongly(change, said):
    lic = _licenses()
    with pytest.raises(SystemExit) as refused:
        lic.sources_record("20260929", "3.13.15", {TRIPLE: _build(TRIPLE, **change)}, CPYTHON)
    assert said in str(refused.value)


def test_the_record_refuses_builds_that_disagree():
    lic = _licenses()
    other = dict(HELD, **{"LICENSE.bzip2.txt": b"another bzip2"})
    with pytest.raises(SystemExit) as refused:
        lic.sources_record("20260929", "3.13.15", {TRIPLE: _build(TRIPLE),
                                                   "x86_64-apple-darwin": _build("x86_64-apple-darwin", held=other)},
                           CPYTHON)
    assert "x86_64-apple-darwin: its LICENSE.bzip2.txt differs from another build's" in str(refused.value)
    with pytest.raises(SystemExit) as refused:
        lic.sources_record("20260929", "3.13.15", {
            TRIPLE: _build(TRIPLE), "x86_64-apple-darwin": _build("x86_64-apple-darwin", pbs=_pbs(licenses=["PSF-2.0"]))},
            CPYTHON)
    assert ("x86_64-apple-darwin: CPython's license is ['PSF-2.0'], not ['Python-2.0', 'CNRI-Python'] as in another "
            "build") in str(refused.value)


needs_zstd = pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd is not installed")


def _archive(path, members):
    """A .tar.zst holding `members` (name: bytes)."""
    plain = str(path) + ".tar"
    with tarfile.open(plain, "w") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    subprocess.run(["zstd", "-q", "-f", plain, "-o", str(path)], check=True)
    os.unlink(plain)
    return str(path)


@needs_zstd
def test_a_full_archive_is_read_for_its_record_its_licenses_and_its_openssl(tmp_path):
    lic = _licenses()
    crypto = b"\x00junk OpenSSL 3.0.0 \x00OpenSSL 3.5.9 29 Sep 2026\x00 more OpenSSL 3.5.9 29 Sep 2026\x00"
    path = _archive(tmp_path / "full.tar.zst", {
        "python/PYTHON.json": json.dumps({"python_version": "3.13.15"}).encode(),
        "python/licenses/LICENSE.a.txt": b"a",
        "python/licenses/deeper/LICENSE.b.txt": b"not at the top",
        "python/build/lib/libcrypto.a": crypto,
        "python/install/lib/libpython.a": b"x" * 100000,
    })
    assert lic._read_full_archive(path) == ({"python_version": "3.13.15"}, {"LICENSE.a.txt": b"a"},
                                            "OpenSSL 3.5.9 29 Sep 2026")
    two = _archive(tmp_path / "two.tar.zst", {"python/PYTHON.json": b"{}", "python/build/lib/libcrypto.a":
                                              b"OpenSSL 3.5.9 29 Sep 2026 OpenSSL 3.5.8 1 Jul 2026"})
    with pytest.raises(SystemExit) as refused:
        lic._read_full_archive(two)
    assert "more than one OpenSSL" in str(refused.value)
    for name, members in (("none.tar.zst", {"python/licenses/LICENSE.a.txt": b"a"}),):
        with pytest.raises(SystemExit) as refused:
            lic._read_full_archive(_archive(tmp_path / name, members))
        assert "not a full archive" in str(refused.value)
    (tmp_path / "garbage.tar.zst").write_bytes(b"not zstd at all")
    bad_json = _archive(tmp_path / "json.tar.zst", {"python/PYTHON.json": b"not json"})
    whole = _archive(tmp_path / "whole.tar.zst", {"python/PYTHON.json": b"{}", "python/licenses/LICENSE.a.txt": b"a"})
    with open(whole, "rb") as fh:
        data = fh.read()
    (tmp_path / "cut.tar.zst").write_bytes(data[:-4])         # its last bytes (zstd's checksum) cut off
    for path in (str(tmp_path / "garbage.tar.zst"), bad_json, str(tmp_path / "cut.tar.zst")):
        with pytest.raises(SystemExit) as refused:
            lic._read_full_archive(path)
        assert "not a full archive" in str(refused.value), path


@needs_zstd
def test_reading_a_full_archive_reads_its_stream_to_the_end(tmp_path):
    """tar stops at the archive's end; what zstd writes after it is read too, or zstd, its pipe full, never ends."""
    lic = _licenses()
    plain = tmp_path / "padded.tar"
    with tarfile.open(plain, "w") as tar:
        info = tarfile.TarInfo("python/PYTHON.json")
        info.size = 2
        tar.addfile(info, io.BytesIO(b"{}"))
    with open(plain, "ab") as fh:
        fh.write(bytes(4 << 20))
    subprocess.run(["zstd", "-q", "-f", str(plain), "-o", str(tmp_path / "padded.tar.zst")], check=True)
    script = ("import importlib.util, sys; spec = importlib.util.spec_from_file_location('l', sys.argv[1]); "
              "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); "
              "print(m._read_full_archive(sys.argv[2]))")
    done = subprocess.run([sys.executable, "-I", "-c", script, os.path.join(DESKTOP, "licenses.py"),
                           str(tmp_path / "padded.tar.zst")], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and done.stdout.strip() == "({}, {}, None)"


def test_reading_a_full_archive_without_zstd_says_so(monkeypatch):
    lic = _licenses()
    monkeypatch.setattr(lic.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit) as refused:
        lic._read_full_archive("anything.tar.zst")
    assert "need zstd" in str(refused.value)


HACL_MIT = ("/* MIT License\n *\n * Copyright (c) 2016-2022 INRIA, CMU and Microsoft Corporation\n"
            " * Copyright (c) 2022-2023 HACL* Contributors\n *\n"
            " * Permission is hereby granted, free of charge, to any person obtaining a copy\n"
            " * of this software and associated documentation files.\n */\n\n\n#include \"internal/Hacl_Hash_SHA2.h\"\n")
# KaRaMeL's notice, as two of its headers lay it out
KRML = ("/* Copyright (c) INRIA and Microsoft Corporation. All rights reserved.\n"
        "   Licensed under the Apache 2.0 License. */\n\n#ifndef __KRML_TARGET_H\n")
KRML_TOO = ("/*\n  Copyright (c) INRIA and Microsoft Corporation. All rights reserved.\n"
            "  Licensed under the Apache 2.0 License.\n*/\n\n\n#ifndef __FStar_UInt128_Verified_H\n")
RST = ("History and License\n\nMersenne Twister\n\n   Copyright (C) 1997 - 2002,  Makoto Matsumoto and Takuji Nishimura,"
       "\n   All rights reserved.\n")         # its spaces not those of the source's line, as rst lays text out
SYSLOG = ("/***********************************************************\nCopyright 1994 by Lance Ellinghouse,\n"
          "Cathedral City, California Republic, United States of America.\n\n"
          "Permission to use, copy, modify, and distribute this software.\n"
          "******************************************************************/\n\n#include \"Python.h\"\n")


def _source(path, version, files):
    """A CPython source archive (.tar.xz) holding `files` (path under its top folder: text)."""
    with tarfile.open(path, "w:xz") as tar:
        for rel, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(f"Python-{version}/{rel}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return str(path)


SOURCE = {"Doc/license.rst": RST, "Modules/_hacl/Hacl_Hash_SHA2.c": HACL_MIT, "Modules/_hacl/Hacl_Hash_SHA2.h": HACL_MIT,
          "Modules/_hacl/include/krml/internal/target.h": KRML,
          "Modules/_hacl/include/krml/FStar_UInt128_Verified.h": KRML_TOO, "Modules/syslogmodule.c": SYSLOG}


def test_the_c_sources_notices_are_those_license_rst_does_not_reproduce():
    lic = _licenses()
    pysqlite = ("/* {name} - {what}\n *\n * Copyright (C) 2005-2010 Gerhard Haring <gh@ghaering.de>\n *\n"
                " * This software is provided 'as-is', without any express or implied\n * warranty.\n */\n\n"
                "#include \"module.h\"\n")
    sources = {
        # each of its copyright lines in license.rst (a line naming "the above copyright notice" is not one)
        "Modules/_randommodule.c": ("/* ------\n   Copyright (C) 1997 - 2002, Makoto Matsumoto and  Takuji Nishimura,\n"
                                    "   All rights reserved.\n\n   Redistribution and use in source and binary forms,\n"
                                    "   provided that the above copyright notice is kept. */\n"),
        # licensed to the PSF: under Python's own license
        "Modules/_hashopenssl.c": ("/*\n * Copyright (C) 2005-2010   Gregory P. Smith (greg@krypto.org)\n"
                                   " * Licensed to PSF under a Contributor Agreement.\n */\n"),
        # a copyright and no terms
        "Modules/_codecsmodule.c": ("/* Written by Marc-Andre Lemburg.\n\n   Copyright (c) Corporation for National "
                                    "Research Initiatives.\n\n   ------------------------------------------------ */\n"),
        # terms and no copyright
        "Python/dup2.c": "/*\n * Public domain dup2() lookalike\n * Permission is not needed.\n */\n",
        # a comment after code on its line
        "Python/x.c": "int x; /* Copyright (c) 2001 Someone. Permission to use it. */\n",
        # each taken from its first copyright line, without the rules around it; pysqlite's the same from there
        "Python/getopt.c": ("/*-----------------------------------------------------------*\n * <RCS keywords>\n *\n"
                            " * C++ Library\n *\n * Copyright 1992-1994, David Gottner\n *\n"
                            " *                    All Rights Reserved\n *\n"
                            " * Permission to use, copy, modify, and distribute this software.\n"
                            " *-----------------------------------------------------------*/\n\n"
                            "/* Modified to support --help and --version, by Georg Brandl. */\n"),
        "Modules/_sqlite/row.c": pysqlite.format(name="row.c", what="an enhanced tuple for database rows"),
        "Modules/_sqlite/util.c": pysqlite.format(name="util.c", what="various utility functions"),
        # one of its copyright lines in license.rst, and the other not
        "Objects/unicodeobject.c": ("/*\n\nUnicode implementation based on original code by Fredrik Lundh.\n\n"
                                    "Copyright (C) 1997 - 2002, Makoto Matsumoto and Takuji Nishimura,\n"
                                    "Copyright (c) 1999 by Secret Labs AB\n\nPermission to use, copy.\n\n*/\n"),
        # after code, and its terms said only by "redistribution"; by "licensed" only
        "Modules/_sre/sre.h": ("#ifndef SRE_INCLUDED\n#define SRE_INCLUDED\n\n/*\n * Secret Labs' Regular Expression "
                               "Engine\n *\n * Copyright (c) 1997-2001 by Secret Labs AB.  All rights reserved.\n *\n"
                               " * See the sre.c file for information on usage and redistribution.\n */\n"),
        "Parser/x.c": "/* Copyright (c) 2020 A Parser Maker.\n   Licensed under the Apache 2.0 License. */\n",
    }
    head, *parts = lic.c_notices(sources, RST, "3.13.15").split("\n\nIn ")
    assert head.startswith("The notices in CPython 3.13.15's C source, ") and all(len(l) <= 116 for l in head.split("\n"))
    assert parts == [
        "Modules/_sqlite/row.c, Modules/_sqlite/util.c:\n\nCopyright (C) 2005-2010 Gerhard Haring <gh@ghaering.de>\n\n"
        "This software is provided 'as-is', without any express or implied\nwarranty.",
        "Modules/_sre/sre.h:\n\nCopyright (c) 1997-2001 by Secret Labs AB.  All rights reserved.\n\n"
        "See the sre.c file for information on usage and redistribution.",
        "Objects/unicodeobject.c:\n\nCopyright (C) 1997 - 2002, Makoto Matsumoto and Takuji Nishimura,\n"
        "Copyright (c) 1999 by Secret Labs AB\n\nPermission to use, copy.",
        "Parser/x.c:\n\nCopyright (c) 2020 A Parser Maker.\nLicensed under the Apache 2.0 License.",
        "Python/getopt.c:\n\nCopyright 1992-1994, David Gottner\n\nAll Rights Reserved\n\n"
        "Permission to use, copy, modify, and distribute this software.\n",
    ]
    # none
    assert "\nIn " not in lic.c_notices({"Python/dup2.c": sources["Python/dup2.c"]}, RST, "3.13.15")


def test_cpythons_own_texts_come_from_its_source(tmp_path):
    lic = _licenses()
    looked_in = {"Modules/syslogmodule.c": SYSLOG}
    for where in ("Objects/x.c", "Python/x.c", "Parser/x.c", "Include/x.h"):
        looked_in[where] = f"/* Copyright (c) 2001 The Maker of {where}.\n   Permission granted. */\n"
    files = dict(SOURCE, **looked_in, **{
        "Modules/_hacl/python_hacl_namespaces.h": "#ifndef X\n/*\n * C's excuse for namespaces\n */\n",
        "Modules/_hacl/README.md": "/* MIT License\n * of what is not C */\n",
        # the C source is looked in only where CPYTHON_C says, and not in the trees CPYTHON_C_SKIP names
        "Modules/expat/xmlparse.c": "/* Copyright (c) 1997 James Clark\n   Permission is hereby granted */\n",
        "Modules/_decimal/libmpdec/io.c": "/* Copyright (c) 2008 Stefan Krah\n   Redistribution and use */\n",
        "Objects/mimalloc/os.c": "/* Copyright (c) 2018 Microsoft Research\n   Licensed under the MIT License */\n",
        "Include/internal/mimalloc/mimalloc.h": "/* Copyright (c) 2018 Microsoft Research\n   License: MIT */\n",
        "Tools/x.c": "/* Copyright (c) 2001 A Tool Maker\n   Permission granted */\n",
        "Lib/x.py": "# Copyright (c) 2001 A Python Maker, permission granted\n",
        "Doc/x.c": "/* Copyright (c) 2001 A Doc Maker\n   Permission granted */\n",
        "Modules/README": "/* Copyright (c) 2001 A Readme Maker\n   Permission granted */\n",
    })
    texts = lic.cpython_texts(_source(tmp_path / "Python-3.13.15.tar.xz", "3.13.15", files), "3.13.15")
    assert list(texts) == ["license.rst", "NOTICE.cpython-c.txt", "LICENSE.hacl.txt", "NOTICE.krml.txt"]
    assert texts["license.rst"] == RST.encode()
    assert texts["NOTICE.cpython-c.txt"] == lic.c_notices(looked_in, RST, "3.13.15").encode()
    assert texts["NOTICE.cpython-c.txt"].decode().count("\nIn ") == 5
    assert texts["LICENSE.hacl.txt"] == (
        b"MIT License\n\nCopyright (c) 2016-2022 INRIA, CMU and Microsoft Corporation\nCopyright (c) 2022-2023 HACL* "
        b"Contributors\n\nPermission is hereby granted, free of charge, to any person obtaining a copy\nof this "
        b"software and associated documentation files.\n")
    assert texts["NOTICE.krml.txt"] == (b"Copyright (c) INRIA and Microsoft Corporation. All rights reserved.\n"
                                        b"Licensed under the Apache 2.0 License.\n")
    for change, said in (
            ({"Doc/license.rst": None}, "holds no Python-3.13.15/Doc/license.rst"),
            ({"Modules/_hacl/Hacl_Hash_MD5.c": HACL_MIT.replace("2022-2023", "2022-2024")},
             "do not say one MIT License and one KaRaMeL notice"),
            ({"Modules/_hacl/include/krml/internal/target.h": None,
              "Modules/_hacl/include/krml/FStar_UInt128_Verified.h": None},
             "do not say one MIT License and one KaRaMeL notice")):
        changed = {rel: text for rel, text in dict(SOURCE, **change).items() if text is not None}
        path = _source(tmp_path / "changed.tar.xz", "3.13.15", changed)
        with pytest.raises(SystemExit) as refused:
            lic.cpython_texts(path, "3.13.15")
        assert said in str(refused.value)
    # another version's source, asked for as that version or not
    texts = lic.cpython_texts(_source(tmp_path / "other.tar.xz", "3.14.0", SOURCE), "3.14.0")
    assert texts["license.rst"] == RST.encode() and texts["NOTICE.cpython-c.txt"].startswith(b"The notices in CPython "
                                                                                              b"3.14.0's C source")
    with pytest.raises(SystemExit) as refused:
        lic.cpython_texts(str(tmp_path / "other.tar.xz"), "3.13.15")
    assert "holds no Python-3.13.15/Doc/license.rst" in str(refused.value)


@needs_zstd
def test_python_sources_writes_the_record_from_the_pinned_releases_archives(tmp_path, monkeypatch):
    lic = _licenses()
    sys.path.insert(0, DESKTOP)
    import bundle
    sums, archives = [], {}
    for triple, _digest in bundle.BUILDS.values():
        name = f"cpython-{bundle.PYTHON}+{bundle.RELEASE}-{triple}-pgo+lto-full.tar.zst"
        members = {"python/PYTHON.json": json.dumps(_pbs(python=bundle.PYTHON)).encode(),
                   "python/build/lib/libcrypto.a": b"OpenSSL 3.5.9 29 Sep 2026"}
        members.update({f"python/licenses/{k}": v for k, v in HELD.items()})
        archives[name] = _archive(tmp_path / name, members)
        with open(archives[name], "rb") as fh:
            sums.append(f"{_sha(fh.read())} *{name}")           # sha256sum's binary form, as some lines are
    sums.append("0" * 64 + "  another-file.tar.gz")
    source = f"Python-{bundle.PYTHON}.tar.xz"
    archives[source] = _source(tmp_path / source, bundle.PYTHON, SOURCE)

    class Answer(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    asked, fetched = [], []
    monkeypatch.setattr(lic.urllib.request, "urlopen",
                        lambda url, timeout: asked.append(url) or Answer("\n".join(sums).encode()))
    monkeypatch.setattr(bundle, "download", lambda url, name, digest: fetched.append((url, name, digest))
                        or archives[name])
    folder = tmp_path / "out"
    folder.mkdir()
    (folder / "LICENSE.gone.txt").write_text("from an earlier release")
    assert lic.python_sources(str(folder)) == str(folder)
    base = f"https://github.com/astral-sh/python-build-standalone/releases/download/{bundle.RELEASE}"
    assert asked == [f"{base}/SHA256SUMS"]
    assert sorted(f[1] for f in fetched) == sorted(archives)
    for url, name, digest in fetched:
        if name == source:          # CPython's own source, from python.org, checked against the digest pinned
            assert url == f"https://www.python.org/ftp/python/{bundle.PYTHON}/{source}"
            assert digest == bundle.CPYTHON_SOURCE
            continue
        with open(archives[name], "rb") as fh:
            assert url == f"{base}/{name.replace('+', '%2B')}" and digest == _sha(fh.read())
    with open(folder / "sources.json", encoding="utf-8") as fh:
        record = json.load(fh)
    assert set(record["builds"]) == {t for t, _d in bundle.BUILDS.values()}
    assert record["cpython"]["source"] == {"archive": source, "sha256": bundle.CPYTHON_SOURCE,
                                           "url": f"https://www.python.org/ftp/python/{bundle.PYTHON}/{source}"}
    assert sorted(os.listdir(folder)) == ["LICENSE.bzip2.txt", "LICENSE.hacl.txt", "LICENSE.openssl-3.txt",
                                          "NOTICE.cpython-c.txt", "NOTICE.krml.txt", "license.rst", "sources.json"]
    with open(folder / "license.rst", encoding="utf-8") as fh:
        assert fh.read() == RST


def test_python_sources_refuses_a_release_without_the_archive(monkeypatch, tmp_path):
    lic = _licenses()

    class Answer(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(lic.urllib.request, "urlopen", lambda url, timeout: Answer(b"0  other.tar.zst\n"))
    with pytest.raises(SystemExit) as refused:
        lic.python_sources(str(tmp_path / "out"))
    assert "the release's SHA256SUMS has no cpython-" in str(refused.value)
    assert not (tmp_path / "out").exists()


# --- the Rust crates ---

CRATES_IO = "registry+https://github.com/rust-lang/crates.io-index"


def _crate(base, name, version, license, files=None, authors=(), repository=None, license_file=None, kind="lib",
           source=CRATES_IO, commit=None):
    folder = base / f"{name}-{version}"
    folder.mkdir(parents=True)
    (folder / "Cargo.toml").write_text("")
    for rel, text in (files or {}).items():
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text(text)
    if commit:          # as cargo package writes it
        (folder / ".cargo_vcs_info.json").write_text(json.dumps({"git": {"sha1": commit}, "path_in_vcs": name}))
    return {"id": f"{source}#{name}@{version}", "name": name, "version": version, "license": license,
            "license_file": license_file, "authors": list(authors), "repository": repository,
            "manifest_path": str(folder / "Cargo.toml"), "targets": [{"kind": [kind]}], "source": source}


def _kept(folder, kept):
    """A desktop/third-party/rust/ folder as rust-sources writes it: `kept` is {(repo, commit): {name: text}}."""
    record = {"repositories": {}}
    for (repo, commit), files in kept.items():
        where = folder.joinpath(*repo.split("/"), commit)
        where.mkdir(parents=True)
        for name, text in files.items():
            (where / name).write_text(text)
        record["repositories"].setdefault(repo, {})[commit] = {
            "crates": ["?"], "files": {name: _sha(text) for name, text in files.items()}}
    (folder / "sources.json").write_text(json.dumps(record))
    return folder


def _graph(tmp_path):
    """cargo metadata's answer for an app with every kind of crate, cargo tree's for two targets, and the app's
    folder. metadata names crates that cargo tree, as cargo builds the app, does not: a proc-macro, what only it
    uses, and build and dev dependencies."""
    base = tmp_path / "crates"
    app = tmp_path / "app"
    app.mkdir()
    root = {"id": "path+file:///app#app@0.1.0", "name": "app", "version": "0.1.0", "license": "Apache-2.0",
            "manifest_path": str(app / "Cargo.toml"), "targets": [{"kind": ["bin"]}], "source": None}
    c = {
        "a": _crate(base, "a", "1.0.0", "MIT OR Apache-2.0", {"LICENSE-MIT": "same text", "LICENSE-APACHE": "a's"}),
        "both": _crate(base, "both", "1.0.0", "MIT", {"LICENSE": "same text"}),
        "macro": _crate(base, "macro", "1.0.0", "MIT", {"LICENSE": "m"}, kind="proc-macro"),
        "under": _crate(base, "under", "1.0.0", "MIT", {"LICENSE": "u"}),
        "builder": _crate(base, "builder", "1.0.0", "MIT", {"LICENSE": "b"}),
        "tester": _crate(base, "tester", "1.0.0", "MIT", {"LICENSE": "t"}),
        "f": _crate(base, "f", "0.3.2", "Zlib OR Apache-2.0 OR MIT"),
        "g": _crate(base, "g", "0.6.4", "MIT", authors=["Gee <g@example.org>"], repository="https://github.com/gee/g",
                    commit="a" * 40),
        "h": _crate(base, "h", "0.3.2", "MIT", repository="https://example.org/h"),
        "h2": _crate(base, "h2", "0.1.0", "MIT/Apache-2.0", authors=[" "]),
        "i": _crate(base, "i", "0.3.0", "BSD-3-Clause", repository="https://example.org/pair"),
        "j": _crate(base, "j", "3.0.0", "BSD-3-Clause", {"LICENSE": "pair's license"},
                    repository="https://Example.org/pair.git/"),
        "k": _crate(base, "k", "0.37.0", "MPL-2.0", {"LICENSE": "k's MPL"}),
        "l": _crate(base, "l", "1.0.0", "GPL-3.0-only", {"COPYING": "gpl"}),
        "m": _crate(base, "m", "1.0.0", None, {"LICENSES/custom.txt": "custom"}, license_file="LICENSES/custom.txt"),
        "n": _crate(base, "n", "1.0.0", "ISC"),
        "o": _crate(base, "o", "0.38.0", "MPL-2.0", repository="https://example.org/o"),
        "p": _crate(base, "p", "1.0.0", "MIT", authors=["Pee"], license_file="../outside.txt"),
        "q": _crate(base, "q", "1.0.0", "ISC", repository="https://example.org/pair"),
        "r": _crate(base, "r", "1.0.0", "MPL-2.0 OR Apache-2.0"),
        "s": _crate(base, "s", "1.0.0", "MIT", authors=[""], repository="https://example.org/s"),
        "t": _crate(base, "t", "1.0.0", "MIT"),
        "u": _crate(base, "u", "1.0.0", "MPL-2.0", {"LICENSE": "u's MPL"},
                    source="git+https://example.org/u.git?rev=1#0123456789abcdef"),
        # on GitHub, each with the commit it was published from: its repository's license files kept for it or not
        "v": _crate(base, "v", "1.0.0", "MIT", repository="https://github.com/vee/v", commit="d" * 40),
        "w": _crate(base, "w", "1.0.0", "BSD-3-Clause", repository="https://github.com/dub/w/", commit="c" * 40),
        "x86only": _crate(base, "x86only", "1.0.0", "MIT", {"LICENSE": "x86"}),
        "y": _crate(base, "y", "1.0.0", "MIT", repository="https://github.com/why/y.git", commit="b" * 40),
        "z": _crate(base, "z", "1.0.0", "MIT", repository="https://github.com/zed/z", commit="e" * 40),
    }
    (base / "outside.txt").write_text("outside")              # p's license-file, outside p
    meta = {"packages": [root, *c.values()], "resolve": {"root": root["id"], "nodes": []}}
    linked = ["a v1.0.0", "f v0.3.2", "both v1.0.0", "a v1.0.0 (*)", "g v0.6.4", "h v0.3.2", "h2 v0.1.0", "i v0.3.0",
              "j v3.0.0", "k v0.37.0", "l v1.0.0", "m v1.0.0", "n v1.0.0", "o v0.38.0", "p v1.0.0", "q v1.0.0",
              "r v1.0.0", "s v1.0.0", "t v1.0.0", "u v1.0.0 (https://example.org/u.git?rev=1#01234567)", "v v1.0.0",
              "w v1.0.0", "y v1.0.0", "z v1.0.0"]
    trees = {TRIPLE: "\n".join(["app v0.1.0 (/app)", *linked]) + "\n",
             "x86_64-apple-darwin": "\n".join(["app v0.1.0 (/app)", *linked, "x86only v1.0.0"]) + "\n"}
    return meta, app, trees


def _cargo(meta, trees, asked):
    """A stand-in for cargo: its metadata and its tree for a target, as _graph gives them."""
    def run(args, **kw):
        asked.append((args, kw.get("cwd")))
        if args[1] == "metadata":
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(meta))
        return subprocess.CompletedProcess(args, 0, stdout=trees[args[args.index("--target") + 1]])
    return run


def _tree_args(target, offline=False):
    return ["cargo", "tree", "--locked", *(["--offline"] if offline else []), "-e", "normal,no-proc-macro",
            "--target", target, "--prefix", "none", "--format", "{p}"]


def test_the_crates_named_are_those_cargo_builds_into_the_app_for_its_targets(tmp_path):
    lic = _licenses()
    meta, app, trees = _graph(tmp_path)
    asked = []
    crates, problems = lic.linked(meta, str(app), [TRIPLE, "x86_64-apple-darwin"], run=_cargo(meta, trees, asked))
    assert problems == []
    assert sorted(p["name"] for p in crates) == ["a", "both", "f", "g", "h", "h2", "i", "j", "k", "l", "m", "n", "o",
                                                 "p", "q", "r", "s", "t", "u", "v", "w", "x86only", "y", "z"]
    assert asked == [(_tree_args(TRIPLE), str(app)), (_tree_args("x86_64-apple-darwin"), str(app))]
    trees[TRIPLE] += "ghost v9.9.9\nnot a line\n\n"
    twice = dict(meta, packages=[*meta["packages"], dict(meta["packages"][1], id="another#a@1.0.0")])
    crates, problems = lic.linked(twice, str(app), [TRIPLE], offline=True, run=_cargo(twice, trees, asked))
    assert asked[-1][0] == _tree_args(TRIPLE, offline=True)
    assert problems == ["cargo tree names 'a v1.0.0', which cargo metadata does not name once",
                        "cargo tree names 'a v1.0.0', which cargo metadata does not name once",
                        "cargo tree names 'ghost v9.9.9', which cargo metadata does not name once",
                        "cargo tree names 'not a line', which cargo metadata does not name once"]
    assert "a" not in [p["name"] for p in crates]


def test_a_crates_license_files_are_found_wherever_it_keeps_them(tmp_path):
    lic = _licenses()
    crate = _crate(tmp_path, "c", "1.0.0", "MIT", {
        "LICENSE": "1", "LICENSE-MIT.md": "2", "COPYING": "3", "NOTICE": "4", "README.md": "no", "UNLICENSE": "no",
        "docs/terms.txt": "5", "src/LICENSE": "6", "src/copying.rs": "code, not a license",
        "src/unicode_tables/LICENSE-UNICODE": "7", "src/notice.json": "data"}, license_file="docs/terms.txt")
    (tmp_path / "c-1.0.0" / "LICENSE.d").mkdir()
    os.symlink(os.path.join(ROOT, "LICENSE"), tmp_path / "c-1.0.0" / "LICENSE-LINK")
    assert lic.crate_files(crate) == [("COPYING", "3"), ("LICENSE", "1"), ("LICENSE-MIT.md", "2"), ("NOTICE", "4"),
                                      ("docs/terms.txt", "5"), ("src/LICENSE", "6"),
                                      ("src/unicode_tables/LICENSE-UNICODE", "7")]
    crate["license_file"] = "LICENSE"                                   # named and found: once
    assert [name for name, _t in lic.crate_files(crate)].count("LICENSE") == 1


def test_the_rust_notice_names_each_crate_with_its_license_files_or_a_text_supplied_for_it(tmp_path):
    lic = _licenses()
    meta, app, trees = _graph(tmp_path)
    asked = []
    run = _cargo(meta, trees, asked)
    kept = _kept(tmp_path / "kept", {
        ("gee/g", "a" * 40): {"LICENSE.md": "The crate g is licensed under the MIT license."},
        ("why/y", "b" * 40): {"LICENSE": "MIT License\n\nCopyright (c) Why\n\nPermission is hereby\ngranted, free of "
                                         "charge, to any person"},
        ("dub/w", "c" * 40): {"LICENSE-BSD": "Copyright (c) Dub. Redistribution and use are permitted."},
        ("zed/z", "e" * 40): {"LICENSE": "z's"},
        ("vee/v", "f" * 40): {"LICENSE": "v's, at another commit than the one it was published from"},
    })
    (kept / "zed" / "z" / ("e" * 40) / "LICENSE").write_text("changed by hand")
    text, problems = lic.rust_notice(str(app), [TRIPLE, "x86_64-apple-darwin"], run=run, kept=str(kept))
    assert asked == [(["cargo", "metadata", "--format-version", "1", "--locked", "--filter-platform", TRIPLE,
                       "--filter-platform", "x86_64-apple-darwin"], str(app)),
                     (_tree_args(TRIPLE), str(app)), (_tree_args("x86_64-apple-darwin"), str(app))]
    # an MIT, BSD or ISC crate with no file of its own and none of its repository's kept is never given a holder
    none = "it ships no license file, and none can be supplied for it"
    assert problems == [f"h 0.3.2: {none}",
                        "l 1.0.0: its license, GPL-3.0-only, is not one the app takes",
                        "m 1.0.0: its Cargo.toml names no license, only the file LICENSES/custom.txt",
                        f"n 1.0.0: {none}", f"p 1.0.0: {none}", f"q 1.0.0: {none}", f"s 1.0.0: {none}",
                        f"t 1.0.0: {none}",
                        "u 1.0.0: its license, MPL-2.0, asks that the app say where its source is, and it is not from "
                        "crates.io",
                        f"v 1.0.0: {none} (python3 desktop/licenses.py rust-sources keeps its repository's, at the "
                        "commit it was published from)",
                        f"z 1.0.0: desktop/third-party/rust/zed/z/{'e' * 40}/LICENSE is not the file rust-sources "
                        "wrote",
                        f"z 1.0.0: {none}"]
    entries = text.split(lic.RULE + "\n")[1:]          # each entry: its title, then what follows it
    by = {entries[i].split(" (")[0]: entries[i] + entries[i + 1] for i in range(0, len(entries), 2)}
    assert [b for b in by] == ["a 1.0.0", "both 1.0.0", "f 0.3.2", "g 0.6.4", "h 0.3.2", "h2 0.1.0", "i 0.3.0",
                               "j 3.0.0", "k 0.37.0", "l 1.0.0", "m 1.0.0", "n 1.0.0", "o 0.38.0", "p 1.0.0",
                               "q 1.0.0", "r 1.0.0", "s 1.0.0", "t 1.0.0", "u 1.0.0", "v 1.0.0", "w 1.0.0",
                               "x86only 1.0.0", "y 1.0.0", "z 1.0.0"]
    flat = {b: " ".join(e.split()) for b, e in by.items()}     # the notes are wrapped
    for b, e in by.items():
        assert all(len(line) <= 116 for line in e.split("\n--- ")[0].splitlines()[2:]), b
    assert "Its source" not in by["a 1.0.0"] and "Its source" not in by["r 1.0.0"]   # each has a license it takes
    assert "Its source" not in by["u 1.0.0"]                                         # not on crates.io
    assert "(The same text as Apache-2.0 of f 0.3.2, above.)" in by["r 1.0.0"]      # Apache's, not the MPL's
    for b in ("h 0.3.2", "n 1.0.0", "p 1.0.0", "q 1.0.0", "s 1.0.0", "t 1.0.0", "v 1.0.0", "z 1.0.0"):
        assert "\n--- " not in by[b] and "Copyright" not in by[b], b              # no file, no holder made up
    # its repository's license files at its commit, and the MIT License's text when they name it and do not hold it
    kept_note = ("(It ships no license file. Its Cargo.toml names MIT; below are the license files at the top of its "
                 "repository, https://github.com/gee/g, at the commit it was published from, " + "a" * 40 + " (its "
                 ".cargo_vcs_info.json), as desktop/third-party/rust/ keeps them, then the MIT License's text, which "
                 "they name and do not hold. Neither the crate nor its repository names a copyright holder, and none "
                 "is added.)")
    assert kept_note in flat["g 0.6.4"] and "Gee" not in by["g 0.6.4"]
    assert by["g 0.6.4"].index("--- LICENSE.md\n\nThe crate g is licensed under the MIT license.\n") \
        < by["g 0.6.4"].index("--- MIT\n\n" + lic.MIT_TEXT)
    assert flat["y 1.0.0"].endswith("as desktop/third-party/rust/ keeps them.) --- LICENSE MIT License Copyright (c) Why "
                                    "Permission is hereby granted, free of charge, to any person")    # it holds it
    assert "https://github.com/why/y.git, at the commit it was published from, " + "b" * 40 in flat["y 1.0.0"]
    assert "--- LICENSE-BSD\n\nCopyright (c) Dub." in by["w 1.0.0"] and "--- MIT" not in by["w 1.0.0"]
    with open(lic.APACHE_TEXT, encoding="utf-8") as fh:
        apache = fh.read().rstrip()
    with open(lic.MPL_TEXT, encoding="utf-8") as fh:
        mpl = fh.read().rstrip()
    assert "--- LICENSE\n\n(The same text as LICENSE-MIT of a 1.0.0, above.)" in by["both 1.0.0"]
    assert ("(It ships no license file. Its Cargo.toml names Zlib OR Apache-2.0 OR MIT; below is the text of "
            "Apache-2.0, which it offers and which names no holder, as LICENSE-APACHE-2.0 beside Proofpane "
            "Community's LICENSE holds it.)") in flat["f 0.3.2"] and apache in by["f 0.3.2"]
    assert "(The same text as Apache-2.0 of f 0.3.2, above.)" in by["h2 0.1.0"]   # the old a/b form, and no author
    assert ("(It ships no license file. j 3.0.0, published from the same repository (https://example.org/pair) "
            "under the same license, ships the files below.)") in flat["i 0.3.0"]
    assert "--- LICENSE\n\npair's license" in by["i 0.3.0"]
    assert "--- LICENSE\n\n(The same text as LICENSE of i 0.3.0, above.)" in by["j 3.0.0"]
    assert "Its source: https://crates.io/crates/k/0.37.0" in by["k 0.37.0"] and "k's MPL" in by["k 0.37.0"]
    o = flat["o 0.38.0"]
    assert o.index("as cssparser 0.37.0 ships it.)") < o.index("Its source: https://crates.io/crates/o/0.38.0")
    assert mpl in by["o 0.38.0"]
    assert "--- LICENSES/custom.txt\n\ncustom" in by["m 1.0.0"]
    assert "outside" not in by["p 1.0.0"] and "Pee" not in by["p 1.0.0"]
    assert "pair's license" not in by["q 1.0.0"]                          # same repository, another license
    assert not {"macro 1.0.0", "under 1.0.0", "builder 1.0.0", "tester 1.0.0"} & set(by)   # not linked
    words = " ".join(text.split())
    assert "(app 0.1.0)" in words and f"for {TRIPLE}, x86_64-apple-darwin, as cargo's resolver builds it" in words
    lic.rust_notice(str(app), [TRIPLE], offline=True, run=run)
    assert asked[-2:] == [(["cargo", "metadata", "--format-version", "1", "--locked", "--offline", "--filter-platform",
                            TRIPLE], str(app)), (_tree_args(TRIPLE, offline=True), str(app))]


def _cargo_works(src):
    try:
        subprocess.run(["cargo", "metadata", "--format-version", "1", "--locked", "--offline"], cwd=src,
                       capture_output=True, check=True, timeout=120)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.mark.parametrize("app, target", [("desktop", "aarch64-apple-darwin"), ("desktop", "x86_64-apple-darwin"),
                                         ("phone", "aarch64-apple-ios")])
def test_every_crate_the_apps_link_has_a_license_they_take_and_a_text(app, target):
    lic = _licenses()
    src = os.path.join(ROOT, app, "src-tauri")
    if not _cargo_works(src):
        pytest.skip("the crates are not here (cargo fetch --locked in the app's src-tauri brings them)")
    text, problems = lic.rust_notice(src, [target], offline=True)
    assert problems == []
    blocks = text.split(lic.RULE + "\n")[1:]
    names = {blocks[i].split(" ")[0] for i in range(0, len(blocks), 2)}
    assert len(names) > 100 and {"tauri", "serde", "option-ext"} <= names
    # what only the compiler runs, or only build scripts and proc-macros use, is not linked
    assert not names & {"proc-macro2", "quote", "syn", "tauri-build", "tauri-macros", "cssparser", "selectors",
                        "html5ever"}
    for i in range(0, len(blocks), 2):
        if blocks[i].rstrip("\n").endswith("(MPL-2.0)"):
            name, version = blocks[i].split(" (")[0].split(" ")
            assert f"Its source: https://crates.io/crates/{name}/{version}" in blocks[i + 1]


@pytest.mark.parametrize("address, where", [
    ("https://github.com/madsmtm/objc2", ("madsmtm", "objc2")), ("https://github.com/a-b/c.d_e.git/", ("a-b", "c.d_e")),
    ("  https://github.com/a/b  ", ("a", "b")), ("https://github.com/a/b.git", ("a", "b")),
    ("https://github.com/a/b/tree/main", None), ("http://github.com/a/b", None), ("https://gitlab.com/a/b", None),
    ("https://github.com/a", None), ("https://github.com/-a/b", None), ("https://github.com/a/..", None),
    ("https://github.com/a/.", None), ("", None), (None, None),
])
def test_a_repository_on_github_is_named_by_its_address(address, where):
    assert _licenses().github(address) == where


def test_a_crates_commit_is_the_one_its_cargo_vcs_info_names(tmp_path):
    lic = _licenses()
    crate = _crate(tmp_path, "c", "1.0.0", "MIT", commit="a" * 40)
    assert lic.commit_of(crate) == "a" * 40
    info = tmp_path / "c-1.0.0" / ".cargo_vcs_info.json"
    for text in ("not json", json.dumps({"git": {"sha1": "A" * 40}}), json.dumps({"git": {"sha1": "a" * 39}}),
                 json.dumps({"git": {"sha1": 1}}), json.dumps({"git": "x"}), json.dumps(["x"]), json.dumps({})):
        info.write_text(text)
        assert lic.commit_of(crate) is None, text
    os.unlink(info)
    assert lic.commit_of(crate) is None


def test_the_kept_files_are_read_only_as_rust_sources_wrote_them(tmp_path):
    lic = _licenses()
    folder = _kept(tmp_path / "kept", {("o/r", "a" * 40): {"LICENSE.md": "md", "LICENSE-MIT": "mit"}})
    url = "https://github.com/o/r"
    assert lic.kept_files(url, "a" * 40, str(folder)) == ([("LICENSE-MIT", "mit"), ("LICENSE.md", "md")], [])
    for address, commit in ((url, "b" * 40), ("https://github.com/o/other", "a" * 40), ("https://example.org/o/r",
                                                                                         "a" * 40), (url, None)):
        assert lic.kept_files(address, commit, str(folder)) == (None, [])
    assert lic.kept_files(url, "a" * 40, str(tmp_path / "none")) == (None, [])     # nothing kept at all
    (folder / "o" / "r" / ("a" * 40) / "LICENSE.md").write_text("changed")
    record = json.loads((folder / "sources.json").read_text())
    record["repositories"]["o/r"]["a" * 40]["files"]["../LICENSE"] = _sha("x")    # a name it never writes
    (folder / "sources.json").write_text(json.dumps(record))
    (folder / "o" / "r" / "LICENSE").write_text("x")                             # where that name leads
    files, problems = lic.kept_files(url, "a" * 40, str(folder))
    assert files == [("LICENSE-MIT", "mit")]
    assert problems == [f"desktop/third-party/rust/o/r/{'a' * 40}/../LICENSE is not the file rust-sources wrote",
                        f"desktop/third-party/rust/o/r/{'a' * 40}/LICENSE.md is not the file rust-sources wrote"]


def test_a_files_git_id_is_gits():
    lic = _licenses()
    assert lic.git_blob(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"     # git hash-object
    assert lic.git_blob(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_a_repositorys_license_files_are_read_with_git_at_the_commit_asked_for(tmp_path):
    lic = _licenses()
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.org", *args],
                              check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    for name, text in (("LICENSE.md", "first"), ("LICENSE-MIT.txt", "mit"), ("README.md", "not a license"),
                       ("COPYING", "run me")):
        (repo / name).write_text(text)
    (repo / "COPYING").chmod(0o755)
    (repo / "sub").mkdir()
    (repo / "sub" / "LICENSE").write_text("below the top")
    os.symlink("LICENSE.md", repo / "LICENSE-LINK")
    git("add", "-A")
    git("commit", "-q", "-m", "first")
    first = git("rev-parse", "HEAD")
    (repo / "LICENSE.md").write_text("second")
    git("commit", "-q", "-am", "second")
    git("config", "uploadpack.allowFilter", "true")
    git("config", "uploadpack.allowAnySHA1InWant", "true")
    found = lic.git_files("o/r", first, url=f"file://{repo}")
    assert found == {"COPYING": (lic.git_blob(b"run me"), b"run me"),
                     "LICENSE-MIT.txt": (lic.git_blob(b"mit"), b"mit"),
                     "LICENSE.md": (lic.git_blob(b"first"), b"first")}
    with pytest.raises(subprocess.CalledProcessError):
        lic.git_files("o/r", "0" * 40, url=f"file://{repo}")        # a commit it does not have


def _small_app(tmp_path):
    """An app whose crates need, or do not need, their repositories' license files, and a stand-in for cargo."""
    base = tmp_path / "crates"
    app = tmp_path / "app"
    app.mkdir()
    root = {"id": "path+file:///app#app@0.1.0", "name": "app", "version": "0.1.0", "license": "Apache-2.0",
            "manifest_path": str(app / "Cargo.toml"), "targets": [{"kind": ["bin"]}], "source": None}
    crates = [
        _crate(base, "mitless", "1.0.0", "MIT", repository="https://github.com/own/mitless", commit="1" * 40),
        _crate(base, "twice", "2.0.0", "MIT", repository="https://github.com/own/mitless/", commit="1" * 40),
        _crate(base, "bsdless", "1.0.0", "BSD-3-Clause", repository="https://github.com/own/bsd.git",
               commit="2" * 40),
        _crate(base, "offers", "1.0.0", "MIT OR Apache-2.0", repository="https://github.com/own/o", commit="3" * 40),
        _crate(base, "pair-a", "1.0.0", "MIT", {"LICENSE": "pair's"}, repository="https://github.com/own/pair"),
        _crate(base, "pair-b", "1.0.0", "MIT", repository="https://github.com/own/pair", commit="4" * 40),
        _crate(base, "hasfile", "1.0.0", "MIT", {"LICENSE": "its own"}, repository="https://github.com/own/h",
               commit="5" * 40),
    ]
    meta = {"packages": [root, *crates], "resolve": {"root": root["id"], "nodes": []}}
    tree = "\n".join(["app v0.1.0 (/app)", *(f"{c['name']} v{c['version']}" for c in crates)]) + "\n"
    return meta, app, base, _cargo(meta, {TRIPLE: tree}, [])


def test_the_notice_says_how_a_crates_repository_files_can_be_kept(tmp_path):
    lic = _licenses()
    meta, app, base, run = _small_app(tmp_path)
    none = "it ships no license file, and none can be supplied for it"
    hint = " (python3 desktop/licenses.py rust-sources keeps its repository's, at the commit it was published from)"
    _text, problems = lic.rust_notice(str(app), [TRIPLE], run=run, kept=str(tmp_path / "nothing kept"))
    assert problems == [f"bsdless 1.0.0: {none}{hint}", f"mitless 1.0.0: {none}{hint}", f"twice 2.0.0: {none}{hint}"]
    # not for one that names no commit, or no repository on GitHub, to take them at
    os.unlink(base / "bsdless-1.0.0" / ".cargo_vcs_info.json")
    next(p for p in meta["packages"] if p["name"] == "mitless")["repository"] = "https://example.org/own/mitless"
    _text, problems = lic.rust_notice(str(app), [TRIPLE], run=run, kept=str(tmp_path / "nothing kept"))
    assert problems == [f"bsdless 1.0.0: {none}", f"mitless 1.0.0: {none}", f"twice 2.0.0: {none}{hint}"]


def test_rust_sources_keeps_the_repository_files_of_the_crates_that_need_them(tmp_path):
    lic = _licenses()
    meta, app, _base, run = _small_app(tmp_path)
    cargo = []

    def recorded(args, **kw):
        cargo.append(args)
        return run(args, **kw)
    stored = {("own/mitless", "1" * 40): {"LICENSE": b"mitless's", "COPYING": b"its copying"},
              ("own/bsd", "2" * 40): {"LICENSE.md": b"bsd's"}}
    asked = []

    def files_at(repo, commit):
        asked.append((repo, commit))
        return {name: (lic.git_blob(data), data) for name, data in stored[(repo, commit)].items()}

    folder = tmp_path / "rust"
    folder.mkdir()
    (folder / "stale.txt").write_text("from an earlier run")
    assert lic.rust_sources(str(folder), apps=((str(app), (TRIPLE,)),), files_at=files_at, run=recorded) == str(folder)
    assert asked == [("own/bsd", "2" * 40), ("own/mitless", "1" * 40)]   # once each: not for what has another text
    assert cargo == [["cargo", "metadata", "--format-version", "1", "--locked", "--filter-platform", TRIPLE],
                     _tree_args(TRIPLE)]
    lic.rust_sources(str(tmp_path / "offline"), apps=((str(app), (TRIPLE,)),), files_at=files_at, offline=True,
                     run=recorded)
    assert cargo[2:] == [["cargo", "metadata", "--format-version", "1", "--locked", "--offline", "--filter-platform",
                          TRIPLE], _tree_args(TRIPLE, offline=True)]
    with open(folder / "sources.json", encoding="utf-8") as fh:
        record = json.load(fh)
    assert record == {"repositories": {
        "own/bsd": {"2" * 40: {"crates": ["bsdless 1.0.0"], "files": {"LICENSE.md": _sha(b"bsd's")}}},
        "own/mitless": {"1" * 40: {"crates": ["mitless 1.0.0", "twice 2.0.0"],
                                   "files": {"COPYING": _sha(b"its copying"), "LICENSE": _sha(b"mitless's")}}}}}
    written = sorted(os.path.relpath(os.path.join(r, f), folder) for r, _d, fs in os.walk(folder) for f in fs)
    assert written == [f"own/bsd/{'2' * 40}/LICENSE.md", f"own/mitless/{'1' * 40}/COPYING",
                       f"own/mitless/{'1' * 40}/LICENSE", "sources.json"]
    # and the notice then gives them
    text, problems = lic.rust_notice(str(app), [TRIPLE], run=run, kept=str(folder))
    assert problems == [] and "--- LICENSE\n\nmitless's\n" in text and "--- LICENSE.md\n\nbsd's\n" in text


@pytest.mark.parametrize("change, said", [
    (dict(files={}), "own/bsd at " + "2" * 40 + ": its top folder holds no license file (for bsdless 1.0.0)"),
    (dict(files={"LICENSE": ("0" * 40, b"bsd's")}), f"own/bsd at {'2' * 40}: LICENSE is not the file its tree names "
                                                    f"({'0' * 40})"),
    (dict(files={"sub/LICENSE": (None, b"x")}), "own/bsd at " + "2" * 40 + ": sub/LICENSE is not the file"),
    (dict(files={"README": (None, b"x")}), "own/bsd at " + "2" * 40 + ": README is not the file"),
    (dict(repository="https://example.org/own/bsd"), "bsdless 1.0.0: it ships no license file, and names no "
                                                     "repository on GitHub and commit it was published from"),
    (dict(commit=None), "bsdless 1.0.0: it ships no license file, and names no repository on GitHub and commit"),
])
def test_rust_sources_stops_writing_nothing_on_what_it_cannot_keep(tmp_path, change, said):
    lic = _licenses()
    meta, app, base, run = _small_app(tmp_path)
    bsd = next(p for p in meta["packages"] if p["name"] == "bsdless")
    if "repository" in change:
        bsd["repository"] = change["repository"]
    if "commit" in change:
        os.unlink(base / "bsdless-1.0.0" / ".cargo_vcs_info.json")

    def files_at(repo, commit):
        if repo == "own/bsd" and "files" in change:
            return {name: (blob or lic.git_blob(data), data) for name, (blob, data) in change["files"].items()}
        return {"LICENSE": (lic.git_blob(b"ok"), b"ok")}

    folder = tmp_path / "rust"
    folder.mkdir()
    (folder / "kept.txt").write_text("as it was")
    with pytest.raises(SystemExit) as stopped:
        lic.rust_sources(str(folder), apps=((str(app), (TRIPLE,)),), files_at=files_at, run=run)
    assert said in str(stopped.value)
    assert os.listdir(folder) == ["kept.txt"]


def test_the_kept_repository_files_are_those_the_apps_crates_need_now(tmp_path):
    """rust-sources, run again on the apps as they are with the files it kept, keeps the same: none is missing, none
    is stale, and each is its commit's."""
    lic = _licenses()
    for src, _targets in lic.APPS:
        if not _cargo_works(os.path.join(ROOT, src)):
            pytest.skip("the crates are not here (cargo fetch --locked in the app's src-tauri brings them)")
    with open(os.path.join(lic.RUST_SOURCES, "sources.json"), encoding="utf-8") as fh:
        kept = json.load(fh)

    def files_at(repo, commit):
        found = {}
        for name in kept["repositories"][repo][commit]["files"]:
            with open(os.path.join(lic.RUST_SOURCES, *repo.split("/"), commit, name), "rb") as fh:
                data = fh.read()
            found[name] = (lic.git_blob(data), data)
        return found

    again = lic.rust_sources(str(tmp_path / "again"), files_at=files_at, offline=True)
    with open(os.path.join(again, "sources.json"), encoding="utf-8") as fh:
        assert json.load(fh) == kept
    assert kept["repositories"] and set(kept["repositories"]) == {"madsmtm/objc2"}
    with open(os.path.join(lic.RUST_SOURCES, "madsmtm", "objc2", "8852b424193ca41602281b3d7540d7c8ed51e49a",
                           "LICENSE.md"), encoding="utf-8") as fh:
        objc2 = fh.read()
    assert "`objc2`, `block2`, `objc2-foundation` and `objc2-encode`" in " ".join(objc2.split())
    assert "Copyright" not in objc2                                   # it names no holder, and none is added


def test_the_standard_librarys_notices_are_the_toolchains(tmp_path):
    lic = _licenses()
    sysroot = tmp_path / "toolchain"
    asked = []

    def run(args, **kw):
        asked.append((args, kw.get("cwd")))
        return subprocess.CompletedProcess(args, 0, stdout=f"{sysroot}\n")

    data, problems = lic.rust_std("app", run=run)
    assert data is None and problems == [f"the Rust toolchain {sysroot} ships no share/doc/rust/COPYRIGHT-library.html, "
                                         "the standard library's notices"]
    (sysroot / "share" / "doc" / "rust").mkdir(parents=True)
    (sysroot / "share" / "doc" / "rust" / "COPYRIGHT-library.html").write_bytes(b"<h1>notices</h1>")
    assert lic.rust_std("app", run=run) == (b"<h1>notices</h1>", [])
    assert asked[-1] == (["rustc", "--print", "sysroot"], "app")
    if shutil.which("rustc"):                                       # the toolchain here ships it
        data, problems = lic.rust_std(os.path.join(ROOT, "desktop", "src-tauri"))
        assert problems == [] and b"Copyright notices for The Rust Standard Library" in data


# --- the builds ---

def test_the_desktop_app_carries_its_licenses_folder_and_the_phone_app_its_crates_notice():
    with open(os.path.join(ROOT, "desktop", "src-tauri", "tauri.conf.json"), encoding="utf-8") as fh:
        desktop = json.load(fh)
    assert desktop["bundle"]["resources"]["../build/resources/licenses/"] == "licenses/"
    with open(os.path.join(ROOT, "phone", "src-tauri", "tauri.conf.json"), encoding="utf-8") as fh:
        phone = json.load(fh)
    # Tauri runs it from phone/: the page is built into phone/dist, then the crates' notice is written beside it
    assert phone["build"]["beforeBuildCommand"] == (
        "cd ../ui && npm run build:phone && cd .. && python3 desktop/licenses.py rust phone/src-tauri "
        "phone/dist/THIRD_PARTY_LICENSES-rust.txt --target aarch64-apple-ios")
    assert phone["build"]["frontendDist"] == "../dist"
    with open(os.path.join(ROOT, "ui", "vite.config.ts"), encoding="utf-8") as fh:
        assert 'phone: "../phone/dist"' in fh.read()


def test_the_apps_licenses_dialog_lists_what_each_build_writes_beside_its_pages():
    """ui/src/components/Licenses.tsx lists, for each build, the notices beside its pages: the page's build writes its
    own and Proofpane Community's (ui/licenses.ts) in every build; the phone's build writes the Rust ones beside them
    (its beforeBuildCommand), and the desktop's bundle.py those and the Python's (write_licenses)."""
    lic = _licenses()
    with open(os.path.join(ROOT, "ui", "src", "components", "Licenses.tsx"), encoding="utf-8") as fh:
        dialog = fh.read()
    with open(os.path.join(ROOT, "ui", "licenses.ts"), encoding="utf-8") as fh:
        page = fh.read()
    listed = re.findall(r'\{ file: "([^"]+)"', dialog)
    assert 'export const FILE = "THIRD_PARTY_LICENSES.txt";' in page
    assert 'export const OWN = ["LICENSE", "LICENSE-APACHE-2.0", "NOTICE"];' in page
    assert listed == ["LICENSE", "NOTICE", "LICENSE-APACHE-2.0", "THIRD_PARTY_LICENSES.txt", lic.RUST_FILE, lic.RUST_STD_FILE,
                      lic.PYTHON_FILE]
    with open(os.path.join(ROOT, "phone", "src-tauri", "tauri.conf.json"), encoding="utf-8") as fh:
        phone = json.load(fh)["build"]["beforeBuildCommand"]
    assert f"phone/dist/{lic.RUST_FILE} " in phone                     # the standard library's is written beside it


def test_the_hosted_images_page_build_has_proofpane_communitys_license_and_notice_beside_it():
    """The image builds the pages in /src/ui, with only ui/ copied in: ui/licenses.ts reads LICENSE, LICENSE-APACHE-2.0
    and NOTICE from the folder above, so they are copied there before the build."""
    with open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8") as fh:
        lines = [line.strip() for line in fh]
    stage = lines[lines.index("FROM node:22-alpine AS ui"):]
    stage = stage[:next(i for i, line in enumerate(stage) if line.startswith("FROM ") and i)]
    assert "WORKDIR /src/ui" in stage
    copy = stage.index("COPY open_edition/LICENSE open_edition/LICENSE-APACHE-2.0 open_edition/NOTICE /src/")
    assert copy < stage.index("RUN npm run build")


def test_the_command_line_writes_the_crates_notice_or_names_what_stops_it(tmp_path, monkeypatch, capsys):
    lic = _licenses()
    seen = []
    monkeypatch.setattr(lic, "rust_notice", lambda src, targets: seen.append((src, targets)) or ("the notice", []))
    monkeypatch.setattr(lic, "rust_std", lambda src: seen.append(src) or (b"<p>std</p>", []))
    out = tmp_path / "dist" / "THIRD_PARTY_LICENSES-rust.txt"
    assert lic.main(["rust", "phone/src-tauri", str(out), "--target", "aarch64-apple-ios"]) == 0
    assert seen == [("phone/src-tauri", ["aarch64-apple-ios"]), "phone/src-tauri"] and out.read_text() == "the notice"
    assert (tmp_path / "dist" / "THIRD_PARTY_LICENSES-rust-std.html").read_bytes() == b"<p>std</p>"
    for name in os.listdir(tmp_path / "dist"):
        os.unlink(tmp_path / "dist" / name)
    for crates, std in ((["x 1: why"], []), ([], ["no std notice"])):
        monkeypatch.setattr(lic, "rust_notice", lambda src, targets, crates=crates: ("the notice", crates))
        monkeypatch.setattr(lic, "rust_std", lambda src, std=std: (None if std else b"x", std))
        assert lic.main(["rust", "phone/src-tauri", str(out), "--target", "aarch64-apple-ios"]) == 1
        assert os.listdir(tmp_path / "dist") == [] and (crates + std)[0] in capsys.readouterr().err
    # the kept sources, rewritten
    monkeypatch.setattr(lic, "rust_sources", lambda: "the rust folder")
    monkeypatch.setattr(lic, "python_sources", lambda: "the python folder")
    assert lic.main(["rust-sources"]) == 0 and capsys.readouterr().out == "the rust folder\n"
    assert lic.main(["python-sources"]) == 0 and capsys.readouterr().out == "the python folder\n"


def test_the_desktop_build_writes_the_apps_licenses_folder(tmp_path, monkeypatch):
    bundle = _bundle()
    monkeypatch.setattr(bundle, "OUT", str(tmp_path / "resources"))
    monkeypatch.setattr(bundle, "BUILD", str(tmp_path))
    (tmp_path / "ui").mkdir()
    (tmp_path / "ui" / "THIRD_PARTY_LICENSES.txt").write_text("the page's notice")
    calls = []
    monkeypatch.setattr(bundle.licenses, "python_notice",
                        lambda *a, **kw: calls.append(("python", a, kw)) or ("the python's notice", []))
    monkeypatch.setattr(bundle.licenses, "rust_notice",
                        lambda *a, **kw: calls.append(("rust", a, kw)) or ("the crates' notice", []))
    monkeypatch.setattr(bundle.licenses, "rust_std", lambda *a, **kw: calls.append(("std", a, kw)) or (b"<std>", []))
    monkeypatch.setattr(bundle, "openssl_license", lambda: "openssl's license")
    folder = bundle.write_licenses("x86_64")
    assert folder == str(tmp_path / "resources" / "licenses")
    assert sorted(os.listdir(folder)) == ["LICENSE", "LICENSE-APACHE-2.0", "NOTICE", "THIRD_PARTY_LICENSES-page.txt",
                                          "THIRD_PARTY_LICENSES-python.txt", "THIRD_PARTY_LICENSES-rust-std.html",
                                          "THIRD_PARTY_LICENSES-rust.txt"]
    with open(os.path.join(folder, "THIRD_PARTY_LICENSES-rust-std.html"), "rb") as fh:
        assert fh.read() == b"<std>"
    for name, said in (("THIRD_PARTY_LICENSES-page.txt", "the page's notice"),
                       ("THIRD_PARTY_LICENSES-python.txt", "the python's notice"),
                       ("THIRD_PARTY_LICENSES-rust.txt", "the crates' notice")):
        with open(os.path.join(folder, name), encoding="utf-8") as fh:
            assert fh.read() == said
    for name in ("LICENSE", "LICENSE-APACHE-2.0", "NOTICE"):
        with open(os.path.join(folder, name), "rb") as a, open(os.path.join(ROOT, name), "rb") as b:
            assert a.read() == b.read()
    # and beside the window's own pages, for their Licenses dialog
    assert sorted(os.listdir(tmp_path / "ui")) == ["THIRD_PARTY_LICENSES-python.txt", "THIRD_PARTY_LICENSES-rust-std.html",
                                                   "THIRD_PARTY_LICENSES-rust.txt", "THIRD_PARTY_LICENSES.txt"]
    for name in ("THIRD_PARTY_LICENSES-python.txt", "THIRD_PARTY_LICENSES-rust-std.html", "THIRD_PARTY_LICENSES-rust.txt"):
        assert (tmp_path / "ui" / name).read_bytes() == (tmp_path / "resources" / "licenses" / name).read_bytes()
    assert calls == [
        ("python", (str(tmp_path / "resources" / "python"), "x86_64-apple-darwin", bundle.PYTHON, bundle.RELEASE,
                    bundle.python_changes(), (bundle.OPENSSL[0], "openssl's license")), {"fetch": bundle.download}),
        ("rust", (os.path.join(DESKTOP, "src-tauri"), ["x86_64-apple-darwin"]), {}),
        ("std", (os.path.join(DESKTOP, "src-tauri"),), {}),
    ]
    calls.clear()
    bundle.write_licenses("arm64")                    # its cryptography is a wheel: no OpenSSL built here
    assert calls[0][1][1:] == ("aarch64-apple-darwin", bundle.PYTHON, bundle.RELEASE, bundle.python_changes(), None)
    assert calls[1][1][1] == ["aarch64-apple-darwin"]


def test_the_desktop_build_stops_on_what_the_app_cannot_carry(tmp_path, monkeypatch):
    bundle = _bundle()
    monkeypatch.setattr(bundle, "OUT", str(tmp_path / "resources"))
    monkeypatch.setattr(bundle.licenses, "python_notice", lambda *a, **kw: ("", ["a 1: why"]))
    monkeypatch.setattr(bundle.licenses, "rust_notice", lambda *a, **kw: ("", ["b 2: why not"]))
    monkeypatch.setattr(bundle.licenses, "rust_std", lambda *a, **kw: (None, ["no std notice"]))
    with pytest.raises(SystemExit) as stopped:
        bundle.write_licenses("arm64")
    for said in ("a 1: why", "b 2: why not", "no std notice"):
        assert said in str(stopped.value)
    assert not (tmp_path / "resources" / "licenses").exists()


def test_openssls_license_comes_from_its_pinned_source(tmp_path, monkeypatch):
    bundle = _bundle()
    archive = tmp_path / "openssl.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        data = b"OpenSSL's Apache License"
        info = tarfile.TarInfo(f"openssl-{bundle.OPENSSL[0]}/LICENSE.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    monkeypatch.setattr(bundle, "openssl_source", lambda: str(archive))
    assert bundle.openssl_license() == "OpenSSL's Apache License"
    assert bundle.builds_openssl("x86_64") and not bundle.builds_openssl("arm64")


def test_the_build_writes_the_licenses_once_the_app_is_laid_out_and_before_it_is_checked(tmp_path, monkeypatch):
    bundle = _bundle()
    monkeypatch.setattr(bundle.sys, "platform", "darwin")
    monkeypatch.setattr(bundle.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(bundle, "OUT", str(tmp_path / "resources"))
    steps = []
    for name in ("fetch_python", "unpack_python", "install_requirements", "trim", "copy_app", "compile_all",
                 "write_licenses", "check_portable", "sign"):
        monkeypatch.setattr(bundle, name, (lambda step: lambda *a, **kw: steps.append((step, a)) or step)(name))
    assert bundle.main([]) == 0
    assert [step for step, _a in steps] == ["fetch_python", "unpack_python", "install_requirements", "trim",
                                            "copy_app", "compile_all", "write_licenses", "check_portable"]
    assert ("write_licenses", ("arm64",)) in steps
    steps.clear()
    assert bundle.main(["--sign", "-"]) == 0
    assert [step for step, _a in steps][-3:] == ["write_licenses", "check_portable", "sign"]
