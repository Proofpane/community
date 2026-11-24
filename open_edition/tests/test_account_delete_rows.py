"""What deleting an account removes and what it keeps, table by table (queue 4.327's ruling of 2026-11-22, 06:40 UTC):
every row keyed by the account goes, in each table with a user_id column, in the tables under its rows (a repository's
verifications, a link's puts and encryption, a grant's tokens, an evaluation's items), the custody it owns, the device
sign-ins it approved and the invitation naming its address, and the account itself; other accounts' rows that name it
are kept, as their own rules keep them, the id then naming no account; the deployment's spending keeps its seconds, the
account's id taken from them. Every table of the store is filled for two accounts, one deleted and one kept, and read
back whole: what changed is exactly that. A table added later with a user_id column is swept with the rest; one added
without one is named here, or the census fails. Also: another account's standing offer to it and custody it holds or
was asked for end as the app ends them; its moves' files go once it has committed; and its Fly machine and volume are
destroyed then, kept by Fly's ids alone until a destroy succeeds."""

import itertools
import os
import time

import pytest

from conftest import ADMIN, signed_up
from proofpane_open import account_delete, store
from proofpane_open.ade import fly_provider, holds, moves, push
from test_account_delete import me, place, word
from test_ade_link import paired, post
from test_hosted_keep import APP, fly, hosted_member, row  # noqa: F401  (fly: a fixture)

# the tables with no user_id column, each with the column naming the deleted account's rows in it: under a row of
# another table, by that row's id; naming the account in a column of their own; or the deployment's own, naming none
UNDER = {"ade_verifications": ("repo_id", "ade_repos"), "ade_link_history_puts": ("link_id", "ade_links"),
         "ade_link_e2e": ("link_id", "ade_links"), "ade_connector_tokens": ("grant_id", "ade_connector_grants"),
         "ade_agent_eval_items": ("eval_id", "ade_agent_evals")}
NAMING = {"users": "id", "device_signins": "approved_by", "invitations": "email", "ade_task_custody": "owner_user"}
GLOBAL = {"account_deleted_fly", "account_deletions", "ade_connector_clients", "ade_connector_documents_known",
          "ade_workspace_meter", "fido_mds_accepted", "passkey_reconsidered", "passkey_used_challenges"}
# the columns of the kept account's rows that may still name the deleted one: by id, and by address
KEPT_BY_ID = {("ade_agent_task_offers", "teammate_user"), ("ade_task_custody", "custodian_user"),
              ("ade_task_account_record", "accounts"), ("ade_agent_tasks", "continued_from")}
# the address another account typed for it is cleared; the one the server wrote as a handed task's sender, kept in the
# task another account received from it (continued_from's handed.from), is not (the record's question)
KEPT_BY_ADDRESS = {("ade_agent_tasks", "continued_from")}
GONE_MAKER = "a deleted account"        # an invitation it made for someone else names this as its maker instead
CHECKED = {("ade_storage_choice", "choice"): "machine"}       # a column whose CHECK takes only certain values
SPEND = "ade_workspace_spend"


def tables(db):
    return [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                                     "ORDER BY name")]


def columns(db, table):
    return db.execute(f"PRAGMA table_info({table})").fetchall()


def keyed(db):
    return [t for t in tables(db) if "user_id" in {c[1] for c in columns(db, t)}]


_n = itertools.count(1000)


def put(db, table, **values):
    """One row of `table`: the values given, and in every other column that must hold one, a value of its type made
    up for it, unique in the store, naming no account."""
    row = {}
    for _, name, kind, notnull, _, pk in columns(db, table):
        if name in values:
            row[name] = values[name]
        elif notnull or pk:
            n = next(_n)
            kind = (kind or "").upper()
            row[name] = CHECKED.get((table, name)) or (n if "INT" in kind else float(n) if "REAL" in kind
                                                       else f"{table}.{name}.{n}")
    db.execute(f"INSERT OR IGNORE INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
               tuple(row.values()))


def everything(db):
    return {t: sorted((tuple(r) for r in db.execute(f"SELECT * FROM {t}")), key=repr) for t in tables(db)}


def naming(db, needle):
    """(table, column) of every value in the store that holds `needle`."""
    found = set()
    for t in tables(db):
        names = [c[1] for c in columns(db, t)]
        for r in db.execute(f"SELECT * FROM {t}"):
            for name, value in zip(names, r):
                if isinstance(value, str) and needle in value or isinstance(value, bytes) and needle.encode() in value:
                    found.add((t, name))
    return found


