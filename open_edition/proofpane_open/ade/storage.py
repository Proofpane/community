"""Where an account's new work lives, chosen by the account: in its repository, on its own machine, or in a workspace
this deployment runs for it. One choice per account, kept in ade_storage_choice. With none kept, an account that
already has a workspace reads `hosted`, so none loses it when this ships: under the fly provider one with an
ade_workspaces row, which only that provider writes; under the local provider any account it gives a workspace, since
it writes no row (the desktop app's among them, which starts its workspace with the app); any other account reads the
default, its own machine.

The choice gates what would start a hosted workspace, and nothing else: each provider's ensure asks may_start first, so
no workspace is started for an account whose choice is not `hosted`; a schedule's or a hook's firing is held with the
same sentence (triggers._held), a signed move is refused with it before it is held (moves.check_free), and a teammate's
hand-off, or a way back to the owner, that would start a workspace says it (task_offers), the way back waiting and
tried every round until the owner chooses `hosted` again. Reading the workspace's state, stopping it and
reading its finishes stay open, and so do linking sessions and connecting repositories under every choice, since the
places combine. A change of choice moves nothing, and left_behind says what stays where it was; a workspace left
behind under the fly provider is destroyed once its days are over (below).

Leaving the hosted choice stops the account's workspace at once, as the page's Stop does, after the page has
named what runs in it (left_behind); a stop that fails keeps the choice as it was. Under the fly provider the
workspace's machine, its volume and its record are then kept OPEN_EDITION_WORKSPACE_KEEP_DAYS days, the view giving the
date and time (workspace_view), and choosing `hosted` again within them keeps them, the next start starting the same
machine and volume; after them the provider's meter destroys them (fly_provider.sweep_left), and delete_workspace does
so at once, with a confirmation made for that act (passkeys.CHANGE's workspace-delete). The move, and its stop, hold
the lock the provider's start holds for the account, so a start under way and a move take turns. A workspace whose
record names another Fly app than the one this deployment makes workspaces in is out of its reach: neither kept for
days nor deleted from here. Under the local provider, which writes no record, the folder stays as it is.

The repository choice takes a read-only connection too: one that cannot write records (no token, a clone, or
a deployment that keeps no key-store key) shows a board and writes no records, and says so, here and on the connection,
each in words of its own (records_note), as does a token this deployment can no longer open, which shows no board
either; records need a token for that repository alone with Contents: read and write. A token's own permission is not
known here until GitHub takes a write or refuses it, saying why (sources.py), so the choice's sentence and the connect
form say that a read-only token shows a board only. On a deployment that keeps no key-store key the choice is said as
public repositories, board only.

An account that is not an admin, and is not the desktop app's own (the one with nothing to confirm with: no password and
no passkey, desktop.ensure_account), is asked once, when it chooses its own machine, to turn end-to-end encryption on;
its "not now" is kept, so it is not asked again. Turning it on stays the owner's own act (e2e_keys.py).
"""

import contextlib
import datetime
import os
import time

from .. import config, store
from . import workspaces

CHOICES = ("repository", "machine", "hosted")
DEFAULT = "machine"
LABELS = {"repository": "In your repository", "machine": "On your own machine",
          "hosted": "In a workspace this deployment runs for you"}
# where the account's work lives, as a refused start says it
WHERE = {"repository": "in its repository", "machine": "on its own machine"}

REPOSITORY = ("Your code and the ADE's signed answers, windows and stops live in your own repository on GitHub; this "
              "deployment keeps, in its own database, your connection and the token you give it, sealed, the commands "
              "it holds for your approval, its Verify results and a key for each alert it has tried to send you, and "
              "keeps what it reads from GitHub in its memory.")
READ_ONLY = ("A connection without a token, with a read-only one, or a clone on this deployment's machine, shows a "
             "board and writes no records: records need a token for that repository alone with Contents: read and "
             "write.")
REPOSITORY_NO_KEY = ("Your code lives in your own public repositories on GitHub, which this deployment shows you as a "
                     "board only: it keeps no key to seal a token with, so it takes none and writes no records (no "
                     "signed answers, windows or stops). It keeps, in its own database, your connections, the commands "
                     "it holds for your approval, its Verify results and a key for each alert it has tried to send you, "
                     "and keeps what it reads from GitHub in its memory.")
