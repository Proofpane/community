"""Proofpane Verify for pull requests: check, claim by claim, what a pull request says about its own change.

Offline and deterministic. It reads a pull request "bundle" (JSON: title, description, changed files with patches,
check runs, reviews, referenced issues) and a per-repository configuration, and produces a Verification Manifest v0
(`proofpane_core.manifest`) with its two check results. No model output sets any outcome.

The fetcher writes issue records in which `exists` is true only for an issue, and keeps a failed lookup out of
`issues`. From 0.1.0.dev0 it builds manifests through proofpane-core. The secret and PII rules are still a small
stand-in for a fuller catalogue.
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
