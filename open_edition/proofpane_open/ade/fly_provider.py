"""The fly provider: one Fly Machine and one volume per user, in a Fly app that holds only workspaces.

A user's machine is made the first time they start their workspace, from the workspace image
(workspace_image/Dockerfile), with a volume of their own as their home, so what they cloned and the programs' own
sign-ins stay between starts. The machine has no public service. The app reaches it over Fly's private network, at
<machine>.vm.<app>.internal, only through the machine's Caddy, which takes TLS 1.3 with the X25519MLKEM768 exchange
and nothing else and requires this app's client certificate; the app in turn trusts only the certificate it made for
that machine, and sends the workspace's secret with every call. The agent in the machine stops by itself after the
idle time, which stops the machine, and the next start starts it again.

Each machine's configuration carries, for its start (workspace_image/start.sh) to put where only their one user reads
them: its certificate and key, this app's client certificate, its secret, and its idle time. The secret is kept here
sealed with the key-store key and the certificate as it is; the key is not kept here.

With them go the two parts its sessions' approval hook takes: this app's own address, and a hook token of
the account's that this app makes at each configuration it writes, as it makes the secret, so that a claude terminal's
or chat's gated command waits for the owner's approval in this deployment's queue (open_edition/ade_hook.py). The token
is not kept here, only its id on the workspace's row: the next configuration's token replaces it, the one before is
revoked, and the row's removal revokes it (store.py, ade_workspaces_token_gone).

Isolation: each user has a machine, a volume and a secret of their own, and a machine's one listening port accepts
nothing without this app's client certificate. Fly joins every app of an organization on one private network, and a
workspace runs its user's code, so each machine's firewall (workspace_image/firewall.nft, loaded by root before
anything else) takes in nothing but that port and Fly's SSH, and lets nothing new out into the private network but
DNS: a user's code reaches the internet, not another machine or another app of the organization. That firewall is
inside the machine; a network of their own for the workspace app and this app (an organization of their own, or apps
made on a network of their own: https://docs.fly.io/networking/custom-private-networks/) adds Fly's separation,
which not even root in a machine can cross. The operator states which it is, OPEN_EDITION_WORKSPACE_OWN_NETWORK=yes or
no; the provider does not start until one is stated.

Spending (the deployment's cap: 50 US dollars a calendar month for all workspace machines together, which a setting
can lower): every minute this app reads the workspace machines' states and adds the time each
started one ran since the last reading to the month's running time; the month's spending is that time at
OPEN_EDITION_WORKSPACE_USD_PER_HOUR, plus the volumes and the stopped machines' root file systems at Fly's storage
price for the part of the month gone. A time this app did not see (it was not running) counts as at most the idle
time: without the app nobody can type into a workspace, so it stops by itself after that. A start is refused when
the month's spending plus an hour of every machine that would then be running passes the cap; every started machine
is stopped when the spending plus a quarter of an hour of them would reach it. It is this app's estimate from Fly's
published prices, not Fly's bill.

Each account's share of the cap: an account that is not an admin may spend
OPEN_EDITION_WORKSPACE_SHARE_USD of it a month, by default the cap divided by OPEN_EDITION_WORKSPACE_MAX and never more
than the cap; an admin's share is the whole cap. An account's spending is its own machine's running time and its own
volume and machine disk, at the prices the cap uses: the meter writes, with each reading, whose machine ran and the
size of each account's storage. A start is refused, in words naming the share, when the account's spending and an hour
of its machine would pass its share; the meter stops that account's machine alone when its spending and a quarter of
an hour would reach it, and the cap's stop of every started machine stays as it is.

What leaving the hosted choice keeps (storage.py): the account's machine stops at once, and its
machine, its volume and its record here are kept OPEN_EDITION_WORKSPACE_KEEP_DAYS days; choosing hosted again within
them starts the same machine and volume. After them the meter's reading destroys the machine and its volume at Fly and
deletes the account's ade_workspaces row, so neither counts toward the cap or the number of workspaces, and records that
it did in ade_workspace_removals; the person may also delete it at once (storage.delete_workspace). What this app keeps
of the account's agents, tasks, controller and finishes stays.

    OPEN_EDITION_WORKSPACE_APP           the Fly app for workspace machines, never this app
    OPEN_EDITION_WORKSPACE_IMAGE         the workspace image, as Fly's registry names it
    OPEN_EDITION_WORKSPACE_FLY_TOKEN     a deploy token for that app only (fly tokens create deploy -a <app>)
    OPEN_EDITION_WORKSPACE_OWN_NETWORK   yes (the two apps are alone on their private network) or no (they share it)
    OPEN_EDITION_KEYSTORE_KEY            the key-store key (ledger.py)
  optional:
    OPEN_EDITION_WORKSPACE_REGION        default this app's region (FLY_REGION), else syd
    OPEN_EDITION_WORKSPACE_CPUS          shared CPUs, default 1
    OPEN_EDITION_WORKSPACE_MEMORY_MB     default 2048
    OPEN_EDITION_WORKSPACE_VOLUME_GB     default 5
    OPEN_EDITION_WORKSPACE_IDLE_MINUTES  default 30
    OPEN_EDITION_WORKSPACE_FOR           admins (the default) or everyone
    OPEN_EDITION_WORKSPACE_MAX           the most workspaces this deployment makes, default 10
    OPEN_EDITION_WORKSPACE_CAP_USD       the month's cap for all workspace machines: 50, or lower
    OPEN_EDITION_WORKSPACE_USD_PER_HOUR  what a started machine of this size costs an hour, default 0.03
    OPEN_EDITION_WORKSPACE_SHARE_USD     an account's share of the cap, unless an admin's: default the cap divided by
                                         OPEN_EDITION_WORKSPACE_MAX, never more than the cap
    OPEN_EDITION_WORKSPACE_KEEP_DAYS     how long a workspace whose account left the hosted choice is kept, default 30
    OPEN_EDITION_FLY_API                 default https://api.machines.dev

The calls follow Fly's Machines API reference (docs.fly.io/machines/api, read 2026-09-28) and are exercised against a
stand-in (tests/fly_fixtures.py) that boots the image's Caddy and agent on this machine. First run on Fly: the volume
and the machine made, the machine started in 25 seconds, its firewall loaded, and the app reached it at its .internal
name through the TLS front.
"""

import base64
import binascii
import contextlib
import dataclasses
import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography import x509
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .. import config, store
from . import approvals
from . import ledger
from . import workspace_client as client
from .workspaces import Endpoint, WorkspaceAccess