MACHINE = ("Your code and files stay where your linked sessions run, on your machine or in your own cloud sessions; this "
           "deployment keeps what those sessions send it (their status, questions, checkpoints, their list of sessions "
           "and, if you let them, their conversations) and the commands it holds for your approval.")
HOSTED_FLY = ("Your code, your files and your programs' sign-ins live on a machine this deployment runs for you; its "
              "operator can read them, and your share of the deployment's monthly cap, or the cap itself, can stop the "
              "machine.")
HOSTED_LOCAL = "Your code and files live in a workspace this app runs on its own machine, as the app's own user."
HOSTED_OFF = "This deployment runs no workspace."
REFUSED = ("This account keeps its work {where}, as Where your work lives in its account says, so this deployment "
           "starts no workspace for it: choose \"" + LABELS["hosted"] + "\" there to start one.")
# what a connection that cannot write records says on it, and why (records_note, routes.list_repos)
NO_TOKEN = ("Board only: this connection has no token, so it writes no records. Records need a token for this "
            "repository alone with Contents: read and write, given as it is connected.")
CLONE = ("Board only: a clone on this deployment's machine writes no records. Records need the repository connected "
         "from GitHub with a token for it alone with Contents: read and write.")
NO_KEY = "Board only: this deployment keeps no key to seal a token with, so a connection here writes no records."
SHUT = ("This deployment cannot open this connection's token now: its key-store key is gone or has changed. It shows "
        "no board and writes no records: remove it and connect the repository again.")
NO_CHOICE = "A choice is in your repository, on your own machine, or in a workspace this deployment runs for you."
CHOOSE_FIRST = "Choose where your work lives first."
# deleting a workspace left behind at once (delete_workspace)
STILL_HOSTED = ("Your work lives in your workspace: choose where else your work lives first, and the workspace is kept "
                "for its days, which you can then cut short.")
NO_WORKSPACE = "This deployment keeps no workspace of yours to delete."
LOCAL_DELETE = ("A workspace this app runs on its own machine is a folder there, which is not deleted from here: it "
                "stays, with everything in it.")
OTHER_APP = ("Your workspace is in the Fly app {app}, where this deployment no longer makes workspaces, so it cannot "
             "delete it from here: it stays there until this deployment's operator deletes it.")
# what a move away from a workspace in another Fly app says of it (left_behind)
ELSEWHERE = ("Your workspace's machine and its volume are in the Fly app {app}, where this deployment no longer makes "
             "workspaces, so it neither stops nor deletes them: they stay there, and its record here, until this "
             "deployment's operator deletes them.")


class Refused(workspaces.WorkspaceAccess):
    """A choice this account may not make here, or a value that is no choice: (HTTP status, why)."""


class NotHosted(workspaces.WorkspaceAccess):
    """A start refused because the account's choice is not `hosted`: 403, in the refusal's words, which a start keeps
    (workspace_routes._run_start) and a hand-off and a way back say (task_offers)."""


def _now(now):
    return int(now if now is not None else time.time())


def _hosted(user):
    """(the provider, None) where this deployment gives this account a workspace, else (None, why not)."""
    try:
        return workspaces.for_user(user), None
    except workspaces.WorkspaceAccess as e:
        return None, str(e)


def can_store_tokens():
    from . import ledger
    try:
        return ledger.keystore_key() is not None
    except ledger.LedgerError:
        return False


def can_write_records(conn):
    """Whether a connection (ledger's row) may write the ADE's records, as its board offers them: on GitHub, with a
    token, on a deployment that keeps a key-store key. Whether that key is the one the token was sealed with shows as
    the board is read, which fails otherwise; whether the token may write, as GitHub takes a write or refuses it,
    saying so (sources.py)."""
    return conn["source"] == "github" and bool(conn["has_token"]) and can_store_tokens()


def _token_opens(user_id, repo_id):
    """Whether the connection's sealed token opens under the key-store key this deployment keeps now."""
    from . import ledger
    with store.connect() as db:
        row = db.execute("SELECT token_box FROM ade_repos WHERE id = ? AND user_id = ?", (repo_id, user_id)).fetchone()
    if row is None or not row["token_box"]:
        return False
    try:
        ledger.unseal(row["token_box"], user_id, repo_id)
    except ledger.LedgerError:
        return False
    return True


