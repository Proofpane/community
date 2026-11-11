"""The FIDO Metadata Service (MDS3): what the FIDO Alliance publishes of each certified authenticator model, which
hardware_keys.py reads to judge whether a passkey is a hardware key.

- The blob is a JWT at OPEN_EDITION_MDS_URL (default https://mds3.fidoalliance.org/). It is trusted only when its
  x5c chain verifies, as a TLS server certificate for mds.fidoalliance.org (or mds3.fidoalliance.org), up to the FIDO
  root bundled below (GlobalSign Root CA - R3), and its signature (RS256 or ES256) verifies with the key of that
  chain's first certificate. The chain's signatures, dates and constraints are checked; no revocation list is fetched.
  The signature is checked before anything in the blob's body is read.
- It is kept on disk as it came, under the data directory (fido-mds3.jwt), and verified again whenever that file is
  read. It is used through the day its nextUpdate names (UTC), or until a certificate of its chain ends if that comes
  first, and fetched again from the start of that day. A blob that does not verify, or whose time has passed, is
  never used: without a valid blob, no key counts as hardware. The highest serial number (no) accepted, with that
  blob's sha256, is kept in the database (fido_mds_accepted), from each blob fetched and kept and each kept file read,
  so a fetched blob replaces the kept one only when its serial number is higher, or it is that same blob again: losing
  the kept file, or its becoming unreadable, lets no older blob in.
- Fetching runs in a thread of its own (start), and no request waits for it: a passkey added while there is no valid
  blob is kept to be judged again once there is one, and wakes the thread (nudge). The thread fetches only once a
  blob is wanted (a request asked for one, a passkey waits to be judged, or a blob is kept already), so a server where
  nobody adds an attested passkey never asks for one. With OPEN_EDITION_MDS_URL empty nothing is fetched, and a blob
  put in the data directory by hand is still verified before it is used.
"""

import base64
import dataclasses
import datetime
import hashlib
import http.client
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse
import urllib.request

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils
from cryptography.x509 import verification

from . import config, store

FILE = "fido-mds3.jwt"
MAX_BLOB = 64 * 1024 * 1024             # the blob was a few megabytes in 2026
FETCH_TIMEOUT = 60                      # seconds for each read of the fetch
FETCH_DEADLINE = 300                    # seconds for the whole fetch
CHECK_EVERY = 3600
RETRY_AFTER = 600
MAX_CHAIN = 5
SIGNER_NAMES = ("mds.fidoalliance.org", "mds3.fidoalliance.org")
ALGORITHMS = ("RS256", "ES256")
_B64URL = re.compile(r"[A-Za-z0-9_-]*")                      # both: fullmatch, never match
_AAGUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

