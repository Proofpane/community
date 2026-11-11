"""The owner's side of the ADE, in the app's SQLite database: the repositories each account connected, and each
commit's signed Verify result.

A connection belongs to one account and is only ever looked up together with that account's id, so no account reads
another's board, token or results. A token for a private repository is stored sealed with AES-256-GCM under the
key-store key, `OPEN_EDITION_KEYSTORE_KEY` (32 bytes in base64), a secret only this app has; the sealed box names its
account and connection as associated data, so it opens only in its own row. Without that key the app refuses to
store a token and connects public repositories only. No route returns a token, sealed or not.
"""

import base64
import binascii
import json
import os
import secrets
import time
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .. import store
from . import sources

MAX_CONNECTIONS = 20
MAX_DIRS = 12
MAX_TOKEN = 400
MAX_EXPECTED = 30
_BOX = "v1."


class LedgerError(ValueError):
    """A request the owner can correct: shown as it is."""


def keystore_key():
    """The key-store key, or None when the deployment has none. A key that is set but is not 32 bytes of base64
    is an error, not a missing key: a typo in a secret should not quietly turn token storage off."""
    raw = os.environ.get("OPEN_EDITION_KEYSTORE_KEY", "").strip()
    if not raw:
        return None
    try:
        key = base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_" if ("-" in raw or "_" in raw) else None,
                               validate=True)
    except (binascii.Error, ValueError):
        key = b""
    if len(key) != 32:
        raise LedgerError("This deployment's key-store key is not 32 bytes of base64, so tokens cannot be stored.")
    return key


def _aad(user_id, repo_id):
    return f"proofpane-open/ade-token/v1/{user_id}/{repo_id}".encode("utf-8")


def seal(token, user_id, repo_id):
    key = keystore_key()
    if key is None:
        raise LedgerError("This deployment keeps no key-store key, so it cannot store a token: connect a public "
                          "repository without one.")
    nonce = secrets.token_bytes(12)
    box = AESGCM(key).encrypt(nonce, token.encode("utf-8"), _aad(user_id, repo_id))
    return _BOX + base64.b64encode(nonce + box).decode("ascii")


def unseal(box, user_id, repo_id):
    key = keystore_key()
    try:
        if key is None or not box.startswith(_BOX):
            raise ValueError
        raw = base64.b64decode(box[len(_BOX):], validate=True)
        return AESGCM(key).decrypt(raw[:12], raw[12:], _aad(user_id, repo_id)).decode("utf-8")
    except (ValueError, binascii.Error, InvalidTag, UnicodeDecodeError):
        raise LedgerError("The stored token cannot be opened (the key-store key may have changed): remove this "
                          "connection and connect the repository again with the token.") from None


def _row(r):
    return {"id": r["id"], "source": r["source"], "repo": r["repo"], "branch": r["branch"],
            "dirs": json.loads(r["dirs"]), "verify": bool(r["verify"]), "has_token": bool(r["token_box"]),
            "created_at": r["created_at"],
            "verify_config": json.loads(r["verify_config"]) if r["verify_config"] else None}


def _text_fields(entry, required, optional, what):
    if not isinstance(entry, dict) or set(entry) - set(required) - set(optional):
        raise LedgerError(f"An expected {what} has the fields {', '.join(required + optional)}, and no others.")
    for key in required:
        if not isinstance(entry.get(key), str) or not entry[key].strip() or len(entry[key]) > 200:
            raise LedgerError(f"An expected {what} names its {key}.")
    return {k: entry[k] for k in required + optional if k in entry}


def clean_verify_config(cfg):
    """A connection's Verify settings: the checks and statuses a commit's CI must report as passed, in Verify's own
    form (`expected_checks`: name, app, and optionally paths and kind "test"; `expected_statuses`: context and
    creator). None when it names nothing. Anything else is refused, so a typo never passes as a setting."""
    if cfg is None:
        return None
    if not isinstance(cfg, dict) or set(cfg) - {"expected_checks", "expected_statuses"}:
        raise LedgerError("Verify's settings are expected_checks and expected_statuses, nothing else.")
    checks, statuses = cfg.get("expected_checks") or [], cfg.get("expected_statuses") or []
    if not isinstance(checks, list) or not isinstance(statuses, list) or len(checks) + len(statuses) > MAX_EXPECTED:
        raise LedgerError(f"Name at most {MAX_EXPECTED} expected checks and statuses, as lists.")
    out_checks = []
    for e in checks:
        e = _text_fields(e, ["name", "app"], ["paths", "kind"], "check")
        if "paths" in e and (not isinstance(e["paths"], list) or not e["paths"]
                             or not all(isinstance(p, str) and p.strip() for p in e["paths"])):
            raise LedgerError("An expected check's paths are a list of path patterns.")
        if "kind" in e and e["kind"] != "test":
            raise LedgerError('An expected check\'s kind, when given, is "test".')
        out_checks.append(e)
    out_statuses = [_text_fields(e, ["context", "creator"], [], "status") for e in statuses]
    if not out_checks and not out_statuses:
        return None
    return {"expected_checks": out_checks, "expected_statuses": out_statuses}


def set_verify_config(user_id, repo_id, cfg):
    """Replace the Verify settings of one of `user_id`'s connections; None clears them. Returns the connection, or
    None when it is not the account's."""
    cfg = clean_verify_config(cfg)
    with store.connect() as db:
        done = db.execute("UPDATE ade_repos SET verify_config = ? WHERE id = ? AND user_id = ?",
                          (json.dumps(cfg) if cfg else None, repo_id, user_id)).rowcount
    return get(user_id, repo_id) if done else None