def records_note(user_id, conn):
    """What a connection of `user_id`'s (ledger's row) says of its records: None where it may write them, else why
    not, in the words of its case: a clone, no token on a deployment that keeps a key-store key, none that keeps no
    key, or a token this deployment cannot open now."""
    if conn["source"] != "github":
        return CLONE
    if conn["has_token"]:
        return None if can_store_tokens() and _token_opens(user_id, conn["id"]) else SHUT
    return NO_TOKEN if can_store_tokens() else NO_KEY


def _provider_name():
    try:
        return workspaces.provider_name()
    except RuntimeError:
        return None


def _sentence(choice):
    if choice == "repository":
        return f"{REPOSITORY} {READ_ONLY}" if can_store_tokens() else REPOSITORY_NO_KEY
    if choice == "machine":
        return MACHINE
    name = _provider_name()
    return HOSTED_FLY if name == "fly" else HOSTED_LOCAL if name == "local" else HOSTED_OFF


def offered(user):
    """The three choices, each {choice, label, sentence, offered, why}: `hosted` offered only where this deployment
    gives this account a workspace (a provider, and its check_user passes), `why` saying why not; the others always."""
    provider, why = _hosted(user)
    out = []
    for choice in CHOICES:
        on = choice != "hosted" or provider is not None
        out.append({"choice": choice, "label": LABELS[choice], "sentence": _sentence(choice), "offered": on,
                    "why": None if on else why})
    return out


def _workspace_row(db, user_id):
    return db.execute("SELECT 1 FROM ade_workspaces WHERE user_id = ?", (user_id,)).fetchone() is not None


def _workspace_folder(user_id):
    """The local provider's folder for the account's workspace (workspaces.LocalProvider.root), where it was made."""
    return os.path.isdir(os.path.join(config.data_dir(), "workspaces", user_id))


def _has_workspace(db, user, provider):
    """Whether the account already has a workspace this deployment runs: under the local provider whenever it is given
    one, since that provider writes no row; else an ade_workspaces row, which only the fly provider writes."""
    if provider is None:
        return False
    if provider.name == "local":
        return True
    return _workspace_row(db, user["id"])


def _stored(db, user_id):
    return db.execute("SELECT * FROM ade_storage_choice WHERE user_id = ?", (user_id,)).fetchone()


def choice_for(user, db=None):
    """The account's choice: the one it made, else `hosted` where it already has a workspace, else the default."""
    if db is None:
        with store.connect() as conn:
            return choice_for(user, conn)
    row = _stored(db, user["id"])
    if row is not None:
        return row["choice"]
    provider, _ = _hosted(user)
    return "hosted" if _has_workspace(db, user, provider) else DEFAULT


def refusal(user, db=None):
    """The words a start is refused with now, or None where the account's choice is `hosted`."""
    choice = choice_for(user, db)
    return None if choice == "hosted" else REFUSED.format(where=WHERE.get(choice, choice))


def may_start(user, db=None):
    """Raise NotHosted (403, in the refusal's words) where the account's choice is not `hosted`."""
    said = refusal(user, db)
    if said is not None:
        raise NotHosted(403, said)


def _count(db, sql, args):
    return db.execute(sql, args).fetchone()[0]


def _approvals(db, uid):
    """The commands held for the account's approval, which stay whatever the choice, since nothing deletes one."""
    n = _count(db, "SELECT COUNT(*) FROM ade_requests WHERE user_id = ?", (uid,))
    if not n:
        return []
    return [(f"{n} commands held for your approval stay" if n != 1 else "1 command held for your approval stays")
            + ", since nothing deletes one."]