API = "https://api.machines.dev"
PORT = 8443
BOOT = "/etc/proofpane/boot"
HOME = "/home/ws"
# the sessions' approval hook: the boot files of this app's address and of the workspace's hook token,
# which start.sh puts where the hook reads them, and the name the owner's list of hook tokens shows the token by
ADE_URL_FILE = "ade-url"
ADE_TOKEN_FILE = "ade-hook-token"
HOOK_TOKEN_NAME = "Hosted workspace"
CERT_DAYS = 5 * 365
RENEW_DAYS = 30
CLIENT_CERT_DAYS = 10 * 365
START_WAIT = 60
HEALTH_WAIT = 60
UPDATE_SETTLE = 180                     # an update pulls the new image (about 1.1 GB) before the machine can start
STATE_TTL = 5.0
MAX_REPLY = 1024 * 1024
STOPPED = ("stopped", "suspended", "created", "failed")
CAP_DECIDED_USD = 50.0                  # the deployment's cap; the setting lowers it
METER_EVERY = 60
STOP_AHEAD = 15 * 60                    # stop the machines this long before the spending would reach the cap
START_AHEAD = 60 * 60                   # a start needs room for an hour of every machine then running
STORAGE_USD_PER_GB_MONTH = 0.15         # Fly's price for volumes and a stopped machine's root file system (read
ROOTFS_GB = 1.2                         # 2026-09-28); the workspace image is about 1.1 GB
BUSY = ("starting", "updating", "replacing", "stopping", "suspending")

_APP = re.compile(r"^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$")
_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,299}$")
_REGION = re.compile(r"^[a-z]{3}$")
_MACHINE = re.compile(r"^[0-9a-z]{1,40}$")
_VOLUME = re.compile(r"^vol_[0-9a-z]{1,60}$")
_BOX = "v1."
# the address the hook takes for a deployment on the internet (ade_hook.URL, its https part): a bare origin
_ADE_ADDRESS = re.compile(r"https://[A-Za-z0-9.-]+(?::\d+)?")


@dataclasses.dataclass(frozen=True)
class Settings:
    app: str
    image: str
    token: str
    region: str
    cpus: int
    memory_mb: int
    volume_gb: int
    idle_seconds: int
    everyone: bool
    max_workspaces: int
    api: str
    own_network: bool = False
    cap_usd: float = 50.0
    usd_per_hour: float = 0.03
    share_usd: float = 5.0
    keep_days: int = 30


def _number(name, default, low, high, multiple=1):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    value = int(raw) if raw.isdigit() else -1
    if not low <= value <= high or value % multiple:
        raise RuntimeError(f"{name} is a whole number from {low} to {high}"
                           + (f" and a multiple of {multiple}" if multiple > 1 else ""))
    return value


def _money(name, default, low, high):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = -1.0
    if not low <= value <= high:
        raise RuntimeError(f"{name} is a number of US dollars from {low:g} to {high:g}")
    return value


def settings():
    """This deployment's fly provider settings, from the environment; RuntimeError naming the first one that is
    missing or wrong."""
    env = os.environ
    app = env.get("OPEN_EDITION_WORKSPACE_APP", "").strip()
    if not _APP.match(app):
        raise RuntimeError("OPEN_EDITION_WORKSPACE_APP names the Fly app for workspace machines")
    if app == env.get("FLY_APP_NAME", "").strip():
        raise RuntimeError("OPEN_EDITION_WORKSPACE_APP is an app of its own, never this app: a workspace runs its "
                           "user's code")
    image = env.get("OPEN_EDITION_WORKSPACE_IMAGE", "").strip()
    if not _IMAGE.match(image):
        raise RuntimeError("OPEN_EDITION_WORKSPACE_IMAGE names the workspace image")
    token = env.get("OPEN_EDITION_WORKSPACE_FLY_TOKEN", "").strip()
    if not token or any(c in token for c in "\r\n\0"):
        raise RuntimeError("OPEN_EDITION_WORKSPACE_FLY_TOKEN is a deploy token for the workspace app")
    own = env.get("OPEN_EDITION_WORKSPACE_OWN_NETWORK", "").strip()
    if own not in ("yes", "no"):
        raise RuntimeError("OPEN_EDITION_WORKSPACE_OWN_NETWORK says whether the workspace app and this app are the "
                           "only apps on their Fly private network: yes or no (fly_provider.py says what each means)")
    region = (env.get("OPEN_EDITION_WORKSPACE_REGION", "").strip() or env.get("FLY_REGION", "").strip() or "syd")
    if not _REGION.match(region):
        raise RuntimeError("OPEN_EDITION_WORKSPACE_REGION is a Fly region, such as syd")
    who = env.get("OPEN_EDITION_WORKSPACE_FOR", "").strip() or "admins"
    if who not in ("admins", "everyone"):
        raise RuntimeError("OPEN_EDITION_WORKSPACE_FOR is admins or everyone")
    api = (env.get("OPEN_EDITION_FLY_API", "").strip() or API).rstrip("/")
    parts = urllib.parse.urlsplit(api)
    local = parts.hostname in ("127.0.0.1", "localhost", "::1", "_api.internal")
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise RuntimeError("OPEN_EDITION_FLY_API is an https address, or Fly's own http://_api.internal:4280")
    most = _number("OPEN_EDITION_WORKSPACE_MAX", 10, 1, 1000)
    cap = _money("OPEN_EDITION_WORKSPACE_CAP_USD", CAP_DECIDED_USD, 0.01, CAP_DECIDED_USD)
    # an account's share, never more than the cap, which an admin's is (share_for): one set above a cap this
    # deployment has since lowered is read as the cap, so that lowering the cap never stops the app from starting
    share = _money("OPEN_EDITION_WORKSPACE_SHARE_USD", cap / most, 0.01, CAP_DECIDED_USD)
    return Settings(app=app, image=image, token=token, region=region,
                    cpus=_number("OPEN_EDITION_WORKSPACE_CPUS", 1, 1, 8),
                    memory_mb=_number("OPEN_EDITION_WORKSPACE_MEMORY_MB", 2048, 256, 16384, multiple=256),
                    volume_gb=_number("OPEN_EDITION_WORKSPACE_VOLUME_GB", 5, 1, 500),
                    idle_seconds=60 * _number("OPEN_EDITION_WORKSPACE_IDLE_MINUTES", 30, 5, 24 * 60),
                    everyone=who == "everyone", max_workspaces=most, api=api, own_network=own == "yes", cap_usd=cap,
                    usd_per_hour=_money("OPEN_EDITION_WORKSPACE_USD_PER_HOUR", 0.03, 0.001, 100),
                    share_usd=min(share, cap),
                    keep_days=_number("OPEN_EDITION_WORKSPACE_KEEP_DAYS", 30, 1, 3650))