def _clean_dirs(dirs):
    if not isinstance(dirs, list) or len(dirs) > MAX_DIRS:
        raise LedgerError(f"Give at most {MAX_DIRS} channel directories.")
    out = []
    for d in dirs:
        d = sources.check_path(d.strip().strip("/") if isinstance(d, str) else d)
        if d not in out:
            out.append(d)
    return out


def add(user, source, repo, branch, dirs, verify, token=None, now=None, verify_config=None):
    """A new connection for `user` (the signed-in account's dictionary). Nothing is fetched here: the route checks
    the repository can be read before it calls this. Returns the connection, without its token."""
    if source not in ("github", "local"):
        raise LedgerError("A repository is on GitHub, or a clone this deployment's admin named.")
    if source == "local":
        if not user.get("admin"):
            raise LedgerError("Only an admin can connect a clone on this machine.")
        if repo not in sources.local_repos():
            raise LedgerError("This deployment names no such clone.")
        if token:
            raise LedgerError("A clone on this machine is read with git and takes no token.")
    else:
        sources.check_repo(repo)
    sources.check_branch(branch)
    dirs = _clean_dirs(dirs)
    verify_config = clean_verify_config(verify_config)
    if not dirs and not verify:
        raise LedgerError("Name at least one channel directory, or turn on Verify for the branch's commits.")
    token = token.strip() if isinstance(token, str) else None
    if token and (len(token) > MAX_TOKEN or any(c.isspace() for c in token)):
        raise LedgerError("That does not look like a GitHub token.")
    now = int(now if now is not None else time.time())
    repo_id = uuid.uuid4().hex
    box = seal(token, user["id"], repo_id) if token else None
    with store.connect() as db:
        count = db.execute("SELECT COUNT(*) FROM ade_repos WHERE user_id = ?", (user["id"],)).fetchone()[0]
        if count >= MAX_CONNECTIONS:
            raise LedgerError(f"An account connects at most {MAX_CONNECTIONS} repositories.")
        db.execute("INSERT INTO ade_repos (id, user_id, source, repo, branch, dirs, verify, token_box, created_at, "
                   "verify_config) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (repo_id, user["id"], source, repo, branch, json.dumps(dirs), int(bool(verify)), box, now,
                    json.dumps(verify_config) if verify_config else None))
    return get(user["id"], repo_id)


def list_for(user_id):
    with store.connect() as db:
        rows = db.execute("SELECT * FROM ade_repos WHERE user_id = ? ORDER BY created_at", (user_id,)).fetchall()
    return [_row(r) for r in rows]


def get(user_id, repo_id):
    """The connection `repo_id` if it is `user_id`'s, else None: another account's connection is not found."""
    with store.connect() as db:
        r = db.execute("SELECT * FROM ade_repos WHERE id = ? AND user_id = ?", (repo_id, user_id)).fetchone()
    return _row(r) if r else None


def remove(user_id, repo_id):
    with store.connect() as db:
        gone = db.execute("DELETE FROM ade_repos WHERE id = ? AND user_id = ?", (repo_id, user_id)).rowcount
        if gone:
            db.execute("DELETE FROM ade_verifications WHERE repo_id = ?", (repo_id,))
    return bool(gone)


def source_for(user_id, repo_id, opener=None):
    """The source to read connection `repo_id` through, with its token opened, if it is `user_id`'s."""
    with store.connect() as db:
        r = db.execute("SELECT * FROM ade_repos WHERE id = ? AND user_id = ?", (repo_id, user_id)).fetchone()
    if r is None:
        return None
    if r["source"] == "local":
        path = sources.local_repos().get(r["repo"])
        if path is None:
            raise LedgerError("This deployment no longer names that clone.")
        return sources.LocalGit(r["repo"], path)
    token = unseal(r["token_box"], user_id, repo_id) if r["token_box"] else None
    return sources.GitHub(r["repo"], token=token, opener=opener)


def save_verification(repo_id, sha, record, now=None):
    now = int(now if now is not None else time.time())
    with store.connect() as db:
        db.execute("INSERT OR REPLACE INTO ade_verifications (repo_id, sha, status, reason, verify, claims, rule, "
                   "because, digest, manifest, signature, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (repo_id, sha, record["status"], record.get("reason"), record.get("verify"), record.get("claims"),
                    record.get("rule"), json.dumps(record.get("because") or []), record.get("digest"),
                    json.dumps(record["manifest"]) if record.get("manifest") else None,
                    json.dumps(record["signature"]) if record.get("signature") else None, now))


_SUMMARY = ("status", "reason", "verify", "claims", "rule", "digest", "created_at")


def _summary(r):
    out = {k: r[k] for k in _SUMMARY}
    out["because"] = json.loads(r["because"]) if r["because"] else []
    return out


def verification_summaries(repo_id):
    """{commit id: {status, reason, verify, claims, rule, because, digest, created_at}} for a connection the caller
    already checked belongs to the account."""
    with store.connect() as db:
        rows = db.execute("SELECT sha, " + ", ".join(_SUMMARY) + ", because FROM ade_verifications "
                          "WHERE repo_id = ?", (repo_id,)).fetchall()
    return {r["sha"]: _summary(r) for r in rows}


def verification(repo_id, sha):
    """The full record of one commit's Verify, manifest and signature included, or None."""
    with store.connect() as db:
        r = db.execute("SELECT * FROM ade_verifications WHERE repo_id = ? AND sha = ?", (repo_id, sha)).fetchone()
    if r is None:
        return None
    out = dict(_summary(r), sha=r["sha"])
    out["manifest"] = json.loads(r["manifest"]) if r["manifest"] else None
    out["signature"] = json.loads(r["signature"]) if r["signature"] else None
    return out