def left_behind(user, choice, db=None):
    """What a move from the account's current choice to `choice` leaves where it was, in sentences,
    read from the account's rows: [] where `choice` is the current one or nothing is there. A move
    deletes nothing at once; a workspace left behind under the fly provider is destroyed after its days, as these
    say."""
    if db is None:
        with store.connect() as conn:
            return left_behind(user, choice, conn)
    current = choice_for(user, db)
    if choice == current:
        return []
    uid = user["id"]
    out = []
    if current == "repository":
        n = _count(db, "SELECT COUNT(*) FROM ade_repos WHERE user_id = ?", (uid,))
        if n == 1:
            out.append("Your connection to a repository stays, its token sealed if it has one, and the board and its "
                       "alerts go on reading it: disconnecting it is a separate act.")
        elif n:
            out.append(f"Your {n} connections to repositories stay, any token sealed, and the board and its alerts go "
                       "on reading them: disconnecting one is a separate act.")
        out += _approvals(db, uid)
    elif current == "machine":
        n = _count(db, "SELECT COUNT(*) FROM ade_links WHERE user_id = ? AND revoked_at IS NULL", (uid,))
        if n == 1:
            out.append("Your linked session stays linked and goes on sending what it sends here, and its machine "
                       "keeps everything it had: revoking its link is a separate act.")
        elif n:
            out.append(f"Your {n} linked sessions stay linked and go on sending what they send here, and each "
                       "machine keeps everything it had: revoking a link is a separate act.")
        out += _approvals(db, uid)
        n = _count(db, "SELECT COUNT(*) FROM ade_hook_tokens WHERE user_id = ? AND revoked_at IS NULL", (uid,))
        if n:
            out.append(f"{n} hook tokens stay until you revoke them." if n != 1 else
                       "1 hook token stays until you revoke it.")
    else:
        row = db.execute("SELECT app FROM ade_workspaces WHERE user_id = ?", (uid,)).fetchone()
        folder = _workspace_folder(uid)
        if row or folder:
            out += _what_runs(user, db)
        if row and row["app"] == _fly_app():
            out += _kept_words()
        elif row:
            out.append(ELSEWHERE.format(app=row["app"]))
        if folder:
            out.append("Your workspace's folder on this machine stays, with everything in it.")
        if row or folder:
            out.append("What this deployment keeps of your workspace's agents, tasks, Manager and finishes stays.")
        n = _count(db, "SELECT COUNT(*) FROM ade_agent_task_offers WHERE user_id = ? AND state = 'held'", (uid,))
        if n == 1:
            out.append("1 hand-off of yours waits on its hold: it pushes from your workspace as the hold ends, and "
                       "keeping this stops the workspace, so it ends failed unless the workspace runs again by then.")
        elif n:
            out.append(f"{n} hand-offs of yours wait on their holds: each pushes from your workspace as its hold ends, "
                       "and keeping this stops the workspace, so each ends failed unless the workspace runs again by "
                       "then.")
        n = _count(db, "SELECT COUNT(*) FROM ade_agent_task_offers WHERE user_id = ? AND state = 'accepted'", (uid,))
        if n:
            out.append((f"{n} tasks of yours are with a teammate: the way back of each" if n != 1 else
                        "1 task of yours is with a teammate: its way back") + " brings the work into your workspace "
                       "while it runs, and starts it when it does not, so after this, which stops it, the way back "
                       "waits, tried every round, until you choose the workspace this deployment runs for you again; "
                       "it then brings the work into that workspace, a new one if this one has been deleted by then.")
    return out


def _provider():
    """This deployment's provider, whether or not it gives this account a workspace now (a stop stays open); None where
    it runs none."""
    try:
        return workspaces.provider()
    except RuntimeError:
        return None


def _fly_app():
    """The Fly app this deployment makes workspaces in, or None where it makes none (fly_provider.settings)."""
    from . import fly_provider
    try:
        return fly_provider.settings().app
    except RuntimeError:
        return None


def _holding(user):
    """The lock the provider's start holds for the account (the fly provider's holding), which a move away from the
    hosted choice holds too while it stops the workspace and keeps its choice; none where the provider has none."""
    hold = getattr(_provider(), "holding", None)
    return hold(user) if hold else contextlib.nullcontext()


def _keep_days():
    """How many days a workspace left behind is kept: the fly provider's setting, which only it keeps one for."""
    from . import fly_provider
    try:
        return fly_provider.settings().keep_days
    except RuntimeError:
        return int(os.environ.get("OPEN_EDITION_WORKSPACE_KEEP_DAYS", "").strip() or 30)


def _date(t):
    """A day and its time as the server says them in a sentence: 29 November 2026, 09:00 (UTC)."""
    d = datetime.datetime.fromtimestamp(t, datetime.timezone.utc)
    return f"{d.day} {d.strftime('%B %Y')}, {d.strftime('%H:%M')} (UTC)"


