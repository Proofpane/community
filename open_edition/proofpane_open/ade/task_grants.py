"""A task's grant: one record per task, the owner's statement of its scope, signed with one of the account's passkeys as
a link command is signed (link_commands.check_grant), at the task's creation and at each widening. This module keeps it,
checks its signature and shows it; enforcing it (an App token per attempt narrowed to its repositories, the check run,
the deploy, the secrets, the egress) is later work, so each scope field below is today kept, signed and shown, and
nothing else.

What the owner signs is one JSON object, its canonical bytes RFC 8785, the WebAuthn challenge its step challenge
(passkeys.step_challenge), around a nonce of purpose task-grant from POST /api/ade/link/nonce, as every signed step is:

    v, deployment, purpose (task-grant), account, nonce, id, created_at, expires_at   checked as link_commands checks
                 every signed payload's; a grant is accepted for at most 600 seconds after it is made
    task         the task's id
    version      1 for the first, one more for each after it
    prev         "" for the first, else the digest of the version before it (64 lowercase hex)
    ask          "sha256:" and the hex sha256 of the task's goal as it stands when signed: the ask, pinned by digest
    requester    {"kind": "owner"} for a grant the owner composes; {"kind": "agent", "id": the task's agent} or
                 {"kind": "link", "id": the linked session} for one that signs what that one asked for
    widening     "" for the owner's own; else the id of the ask it signs
    repositories [{repo, push, merge}]: each a repository of the workspace by its name, the branches it may push and
                 those a pull request of it may merge into; the task's own repository, where it has one, with the
                 task's own branch pp/task-<id> among those it may push
    environments [{name, rung}]: each it may deploy to, and the rung that deploy needs
    secrets      [{name, lease_seconds}]: each by its name, never its value, with its lease
    egress       [host]: the hosts a hosted attempt may reach beyond those its programs need
    lease_end    unix seconds: past it, every credential the task holds stops
    rungs        {merge, deploy}: the rung each act needs (the defaults: merge into a protected branch at L2, a
                 production deploy at L2-high)

Each list is sorted and names each entry once, so one scope has one canonical form; each name is checked for a
secret's shape, refused with that shape's name, never the value. The grant's digest is the hex sha256 of
"proofpane-task-grant/1", a zero byte and the canonical bytes; an attempt's message names it (tasks.attempt_message).

Who changes it: the owner alone, with a passkey, in a browser on the hosted site, from a phone
or a computer. An account with no passkey (the desktop server's own, which has nothing to sign with) gives no grant, and
the phone app leaves passkeys out, so it signs none. Nothing but the owner's signature changes a grant: a change of the
task naming one is refused (tasks.update_task), and a version is kept only when its signature holds over its canonical
bytes, for this account and this task, as the version after the newest, naming the newest's digest and the task's goal
as it stands.

A version stands until the passkey that signed it goes: removing a passkey
(passkeys.remove_in), or turning high security on, after which only the account's hardware keys sign
(passkeys.high_security_in), withdraws in the same transaction each version the passkeys that go signed
(passkeys_going), saying why, and a version withdrawn stays so. The grant then stands at its newest version not
withdrawn, or at none, and an attempt's message names that one, or none; the next version still follows the newest
kept, withdrawn or not, so the chain of digests stays whole; and each ask that waits is carried onto the grant that
stands, so that signing it never gives again what a withdrawn version gave. The task, its versions, the board and the
page say which.

An agent (its task attempt's own tool ask_grant, which puts the ask on the attempt's record, `grant_asks`, kept by the
workspace's view of the record to the bounds here) or a linked session (POST /api/ade/link/tasks/{id}/grant-widenings,
signed by its own key, the request its client's command ade_link.py widen makes) only asks. An ask names what it adds
(repositories and their branches, environments, secrets,
hosts, a later lease end, other rungs); the app keeps beside it the whole scope it would make, the grant as it stands
joined with what it adds, for the owner to sign as it is. It waits in the owner's list (the ADE's Approvals, counted in
ade/summary.py) and changes nothing until the owner signs it; the owner may refuse it with the sign-in alone. A linked
session revoked takes its asks that wait with it (link_ended), and an ask a revoked session left waiting is said so and
never signed. A newer version carries each ask that waits onto itself; an ask past its day, or of a task that has ended
or was deleted, waits no more. Each ask is taken once by its client_id, so a record copied again asks nothing more; an
ask that adds nothing (whether or not the task has a grant), one whose version a passkey's signature could not carry
(check_signable), and the same ask from one asker while it waits are refused as they are asked.

A task with no repository needs no grant to start, as today; in this build no task needs one (what an attempt of a task
with no grant may hold is later work).
"""

import json
import re
import secrets
import sys
import time

from .. import passkeys, paths, store  # noqa: F401  (paths puts the open packages on sys.path)
from . import agents, link, link_commands, tasks
from . import task_shapes as shapes
from proofpane_core.canonical import canonicalize

PURPOSE = link_commands.TASK_GRANT
TAG = b"proofpane-task-grant/1"
SCOPE = ("repositories", "environments", "secrets", "egress", "lease_end", "rungs")
ADDABLE = ("repositories", "environments", "secrets", "egress")
ASK_KEYS = frozenset({"client_id", "add", "lease_end", "rungs"})
RUNGS = ("L2", "L2-high", "L3")
ACTS = ("merge", "deploy")
DEFAULT_RUNGS = {"merge": "L2", "deploy": "L2-high"}
DEFAULT_LEASE = 7 * 86400               # an ask of a task with no grant asks for a week, unless it names an end
LEASE_MIN, LEASE_MAX = 60, 366 * 86400  # a lease ends a minute to 366 days after it is signed or asked
SECRET_LEASE_MIN, SECRET_LEASE_MAX = 60, 30 * 86400
REPOS_MAX = BRANCHES_MAX = ENVIRONMENTS_MAX = 20
SECRETS_MAX = EGRESS_MAX = 50
VERSION_MAX = 2 ** 53 - 1
ASK_LIFE = 86400                        # an ask waits a day
WAITING_PER_TASK = 5
WAITING_PER_ACCOUNT = 50
ASKS_PER_RECORD = 5
ENDED = ("done", "stopped")
REMOVED, HIGH_SECURITY = "removed", "high_security"          # why a version was withdrawn (passkeys_going)
WITHDRAWN_JOURNALED = 50                # versions one task's journal event names; it says how many went beside them

