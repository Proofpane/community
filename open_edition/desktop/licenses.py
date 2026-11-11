"""The third-party licenses of what Proofpane Community's desktop and phone apps carry besides their pages (NOTICE; the
pages' own, their npm packages', are written by the pages' build, ui/licenses.ts):

- THIRD_PARTY_LICENSES-python.txt, the desktop app's Python (bundle.py writes it): CPython as python-build-standalone
  builds it (its license and notices, from CPython's own source), with the libraries that build links into it
  (python-build-standalone's own record of them, and the files it holds their licenses in; all kept in
  desktop/third-party/python/ for the pinned release), every Python package the app installs (the license its
  metadata names, the license files it ships, and the components its SBOMs list), and, on an Intel Mac, the OpenSSL
  built here into cryptography.
- THIRD_PARTY_LICENSES-rust.txt, the Rust crates whose code an app's shell links for its target, as cargo's resolver
  builds it (`cargo tree`, with `cargo metadata` for each crate's folder and license): the desktop app's (bundle.py)
  and the phone app's (its beforeBuildCommand, phone/src-tauri/tauri.conf.json); and beside it
  THIRD_PARTY_LICENSES-rust-std.html, the copyright notices of the Rust standard library, which every Rust program
  carries, as the toolchain that builds the app ships them.

Everything named carries a license ALLOWED names, or a choice (OR) of licenses one of which is, and a license text: its
own files or, for a crate that ships none, files supplied from what its Cargo.toml declares (supplied()): those of a
crate published from its repository under the same license, the text of a license it offers that names no holder, or
its repository's own license files at the commit it was published from, which rust-sources keeps in
desktop/third-party/rust/. No holder is ever made up. A crate from crates.io under MPL-2.0 is allowed too, with where
its source is there: crates.io keeps every version it publishes. Anything else stops the build, which says what.

    python3 desktop/licenses.py rust SRC_TAURI OUT --target TRIPLE   the Rust crates an app links for TRIPLE (repeat
                                                                     --target for more than one), written to OUT,
                                                                     and the standard library's notices beside it
    python3 desktop/licenses.py python-sources                       rewrite desktop/third-party/python/ from the
                                                                     pinned release's full archives and CPython's
                                                                     source (downloads them, into build/downloads;
                                                                     needs zstd)
    python3 desktop/licenses.py rust-sources                         rewrite desktop/third-party/rust/ from GitHub,
                                                                     with git: the license files of the
                                                                     repositories of the crates the apps link that
                                                                     need them, at the commits they were published
                                                                     from

Standard library only.
"""

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import urllib.parse
import urllib.request
from email.parser import HeaderParser

HERE = os.path.dirname(os.path.abspath(__file__))
OPEN_EDITION = os.path.dirname(HERE)
THIRD_PARTY = os.path.join(HERE, "third-party")
PYTHON_SOURCES = os.path.join(THIRD_PARTY, "python")
APACHE_TEXT = os.path.join(OPEN_EDITION, "LICENSE-APACHE-2.0")   # the Apache License 2.0's text, kept beside LICENSE
MPL_TEXT = os.path.join(THIRD_PARTY, "MPL-2.0.txt")      # the MPL 2.0, as cssparser 0.37.0 ships it
RUST_SOURCES = os.path.join(THIRD_PARTY, "rust")         # crates' repositories' license files (rust-sources)
PYTHON_FILE = "THIRD_PARTY_LICENSES-python.txt"
RUST_FILE = "THIRD_PARTY_LICENSES-rust.txt"
RUST_STD_FILE = "THIRD_PARTY_LICENSES-rust-std.html"       # the Rust standard library's, as its toolchain ships it

# The licenses the apps take (SPDX identifiers): permissive ones, whose conditions for software carried in a built app
# the notices meet: the copyright notice and license text as its project gives them (never a holder made up here), an
# Apache NOTICE file among the license files where one is shipped, and for Python (PSF, CNRI) a summary of the changes
# made to it (python_notice).
ALLOWED = frozenset({
    "0BSD", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "BSL-1.0", "CC0-1.0", "ISC", "MIT", "MIT-0", "Unicode-3.0",
    "Unicode-DFS-2016", "Unlicense", "Zlib",
    "PSF-2.0", "Python-2.0", "CNRI-Python",           # Python's
    "blessing", "bzip2-1.0.6",                         # SQLite's and bzip2's, built into the Python
})
# Taken for a Rust crate from crates.io too, with where its source is there (MPL-2.0 3.2: whoever has the executable
# is told where), crates.io keeping every version it publishes.
SOURCE_NAMED = frozenset({"MPL-2.0"})
CRATES_IO = "registry+https://github.com/rust-lang/crates.io-index"       # cargo metadata's source for a crates.io crate
LICENSE_FILE = re.compile(r"^(licen[cs]e|copying|notice)([._-][a-z0-9._-]*)?$", re.I)
RULE = "=" * 100

# What python-build-standalone links into its Python for macOS, by the static library: the library's name, its license
# and the file in that build's licenses/ folder that holds it, as we read that file (for OpenSSL, the version
# python-sources finds built: 3, under the Apache License 2.0). python-sources checks that each file is one
# python-build-standalone names for every extension module linking the library.
LIBRARIES = {
    "bz2": ("bzip2", "bzip2-1.0.6", "LICENSE.bzip2.txt"),
    "crypto": ("OpenSSL", "Apache-2.0", "LICENSE.openssl-3.txt"),
    "expat": ("Expat", "MIT", "LICENSE.expat.txt"),
    "ffi": ("libffi", "MIT", "LICENSE.libffi.txt"),
    "lzma": ("liblzma", "0BSD", "LICENSE.liblzma.txt"),
    "mpdec": ("mpdecimal", "BSD-2-Clause", "LICENSE.mpdecimal.txt"),
    "sqlite3": ("SQLite", "blessing", "LICENSE.sqlite.txt"),
    "ssl": ("OpenSSL", "Apache-2.0", "LICENSE.openssl-3.txt"),
    "uuid": ("libuuid", "BSD-3-Clause", "LICENSE.libuuid.txt"),
}


# --- SPDX license expressions ---

def _tokens(expression):
    """An expression's tokens; the old crates.io form "MIT/Apache-2.0" is a choice, as "MIT OR Apache-2.0"."""
    return re.findall(r"\(|\)|[^\s()]+", expression.replace("/", " OR "))


def _tree(expression):
    """An SPDX expression as a tree, ("OR" | "AND", [terms]) or an identifier, an exception (WITH) dropped, since it
    only adds permissions; None if it is not one."""
    tokens, at = _tokens(expression or ""), 0

    def word(i):
        return tokens[i].upper() if i < len(tokens) else None

    def either():
        nonlocal at
        terms = [both()]
        while word(at) == "OR":
            at += 1
            terms.append(both())
        return terms[0] if len(terms) == 1 else ("OR", terms)

    def both():
        nonlocal at
        terms = [one()]
        while word(at) == "AND":
            at += 1
            terms.append(one())
        return terms[0] if len(terms) == 1 else ("AND", terms)

    def one():
        nonlocal at
        if at >= len(tokens) or tokens[at] == ")" or word(at) in ("AND", "OR", "WITH"):
            raise ValueError
        token = tokens[at]
        at += 1
        if token == "(":
            inner = either()
            if word(at) != ")":
                raise ValueError
            at += 1
            return inner
        if word(at) == "WITH":
            if at + 1 >= len(tokens) or tokens[at + 1] in "()" or word(at + 1) in ("AND", "OR", "WITH"):
                raise ValueError
            at += 2
        return token

    try:
        tree = either()
    except ValueError:
        return None
    return tree if at == len(tokens) else None


