"""A hosted workspace's share of the cap for each account, and what leaving the hosted choice keeps and for how long,
under the fly provider, against the stand-in for Fly's Machines API (fly_fixtures.py).

Each account that is not an admin has a share of the cap, OPEN_EDITION_WORKSPACE_SHARE_USD, by default the
cap divided by OPEN_EDITION_WORKSPACE_MAX and never more than the cap; an admin's is the whole cap. Its spending is its
own machine's hours and its own volume and machine disk, at the cap's prices. A start is refused, in words naming the
share, when its spending and an hour of its machine would pass it; the meter stops that account's machine alone when its
spending and a quarter of an hour would reach it, and the cap's stop of every started machine stays. The page shows
the account its month's spending against its share.

Leaving the hosted choice stops the machine at once, after a confirmation that names what is running; the
volume and the app's records of the workspace are kept OPEN_EDITION_WORKSPACE_KEEP_DAYS days (30), the page giving the
date, and choosing hosted again within them starts the same machine and volume. After them the meter's reading
destroys the machine and its volume at Fly and deletes the account's ade_workspaces row, so that neither counts toward
the cap or the number of workspaces, and records that it did; the account's agents, tasks, controller and finishes
stay. The person may delete the workspace at once, with a confirmation made for that act as an approval's is.

Also tested: a workspace whose record names another Fly app is
neither kept nor deleted from here; one removal that keeps failing holds up no other; a volume Fly lists as being
destroyed is not destroyed again; a workspace left before its time was written is kept from the first reading; a stop
that fails keeps the choice; the days are the deployment's setting; the meter's quarter of an hour; a leave and a start
under way take turns; the delete at once spends no confirmation where it would delete nothing; every run under way is
named; the day kept until comes with its time, and a removal says how long it was kept; the local provider's delete says
its folder stays; and the page's state is read from Fly at most once a few seconds.

Nothing here imports what these tests check: each test reads it through the routes, the provider's own calls and the
meter, so where it is not built a test fails on an assertion.
"""

import base64
import re
import threading
import time

import pytest

from conftest import ADMIN as ADMIN_EMAIL
from conftest import signed_up
from fly_fixtures import FakeFly
from proofpane_open import auth, store
from proofpane_open.ade import fly_provider, storage, workspaces

APP, TOKEN = "pp-workspaces-test", "fo1_not_a_real_token_" + "z" * 20
NEW_APP = "pp-workspaces-next"
KEY = base64.b64encode(bytes(range(32))).decode()
ST = "/api/ade/storage"
WS = "/api/ade/workspace"
DELETE_WS = "/api/ade/storage/workspace"
ADMIN = {"id": "u-admin", "admin": True}
MEMBER = {"id": "u-member", "admin": False}
OTHER = {"id": "u-other", "admin": False}
MEMBER_EMAIL = "member@example.com"
PASSWORD = "correct horse battery"
OCT = 1790812800.0              # 2026-10-01T00:00:00Z
DAY = 86400


