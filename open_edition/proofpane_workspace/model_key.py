"""Claude Code's `apiKeyHelper` for a session on another provider's model (providers.py): prints the key kept for the
provider named, and nothing else; ends 1, saying nothing, when there is none, which Claude Code reports as a failing
helper.

    python model_key.py <provider>
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import providers  # noqa: E402  (beside this file; the package itself is not on the path where Claude Code runs this)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    key = providers.keys(os.path.expanduser("~")).get(argv[0]) if len(argv) == 1 else None
    if not key:
        return 1
    sys.stdout.write(key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