def allowed(expression, licenses=ALLOWED):
    """Whether an SPDX expression can be met with `licenses` alone (case aside): an OR by one of its terms, an AND by
    all of them."""
    tree = _tree(expression)
    names = {name.lower() for name in licenses}

    def met(node):
        if isinstance(node, str):
            return node.lower() in names
        op, terms = node
        return any(met(t) for t in terms) if op == "OR" else all(met(t) for t in terms)

    return tree is not None and met(tree)


# --- the notice ---

def _decode(data):
    """Bytes as text: UTF-8, or Latin-1 for bytes that are not (every byte read as itself)."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _text(path):
    """A file's text (_decode)."""
    with open(path, "rb") as fh:
        return _decode(fh.read())


def _inside(rel):
    """Whether a relative path in a package's metadata stays inside the folder it is relative to."""
    parts = rel.replace("\\", "/").split("/")
    return bool(rel) and not rel.startswith(("/", "\\")) and ":" not in parts[0] and ".." not in parts


def notice(title, about, entries):
    """The file: a title, what it is (`about`, wrapped), then each entry: its title, its notes (wrapped), its files as
    they are. A file whose text is that of one before it, exactly, points to that one instead of repeating it (most
    crates ship the same Apache License text)."""
    out, first = [title, "", *textwrap.wrap(about, 116, break_on_hyphens=False), ""], {}
    for entry in entries:
        out += [RULE, entry["title"], RULE, ""]
        for note in entry.get("notes", ()):
            out += [*textwrap.wrap(note, 116, break_on_hyphens=False, break_long_words=False), ""]
        for name, text in entry.get("files", ()):
            body = text.rstrip()
            key = hashlib.sha256(body.encode("utf-8", "surrogatepass")).hexdigest()
            if key in first:
                out += [f"--- {name}", "", f"(The same text as {first[key]}, above.)", ""]
            else:
                first[key] = f"{name} of {entry.get('who', entry['title'])}"
                out += [f"--- {name}", "", body, ""]
        if entry.get("after"):
            out += [*entry["after"], ""]
    return "\n".join(out)


def _apache():
    """The Apache License 2.0's text, as LICENSE-APACHE-2.0 holds it beside Proofpane Community's LICENSE."""
    with open(APACHE_TEXT, encoding="utf-8") as fh:
        return fh.read()


# The MIT License's text (SPDX "MIT"), its permission notice and disclaimer, with no copyright line: for a crate whose
# repository names the license and neither holds its text nor names a holder (objc2's LICENSE.md links to the text).
MIT_TEXT = "MIT License\n\n" + "\n\n".join(textwrap.fill(paragraph, 79) for paragraph in (
    "Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated "
    "documentation files (the \"Software\"), to deal in the Software without restriction, including without "
    "limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the "
    "Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:",
    "The above copyright notice and this permission notice shall be included in all copies or substantial portions of "
    "the Software.",
    "THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO "
    "THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE "
    "AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF "
    "CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS "
    "IN THE SOFTWARE.")) + "\n"


# --- the Python ---

STATIC_CRATES = "https://static.crates.io/crates"
# A component its SBOM gives no license for, by name: the license its project has had from the major version given on.
GENERIC = {"openssl": ("Apache-2.0", 3)}          # OpenSSL, under the Apache License 2.0 since 3.0
CRATE_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
CRATE_VERSION = re.compile(r"[0-9A-Za-z.+-]{1,64}")
ARCHIVE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\.(?:tar\.gz|tgz|crate)")
SHA256 = re.compile(r"[0-9a-f]{64}")


def _sbom_components(document):
    """A CycloneDX document's components, nested ones too, each a dict: name, version, license ("" when it gives
    none), ref (its bom-ref), purl, sha256 (the SHA-256 it gives, or ""), url (where a release is downloaded from, or
    "") and repository (its vcs, or ""); None for a document that is not CycloneDX."""
    if not isinstance(document, dict) or document.get("bomFormat") != "CycloneDX" \
            or not isinstance(document.get("components", []), list):
        return None
    found = []

    def walk(components):
        for c in components if isinstance(components, list) else ():
            if not isinstance(c, dict):
                continue
            terms = []
            for entry in c.get("licenses") or ():
                if not isinstance(entry, dict):
                    continue
                if isinstance(entry.get("expression"), str):
                    terms.append(entry["expression"])
                elif isinstance(entry.get("license"), dict):
                    one = entry["license"].get("id") or entry["license"].get("name")
                    if isinstance(one, str):
                        terms.append(one)
            refs = [r for r in c.get("externalReferences") or () if isinstance(r, dict) and isinstance(r.get("url"), str)]
            purl = str(c.get("purl") or "")
            url = next((r["url"] for r in refs if r.get("type") == "distribution"), "")
            if not url and "download_url=" in purl:
                url = urllib.parse.unquote(purl.split("download_url=", 1)[1].split("&")[0])
            found.append({
                "name": str(c.get("name") or "?"), "version": str(c.get("version") or ""),
                "license": terms[0] if len(terms) == 1 else " AND ".join(f"({t})" for t in terms),
                "ref": str(c.get("bom-ref") or ""), "purl": purl, "url": url,
                "sha256": next((str(h.get("content", "")).lower() for h in c.get("hashes") or ()
                                if isinstance(h, dict) and h.get("alg") == "SHA-256"), ""),
                "repository": next((r["url"] for r in refs if r.get("type") == "vcs"), ""),
            })
            walk(c.get("components"))

    walk(document.get("components", []))
    return found


def archive_files(path, nested):
    """The license files in a source archive (a .crate, or a release's .tar.gz) under its one top folder, each (its path
    from there, its text): those below the top too when `nested`; links and anything leaving the folder left out."""
    found = {}
    with tarfile.open(path) as tar:
        for member in tar:
            parts = member.name.split("/")
            if member.isfile() and len(parts) >= 2 and (nested or len(parts) == 2) and ".." not in parts \
                    and _license_name(parts[-1]):
                found["/".join(parts[1:])] = _decode(tar.extractfile(member).read())
    return [(rel, found[rel]) for rel in sorted(found, key=lambda rel: (rel.count("/"), rel))]


def _crate_archive(name, version, sha256, fetch, cargo_home):
    """A crate's archive, checked against `sha256`: cargo's own copy where it has one that matches, else fetched from
    crates.io (`fetch(url, file name, sha256)`, which checks it)."""
    for path in sorted(glob.glob(os.path.join(cargo_home, "registry", "cache", "*", f"{name}-{version}.crate"))):
        with open(path, "rb") as fh:
            if _sha256(fh.read()) == sha256:
                return path
    return fetch(f"{STATIC_CRATES}/{name}/{name}-{version}.crate", f"{name}-{version}.crate", sha256)


