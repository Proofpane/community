"""Put the open packages on sys.path when they are not installed (they have no installed package yet).

In the container they sit at OSS_ROOT (default /app/oss); in the source tree, at ../../oss from here.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
OSS_ROOT = os.environ.get("OPEN_EDITION_OSS_ROOT") or os.path.normpath(os.path.join(_HERE, "..", "..", "oss"))
PACKAGES = ("core", "verify", "verify-pr", "sign")


def install():
    for name in PACKAGES:
        path = os.path.join(OSS_ROOT, name)
        if not os.path.isdir(path):
            raise RuntimeError(f"the open package directory {path} does not exist")
        if path not in sys.path:
            sys.path.insert(0, path)


install()
