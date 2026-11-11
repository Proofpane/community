"""The fly provider, against a stand-in for Fly's Machines API (fly_fixtures.py).

Its settings are checked when the app starts; a user's first start makes a volume and a machine that has no public
service; what reaches the machine is its certificate, key, secret and this app's client certificate, and of those only
the certificate and the sealed secret are kept here, with this app's address and a hook token for its sessions'
approval hook beside them (test_workspace_hosted_gate.py); later starts start the same machine, taking a
new image only
while it is stopped; each account reaches only its own machine. The last tests boot the image's Caddy and the agent on
this computer and go through them for real: TLS 1.3 with X25519MLKEM768 only, the app's client certificate required,
the machine's own certificate pinned, a terminal through it all.
"""

import asyncio
import base64
import datetime
import json
import os
import re
import ssl
import stat
import threading

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import ExtendedKeyUsageOID

from conftest import ADMIN as ADMIN_EMAIL
from fly_fixtures import FakeFly, LocalMachines, caddy_binary
from proofpane_open import config, store
from proofpane_open.ade import fly_provider as fly
from proofpane_open.ade import workspace_client as client
from proofpane_open.ade import workspaces
from proofpane_open.ade.workspaces import WorkspaceAccess

APP, TOKEN, IMAGE = "pp-workspaces-test", "fo1_not_a_real_token_" + "z" * 20, "registry.fly.io/pp-workspaces-test:v1"
KEY = base64.b64encode(b"k" * 32).decode()
ADMIN = {"id": "u-admin", "admin": True}
MEMBER = {"id": "u-member", "admin": False}


@pytest.fixture
def fake(monkeypatch):
    f = FakeFly(APP, TOKEN)
    for name, value in {"OPEN_EDITION_ADE": "fly", "OPEN_EDITION_WORKSPACE_APP": APP,
                        "OPEN_EDITION_WORKSPACE_IMAGE": IMAGE, "OPEN_EDITION_WORKSPACE_FLY_TOKEN": TOKEN,
                        "OPEN_EDITION_WORKSPACE_OWN_NETWORK": "yes", "OPEN_EDITION_KEYSTORE_KEY": KEY,
                        "OPEN_EDITION_FLY_API": f.url, "OPEN_EDITION_WORKSPACE_REGION": "syd"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    monkeypatch.setattr(fly, "wait_healthy", lambda endpoint, wait=None: None)
    # the two accounts these tests start workspaces for keep their work in one (ade/storage.py): one that has not
    # chosen and has no workspace yet keeps it on its own machine, and the provider starts nothing for it
    # (test_storage_choice.py)
    with store.connect() as db:
        db.executemany("INSERT INTO ade_storage_choice (user_id, choice, chosen_at) VALUES (?, 'hosted', 0)",
                       [(ADMIN["id"],), (MEMBER["id"],)])
        # the spending meter reads an account as a start does (auth.user, for its share of the cap): ADMIN's is
        # the address conftest makes an admin's
        db.execute("INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, '', 0)",
                   (ADMIN["id"], ADMIN_EMAIL))
    yield f
    f.close()


def _machine(fake):
    (m,) = [m for m in fake.machines.values() if m["state"] != "destroyed"]
    return m


def _files(machine):
    return {os.path.basename(f["guest_path"]): base64.b64decode(f["raw_value"]).decode()
            for f in machine["config"]["files"]}


# ---- settings -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["OPEN_EDITION_WORKSPACE_APP", "OPEN_EDITION_WORKSPACE_IMAGE",
                                  "OPEN_EDITION_WORKSPACE_FLY_TOKEN", "OPEN_EDITION_WORKSPACE_OWN_NETWORK"])
def test_each_setting_is_needed_before_the_app_starts(fake, monkeypatch, name):
    monkeypatch.delenv(name)
    with pytest.raises(RuntimeError, match=name):
        workspaces.check_config()


