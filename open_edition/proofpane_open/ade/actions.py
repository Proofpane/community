"""The ADE: the owner's actions, written back as signed commits.

From the board the owner can answer a question, open, extend or close a window, and stop a channel's sessions or let
them go on. Each is one commit to the channel's branch, made through GitHub's API with the token they connected the
repository with (it needs Contents: read and write), carrying their words and the time, with a `Proofpane-ADE:` trailer
naming this deployment's key. The file it writes (an answers file, a window file or `STOP.md`) is signed over its
exact bytes with the deployment's composite ML-DSA-65 + Ed25519 key, in a file beside it written by the same commit,
`<name>.sig.json`:

    {"statement": {"repo", "branch", "path", "sha256", "signed_at", "by"},
     "signature": {"alg", "purpose", "key", "sig"}}

The statement binds the signature to one file in one repository on one branch, so a signed window cannot be copied
into another channel, branch or repository and still hold. A session acts on a window or an answer only when
`python -m proofpane_open.ade.check` says the signature holds for that file under the published key. That replaces
today's check, that only the cloud session's commits touched a window file, which anyone who can push to the branch
can forge. A `STOP.md` is honoured whether or not it is signed: stopping is always the safe direction.

This module builds and checks the texts and signatures; it fetches and writes nothing. `routes.py` reads the files
at the branch's head and hands the result to `sources.GitHub.commit_files`.
"""

import base64
import hashlib
import json
import re

from .. import paths  # noqa: F401  (paths puts the open packages on sys.path)
from proofpane_sign import document

from . import channel
from .commits import ADE_TRAILER

PURPOSES = {"window": "ade.window", "answers": "ade.answers", "stop": "ade.stop"}
STATEMENT_KEYS = ("repo", "branch", "path", "sha256", "signed_at", "by")
MAX_WORDS = 2000
MAX_NAME = 100
_ONE_LINE = re.compile(r"[^\r\n\x00-\x08\x0b-\x1f\x7f]+")


class ActionError(ValueError):
    """A request the owner can correct: shown as it is."""


def sig_path(path):
    return path + ".sig.json"


def one_line(text, what, limit=MAX_WORDS):
    """`text` stripped, if it is one line of printable text no longer than `limit`."""
    text = text.strip() if isinstance(text, str) else ""
    if not text or len(text) > limit or not _ONE_LINE.fullmatch(text):
        raise ActionError(f"The {what} is one line of text, at most {limit} characters.")
    return text


def utc_minute(dt):
    return dt.strftime("%Y-%m-%dT%H:%MZ")


def statement(repo, branch, path, text, by, now):
    return {"repo": repo, "branch": branch, "path": path,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "signed_at": now.strftime(
                "%Y-%m-%dT%H:%M:%SZ"), "by": by}


def sign_file(private_key, kind, repo, branch, path, text, by, now):
    """The `.sig.json` text for the file `path` whose content is `text`."""
    st = statement(repo, branch, path, text, by, now)
    return json.dumps({"statement": st, "signature": document.sign_document(st, private_key, PURPOSES[kind])},
                      indent=1, sort_keys=True) + "\n"


def check_file(kind, text, sig_text, public_key, repo=None, branch=None, path=None):
    """(True, statement) when `sig_text` signs exactly `text` for `kind` under `public_key`, and names the given
    repository, branch and path (each checked only when given); else (False, the reason)."""
    try:
        wrapper = json.loads(sig_text)
    except (TypeError, ValueError):
        return False, "the signature file is not JSON"
    if not isinstance(wrapper, dict) or set(wrapper) != {"statement", "signature"}:
        return False, "the signature file is not a statement and a signature"
    st = wrapper["statement"]
    if not isinstance(st, dict) or tuple(sorted(st)) != tuple(sorted(STATEMENT_KEYS)):
        return False, "the statement does not have exactly its six fields"
    if not document.verify_document(st, wrapper["signature"], public_key, PURPOSES[kind]):
        return False, "the signature does not verify under this key for this kind of file"
    if st["sha256"] != hashlib.sha256(text.encode("utf-8")).hexdigest():
        return False, "the file was changed after it was signed"
    for key, want in (("repo", repo), ("branch", branch), ("path", path)):
        if want is not None and st[key] != want:
            return False, f"it was signed for {key} {st[key]!r}, not {want!r}"
    return True, st


def trailer(public_key):
    return f"{ADE_TRAILER}: {document.key_id(public_key)}"


def commit_message(subject, words, by, now, public_key):
    """The subject, the owner's words verbatim with who and when, and the trailer that marks the ADE's commits."""
    return (f"{subject}\n\nWritten through the ADE by {by} at {now.strftime('%Y-%m-%dT%H:%M:%SZ')}.\n\n"
            f"{words}\n\n{trailer(public_key)}\n")


def answers_text(current, qid, words):
    """The answers file with `<qid>: <words>` appended. A file that does not exist yet gets a header and the `---`
    line the reader skips to."""
    line = f"{qid}: {words}"
    if current is None:
        return ("# Answers\n\nOne line each, `<id>: <answer>`, written through the ADE. The questions are in the "
                "questions file beside this one.\n\n---\n" + line + "\n")
    return current + ("" if current.endswith("\n") or not current else "\n") + line + "\n"


def window_text(name, start, end, by, now, words):
    """A new window file: its one heading, its one window line, and the record under them."""
    return (f"## GO: {name}\nwindow: {utc_minute(start)} to {utc_minute(end)}\n\n"
            f"Written through the ADE by {by} at {utc_minute(now)}. The record, not instructions.\n\n"
            f"- {words}\n")


def window_changed(current, end, by, now, words, what):
    """An existing window file with only its window line's end changed, and one line appended that says so."""
    w = channel.read_window("GO.md", current)
    if w["problems"]:
        raise ActionError("That window file is not valid, so it is not changed: " + "; ".join(w["problems"]))
    lines = current.split("\n")
    at = next(i for i, line in enumerate(lines) if line.startswith("window: "))  # valid: exactly one such line
    lines[at] = f"window: {w['start']} to {utc_minute(end)}"
    text = "\n".join(lines)
    return text + ("" if text.endswith("\n") else "\n") + (
        f"- {what} through the ADE by {by} at {utc_minute(now)}, to end {utc_minute(end)}: {words}\n")


def stop_text(by, now, words):
    return f"# STOP\n\nWritten through the ADE by {by} at {utc_minute(now)}.\n\n{words}\n"


def public_key_json(public_key):
    """The public key as `/api/public-key` serves it and `check` reads it."""
    return {"alg": "id-MLDSA65-Ed25519-SHA512", "key": document.key_id(public_key),
            "public_key": base64.b64encode(public_key).decode("ascii")}