@pytest.fixture
def quiet(monkeypatch):
    """Notices sent at once, to no push service: the made-up devices' addresses are no push service's."""
    monkeypatch.setattr(push, "_spawn", lambda work: work())
    monkeypatch.setattr(push, "_default_post", lambda *a, **k: None)


@pytest.fixture
def two(client):
    """Two accounts: the one whose deletion waits (its headers, id and address), and the one kept."""
    gone = signed_up(client)
    kept = signed_up(client, email="bo@example.com")
    return gone, me(client, gone), ADMIN, me(client, kept), "bo@example.com"


def test_every_table_is_keyed_by_the_account_under_a_row_of_its_own_names_it_or_is_the_deployments(client):
    signed_up(client)
    with store.connect() as db:
        without = set(tables(db)) - set(keyed(db))
    assert without == set(UNDER) | set(NAMING) | GLOBAL, without ^ (set(UNDER) | set(NAMING) | GLOBAL)


def test_every_row_of_the_deleted_account_goes_and_every_row_of_the_kept_one_stays(client, two, quiet):
    h, gone, gone_email, kept, kept_email = two
    d = place(client, h, word(client, h, "account-delete")).json()["deletion"]
    with store.connect() as db:
        for t in keyed(db):
            for who in (gone, kept):
                parent = {"id": f"{t}-{who}"} if t in {p for _, p in UNDER.values()} else {}
                put(db, t, user_id=who, **parent)
        for t, (column, parent) in UNDER.items():
            for who in (gone, kept):
                put(db, t, **{column: f"{parent}-{who}"})
        for who in (gone, kept):
            put(db, "device_signins", approved_by=who)
            put(db, "ade_task_custody", id=f"custody-of-{who}", owner_user=who, state="ended", agents="[]")
        # the kept account's own rows that name the deleted one, by id and by address
        put(db, "ade_agent_task_offers", id="offer-taken", user_id=kept, teammate_user=gone, address=gone_email,
            state="accepted")
        put(db, "ade_task_custody", id="custody-held", owner_user=kept, custodian_user=gone, address=gone_email,
            state="ended", agents="[]")
        put(db, "ade_task_account_record", user_id=kept, accounts='{"teammate": "%s"}' % gone)
        put(db, "ade_agent_tasks", user_id=kept, continued_from='{"from_user": "%s", "task": "t1", "handed": {"from": '
            '"%s", "done_steps": [], "outward_effects": []}}' % (gone, gone_email))
        put(db, "invitations", token_hash="carol", email="carol@example.com", created_by=gone_email)
        # the fill reached every table keyed by the account, the deployment's spending among them
        assert naming(db, gone) >= {(t, "user_id") for t in keyed(db)} and SPEND in keyed(db)
        before = everything(db)

    holds.settle(now=d["takes_effect_at"])

    with store.connect() as db:
        after = everything(db)
        assert naming(db, gone) == KEPT_BY_ID
        assert naming(db, gone_email) == KEPT_BY_ADDRESS
        cols = {t: [c[1] for c in columns(db, t)] for t in after}
    # what changed is exactly the deleted account's rows gone, its spending's seconds kept without its id, its address
    # taken from an invitation it made, and the record of the deletion done
    parents = {f"{p}-{gone}" for _, p in UNDER.values()}

    def its(t, r):
        named = dict(zip(cols[t], r))
        return (named.get("user_id") == gone or t in UNDER and named[UNDER[t][0]] in parents
                or t in NAMING and named[NAMING[t]] in (gone, gone_email))
    for t in after:
        want = [r for r in before[t] if not its(t, r)]
        if t == SPEND:
            want += [tuple(None if c == "user_id" else v for c, v in zip(cols[t], r)) for r in before[t] if its(t, r)]
        if t == "account_deletions":
            want += [(d["id"], d["takes_effect_at"])]
        if t == "invitations":
            want = [tuple(GONE_MAKER if c == "created_by" and v == gone_email else v for c, v in zip(cols[t], r))
                    for r in want]
        naming_column = {"ade_agent_task_offers": "teammate_user", "ade_task_custody": "custodian_user"}.get(t)
        if naming_column:
            want = [tuple(None if c == "address" and dict(zip(cols[t], r))[naming_column] == gone else v
                          for c, v in zip(cols[t], r)) for r in want]
        assert after[t] == sorted(want, key=repr), t
    assert any(dict(zip(cols["users"], r))["id"] == kept for r in after["users"])
    assert all(dict(zip(cols["users"], r))["id"] != gone for r in after["users"])