@pytest.mark.parametrize("name,value,words", [
    ("OPEN_EDITION_WORKSPACE_OWN_NETWORK", "true", "OWN_NETWORK"),
    ("OPEN_EDITION_FLY_API", "http://api.example.com", "https"),
    ("OPEN_EDITION_WORKSPACE_MEMORY_MB", "1000", "multiple of 256"),
    ("OPEN_EDITION_WORKSPACE_IDLE_MINUTES", "1", "IDLE_MINUTES"),
    ("OPEN_EDITION_WORKSPACE_FOR", "members", "admins or everyone"),
    ("OPEN_EDITION_WORKSPACE_REGION", "Sydney", "region"),
    ("OPEN_EDITION_WORKSPACE_APP", "Has Spaces", "WORKSPACE_APP"),
])
def test_a_setting_that_is_wrong_is_named(fake, monkeypatch, name, value, words):
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=words):
        workspaces.check_config()


def test_the_workspace_app_is_never_this_app(fake, monkeypatch):
    monkeypatch.setenv("FLY_APP_NAME", APP)
    with pytest.raises(RuntimeError, match="never this app"):
        workspaces.check_config()


def test_without_a_key_store_key_the_provider_does_not_start(fake, monkeypatch):
    monkeypatch.delenv("OPEN_EDITION_KEYSTORE_KEY")
    with pytest.raises(RuntimeError, match="OPEN_EDITION_KEYSTORE_KEY"):
        workspaces.check_config()


def test_the_network_is_stated_either_way(fake, monkeypatch):
    for value, own in (("yes", True), ("no", False)):
        monkeypatch.setenv("OPEN_EDITION_WORKSPACE_OWN_NETWORK", value)
        workspaces.check_config()
        assert fly.settings().own_network is own


@pytest.mark.parametrize("name,value", [
    ("OPEN_EDITION_WORKSPACE_CAP_USD", "50.01"), ("OPEN_EDITION_WORKSPACE_CAP_USD", "0"),
    ("OPEN_EDITION_WORKSPACE_CAP_USD", "fifty"), ("OPEN_EDITION_WORKSPACE_USD_PER_HOUR", "0"),
    ("OPEN_EDITION_WORKSPACE_USD_PER_HOUR", "-1")])
def test_the_cap_can_be_lowered_not_raised_and_the_price_is_a_price(fake, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=name):
        workspaces.check_config()


def test_the_cap_is_the_owner_s_fifty_unless_lowered(fake, monkeypatch):
    assert fly.settings().cap_usd == 50.0 == fly.CAP_DECIDED_USD
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_CAP_USD", "12.5")
    assert fly.settings().cap_usd == 12.5


def test_fly_s_own_api_address_and_a_local_one_are_allowed(fake, monkeypatch):
    for api in ("http://_api.internal:4280", "https://api.machines.dev"):
        monkeypatch.setenv("OPEN_EDITION_FLY_API", api)
        workspaces.check_config()


# ---- a user's first start -------------------------------------------------------------------------------------------

def test_the_first_start_makes_a_volume_and_a_machine_closed_to_the_internet(fake):
    workspaces.for_user(ADMIN).ensure(ADMIN)
    label = fly.account_label(ADMIN["id"])
    (vol,) = fake.volumes.values()
    assert vol["name"] == "ws_" + label[:20] and vol["region"] == "syd" and vol["size_gb"] == 5 and vol["encrypted"]
    m = _machine(fake)
    cfg = m["config"]
    assert m["name"] == "ws-" + label and m["region"] == "syd" and m["state"] == "started"
    assert "services" not in cfg and cfg["image"] == IMAGE
    assert cfg["mounts"] == [{"volume": vol["id"], "path": "/home/ws"}]
    assert sorted(f["guest_path"] for f in cfg["files"]) == [
        "/etc/proofpane/boot/ade-hook-token", "/etc/proofpane/boot/ade-url", "/etc/proofpane/boot/app.crt",
        "/etc/proofpane/boot/secret", "/etc/proofpane/boot/tls.crt", "/etc/proofpane/boot/tls.key"]
    assert cfg["env"] == {"PP_WORKSPACE_IDLE_SECONDS": "1800"}
    assert cfg["guest"] == {"cpu_kind": "shared", "cpus": 1, "memory_mb": 2048}
    assert cfg["restart"] == {"policy": "on-failure", "max_retries": 3} and cfg["auto_destroy"] is False
    assert cfg["metadata"]["proofpane_workspace"] == label and ADMIN["id"] not in json.dumps(cfg)
    assert all(h.get("Authorization") == "Bearer " + TOKEN for *_, h in fake.log)