def _what_runs(user, db):
    """What runs in the account's workspace now, as a move away from it names before it is confirmed: whether its
    provider says it is running (the fly provider from the state it read a moment ago, so a read of the page does not
    ask Fly each time), and its agents' runs under way, an improve loop's among them, which keeping the move stops."""
    p = _provider()
    try:
        ask = getattr(p, "running_now", None)
        running = bool(p is not None and (ask(user) if ask else p.status(user).get("running")))
    except workspaces.WorkspaceAccess:
        return ["Keeping this stops your workspace at once if it is running, as Stop does, and what runs in it."]
    if not running:
        return []
    n = _count(db, "SELECT COUNT(*) FROM ade_agent_runs WHERE user_id = ? AND state IN ('working', 'waiting')",
               (user["id"],))
    runs = (f": {n} of your agents' runs are under way in it" if n > 1
            else ": 1 of your agents' runs is under way in it" if n else "")
    return ["Your workspace is running now: keeping this stops it at once, as Stop does, and every session in "
            f"it{runs}."]


def _kept_words(now=None):
    """What a move away from a hosted workspace keeps of it, and until when."""
    days = _keep_days()
    span = f"{days} days" if days != 1 else "1 day"
    until = _date(_now(now) + days * 86400)
    return [f"Your workspace's machine, its volume, with your code, your files and your programs' sign-ins on it, and "
            f"its record here are kept for {span}, until {until}: choose a workspace this deployment runs for you "
            "again before then and they stay yours, its next start starting the same machine and volume as your share "
            "and this deployment's cap allow. Meanwhile the volume and the machine's disk count toward your share and "
            "this deployment's monthly cap, and the record counts toward the number of workspaces this deployment "
            "allows.",
            f"After {span} this deployment destroys the machine and the volume, with everything on it, and deletes "
            "the record; you can delete them at once from here instead, once this is kept."]


def _leave(user):
    """Stop the account's workspace at once, as the page's Stop does: a stop that fails raises, so the
    choice is not kept."""
    p = _provider()
    if p is not None:
        p.stop(user)


def choose(user, choice, now=None):
    """Keep `choice` as the account's: refused (400) where it is no choice, and (409, saying why) where this deployment
    does not offer it to this account. A move away from `hosted` stops the account's workspace first, and under the
    fly provider keeps it from now (ade_workspaces.left_at); a move to `hosted` keeps it no longer. Both are made under
    the lock the provider's start holds for the account (_holding). Returns view()."""
    if choice not in CHOICES:
        raise Refused(400, NO_CHOICE)
    if choice == "hosted":
        _, why = _hosted(user)
        if why is not None:
            raise Refused(409, why)
    with _holding(user):
        if choice != "hosted" and choice_for(user) == "hosted":
            _leave(user)
        now = _now(now)
        with store.connect() as db:
            db.execute("INSERT INTO ade_storage_choice (user_id, choice, chosen_at) VALUES (?,?,?) ON "
                       "CONFLICT(user_id) DO UPDATE SET choice = excluded.choice, chosen_at = excluded.chosen_at",
                       (user["id"], choice, now))
            if choice == "hosted":
                db.execute("UPDATE ade_workspaces SET left_at = NULL WHERE user_id = ?", (user["id"],))
            else:
                db.execute("UPDATE ade_workspaces SET left_at = ? WHERE user_id = ? AND left_at IS NULL",
                           (now, user["id"]))
    return view(user)


def workspace_view(user, db):
    """{share, kept_until, keep_days, removed}: under the fly provider, the account's month against its share of the cap
    where this deployment gives it a workspace; when a workspace it left is kept until, only one
    in the Fly app this deployment makes workspaces in; and, where it has none now, the last one removed, when and why.
    Each None where it does not apply."""
    out = {"share": None, "kept_until": None, "keep_days": None, "removed": None}
    if _provider_name() != "fly":
        return out
    out["keep_days"] = _keep_days()
    provider, _ = _hosted(user)
    if provider is not None and hasattr(provider, "share"):
        out["share"] = provider.share(user)
    row = db.execute("SELECT left_at, app FROM ade_workspaces WHERE user_id = ?", (user["id"],)).fetchone()
    stored = _stored(db, user["id"])
    if (row is not None and row["left_at"] is not None and row["app"] == _fly_app() and stored is not None
            and stored["choice"] != "hosted"):
        out["kept_until"] = row["left_at"] + out["keep_days"] * 86400
    if row is None:
        gone = db.execute("SELECT removed_at, why FROM ade_workspace_removals WHERE user_id = ? ORDER BY "
                          "removed_at DESC, id DESC LIMIT 1", (user["id"],)).fetchone()
        if gone is not None:
            out["removed"] = {"removed_at": gone["removed_at"], "why": gone["why"]}
    return out