# GlobalSign Root CA - R3, the root the FIDO Alliance names for the blob's chain; its SHA-256 fingerprint is
# cbb522d7b7f127ad6a0113865bdf1cd4102e7d0759af635a7cf4720dc963c53b (tests/test_fido_mds.py checks it). It expires on
# 2029-03-18.
ROOT_PEM = b"""-----BEGIN CERTIFICATE-----
MIIDXzCCAkegAwIBAgILBAAAAAABIVhTCKIwDQYJKoZIhvcNAQELBQAwTDEgMB4G
A1UECxMXR2xvYmFsU2lnbiBSb290IENBIC0gUjMxEzARBgNVBAoTCkdsb2JhbFNp
Z24xEzARBgNVBAMTCkdsb2JhbFNpZ24wHhcNMDkwMzE4MTAwMDAwWhcNMjkwMzE4
MTAwMDAwWjBMMSAwHgYDVQQLExdHbG9iYWxTaWduIFJvb3QgQ0EgLSBSMzETMBEG
A1UEChMKR2xvYmFsU2lnbjETMBEGA1UEAxMKR2xvYmFsU2lnbjCCASIwDQYJKoZI
hvcNAQEBBQADggEPADCCAQoCggEBAMwldpB5BngiFvXAg7aEyiie/QV2EcWtiHL8
RgJDx7KKnQRfJMsuS+FggkbhUqsMgUdwbN1k0ev1LKMPgj0MK66X17YUhhB5uzsT
gHeMCOFJ0mpiLx9e+pZo34knlTifBtc+ycsmWQ1z3rDI6SYOgxXG71uL0gRgykmm
KPZpO/bLyCiR5Z2KYVc3rHQU3HTgOu5yLy6c+9C7v/U9AOEGM+iCK65TpjoWc4zd
QQ4gOsC0p6Hpsk+QLjJg6VfLuQSSaGjlOCZgdbKfd/+RFO+uIEn8rUAVSNECMWEZ
XriX7613t2Saer9fwRPvm2L7DWzgVGkWqQPabumDk3F2xmmFghcCAwEAAaNCMEAw
DgYDVR0PAQH/BAQDAgEGMA8GA1UdEwEB/wQFMAMBAf8wHQYDVR0OBBYEFI/wS3+o
LkUkrk1Q+mOai97i3Ru8MA0GCSqGSIb3DQEBCwUAA4IBAQBLQNvAUKr+yAzv95ZU
RUm7lgAJQayzE4aGKAczymvmdLm6AC2upArT9fHxD4q/c2dKg8dEe3jgr25sbwMp
jjM5RcOO5LlXbKr8EpbsU8Yt5CRsuZRj+9xTaGdWPoO4zzUhw8lo/s7awlOqzJCK
6fBdRoyV3XpYKBovHd7NADdBj+1EbddTKJd+82cEHhXXipa0095MJ6RMG3NzdvQX
mcIfeg7jLQitChws/zyrVQ4PkX4268NXSb7hLi18YIvDQVETI53O9zJrlAGomecs
Mx86OyXShkDOOyyGeMlhLxS67ttVb9+E7gUJTb0o2HLO02JQZR7rkpeDMdmztcpH
WD9f
-----END CERTIFICATE-----
"""


class BlobError(ValueError):
    """A blob that cannot be had or is not one to trust, with why."""


@dataclasses.dataclass(frozen=True)
class Blob:
    """A verified blob: its serial number, its nextUpdate, when to fetch the next (the start of that day, UTC), until
    when it is used (the end of that day, or the end of its chain's first certificate to end), and its entries by
    AAGUID (an AAGUID it lists twice maps to None)."""
    no: int
    next_update: str
    refresh_at: int
    expires_at: int
    entries: dict

    def valid(self, now):
        return now < self.expires_at


def _now(now):
    return int(now if now is not None else time.time())


def _moment(now):
    return datetime.datetime.fromtimestamp(now, datetime.timezone.utc)


def root():
    """The trusted root, from ROOT_PEM (a test puts a root of its own there)."""
    return x509.load_pem_x509_certificate(ROOT_PEM)


def path():
    return os.path.join(config.data_dir(), FILE)