def test_another_accounts_standing_offer_and_custody_naming_it_end_as_the_app_ends_them(client, two, quiet):
    h, gone, gone_email, kept, _ = two
    d = place(client, h, word(client, h, "account-delete")).json()["deletion"]
    with store.connect() as db:
        put(db, "ade_agent_task_offers", id="offer-out", user_id=kept, teammate_user=gone, address=gone_email,
            state="offered", kind="pressed", hold_id=None)
        put(db, "ade_agent_task_offers", id="offer-drafted", user_id=kept, teammate_user=gone, address=gone_email,
            state="draft", kind="pressed", hold_id=None)
        put(db, "ade_task_custody", id="custody-held", owner_user=kept, custodian_user=gone, address=gone_email,
            state="accepted", confirmed_at=1, agents='["a1"]')
        put(db, "ade_task_custody", id="custody-asked", owner_user=kept, custodian_user=gone, address=gone_email,
            state="asked", confirmed_at=1, agents='["a1"]')
    holds.settle(now=d["takes_effect_at"])
    with store.connect() as db:
        offer = db.execute("SELECT * FROM ade_agent_task_offers WHERE id = 'offer-out'").fetchone()
        assert db.execute("SELECT 1 FROM ade_agent_task_offers WHERE id = 'offer-drafted'").fetchone() is None
        held = db.execute("SELECT * FROM ade_task_custody WHERE id = 'custody-held'").fetchone()
        asked = db.execute("SELECT * FROM ade_task_custody WHERE id = 'custody-asked'").fetchone()
    assert (offer["state"], offer["why"], offer["teammate_user"], offer["address"]) == (
        "failed", "The teammate's account was deleted.", None, None)
    assert (held["state"], held["ended_by"], held["why"], held["custodian_user"]) == (
        "ended", "app", "The custodian's account was deleted.", gone)          # an accepted custody's, kept its days
    assert (asked["state"], asked["ended_by"], asked["custodian_user"], asked["address"], asked["agents"]) == (
        "ended", "app", None, None, "[]")                                       # a request never accepted keeps none


def test_its_moves_files_go_once_it_has_committed_and_the_kept_accounts_stay(client, two, quiet):
    h, gone, _, kept, _ = two
    d = place(client, h, word(client, h, "account-delete")).json()["deletion"]
    with store.connect() as db:
        put(db, "ade_link_moves", seq=901, user_id=gone)
        put(db, "ade_link_moves", seq=902, user_id=kept)
    for seq in (901, 902):
        os.makedirs(moves.folder(seq))
        with open(os.path.join(moves.folder(seq), "work.txt"), "w") as f:
            f.write("a session's files")
    holds.settle(now=d["takes_effect_at"])
    assert not os.path.exists(moves.folder(901)) and os.path.exists(os.path.join(moves.folder(902), "work.txt"))


def test_its_workspace_at_fly_is_destroyed_once_it_has_committed_kept_by_flys_ids_alone_until_then(fly, client,  # noqa: F811
                                                                                                   quiet, monkeypatch):
    h, member = hosted_member(client, fly)
    machine, volume = row(member["id"])["machine_id"], row(member["id"])["volume_id"]
    _, living = hosted_member(client, fly, email="living@example.com")          # another account's workspace, running
    theirs = (row(living["id"])["machine_id"], row(living["id"])["volume_id"])
    fly.refuse_deletes.add(volume)                                   # Fly will not destroy its volume, this time
    d = place(client, h, word(client, h, "account-delete")).json()["deletion"]
    later = []
    monkeypatch.setattr(account_delete, "_spawn", later.append)      # Fly's calls go in a thread of their own
    holds.settle(now=d["takes_effect_at"])
    assert fly.machines[machine]["state"] != "destroyed"            # so the settle that applied it waits for none
    (work,) = later
    work()
    assert fly.machines[machine]["state"] == "destroyed" and fly.volumes[volume]["state"] != "destroyed"
    with store.connect() as db:
        assert row(member["id"]) is None
        assert [tuple(r) for r in db.execute("SELECT * FROM account_deleted_fly")] == [
            (APP, "volumes", volume, d["takes_effect_at"])]
    fly.refuse_deletes.discard(volume)
    fly_provider.meter_tick(fly_provider.settings(), now=d["takes_effect_at"] + 60)
    assert fly.volumes[volume]["state"] == "destroyed"
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM account_deleted_fly").fetchone()[0] == 0
    # the other account's machine and volume were never kept for destroying, and still run
    assert fly.machines[theirs[0]]["state"] != "destroyed" and fly.volumes[theirs[1]]["state"] != "destroyed"
    assert not [c for c in fly.calls("DELETE") if theirs[0] in str(c) or theirs[1] in str(c)]


