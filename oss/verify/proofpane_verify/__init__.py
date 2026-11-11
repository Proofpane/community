"""proofpane-verify: check Proofpane artifacts offline.

v0.1 checks Verification Manifests: well formed, decision re-derived from the listed obligations, digest. It does
not show that the obligations are complete or their outcomes right (see __main__). Evidence bundles (v1 packs and
v2 local bundles) come later.
"""

import os
import sys

VERSION = "0.1.0.dev0"

# In the source tree the core library sits beside this package. There is no installed package yet; once there
# is, the import succeeds and nothing is added to the path.
try:
    import proofpane_core  # noqa: F401
except ImportError:  # pragma: no cover - exercised only when run from the source tree
    sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "core")))