def _b64url(text, what):
    if not _B64URL.fullmatch(text) or len(text) % 4 == 1:
        raise BlobError(f"its {what} is not base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _no_constant(name):
    raise ValueError(f"{name} is not a number JSON has")


def _json(raw, what):
    try:
        return json.loads(raw.decode("utf-8"), parse_constant=_no_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise BlobError(f"its {what} is not JSON") from None


def _check_chain(certs, now):
    """The chain verifies up to the trusted root as a TLS server certificate of one of SIGNER_NAMES."""
    trusted = verification.Store([root()])
    for name in SIGNER_NAMES:
        try:
            verification.PolicyBuilder().store(trusted).time(_moment(now)).build_server_verifier(
                verification.DNSName(name)).verify(certs[0], certs[1:])
            return
        except Exception:  # noqa: BLE001  a chain that does not verify, or cannot be read, is not trusted
            continue
    raise BlobError("its certificate chain does not verify up to the FIDO root, for mds.fidoalliance.org")


def _check_signature(alg, cert, signed, signature):
    try:
        key = cert.public_key()
    except Exception:  # noqa: BLE001  a key this library cannot read signs nothing
        raise BlobError("its signing key cannot be read") from None
    if alg == "RS256" and (not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048):
        raise BlobError("its signing key is not RSA of at least 2048 bits, as RS256 needs")
    if alg == "ES256" and (not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1)
                           or len(signature) != 64):
        raise BlobError("its signing key or signature is not P-256, as ES256 needs")
    try:
        if alg == "RS256":
            key.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
        else:
            der = utils.encode_dss_signature(int.from_bytes(signature[:32], "big"),
                                             int.from_bytes(signature[32:], "big"))
            key.verify(der, signed, ec.ECDSA(hashes.SHA256()))
    except Exception:  # noqa: BLE001  InvalidSignature, or a signature this library cannot read
        raise BlobError("its signature does not verify") from None


def parse(data, now=None):
    """The blob `data` holds, its chain and signature verified first, whatever its time; BlobError otherwise."""
    now = _now(now)
    if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_BLOB:
        raise BlobError("it is not a blob of a size this server takes")
    try:
        text = bytes(data).decode("ascii").strip()
    except UnicodeDecodeError:
        raise BlobError("it is not a JWT") from None
    parts = text.split(".")
    if len(parts) != 3:
        raise BlobError("it is not a JWT")
    header = _json(_b64url(parts[0], "header"), "header")
    if not isinstance(header, dict) or header.get("alg") not in ALGORITHMS:
        raise BlobError("its header names no algorithm this server takes (RS256, ES256)")
    if "crit" in header:
        raise BlobError("its header asks for extensions this server does not read (crit)")
    chain = header.get("x5c")
    if not isinstance(chain, list) or not 1 <= len(chain) <= MAX_CHAIN or not all(isinstance(c, str) for c in chain):
        raise BlobError("its header carries no certificate chain (x5c)")
    try:
        certs = [x509.load_der_x509_certificate(base64.b64decode(c, validate=True)) for c in chain]
    except Exception:  # noqa: BLE001  a version or an encoding this library does not read, among others
        raise BlobError("its certificates cannot be read") from None
    _check_chain(certs, now)
    _check_signature(header["alg"], certs[0], f"{parts[0]}.{parts[1]}".encode("ascii"),
                     _b64url(parts[2], "signature"))
    payload = _json(_b64url(parts[1], "body"), "body")
    if not isinstance(payload, dict):
        raise BlobError("its body is not an object")
    no, next_update, entries = payload.get("no"), payload.get("nextUpdate"), payload.get("entries")
    if not isinstance(no, int) or isinstance(no, bool) or no < 0:
        raise BlobError("its body has no serial number (no)")
    if not isinstance(next_update, str) or not _DATE.fullmatch(next_update):
        raise BlobError("its body has no nextUpdate date")
    try:
        day = datetime.date.fromisoformat(next_update)
    except ValueError:
        raise BlobError("its body has no nextUpdate date") from None
    if not isinstance(entries, list):
        raise BlobError("its body has no entries")
    refresh_at = int(datetime.datetime(day.year, day.month, day.day, tzinfo=datetime.timezone.utc).timestamp())
    ends = min(int(c.not_valid_after_utc.timestamp()) for c in certs)
    index = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("aaguid"), str):
            continue                # a U2F or UAF entry: no AAGUID
        aaguid = entry["aaguid"].lower()
        if _AAGUID.fullmatch(aaguid):
            index[aaguid] = None if aaguid in index else entry
    return Blob(no=no, next_update=next_update, refresh_at=refresh_at, expires_at=min(refresh_at + 86400, ends),
                entries=index)