def test_its_fly_ids_are_kept_as_its_rows_go_whatever_becomes_of_the_destroy_after(fly, client, quiet,  # noqa: F811
                                                                                  monkeypatch):
    h, member = hosted_member(client, fly)
    machine, volume = row(member["id"])["machine_id"], row(member["id"])["volume_id"]
    monkeypatch.setattr(fly_provider, "destroy_deleted", lambda *a, **k: None)     # as if the process ended at commit
    d = place(client, h, word(client, h, "account-delete")).json()["deletion"]
    holds.settle(now=d["takes_effect_at"])
    with store.connect() as db:
        kept = sorted(tuple(r) for r in db.execute("SELECT app, kind, fly_id FROM account_deleted_fly"))
    assert kept == sorted([(APP, "machines", machine), (APP, "volumes", volume)])
    assert fly.machines[machine]["state"] != "destroyed"
    fly_provider.meter_tick(fly_provider.settings(), now=d["takes_effect_at"] + 60)
    assert fly.machines[machine]["state"] == "destroyed" and fly.volumes[volume]["state"] == "destroyed"


def test_what_a_start_under_way_leaves_for_it_at_fly_is_destroyed_and_its_rows_go(fly, client, quiet,  # noqa: F811
                                                                                 monkeypatch):
    h, member = hosted_member(client, fly)
    machine, volume = row(member["id"])["machine_id"], row(member["id"])["volume_id"]
    with store.connect() as db:                         # its row not written yet, as a first start's before it is
        db.execute("DELETE FROM ade_workspaces WHERE user_id = ?", (member["id"],))
    d = place(client, h, word(client, h, "account-delete")).json()["deletion"]

    def start_ends_first(work):                         # the start, holding the account's lock, writes its rows first
        with store.connect() as db:
            put(db, "ade_workspaces", user_id=member["id"], app=APP, machine_id=machine, volume_id=volume)
            put(db, "ade_hook_tokens", user_id=member["id"])
            put(db, "ade_workspace_removals", user_id=member["id"])
        work()
    monkeypatch.setattr(account_delete, "_spawn", start_ends_first)
    holds.settle(now=d["takes_effect_at"])
    assert fly.machines[machine]["state"] == "destroyed" and fly.volumes[volume]["state"] == "destroyed"
    with store.connect() as db:
        assert naming(db, member["id"]) == set()


def test_the_meter_writes_no_deleted_accounts_id_back_into_the_spending(fly, client, quiet,  # noqa: F811
                                                                        monkeypatch):
    h, member = hosted_member(client, fly)
    machine = row(member["id"])["machine_id"]
    stale = fly_provider._owners(fly_provider.settings())             # read before the deletion committed
    d = place(client, h, word(client, h, "account-delete")).json()["deletion"]
    monkeypatch.setattr(account_delete, "_spawn", lambda work: None)    # its machine still runs, not destroyed yet
    holds.settle(now=d["takes_effect_at"])
    monkeypatch.setattr(fly_provider, "_owners", lambda s: stale)
    fly_provider.meter_tick(fly_provider.settings(), now=d["takes_effect_at"] + 60)
    with store.connect() as db:
        spent = db.execute(f"SELECT user_id FROM {SPEND} WHERE machine_id = ?", (machine,)).fetchall()
    assert spent and all(r["user_id"] is None for r in spent)