def component_entries(components, fetch, cargo_home):
    """(entries, problems) for the components the packages' SBOMs list, each once, sorted by name: `components` is
    {key: (component, [whose wheel lists it])}. Each with the license files of its own source, checked against the
    SHA-256 its SBOM gives: a crate's archive (in cargo's cache, or from crates.io), a release's (from where its SBOM
    says); a crate with none is supplied a text as a crate of the app's shell is (supplied())."""
    rows, problems, texts = [], [], None
    for c, whose in components.values():
        who = f"{c['name']} {c['version']}"
        row = {"c": c, "who": who, "license": c["license"], "files": [], "notes": [], "source": None,
               "whose": " and of ".join(sorted(set(whose)))}
        crate = c["purl"].startswith("pkg:cargo/") and c["ref"].startswith(CRATES_IO + "#")
        if fetch is None:
            problems.append(f"{who}: the SBOM of {row['whose']} lists it, and this build fetches no sources")
        elif crate and CRATE_NAME.fullmatch(c["name"]) and CRATE_VERSION.fullmatch(c["version"]) \
                and SHA256.fullmatch(c["sha256"]):
            row["files"] = archive_files(_crate_archive(c["name"], c["version"], c["sha256"], fetch, cargo_home), True)
            row["source"] = "from crates.io"
        elif not crate and c["url"].startswith("https://") and SHA256.fullmatch(c["sha256"]) \
                and ARCHIVE_NAME.fullmatch(os.path.basename(urllib.parse.urlsplit(c["url"]).path)):
            name = os.path.basename(urllib.parse.urlsplit(c["url"]).path)
            row["files"] = archive_files(fetch(c["url"], name, c["sha256"]), False)
            row["source"] = f"from {c['url']}"
            known = GENERIC.get(c["name"].lower())
            major = re.match(r"\d+", c["version"])
            if not row["license"] and known and major and int(major.group()) >= known[1]:
                row["license"] = known[0]
                row["notes"].append(f"Its SBOM names no license; {c['name']} has been under {known[0]} since version "
                                    f"{known[1]}.")
        else:
            problems.append(f"{who}: the SBOM of {row['whose']} gives no source this build can take its license text "
                            "from (a crates.io crate, or a release's https address, with its SHA-256)")
        rows.append(row)
    shipped = {r["c"]["ref"]: ({"id": r["c"]["ref"], "name": r["c"]["name"], "version": r["c"]["version"],
                                "license": r["license"], "repository": r["c"]["repository"]}, r["files"])
               for r in rows}
    entries = []
    for row in sorted(rows, key=lambda r: (r["c"]["name"].lower(), r["c"]["version"])):
        who, license = row["who"], row["license"]
        if row["source"]:
            row["notes"].insert(0, f"In the wheel of {row['whose']}, as its SBOM lists it; its license files are those "
                                   f"of its source, {row['source']}, checked against the SHA-256 the SBOM gives.")
        if not license:
            problems.append(f"{who}: the SBOM of {row['whose']} names no license for it")
        elif not allowed(license):
            if allowed(license, ALLOWED | SOURCE_NAMED) and row["source"] == "from crates.io":
                row["notes"].append(f"Its source: https://crates.io/crates/{row['c']['name']}/{row['c']['version']}")
            else:
                problems.append(f"{who}: the SBOM of {row['whose']} names its license, {license}, which is not one "
                                "the app takes")
        if row["source"] and not row["files"]:
            if texts is None:
                with open(APACHE_TEXT, encoding="utf-8") as fh, open(MPL_TEXT, encoding="utf-8") as mpl:
                    texts = {"Apache-2.0": fh.read(), "MPL-2.0": mpl.read()}
            supply = supplied(shipped[row["c"]["ref"]][0], shipped, texts, "Its SBOM") \
                if row["source"] == "from crates.io" else None
            if supply is None:
                problems.append(f"{who}: its source ships no license file, and none can be supplied for it")
            else:
                row["notes"].insert(1, supply[0])
                row["files"] = supply[1]
        entries.append({"title": f"{who} ({license or 'no license named'})", "who": who, "notes": row["notes"],
                        "files": row["files"]})
    return entries, problems