def test_only_the_certificate_and_the_sealed_secret_are_kept_here(fake, env):
    workspaces.for_user(ADMIN).ensure(ADMIN)
    files = _files(_machine(fake))
    with store.connect() as conn:
        row = dict(conn.execute("SELECT * FROM ade_workspaces WHERE user_id = ?", (ADMIN["id"],)).fetchone())
    assert row["cert"] == files["tls.crt"] and fly.unseal(row["secret_box"], ADMIN["id"]) == files["secret"]
    assert fly.unseal(row["secret_box"], MEMBER["id"]) is None            # sealed to its own account
    with open(config.db_path(), "rb") as fh:
        db = fh.read()
    assert files["secret"].encode() not in db and b"PRIVATE KEY" not in db
    assert len(files["secret"]) >= 40


def test_the_certificates_are_what_each_side_needs(fake):
    workspaces.for_user(ADMIN).ensure(ADMIN)
    files = _files(_machine(fake))
    cert = x509.load_pem_x509_certificate(files["tls.crt"].encode())
    assert isinstance(cert.public_key(), ed25519.Ed25519PublicKey)
    assert cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(
        x509.DNSName) == [f"*.vm.{APP}.internal"]
    assert list(cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value) == [
        ExtendedKeyUsageOID.SERVER_AUTH]
    assert cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False
    app_cert = x509.load_pem_x509_certificate(files["app.crt"].encode())
    assert list(app_cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value) == [
        ExtendedKeyUsageOID.CLIENT_AUTH]
    crt, key = fly.client_identity()
    assert open(crt).read() == files["app.crt"] and stat.S_IMODE(os.stat(key).st_mode) == 0o600
    left = cert.not_valid_after_utc - datetime.datetime.now(datetime.timezone.utc)
    assert datetime.timedelta(days=4 * 365) < left


def test_the_endpoint_is_the_machine_s_private_name_with_its_own_certificate_pinned(fake):
    endpoint = workspaces.for_user(ADMIN).ensure(ADMIN)
    m = _machine(fake)
    assert endpoint.http == f"https://{m['id']}.vm.{APP}.internal:8443"
    assert endpoint.ws == f"wss://{m['id']}.vm.{APP}.internal:8443"
    assert endpoint.secret == _files(m)["secret"]
    assert endpoint.ssl.minimum_version == ssl.TLSVersion.TLSv1_3 and endpoint.ssl.verify_mode == ssl.CERT_REQUIRED
    # the name is not checked, so the machine's own certificate must be the only thing trusted: no public CA
    assert endpoint.ssl.cert_store_stats() == {"x509": 1, "crl": 0, "x509_ca": 0}


# ---- later starts ---------------------------------------------------------------------------------------------------

def test_a_later_start_starts_the_same_machine(fake):
    p = workspaces.for_user(ADMIN)
    p.ensure(ADMIN)
    before = _files(_machine(fake))
    p.stop(ADMIN)
    assert _machine(fake)["state"] == "stopped" and p.endpoint(ADMIN) is None
    assert p.status(ADMIN)["running"] is False
    p.ensure(ADMIN)
    assert len(fake.machines) == 1 and _machine(fake)["state"] == "started"
    assert _files(_machine(fake)) == before
    assert len(fake.calls("POST", "/start")) == 1 and p.status(ADMIN)["running"] is True