def verify(data, now=None):
    """The blob `data` holds while it is valid: parse, and its time not passed; BlobError otherwise."""
    now = _now(now)
    blob = parse(data, now)
    if not blob.valid(now):
        raise BlobError(f"its time has passed (nextUpdate {blob.next_update})")
    return blob


# ---- the blob kept on disk -----------------------------------------------------------------------------------------

_lock = threading.Lock()
_cache = {"key": None, "blob": None, "error": None}


def _digest(data):
    return hashlib.sha256(bytes(data)).hexdigest()


def accepted():
    """(the highest serial number accepted here, that blob's sha256), or (None, None) when none was."""
    with store.connect() as db:
        row = db.execute("SELECT no, digest FROM fido_mds_accepted WHERE id = 1").fetchone()
    return (row["no"], row["digest"]) if row else (None, None)


def _accept(no, digest, now=None):
    """Record a blob as accepted when its serial number is higher than the highest recorded, or none is."""
    with store.connect() as db:
        db.execute("INSERT INTO fido_mds_accepted (id, no, digest, accepted_at) VALUES (1, ?, ?, ?) ON CONFLICT (id) "
                   "DO UPDATE SET no = excluded.no, digest = excluded.digest, accepted_at = excluded.accepted_at "
                   "WHERE excluded.no > fido_mds_accepted.no", (no, digest, _now(now)))