_ID = re.compile(r"[0-9a-f]{32}")                         # these: fullmatch, never match
_AGENT = re.compile(r"[0-9a-f]{12}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_ASK = re.compile(r"sha256:[0-9a-f]{64}")
_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")      # a workspace repository's name, as an agent names it
_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
_ENVIRONMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_SECRET = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOST = re.compile(rf"{_LABEL}(?:\.{_LABEL})*")

NO_PASSKEY = ("A grant is signed with one of this account's passkeys, in a browser on the hosted site, and this "
              "account has none: add one in Account first. The desktop app's own account, which has nothing to sign "
              "with, gives no grant.")
BY_SIGNATURE = ("A task's grant changes only with its owner's signature (POST /api/ade/agents/tasks/{id}/grant, signed "
                "with a passkey), never with a change of the task.")
CHANGED = ("This grant is not the version after the task's newest: its grant changed since this was composed, or this "
           "names another version before it. Read the task again, and sign the newest.")
NOTHING = "It asks for nothing its grant does not give already."
NOTHING_FIRST = "It asks for nothing beyond what any grant of the task names: its own repository and branch."
TOO_BIG = (f"The grant that signs this ask would be more than {link_commands.PAYLOAD_MAX} bytes, which a passkey's "
           "signature does not carry: ask for less.")
SAME_ASK = "The same ask waits already, {}: it is not kept twice."
ENDED_WORDS = "The task has ended: its grant does not change."
NO_ASK = "No such ask."
LINK_REVOKED = ("The linked session that asked for this was revoked: its ask is not signed. Refuse it, or compose your "
                "own.")


def _now(now):
    return int(now if now is not None else time.time())


def _int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _bad(words):
    return link.LinkError(422, words)


def _error(status, words, **extra):
    return agents.AgentError(status, words, **extra)


def _canon(value):
    out = canonicalize(value)
    return out if isinstance(out, bytes) else out.encode("utf-8")


def _text(value):
    return _canon(value).decode("utf-8")


def digest(raw):
    """A grant's digest: the hex sha256 of TAG, a zero byte and its canonical bytes."""
    return shapes.tagged_digest(TAG, raw)


def own_branch(task):
    """The task's own branch, the one its attempts push: the owner's task's, for a task received from a teammate."""
    return "pp/task-" + tasks._branch_task(task)


def ask_of(task):
    """The task's ask as a grant pins it: its goal, as it stands now, by digest."""
    return "sha256:" + task["goal_digest"]


# ---- a grant's fields ---------------------------------------------------------------------------------------------

def _sorted_once(values, what):
    if len(set(values)) != len(values):
        raise _bad(f"{what} name each once.")
    if values != sorted(values):
        raise _bad(f"{what} are sorted, so that a grant has one form.")


def _branches(value, what):
    if not isinstance(value, list) or len(value) > BRANCHES_MAX:
        raise _bad(f"A repository's {what} branches are a list of at most {BRANCHES_MAX}.")
    for b in value:
        if not (isinstance(b, str) and _BRANCH.fullmatch(b) and ".." not in b and "//" not in b
                and not b.endswith(("/", ".", ".lock"))):
            raise _bad(f"A repository's {what} branch is a branch's name: letters, digits, '.', '_', '-' and '/'.")
        link.check_secrets(b, "branch")
    _sorted_once(value, f"A repository's {what} branches")


def _repositories(value):
    if not isinstance(value, list) or len(value) > REPOS_MAX:
        raise _bad(f"A grant's repositories are a list of at most {REPOS_MAX}.")
    for r in value:
        if not isinstance(r, dict) or set(r) != {"repo", "push", "merge"}:
            raise _bad("A grant's repository is {repo, push, merge}.")
        name = r["repo"]
        if not (isinstance(name, str) and _REPO.fullmatch(name) and ".." not in name and not name.endswith(".git")):
            raise _bad("A grant's repository is one of the workspace's, by its name.")
        link.check_secrets(name, "repository")
        _branches(r["push"], "push")
        _branches(r["merge"], "merge")
    _sorted_once([r["repo"] for r in value], "A grant's repositories")


def _environments(value):
    if not isinstance(value, list) or len(value) > ENVIRONMENTS_MAX:
        raise _bad(f"A grant's environments are a list of at most {ENVIRONMENTS_MAX}.")
    for e in value:
        if not isinstance(e, dict) or set(e) != {"name", "rung"}:
            raise _bad("A grant's environment is {name, rung}.")
        if not (isinstance(e["name"], str) and _ENVIRONMENT.fullmatch(e["name"])):
            raise _bad("An environment is named with letters, digits, '.', '_' and '-'.")
        link.check_secrets(e["name"], "environment")
        if e["rung"] not in RUNGS:
            raise _bad("An environment's rung is " + ", ".join(RUNGS) + ".")
    _sorted_once([e["name"] for e in value], "A grant's environments")


def _secrets(value):
    if not isinstance(value, list) or len(value) > SECRETS_MAX:
        raise _bad(f"A grant's secrets are a list of at most {SECRETS_MAX}.")
    for s in value:
        if not isinstance(s, dict) or set(s) != {"name", "lease_seconds"}:
            raise _bad("A grant's secret is {name, lease_seconds}: its name, never its value.")
        if not (isinstance(s["name"], str) and _SECRET.fullmatch(s["name"])):
            raise _bad("A secret is named as a variable is: letters, digits and '_'.")
        link.check_secrets(s["name"], "secret's name")
        if not _int(s["lease_seconds"]) or not SECRET_LEASE_MIN <= s["lease_seconds"] <= SECRET_LEASE_MAX:
            raise _bad(f"A secret's lease is {SECRET_LEASE_MIN} to {SECRET_LEASE_MAX} seconds.")
    _sorted_once([s["name"] for s in value], "A grant's secrets")


def _egress(value):
    if not isinstance(value, list) or len(value) > EGRESS_MAX:
        raise _bad(f"A grant's egress is a list of at most {EGRESS_MAX} hosts.")
    for h in value:
        if not (isinstance(h, str) and len(h) <= 253 and _HOST.fullmatch(h)):
            raise _bad("An egress host is a host's name, in lowercase, with no scheme, port or path.")
        link.check_secrets(h, "host")
    _sorted_once(value, "A grant's egress hosts")


def _rungs(value):
    if not isinstance(value, dict) or set(value) != set(ACTS) or any(value[a] not in RUNGS for a in ACTS):
        raise _bad("A grant's rungs are {merge, deploy}, each " + ", ".join(RUNGS) + ".")


def _lease_end(value, start):
    if not _int(value) or not start + LEASE_MIN <= value <= start + LEASE_MAX:
        raise _bad(f"A grant's lease ends {LEASE_MIN} seconds to {LEASE_MAX // 86400} days after it is made.")


def check_scope(scope, start):
    """The six scope fields of `scope` (a payload, or a scope an ask makes), each in its one form; refused (422) naming
    the first that is not. `start` is the time a lease is counted from: a payload's created_at, or an ask's time."""
    _repositories(scope["repositories"])
    _environments(scope["environments"])
    _secrets(scope["secrets"])
    _egress(scope["egress"])
    _lease_end(scope["lease_end"], start)
    _rungs(scope["rungs"])


def _requester(value):
    ok = isinstance(value, dict) and (value == {"kind": "owner"} or (
        set(value) == {"kind", "id"} and isinstance(value["id"], str)
        and ((value["kind"] == "agent" and _AGENT.fullmatch(value["id"]) is not None)
             or (value["kind"] == "link" and _ID.fullmatch(value["id"]) is not None))))
    if not ok:
        raise _bad("A grant's requester is {kind: owner}, or the agent or the linked session that asked: {kind, id}.")


def check_fields(payload):
    """A grant's own fields, each checked before the signature: link_commands._check_payload calls this once the fields
    every signed payload has are checked. Refused (422) naming the first that is out of shape."""
    if not isinstance(payload["task"], str) or not _ID.fullmatch(payload["task"]):
        raise _bad("A grant's task is a task's id: 32 lowercase hex characters.")
    version, prev = payload["version"], payload["prev"]
    if not _int(version) or not 0 < version <= VERSION_MAX:
        raise _bad("A grant's version is a whole number from 1.")
    if not isinstance(prev, str) or (prev != "" and not _DIGEST.fullmatch(prev)) or (prev == "") != (version == 1):
        raise _bad("A grant's prev is the digest of the version before it, as 64 lowercase hex characters, and empty "
                   "for the first.")
    if not isinstance(payload["ask"], str) or not _ASK.fullmatch(payload["ask"]):
        raise _bad("A grant's ask is sha256: and 64 lowercase hex characters: its task's goal, pinned by digest.")
    _requester(payload["requester"])
    widening = payload["widening"]
    if not isinstance(widening, str) or (widening != "" and not _ID.fullmatch(widening)):
        raise _bad("A grant's widening is the id of the ask it signs, 32 lowercase hex characters, or empty.")
    if (widening == "") != (payload["requester"] == {"kind": "owner"}):
        raise _bad("A grant the owner composes names the owner as its requester and no widening; one that signs an ask "
                   "names that ask and the one who asked.")
    check_scope(payload, payload["created_at"])


def check_own_repository(task, repositories):
    """A grant of a task with a repository names it, and every repository a grant names carries the task's own branch
    among those it may push, for each repository: pp/task-<id>, or the owner's task's branch for a task received from a
    teammate. 422 else."""
    branch = own_branch(task)
    if task["repo"] and not any(r["repo"] == task["repo"] for r in repositories):
        raise _bad(f"A grant of this task names its repository, {task['repo']}, with the task's own branch, {branch}, "
                   "among those it may push.")
    for r in repositories:
        if branch not in r["push"]:
            raise _bad(f"Each repository a grant of this task names carries the task's own branch, {branch}, among "
                       f"those it may push: {r['repo']} does not.")


# ---- the scope an ask makes -----------------------------------------------------------------------------------------

def _scope(value):
    return {k: value[k] for k in SCOPE}


def first_scope(task, now):
    """The scope an ask of a task with no grant widens: the task's own repository with its own branch, nothing else, a
    lease of DEFAULT_LEASE from now, and the default rungs."""
    repos = [{"repo": task["repo"], "push": [own_branch(task)], "merge": []}] if task["repo"] else []
    return {"repositories": repos, "environments": [], "secrets": [], "egress": [], "lease_end": now + DEFAULT_LEASE,
            "rungs": dict(DEFAULT_RUNGS)}


def _listed(value, what):
    if value is None:
        return []
    if not isinstance(value, list):
        raise _bad(f"An ask's {what} are a list.")
    return value


def _adds(given):
    """What an ask adds, from {client_id, add: {repositories?, environments?, secrets?, egress?}, lease_end?, rungs?}:
    each list sorted and naming each entry once, each name checked as a grant's is; refused (422) otherwise."""
    add = given.get("add") if given.get("add") is not None else {}
    if not isinstance(add, dict) or set(add) - set(ADDABLE):
        raise _bad("An ask's add holds " + ", ".join(ADDABLE) + ", each a list.")
    repos = {}
    for r in _listed(add.get("repositories"), "repositories"):
        if not isinstance(r, dict) or "repo" not in r or set(r) - {"repo", "push", "merge"}:
            raise _bad("An ask's repository is {repo, push?, merge?}.")
        push, merge = _listed(r.get("push"), "branches"), _listed(r.get("merge"), "branches")
        if not isinstance(r["repo"], str) or not all(isinstance(b, str) for b in push + merge):
            raise _bad("An ask's repositories and branches are each named.")
        was = repos.setdefault(r["repo"], (set(), set()))
        was[0].update(push)
        was[1].update(merge)
    environments = {}
    for e in _listed(add.get("environments"), "environments"):
        if not isinstance(e, dict) or set(e) != {"name", "rung"} or not isinstance(e["name"], str):
            raise _bad("An ask's environment is {name, rung}.")
        environments[e["name"]] = e["rung"]
    leases = {}
    for s in _listed(add.get("secrets"), "secrets"):
        if not isinstance(s, dict) or set(s) != {"name", "lease_seconds"} or not isinstance(s["name"], str) \
                or not _int(s["lease_seconds"]):
            raise _bad("An ask's secret is {name, lease_seconds}: its name, never its value.")
        leases[s["name"]] = max(leases.get(s["name"], s["lease_seconds"]), s["lease_seconds"])
    hosts = _listed(add.get("egress"), "hosts")
    if not all(isinstance(h, str) for h in hosts):
        raise _bad("An ask's hosts are each named.")
    out = {"repositories": [{"repo": n, "push": sorted(p), "merge": sorted(m)} for n, (p, m) in sorted(repos.items())],
           "environments": [{"name": n, "rung": environments[n]} for n in sorted(environments)],
           "secrets": [{"name": n, "lease_seconds": leases[n]} for n in sorted(leases)],
           "egress": sorted(set(hosts)), "lease_end": given.get("lease_end"), "rungs": given.get("rungs")}
    _repositories(out["repositories"])
    _environments(out["environments"])
    _secrets(out["secrets"])
    _egress(out["egress"])
    if out["lease_end"] is not None and not _int(out["lease_end"]):
        raise _bad("An ask's lease_end is unix seconds.")
    if out["rungs"] is not None:
        _rungs(out["rungs"])
    return out


def widened(scope, adds, own=None):
    """`scope` with what `adds` adds: each repository's branches joined, an environment at the rung asked for, a
    secret's longer lease, the hosts joined, the later lease end, and the rungs asked for in place of its own; with
    `own`, the task's own branch, every repository pushing it too (check_own_repository)."""
    repos = {r["repo"]: (set(r["push"]), set(r["merge"])) for r in scope["repositories"]}
    for r in adds["repositories"]:
        push, merge = repos.get(r["repo"], (set(), set()))
        repos[r["repo"]] = (push | set(r["push"]), merge | set(r["merge"]))
    if own:
        repos = {n: (p | {own}, m) for n, (p, m) in repos.items()}
    environments = {e["name"]: e["rung"] for e in scope["environments"]}
    environments.update({e["name"]: e["rung"] for e in adds["environments"]})
    leases = {s["name"]: s["lease_seconds"] for s in scope["secrets"]}
    for s in adds["secrets"]:
        leases[s["name"]] = max(leases.get(s["name"], 0), s["lease_seconds"])
    lease_end = scope["lease_end"] if adds["lease_end"] is None else max(scope["lease_end"], adds["lease_end"])
    return {"repositories": [{"repo": n, "push": sorted(p), "merge": sorted(m)} for n, (p, m) in sorted(repos.items())],
            "environments": [{"name": n, "rung": environments[n]} for n in sorted(environments)],
            "secrets": [{"name": n, "lease_seconds": leases[n]} for n in sorted(leases)],
            "egress": sorted(set(scope["egress"]) | set(adds["egress"])), "lease_end": lease_end,
            "rungs": dict(adds["rungs"] if adds["rungs"] is not None else scope["rungs"])}


# ---- where a grant stands -------------------------------------------------------------------------------------------

def head(db, task_id, user_id):
    """The newest version of the task's grant kept, withdrawn or not: the one the next version follows, naming its
    digest (sign). None for a task with none."""
    row = db.execute("SELECT * FROM ade_task_grants WHERE task_id = ? AND user_id = ? ORDER BY version DESC LIMIT 1",
                     (task_id, user_id)).fetchone()
    return dict(row) if row is not None else None


def current(db, task_id, user_id):
    """The version of the task's grant that stands: its newest version not withdrawn, that is, signed by no passkey that
    went since (passkeys_going). None for a task with none, or whose every version was withdrawn."""
    row = db.execute("SELECT * FROM ade_task_grants WHERE task_id = ? AND user_id = ? AND withdrawn_at IS NULL "
                     "ORDER BY version DESC LIMIT 1", (task_id, user_id)).fetchone()
    return dict(row) if row is not None else None


def message_part(db, user_id, task_id):
    """What an attempt's message names of its task's grant (tasks.attempt_message): the digest and the version of the
    one that stands when the message is sent (current); each None for a task with no grant standing."""
    row = current(db, task_id, user_id)
    return {"grant_digest": row["digest"] if row else None, "grant_version": row["version"] if row else None}


def state_of(row, task, now):
    """An ask's state as of `now`: waiting while its task is open and its day is not over; void once its task has ended
    or was deleted; expired once its day is over; else as its row says (signed, refused, or void)."""
    if row["state"] != "waiting":
        return row["state"]
    if task is None or task["state"] in ENDED:
        return "void"
    if row["expires_at"] <= now:
        return "expired"
    return "waiting"


# ---- the owner's side: signing, refusing --------------------------------------------------------------------------

def sign(user, task_id, data, now=None):
    """POST /tasks/{id}/grant: keep the version of the task's grant its owner signed ({payload, credential}), once
    every check holds: the signature (link_commands.check_grant), then, in one transaction, the task (the account's
    own, open), the payload's task, its place after the newest version, the task's goal as it stands, the task's own
    repository and branch, and, for an ask it signs, that ask as it waits, its scope and its requester exactly. {task,
    grant}."""
    now = _now(now)
    if not passkeys.confirm_ways(user["id"])["passkeys"]:
        raise _error(412, NO_PASSKEY)
    payload, raw, credential, row, auth = link_commands.check_grant(user["id"], data, now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        task = tasks._task(db, user["id"], task_id)
        if payload["task"] != task["id"]:
            raise _bad("The grant names another task than the one it is sent for.")
        if task["state"] in ENDED:
            raise _error(412, ENDED_WORDS)
        newest = head(db, task["id"], user["id"])          # withdrawn or not: the chain of digests stays whole
        after = (newest["version"] + 1, newest["digest"]) if newest else (1, "")
        if (payload["version"], payload["prev"]) != after:
            raise _error(409, CHANGED, version=newest["version"] if newest else 0,
                         digest=newest["digest"] if newest else None)
        if payload["ask"] != ask_of(task):
            raise _error(409, "The grant pins another goal than the task's as it stands: sign it again with the goal "
                              "as it is now.")
        check_own_repository(task, payload["repositories"])
        if payload["lease_end"] <= now:
            raise _bad("The grant's lease has ended already.")
        asked = None
        if payload["widening"]:
            asked = db.execute("SELECT * FROM ade_task_grant_widenings WHERE id = ? AND user_id = ?",
                               (payload["widening"], user["id"])).fetchone()
            if asked is None or asked["task_id"] != task["id"]:
                raise _error(409, "The grant signs an ask that is not this task's.")
            state = state_of(asked, task, now)
            if state != "waiting":
                raise _error(409, f"That ask is {state}: only an ask that waits is signed.", state=state)
            if asked["requester_kind"] == "link" and _link_revoked(db, user["id"], asked["requester_id"]):
                raise _error(409, LINK_REVOKED)
            if asked["base_version"] != (newest["version"] if newest else 0):
                raise _error(409, "That ask widens another version of the grant: read it again.")
            if _text(_scope(payload)) != asked["scope"]:
                raise _error(409, "The grant is not the scope that was asked for: sign it as it was asked, or compose "
                                  "your own.")
            if payload["requester"] != {"kind": asked["requester_kind"], "id": asked["requester_id"]}:
                raise _error(409, "The grant names another requester than the one who asked.")
        link_commands.commit_statement(db, user["id"], payload, row, auth, now)
        kept = digest(raw)
        requester = payload["requester"]
        db.execute("INSERT INTO ade_task_grants (task_id, version, user_id, payload, signature, passkey_id, digest, "
                   "requester_kind, requester_id, widening_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (task["id"], payload["version"], user["id"], raw.decode("utf-8"),
                    json.dumps(credential, separators=(",", ":")), row["id"], kept, requester["kind"],
                    requester.get("id"), payload["widening"] or None, now))
        if asked is not None:
            db.execute("UPDATE ade_task_grant_widenings SET state = 'signed', decided_at = ?, version = ? WHERE id = ? "
                       "AND state = 'waiting'", (now, payload["version"], asked["id"]))
        _carry(db, task, payload["version"], _scope(payload), now)
        tasks.journal(db, user["id"], task["id"], "task.grant_signed",
                      {"version": payload["version"], "digest": kept, "by": requester["kind"],
                       "widening": payload["widening"] or None}, now=now)
        return {"task": tasks.view(db, user["id"], task["id"], now), "grant": grant_view(db, task, now)}


def check_signable(task, user_id, scope):
    """The version that would sign `scope` for an ask, at its largest (each of its fields not yet known at its longest:
    the nonce, the version's number, the asker), within link_commands.PAYLOAD_MAX canonical bytes, so that no ask is
    kept that the owner meets only as a refusal after the passkey's prompt. 422 else."""
    largest = dict({"v": link_commands.VERSION, "deployment": link.signing_host(), "purpose": PURPOSE,
                    "account": user_id, "nonce": "n" * passkeys.MAX_CHALLENGE_ID, "id": "0" * 32, "task": task["id"],
                    "version": VERSION_MAX, "prev": "0" * 64, "ask": "sha256:" + "0" * 64,
                    "requester": {"kind": "link", "id": "0" * 32}, "widening": "0" * 32, "created_at": 10 ** 10 - 1,
                    "expires_at": 10 ** 10 - 1}, **_scope(scope))
    if len(_canon(largest)) > link_commands.PAYLOAD_MAX:
        raise _bad(TOO_BIG)


def _carry(db, task, version, scope, now):
    """Each ask of the task that waits, carried onto the version just signed: what it adds joined with that version's
    scope; void where nothing is left for it to add, or the scope it would make is out of bounds, its version among
    them (check_signable)."""
    for row in db.execute("SELECT * FROM ade_task_grant_widenings WHERE task_id = ? AND state = 'waiting' AND "
                          "expires_at > ?", (task["id"], now)).fetchall():
        made = widened(scope, json.loads(row["adds"]), own_branch(task))
        try:
            if _canon(made) == _canon(scope):
                raise _bad(NOTHING)
            check_scope(made, now)
            check_signable(task, task["user_id"], made)
        except link.LinkError:
            db.execute("UPDATE ade_task_grant_widenings SET state = 'void', decided_at = ? WHERE id = ?",
                       (now, row["id"]))
            continue
        db.execute("UPDATE ade_task_grant_widenings SET base_version = ?, scope = ? WHERE id = ?",
                   (version, _text(made), row["id"]))


def passkeys_going(db, user_id, which, args, why, now):
    """Inside a change's own transaction, as the account's passkeys matching `which` go (SQL after "passkey_id", with
    `args`, as passkeys._withdraw_signed takes them): remove_in's one, removed (REMOVED), or high_security_in's, each
    that is not a hardware key (HIGH_SECURITY). Each version of a task's grant one of them signed is withdrawn, saying
    why, so that nothing a passkey that is gone signed stands: the grant stands at its newest version a passkey still
    held signed (current), or at none, and its next version follows its newest still (head). Each task whose versions
    went journals it, and an open one's asks that wait are carried onto the grant that stands, or onto a first grant's
    scope where none is left (_carry). The number of versions withdrawn."""
    rows = db.execute(f"SELECT task_id, version FROM ade_task_grants WHERE user_id = ? AND withdrawn_at IS NULL AND "
                      f"passkey_id {which} ORDER BY task_id, version", (user_id, *args)).fetchall()
    if not rows:
        return 0
    db.execute(f"UPDATE ade_task_grants SET withdrawn_at = ?, withdrawn_why = ? WHERE user_id = ? AND withdrawn_at IS "
               f"NULL AND passkey_id {which}", (now, why, user_id, *args))
    went = {}
    for r in rows:
        went.setdefault(r["task_id"], []).append(r["version"])
    for task_id, versions in went.items():
        task = tasks.get(db, user_id, task_id)
        if task is None:
            continue                    # a deleted task's: its grant is read nowhere
        tasks.journal(db, user_id, task_id, "task.grant_withdrawn",
                      {"versions": versions[-WITHDRAWN_JOURNALED:], "count": len(versions), "why": why}, now=now)
        if task["state"] not in ENDED:
            stands = current(db, task_id, user_id)
            _carry(db, task, head(db, task_id, user_id)["version"],
                   _scope(json.loads(stands["payload"])) if stands else first_scope(task, now), now)
    return len(rows)


def _link_revoked(db, user_id, link_id):
    """Whether the account's linked session `link_id` was revoked (or is not the account's at all)."""
    row = db.execute("SELECT revoked_at FROM ade_links WHERE id = ? AND user_id = ?", (link_id, user_id)).fetchone()
    return row is None or row["revoked_at"] is not None


def link_ended(db, user_id, link_id, now):
    """Inside link.end_link's transaction, as a linked session is revoked: each ask for more in a task's grant it made
    that waits ends with it (void), each journaled with why, so that the owner's passkey cannot apply what a session
    revoked for misbehaving, or for a lost machine, asked for. The number ended."""
    rows = db.execute("SELECT id, task_id FROM ade_task_grant_widenings WHERE user_id = ? AND requester_kind = 'link' "
                      "AND requester_id = ? AND state = 'waiting' AND expires_at > ?",
                      (user_id, link_id, now)).fetchall()
    for r in rows:
        db.execute("UPDATE ade_task_grant_widenings SET state = 'void', decided_at = ? WHERE id = ? AND state = "
                   "'waiting'", (now, r["id"]))
        task = tasks.get(db, user_id, r["task_id"])
        if task is not None and task["state"] not in ENDED:
            tasks.journal(db, user_id, r["task_id"], "task.grant_ask_ended", {"widening": r["id"], "why": "revoked"},
                          now=now)
    return len(rows)


def refuse(user, widening_id, now=None):
    """POST /tasks/grant-widenings/{id}/refuse: the owner refuses an ask that waits, with the sign-in alone, since a
    refusal only keeps the grant as it is. {widening}."""
    now = _now(now)
    if not isinstance(widening_id, str) or not _ID.fullmatch(widening_id):
        raise _error(404, NO_ASK)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM ade_task_grant_widenings WHERE id = ? AND user_id = ?",
                         (widening_id, user["id"])).fetchone()
        if row is None:
            raise _error(404, NO_ASK)
        task = tasks.get(db, user["id"], row["task_id"])
        state = state_of(row, task, now)
        if state != "waiting":
            raise _error(409, f"That ask is {state}: only an ask that waits is refused.", state=state)
        db.execute("UPDATE ade_task_grant_widenings SET state = 'refused', decided_at = ? WHERE id = ? AND "
                   "state = 'waiting'", (now, widening_id))
        tasks.journal(db, user["id"], row["task_id"], "task.grant_refused",
                      {"widening": widening_id, "by": row["requester_kind"]}, now=now)
        row = db.execute("SELECT * FROM ade_task_grant_widenings WHERE id = ?", (widening_id,)).fetchone()
        return {"widening": widening_view(db, row, task, now)}


