"""Run every test suite under oss/, each in its own interpreter so their test modules cannot shadow each other.

    python3 oss/run_tests.py

Standard library only, except the sign suite, which needs pyca/cryptography 50 or later and fails without it.
Exits non-zero if any suite fails.
"""

import os
import subprocess
import sys

OSS = os.path.dirname(os.path.abspath(__file__))
SUITES = ["core", "verify", "verify-pr", "sign"]


def main():
    failed = []
    for suite in SUITES:
        tests = os.path.join(OSS, suite, "tests")
        proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", tests],
                              cwd=os.path.join(OSS, suite), capture_output=True, text=True)
        tail = [ln for ln in proc.stderr.strip().splitlines() if ln.startswith(("Ran ", "OK", "FAILED"))]
        print(f"{suite:10s} {' '.join(tail)}")
        if proc.returncode:
            failed.append(suite)
            print(proc.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
