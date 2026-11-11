"""Verify on finish: a finished workspace session's change, verified and
signed by the app, which holds the signing key; the workspace only reports.

The workspace reports its session's base and head commits and the diff between them. The claim is the session's
task: its first line as the title, the rest as the description. The manifest names the repository and both commits,
so anyone holding the repository can re-derive it offline, and a workspace that reported a diff other than the one
its branch holds is caught there. The report is checked for shape before anything of it is used: a workspace is the
user's own machine and anything in it can be changed from its terminal.

A finish with no change, or with a diff larger than Verify reads, is recorded as skipped, with the reason, never as
passed.
"""

import json
import re
import time
import uuid

from .. import config, store, tools
from .verifier import because_of

_SHA = re.compile(r"^[0-9a-f]{40}$")
_ID = re.compile(r"^[0-9a-f]{12}$")
_LABEL = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
MAX_TASK = 4000


class BadReport(ValueError):
    pass


def check_report(report, branch=None):
    """The report, checked for shape; its branch is the session's own, or, for an agent's run, `branch` (the one the
    app's records give it)."""
    ok = (isinstance(report, dict) and isinstance(report.get("session"), str) and _ID.match(report["session"])
          and report.get("branch") == (branch or f"pp/{report['session']}")
          and all(isinstance(report.get(k), str) and _SHA.match(report[k]) for k in ("base", "head"))
          and isinstance(report.get("label"), str) and len(report["label"]) <= 200 and _LABEL.match(report["label"])
          and isinstance(report.get("repo"), str) and 0 < len(report["repo"]) <= 100
          and isinstance(report.get("task"), str) and 0 < len(report["task"]) <= MAX_TASK
          and isinstance(report.get("too_large"), bool)
          and (report.get("diff") is None or isinstance(report.get("diff"), str))
          and (report["too_large"] or isinstance(report.get("diff"), str)))
    if not ok:
        raise BadReport("The workspace's report is not in the expected form.")
    return report


def verify_report(report, branch=None):
    """The record for one finish: status `verified`, `skipped` or `error`, as the ADE's commit results are."""
    report = check_report(report, branch)
    if report["too_large"]:
        return {"status": "skipped", "reason": f"its diff is larger than {config.MAX_DIFF // (1024 * 1024)} MB, "
                                                "the most Verify reads here"}
    if not report["diff"].strip():
        return {"status": "skipped", "reason": f"no change since the {'run' if branch else 'session'}'s base"}
    title, _, rest = report["task"].partition("\n")
    try:
        out = tools.verify(title.strip(), rest.strip(), report["diff"], repo=report["label"],
                           base_sha=report["base"], head_sha=report["head"])
    except tools.ToolError as e:
        return {"status": "error", "reason": str(e)}
    decision = out["decision"] or {}
    return {"status": "verified", "reason": None, "verify": decision.get("verify"), "claims": decision.get("claims"),
            "rule": decision.get("rule_id"), "because": because_of(out["manifest"], decision),
            "digest": out["digest"], "manifest": out["manifest"], "signature": out["signature"]}


_SUMMARY = ("id", "session", "repo", "branch", "base", "head", "task", "status", "reason", "verify", "claims", "rule",
            "digest", "created_at")


def _summary(r):
    out = {k: r[k] for k in _SUMMARY}
    out["because"] = json.loads(r["because"]) if r["because"] else []
    return out


def save(user_id, report, record, now=None):
    now = int(now if now is not None else time.time())
    finish_id = uuid.uuid4().hex
    with store.connect() as db:
        db.execute("INSERT INTO ade_finishes (id, user_id, session, repo, branch, base, head, task, status, reason, "
                   "verify, claims, rule, because, digest, manifest, signature, created_at) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (finish_id, user_id, report["session"], report["label"], report["branch"], report["base"],
                    report["head"], report["task"], record["status"], record.get("reason"), record.get("verify"),
                    record.get("claims"), record.get("rule"), json.dumps(record.get("because") or []),
                    record.get("digest"), json.dumps(record["manifest"]) if record.get("manifest") else None,
                    json.dumps(record["signature"]) if record.get("signature") else None, now))
    return get(user_id, finish_id, full=False)


def list_for(user_id, session=None, limit=50):
    q = "SELECT * FROM ade_finishes WHERE user_id = ?"
    args = [user_id]
    if session is not None:
        q += " AND session = ?"
        args.append(session)
    q += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    args.append(limit)
    with store.connect() as db:
        rows = db.execute(q, args).fetchall()
    return [_summary(r) for r in rows]


def get(user_id, finish_id, full=True):
    """One of `user_id`'s finishes, with its manifest and signature when `full`; None when it is not theirs."""
    with store.connect() as db:
        r = db.execute("SELECT * FROM ade_finishes WHERE id = ? AND user_id = ?", (finish_id, user_id)).fetchone()
    if r is None:
        return None
    out = _summary(r)
    if full:
        out["manifest"] = json.loads(r["manifest"]) if r["manifest"] else None
        out["signature"] = json.loads(r["signature"]) if r["signature"] else None
    return out
