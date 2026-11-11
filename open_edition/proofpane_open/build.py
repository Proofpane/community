"""What this build is: its release (such as 58a) and the commit its image was built from, written into the image as it
is built and never read from git as it runs (an image holds no repository). GET /api/health says both, and the page
shows them under the account's address.

The Dockerfile takes them as build arguments, OPEN_EDITION_RELEASE and OPEN_EDITION_COMMIT, and keeps them here with
`python build.py RELEASE COMMIT`. Either one not given (a build without the argument, whose default is "unknown", or a
run from the source tree) is "unknown", and so is anything kept that is not of its shape: a release is 1 to 4 digits
and at most one lower-case letter, a commit 7 to 64 lower-case hexadecimal digits. A value given that is not of its
shape stops the build, and so does an empty one, which is what a `$(git rev-parse HEAD)` that failed gives.
"""

import json
import os
import re
import stat
import sys

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build.json")
UNKNOWN = "unknown"
MAX_BYTES = 4096                     # two short values; a longer file is not one this module wrote
_SHAPES = {"release": re.compile(r"[0-9]{1,4}[a-z]?"),      # each: fullmatch, never match
           "commit": re.compile(r"[0-9a-f]{7,64}")}


def info(path=None):
    """{release, commit} as the build kept them, each "unknown" when it kept none or one not of its shape. It answers
    whatever is at the path: anything but a regular file (a folder, a pipe, which is opened without waiting for a
    writer), a file over MAX_BYTES, or one that cannot be read or parsed (nested too deep among them) kept nothing."""
    kept = None
    try:
        fd = os.open(path or PATH, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        try:
            if stat.S_ISREG(os.fstat(fd).st_mode):
                raw = os.read(fd, MAX_BYTES + 1)
                if len(raw) <= MAX_BYTES:
                    kept = json.loads(raw.decode("utf-8"))
        finally:
            os.close(fd)
    except Exception:  # noqa: BLE001  a health check answers, whatever the file holds
        kept = None
    if not isinstance(kept, dict):
        kept = {}
    return {k: v if isinstance(v, str) and shape.fullmatch(v) else UNKNOWN
            for k, shape in _SHAPES.items() for v in (kept.get(k),)}


def write(release, commit, path=None):
    """Keep `release` and `commit` for info(). "unknown", the build argument's default when it is not given, is kept as
    it is; any other value not of its shape, an empty one among them, is refused (ValueError), so a build given a wrong
    one stops. What it kept."""
    out = {}
    for k, v in (("release", release), ("commit", commit)):
        if v == UNKNOWN:
            out[k] = UNKNOWN
        elif _SHAPES[k].fullmatch(v):
            out[k] = v
        else:
            raise ValueError(f"OPEN_EDITION_{k.upper()} is not a {k}: {v[:80]!r}")
    with open(path or PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh)
    return out


if __name__ == "__main__":
    # python build.py RELEASE COMMIT [PATH]: the Dockerfile's step; PATH, where to keep them, is for the tests
    if len(sys.argv) not in (3, 4):
        sys.exit("usage: build.py RELEASE COMMIT [PATH]")
    try:
        print("build:", write(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) == 4 else None))
    except ValueError as e:
        sys.exit(f"build: {e}")
