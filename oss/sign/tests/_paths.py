import os
import sys

SIGN = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
CORE = os.path.normpath(os.path.join(SIGN, "..", "core"))
VECTORS = os.path.join(SIGN, "vectors")
for path in (SIGN, CORE):
    if path not in sys.path:
        sys.path.insert(0, path)