# ---- the askers' side ---------------------------------------------------------------------------------------------

def ask_in(db, user_id, task, requester, given, now, run_id=None):
    """Inside the caller's transaction: keep an ask for more in `task`'s grant from `requester` (the task's own agent,
    or one of the account's live linked sessions), or return the one it made already under the same client_id, whatever
    has become of it. The ask changes nothing of the grant. Its row."""
    if not isinstance(given, dict) or set(given) - ASK_KEYS:
        raise _bad("An ask is {client_id, add, lease_end?, rungs?}.")
    client_id = given.get("client_id")
    if not isinstance(client_id, str) or not _ID.fullmatch(client_id):
        raise _bad("An ask's client_id is 32 lowercase hex characters, new for each ask.")
    if requester["kind"] == "agent" and requester["id"] != task["agent_id"]:
        raise _error(403, "An agent asks for more only in its own task's grant.")
    have = db.execute("SELECT * FROM ade_task_grant_widenings WHERE user_id = ? AND task_id = ? AND requester_kind = ? "
                      "AND requester_id = ? AND client_id = ?",
                      (user_id, task["id"], requester["kind"], requester["id"], client_id)).fetchone()
    if have is not None:
        return dict(have)
    if task["state"] in ENDED:
        raise _error(412, ENDED_WORDS)
    adds = _adds(given)
    newest = head(db, task["id"], user_id)                 # the version the one that signs it follows
    stands = current(db, task["id"], user_id)              # the scope it widens: the grant that stands
    base = _scope(json.loads(stands["payload"])) if stands else first_scope(task, now)
    scope = widened(base, adds, own_branch(task))
    # an ask that adds nothing is refused whether or not the task has a grant
    if _canon(scope) == _canon(base):
        raise _bad(NOTHING if stands is not None else NOTHING_FIRST)
    if scope["lease_end"] <= now + LEASE_MIN:
        raise _bad("Its grant's lease has ended: an ask names a new lease_end with what it asks for.")
    check_scope(scope, now)
    check_signable(task, user_id, scope)
    # nor is the same ask kept twice from one asker while it waits
    same = db.execute("SELECT id FROM ade_task_grant_widenings WHERE user_id = ? AND task_id = ? AND "
                      "requester_kind = ? AND requester_id = ? AND state = 'waiting' AND expires_at > ? AND adds = ?",
                      (user_id, task["id"], requester["kind"], requester["id"], now, _text(adds))).fetchone()
    if same is not None:
        raise _error(409, SAME_ASK.format(same["id"]), widening=same["id"])
    if db.execute("SELECT COUNT(*) FROM ade_task_grant_widenings WHERE task_id = ? AND state = 'waiting' AND "
                  "expires_at > ?", (task["id"], now)).fetchone()[0] >= WAITING_PER_TASK:
        raise _error(429, f"A task's grant has at most {WAITING_PER_TASK} asks waiting at once.")
    if db.execute("SELECT COUNT(*) FROM ade_task_grant_widenings WHERE user_id = ? AND state = 'waiting' AND "
                  "expires_at > ?", (user_id, now)).fetchone()[0] >= WAITING_PER_ACCOUNT:
        raise _error(429, f"An account has at most {WAITING_PER_ACCOUNT} asks for more in its tasks' grants waiting.")
    widening_id = secrets.token_hex(16)
    db.execute("INSERT INTO ade_task_grant_widenings (id, user_id, task_id, requester_kind, requester_id, client_id, "
               "run_id, base_version, adds, scope, state, created_at, expires_at) VALUES "
               "(?,?,?,?,?,?,?,?,?,?,'waiting',?,?)",
               (widening_id, user_id, task["id"], requester["kind"], requester["id"], client_id, run_id,
                newest["version"] if newest else 0, _text(adds), _text(scope), now, now + ASK_LIFE))
    tasks.journal(db, user_id, task["id"], "task.grant_asked", {"widening": widening_id, "by": requester["kind"]},
                  run_id=run_id, now=now)
    return dict(db.execute("SELECT * FROM ade_task_grant_widenings WHERE id = ?", (widening_id,)).fetchone())


