"""The ADE: Verify on each commit a session pushes, signed.

For each commit on a connected branch that has no result yet, oldest first and a few at a time, the change is taken
from the repository (the commit against its one parent) and the commit message is taken as the claim: its subject as
the title and the rest, less the trailers, as the description. The Verification Manifest v0 that `tools.verify`
builds names the repository and both commits, so anyone holding the repository can re-derive it offline, and it is
signed with the deployment's composite ML-DSA-65 + Ed25519 key, exactly as the Verify page signs a pasted diff.

The result keeps the gate's rule and the obligations it names, so the board can say why a commit did not pass:
a commit carries no CI result here, so the required `ci.checks_green` stays unverified and the gate asks for a
person (`needs-person`), which the board shows as that and not as a failure of the change.

A commit Verify cannot take is recorded as skipped, with the reason, and never as passed: a merge commit (its change
is its parents'), the first commit (no parent to compare with), an empty change, or a diff larger than Verify reads.
A diff the reader refuses, or a source that fails, is recorded as an error with what went wrong.
"""

import time

from .. import config, tools
from . import board, channel, commits as commits_mod, ledger, sources

BATCH = 5
RECHECK_AFTER = 5 * 60          # a commit whose CI was pending is read again after this long
RECHECK_FOR = 24 * 3600         # and no later than this after the commit


def _description(commit):
    """The message less its subject line, and less its last paragraph when every line of that is a trailer."""
    paragraphs = [p.strip() for p in commit["message"].partition("\n")[2].split("\n\n") if p.strip()]
    if paragraphs:
        lines = [line for line in paragraphs[-1].split("\n") if line.strip()]
        if len(commits_mod.trailers("subject\n\n" + paragraphs[-1])) == len(lines):
            paragraphs = paragraphs[:-1]
    return "\n\n".join(paragraphs)


def verify_commit(source, commit, verify_config=None):
    """The record for one commit: status `verified`, `skipped` or `error`, and for a verified one the decision,
    digest, manifest and signature. With `verify_config` (the connection's expected checks), the commit's CI is read from
    the source and given to Verify; CI that cannot be read is said in `reason`, and Verify then sees none."""
    if len(commit["parents"]) > 1:
        return {"status": "skipped", "reason": "a merge commit: its change is its parents'"}
    if not commit["parents"]:
        return {"status": "skipped", "reason": "the first commit: it has no parent to compare with"}
    try:
        text = source.commit_diff(commit["sha"], commit["parents"][0])
    except sources.SourceError as e:
        return {"status": "error", "reason": str(e)}
    if not text.strip():
        return {"status": "skipped", "reason": "no change: the commit's tree is its parent's"}
    if len(text.encode("utf-8")) > config.MAX_DIFF:
        return {"status": "skipped", "reason": f"its diff is larger than {config.MAX_DIFF // (1024 * 1024)} MB, "
                                               "the most Verify reads here"}
    check_runs, statuses, note = [], [], None
    reader = getattr(source, "commit_checks", None)
    if verify_config and reader is not None:
        try:
            check_runs, statuses = reader(commit["sha"])
        except sources.SourceError as e:
            note = f"CI could not be read: {e}"
    try:
        out = tools.verify(commit["subject"], _description(commit), text, repo=source.label,
                           base_sha=commit["parents"][0], head_sha=commit["sha"], check_runs=check_runs,
                           statuses=statuses, verify_config=verify_config)
    except tools.ToolError as e:
        return {"status": "error", "reason": str(e)}
    decision = out["decision"] or {}
    return {"status": "verified", "reason": note, "verify": decision.get("verify"), "claims": decision.get("claims"),
            "rule": decision.get("rule_id"), "because": because_of(out["manifest"], decision),
            "digest": out["digest"], "manifest": out["manifest"], "signature": out["signature"]}


def because_of(manifest, decision):
    """What keeps the deciding check from passing, as the gate names it: each obligation in its `because`, with the
    resolver, the outcome and the reason the manifest records, so the board can say why without the manifest."""
    named = set(decision.get("because") or [])
    out = []
    for o in manifest.get("obligations") or []:
        if o.get("id") in named:
            reason = o.get("reason")
            out.append({"id": o.get("id"), "resolver": o.get("resolver"), "outcome": o.get("outcome"),
                        "reason": reason if isinstance(reason, str) else None})
    return out


def _ci_pending(summary):
    """Whether a stored result was decided while the commit's CI was still running or not yet reported."""
    return any(b.get("resolver") == "ci.checks_green" and (b.get("reason") or "").startswith("pending:")
               for b in summary.get("because") or [])


def verify_new(repo_id, source, branch, limit=BATCH, verify_config=None, now=None):
    """Verify up to `limit` of the branch's recent commits, oldest first: those with no result yet, then those whose
    CI was pending when they were verified, again, at most every RECHECK_AFTER seconds and for RECHECK_FOR seconds
    after the commit. Returns {done: [{sha, status, ...}], remaining: <how many are still due>}."""
    now = int(now if now is not None else time.time())
    head = source.head(branch)
    recent = source.branch_commits(head, board.BRANCH_HISTORY)
    have = ledger.verification_summaries(repo_id)
    todo = [c for c in reversed(recent) if c["sha"] not in have]
    for c in reversed(recent):
        s = have.get(c["sha"])
        made = channel.parse_time(c["time"]) if c.get("time") else None
        if (s and _ci_pending(s) and now - s["created_at"] >= RECHECK_AFTER and made is not None
                and now - made.timestamp() < RECHECK_FOR):
            todo.append(c)
    done = []
    for c in todo[:limit]:
        record = verify_commit(source, c, verify_config)
        ledger.save_verification(repo_id, c["sha"], record, now=now)
        done.append({"sha": c["sha"], "short": c["short"], "status": record["status"], "reason": record.get("reason"),
                     "verify": record.get("verify"), "claims": record.get("claims"), "rule": record.get("rule"),
                     "because": record.get("because") or [], "digest": record.get("digest")})
    return {"done": done, "remaining": max(0, len(todo) - len(done))}
