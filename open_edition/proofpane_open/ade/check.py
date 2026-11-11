"""The check a session runs before it acts on a window or an answer the ADE wrote:

    python -m proofpane_open.ade.check FILE --key KEY.json --repo owner/name --branch BRANCH --path dir/FILE

FILE is the file in the session's own checkout, and its signature is `FILE.sig.json` beside it. KEY.json is the
deployment's public key as `/api/public-key` serves it, fetched once over HTTPS and kept; `--key-id sha256:...` pins
its id too, so a key file swapped on disk is refused. The kind of file comes from its name (`GO*.md` a window,
`*answers*.md` answers, `STOP.md` a stop) unless `--kind` gives it.

Exit status 0: the signature holds for this file's exact bytes, in this repository, on this branch, at this path,
under this key. 1: it does not, and the line printed says why. 2: the command was used wrongly. Only 0 lets a session
act on a window or an answer. A session needs no check to stop: it stops for any `STOP.md`, signed or not.

Needs the open packages (`oss/sign`, `oss/core`) and pyca `cryptography` 50 or later, which carries ML-DSA.
"""

import argparse
import os
import sys

from .. import paths  # noqa: F401  (paths puts the open packages on sys.path)
from proofpane_sign import document

from . import actions, channel

_KINDS = {"window": "window", "answers": "answers", "stop": "stop"}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m proofpane_open.ade.check")
    parser.add_argument("file")
    parser.add_argument("--key", required=True, help="the deployment's public key, as /api/public-key serves it")
    parser.add_argument("--key-id", help="the key id to insist on, sha256:...")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--path", required=True, help="the file's path in the repository")
    parser.add_argument("--kind", choices=sorted(actions.PURPOSES))
    args = parser.parse_args(argv)
    kind = args.kind or _KINDS.get(channel.role_of(os.path.basename(args.file)))
    if kind is None:
        print("cannot tell what kind of file this is from its name: give --kind", file=sys.stderr)
        return 2
    try:
        public_key = document.read_public_key(args.key)
    except (OSError, ValueError) as e:
        print(f"NOT CHECKED: the key file cannot be read ({e})")
        return 1
    if args.key_id and document.key_id(public_key) != args.key_id:
        print(f"NOT CHECKED: the key file holds {document.key_id(public_key)}, not the pinned {args.key_id}")
        return 1
    try:
        with open(args.file, "rb") as fh:
            data = fh.read()
        with open(args.file + ".sig.json", encoding="utf-8") as fh:
            sig = fh.read()
    except OSError as e:
        print(f"NOT SIGNED: {e.strerror}: {e.filename}")
        return 1
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        print("NOT SIGNED: the file is not UTF-8")
        return 1
    ok, detail = actions.check_file(kind, text, sig, public_key, args.repo, args.branch, args.path)
    if not ok:
        print(f"NOT SIGNED: {detail}")
        return 1
    print(f"signed: {args.path} in {args.repo} on {args.branch}, by {detail['by']} at {detail['signed_at']}, "
          f"key {document.key_id(public_key)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