def ask_from_link(link_row, task_id, data, now=None):
    """POST /api/ade/link/tasks/{id}/grant-widenings, signed by a linked session's own key: its ask for more in one of
    its account's tasks' grants ({client_id, add, lease_end?, rungs?}), kept to wait for the owner. {widening: {id,
    state, base_version, created_at, expires_at, version}}: the same ask asked again (its client_id) says where it
    stands."""
    now = _now(now)
    if not isinstance(task_id, str) or not shapes.TASK_ID.fullmatch(task_id):
        raise link.LinkError(404, "No such task.")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        task = tasks.get(db, link_row["user_id"], task_id)
        if task is None:
            raise link.LinkError(404, "No such task.")
        row = ask_in(db, link_row["user_id"], task, {"kind": "link", "id": link_row["id"]}, data, now)
        return {"widening": {"id": row["id"], "state": state_of(row, task, now), "base_version": row["base_version"],
                             "created_at": row["created_at"], "expires_at": row["expires_at"],
                             "version": row["version"]}}


def take_record_asks(db, user_id, task, run, record, now):
    """In tasks.take_record's transaction, while the attempt holds its task's lease at its fence: each ask for more in
    the task's grant its record carries (`grant_asks`, at most ASKS_PER_RECORD), kept as its agent's, each in a
    savepoint of its own. One that cannot be kept (out of shape, nothing new, the task ended, too many waiting) is said
    on standard error and left out; it never fails the record's copy."""
    given = record.get("grant_asks") if isinstance(record, dict) else None
    if not isinstance(given, list):
        return
    for item in given[:ASKS_PER_RECORD]:
        db.execute("SAVEPOINT grant_ask")
        try:
            ask_in(db, user_id, task, {"kind": "agent", "id": run["agent_id"]}, item, now, run_id=run["run_id"])
        except (agents.AgentError, link.LinkError, ValueError) as e:
            db.execute("ROLLBACK TO grant_ask")
            print(f"task_grants: an attempt's ask for more in its task's grant was not kept: {e}", file=sys.stderr)
        db.execute("RELEASE grant_ask")