def test_a_new_image_is_taken_only_while_the_machine_is_stopped(fake, monkeypatch):
    p = workspaces.for_user(ADMIN)
    first = p.ensure(ADMIN)
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_IMAGE", IMAGE.replace(":v1", ":v2"))
    again = p.ensure(ADMIN)                           # running: its sessions keep going on v1
    assert _machine(fake)["config"]["image"] == IMAGE and again.secret == first.secret
    p.stop(ADMIN)
    third = p.ensure(ADMIN)                           # stopped: it starts on v2, with new material
    m = _machine(fake)
    assert m["config"]["image"].endswith(":v2") and m["state"] == "started"
    assert third.secret != first.secret and third.secret == _files(m)["secret"]


def test_a_stopped_machine_updated_to_a_new_image_is_started_once_the_update_settles(fake, monkeypatch):
    # what failed on a workspace app's machine: Fly left the updated machine stopped, and the start
    # waited a minute for a machine nothing started
    monkeypatch.setattr(fly.time, "sleep", lambda seconds: None)
    p = workspaces.for_user(ADMIN)
    p.ensure(ADMIN)
    p.stop(ADMIN)
    fake.settle_reads = 3
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_IMAGE", IMAGE.replace(":v1", ":v2"))
    before = len(fake.calls("POST", "/start"))
    p.ensure(ADMIN)
    m = _machine(fake)
    assert m["state"] == "started" and m["config"]["image"].endswith(":v2")
    assert len(fake.calls("POST", "/start")) == before + 1          # one start, sent after the update had settled
    # and a start Fly refuses although the machine reads as stopped is tried again, not taken as the answer
    p.stop(ADMIN)
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_IMAGE", IMAGE.replace(":v1", ":v3"))
    fake.refuse_starts = 2
    p.ensure(ADMIN)
    assert _machine(fake)["state"] == "started" and fake.refuse_starts == 0


def test_an_update_that_does_not_settle_is_told(fake, monkeypatch):
    clock = [0.0]

    def sleep(seconds):
        clock[0] += seconds
        assert clock[0] < 10 * fly.UPDATE_SETTLE, "the wait for the update has no end"

    monkeypatch.setattr(fly.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(fly.time, "sleep", sleep)
    p = workspaces.for_user(ADMIN)
    p.ensure(ADMIN)
    p.stop(ADMIN)
    fake.settle_reads = 10 ** 6                                        # still being made, as far as anyone can tell
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_IMAGE", IMAGE.replace(":v1", ":v2"))
    with pytest.raises(workspaces.WorkspaceAccess, match="did not finish in time"):
        p.ensure(ADMIN)


def test_fly_s_resolved_digest_is_the_same_image(fake):
    p = workspaces.for_user(ADMIN)
    p.ensure(ADMIN)
    p.stop(ADMIN)
    _machine(fake)["config"]["image"] = IMAGE + "@sha256:" + "a" * 64
    p.ensure(ADMIN)
    updates = [path for _, path, _ in fake.calls("POST") if re.fullmatch(rf"/v1/apps/{APP}/machines/[0-9a-f]+", path)]
    assert fake.calls("POST", "/start") and updates == []


def test_a_certificate_near_its_end_is_renewed_even_while_the_machine_runs(fake, monkeypatch):
    p = workspaces.for_user(ADMIN)
    first = p.ensure(ADMIN)
    monkeypatch.setattr(fly, "RENEW_DAYS", 10 * 365)      # every certificate is now near its end
    again = p.ensure(ADMIN)
    assert again.secret != first.secret and again.secret == _files(_machine(fake))["secret"]


def test_a_new_idle_time_is_taken_when_the_machine_is_next_stopped(fake, monkeypatch):
    p = workspaces.for_user(ADMIN)
    p.ensure(ADMIN)
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_IDLE_MINUTES", "45")
    p.ensure(ADMIN)
    assert _machine(fake)["config"]["env"] == {"PP_WORKSPACE_IDLE_SECONDS": "1800"}     # running: left as it is
    p.stop(ADMIN)
    p.ensure(ADMIN)
    assert _machine(fake)["config"]["env"] == {"PP_WORKSPACE_IDLE_SECONDS": "2700"}


def test_waiting_for_a_machine_needs_its_secret_not_just_an_answer(tmp_path):
    from workspace_fixtures import SECRET, Agent
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home))
    try:
        base = a.base
        _real_wait_healthy(workspaces.Endpoint(base, base.replace("http", "ws", 1), SECRET, None), wait=1)
        with pytest.raises(WorkspaceAccess):
            _real_wait_healthy(workspaces.Endpoint(base, base.replace("http", "ws", 1), "x" * 40, None), wait=1)
    finally:
        a.close()