def _read_kept():
    """The kept blob, parsed (whatever its time), or None, verified again when the file changed since it was read;
    each one read is recorded as accepted (_accept)."""
    where = path()
    try:
        stat = os.stat(where)
    except OSError:
        return None
    key = (where, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    with _lock:
        if _cache["key"] != key:
            blob = error = None
            try:
                with open(where, "rb") as fh:
                    data = fh.read(MAX_BLOB + 1)
                blob = parse(data)
                _accept(blob.no, _digest(data))
            except Exception as e:  # noqa: BLE001  whatever keeps it from being read and verified, it is no blob
                blob, error = None, f"{type(e).__name__}: {e}"
            _cache.update(key=key, blob=blob, error=error)
        return _cache["blob"]


def kept(now=None):
    """The blob kept on disk while it is valid; None when there is none, it does not verify, or its time has passed."""
    blob = _read_kept()
    return blob if blob is not None and blob.valid(_now(now)) else None


def forget():
    """Read and verify the kept blob afresh at its next use (after the trusted root changed, in a test)."""
    with _lock:
        _cache.update(key=None, blob=None, error=None)


def status(now=None):
    """What the page says of the metadata: {valid, next_update}."""
    blob = kept(now)
    return {"valid": blob is not None, "next_update": blob.next_update if blob is not None else None}


def _keep(data):
    """Write the blob's bytes in place of the kept one's, readable by this user only, whole or not at all."""
    where = path()
    tmp = f"{where}.{secrets.token_hex(4)}.tmp"
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, where)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def fetch(url, timeout=FETCH_TIMEOUT):
    """The blob's bytes from an https address, at most MAX_BLOB of them; BlobError when they cannot be had. The fetch
    is given up once FETCH_DEADLINE seconds have passed: each read takes what has arrived (read1) and waits at most
    `timeout` seconds, so a sender that trickles cannot hold it longer. Tests put a fetcher of their own here: none
    reaches the network."""
    if urllib.parse.urlsplit(url).scheme != "https":
        raise BlobError("the metadata's address is not https")
    request = urllib.request.Request(url, headers={"User-Agent": "proofpane-community"})
    deadline = time.monotonic() + FETCH_DEADLINE
    chunks, size = [], 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            while True:
                chunk = answer.read1(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BLOB:
                    raise BlobError("the blob is larger than this server takes")
                if time.monotonic() > deadline:
                    raise BlobError("the fetch took too long")
                chunks.append(chunk)
    except BlobError:
        raise
    except (OSError, ValueError, http.client.HTTPException) as e:
        raise BlobError(f"it could not be fetched ({type(e).__name__})") from None
    return b"".join(chunks)


def refresh(now=None):
    """Fetch the blob, verify it, and keep it when its serial number is higher than the highest accepted here (in the
    database, and the kept file's), or it is the blob accepted with that number again. Returns it; BlobError says why
    when there is none."""
    now = _now(now)
    where = config.mds_url()
    if not where:
        raise BlobError("no address is set for the metadata (OPEN_EDITION_MDS_URL)")
    data = fetch(where)
    blob = verify(data, now)
    held = _read_kept()                     # recorded as accepted, if it was not
    if held is not None and blob.no < held.no:
        raise BlobError(f"its serial number ({blob.no}) is lower than the kept blob's ({held.no})")
    highest, digest = accepted()
    if highest is not None and blob.no < highest:
        raise BlobError(f"its serial number ({blob.no}) is lower than the highest accepted here ({highest})")
    if blob.no == highest and _digest(data) != digest:
        raise BlobError(f"it is not the blob accepted here with its serial number ({blob.no})")
    _keep(data)
    _accept(blob.no, _digest(data), now)
    return blob


# ---- the refresher -------------------------------------------------------------------------------------------------

_wake = threading.Event()
_stop = threading.Event()
_state = {"thread": None, "wanted": False, "next_try": 0, "handed": None}


def running():
    thread = _state["thread"]
    return thread is not None and thread.is_alive()


def tick(on_blob, waiting, now=None):
    """One round of the refresher: fetch when a blob is wanted and none valid is kept, or the kept one's nextUpdate
    has come; then, with a valid blob, on_blob(now) when waiting() says passkeys wait to be judged, or the blob is one
    this process has not handed it (a serial number new to it: on_blob reconsiders the hardware keys against it).
    Returns the seconds until the next round."""
    now = _now(now)
    blob = kept(now)
    pending = bool(waiting())
    due = blob is None or now >= blob.refresh_at
    wanted = _state["wanted"] or pending or os.path.exists(path())
    delay = CHECK_EVERY
    if due and wanted and config.mds_url() and now >= _state["next_try"]:
        try:
            blob = refresh(now)
            _state["wanted"] = False
            pending = pending or bool(waiting())        # a passkey stored while the fetch ran waits for it too
        except Exception as e:  # noqa: BLE001  no blob, or one not kept (a full disk): tried again after a while
            _state["next_try"] = now + RETRY_AFTER
            delay = RETRY_AFTER
            print(f"fido metadata: none fetched: {type(e).__name__}: {e}", file=sys.stderr)
    if blob is not None:
        if pending or blob.no != _state["handed"]:
            on_blob(now)
            _state["handed"] = blob.no      # after it returns: one that failed is handed it again next round
        if now < blob.refresh_at:
            delay = min(delay, max(60, blob.refresh_at - now))
    return delay


def nudge():
    """Ask the refresher for a round now: a passkey was stored to be judged once a valid blob is there."""
    _state["wanted"] = True
    _wake.set()


def start(on_blob, waiting):
    """Run the refresher in a thread of this process (the app runs one process, so there is one). None when no
    address is set."""
    if not config.mds_url():
        return None
    if running():
        return _state["thread"]
    _stop.clear()

    def loop():
        while not _stop.is_set():
            try:
                delay = tick(on_blob, waiting)
            except Exception as e:  # noqa: BLE001  the refresher never takes the app down
                print(f"fido metadata: a round failed: {type(e).__name__}: {e}", file=sys.stderr)
                delay = RETRY_AFTER
            _wake.wait(delay)
            _wake.clear()

    thread = threading.Thread(target=loop, name="fido-mds", daemon=True)
    _state["thread"] = thread
    thread.start()
    return thread


def stop():
    """End the refresher's thread (its round, if one runs, first)."""
    thread = _state["thread"]
    _stop.set()
    _wake.set()
    if thread is not None:
        thread.join(FETCH_TIMEOUT)
    _state.update(thread=None, wanted=False, next_try=0, handed=None)