def delete_workspace(user, confirmation, now=None):
    """Delete the account's workspace left behind at once: refused (409) while its choice is `hosted`, (404)
    where it has none, (409) where its record names another Fly app or it has kept no choice, which would delete
    nothing, and (403, from passkeys) without a confirmation made for this act, workspace-delete, which is used up
    before anything is destroyed and only once each of those is past. The fly provider destroys the machine and its
    volume and deletes the record (fly_provider.remove_workspace); a failure at Fly is said (502) and the record kept.
    Under the local provider the workspace is a folder, which is not deleted from here (404). Returns view()."""
    from .. import passkeys
    from . import fly_provider
    name = _provider_name()
    if name != "fly":
        raise Refused(404, LOCAL_DELETE if name == "local" else HOSTED_OFF)
    with store.connect() as db:
        if choice_for(user, db) == "hosted":
            raise Refused(409, STILL_HOSTED)
        row = db.execute("SELECT app FROM ade_workspaces WHERE user_id = ?", (user["id"],)).fetchone()
        if row is None:
            raise Refused(404, NO_WORKSPACE)
        if row["app"] != _fly_app():
            raise Refused(409, OTHER_APP.format(app=row["app"]))
        if _stored(db, user["id"]) is None:
            raise Refused(409, CHOOSE_FIRST)
    passkeys.use_confirmation(user["id"], confirmation, "workspace-delete", now)
    try:
        fly_provider.remove_workspace(fly_provider.settings(), user["id"], fly_provider.DELETED_WHY, now)
    except fly_provider.FlyError as e:
        raise workspaces.WorkspaceAccess(502, f"Fly: {e}") from None
    return view(user)


def _desktop_account(user):
    """The desktop app's own account: the one with nothing to confirm with, no password and no passkey
    (desktop.ensure_account; passkeys.confirm_ways), which signs in only by the token the app gives its shell."""
    from .. import passkeys
    return not passkeys.confirm_ways(user["id"])["needed"]


def e2e_state(user, db=None):
    """{on, not_now, ask}: whether the account's end-to-end encryption is on, whether it said "not now", and whether the
    page asks it now: its choice, made, its own machine; encryption off; no "not now" kept; not an admin, and not the
    desktop app's own account."""
    if db is None:
        with store.connect() as conn:
            return e2e_state(user, conn)
    from . import e2e_keys
    on = bool(e2e_keys.on(db, user["id"]))
    row = _stored(db, user["id"])
    not_now = bool(row is not None and row["e2e_not_now"])
    ask = (row is not None and row["choice"] == "machine" and not on and not not_now and not user.get("admin")
           and not _desktop_account(user))
    return {"on": on, "not_now": not_now, "ask": ask}


def e2e_not_now(user, now=None):
    """The account's "not now" to the question, kept so it is not asked again; its Encryption settings still turn it
    on. Refused (409) before the account has chosen. Returns view()."""
    now = _now(now)
    with store.connect() as db:
        if _stored(db, user["id"]) is None:
            raise Refused(409, CHOOSE_FIRST)
        db.execute("UPDATE ade_storage_choice SET e2e_not_now = ? WHERE user_id = ?", (now, user["id"]))
    return view(user)


def view(user):
    """What the page shows: the choice, whether the account made it, the words a start is refused with now, the three
    choices, what a move to each leaves, the encryption question, and the account's workspace (workspace_view)."""
    with store.connect() as db:
        current = choice_for(user, db)
        # what a move leaves depends only on where the work is now, so it is read once (a running workspace's state is
        # asked of its provider)
        leaves = left_behind(user, next(c for c in CHOICES if c != current), db)
        return {"choice": current, "chosen": _stored(db, user["id"]) is not None, "refusal": refusal(user, db),
                "choices": offered(user), "left_behind": {c: [] if c == current else list(leaves) for c in CHOICES},
                "e2e": e2e_state(user, db), "workspace": workspace_view(user, db)}