def test_a_new_client_certificate_restarts_even_a_running_machine(fake, env):
    p = workspaces.for_user(ADMIN)
    p.ensure(ADMIN)
    os.unlink(os.path.join(str(env), "workspace-client.crt"))
    p.ensure(ADMIN)
    crt, _ = fly.client_identity()
    assert _files(_machine(fake))["app.crt"] == open(crt).read()


def test_a_machine_made_but_not_written_down_is_found_again(fake):
    p = workspaces.for_user(ADMIN)
    p.ensure(ADMIN)
    with store.connect() as conn:
        conn.execute("UPDATE ade_workspaces SET machine_id = NULL WHERE user_id = ?", (ADMIN["id"],))
    endpoint = p.ensure(ADMIN)
    assert len(fake.machines) == 1 and endpoint.secret == _files(_machine(fake))["secret"]


def test_a_destroyed_machine_is_made_again_on_the_same_volume(fake):
    p = workspaces.for_user(ADMIN)
    p.ensure(ADMIN)
    old = _machine(fake)
    fake.destroy(old["id"])
    p.ensure(ADMIN)
    new = _machine(fake)
    assert new["id"] != old["id"] and new["config"]["mounts"] == old["config"]["mounts"] and len(fake.volumes) == 1


def test_concurrent_starts_make_one_machine(fake):
    p = workspaces.for_user(ADMIN)
    errors = []

    def start():
        try:
            p.ensure(ADMIN)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=start) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and len(fake.machines) == 1 and len(fake.volumes) == 1


# ---- accounts -------------------------------------------------------------------------------------------------------