def distributions(site, fetch=None, cargo_home=None):
    """(entries, problems): each distribution installed in `site` (its .dist-info folders), sorted by name, then each
    component its SBOMs list (component_entries; `fetch` and `cargo_home` find their sources). A component its SBOM
    lists as part of the package itself (a path dependency of its build) is under the package's own license."""
    entries, problems, components = [], [], {}
    for folder in sorted(f for f in os.listdir(site) if f.endswith(".dist-info")):
        info = os.path.join(site, folder)
        if not os.path.isfile(os.path.join(info, "METADATA")):
            problems.append(f"{folder}: it has no METADATA")
            continue
        meta = HeaderParser().parsestr(_text(os.path.join(info, "METADATA")))
        name, version = meta.get("Name", folder), meta.get("Version", "")
        who = f"{name} {version}"
        license = (meta.get("License-Expression") or "").strip()
        if not license:
            # metadata before License-Expression: its License field, when that is an expression and not a text
            old = (meta.get("License") or "").strip()
            license = old if _tree(old) is not None else ""
        if not license:
            problems.append(f"{who}: its metadata names no license this build reads")
        elif not allowed(license):
            problems.append(f"{who}: its license, {license}, is not one the app takes")
        files = []
        declared = meta.get_all("License-File") or []
        for rel in declared:
            # relative to the .dist-info's licenses/ folder (metadata 2.4), or to the .dist-info itself (before),
            # where an older wheel also put it by its file name alone
            where = [os.path.join(info, "licenses", rel), os.path.join(info, rel),
                     os.path.join(info, os.path.basename(rel.replace("\\", "/")))] if _inside(rel) else []
            path = next((p for p in where if os.path.isfile(p)), None)
            if path is None:
                problems.append(f"{who}: its metadata names the license file {rel}, which it does not ship")
            else:
                files.append((os.path.relpath(path, site).replace(os.sep, "/"), _text(path)))
        if not declared:
            for sub in (info, os.path.join(info, "licenses")):
                for f in sorted(os.listdir(sub)) if os.path.isdir(sub) else ():
                    if LICENSE_FILE.match(f) and os.path.isfile(os.path.join(sub, f)):
                        files.append((os.path.relpath(os.path.join(sub, f), site).replace(os.sep, "/"),
                                      _text(os.path.join(sub, f))))
        if not files and not any(p.startswith(f"{who}: its metadata names the license file") for p in problems):
            problems.append(f"{who}: it ships no license file")
        after = []
        sboms = os.path.join(info, "sboms")
        for f in sorted(os.listdir(sboms)) if os.path.isdir(sboms) else ():
            rel = f"{folder}/sboms/{f}"
            try:
                with open(os.path.join(sboms, f), encoding="utf-8") as fh:
                    listed = _sbom_components(json.load(fh))
            except (OSError, ValueError):
                listed = None
            if listed is None:
                problems.append(f"{who}: its SBOM {rel} is not a CycloneDX document this build reads")
                continue
            own = [c for c in listed if c["ref"].startswith("path+")]
            built_in = [c for c in listed if not c["ref"].startswith("path+")]
            if built_in:
                after += textwrap.wrap(f"Its SBOM {rel} lists these as built into it, each named below, after the "
                                       "packages: " + ", ".join(f"{c['name']} {c['version']}" for c in built_in) + ".",
                                       116, break_on_hyphens=False)
            if own:
                after += textwrap.wrap(f"It lists these as parts of {name} itself, under its license above: "
                                       + ", ".join(f"{c['name']} {c['version']}" for c in own) + ".",
                                       116, break_on_hyphens=False)
            for c in own:
                if c["license"] and c["license"] != license:
                    problems.append(f"{who}: its SBOM {rel} lists {c['name']} {c['version']}, part of it, under "
                                    f"{c['license']}, not its own license")
            for c in built_in:
                components.setdefault((c["name"], c["version"], c["sha256"] or c["url"] or c["ref"]), (c, []))[1] \
                    .append(who)
        entries.append({"title": f"{who} ({license or 'no license named'})", "who": who, "files": files,
                        "after": after, "key": (name.lower(), version)})
    entries.sort(key=lambda e: e["key"])
    more, still = component_entries(components, fetch, cargo_home or os.environ.get("CARGO_HOME")
                                    or os.path.join(os.path.expanduser("~"), ".cargo"))
    return entries + more, problems + still


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def python_notice(python_dir, triple, python, release, changes, openssl=None, sources=PYTHON_SOURCES, fetch=None,
                  cargo_home=None):
    """(text, problems): the notice for the Python the desktop app carries, CPython `python` from
    python-build-standalone's `release` for `triple`, as it is laid out in `python_dir` (trimmed: an extension module
    in a file of its own is carried only if its file is). `changes` names what the app's build leaves out of it, for
    the summary of the changes made to Python its licenses ask for (PSF-2.0 and CNRI-Python, section 3). `openssl` is
    (version, its LICENSE.txt) for the OpenSSL built here into cryptography; `fetch` and `cargo_home` find the sources
    of what the packages' SBOMs list (component_entries)."""
    problems = []
    with open(os.path.join(sources, "sources.json"), encoding="utf-8") as fh:
        record = json.load(fh)
    if (record.get("python"), record.get("release")) != (python, release):
        problems.append(f"desktop/third-party/python/ was written for CPython {record.get('python')} from release "
                        f"{record.get('release')}, not {python} from {release}: run python3 desktop/licenses.py "
                        "python-sources")
    build = record.get("builds", {}).get(triple)
    if build is None:
        problems.append(f"desktop/third-party/python/ has no record of the build for {triple}")
        build = {"extensions": {}}

    def stored(name):
        path = os.path.join(sources, name)
        data = None
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                data = fh.read()
        if data is None or _sha256(data) != record.get("files", {}).get(name):
            problems.append(f"desktop/third-party/python/{name} is not the file python-sources wrote")
            return ""
        return _text(path)

    cpython = record.get("cpython", {})
    license = " AND ".join(cpython.get("licenses", []))
    if not allowed(license):
        problems.append(f"CPython {python}: its license, {license or 'none named'}, is not one the app takes")
    if not changes:
        problems.append(f"CPython {python}: no summary of the changes made to it, which its licenses ask for")
    entries = [{
        "title": f"CPython {python} ({license})", "who": f"CPython {python}",
        "notes": [
            f"From CPython {python}'s source: its license and its acknowledgements for the software it incorporates "
            "(Doc/license.rst); the notices in its C source that license.rst does not reproduce "
            "(NOTICE.cpython-c.txt); and the notices of HACL*, which its hash modules are built from and which "
            "license.rst does not name: the MIT License its C files open with (LICENSE.hacl.txt), and KaRaMeL's "
            "notice in the headers they include (NOTICE.krml.txt), under the Apache License 2.0, whose text follows.",
            "The changes made to it for this app (a brief summary, as the PSF and CNRI licenses ask): "
            f"python-build-standalone builds it from its source, with changes of its own (release {release}, "
            "https://github.com/astral-sh/python-build-standalone); this app's build (desktop/bundle.py) then leaves "
            "out " + "; ".join(changes or ["nothing"]) + "; installs the packages below into it; and compiles its "
            "bytecode, never to be checked against its source.",
        ],
        "files": [(name, stored(name)) for name in cpython.get("files", [])] + [("Apache-2.0", _apache())],
    }]
    libraries = {}                                  # license file -> (name, license, {module, ...})
    for module, ext in sorted(build["extensions"].items()):
        shared = ext.get("shared")
        if shared and not os.path.exists(os.path.join(python_dir, shared)):
            continue                                # left out of the app (bundle.trim)
        for lib in ext.get("static", ()):
            if lib not in LIBRARIES:
                problems.append(f"python-build-standalone builds {lib} into {module}, which the app carries, and "
                                "desktop/licenses.py names no license for it (LIBRARIES)")
                continue
            name, license, file = LIBRARIES[lib]
            libraries.setdefault(file, (name, license, set()))[2].add(module)
    for file, (name, license, modules) in sorted(libraries.items(), key=lambda kv: kv[1][0].lower()):
        label = name
        if name == "OpenSSL" and build.get("openssl"):
            label = f"OpenSSL {build['openssl'].split()[1]}"
        entries.append({"title": f"{label} ({license}), built into {', '.join(sorted(modules))}", "who": label,
                        "notes": ["As python-build-standalone ships its license."], "files": [(file, stored(file))]})
    if openssl:
        version, text = openssl
        entries.append({"title": f"OpenSSL {version} (Apache-2.0), built here into cryptography's extension module",
                        "who": f"OpenSSL {version}, built here",
                        "notes": ["cryptography has no wheel for an Intel Mac: the build makes it from its source, "
                                  "against OpenSSL built from its pinned source (desktop/bundle.py)."],
                        "files": [("LICENSE.txt", text)]})
    site = os.path.join(python_dir, "lib", "python" + ".".join(python.split(".")[:2]), "site-packages")
    packages, more = distributions(site, fetch, cargo_home)
    problems += more
    title = "Third-party software in this build of Proofpane Community's desktop app: its Python"
    about = (
        "Proofpane Community is open source under the Apache License 2.0 (its LICENSE and NOTICE files). Its "
        f"desktop app carries a Python: CPython {python} as python-build-standalone builds it (release {release}, for "
        f"{triple}), with the libraries that build links into it, and the Python packages below, each under its own "
        "license. Each is named with its license, then the license files it ships, as it ships them, and for a "
        "package, the components its SBOMs list as built into it, each then named with the license files of its "
        "own source. Written by the build (desktop/licenses.py), from "
        "python-build-standalone's own record of what it links in (desktop/third-party/python/) and each package's "
        "metadata."
    )
    return notice(title, about, entries + packages), problems


# --- python-build-standalone's record, kept for the pinned release ---