def check_config():
    settings()
    try:
        key = ledger.keystore_key()
    except ledger.LedgerError as e:
        raise RuntimeError(str(e)) from None
    if key is None:
        raise RuntimeError("the fly provider keeps each workspace's secret sealed with the key-store key: set "
                           "OPEN_EDITION_KEYSTORE_KEY")


# ---- names, secrets and certificates --------------------------------------------------------------------------------

def account_label(user_id):
    """What names a user's machine and volume at Fly: a hash of the account's id, not the id itself."""
    return hashlib.sha256(("proofpane-open/workspace/" + user_id).encode("utf-8")).hexdigest()[:24]


def machine_name(label):
    return "ws-" + label


def volume_name(label):
    return "ws_" + label[:20]


def address(machine_id, app):
    """Where the app reaches a machine: its name on the private network, which Caddy needs to match the Host header."""
    return f"{machine_id}.vm.{app}.internal", PORT


def _aad(user_id):
    return f"proofpane-open/ade-workspace-secret/v1/{user_id}".encode("utf-8")


def seal(secret, user_id):
    key = ledger.keystore_key()
    if key is None:
        raise WorkspaceAccess(503, "This deployment keeps no key-store key, so it cannot keep a workspace's secret.")
    nonce = secrets.token_bytes(12)
    return _BOX + base64.b64encode(nonce + AESGCM(key).encrypt(nonce, secret.encode("utf-8"), _aad(user_id))).decode()


def unseal(box, user_id):
    """The secret, or None when the box cannot be opened (another account's, or the key-store key changed)."""
    try:
        key = ledger.keystore_key()
        if key is None or not isinstance(box, str) or not box.startswith(_BOX):
            return None
        raw = base64.b64decode(box[len(_BOX):], validate=True)
        return AESGCM(key).decrypt(raw[:12], raw[12:], _aad(user_id)).decode("utf-8")
    except (ValueError, binascii.Error, InvalidTag, UnicodeDecodeError, ledger.LedgerError):
        return None


def _certificate(key, name, usage, dns_names, days):
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
               .serial_number(x509.random_serial_number())
               .not_valid_before(now - datetime.timedelta(minutes=5))
               .not_valid_after(now + datetime.timedelta(days=days))
               .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
               .add_extension(x509.ExtendedKeyUsage([usage]), critical=False))
    if dns_names:
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in dns_names]),
                                        critical=False)
    return builder.sign(key, None)