def test_each_account_reaches_its_own_machine_and_no_other(fake, monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_FOR", "everyone")
    a = workspaces.for_user(ADMIN).ensure(ADMIN)
    b = workspaces.for_user(MEMBER).ensure(MEMBER)
    ids = {m["config"]["metadata"]["proofpane_workspace"]: m["id"] for m in fake.machines.values()}
    assert ids[fly.account_label(ADMIN["id"])] in a.http and ids[fly.account_label(MEMBER["id"])] in b.http
    assert a.http != b.http and a.secret != b.secret and len(fake.volumes) == 2
    assert workspaces.for_user(ADMIN).endpoint(ADMIN).http == a.http


def test_members_have_no_workspace_until_everyone_is_set(fake, monkeypatch):
    with pytest.raises(WorkspaceAccess) as e:
        workspaces.for_user(MEMBER)
    assert e.value.status == 403 and fake.log == []
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_FOR", "everyone")
    workspaces.for_user(MEMBER)


def test_the_deployment_makes_no_more_workspaces_than_it_is_set_to(fake, monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_FOR", "everyone")
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_MAX", "1")
    workspaces.for_user(ADMIN).ensure(ADMIN)
    with pytest.raises(WorkspaceAccess) as e:
        workspaces.for_user(MEMBER).ensure(MEMBER)
    assert e.value.status == 403 and len(fake.machines) == 1
    workspaces.for_user(ADMIN).ensure(ADMIN)                 # the one that exists still starts


def test_a_workspace_in_another_fly_app_is_not_moved(fake, monkeypatch):
    workspaces.for_user(ADMIN).ensure(ADMIN)
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_APP", "pp-other-app")
    p = workspaces.for_user(ADMIN)
    assert p.endpoint(ADMIN) is None
    with pytest.raises(WorkspaceAccess, match="not moved"):
        p.ensure(ADMIN)


# ---- spending -------------------------------------------------------------------------------------------------------

OCT = 1790812800.0           # 2026-10-01T00:00:00Z: early in a month, so the storage's share is a few cents at most


def test_the_meter_counts_the_time_started_machines_run(fake):
    s = fly.settings()
    workspaces.for_user(ADMIN).ensure(ADMIN)
    assert fly.meter_tick(s, now=OCT)["running_hours"] == 0         # the first reading has nothing before it
    for n in range(1, 31):
        spend = fly.meter_tick(s, now=OCT + 60 * n)
    assert spend["running_hours"] == 0.5 and spend["compute_usd"] == round(0.5 * s.usd_per_hour, 4)
    workspaces.for_user(ADMIN).stop(ADMIN)
    assert fly.meter_tick(s, now=OCT + 60 * 31)["running_hours"] == 0.5   # a stopped machine adds nothing
    # storage: the volume and the machine's root file system, for the part of the month gone
    assert 0 < spend["storage_usd"] < 0.01 and spend["spent_usd"] == round(spend["compute_usd"] + spend["storage_usd"],
                                                                           4)
    late = fly.spending(s, now=OCT + 30 * 86400)                   # the same storage, a month on
    assert round(late["storage_usd"], 2) == round((5 + fly.ROOTFS_GB) * 0.15 * 30 / 31, 2)


def test_a_time_the_app_did_not_see_counts_at_most_the_idle_time(fake):
    s = fly.settings()
    workspaces.for_user(ADMIN).ensure(ADMIN)
    fly.meter_tick(s, now=OCT)
    spend = fly.meter_tick(s, now=OCT + 5 * 3600)                  # five hours with the app away
    assert spend["running_hours"] == s.idle_seconds / 3600 == 0.5


def test_each_month_is_its_own(fake):
    s = fly.settings()
    workspaces.for_user(ADMIN).ensure(ADMIN)
    fly.meter_tick(s, now=OCT - 600)
    fly.meter_tick(s, now=OCT - 60)                                # nine minutes in September
    assert fly.spending(s, now=OCT - 30)["month"] == "2026-09"
    assert fly.spending(s, now=OCT - 30)["running_hours"] == round(540 / 3600, 3)
    fly.meter_tick(s, now=OCT + 60)                                # two minutes, counted in October
    assert fly.spending(s, now=OCT + 61)["month"] == "2026-10"
    assert fly.spending(s, now=OCT + 61)["running_hours"] == round(120 / 3600, 3)


def test_the_meter_stops_every_machine_before_the_cap_is_reached(fake, monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_CAP_USD", "1")
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_USD_PER_HOUR", "1")
    s = fly.settings()
    workspaces.for_user(ADMIN).ensure(ADMIN)
    fly.meter_tick(s, now=OCT)
    spend = fly.meter_tick(s, now=OCT + 30 * 60)                   # 50 cents run; with the next quarter hour, 75
    assert spend["stopped"] == [] and _machine(fake)["state"] == "started"
    spend = fly.meter_tick(s, now=OCT + 45 * 60)                   # 75 run; with the next quarter hour, the dollar
    assert spend["stopped"] == [_machine(fake)["id"]] and _machine(fake)["state"] == "stopped"


def test_a_start_is_refused_when_the_month_has_no_room(fake, monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_CAP_USD", "1")
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_USD_PER_HOUR", "0.5")
    now = [OCT + 600]
    monkeypatch.setattr(fly, "clock", lambda: now[0])
    p = workspaces.for_user(ADMIN)
    p.ensure(ADMIN)
    s = fly.settings()
    fly.meter_tick(s)
    now[0] += 30 * 60
    fly.meter_tick(s)                                              # 25 cents run
    p.ensure(ADMIN)                                                # running already: nothing new to spend
    p.stop(ADMIN)
    p.ensure(ADMIN)                                                # 25, and an hour's 50, is under the dollar
    p.stop(ADMIN)
    with store.connect() as conn:
        conn.execute("UPDATE ade_workspace_spend SET seconds = seconds + 3600")      # 75 cents
    with pytest.raises(WorkspaceAccess, match="no room to start") as e:
        p.ensure(ADMIN)                                            # 75, and an hour's 50, passes it
    assert e.value.status == 403 and _machine(fake)["state"] == "stopped"


def test_the_spending_is_shown_to_an_admin_only(fake, monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_FOR", "everyone")
    before = workspaces.for_user(ADMIN).status(ADMIN)
    assert before["spending"]["cap_usd"] == 50.0 and "not Fly's bill" in before["spending"]["measured"]
    workspaces.for_user(ADMIN).ensure(ADMIN)
    assert workspaces.for_user(ADMIN).status(ADMIN)["spending"]["month"]
    assert "spending" not in workspaces.for_user(MEMBER).status(MEMBER)


def test_the_meter_runs_beside_the_app_for_the_fly_provider_only(fake, monkeypatch):
    started = []
    monkeypatch.setattr(fly, "start_meter", lambda: started.append(1))
    workspaces.start()
    assert started == [1]
    monkeypatch.setenv("OPEN_EDITION_ADE", "local")
    workspaces.start()
    assert started == [1]


# ---- Fly's answers --------------------------------------------------------------------------------------------------

def test_a_rate_limited_call_is_tried_again(fake):
    fake.answers = [(429, {"error": "rate limited"}), (429, {"error": "rate limited"})]
    workspaces.for_user(ADMIN).ensure(ADMIN)
    assert len(fake.machines) == 1


def test_an_error_names_fly_s_message_and_never_the_token(fake):
    fake.answers = [(500, {"error": "the region is out of capacity"})]
    with pytest.raises(WorkspaceAccess) as e:
        workspaces.for_user(ADMIN).ensure(ADMIN)
    assert e.value.status == 502 and "out of capacity" in str(e.value) and TOKEN not in str(e.value)


def test_a_flyv1_token_is_sent_as_it_is(fake, monkeypatch):
    fake.expected = "FlyV1 fm2_" + "q" * 30
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_FLY_TOKEN", fake.expected)
    workspaces.for_user(ADMIN).ensure(ADMIN)
    assert len(fake.machines) == 1


def test_the_state_is_asked_of_fly_at_most_every_few_seconds(fake):
    p = workspaces.for_user(ADMIN)
    p.ensure(ADMIN)
    gets = len(fake.calls("GET"))
    for _ in range(5):
        assert p.endpoint(ADMIN) is not None
    assert len(fake.calls("GET")) == gets


# ---- through the image's own TLS front, for real --------------------------------------------------------------------

needs_caddy = pytest.mark.skipif(caddy_binary() is None, reason="needs Caddy: set PP_TEST_CADDY or put caddy on PATH")


@pytest.fixture
def machines(fake, tmp_path, monkeypatch):
    local = LocalMachines(str(tmp_path / "fly"))
    fake.boot, fake.halt = local.boot, local.halt
    monkeypatch.setattr(fly, "address", local.address)
    monkeypatch.setattr(fly, "wait_healthy", _real_wait_healthy)
    yield local
    local.close()


_real_wait_healthy = fly.wait_healthy


@needs_caddy
def test_the_app_reaches_the_machine_through_its_tls_front(machines, fake, tmp_path):
    endpoint = workspaces.for_user(ADMIN).ensure(ADMIN)
    assert endpoint.http.startswith("https://localhost:")
    assert client.call(endpoint, "GET", "/sessions") == {"sessions": []}
    boot = tmp_path / "fly" / "boot" / _machine(fake)["id"]
    assert not (boot / "secret").exists()                      # the agent read it and deleted it


def _context(endpoint, pin=None, client_cert=True, groups=None, tls12=False):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2 if tls12 else ssl.TLSVersion.TLSv1_3
    if tls12:
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = False
    ctx.load_verify_locations(cadata=pin)
    if client_cert:
        ctx.load_cert_chain(*fly.client_identity())
    if groups:
        ctx.set_ecdh_curve(groups)
    return ctx


@needs_caddy
def test_nothing_else_gets_through_the_tls_front(machines, fake):
    endpoint = workspaces.for_user(ADMIN).ensure(ADMIN)
    pin = _files(_machine(fake))["tls.crt"]
    other_pin = fly.material(APP)["cert"]
    # each of these fails in the handshake, before any HTTP: "not answering", not an answer from the agent
    for label, ctx in (("no client certificate", _context(endpoint, pin, client_cert=False)),
                       ("another machine's certificate pinned", fly.tls_context(other_pin, *fly.client_identity())),
                       ("only the classical X25519 exchange", _context(endpoint, pin, groups="X25519")),
                       ("only TLS 1.2", _context(endpoint, pin, tls12=True))):
        wrong = workspaces.Endpoint(endpoint.http, endpoint.ws, endpoint.secret, ctx)
        with pytest.raises(client.WorkspaceCallError) as e:
            client.call(wrong, "GET", "/sessions")
        assert str(e.value) == "The workspace is not answering.", label
    right = workspaces.Endpoint(endpoint.http, endpoint.ws, endpoint.secret, _context(endpoint, pin))
    assert client.call(right, "GET", "/sessions") == {"sessions": []}
    # through the handshake with everything right but the secret: the agent itself refuses
    no_secret = workspaces.Endpoint(endpoint.http, endpoint.ws, "w" * 43, endpoint.ssl)
    with pytest.raises(client.WorkspaceCallError) as e:
        client.call(no_secret, "GET", "/sessions")
    assert str(e.value) == "The workspace answered 401."


@needs_caddy
def test_a_terminal_through_the_tls_front(machines, fake, tmp_path):
    from workspace_fixtures import make_repo
    endpoint = workspaces.for_user(ADMIN).ensure(ADMIN)
    volume = next(iter(fake.volumes))
    make_repo(str(tmp_path / "fly" / "volumes" / volume / "workspace" / "repos" / "demo"))
    s = client.call(endpoint, "POST", "/sessions", {"repo": "demo", "program": "shell", "task": "t"})

    async def run():
        agent = await client.open_terminal(endpoint, s["id"])
        try:
            await agent.send(b"echo through-$((40+2))\n")
            got = b""
            while b"through-42" not in got:
                msg = await asyncio.wait_for(agent.recv(), 15)
                if isinstance(msg, bytes):
                    got += msg
        finally:
            await agent.close()

    asyncio.run(run())


@needs_caddy
def test_after_a_new_image_the_old_machine_s_certificate_and_secret_no_longer_work(machines, fake, monkeypatch):
    p = workspaces.for_user(ADMIN)
    old = p.ensure(ADMIN)
    p.stop(ADMIN)
    monkeypatch.setenv("OPEN_EDITION_WORKSPACE_IMAGE", IMAGE.replace(":v1", ":v2"))
    new = p.ensure(ADMIN)
    assert client.call(new, "GET", "/sessions") == {"sessions": []}
    with pytest.raises(client.WorkspaceCallError) as e:
        client.call(old, "GET", "/sessions")
    assert str(e.value) == "The workspace is not answering."            # the old certificate: refused in TLS
    old_secret = workspaces.Endpoint(new.http, new.ws, old.secret, new.ssl)
    with pytest.raises(client.WorkspaceCallError) as e:
        client.call(old_secret, "GET", "/sessions")
    assert str(e.value) == "The workspace answered 401."                # the old secret: refused by the agent