def sources_record(release, python, builds, cpython):
    """(record, files) for desktop/third-party/python/, from python-build-standalone's full archives for `release`:
    `builds` is {triple: {"archive", "sha256", "python_json": PYTHON.json read, "licenses": {name: bytes} (its
    licenses/ folder), "openssl": the OpenSSL version text its libcrypto carries, or None}}; `cpython` is {"archive",
    "sha256", "url", "texts": cpython_texts()} for CPython's own source. Stops on anything this module would read
    wrongly."""
    problems, files, record = [], {}, {"release": release, "python": python, "builds": {}}
    for triple, build in sorted(builds.items()):
        info, held = build["python_json"], build["licenses"]
        if info.get("python_version") != python:
            problems.append(f"{triple}: its archive holds CPython {info.get('python_version')}, not {python}")
        mine = {"licenses": list(info.get("licenses") or []), "files": list(cpython["texts"]),
                "source": {key: cpython[key] for key in ("archive", "sha256", "url")}}
        if record.setdefault("cpython", mine) != mine:
            problems.append(f"{triple}: CPython's license is {mine['licenses']}, not {record['cpython']['licenses']} "
                            "as in another build")
        extensions = {}
        for module, variants in sorted(info.get("build_info", {}).get("extensions", {}).items()):
            static, named, shared = [], set(), None
            for variant in variants:
                static += [l["name"] for l in variant.get("links", ()) if "path_static" in l and l["name"] not in static]
                named |= {os.path.basename(p) for p in variant.get("license_paths") or ()}
                if variant.get("shared_lib"):
                    if not variant["shared_lib"].startswith("install/"):
                        problems.append(f"{triple}: {module} is a file outside the install ({variant['shared_lib']})")
                    shared = variant["shared_lib"][len("install/"):]
            if not static:
                continue
            extensions[module] = {"shared": shared, "static": static}
            for lib in static:
                if lib not in LIBRARIES:
                    continue                            # python_notice refuses it if the app carries the module
                file = LIBRARIES[lib][2]
                if file not in named:
                    problems.append(f"{triple}: python-build-standalone names {sorted(named)} for {module}, not "
                                    f"{file}, which desktop/licenses.py reads for {lib}")
                elif file not in held:
                    problems.append(f"{triple}: its archive does not hold {file}")
                else:
                    files.setdefault(file, held[file])
        uses_openssl = any(lib in ("crypto", "ssl") for ext in extensions.values() for lib in ext["static"])
        if uses_openssl and not re.match(r"OpenSSL 3\.\d+\.\d+ ", build.get("openssl") or ""):
            problems.append(f"{triple}: its OpenSSL is {build.get('openssl')!r}, not version 3, whose license "
                            "LICENSE.openssl-3.txt is")
        for name, data in held.items():
            if name in files and files[name] != data:
                problems.append(f"{triple}: its {name} differs from another build's")
        record["builds"][triple] = {"archive": build["archive"], "sha256": build["sha256"],
                                    "openssl": build.get("openssl") if uses_openssl else None,
                                    "extensions": extensions}
    if problems:
        raise SystemExit("python-sources:\n  " + "\n  ".join(problems))
    files = {**cpython["texts"], **files}
    record["files"] = {name: _sha256(data) for name, data in sorted(files.items())}
    return record, files


def _uncomment(inner):
    """A C comment's text, from between its markers: each line without its leading "*" and the spaces around, and
    without the lines around it that hold no letter or digit (blank, or a rule)."""
    lines = [re.sub(r"^\s*\*?", "", line).strip() for line in inner.splitlines()]
    while lines and not re.search(r"[A-Za-z0-9]", lines[0]):
        lines.pop(0)
    while lines and not re.search(r"[A-Za-z0-9]", lines[-1]):
        lines.pop()
    return "\n".join(lines) + "\n" if lines else ""


def _comment(text):
    """The comment a C file opens with, its text (_uncomment), or None."""
    m = re.match(r"\s*/\*(.*?)\*/", text, re.S)
    return _uncomment(m.group(1)) or None if m else None


# Where cpython_texts() looks for the notices in CPython's C source (which its interpreter and extension modules are
# compiled from) that license.rst does not reproduce; not in HACL*'s tree, whose notices are taken whole, nor in those
# of the libraries python-build-standalone links its own build of instead (expat, libmpdec: LIBRARIES) or which
# license.rst gives a section of their own (mimalloc).
CPYTHON_C = ("Modules/", "Objects/", "Python/", "Parser/", "Include/")
CPYTHON_C_SKIP = ("Modules/_hacl/", "Modules/expat/", "Modules/_decimal/libmpdec/", "Objects/mimalloc/",
                  "Include/internal/mimalloc/")
COPYRIGHT_LINE = re.compile(r"copyright\s*(\(c\)|\d{4})", re.I)          # "Copyright (c) ...", "Copyright 1994 ..."
TERMS = re.compile(r"permission|redistribut|licen[cs]e|provided 'as-is'", re.I)


def c_notices(sources, rst, version):
    """NOTICE.cpython-c.txt: the notices in CPython's C source (`sources`, {path: text} of its .c and .h files) that
    its license.rst (`rst`) does not reproduce, each after the files that hold it. A notice is a comment that states a
    copyright (COPYRIGHT_LINE) and terms of use (TERMS), but not one licensed to the PSF (under Python's own license),
    nor one each of whose copyright lines license.rst holds; it is taken from its first copyright line on."""
    held, found = " ".join(rst.split()).lower(), {}
    for path, text in sorted(sources.items()):
        for m in re.finditer(r"^[ \t]*/\*(.*?)\*/", text, re.M | re.S):
            lines = _uncomment(m.group(1)).splitlines()
            rights = [i for i, line in enumerate(lines) if COPYRIGHT_LINE.search(line)]
            body = "\n".join(lines)
            if not rights or not TERMS.search(body) or "licensed to psf" in body.lower() \
                    or all(" ".join(lines[i].split()).lower() in held for i in rights):
                continue
            found.setdefault("\n".join(lines[rights[0]:]), []).append(path)
    out = textwrap.wrap(
        f"The notices in CPython {version}'s C source, which its interpreter and extension modules are compiled "
        "from, that its license and acknowledgements (Doc/license.rst) do not reproduce: the comments in its .c and .h "
        f"files under {', '.join(CPYTHON_C)} that state a copyright and the terms of a use, but for those licensed to "
        "the PSF (under Python's own license) and those whose copyright lines license.rst holds. The trees of "
        f"{', '.join(CPYTHON_C_SKIP)} are left to their own notices. Each is given after the files that hold it, from "
        "its first copyright line on. Written by desktop/licenses.py python-sources.", 116, break_on_hyphens=False)
    for text, paths in sorted(found.items(), key=lambda kv: kv[1]):
        out += ["", *textwrap.wrap("In " + ", ".join(paths) + ":", 116, break_on_hyphens=False), "", text]
    return "\n".join(out) + "\n"


def cpython_texts(path, version):
    """CPython's own license texts, from its source archive at `path`: Doc/license.rst (its license, and its
    acknowledgements for the software it incorporates), the notices in its C source that license.rst does not
    reproduce (c_notices), and those of HACL*, which its hash modules are built from and which license.rst does not
    name: the MIT License its C files open with, and the notice of KaRaMeL's headers, which they include (under the
    Apache License 2.0). Stops unless it holds license.rst and HACL*'s files say one of each."""
    top, license, mit_heads, krml, sources = f"Python-{version}/", None, set(), set(), {}
    with tarfile.open(path) as tar:
        for member in tar:
            rel = member.name[len(top):] if member.name.startswith(top) else ""
            if not member.isfile() or not rel:
                continue
            if rel == "Doc/license.rst":
                license = tar.extractfile(member).read()
            elif rel.startswith("Modules/_hacl/") and rel.endswith((".c", ".h")):
                head = _comment(_decode(tar.extractfile(member).read())) or ""
                if head.startswith("MIT License"):
                    mit_heads.add(head)
                elif "Licensed under the Apache 2.0 License" in head:
                    krml.add(head)
            elif rel.startswith(CPYTHON_C) and not rel.startswith(CPYTHON_C_SKIP) and rel.endswith((".c", ".h")):
                sources[rel] = _decode(tar.extractfile(member).read())
    if license is None:
        raise SystemExit(f"python-sources: {path} holds no {top}Doc/license.rst")
    if len(mit_heads) != 1 or len(krml) != 1:
        raise SystemExit(f"python-sources: {path}'s HACL* files do not say one MIT License and one KaRaMeL notice")
    return {"license.rst": license, "NOTICE.cpython-c.txt": c_notices(sources, _decode(license), version).encode(),
            "LICENSE.hacl.txt": mit_heads.pop().encode(), "NOTICE.krml.txt": krml.pop().encode()}


