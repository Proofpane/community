import os
import sys

CORE = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
VECTORS = os.path.join(CORE, "vectors")
if CORE not in sys.path:
    sys.path.insert(0, CORE)
