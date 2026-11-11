"""The three tools of the hosted edition, each a thin call into the open packages.

- verify: a title, a description and a git diff in; the Verification Manifest v0 that `verify_pr` builds out, with
  its re-derived decision, and this deployment's signature on it.
- check_manifest / check_chain: the offline verifier's own functions (`proofpane_verify`), run on the uploaded text.
- check_signature: `proofpane_sign.document.verify_document` against this deployment's public key.

Nothing here decides an outcome itself: the decisions are the open packages', so what a user sees here is what the
same packages print on their own machine.
"""

import base64
import os
import tempfile

from . import config, diff, paths, signing  # noqa: F401  (paths puts the open packages on sys.path)
from proofpane_core import manifest as core_manifest
from proofpane_sign import document
from proofpane_verify.__main__ import check_chain as _check_chain
from proofpane_verify.__main__ import check_manifest as _check_manifest
from verify_pr.config import load_config
from verify_pr.verify import verify as _verify

MANIFEST_PURPOSE = "verification-manifest"
NO_COMMIT = "0" * 40  # git's null object id: a pasted diff has no commits
PASTED_REPO = "pasted/diff"


class ToolError(ValueError):
    pass


def _bounded(value, limit, what):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ToolError(f"The {what} must be text.")
    if len(value.encode("utf-8")) > limit:
        raise ToolError(f"The {what} is longer than {limit // 1024} KB.")
    return value


def verify(title, body, diff_text, repo=None, base_sha=None, head_sha=None, check_runs=None, statuses=None,
           verify_config=None):
    """`repo` (owner/name) and the two commits are optional: a pasted diff has none, and the manifest then records
    the repository as `pasted/diff` and each commit as 40 zeros, git's null object id, rather than inventing them.
    `check_runs` and `statuses` are the head commit's CI as GitHub reports it, and `verify_config` a repository's Verify
    settings (its expected checks); the ADE passes them for a commit, the pasted-diff page has none."""
    title = _bounded(title, config.MAX_TEXT, "title")
    body = _bounded(body, config.MAX_TEXT, "description")
    diff_text = _bounded(diff_text, config.MAX_DIFF, "diff")
    try:
        files = diff.parse(diff_text or "")
    except diff.DiffError as e:
        raise ToolError(str(e)) from None
    bundle = {"repo": repo or PASTED_REPO, "base_sha": base_sha or NO_COMMIT, "head_sha": head_sha or NO_COMMIT,
              "title": title or "", "body": body or "",
              "changed_files": len(files), "files": files, "check_runs": list(check_runs or []),
              "statuses": list(statuses or []), "reviews": []}
    try:
        m = _verify(bundle, load_config(overrides=verify_config), cfg_source=None)
    except ValueError as e:
        raise ToolError(str(e)) from None
    signature = document.sign_document(m, signing.private_key(), MANIFEST_PURPOSE)
    return {"manifest": m, "digest": core_manifest.manifest_digest(m),
            "decision": core_manifest.rederived_decision(m), "signature": signature}


def _through_file(text, check, **kw):
    text = _bounded(text, config.MAX_EVIDENCE, "file")
    if not text:
        raise ToolError("The file is empty.")
    fd, path = tempfile.mkstemp(prefix="upload-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        result = check(path, **kw)
    finally:
        os.unlink(path)
    # the checker names the file it read; the temporary path means nothing to the user
    result["errors"] = [e.replace(path, "the file") for e in result.get("errors", [])]
    return result


def check_manifest(text):
    return _through_file(text, _check_manifest)


def check_chain(text):
    return _through_file(text, _check_chain)


def check_signature(value, signature, purpose=MANIFEST_PURPOSE):
    return {"ok": document.verify_document(value, signature, signing.public_key(), purpose),
            "key": document.key_id(signing.public_key())}


def public_key():
    pk = signing.public_key()
    return {"alg": "id-MLDSA65-Ed25519-SHA512", "key": document.key_id(pk),
            "public_key": base64.b64encode(pk).decode("ascii")}