def test_what_its_own_linked_session_writes_as_the_deletion_takes_effect_is_swept_after(client, monkeypatch, quiet):
    monkeypatch.setenv(account_delete.SETTING, "86400")
    h = signed_up(client)
    gone = me(client, h)
    s = paired(client, h)
    d = place(client, h, word(client, h, "account-delete")).json()["deletion"]
    later = d["takes_effect_at"] + 1
    monkeypatch.setattr(time, "time", lambda: later)
    post(s, "note", "a note sent as its account was deleted")   # the request's own settle applies the deletion
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM users WHERE id = ?", (gone,)).fetchone()[0] == 0
        assert naming(db, gone)                         # then it wrote, for an account no longer there
    account_delete.round_once(now=later + 60)
    with store.connect() as db:
        assert naming(db, gone) == set()


def test_the_sweep_takes_the_deleted_accounts_stragglers_alone_and_only_in_its_window(client, quiet):
    h = signed_up(client)
    user = me(client, h)
    with store.connect() as db:                         # a row of an id no account has, which no deletion named
        put(db, "ade_push_seen", user_id="no-such-account")
    d = place(client, h, word(client, h, "account-delete")).json()["deletion"]
    holds.settle(now=d["takes_effect_at"])
    with store.connect() as db:                         # a straggler, written after the deletion committed
        put(db, "ade_agent_sync", user_id=user)
    account_delete.round_once(now=d["takes_effect_at"] + account_delete.SWEEP_AFTER)
    with store.connect() as db:
        assert naming(db, user) == set()
        assert naming(db, "no-such-account") == {("ade_push_seen", "user_id")}     # no deletion named it
        put(db, "ade_agent_sync", user_id=user)         # a straggler after the window
    account_delete.round_once(now=d["takes_effect_at"] + account_delete.SWEEP_AFTER + 1)
    with store.connect() as db:
        assert naming(db, user) == {("ade_agent_sync", "user_id")}


def test_what_a_start_wrote_for_it_goes_though_flys_listing_fails(fly, client, quiet, monkeypatch):  # noqa: F811
    h, member = hosted_member(client, fly)
    machine, volume = row(member["id"])["machine_id"], row(member["id"])["volume_id"]
    d = place(client, h, word(client, h, "account-delete")).json()["deletion"]
    real = fly_provider.Machines.call

    def no_listing(self, method, path, *args, **kwargs):
        if method == "GET" and path in ("/machines", "/volumes"):
            raise fly_provider.FlyError(503, "Fly's listing is down.")
        return real(self, method, path, *args, **kwargs)
    monkeypatch.setattr(fly_provider.Machines, "call", no_listing)
    monkeypatch.setattr(fly_provider, "LIST_WAITS", (0, 0))

    def start_ends_first(work):
        with store.connect() as db:
            put(db, "ade_workspaces", user_id=member["id"], app=APP, machine_id=machine, volume_id=volume)
            put(db, "ade_hook_tokens", user_id=member["id"])
        work()
    monkeypatch.setattr(account_delete, "_spawn", start_ends_first)
    holds.settle(now=d["takes_effect_at"])
    with store.connect() as db:
        assert naming(db, member["id"]) == set()
        assert db.execute("SELECT COUNT(*) FROM account_deleted_fly").fetchone()[0] == 0
    # what its row named, kept as the deletion went, is destroyed without the listing
    assert fly.machines[machine]["state"] == "destroyed" and fly.volumes[volume]["state"] == "destroyed"


def test_ids_a_start_wrote_after_the_deletion_are_kept_and_destroyed_though_flys_listing_fails(fly, client,  # noqa: F811
                                                                                              quiet, monkeypatch):
    h, member = hosted_member(client, fly)
    d = place(client, h, word(client, h, "account-delete")).json()["deletion"]
    s_ = fly_provider.settings()
    api = fly_provider.Machines(s_)
    real = fly_provider.Machines.call

    def no_listing(self, method, path, *args, **kwargs):
        if method == "GET" and path in ("/machines", "/volumes"):
            raise fly_provider.FlyError(503, "Fly's listing is down.")
        return real(self, method, path, *args, **kwargs)
    made = {}

    def start_replaces_its_machine(work):               # a start under way writes ids the deletion never saw
        volume = api.call("POST", "/volumes", {"name": "ws_other", "region": "syd", "size_gb": 1, "encrypted": True})
        machine = api.call("POST", "/machines", {"name": "ws-other", "region": "syd", "config": {"image": "x"}})
        made.update(machine=machine["id"], volume=volume["id"])
        with store.connect() as db:
            put(db, "ade_workspaces", user_id=member["id"], app=APP, machine_id=machine["id"], volume_id=volume["id"])
        monkeypatch.setattr(fly_provider.Machines, "call", no_listing)
        work()
    monkeypatch.setattr(fly_provider, "LIST_WAITS", (0, 0))
    monkeypatch.setattr(account_delete, "_spawn", start_replaces_its_machine)
    holds.settle(now=d["takes_effect_at"])
    assert fly.machines[made["machine"]]["state"] == "destroyed" and fly.volumes[made["volume"]]["state"] == "destroyed"
    with store.connect() as db:
        assert naming(db, member["id"]) == set()