# ---- what the page reads ------------------------------------------------------------------------------------------

def _requester_name(db, user_id, kind, rid):
    table = {"agent": "ade_agents", "link": "ade_links"}.get(kind)
    if table is None:
        return None
    row = db.execute(f"SELECT name FROM {table} WHERE id = ? AND user_id = ?", (rid, user_id)).fetchone()
    return row["name"] if row else None


def _requester_view(db, user_id, kind, rid):
    """Who asked, as the owner's list shows it: its kind, its id and its name; and `revoked`, true, for a linked
    session revoked since, said only then (a live asker's members stay the three test_2 pins)."""
    view = {"kind": kind, "id": rid, "name": _requester_name(db, user_id, kind, rid)}
    if kind == "link" and _link_revoked(db, user_id, rid):
        view["revoked"] = True
    return view


def widening_view(db, row, task, now):
    """An ask as the owner's list shows it: whose task, who asked (and whether that linked session was revoked), what it
    adds, the scope it would make, where it stands; the scope of the grant that stands (`stands`, null where none does)
    and the task's goal as it stands (`goal`), so that the list says each value the ask changes from what to what and
    the goal the version that signs it pins; and what the page needs to compose the grant
    that signs it: the version it widens and that version's digest ("" for none), and the task's ask as it stands."""
    base = db.execute("SELECT digest FROM ade_task_grants WHERE task_id = ? AND user_id = ? AND version = ?",
                      (row["task_id"], row["user_id"], row["base_version"])).fetchone()
    stands = current(db, row["task_id"], row["user_id"])
    return {"id": row["id"], "task_id": row["task_id"], "task_title": task["title"] if task else None,
            "requester": _requester_view(db, row["user_id"], row["requester_kind"], row["requester_id"]),
            "base_version": row["base_version"], "base_digest": base["digest"] if base else "",
            "ask": ask_of(task) if task else None, "adds": json.loads(row["adds"]), "scope": json.loads(row["scope"]),
            "stands": _scope(json.loads(stands["payload"])) if stands else None, "goal": task["goal"] if task else None,
            "state": state_of(row, task, now), "created_at": row["created_at"], "expires_at": row["expires_at"],
            "decided_at": row["decided_at"], "version": row["version"]}