VERSION_TEXT = rb"OpenSSL \d+\.\d+\.\d+[a-z]? +\d+ [A-Z][a-z]{2} \d{4}"     # as libcrypto carries it


def _read_full_archive(path):
    """PYTHON.json, the licenses/ folder and the OpenSSL version text from a full archive (.tar.zst, through zstd)."""
    zstd = shutil.which("zstd")
    if not zstd:
        raise SystemExit("python-sources reads python-build-standalone's full archives, which need zstd "
                         "(brew install zstd)")
    proc = subprocess.Popen([zstd, "-dc", path], stdout=subprocess.PIPE)
    info, held, openssl = None, {}, set()
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
            for member in tar:
                if not member.isfile():
                    continue
                if member.name == "python/PYTHON.json":
                    info = json.load(tar.extractfile(member))
                elif member.name.startswith("python/licenses/") and member.name.count("/") == 2:
                    held[os.path.basename(member.name)] = tar.extractfile(member).read()
                elif member.name == "python/build/lib/libcrypto.a":
                    data = tar.extractfile(member).read()
                    openssl |= {m.decode() for m in re.findall(VERSION_TEXT, data)}
        proc.stdout.read()                          # what tar leaves of the stream, so zstd can finish
    except (tarfile.TarError, ValueError):
        info = None
        proc.kill()
    finally:
        proc.stdout.close()
    if proc.wait() != 0 or info is None:
        raise SystemExit(f"{path}: not a full archive zstd and tar could read")
    if len(openssl) > 1:
        raise SystemExit(f"{path}: its libcrypto names more than one OpenSSL ({sorted(openssl)})")
    return info, held, (openssl.pop() if openssl else None)