def test_the_fly_cleanup_waits_for_a_start_holding_the_accounts_lock(fly, client, quiet, monkeypatch):  # noqa: F811
    import threading
    h, member = hosted_member(client, fly)
    d = place(client, h, word(client, h, "account-delete")).json()["deletion"]
    held, written = threading.Event(), threading.Event()

    def start():                                        # a start holding the account's lock, writing as it ends
        with fly_provider._provider()._user_lock(member["id"]):
            held.set()
            time.sleep(0.3)
            with store.connect() as db:
                put(db, "ade_hook_tokens", user_id=member["id"])
            written.set()
    starting = threading.Thread(target=start)

    def cleanup(work):
        starting.start()
        held.wait(5)
        work()
    monkeypatch.setattr(account_delete, "_spawn", cleanup)
    holds.settle(now=d["takes_effect_at"])
    starting.join(5)
    assert written.is_set()
    with store.connect() as db:                         # written under the lock before the cleanup took it: gone
        assert naming(db, member["id"]) == set()


def test_the_apps_lifespan_starts_the_deletions_own_round(monkeypatch):
    import asyncio
    from proofpane_open import main
    started = []
    for module, name in ((main.ade_push, "start"), (main.ade_moves, "start"), (main.ade_link_usage, "start"),
                         (main.ade_e2e_keys, "start"), (main.oauth, "start"), (main.fido_mds, "start"),
                         (main.ade_workspaces, "start"), (main.ade_workspaces, "shutdown")):
        monkeypatch.setattr(module, name, lambda *a, **k: None)
    monkeypatch.setattr(main.ade_workspaces, "provider", lambda: None)
    monkeypatch.setattr(account_delete, "start", lambda *a, **k: started.append("round"))

    async def serve_once():
        async with main._lifespan(main.app):
            pass
    asyncio.run(serve_once())
    assert started == ["round"]


def test_a_starts_save_after_the_deletion_keeps_the_fly_ids_it_carries(fly, client, quiet, monkeypatch):  # noqa: F811
    h, member = hosted_member(client, fly)
    d = place(client, h, word(client, h, "account-delete")).json()["deletion"]
    monkeypatch.setattr(account_delete, "_spawn", lambda work: None)    # the cleanup waits on the start's lock
    holds.settle(now=d["takes_effect_at"])
    # the start, under way as the deletion committed, writes what it made at Fly as _ensure writes it, through _save
    with pytest.raises(fly_provider.WorkspaceAccess) as refused:
        fly_provider._save(member["id"], volume_id="vol_aftermath1")
    assert refused.value.status == 410
    with pytest.raises(fly_provider.WorkspaceAccess):
        fly_provider._save(member["id"], machine_id="m4ftermath2", cert="c")
    with store.connect() as db:
        kept = {tuple(r) for r in db.execute("SELECT app, kind, fly_id FROM account_deleted_fly")}
    assert {(APP, "volumes", "vol_aftermath1"), (APP, "machines", "m4ftermath2")} <= kept


def test_the_sweep_keeps_the_fly_ids_of_a_workspace_row_written_since(client, quiet):
    h = signed_up(client)
    user = me(client, h)
    d = place(client, h, word(client, h, "account-delete")).json()["deletion"]
    holds.settle(now=d["takes_effect_at"])
    with store.connect() as db:                         # a start's reserved row and its saves, after the deletion
        put(db, "ade_workspaces", user_id=user, app="pp-ws", machine_id="m5traggler", volume_id="vol_straggler")
    account_delete.round_once(now=d["takes_effect_at"] + 60)
    with store.connect() as db:
        assert naming(db, user) == set()
        kept = {tuple(r) for r in db.execute("SELECT app, kind, fly_id FROM account_deleted_fly")}
    assert {("pp-ws", "machines", "m5traggler"), ("pp-ws", "volumes", "vol_straggler")} <= kept