@pytest.fixture
def fly(monkeypatch):
    """The fly provider against the stand-in, for everyone, the provider tests' three accounts keeping their work in
    their workspaces."""
    fake = FakeFly(APP, TOKEN)
    for name, value in {"OPEN_EDITION_ADE": "fly", "OPEN_EDITION_WORKSPACE_APP": APP,
                        "OPEN_EDITION_WORKSPACE_IMAGE": f"registry.fly.io/{APP}:v1",
                        "OPEN_EDITION_WORKSPACE_FLY_TOKEN": TOKEN, "OPEN_EDITION_WORKSPACE_OWN_NETWORK": "yes",
                        "OPEN_EDITION_KEYSTORE_KEY": KEY, "OPEN_EDITION_FLY_API": fake.url,
                        "OPEN_EDITION_WORKSPACE_REGION": "syd", "OPEN_EDITION_WORKSPACE_FOR": "everyone"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    monkeypatch.setattr(fly_provider, "wait_healthy", lambda endpoint, wait=None: None)
    with store.connect() as db:
        db.executemany("INSERT INTO ade_storage_choice (user_id, choice, chosen_at) VALUES (?, 'hosted', 0)",
                       [(ADMIN["id"],), (MEMBER["id"],), (OTHER["id"],)])
        # the meter reads an account as a start does (auth.user): ADMIN's is the address conftest makes an admin's
        db.execute("INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, '', 0)",
                   (ADMIN["id"], ADMIN_EMAIL))
    yield fake
    workspaces.shutdown()
    fake.close()


def priced(monkeypatch, cap="10", most="2", per_hour="1", share=None):
    """A cap, a number of workspaces and a price that make the share's sums easy to read."""
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_CAP_USD", cap)
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_MAX", most)
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_USD_PER_HOUR", per_hour)
    if share is not None:
        monkeypatch.setenv("OPEN_EDITION_WORKSPACE_SHARE_USD", share)


def row(user_id):
    with store.connect() as db:
        r = db.execute("SELECT * FROM ade_workspaces WHERE user_id = ?", (user_id,)).fetchone()
    return dict(r) if r else None


def machine_of(fake, user_id):
    r = row(user_id)
    return fake.machines.get(r["machine_id"]) if r and r.get("machine_id") else None


def volume_of(fake, user_id):
    r = row(user_id)
    return fake.volumes.get(r["volume_id"]) if r and r.get("volume_id") else None


def ran(user_id, hours, month="2026-10"):
    """`hours` more of the account's machine this month, as the meter would have counted them."""
    with store.connect() as db:
        db.execute("INSERT INTO ade_workspace_spend (month, machine_id, seconds) VALUES (?, ?, ?) ON CONFLICT (month, "
                   "machine_id) DO UPDATE SET seconds = seconds + excluded.seconds",
                   (month, row(user_id)["machine_id"], int(hours * 3600)))


def refused_start(user):
    """The WorkspaceAccess a start for `user` raised, or None where it started."""
    try:
        workspaces.for_user(user).ensure(user)
    except workspaces.WorkspaceAccess as e:
        return e
    return None


def share_of(user):
    return workspaces.for_user(user).status(user).get("share") or {}


# ---- each account's share of the cap -------------------------------------------------------------------------------

def test_the_share_is_the_cap_over_the_number_of_workspaces_unless_set_and_never_more_than_the_cap(fly, monkeypatch):
    assert getattr(fly_provider.settings(), "share_usd", None) == 5.0              # 50 over 10
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_SHARE_USD", "7")
    assert getattr(fly_provider.settings(), "share_usd", None) == 7.0
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_SHARE_USD", "60")
    try:
        fly_provider.settings()
        said = None
    except RuntimeError as e:
        said = str(e)
    assert said is not None and "OPEN_EDITION_WORKSPACE_SHARE_USD" in said


def test_a_share_is_never_more_than_the_cap_the_deployment_set(fly, monkeypatch):
    priced(monkeypatch, cap="10", share="20")                 # a cap lowered below a share set before it
    try:
        share = getattr(fly_provider.settings(), "share_usd", None)
    except RuntimeError as e:
        share = str(e)                                        # a deployment that would not start
    assert share == 10.0
    assert share_of(MEMBER).get("share_usd") == 10.0


def test_an_account_s_spending_counts_its_own_volume_and_machine_disk(fly, monkeypatch):
    priced(monkeypatch)
    now = [OCT + 15 * DAY]
    monkeypatch.setattr(fly_provider, "clock", lambda: now[0])
    for user in (MEMBER, OTHER):
        workspaces.for_user(user).ensure(user)
    fly_provider.meter_tick(fly_provider.settings(), now=now[0])      # the first reading: storage, no hours yet
    # half a month of its 5 GB volume and its machine's root file system, and not the other account's as well
    month = (5 + fly_provider.ROOTFS_GB) * fly_provider.STORAGE_USD_PER_GB_MONTH
    assert abs(share_of(MEMBER).get("spent_usd", -1.0) - month * 15 / 31) < 0.001


def test_a_member_s_start_is_refused_at_its_share_while_another_account_starts(fly, monkeypatch):
    priced(monkeypatch)                                       # cap 10, two workspaces: a share of 5
    now = [OCT + 3600]
    monkeypatch.setattr(fly_provider, "clock", lambda: now[0])
    workspaces.for_user(MEMBER).ensure(MEMBER)
    workspaces.for_user(MEMBER).stop(MEMBER)
    ran(MEMBER["id"], 4.5)                                    # 4.5 and an hour's 1 pass the 5
    refused = refused_start(MEMBER)
    assert refused is not None and refused.status == 403, refused
    assert "$5.00" in str(refused) and "share" in str(refused)
    assert machine_of(fly, MEMBER["id"])["state"] == "stopped"
    assert refused_start(OTHER) is None                       # the month's 4.5 and an hour leave the cap room
    assert machine_of(fly, OTHER["id"])["state"] == "started"


def test_the_meter_stops_one_account_s_machine_at_its_share_and_every_machine_at_the_cap(fly, monkeypatch):
    priced(monkeypatch, most="3", share="6")
    for user in (ADMIN, MEMBER, OTHER):
        workspaces.for_user(user).ensure(user)
    s = fly_provider.settings()
    fly_provider.meter_tick(s, now=OCT + 3600)               # the first reading
    ran(MEMBER["id"], 5.9)                                    # 5.9 and a quarter hour reach its 6
    spend = fly_provider.meter_tick(s, now=OCT + 3660)
    assert machine_of(fly, MEMBER["id"])["state"] == "stopped"
    assert machine_of(fly, OTHER["id"])["state"] == "started" and machine_of(fly, ADMIN["id"])["state"] == "started"
    assert spend["stopped"] == [row(MEMBER["id"])["machine_id"]]
    ran(OTHER["id"], 3.9)                                     # the month's 9.8 and two quarter hours pass the cap
    spend = fly_provider.meter_tick(s, now=OCT + 3720)
    assert machine_of(fly, OTHER["id"])["state"] == "stopped" and machine_of(fly, ADMIN["id"])["state"] == "stopped"
    assert sorted(spend["stopped"]) == sorted([row(OTHER["id"])["machine_id"], row(ADMIN["id"])["machine_id"]])


def test_the_meter_stops_a_member_s_machine_a_quarter_of_an_hour_before_its_share(fly, monkeypatch):
    priced(monkeypatch, cap="20", share="6")
    for user in (MEMBER, OTHER):
        workspaces.for_user(user).ensure(user)
    s = fly_provider.settings()
    fly_provider.meter_tick(s, now=OCT + 3600)               # the first reading
    ran(MEMBER["id"], 5.9)                                    # 5.9 and a quarter hour reach its 6
    ran(OTHER["id"], 5.5)                                     # 5.5 and a quarter hour do not; and an hour would
    spend = fly_provider.meter_tick(s, now=OCT + 3660)
    assert machine_of(fly, MEMBER["id"])["state"] == "stopped"
    assert machine_of(fly, OTHER["id"])["state"] == "started"
    assert spend["stopped"] == [row(MEMBER["id"])["machine_id"]]


def test_an_admin_is_bounded_by_the_cap_alone(fly, monkeypatch):
    priced(monkeypatch, cap="20", most="4")                   # a member's share 5, an admin's the cap's 20
    now = [OCT + 3600]
    monkeypatch.setattr(fly_provider, "clock", lambda: now[0])
    for user in (ADMIN, MEMBER):
        workspaces.for_user(user).ensure(user)
        workspaces.for_user(user).stop(user)
    ran(ADMIN["id"], 5.5)                                     # past a member's share
    ran(MEMBER["id"], 4.2)                                    # 4.2 and an hour pass its 5; the month's 9.7 and an
    assert share_of(ADMIN).get("share_usd") == 20.0 and share_of(MEMBER).get("share_usd") == 5.0
    refused = refused_start(MEMBER)                           # hour leave the cap room
    assert refused is not None and refused.status == 403 and "$5.00" in str(refused)
    assert refused_start(ADMIN) is None                       # 5.5 and an hour are under its share, the cap
    spend = fly_provider.meter_tick(fly_provider.settings(), now=now[0])
    assert spend["stopped"] == [] and machine_of(fly, ADMIN["id"])["state"] == "started"


def test_the_status_shows_the_account_its_month_spending_against_its_share(fly, monkeypatch, client):
    priced(monkeypatch)
    h = signed_up(client, MEMBER_EMAIL)
    assert client.put(ST, headers=h, json={"choice": "hosted"}).status_code == 200
    me = auth.user(client.get("/api/auth/me", headers=h).json()["id"])
    workspaces.for_user(me).ensure(me)
    now = time.time()
    fly_provider.meter_tick(fly_provider.settings(), now=now)
    ran(me["id"], 2, month=time.strftime("%Y-%m", time.gmtime(now)))
    share = client.get(WS, headers=h).json().get("share") or {}
    assert share.get("share_usd") == 5.0 and share.get("month") == time.strftime("%Y-%m", time.gmtime(now))
    # two hours, and the volume's and the machine's disk for the part of the month gone: at most a month of them
    most = 2.0 + (5 + fly_provider.ROOTFS_GB) * fly_provider.STORAGE_USD_PER_GB_MONTH
    assert 2.0 <= share.get("spent_usd", 0) <= most
    shown = client.get(ST, headers=h).json().get("workspace") or {}
    assert (shown.get("share") or {}).get("share_usd") == 5.0


# ---- leaving the hosted choice -------------------------------------------------------------------------------------

def hosted_member(client, fake, email=MEMBER_EMAIL):
    """A member signed up, keeping its work in a workspace that is running; returns (headers, user)."""
    h = signed_up(client, email)
    assert client.put(ST, headers=h, json={"choice": "hosted"}).status_code == 200
    me = auth.user(client.get("/api/auth/me", headers=h).json()["id"])
    workspaces.for_user(me).ensure(me)
    assert machine_of(fake, me["id"])["state"] == "started"
    return h, me


def test_leaving_names_what_is_running_before_it_is_confirmed(fly, client):
    h, me = hosted_member(client, fly)
    left = client.get(ST, headers=h).json()["left_behind"]["machine"]
    assert any("running now" in line and "stops it at once" in line for line in left), left
    assert any("30 days" in line for line in left), left
    assert left == client.get(ST, headers=h).json()["left_behind"]["repository"]


def test_leaving_names_every_run_under_way_in_the_workspace(fly, client):
    h, me = hosted_member(client, fly)
    with store.connect() as db:          # an improve loop's run, as much under way in the workspace as any other
        db.execute("INSERT INTO ade_agent_runs (run_id, agent_id, user_id, state, loop, updated_at) VALUES ('r1', "
                   "'a1', ?, 'working', 1, ?)", (me["id"], int(time.time())))
    left = client.get(ST, headers=h).json()["left_behind"]["machine"]
    assert any("1 of your agents' runs is under way" in line for line in left), left


def test_the_day_a_workspace_would_be_kept_until_is_given_with_its_time(fly, client):
    h, me = hosted_member(client, fly)
    left = client.get(ST, headers=h).json()["left_behind"]["machine"]
    assert any(re.search(r"until \d{1,2} [A-Z][a-z]+ \d{4}, \d{2}:\d{2} \(UTC\)", line) for line in left), left


def test_the_storage_page_reads_the_workspace_s_state_from_fly_at_most_once_a_few_seconds(fly, client):
    h, me = hosted_member(client, fly)
    asked = f"/machines/{row(me['id'])['machine_id']}"
    before = len(fly.calls("GET", asked))
    left = client.get(ST, headers=h).json()["left_behind"]
    client.get(ST, headers=h)
    assert any("running now" in line and "stops it at once" in line for line in left["machine"]), left
    assert len(fly.calls("GET", asked)) - before <= 1


def test_leaving_the_hosted_choice_stops_the_machine_at_once_and_keeps_its_volume_and_row(fly, client):
    h, me = hosted_member(client, fly)
    before = time.time()
    r = client.put(ST, headers=h, json={"choice": "machine"})
    assert r.status_code == 200, r.text
    assert machine_of(fly, me["id"])["state"] == "stopped"
    assert volume_of(fly, me["id"])["state"] != "destroyed" and row(me["id"]) is not None
    assert [c for c in fly.log if c[0] == "DELETE"] == []
    until = (r.json().get("workspace") or {}).get("kept_until")
    assert until is not None and before + 30 * DAY - 5 <= until <= time.time() + 30 * DAY + 5


def test_a_stop_that_fails_keeps_the_hosted_choice(fly, client):
    h, me = hosted_member(client, fly)
    fly.answers = [(500, {"error": "Fly could not stop it"})]     # the stop's own call
    r = client.put(ST, headers=h, json={"choice": "machine"})
    assert r.status_code == 502, r.text
    assert client.get(ST, headers=h).json()["choice"] == "hosted"
    assert machine_of(fly, me["id"])["state"] == "started" and row(me["id"]).get("left_at") is None


def in_thread(fn):
    """Run `fn` in a thread of its own; returns (the thread, {"out": its answer} once it has one)."""
    out = {}
    t = threading.Thread(target=lambda: out.update(out=fn()), daemon=True)
    t.start()
    return t, out


def test_leaving_while_a_start_is_under_way_stops_what_it_started(fly, client, monkeypatch):
    h, me = hosted_member(client, fly)
    workspaces.for_user(me).stop(me)
    under_way, go = threading.Event(), threading.Event()
    room = fly_provider._room_to_start

    def slow_room(*args, **kwargs):          # a start past its own checks, about to start the machine
        under_way.set()
        go.wait(5)
        return room(*args, **kwargs)

    monkeypatch.setattr(fly_provider, "_room_to_start", slow_room)
    start, _ = in_thread(lambda: workspaces.for_user(me).ensure(me))
    assert under_way.wait(5)
    leave, left = in_thread(lambda: client.put(ST, headers=h, json={"choice": "machine"}))
    time.sleep(0.5)                          # the leave asked while the start is under way
    go.set()
    start.join(10)
    leave.join(10)
    assert left["out"].status_code == 200
    assert machine_of(fly, me["id"])["state"] == "stopped"


def test_a_start_that_waited_on_a_leave_is_refused_after_it(fly, client, monkeypatch):
    h, me = hosted_member(client, fly)
    workspaces.for_user(me).stop(me)
    stopped, go = threading.Event(), threading.Event()
    stop = fly_provider.FlyProvider.stop

    def slow_stop(self, user):               # a leave that has stopped the machine and not yet kept its choice
        out = stop(self, user)
        stopped.set()
        go.wait(5)
        return out

    monkeypatch.setattr(fly_provider.FlyProvider, "stop", slow_stop)
    leave, left = in_thread(lambda: client.put(ST, headers=h, json={"choice": "machine"}))
    assert stopped.wait(5)
    start, started = in_thread(lambda: refused_start(me))
    time.sleep(0.5)                          # the start asked while the leave is under way
    go.set()
    leave.join(10)
    start.join(10)
    assert left["out"].status_code == 200
    assert started.get("out") is not None and started["out"].status == 403
    assert machine_of(fly, me["id"])["state"] == "stopped"


def test_moving_between_the_other_choices_does_not_restart_the_days(fly, client, monkeypatch):
    t = [time.time()]
    monkeypatch.setattr(storage, "_now", lambda now=None: int(t[0]))
    h, me = hosted_member(client, fly)
    until = (client.put(ST, headers=h, json={"choice": "machine"}).json().get("workspace") or {}).get("kept_until")
    t[0] += 10 * DAY
    later = client.put(ST, headers=h, json={"choice": "repository"}).json()
    assert until is not None and (later.get("workspace") or {}).get("kept_until") == until


def test_leaving_again_keeps_the_workspace_from_the_second_leaving(fly, client, monkeypatch):
    t = [time.time()]
    t0 = int(t[0])
    monkeypatch.setattr(storage, "_now", lambda now=None: int(t[0]))
    h, me = hosted_member(client, fly)
    machine = row(me["id"])["machine_id"]
    client.put(ST, headers=h, json={"choice": "machine"})
    t[0] += 20 * DAY
    client.put(ST, headers=h, json={"choice": "hosted"})
    t[0] += 5 * DAY
    view = client.put(ST, headers=h, json={"choice": "machine"}).json()
    assert (view.get("workspace") or {}).get("kept_until") == t0 + 25 * DAY + 30 * DAY
    fly_provider.meter_tick(fly_provider.settings(), now=t0 + 31 * DAY)     # 31 days after the first leaving
    assert fly.machines[machine]["state"] != "destroyed" and row(me["id"]) is not None


def test_choosing_hosted_again_within_the_days_starts_the_same_machine_and_volume(fly, client):
    h, me = hosted_member(client, fly)
    machine, volume = row(me["id"])["machine_id"], row(me["id"])["volume_id"]
    view = client.put(ST, headers=h, json={"choice": "repository"}).json()
    assert (view.get("workspace") or {}).get("kept_until") is not None
    fly_provider.meter_tick(fly_provider.settings(), now=time.time() + 29 * DAY)     # within the days
    assert machine_of(fly, me["id"]) is not None and machine_of(fly, me["id"])["state"] == "stopped"
    view = client.put(ST, headers=h, json={"choice": "hosted"}).json()
    assert (view.get("workspace") or {}).get("kept_until") is None
    workspaces.for_user(me).ensure(me)
    assert (row(me["id"])["machine_id"], row(me["id"])["volume_id"]) == (machine, volume)
    assert fly.machines[machine]["state"] == "started"


def test_after_the_days_the_meter_destroys_the_machine_and_volume_and_deletes_the_row(fly, client, monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_MAX", "1")
    h, me = hosted_member(client, fly)
    machine, volume = row(me["id"])["machine_id"], row(me["id"])["volume_id"]
    now = int(time.time())
    with store.connect() as db:
        db.execute("INSERT INTO ade_agents (id, user_id, name, brief, created_at, updated_at) VALUES "
                   "('a1', ?, 'n', 'b', ?, ?)", (me["id"], now, now))
        db.execute("INSERT INTO ade_agent_tasks (id, user_id, agent_id, title, goal, goal_digest, state, backend, "
                   "home_grp, cross_groups, created_at, updated_at) VALUES ('t1', ?, 'a1', 't', 'g', 'd', 'open', "
                   "'claude', 'g', 0, ?, ?)", (me["id"], now, now))
        db.execute("INSERT INTO ade_controllers (user_id, models, efforts, chat_model, chat_effort, created_at, "
                   "updated_at) VALUES (?, '[]', '[]', 'm', 'e', ?, ?)", (me["id"], now, now))
        db.execute("INSERT INTO ade_finishes (id, user_id, session, repo, branch, base, head, task, status, "
                   "created_at) VALUES ('f1', ?, 's', 'r', 'b', 'x', 'y', 't', 'ok', ?)", (me["id"], now))
    client.put(ST, headers=h, json={"choice": "machine"})
    assert refused_start(ADMIN) is not None                   # the one workspace allowed is still the member's
    fly_provider.meter_tick(fly_provider.settings(), now=time.time() + 30 * DAY + 60)
    assert fly.machines[machine]["state"] == "destroyed" and fly.volumes[volume]["state"] == "destroyed"
    assert row(me["id"]) is None
    removed = (client.get(ST, headers=h).json().get("workspace") or {}).get("removed") or {}
    assert removed.get("removed_at") and "30 days" in removed.get("why", "")
    with store.connect() as db:
        for table in ("ade_agents", "ade_agent_tasks", "ade_controllers", "ade_finishes"):
            assert db.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (me["id"],)).fetchone()[0] == 1, table
    assert refused_start(ADMIN) is None                       # it no longer counts toward the number of workspaces


def test_the_days_kept_are_the_deployment_s_setting(fly, client, monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_KEEP_DAYS", "7")
    h, me = hosted_member(client, fly)
    left = client.get(ST, headers=h).json()["left_behind"]["machine"]
    assert any("7 days" in line for line in left) and not any("30 days" in line for line in left), left
    before = int(time.time())
    until = (client.put(ST, headers=h, json={"choice": "machine"}).json().get("workspace") or {}).get("kept_until")
    assert until is not None and before + 7 * DAY <= until <= time.time() + 7 * DAY
    fly_provider.meter_tick(fly_provider.settings(), now=time.time() + 7 * DAY + 60)
    assert row(me["id"]) is None


def test_a_removal_says_how_long_the_workspace_was_kept(fly, client):
    h, me = hosted_member(client, fly)
    client.put(ST, headers=h, json={"choice": "machine"})
    fly_provider.meter_tick(fly_provider.settings(), now=time.time() + 32 * DAY + 60)    # no reading for 2 days more
    why = ((client.get(ST, headers=h).json().get("workspace") or {}).get("removed") or {}).get("why", "")
    assert "32 days" in why, why


def test_a_workspace_left_before_its_time_was_written_is_kept_from_the_first_reading(fly, client):
    h, me = hosted_member(client, fly)
    workspaces.for_user(me).stop(me)
    with store.connect() as db:          # left as an older store kept it: no time written
        db.execute("UPDATE ade_storage_choice SET choice = 'machine' WHERE user_id = ?", (me["id"],))
    first = int(time.time()) + 3600
    fly_provider.meter_tick(fly_provider.settings(), now=first)
    assert row(me["id"]) is not None and row(me["id"]).get("left_at") == first
    kept = (client.get(ST, headers=h).json().get("workspace") or {}).get("kept_until")
    assert kept == first + 30 * DAY
    fly_provider.meter_tick(fly_provider.settings(), now=first + 30 * DAY - 60)
    assert row(me["id"]) is not None
    fly_provider.meter_tick(fly_provider.settings(), now=first + 30 * DAY + 60)
    assert row(me["id"]) is None


def test_a_removal_that_keeps_failing_holds_up_no_other(fly, client, monkeypatch):
    t = [time.time()]
    t0 = int(t[0])
    monkeypatch.setattr(storage, "_now", lambda now=None: int(t[0]))
    h1, first = hosted_member(client, fly, "first@example.com")
    h2, second = hosted_member(client, fly, "second@example.com")
    stuck, gone = row(first["id"])["volume_id"], row(second["id"])["volume_id"]
    client.put(ST, headers=h1, json={"choice": "machine"})       # the first to leave
    t[0] += 60
    client.put(ST, headers=h2, json={"choice": "machine"})       # and a minute later the second
    fly.refuse_deletes.add(stuck)                                 # Fly will not destroy the first's volume
    for minutes in (2, 3, 4):
        fly_provider.meter_tick(fly_provider.settings(), now=t0 + 30 * DAY + minutes * 60)
    assert row(second["id"]) is None and fly.volumes[gone]["state"] == "destroyed"
    assert row(first["id"]) is not None and fly.volumes[stuck]["state"] != "destroyed"


def test_a_volume_fly_lists_as_being_destroyed_is_not_destroyed_again(fly, client):
    h, me = hosted_member(client, fly)
    volume = row(me["id"])["volume_id"]
    client.put(ST, headers=h, json={"choice": "machine"})
    fly.volumes[volume]["state"] = "pending_destroy"              # Fly is destroying it already
    fly_provider.meter_tick(fly_provider.settings(), now=time.time() + 30 * DAY + 60)
    assert row(me["id"]) is None
    assert fly.calls("DELETE", f"/volumes/{volume}") == []


def confirmation(client, h, change):
    r = client.post("/api/account/confirm", headers=h, json={"password": PASSWORD, "change": change})
    return r.json().get("confirmation") if r.status_code == 200 else None


def test_the_delete_at_once_needs_its_own_confirmation(fly, client):
    h, me = hosted_member(client, fly)
    machine, volume = row(me["id"])["machine_id"], row(me["id"])["volume_id"]
    client.put(ST, headers=h, json={"choice": "machine"})
    r = client.request("DELETE", DELETE_WS, headers=h, json={})
    assert r.status_code == 403, r.text
    other = confirmation(client, h, "sign-out-everywhere")
    r = client.request("DELETE", DELETE_WS, headers=h, json={"confirmation": other})
    assert r.status_code == 403, r.text
    assert fly.machines[machine]["state"] != "destroyed" and row(me["id"]) is not None
    made_for_it = confirmation(client, h, "workspace-delete")
    assert made_for_it is not None
    r = client.request("DELETE", DELETE_WS, headers=h, json={"confirmation": made_for_it})
    assert r.status_code == 200, r.text
    assert fly.machines[machine]["state"] == "destroyed" and fly.volumes[volume]["state"] == "destroyed"
    assert row(me["id"]) is None and ((r.json().get("workspace") or {}).get("removed") or {}).get("removed_at")


def test_the_delete_at_once_is_refused_while_the_work_lives_in_the_workspace(fly, client):
    h, me = hosted_member(client, fly)
    made_for_it = confirmation(client, h, "workspace-delete")
    r = client.request("DELETE", DELETE_WS, headers=h, json={"confirmation": made_for_it})
    assert r.status_code == 409, r.text
    assert machine_of(fly, me["id"])["state"] == "started" and row(me["id"]) is not None


def test_the_delete_at_once_spends_no_confirmation_where_no_choice_was_kept(fly, client, monkeypatch):
    h, me = hosted_member(client, fly)
    machine = row(me["id"])["machine_id"]
    workspaces.for_user(me).stop(me)
    with store.connect() as db:          # no choice kept, and no workspace offered now: its work reads as on a machine
        db.execute("DELETE FROM ade_storage_choice WHERE user_id = ?", (me["id"],))
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_FOR", "admins")
    made_for_it = confirmation(client, h, "workspace-delete")
    r = client.request("DELETE", DELETE_WS, headers=h, json={"confirmation": made_for_it})
    assert r.status_code == 409, r.text
    assert row(me["id"]) is not None and fly.machines[machine]["state"] != "destroyed"
    assert client.put(ST, headers=h, json={"choice": "machine"}).status_code == 200
    r = client.request("DELETE", DELETE_WS, headers=h, json={"confirmation": made_for_it})
    assert r.status_code == 200, r.text
    assert row(me["id"]) is None and fly.machines[machine]["state"] == "destroyed"


def test_a_workspace_in_another_fly_app_is_neither_kept_nor_deleted_from_here(fly, client, monkeypatch):
    h, me = hosted_member(client, fly)
    machine, volume = row(me["id"])["machine_id"], row(me["id"])["volume_id"]
    fly.other_apps.add(NEW_APP)
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_APP", NEW_APP)     # the operator makes workspaces in another app now
    left = client.get(ST, headers=h).json()["left_behind"]["machine"]
    assert any(APP in line for line in left) and not any("30 days" in line for line in left), left
    assert client.put(ST, headers=h, json={"choice": "machine"}).status_code == 200
    assert (client.get(ST, headers=h).json().get("workspace") or {}).get("kept_until") is None
    made_for_it = confirmation(client, h, "workspace-delete")
    r = client.request("DELETE", DELETE_WS, headers=h, json={"confirmation": made_for_it})
    assert r.status_code == 409 and APP in r.json().get("detail", ""), r.text
    fly_provider.meter_tick(fly_provider.settings(), now=time.time() + 31 * DAY)
    assert row(me["id"]) is not None and fly.calls("DELETE") == []
    assert fly.machines[machine]["state"] != "destroyed" and fly.volumes[volume]["state"] != "destroyed"


def test_under_the_local_provider_the_delete_at_once_says_its_folder_stays(client, monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_ADE", "local")
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    h = signed_up(client)                # the admin, whose workspace would be a folder on this machine
    r = client.request("DELETE", DELETE_WS, headers=h, json={})
    assert r.status_code == 404 and "folder" in r.json().get("detail", ""), r.text