def _pem_key(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode("ascii")


def material(app):
    """A new secret, key and certificate for one workspace machine."""
    key = ed25519.Ed25519PrivateKey.generate()
    cert = _certificate(key, "proofpane workspace", ExtendedKeyUsageOID.SERVER_AUTH, [f"*.vm.{app}.internal"],
                        CERT_DAYS)
    return {"secret": secrets.token_urlsafe(32), "key": _pem_key(key),
            "cert": cert.public_bytes(serialization.Encoding.PEM).decode("ascii")}


def fingerprint(pem):
    der = x509.load_pem_x509_certificate(pem.encode("ascii")).public_bytes(serialization.Encoding.DER)
    return hashlib.sha256(der).hexdigest()[:32]


def expiring(pem):
    try:
        cert = x509.load_pem_x509_certificate(pem.encode("ascii"))
    except ValueError:
        return True
    left = cert.not_valid_after_utc - datetime.datetime.now(datetime.timezone.utc)
    return left < datetime.timedelta(days=RENEW_DAYS)


_client_lock = threading.Lock()


def _write_private(path, text):
    tmp = path + ".tmp"
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(text)
    os.replace(tmp, path)


def client_identity():
    """(certificate path, key path) of this app's client certificate for its workspaces: made on first use, kept in
    the data directory and readable by this user only. Every machine's Caddy trusts it and nothing else."""
    d = config.data_dir()
    crt, key = os.path.join(d, "workspace-client.crt"), os.path.join(d, "workspace-client.key")
    with _client_lock:
        if not (os.path.exists(crt) and os.path.exists(key)):
            k = ed25519.Ed25519PrivateKey.generate()
            cert = _certificate(k, "proofpane open edition", ExtendedKeyUsageOID.CLIENT_AUTH, None, CLIENT_CERT_DAYS)
            _write_private(key, _pem_key(k))
            _write_private(crt, cert.public_bytes(serialization.Encoding.PEM).decode("ascii"))
    return crt, key


def tls_context(cert_pem, client_crt, client_key):
    """TLS 1.3, trusting only this machine's own certificate, and presenting this app's. The name is not checked: the
    one certificate made for this machine is the whole of what is trusted, which a name could only narrow."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(cadata=cert_pem)
    ctx.load_cert_chain(client_crt, client_key)
    return ctx


def _b64(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def ade_address():
    """This app's address as the sessions' approval hook takes it (ade_hook.URL: a bare https origin), or None, when the
    deployment's public address is not one: the machine is then given none, and the hook refuses each gated command,
    saying it has no ADE address."""
    url = config.public_url()
    return url if _ADE_ADDRESS.fullmatch(url) else None


def hook_material(user_id):
    """For a configuration about to be written: a new hook token of the account's for the workspace's sessions, and this
    app's address. Returns (the token's id, the parts machine_config takes). The token is kept nowhere
    here; its id goes on the row once the configuration is written (_with_hook_token)."""
    token_id, token = approvals.create_token(user_id, HOOK_TOKEN_NAME, cap=False)
    return token_id, {"ade_token": token, "ade_url": ade_address()}


def hook_token_live(user_id, token_id):
    """Whether the workspace's hook token is one of the account's live ones. One the owner revoked, or none at all (a
    machine made before workspaces had one), makes the configuration stale: the machine takes a new one when it is next
    started from stopped, never by restarting it under its sessions."""
    if not token_id:
        return False
    with store.connect() as conn:
        return conn.execute("SELECT 1 FROM ade_hook_tokens WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
                            (token_id, user_id)).fetchone() is not None


def machine_config(s, volume_id, stuff, client_pem, label):
    """A machine's whole configuration (Fly takes no partial update). No services: closed to the internet. With the
    sessions' approval hook's two parts (hook_material) when `stuff` has them."""
    files = [{"guest_path": f"{BOOT}/tls.crt", "raw_value": _b64(stuff["cert"])},
             {"guest_path": f"{BOOT}/tls.key", "raw_value": _b64(stuff["key"])},
             {"guest_path": f"{BOOT}/app.crt", "raw_value": _b64(client_pem)},
             {"guest_path": f"{BOOT}/secret", "raw_value": _b64(stuff["secret"])}]
    for name, part in ((ADE_URL_FILE, "ade_url"), (ADE_TOKEN_FILE, "ade_token")):
        if stuff.get(part):
            files.append({"guest_path": f"{BOOT}/{name}", "raw_value": _b64(stuff[part])})
    return {
        "image": s.image,
        "guest": {"cpu_kind": "shared", "cpus": s.cpus, "memory_mb": s.memory_mb},
        "env": {"PP_WORKSPACE_IDLE_SECONDS": str(s.idle_seconds)},
        "mounts": [{"volume": volume_id, "path": HOME}],
        "files": files,
        "restart": {"policy": "on-failure", "max_retries": 3},
        "metadata": {"proofpane_workspace": label, "proofpane_client": fingerprint(client_pem)},
        "auto_destroy": False,
    }


def _dict(value):
    return value if isinstance(value, dict) else {}


def outdated(cfg, s):
    """Whether a machine's configuration differs from what this deployment asks for, in what this provider sets:
    the image (Fly may add the digest it resolved), the size, and the idle time. Anything Fly fills in is ignored."""
    cfg = _dict(cfg)
    image = cfg.get("image") if isinstance(cfg.get("image"), str) else ""
    guest, env = _dict(cfg.get("guest")), _dict(cfg.get("env"))
    return (not (image == s.image or image.startswith(s.image + "@"))
            or (guest.get("cpu_kind"), guest.get("cpus"), guest.get("memory_mb")) != ("shared", s.cpus, s.memory_mb)
            or env.get("PP_WORKSPACE_IDLE_SECONDS") != str(s.idle_seconds))


# ---- Fly's Machines API ---------------------------------------------------------------------------------------------

class FlyError(Exception):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _detail(err):
    try:
        body = json.loads(err.read(64 * 1024))
        text = body.get("error") if isinstance(body, dict) else None
    except (ValueError, AttributeError, OSError):
        text = None
    return text[:200] if isinstance(text, str) else f"HTTP {err.code}"


class Machines:
    """Fly's Machines API for the workspace app. Calls use no proxy from the environment and follow no redirect; a
    rate-limited call is tried again a few times. The token never appears in an error."""

    def __init__(self, s):
        self.base = f"{s.api}/v1/apps/{s.app}"
        self.auth = s.token if s.token.startswith("FlyV1 ") else "Bearer " + s.token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def call(self, method, path, body=None, query=None, timeout=30):
        url = self.base + path + ("?" + urllib.parse.urlencode(query) if query else "")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        for attempt in range(4):
            req = urllib.request.Request(url, data=data, method=method,
                                         headers={"Authorization": self.auth, "Content-Type": "application/json",
                                                  "Accept": "application/json"})
            try:
                with self.opener.open(req, timeout=timeout) as r:
                    raw = r.read(MAX_REPLY + 1)
                if len(raw) > MAX_REPLY:
                    raise FlyError(502, "Fly's machine service answered with too much.")
                return json.loads(raw) if raw.strip() else {}
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 3:
                    wait = e.headers.get("Retry-After", "1") if e.headers else "1"
                    time.sleep(min(5.0, float(wait) if wait.replace(".", "", 1).isdigit() else 1.0))
                    continue
                raise FlyError(e.code, _detail(e)) from None
            except (urllib.error.URLError, OSError, ValueError):
                raise FlyError(502, "Fly's machine service is not answering.") from None
        raise FlyError(429, "Fly's machine service is busy.")

    def get(self, kind, ident):
        try:
            return self.call("GET", f"/{kind}/{ident}")
        except FlyError as e:
            if e.status == 404:
                return None
            raise


def start_when_settled(api, machine_id, wait=None):
    """Start a machine an update left stopped, once the update has settled: while its new version is made it reads
    as created or busy, and a start then is refused; a machine something else has started already is left as it is."""
    deadline = time.monotonic() + (UPDATE_SETTLE if wait is None else wait)
    while True:
        state = (api.get("machines", machine_id) or {}).get("state")
        if state == "started":
            return
        if state in ("stopped", "suspended"):
            try:
                api.call("POST", f"/machines/{machine_id}/start")
                return
            except FlyError as e:
                if e.status not in (409, 412, 422):
                    raise
        elif state in ("failed", "destroyed", "destroying", None):
            raise FlyError(502, f"the workspace machine's update ended {state or 'with no machine'}")
        if time.monotonic() > deadline:
            raise FlyError(504, "the workspace machine's update did not finish in time")
        time.sleep(1)


def wait_healthy(endpoint, wait=HEALTH_WAIT):
    """Until the machine answers as this endpoint expects: through a TLS front holding the certificate made for it,
    with an agent that takes this secret. After an update that restarts a running machine, the old one keeps
    answering for a moment with the old certificate and secret; both are refused here until the new one is up.
    Listing the sessions needs the secret and does not count as activity."""
    deadline = time.monotonic() + wait
    while True:
        try:
            client.call(endpoint, "GET", "/sessions", timeout=5)
            return
        except client.WorkspaceCallError:
            if time.monotonic() > deadline:
                raise WorkspaceAccess(502, "The workspace machine started, but its agent is not answering.") from None
            time.sleep(1)


# ---- the rows ------------------------------------------------------------------------------------------------------

def _row(user_id):
    with store.connect() as conn:
        r = conn.execute("SELECT * FROM ade_workspaces WHERE user_id = ?", (user_id,)).fetchone()
    return dict(r) if r else None


def _save(user_id, **fields):
    cols = ", ".join(f"{k} = ?" for k in fields)
    with store.connect() as conn:
        conn.execute(f"UPDATE ade_workspaces SET {cols}, updated_at = ? WHERE user_id = ?",
                     (*fields.values(), int(time.time()), user_id))
    return _row(user_id)


def _reserve(user_id, s):
    now = int(time.time())
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM ade_workspaces WHERE user_id = ?", (user_id,)).fetchone() is None:
            n = conn.execute("SELECT COUNT(*) FROM ade_workspaces WHERE app = ?", (s.app,)).fetchone()[0]
            if n >= s.max_workspaces:
                raise WorkspaceAccess(403, "This deployment has made as many workspaces as it is set to make.")
            conn.execute("INSERT INTO ade_workspaces (user_id, app, region, created_at, updated_at) "
                         "VALUES (?, ?, ?, ?, ?)", (user_id, s.app, s.region, now, now))
    return _row(user_id)


def _with_hook_token(user_id, row, token_id, token, write, read_back=None):
    """Write a configuration that carries the new hook token `token` (its id `token_id`; `write`, Fly's call), then
    make that token the workspace's and revoke the one before it (_settle). Fly refusing the write (an answer of 4xx
    but 408) revokes the new token and leaves the one before as it was. A write whose outcome is not known (Fly's 5xx,
    or no answer, which Machines.call gives as 502, perhaps after Fly took it) is settled by the machine as `read_back`
    reads it: carrying the new token, as if the write had answered; carrying another, as a refusal; and where that
    cannot be read, neither token is left live and the row names none, so the machine refuses each gated command until
    it is next started from stopped, which writes a configuration anew (hook_token_live): never a machine left on a
    revoked token with nothing to repair it, and never a token left live that no machine was given. The error is
    raised whichever it was. Returns (Fly's answer, the row)."""
    before = row.get("hook_token_id")
    try:
        machine = write()
    except BaseException as e:
        refused = isinstance(e, FlyError) and 400 <= e.status < 500 and e.status != 408
        taken = False if refused or read_back is None else _carries(read_back, token)
        if taken:
            with contextlib.suppress(Exception):
                _settle(user_id, token_id, before)
        else:
            for t in (token_id, before if taken is None else None):
                if t:
                    with contextlib.suppress(Exception):
                        approvals.revoke_token(user_id, t)
            if taken is None:
                with contextlib.suppress(Exception):
                    _save(user_id, hook_token_id=None)
        raise
    return machine, _settle(user_id, token_id, before)


def _settle(user_id, token_id, before):
    """The new token the workspace's on its row, and the one before revoked. A row that cannot be written leaves
    neither token live (the machine then refuses each gated command until a configuration is written anew), since the
    row would not name the token the machine has, which its removal is to revoke. Returns the row."""
    try:
        row = _save(user_id, hook_token_id=token_id)
    except BaseException:
        for t in (token_id, before):
            if t:
                with contextlib.suppress(Exception):
                    approvals.revoke_token(user_id, t)
        raise
    if before and before != token_id:
        approvals.revoke_token(user_id, before)
    return row


def _carries(read_back, token):
    """Whether the machine `read_back` gives (Fly's answer, or None) carries `token` in its boot file: True, False, or
    None where that cannot be told (no machine read, a configuration that says nothing of its files, or a token file
    whose value Fly does not give back)."""
    try:
        machine = read_back()
    except Exception:  # noqa: BLE001  not known, which revokes both
        return None
    files = _dict(_dict(machine).get("config")).get("files")
    if not isinstance(files, list):
        return None
    for f in files:
        if isinstance(f, dict) and f.get("guest_path") == f"{BOOT}/{ADE_TOKEN_FILE}":
            raw = f.get("raw_value")
            if not isinstance(raw, str) or not raw:
                return None
            try:
                given = base64.b64decode(raw, validate=True)
            except (binascii.Error, ValueError):
                return None
            return hmac.compare_digest(hashlib.sha256(given).digest(), hashlib.sha256(token.encode()).digest())
    return False


# ---- spending -------------------------------------------------------------------------------------------------------

clock = time.time            # the time the meter reads; a test sets its own

def _month(now):
    """(the month as YYYY-MM, its first second, the next month's first second), in UTC."""
    t = datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
    start = t.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    following = (start + datetime.timedelta(days=32)).replace(day=1)
    return t.strftime("%Y-%m"), start.timestamp(), following.timestamp()


def _live(items):
    return [x for x in items or [] if isinstance(x, dict) and x.get("state") not in ("destroyed", "destroying")]


def spending(s, now=None):
    """The month's spending as this app measures it (the module's docstring says how), from its own records."""
    now = now if now is not None else clock()
    month, start, end = _month(now)
    with store.connect() as conn:
        seconds = conn.execute("SELECT COALESCE(SUM(seconds), 0) FROM ade_workspace_spend WHERE month = ?",
                               (month,)).fetchone()[0]
        row = conn.execute("SELECT storage_gb FROM ade_workspace_meter WHERE id = 1").fetchone()
    hours = seconds / 3600
    compute = hours * s.usd_per_hour
    storage = (row[0] if row else 0.0) * STORAGE_USD_PER_GB_MONTH * max(0.0, now - start) / (end - start)
    return {"month": month, "spent_usd": round(compute + storage, 4), "cap_usd": s.cap_usd,
            "running_hours": round(hours, 3), "compute_usd": round(compute, 4), "storage_usd": round(storage, 4),
            "usd_per_hour": s.usd_per_hour,
            "measured": "every minute by this app, from the machines' states: running hours at the price set for "
                        "this size, and storage at Fly's price; an estimate from Fly's published prices, not Fly's "
                        "bill"}


def share_for(s, user):
    """The account's share of the month's cap: the whole cap for an admin, else the share set, which is
    never more than the cap."""
    return s.cap_usd if user.get("admin") else min(s.share_usd, s.cap_usd)


def account_spending(s, user_id, share, now=None):
    """The account's month, as spending() measures the deployment's: the running time the meter wrote as its machine's,
    and, from a store written before the meter wrote whose it was, its current machine's; and its own volume and
    machine disk at the size the meter last read (none before a reading, as for the cap), at the cap's prices.
    {month, spent_usd, share_usd}."""
    now = now if now is not None else clock()
    month, start, end = _month(now)
    with store.connect() as conn:
        row = conn.execute("SELECT machine_id, storage_gb FROM ade_workspaces WHERE user_id = ?",
                           (user_id,)).fetchone()
        seconds = conn.execute("SELECT COALESCE(SUM(seconds), 0) FROM ade_workspace_spend WHERE month = ? AND "
                               "(user_id = ? OR (user_id IS NULL AND machine_id = ?))",
                               (month, user_id, (row["machine_id"] if row else None) or "")).fetchone()[0]
    gb = (row["storage_gb"] if row else None) or 0.0
    storage = gb * STORAGE_USD_PER_GB_MONTH * max(0.0, now - start) / (end - start)
    return {"month": month, "spent_usd": round(seconds / 3600 * s.usd_per_hour + storage, 4),
            "share_usd": round(share, 4)}


def _owners(s):
    """{machine id: user id} and {user id: (volume id, machine id)} of this app's workspace rows."""
    with store.connect() as conn:
        rows = conn.execute("SELECT user_id, volume_id, machine_id FROM ade_workspaces WHERE app = ?",
                            (s.app,)).fetchall()
    return ({r["machine_id"]: r["user_id"] for r in rows if r["machine_id"]},
            {r["user_id"]: (r["volume_id"], r["machine_id"]) for r in rows})


def _account(user_id):
    """The account as a start reads it (auth.user), whose `admin` its share follows; one no longer kept here is read as
    an account that is not an admin."""
    from .. import auth
    return auth.user(user_id) or {"id": user_id, "admin": False}


def meter_tick(s, now=None):
    """One reading: add the time each started machine ran since the last reading (at most the idle time, when this
    app did not see it) to the month, with the account whose machine it is; keep the storage's size, the deployment's
    and each account's; stop every started machine when the spending plus STOP_AHEAD of them would reach the cap, and
    otherwise each one whose account's spending plus STOP_AHEAD of it would reach its share; then remove the
    workspaces kept past their days (sweep_left). Returns the spending, with the machines it stopped."""
    now = now if now is not None else clock()
    api = Machines(s)
    machines = _live(api.call("GET", "/machines"))
    volumes = _live(api.call("GET", "/volumes"))
    started = [m for m in machines if m.get("state") == "started" and _MACHINE.match(str(m.get("id")))]
    storage_gb = sum(float(v.get("size_gb") or 0) for v in volumes) + ROOTFS_GB * len(machines)
    sizes = {v.get("id"): float(v.get("size_gb") or 0) for v in volumes}
    live = {m.get("id") for m in machines}
    owner, held = _owners(s)
    month = _month(now)[0]
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT last_tick FROM ade_workspace_meter WHERE id = 1").fetchone()
        seen = min(max(0.0, now - row[0]), float(s.idle_seconds)) if row else 0.0
        for m in started:
            conn.execute("INSERT INTO ade_workspace_spend (month, machine_id, seconds, user_id) VALUES (?, ?, ?, ?) "
                         "ON CONFLICT (month, machine_id) DO UPDATE SET seconds = seconds + excluded.seconds, "
                         "user_id = COALESCE(excluded.user_id, ade_workspace_spend.user_id)",
                         (month, m["id"], int(round(seen)), owner.get(m["id"])))
        conn.execute("INSERT INTO ade_workspace_meter (id, last_tick, storage_gb) VALUES (1, ?, ?) "
                     "ON CONFLICT (id) DO UPDATE SET last_tick = excluded.last_tick, storage_gb = excluded.storage_gb",
                     (now, storage_gb))
        for user_id, (volume_id, machine_id) in held.items():
            conn.execute("UPDATE ade_workspaces SET storage_gb = ? WHERE user_id = ?",
                         (sizes.get(volume_id, 0.0) + (ROOTFS_GB if machine_id in live else 0.0), user_id))
    spend = spending(s, now)
    spend["stopped"] = []
    if started and spend["spent_usd"] + len(started) * s.usd_per_hour * STOP_AHEAD / 3600 >= s.cap_usd:
        stop = started
    else:
        stop = []
        for m in started:
            user_id = owner.get(m["id"])
            if user_id is None:
                continue            # a machine no account's row names: the cap alone bounds it
            share = share_for(s, _account(user_id))
            if account_spending(s, user_id, share, now)["spent_usd"] + s.usd_per_hour * STOP_AHEAD / 3600 >= share:
                stop.append(m)
    for m in stop:
        try:
            api.call("POST", f"/machines/{m['id']}/stop")
            spend["stopped"].append(m["id"])
        except FlyError as e:
            print(f"workspace spending: could not stop {m['id']}: {e}", file=sys.stderr)
    try:
        sweep_left(s, now)
    except Exception as e:  # noqa: BLE001  a removal that fails is tried again at the next reading
        print(f"workspace removal: {type(e).__name__}: {e}", file=sys.stderr)
    return spend


def _room_to_start(s, api, machine_id=None, user_id=None, share=None):
    """Refuse to start a machine when the account's spending plus START_AHEAD of its machine would pass its share, or
    the month's spending plus START_AHEAD of every machine then running would pass the cap."""
    spend = spending(s)
    running = [m for m in _live(api.call("GET", "/machines"))
               if m.get("state") == "started" and m.get("id") != machine_id]
    if spend["spent_usd"] + (len(running) + 1) * s.usd_per_hour * START_AHEAD / 3600 > s.cap_usd:
        raise WorkspaceAccess(403, f"This month's workspace spending, ${spend['spent_usd']:.2f} of the "
                                   f"${s.cap_usd:.2f} cap, leaves no room to start a machine.")
    if user_id is not None:
        share = s.share_usd if share is None else share
        mine = account_spending(s, user_id, share)
        if mine["spent_usd"] + s.usd_per_hour * START_AHEAD / 3600 > share:
            raise WorkspaceAccess(403, f"Your workspace's spending this month, ${mine['spent_usd']:.2f} of your "
                                       f"${share:.2f} share of this deployment's monthly cap, leaves no room for "
                                       f"another hour of your machine.")


# ---- removing a workspace left behind -------------------------------------------------------------------------------

KEPT_WHY = ("It was kept {kept} from {since} (UTC), while your work lived elsewhere; then its machine and its volume "
            "were destroyed.")
GOING = ("destroyed", "destroying", "pending_destroy")     # the states of a volume Fly is destroying or has destroyed
DELETED_WHY = "You deleted it from Where your work lives, with a confirmation made for that."


def _destroy(api, kind, ident, query=None):
    """DELETE one machine or volume at Fly; one Fly no longer has counts as destroyed."""
    try:
        api.call("DELETE", f"/{kind}/{ident}", query=query)
    except FlyError as e:
        if e.status != 404:
            raise


def _day(t):
    """A day and its time in a sentence: 29 November 2026, 09:00."""
    d = datetime.datetime.fromtimestamp(t, datetime.timezone.utc)
    return f"{d.day} {d.strftime('%B %Y')}, {d.strftime('%H:%M')}"


def _provider():
    """This process's fly provider, whose lock for an account a start holds too, so no start adopts a machine or volume
    as it is destroyed."""
    from . import workspaces
    p = workspaces.provider()
    return p if isinstance(p, FlyProvider) else FlyProvider()


def remove_workspace(s, user_id, why, now=None, left_by=None):
    """Destroy the account's machine and its volume at Fly (its row's, and any made for it that its row does not name),
    delete its ade_workspaces row and record that it did, in ade_workspace_removals; what this app keeps of the
    account's agents, tasks, controller and finishes is not touched. Only while the account's choice is not hosted,
    read under the account's lock, and only a row of this deployment's app: one in another app is there, where this
    app's calls never reach it; with `left_by`, only when it left the hosted choice at or before that time. A volume
    Fly lists as being destroyed is not destroyed again. FlyError when Fly does not destroy them, the row and the record
    left as they were. Returns the record, or None when nothing was removed."""
    now = int(now if now is not None else clock())
    p = _provider()
    with p._user_lock(user_id):
        row = _row(user_id)
        with store.connect() as conn:
            chose = conn.execute("SELECT choice FROM ade_storage_choice WHERE user_id = ?", (user_id,)).fetchone()
        if row is None or row["app"] != s.app or chose is None or chose["choice"] == "hosted":
            return None
        if left_by is not None and (row.get("left_at") is None or row["left_at"] > left_by):
            return None
        api = Machines(s)
        label = account_label(user_id)
        machines = {m.get("id") for m in _live(api.call("GET", "/machines"))
                    if _dict(_dict(m.get("config")).get("metadata")).get("proofpane_workspace") == label}
        listed = [v for v in api.call("GET", "/volumes") or [] if isinstance(v, dict)]
        going = {v.get("id") for v in listed if v.get("state") in GOING}
        volumes = {v.get("id") for v in listed if v.get("name") == volume_name(label)}
        for mid in sorted((machines | {row.get("machine_id")}) - {None, ""}):
            if _MACHINE.match(str(mid)):
                _destroy(api, "machines", mid, {"force": "true"})
        for vid in sorted((volumes | {row.get("volume_id")}) - {None, ""} - going):
            if _VOLUME.match(str(vid)):
                _destroy(api, "volumes", vid)
        with store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM ade_workspaces WHERE user_id = ?", (user_id,))
            conn.execute("INSERT INTO ade_workspace_removals (user_id, app, machine_id, volume_id, why, removed_at) "
                         "VALUES (?, ?, ?, ?, ?, ?)",
                         (user_id, row["app"], row.get("machine_id"), row.get("volume_id"), why, now))
        p._states.pop(user_id, None)
    return {"removed_at": now, "why": why}


def sweep_left(s, now=None):
    """Remove each workspace of this app whose account left the hosted choice OPEN_EDITION_WORKSPACE_KEEP_DAYS days
    ago and has not chosen it again, the longest kept first; one that left before the time was written
    (ade_workspaces.left_at) has it written now, so its days start at this reading. A removal that fails is said and
    tried again at the next reading, and holds up none of the others. Returns the accounts it removed."""
    now = now if now is not None else clock()
    with store.connect() as conn:
        rows = conn.execute("SELECT w.user_id, w.left_at FROM ade_workspaces w JOIN ade_storage_choice c ON "
                            "c.user_id = w.user_id WHERE w.app = ? AND c.choice <> 'hosted' "
                            "ORDER BY w.left_at, w.user_id", (s.app,)).fetchall()
        for r in rows:
            if r["left_at"] is None:
                conn.execute("UPDATE ade_workspaces SET left_at = ? WHERE user_id = ? AND left_at IS NULL",
                             (int(now), r["user_id"]))
    removed = []
    by = now - s.keep_days * 86400
    for r in rows:
        if r["left_at"] is None or r["left_at"] > by:
            continue
        days = int((now - r["left_at"]) // 86400)
        why = KEPT_WHY.format(kept=f"{days} days" if days != 1 else "1 day", since=_day(r["left_at"]))
        try:
            if remove_workspace(s, r["user_id"], why, now, left_by=by) is not None:
                removed.append(r["user_id"])
        except Exception as e:  # noqa: BLE001  tried again at the next reading; the others go on
            print(f"workspace removal: {r['user_id']}: {type(e).__name__}: {e}", file=sys.stderr)
    return removed


def start_meter(every=METER_EVERY):
    """The reading every `every` seconds, in a thread of this process (one process, so one meter)."""
    def loop():
        while True:
            time.sleep(every)
            try:
                meter_tick(settings())
            except Exception as e:  # noqa: BLE001  the meter never takes the app down
                print(f"workspace spending: a reading failed: {type(e).__name__}: {e}", file=sys.stderr)

    thread = threading.Thread(target=loop, name="ade-workspace-meter", daemon=True)
    thread.start()
    return thread


# ---- the provider ---------------------------------------------------------------------------------------------------

class FlyProvider:
    name = "fly"

    def __init__(self):
        self._lock = threading.Lock()
        self._user_locks = {}
        self._states = {}
        self._contexts = {}

    def _user_lock(self, user_id):
        with self._lock:
            return self._user_locks.setdefault(user_id, threading.Lock())

    def check_user(self, user):
        if not settings().everyone and not user.get("admin"):
            raise WorkspaceAccess(403, "On this deployment a workspace is for admins only, for now.")

    def month_hours(self, user, now=None):
        """The hours this user's machine ran this month, as the meter measured them: what an agent's schedules and
        hooks are held to (triggers.py)."""
        mid = self._ids(_row(user["id"]), settings())
        if mid is None:
            return 0.0
        with store.connect() as conn:
            row = conn.execute("SELECT seconds FROM ade_workspace_spend WHERE month = ? AND machine_id = ?",
                               (_month(now if now is not None else clock())[0], mid)).fetchone()
        return (row[0] if row else 0) / 3600

    def _ids(self, row, s):
        if row is None or row["app"] != s.app:
            return None
        mid = row.get("machine_id")
        return mid if isinstance(mid, str) and _MACHINE.match(mid) else None

    def _state(self, user_id, api, mid, fresh=False):
        seen = self._states.get(user_id)
        if not fresh and seen and seen[1] == mid and time.monotonic() - seen[0] < STATE_TTL:
            return seen[2]
        machine = api.get("machines", mid)
        state = (machine or {}).get("state") or "missing"
        self._states[user_id] = (time.monotonic(), mid, state)
        return state

    def _endpoint(self, user_id, row, s):
        secret = unseal(row.get("secret_box"), user_id)
        mid = self._ids(row, s)
        if secret is None or not row.get("cert") or mid is None:
            return None
        crt, key = client_identity()
        cache = (user_id, mid, row["cert"], crt)
        ctx = self._contexts.get(cache)
        if ctx is None:
            ctx = tls_context(row["cert"], crt, key)
            self._contexts = {k: v for k, v in self._contexts.items() if k[0] != user_id}
            self._contexts[cache] = ctx
        host, port = address(mid, s.app)
        return Endpoint(f"https://{host}:{port}", f"wss://{host}:{port}", secret, ctx)

    def holding(self, user):
        """The lock a start holds for the account (ensure), which a move away from the hosted choice holds too while it
        stops the workspace and keeps its choice (storage.choose), so that neither runs into the other."""
        return self._user_lock(user["id"])

    def running_now(self, user):
        """Whether the account's machine is started, as this process read it in the last STATE_TTL seconds, else as Fly
        says now: what a move away from the hosted choice names (storage._what_runs), without asking Fly at every read
        of the page."""
        s = settings()
        mid = self._ids(_row(user["id"]), s)
        if mid is None:
            return False
        try:
            return self._state(user["id"], Machines(s), mid) == "started"
        except FlyError as e:
            raise WorkspaceAccess(502, f"Fly: {e}") from None

    def share(self, user):
        """The account's month against its share of the cap: {month, spent_usd, share_usd}."""
        s = settings()
        return account_spending(s, user["id"], share_for(s, user))

    def status(self, user):
        s = settings()
        row = _row(user["id"])
        mid = self._ids(row, s)
        mine = {"share": self.share(user)}
        if mid is None:
            return dict({"provider": self.name, "running": False, "made": False, "state": "not made yet"}, **mine,
                        **({"spending": spending(s)} if user.get("admin") else {}))
        try:
            state = self._state(user["id"], Machines(s), mid, fresh=True)
        except FlyError as e:
            raise WorkspaceAccess(502, f"Fly: {e}") from None
        return dict({"provider": self.name, "running": state == "started", "made": True, "state": state,
                     "region": row["region"]}, **mine, **({"spending": spending(s)} if user.get("admin") else {}))

    def endpoint(self, user):
        s = settings()
        row = _row(user["id"])
        mid = self._ids(row, s)
        if mid is None:
            return None
        try:
            if self._state(user["id"], Machines(s), mid) != "started":
                return None
        except FlyError as e:
            raise WorkspaceAccess(502, f"Fly: {e}") from None
        return self._endpoint(user["id"], row, s)

    def touch(self, user):
        pass            # the agent in the machine keeps its own idle time

    def ensure(self, user):
        from . import storage
        storage.may_start(user)         # no machine for an account whose work lives elsewhere, as it chose (storage.py)
        s = settings()
        uid = user["id"]
        with self._user_lock(uid):
            storage.may_start(user)     # again, under the lock a move away from hosted holds (storage.choose)
            try:
                return self._ensure(uid, s, share_for(s, user))
            except FlyError as e:
                raise WorkspaceAccess(502, f"Fly: {e}") from None

    def _ensure(self, uid, s, share):
        row = _row(uid) or _reserve(uid, s)
        if row["app"] != s.app:
            raise WorkspaceAccess(409, f"This account's workspace is in the Fly app {row['app']}, not "
                                       f"{s.app}; it is not moved.")
        api = Machines(s)
        label = account_label(uid)
        if not (row.get("volume_id") and _VOLUME.match(row["volume_id"])):
            vol = next((v for v in api.call("GET", "/volumes") or []
                        if v.get("name") == volume_name(label) and v.get("state") not in ("destroyed", "destroying",
                                                                                            "pending_destroy")), None)
            if vol is None:
                vol = api.call("POST", "/volumes", {"name": volume_name(label), "region": row["region"],
                                                   "size_gb": s.volume_gb, "encrypted": True})
            if not _VOLUME.match(str(vol.get("id"))):
                raise FlyError(502, "Fly's machine service gave a volume no id.")
            row = _save(uid, volume_id=vol["id"])
        mid = self._ids(row, s)
        machine = api.get("machines", mid) if mid else None
        if machine is None or machine.get("state") in ("destroyed", "destroying"):
            # one made by a start that stopped before it was written down here
            machine = next((m for m in api.call("GET", "/machines") or []
                            if _dict(_dict(m.get("config")).get("metadata")).get("proofpane_workspace") == label
                            and m.get("state") not in ("destroyed", "destroying")), None)
        crt, _ = client_identity()
        with open(crt, encoding="ascii") as fh:
            client_pem = fh.read()
        if machine is None:
            _room_to_start(s, api, user_id=uid, share=share)
            stuff = material(s.app)
            # a token a machine that is gone was given is no one's now: revoked, and the row names none
            if row.get("hook_token_id"):
                approvals.revoke_token(uid, row["hook_token_id"])
            row = _save(uid, machine_id=None, cert=stuff["cert"], secret_box=seal(stuff["secret"], uid),
                        image=s.image, hook_token_id=None)
            # the sessions' approval hook's token, made once the secret is kept, so no failure before leaves one live
            token_id, held = hook_material(uid)
            stuff.update(held)
            body = {"name": machine_name(label), "region": row["region"],
                    "config": machine_config(s, row["volume_id"], stuff, client_pem, label)}
            machine, row = _with_hook_token(uid, row, token_id, held["ade_token"],
                                            lambda: api.call("POST", "/machines", body))
        else:
            state = machine.get("state")
            cfg = _dict(machine.get("config"))
            # must: without new material the app cannot reach this machine; stale: it runs an older image or size, or
            # its sessions' hook token is no longer live (revoked, or none yet), which a stopped machine takes anew
            must = (unseal(row.get("secret_box"), uid) is None or not row.get("cert") or expiring(row["cert"])
                    or row.get("machine_id") != machine.get("id")
                    or _dict(cfg.get("metadata")).get("proofpane_client") != fingerprint(client_pem))
            stale = must or outdated(cfg, s) or not hook_token_live(uid, row.get("hook_token_id"))
            if state == "stopping":
                api.call("GET", f"/machines/{machine['id']}/wait",
                         query={"state": "stopped", "instance_id": machine.get("instance_id", ""), "timeout": 30})
                state = "stopped"
            if state in STOPPED:
                _room_to_start(s, api, machine.get("id"), user_id=uid, share=share)
            if (state == "started" and must) or (state in STOPPED and stale):
                # a running machine restarts with its new configuration; a stopped one takes it and stays stopped
                # (Fly leaves it so: a stopped workspace updated to a new image never
                # started, and the start waited for nothing), so it is started once the update has settled
                stuff = material(s.app)
                row = _save(uid, cert=stuff["cert"], secret_box=seal(stuff["secret"], uid), image=s.image)
                token_id, held = hook_material(uid)
                stuff.update(held)
                body, mid = {"config": machine_config(s, row["volume_id"], stuff, client_pem, label)}, machine["id"]
                machine, row = _with_hook_token(uid, row, token_id, held["ade_token"],
                                                lambda: api.call("POST", f"/machines/{mid}", body),
                                                lambda: api.get("machines", mid))
                if state in STOPPED:
                    start_when_settled(api, machine.get("id"))
            elif state in STOPPED:
                api.call("POST", f"/machines/{machine['id']}/start")
        if not _MACHINE.match(str(machine.get("id"))):
            raise FlyError(502, "Fly's machine service gave a machine no id.")
        row = _save(uid, machine_id=machine["id"])
        try:
            api.call("GET", f"/machines/{machine['id']}/wait", query={"state": "started", "timeout": START_WAIT},
                     timeout=START_WAIT + 10)
        except FlyError as e:
            if e.status in (408, 504):
                raise WorkspaceAccess(504, "The workspace machine did not start in time.") from None
            raise
        self._states[uid] = (time.monotonic(), machine["id"], "started")
        endpoint = self._endpoint(uid, row, s)
        wait_healthy(endpoint)
        return endpoint

    def stop(self, user):
        s = settings()
        mid = self._ids(_row(user["id"]), s)
        if mid is not None:
            try:
                Machines(s).call("POST", f"/machines/{mid}/stop")
            except FlyError as e:
                if e.status not in (404, 409, 412):
                    raise WorkspaceAccess(502, f"Fly: {e}") from None
            self._states.pop(user["id"], None)
        return {"provider": self.name, "running": False}

    def shutdown(self):
        pass            # the machines are not this process's children: they stop by themselves when idle
