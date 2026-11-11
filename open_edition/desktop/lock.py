"""The desktop app's Python packages, held to what its build was tested with (desktop/requirements.lock).

The lock names each package at one version, with the sha256 of every file PyPI has for that version (its wheels for
every platform, and its sdist), so pip installs those files and no others (bundle.py: --require-hashes --no-deps,
wheels only but for cryptography on an Intel Mac, built from its sdist). This rewrites the hashes from the versions
the lock names: to move a package, change its version in the lock, run this, build, and test the app.

    python3 desktop/lock.py             rewrite desktop/requirements.lock's hashes from PyPI
    python3 desktop/lock.py --check     only check that every package has hashes and the server's and the agent's
                                        requirements are in it

Standard library only.
"""

import argparse
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LOCK = os.path.join(HERE, "requirements.lock")
OPEN_EDITION = os.path.dirname(HERE)
REQUIREMENTS = (os.path.join(OPEN_EDITION, "requirements.txt"),
                os.path.join(OPEN_EDITION, "proofpane_workspace", "requirements.txt"))
HEADER = """\
# Proofpane Community's desktop app: its Python packages, each pinned to the version its build was tested with and
# held to the files PyPI has for that version, by their sha256 (desktop/lock.py writes the hashes; to move a package,
# change its version here, run it, build and test the app). bundle.py installs exactly these, with --require-hashes.
"""
_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\;]+)")


def normalise(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def read_pins(path=LOCK):
    """{normalised name: (name as written, version)} from the lock's `name==version` lines."""
    pins = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = _PIN.match(line.strip())
            if m:
                pins[normalise(m.group(1))] = (m.group(1), m.group(2))
    return pins


def read_hashes(path=LOCK):
    """{normalised name: [sha256, ...]} as the lock holds them."""
    out, current = {}, None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = _PIN.match(line.strip())
            if m:
                current = normalise(m.group(1))
                out[current] = []
            for digest in re.findall(r"--hash=sha256:([0-9a-f]{64})", line):
                if current:
                    out[current].append(digest)
    return out


def pypi_hashes(name, version):
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=60) as resp:
        files = json.load(resp)["urls"]
    digests = sorted({f["digests"]["sha256"] for f in files if not f.get("yanked")})
    if not digests:
        raise SystemExit(f"PyPI has no files for {name}=={version}")
    return digests


def write(pins, path=LOCK):
    lines = [HEADER]
    for key in sorted(pins):
        name, version = pins[key]
        hashes = pypi_hashes(name, version)
        lines.append(f"{name}=={version} \\\n" + " \\\n".join(f"    --hash=sha256:{h}" for h in hashes) + "\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(lines))


def requirement_names(paths=REQUIREMENTS):
    """The packages the server and the workspace agent ask for, with what they ask: {normalised name: spec}."""
    out = {}
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.split("#", 1)[0].strip()
                m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(.*)$", line)
                if m:
                    out[normalise(m.group(1))] = m.group(2).strip()
    return out


def _version(text):
    return tuple(int(x) for x in re.findall(r"\d+", text)[:4])


def check(path=LOCK, paths=REQUIREMENTS):
    """Every package in the lock has hashes, and every requirement of the server and the agent is in it at a version
    it allows (== the same, >= at least)."""
    pins, hashes, bad = read_pins(path), read_hashes(path), []
    for key, (name, _version_text) in pins.items():
        if not hashes.get(key):
            bad.append(f"{name} has no hash")
    for key, spec in requirement_names(paths).items():
        if key not in pins:
            bad.append(f"{key} is asked for and not in the lock")
            continue
        held = pins[key][1]
        for op, wanted in re.findall(r"(==|>=)\s*([^\s,;]+)", spec):
            if (op == "==" and held != wanted) or (op == ">=" and _version(held) < _version(wanted)):
                bad.append(f"{key} {held} does not meet {op}{wanted}")
    return bad


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 desktop/lock.py")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if not args.check:
        write(read_pins())
    bad = check()
    for b in bad:
        print(b, file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