def waiting_in(db, user_id, now, task_id=None):
    """The account's asks that wait for the owner (one task's, with `task_id`), oldest first, each as widening_view
    gives it."""
    sql = ("SELECT w.* FROM ade_task_grant_widenings w JOIN ade_agent_tasks t ON t.id = w.task_id AND t.user_id = "
           "w.user_id WHERE w.user_id = ? AND w.state = 'waiting' AND w.expires_at > ? AND t.deleted_at IS NULL AND "
           "t.state NOT IN ('done', 'stopped')")
    args = [user_id, now]
    if task_id is not None:
        sql += " AND w.task_id = ?"
        args.append(task_id)
    return [widening_view(db, r, tasks.get(db, user_id, r["task_id"]), now)
            for r in db.execute(sql + " ORDER BY w.created_at, w.id", args).fetchall()]


def waiting(user_id, now=None):
    """The account's asks that wait for the owner: what the owner's list shows, and the summary counts."""
    now = _now(now)
    with store.connect() as db:
        return waiting_in(db, user_id, now)


def listing(user, now=None):
    """GET /tasks/grant-widenings: the owner's list of asks for more in their tasks' grants, each waiting."""
    return {"widenings": waiting(user["id"], now)}


def grant_view(db, task, now):
    """The task's grant as the page shows it, or None for a task with none: the version that stands (current), its
    scope, who asked for it and which ask it signs, the passkey that signed it, whether it pins the task's goal as it
    stands now, and whether its lease has ended; each version newer than it, withdrawn as the passkey that signed it
    went (passkeys_going), and why; and where the next version follows (`next`: its number and the digest it names).
    Where every version was withdrawn, no version stands: its version and digest are None, and it says no scope."""
    newest = head(db, task["id"], task["user_id"])
    if newest is None:
        return None
    row = current(db, task["id"], task["user_id"])
    withdrawn = [{"version": r["version"], "digest": r["digest"], "why": r["withdrawn_why"], "at": r["withdrawn_at"]}
                 for r in db.execute("SELECT version, digest, withdrawn_why, withdrawn_at FROM ade_task_grants WHERE "
                                     "task_id = ? AND user_id = ? AND version > ? ORDER BY version",
                                     (task["id"], task["user_id"], row["version"] if row else 0)).fetchall()]
    after = {"version": newest["version"] + 1, "prev": newest["digest"]}
    if row is None:
        return {"version": None, "digest": None, "withdrawn": withdrawn, "next": after}
    p = json.loads(row["payload"])
    key = db.execute("SELECT name FROM passkeys WHERE id = ?", (row["passkey_id"],)).fetchone()
    return dict({"version": row["version"], "digest": row["digest"], "signed_at": row["created_at"],
                 "requester": p["requester"], "widening": p["widening"] or None}, **_scope(p),
                ask=p["ask"], ask_current=p["ask"] == ask_of(task), lease_ended=p["lease_end"] <= now,
                passkey=key["name"] if key else None, withdrawn=withdrawn, next=after)