def python_sources(folder=PYTHON_SOURCES):
    """Rewrite `folder` from the full archives of bundle.py's pinned release, one for each pinned build, each checked
    against the release's own SHA256SUMS, and from CPython's own source, checked against bundle.CPYTHON_SOURCE."""
    sys.path.insert(0, HERE)
    import bundle
    base = f"https://github.com/astral-sh/python-build-standalone/releases/download/{bundle.RELEASE}"
    with urllib.request.urlopen(f"{base}/SHA256SUMS", timeout=60) as resp:
        lines = [line.split(None, 1) for line in resp.read().decode().splitlines() if line.strip()]
    sums = {name.strip().lstrip("*"): digest for digest, name in lines}
    builds = {}
    for triple, _digest in sorted(bundle.BUILDS.values()):
        name = f"cpython-{bundle.PYTHON}+{bundle.RELEASE}-{triple}-pgo+lto-full.tar.zst"
        if name not in sums:
            raise SystemExit(f"the release's SHA256SUMS has no {name}")
        path = bundle.download(f"{base}/{urllib.parse.quote(name)}", name, sums[name])
        info, held, openssl = _read_full_archive(path)
        builds[triple] = {"archive": name, "sha256": sums[name], "python_json": info, "licenses": held,
                          "openssl": openssl}
    name = f"Python-{bundle.PYTHON}.tar.xz"
    url = f"https://www.python.org/ftp/python/{bundle.PYTHON}/{name}"
    cpython = {"archive": name, "sha256": bundle.CPYTHON_SOURCE, "url": url,
               "texts": cpython_texts(bundle.download(url, name, bundle.CPYTHON_SOURCE), bundle.PYTHON)}
    record, files = sources_record(bundle.RELEASE, bundle.PYTHON, builds, cpython)
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder)
    for name, data in files.items():
        with open(os.path.join(folder, name), "wb") as fh:
            fh.write(data)
    with open(os.path.join(folder, "sources.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return folder


# --- the Rust crates ---

def cargo_metadata(src, targets, offline=False, run=subprocess.run):
    """`cargo metadata` for the crate in `src`, its dependencies only those for `targets`, from its Cargo.lock as it
    is (--locked)."""
    args = ["cargo", "metadata", "--format-version", "1", "--locked", *(["--offline"] if offline else [])]
    for target in targets:
        args += ["--filter-platform", target]
    return json.loads(run(args, cwd=src, stdout=subprocess.PIPE, text=True, check=True).stdout)


def linked(meta, src, targets, offline=False, run=subprocess.run):
    """(packages, problems): the packages whose code the crate in `src` links for `targets`, as cargo's own resolver
    builds them: `cargo tree` for each target, normal dependencies only and no proc-macro crate (run by the compiler,
    never linked). `cargo metadata` alone also counts what only build scripts and proc-macros use, which it merges in."""
    by_version = {}
    for package in meta["packages"]:
        by_version.setdefault((package["name"], package["version"]), []).append(package)
    found, problems = {}, []
    for target in targets:
        args = ["cargo", "tree", "--locked", *(["--offline"] if offline else []), "-e", "normal,no-proc-macro",
                "--target", target, "--prefix", "none", "--format", "{p}"]
        for line in run(args, cwd=src, stdout=subprocess.PIPE, text=True, check=True).stdout.splitlines():
            line = line.strip().removesuffix(" (*)")              # (*): named again, its dependencies above
            if not line:
                continue
            m = re.fullmatch(r"(\S+) v(\S+)( \(.*\))?", line)     # name, version, and its source unless crates.io
            same = by_version.get((m[1], m[2]), []) if m else []
            if len(same) != 1:
                problems.append(f"cargo tree names {line!r}, which cargo metadata does not name once")
            elif same[0]["id"] != meta["resolve"]["root"]:
                found[same[0]["id"]] = same[0]
    return list(found.values()), problems


def _repository(url):
    return (url or "").strip().lower().rstrip("/").removesuffix(".git")


# source files named like licenses, which are not (objc2-foundation's src/copying.rs)
CODE = frozenset({"asm", "c", "cc", "cpp", "go", "h", "hpp", "java", "js", "json", "kt", "lock", "m", "mm", "py", "pyi",
                  "rs", "s", "sh", "swift", "toml", "ts"})


def _license_name(name):
    """Whether a file's name says it holds a license or a notice (LICENSE, COPYING, NOTICE and their variants)."""
    return bool(LICENSE_FILE.match(name)) and (name.rsplit(".", 1)[1].lower() if "." in name else "") not in CODE


def license_files(top, license_file=None):
    """The license files in `top` (a crate or a source tree as published), each (its path from there, its text): every
    file there or below whose name says so (regex-syntax's Unicode tables have theirs in src/unicode_tables/), and the
    one its Cargo.toml names (license-file) wherever that is inside it; those at the top first."""
    found = []
    for root, dirs, files in os.walk(top):
        dirs.sort()
        for name in files:
            path = os.path.join(root, name)
            if _license_name(name) and os.path.isfile(path) and not os.path.islink(path):
                found.append(os.path.relpath(path, top).replace(os.sep, "/"))
    named = (license_file or "").replace("\\", "/")
    if named and _inside(named) and named not in found and os.path.isfile(os.path.join(top, named)):
        found.append(named)
    return [(rel, _text(os.path.join(top, rel))) for rel in sorted(found, key=lambda rel: (rel.count("/"), rel))]


def crate_files(package):
    """The license files a crate in the cargo registry ships (license_files)."""
    return license_files(os.path.dirname(package["manifest_path"]), package.get("license_file"))


def supplied(package, shipped, texts, named="Its Cargo.toml", kept=None):
    """For a crate that ships no license file, (note, files) from what its Cargo.toml declares, or None:
    1. the files of a crate in the same build published from the same repository under the same license;
    2. the text of a license it offers that names no holder: the Apache License 2.0 (LICENSE-APACHE-2.0 beside
       Proofpane Community's LICENSE) or the MPL 2.0 (third-party/MPL-2.0.txt);
    3. its repository's own license files at the commit it was published from, as rust-sources keeps them: `kept`,
       (its repository, the commit, the files), or None; for MIT, if they name the license without holding its text,
       the MIT License's text too, with no copyright line (MIT_TEXT).
    A license whose text names its holder (MIT, BSD) is never given one made up here. `shipped` is {package id:
    (package, its files)} for every crate in the build, `texts` {"Apache-2.0": text, "MPL-2.0": text}; `named` says
    what names its license."""
    license, repository = package.get("license") or "", _repository(package.get("repository"))
    if not license:
        return None
    for pkg, files in sorted(shipped.values(), key=lambda pf: (pf[0]["name"], pf[0]["version"])):
        if files and pkg["id"] != package["id"] and repository and _repository(pkg.get("repository")) == repository \
                and pkg.get("license") == license:
            return (f"(It ships no license file. {pkg['name']} {pkg['version']}, published from the same repository "
                    f"({package['repository']}) under the same license, ships the files below.)", files)
    for name, where in (("Apache-2.0", "LICENSE-APACHE-2.0 beside Proofpane Community's LICENSE holds it"),
                        ("MPL-2.0", "cssparser 0.37.0 ships it")):
        if allowed(license, {name}):
            return (f"(It ships no license file. {named} names {license}; below is the text of {name}, which it "
                    f"offers and which names no holder, as {where}.)", [(name, texts[name])])
    if kept:
        address, commit, files = kept
        note = (f"(It ships no license file. {named} names {license}; below are the license files at the top of its "
                f"repository, {address}, at the commit it was published from, {commit} (its .cargo_vcs_info.json), "
                "as desktop/third-party/rust/ keeps them")
        if allowed(license, {"MIT"}) and not any("Permission is hereby granted" in " ".join(text.split())
                                                 for _name, text in files):
            return (note + ", then the MIT License's text, which they name and do not hold. Neither the crate nor its "
                    "repository names a copyright holder, and none is added.)", [*files, ("MIT", MIT_TEXT)])
        return note + ".)", files
    return None


GITHUB = re.compile(r"https://github\.com/([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9_.-]{1,100}?)(?:\.git)?/?")
COMMIT = re.compile(r"[0-9a-f]{40}")


def github(repository):
    """(owner, name) of a repository on GitHub, by its address, or None."""
    m = GITHUB.fullmatch((repository or "").strip())
    return (m[1], m[2]) if m and m[2] not in (".", "..") else None


def commit_of(package):
    """The commit a crate in the cargo registry was published from, as its .cargo_vcs_info.json says, or None."""
    try:
        with open(os.path.join(os.path.dirname(package["manifest_path"]), ".cargo_vcs_info.json"),
                  encoding="utf-8") as fh:
            sha = json.load(fh)["git"]["sha1"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return sha if isinstance(sha, str) and COMMIT.fullmatch(sha) else None


def kept_files(repository, commit, folder=RUST_SOURCES):
    """(files, problems): the license files at the top of `repository` at `commit` that rust-sources keeps in
    `folder`, each (its name, its text), or None when it keeps none; a file that is not the one it wrote is a problem."""
    where, record = github(repository), os.path.join(folder, "sources.json")
    if not where or not os.path.isfile(record):
        return None, []
    with open(record, encoding="utf-8") as fh:
        kept = json.load(fh).get("repositories", {}).get("/".join(where), {}).get(commit)
    if kept is None:
        return None, []
    files, problems = [], []
    for name, digest in sorted(kept.get("files", {}).items()):
        path, data = os.path.join(folder, *where, commit, name), None
        if _license_name(name) and os.path.isfile(path):          # a name it writes: no folder in it
            with open(path, "rb") as fh:
                data = fh.read()
        if data is None or _sha256(data) != digest:
            problems.append(f"desktop/third-party/rust/{'/'.join(where)}/{commit}/{name} is not the file rust-sources "
                            "wrote")
        else:
            files.append((name, _decode(data)))
    return files, problems


def rust_notice(src, targets, offline=False, run=subprocess.run, kept=RUST_SOURCES):
    """(text, problems): the notice for the Rust crates the app built from `src` links for `targets`; `kept` is the
    folder rust-sources keeps crates' repositories' license files in."""
    meta = cargo_metadata(src, targets, offline=offline, run=run)
    root = next(p for p in meta["packages"] if p["id"] == meta["resolve"]["root"])
    crates, problems = linked(meta, src, targets, offline=offline, run=run)
    crates.sort(key=lambda p: (p["name"], p["version"]))
    shipped = {p["id"]: (p, crate_files(p)) for p in crates}
    with open(APACHE_TEXT, encoding="utf-8") as fh:
        apache = fh.read()
    with open(MPL_TEXT, encoding="utf-8") as fh:
        texts = {"Apache-2.0": apache, "MPL-2.0": fh.read()}
    entries = []
    for package in crates:
        who, license = f"{package['name']} {package['version']}", package.get("license") or ""
        files, notes = shipped[package["id"]][1], []
        if not license:
            problems.append(f"{who}: its Cargo.toml names no license"
                            + (f", only the file {package['license_file']}" if package.get("license_file") else ""))
        elif not allowed(license):
            if not allowed(license, ALLOWED | SOURCE_NAMED):
                problems.append(f"{who}: its license, {license}, is not one the app takes")
            elif package.get("source") != CRATES_IO:
                problems.append(f"{who}: its license, {license}, asks that the app say where its source is, and it "
                                "is not from crates.io")
            else:
                notes.append(f"Its source: https://crates.io/crates/{package['name']}/{package['version']}")
        if not files:
            commit = commit_of(package)
            found, more = kept_files(package.get("repository"), commit, kept)
            problems += [f"{who}: {p}" for p in more]
            supply = supplied(package, shipped, texts, kept=(package["repository"], commit, found) if found else None)
            if supply is None:
                problems.append(f"{who}: it ships no license file, and none can be supplied for it" + (
                    " (python3 desktop/licenses.py rust-sources keeps its repository's, at the commit it was published "
                    "from)" if github(package.get("repository")) and commit and not more else ""))
            else:
                notes.insert(0, supply[0])
                files = supply[1]
        entries.append({"title": f"{who} ({license or 'no license named'})", "who": who, "notes": notes,
                        "files": files})
    title = "Third-party software in this build of Proofpane Community's app: its Rust crates"
    about = (
        "Proofpane Community is open source under the Apache License 2.0 (its LICENSE and NOTICE files). The "
        f"app's shell ({root['name']} {root['version']}) is built from the Rust crates below, each under its own "
        f"license: every crate whose code it links for {', '.join(targets)}, as cargo's resolver builds it, named with "
        "its version and the license its Cargo.toml gives, then the license files it ships, as it ships them; for a "
        "crate that ships none, its note says which files it is given and where they come from. Written "
        "by the build (desktop/licenses.py) from `cargo tree` (normal dependencies, no proc-macro crate), `cargo "
        "metadata` and the crates' own files. The Rust standard library, which every "
        f"Rust program carries, is not a crate here: its notices are beside this file ({RUST_STD_FILE}), as the "
        "toolchain that built the app ships them."
    )
    return notice(title, about, entries), problems


def rust_std(src, run=subprocess.run):
    """(data, problems): the Rust standard library's copyright notices, as the toolchain that builds the crate in `src`
    ships them (share/doc/rust/COPYRIGHT-library.html): every Rust program carries the standard library."""
    sysroot = run(["rustc", "--print", "sysroot"], cwd=src, stdout=subprocess.PIPE, text=True,
                  check=True).stdout.strip()
    path = os.path.join(sysroot, "share", "doc", "rust", "COPYRIGHT-library.html")
    if not os.path.isfile(path):
        return None, [f"the Rust toolchain {sysroot} ships no share/doc/rust/COPYRIGHT-library.html, the standard "
                      "library's notices"]
    with open(path, "rb") as fh:
        return fh.read(), []


# --- the license files of crates' repositories, kept for the crates that ship none ---

# the apps whose crates rust-sources reads: each one's src-tauri, and the targets its shell is built for (bundle.py's
# BUILDS, the phone's beforeBuildCommand)
APPS = (("desktop/src-tauri", ("aarch64-apple-darwin", "x86_64-apple-darwin")),
        ("phone/src-tauri", ("aarch64-apple-ios",)))


def git_blob(data):
    """The id git gives a file's content (its blob's SHA-1), by which a commit's tree names it."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def git_files(repo, commit, url=None, run=subprocess.run):
    """The license files at the top of a repository on GitHub (`repo`, "owner/name") at `commit`: {name: (its blob
    id, its bytes)}. Fetched with git from `url` (GitHub's, by default): the commit and its trees, then the contents of
    those files only; git checks each object against its id, so what is read is the commit's."""
    with tempfile.TemporaryDirectory() as tmp:
        def git(*args):
            return run(["git", "-C", tmp, *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       env=dict(os.environ, GIT_TERMINAL_PROMPT="0")).stdout

        git("init", "-q")
        git("remote", "add", "origin", url or f"https://github.com/{repo}")
        # what lets git fetch a missing file's content when it is read; git 2.43 sets it on a filtered fetch itself
        git("config", "remote.origin.promisor", "true")
        git("config", "remote.origin.partialclonefilter", "blob:none")
        git("fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", commit)
        found = {}
        for entry in git("ls-tree", "-z", commit).split(b"\0"):
            if not entry:
                continue
            info, name = entry.split(b"\t", 1)
            mode, kind, blob = info.decode().split()
            name = name.decode("utf-8", "replace")
            if kind == "blob" and mode in ("100644", "100755") and _license_name(name):
                found[name] = (blob, git("cat-file", "blob", blob))
        return found


def rust_sources(folder=RUST_SOURCES, apps=APPS, files_at=git_files, offline=False, run=subprocess.run):
    """Rewrite `folder` (desktop/third-party/rust/) from GitHub. For each crate an app links (APPS) that ships no
    license file and to which supplied() can give none of what it has without them (a crate from its repository under
    the same license, a license it offers that names no holder): the license files at the top of its repository at
    the commit it was published from (its .cargo_vcs_info.json), as `files_at` (git_files) reads them, each checked
    against the id the commit's tree gives it. Stops, writing nothing, on a crate that names no repository on GitHub
    and commit, or whose repository holds no such file there."""
    with open(MPL_TEXT, encoding="utf-8") as fh:
        texts = {"Apache-2.0": _apache(), "MPL-2.0": fh.read()}
    wanted, problems = {}, []
    for src, targets in apps:
        src = os.path.join(OPEN_EDITION, src)
        crates, more = linked(cargo_metadata(src, targets, offline=offline, run=run), src, targets, offline=offline,
                              run=run)
        problems += more
        shipped = {p["id"]: (p, crate_files(p)) for p in crates}
        for package, files in shipped.values():
            if files or supplied(package, shipped, texts) is not None:
                continue
            who, where, commit = f"{package['name']} {package['version']}", github(package.get("repository")), \
                commit_of(package)
            if where and commit:
                wanted.setdefault(("/".join(where), commit), set()).add(who)
            else:
                problems.append(f"{who}: it ships no license file, and names no repository on GitHub and commit it "
                                "was published from to take its repository's from")
    record, files = {"repositories": {}}, {}
    for (repo, commit), crates in sorted(wanted.items()):
        found = files_at(repo, commit)
        if not found:
            problems.append(f"{repo} at {commit}: its top folder holds no license file (for "
                            f"{', '.join(sorted(crates))})")
        kept = {}
        for name, (blob, data) in sorted(found.items()):
            if not _license_name(name) or git_blob(data) != blob:      # a license file's name has no folder in it
                problems.append(f"{repo} at {commit}: {name} is not the file its tree names ({blob})")
                continue
            files[(repo, commit, name)] = data
            kept[name] = _sha256(data)
        record["repositories"].setdefault(repo, {})[commit] = {"crates": sorted(crates), "files": kept}
    if problems:
        raise SystemExit("rust-sources:\n  " + "\n  ".join(problems))
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder)
    for (repo, commit, name), data in sorted(files.items()):
        os.makedirs(os.path.join(folder, *repo.split("/"), commit), exist_ok=True)
        with open(os.path.join(folder, *repo.split("/"), commit, name), "wb") as fh:
            fh.write(data)
    with open(os.path.join(folder, "sources.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return folder


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 desktop/licenses.py")
    sub = parser.add_subparsers(dest="command", required=True)
    rust = sub.add_parser("rust", help="the Rust crates an app links, for its targets, and the standard library's")
    rust.add_argument("src", help="the app's src-tauri folder")
    rust.add_argument("out", help=f"the file to write; {RUST_STD_FILE} is written beside it")
    rust.add_argument("--target", action="append", required=True, help="a target triple the app is built for")
    sub.add_parser("python-sources", help="rewrite desktop/third-party/python/ from the pinned release")
    sub.add_parser("rust-sources", help="rewrite desktop/third-party/rust/ from GitHub")
    args = parser.parse_args(argv)
    if args.command == "python-sources":
        print(python_sources())
        return 0
    if args.command == "rust-sources":
        print(rust_sources())
        return 0
    text, problems = rust_notice(args.src, args.target)
    std, more = rust_std(args.src)
    if problems + more:
        print("third-party licenses the app cannot carry:\n  " + "\n  ".join(problems + more), file=sys.stderr)
        return 1
    folder = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(folder, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    with open(os.path.join(folder, RUST_STD_FILE), "wb") as fh:
        fh.write(std)
    print(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