def grant_for(task):
    """What a grant of the task names, for the page to compose one from: its repository (None for a task with none),
    its own branch, and its ask."""
    return {"repo": task["repo"], "branch": own_branch(task), "ask": ask_of(task)}


def of_task(user, task_id, now=None):
    """GET /tasks/{id}/grant: the task's grant, each version as its passkey signed it (its canonical payload and the
    passkey's answer, so that anyone holding the account's passkeys' public keys can check it) and whether it was
    withdrawn (when, and why), and its asks that wait."""
    now = _now(now)
    with store.connect() as db:
        task = tasks._task(db, user["id"], task_id)
        versions = [{"version": r["version"], "digest": r["digest"], "payload": r["payload"],
                     "signature": json.loads(r["signature"]), "passkey_id": r["passkey_id"],
                     "requester": {"kind": r["requester_kind"], "id": r["requester_id"]},
                     "widening": r["widening_id"], "signed_at": r["created_at"],
                     "withdrawn": {"at": r["withdrawn_at"], "why": r["withdrawn_why"]} if r["withdrawn_at"] is not None
                     else None}
                    for r in db.execute("SELECT * FROM ade_task_grants WHERE task_id = ? AND user_id = ? ORDER BY "
                                        "version", (task["id"], user["id"])).fetchall()]
        return {"grant": grant_view(db, task, now), "versions": versions,
                "widenings": waiting_in(db, user["id"], now, task["id"])}
