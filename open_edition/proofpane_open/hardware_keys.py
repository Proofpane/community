"""Whether a new passkey is a hardware key, judged from its attestation and the FIDO metadata (fido_mds.py). A
hardware key here is one for which all four hold:

1. It verified its user (a PIN) as it was made: the UV flag of its authenticator data.
2. It is certified, with a genuine attestation: a packed attestation with an x5c chain whose leaf certificate meets
   WebAuthn's rules for one (8.2.1: version 3, a maker's name with the unit "Authenticator Attestation", basic
   constraints that say it is not a CA, a key usage, where it has one, that lets it sign) and carries the
   authenticator's AAGUID (extension 1.3.6.1.4.1.45724.1.1.4, not critical, the same as its authenticator data's);
   whose signature over the authenticator data and the client data's sha256 verifies with that certificate's key;
   and whose chain verifies up to one of the roots the FIDO metadata lists for that AAGUID, every certificate that
   issues another saying it is a CA, within its path length. The metadata's latest status report for it is a FIDO
   certification, and none of its reports says it is revoked or compromised.
3. It can derive a key: the metadata lists hmac-secret among its extensions (which WebAuthn's PRF needs).
4. It works with a phone: NFC or USB among the transports the metadata lists for its model; only when it lists none,
   among those the browser reported (getTransports), and the judgment says it rests on the browser's word then.

Whatever else holds, a key whose authenticator data sets the backup-eligible flag (BE), as a synced passkey's does, or
the backup-state flag (BS), is a device passkey: its credential may be copied off it. Anything else is a device passkey,
with one line saying why: the first check that does not hold. A key that meets what its attestation alone shows while
there is no valid blob is a device passkey until it is judged again, once one is there (passkeys.judge_again). The
judgment is made once; a report of a later blob that the model is revoked or compromised (reported) makes a hardware key
a device passkey, never the other way (passkeys.reconsider). A certificate this module cannot read, for any reason, is
a reason for a device passkey, never an error.
"""

import base64
import datetime
import hashlib
import re
import typing
import unicodedata
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.x509 import verification
from cryptography.x509.oid import NameOID

HARDWARE, DEVICE = "hardware", "device"
AAGUID_OID = x509.ObjectIdentifier("1.3.6.1.4.1.45724.1.1.4")
ATTESTATION_UNIT = "Authenticator Attestation"
CERTIFIED = frozenset({"FIDO_CERTIFIED", "FIDO_CERTIFIED_L1", "FIDO_CERTIFIED_L1plus", "FIDO_CERTIFIED_L2",
                       "FIDO_CERTIFIED_L2plus", "FIDO_CERTIFIED_L3", "FIDO_CERTIFIED_L3plus"})
UNTRUSTED = {"REVOKED": "revoked", "ATTESTATION_KEY_COMPROMISE": "with its attestation key compromised",
             "USER_VERIFICATION_BYPASS": "with a way around its PIN",
             "USER_KEY_REMOTE_COMPROMISE": "with its keys open to remote attack",
             "USER_KEY_PHYSICAL_COMPROMISE": "with its keys open to physical attack"}
PHONE_TRANSPORTS = frozenset({"nfc", "usb"})
MAX_CHAIN = 5
MAX_MAKER = 120
NO_BLOB = "This server has no valid copy of the FIDO metadata yet: the key is judged again once it has one."
# why a key is a device passkey where its attestation does not chain to a root the metadata lists for the model it
# claims, which keeps the maker its attestation named but did not prove
UNCHAINED = "Its attestation certificate does not chain to a root the FIDO metadata lists for its model."
# the reports after which a model's attestation proves nothing of the model either: one revoked (a fraudulent product
# or a backdoor) or one whose attestation key is compromised, with which anyone can attest to be that model. The
# others report a weakness of a model its attestation did prove. Where a model has both, these decide its words.
DISPROVEN = ("REVOKED", "ATTESTATION_KEY_COMPROMISE")


def status_words(status):
    """Why a key whose model the metadata reports untrusted (UNTRUSTED's `status`) is no hardware key, at its judgment."""
    return f"The FIDO metadata reports its model {UNTRUSTED[status]}."


def reported_words(status):
    """Why a hardware key whose model the metadata has reported since (UNTRUSTED's `status`) is no longer one."""
    return f"The FIDO metadata has since reported its model {UNTRUSTED[status]}."


# the judgments that keep a maker the attestation did not prove, or that a report since says it proves nothing of:
# passkeys.model_of passes over each, so no passkey is named, or shown, as a model nothing showed it to be
UNPROVEN = frozenset({UNCHAINED} | {status_words(s) for s in DISPROVEN} | {reported_words(s) for s in DISPROVEN})


def _first_untrusted(statuses):
    """Of the untrusted statuses reported (in the metadata's order), the one whose words a key gets: the first that
    says its attestation proves nothing (DISPROVEN), else the first; None for none."""
    untrusted = [s for s in statuses if s in UNTRUSTED]
    return next((s for s in untrusted if s in DISPROVEN), untrusted[0] if untrusted else None)
_FMT = re.compile(r"[a-z0-9-]{1,32}")                        # both: fullmatch, never match
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class Judgment(typing.NamedTuple):
    """A passkey's tier, its model's AAGUID (None when it gave none), its maker's description from the FIDO metadata
    (None when the metadata does not name it), why it is a device passkey (None for a hardware key), whether it is to
    be judged again once a valid blob is there, and, for a hardware key, whether its working with a phone rests on the
    browser's word (the metadata lists no transports for its model) and the sha256 of each certificate of the chain its
    attestation verified by, its leaf first and its anchor last, which a later status report about one certificate is
    matched against (reported)."""
    tier: str
    aaguid: typing.Optional[str]
    maker: typing.Optional[str]
    why: typing.Optional[str]
    again: bool = False
    by_browser: bool = False
    certs: tuple = ()


def device(why, aaguid=None, maker=None, again=False):
    return Judgment(DEVICE, aaguid, maker, why, again)


def _signature_ok(alg, cert, signature, signed):
    """Whether `signature` over `signed` verifies with the key of `cert` under the COSE algorithm `alg` (ES256, RS256
    of at least 2048 bits, EdDSA), the key of the kind the algorithm names."""
    try:
        key = cert.public_key()
        if alg == -7 and isinstance(key, ec.EllipticCurvePublicKey) and isinstance(key.curve, ec.SECP256R1):
            key.verify(signature, signed, ec.ECDSA(hashes.SHA256()))
        elif alg == -257 and isinstance(key, rsa.RSAPublicKey) and key.key_size >= 2048:
            key.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
        elif alg == -8 and isinstance(key, Ed25519PublicKey):
            key.verify(signature, signed)
        else:
            return False
    except Exception:  # noqa: BLE001  a key or a signature that cannot be read verifies nothing
        return False
    return True


def _leaf_problem(leaf, aaguid_raw):
    """Why the attestation certificate is not one a certified key's packed attestation carries, or None."""
    try:
        if leaf.version != x509.Version.v3:
            return "Its attestation certificate is not of version 3, as WebAuthn asks."
        subject = leaf.subject

        def values(oid):
            return [a.value for a in subject.get_attributes_for_oid(oid)]

        if not values(NameOID.COUNTRY_NAME) or not values(NameOID.ORGANIZATION_NAME) \
                or values(NameOID.ORGANIZATIONAL_UNIT_NAME) != [ATTESTATION_UNIT] or not values(NameOID.COMMON_NAME):
            return "Its attestation certificate does not name its maker as WebAuthn asks."
        try:
            if leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
                return "Its attestation certificate is a CA's, not a key's."
        except x509.ExtensionNotFound:
            return "Its attestation certificate does not say it is no CA (basic constraints), as WebAuthn asks."
        try:
            if not leaf.extensions.get_extension_for_class(x509.KeyUsage).value.digital_signature:
                return "Its attestation certificate's key usage does not let it sign."
        except x509.ExtensionNotFound:
            pass                            # WebAuthn asks for no key usage; where there is one, it must allow this
        try:
            ext = leaf.extensions.get_extension_for_oid(AAGUID_OID)
        except x509.ExtensionNotFound:
            return "Its attestation certificate does not carry its model's id (AAGUID)."
    except Exception:  # noqa: BLE001  the client sent it: whatever fails to read in it, it is no certified key's
        return "Its attestation certificate cannot be read."
    if ext.critical:
        return "Its attestation certificate marks its model's id critical, which WebAuthn does not allow."
    value = ext.value.value if isinstance(ext.value, x509.UnrecognizedExtension) else None
    if not isinstance(value, bytes) or len(value) != 18 or value[:2] != b"\x04\x10":
        return "Its attestation certificate's model id cannot be read."
    if value[2:] != aaguid_raw:
        return "Its attestation certificate names another model than the key does."
    return None


def _cert_signing(policy, cert, key_usage):
    """A CA's key usage, where it has one, lets it sign certificates (RFC 5280, 4.2.1.3)."""
    if key_usage is not None and not key_usage.key_cert_sign:
        raise ValueError("a CA's key usage does not let it sign certificates")


def _is_ca(policy, cert, basic_constraints):
    """A certificate that issues another in the chain says it is a CA (RFC 5280, 4.2.1.9). cryptography's verifier
    checks this itself as it builds a path; this check is here so that what holds does not rest on that alone."""
    if basic_constraints is None or not basic_constraints.ca:
        raise ValueError("a certificate that says it is no CA issues no certificate")


def chain_constraints_hold(chain):
    """Whether a verified chain (the leaf first, the anchor last) keeps its CAs' constraints: each certificate above the
    leaf says it is a CA, and no more CAs come below one than its path length allows (RFC 5280, 4.2.1.9). Checked
    again after cryptography's verifier, which keeps them too, for the same reason as _is_ca."""
    for depth, cert in enumerate(chain[1:]):
        try:
            constraints = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
        except Exception:  # noqa: BLE001  none, or one that cannot be read: no CA
            return False
        if not constraints.ca or (constraints.path_length is not None and depth > constraints.path_length):
            return False
    return True


def _chains(leaf, intermediates, statement, now):
    """The chain (the leaf first, the anchor last) by which the leaf and the attestation's other certificates verify up
    to a root the metadata statement lists (a root, an intermediate or the leaf itself, as MDS allows), at `now`:
    signatures, names, dates, CA constraints; None when they do not."""
    roots = []
    listed = statement.get("attestationRootCertificates")
    for item in listed if isinstance(listed, list) else ():
        if isinstance(item, str):
            try:
                roots.append(x509.load_der_x509_certificate(base64.b64decode(item, validate=True)))
            except Exception:  # noqa: BLE001  one the server cannot read anchors nothing
                continue
    if not roots:
        return None
    ca = verification.ExtensionPolicy.permit_all().require_present(
        x509.BasicConstraints, verification.Criticality.AGNOSTIC, _is_ca).may_be_present(
        x509.KeyUsage, verification.Criticality.AGNOSTIC, _cert_signing)
    builder = verification.PolicyBuilder().store(verification.Store(roots)).time(
        datetime.datetime.fromtimestamp(now, datetime.timezone.utc)).extension_policies(
        ca_policy=ca, ee_policy=verification.ExtensionPolicy.permit_all())
    try:
        chain = builder.build_client_verifier().verify(leaf, intermediates).chain
    except Exception:  # noqa: BLE001  a chain that does not verify, or cannot be read, anchors nothing
        return None
    return chain if chain_constraints_hold(chain) else None


def _status_problem(reports):
    """Why the metadata's status reports do not make the model a certified one to trust, or None: a report that says
    it is revoked or compromised, whenever it was made; or a latest report (those of the latest date, with any that
    has none) that is not a FIDO certification."""
    if not isinstance(reports, list) or not reports:
        return "The FIDO metadata gives no status for its model."
    read = []
    for r in reports:
        date = r.get("effectiveDate") if isinstance(r, dict) else None
        if not isinstance(r, dict) or not isinstance(r.get("status"), str) or (
                date is not None and (not isinstance(date, str) or not _DATE.fullmatch(date) or not _is_date(date))):
            return "The FIDO metadata's status reports for its model cannot be read."
        read.append((r["status"], date))
    untrusted = _first_untrusted(status for status, _ in read)
    if untrusted is not None:
        return status_words(untrusted)
    dated = [d for _, d in read if d is not None]
    latest = max(dated) if dated else None
    now_reported = [s for s, d in read if d is None or d == latest]
    if not all(s in CERTIFIED for s in now_reported):
        return "The FIDO metadata does not list its model as FIDO certified now."
    return None


def _is_date(text):
    try:
        datetime.date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _strings(value):
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def maker(statement):
    """The maker's description of the model, as the metadata gives it, kept to characters that show themselves."""
    text = statement.get("description") if isinstance(statement, dict) else None
    if not isinstance(text, str):
        return None
    text = "".join(c for c in text if c == " " or unicodedata.category(c)[0] not in "CZ").strip()[:MAX_MAKER]
    return text or None


def judge(attestation, client_data_hash, transports, blob_now, now):
    """The Judgment of a new passkey from its attestation object as read (fmt, attStmt, authData), the sha256 of its
    client data, the transports the browser reported, and blob_now(): the valid metadata blob, or None, asked for only
    once the attestation alone holds."""
    data = attestation["authData"]
    aaguid_raw = data[37:53]
    aaguid = str(uuid.UUID(bytes=aaguid_raw)) if any(aaguid_raw) else None
    if not data[32] & 0x04:
        return device("It did not verify you (a PIN or a fingerprint) as it was made.", aaguid)
    if data[32] & 0x08:
        return device("It may be copied to other devices (its backup-eligible flag is set), as a synced passkey can: "
                      "it is not bound to one key.", aaguid)
    if data[32] & 0x10:                     # backed up, which registration refuses without the flag above
        return device("It says it is backed up (its backup-state flag is set): it is not bound to one key.", aaguid)
    fmt, statement = attestation["fmt"], attestation["attStmt"]
    if fmt == "none":
        return device("It gave no attestation (a browser may ask before it shares one), so nothing shows it is a "
                      "certified security key.", aaguid)
    if fmt != "packed":
        kind = fmt if _FMT.fullmatch(fmt) else "another"
        return device(f"Its attestation is of the {kind} kind, not the packed kind a certified security key gives.",
                      aaguid)
    if "x5c" not in statement:
        return device("It attested only with its own key, which no maker certified.", aaguid)
    alg, signature, chain = statement.get("alg"), statement.get("sig"), statement.get("x5c")
    if not isinstance(alg, int) or isinstance(alg, bool) or not isinstance(signature, bytes) \
            or not isinstance(chain, list) or not 1 <= len(chain) <= MAX_CHAIN \
            or not all(isinstance(c, bytes) for c in chain):
        return device("Its attestation cannot be read.", aaguid)
    try:
        certs = [x509.load_der_x509_certificate(c) for c in chain]
    except Exception:  # noqa: BLE001  a version or an encoding this library does not read, among others
        return device("Its attestation's certificates cannot be read.", aaguid)
    if not _signature_ok(alg, certs[0], signature, data + client_data_hash):
        return device("Its attestation's signature does not verify.", aaguid)
    problem = _leaf_problem(certs[0], aaguid_raw)
    if problem:
        return device(problem, aaguid)
    blob = blob_now()
    if blob is None:
        return device(NO_BLOB, aaguid, again=True)
    entry = blob.entries.get(aaguid)
    if entry is None:
        return device("The FIDO metadata lists its model twice." if aaguid in blob.entries
                      else "The FIDO metadata does not list its model.", aaguid)
    statement = entry.get("metadataStatement") if isinstance(entry.get("metadataStatement"), dict) else {}
    named = maker(statement)
    path = _chains(certs[0], certs[1:], statement, now)
    if path is None:
        return device(UNCHAINED, aaguid, named)
    problem = _status_problem(entry.get("statusReports"))
    if problem:
        return device(problem, aaguid, named)
    info = statement.get("authenticatorGetInfo") if isinstance(statement.get("authenticatorGetInfo"), dict) else {}
    if "hmac-secret" not in _strings(info.get("extensions")):
        return device("It cannot derive a key: the FIDO metadata lists no hmac-secret for its model.", aaguid, named)
    listed = {t.lower() for t in _strings(info.get("transports"))}
    # the chain it verified by, its anchor too, which a later report about one certificate is matched against
    fingerprints = tuple(hashlib.sha256(c.public_bytes(serialization.Encoding.DER)).hexdigest() for c in path)
    if listed:                              # the metadata's word decides wherever it gives one
        if not PHONE_TRANSPORTS & listed:
            return device("The FIDO metadata lists neither NFC nor USB for its model, which a phone needs.", aaguid,
                          named)
        return Judgment(HARDWARE, aaguid, named, None, certs=fingerprints)
    if not PHONE_TRANSPORTS & {t.lower() for t in transports}:
        return device("Neither the FIDO metadata nor the browser lists NFC or USB for it, which a phone needs.",
                      aaguid, named)
    return Judgment(HARDWARE, aaguid, named, None, by_browser=True, certs=fingerprints)


def reported(entry, certs):
    """Why a status report the metadata carries now for a hardware key's model makes it no hardware key, or None: a
    report that says the model is revoked or compromised, about the whole model or about a certificate of the key's
    attestation (its sha256 among `certs`). A report about a certificate that cannot be read, or one when no
    certificate of the key's attestation was kept to tell (`certs` empty), is taken to be about the key."""
    reports = entry.get("statusReports") if isinstance(entry, dict) else None
    about_it = []
    for r in reports if isinstance(reports, list) else []:
        if not isinstance(r, dict) or not isinstance(r.get("status"), str) or r["status"] not in UNTRUSTED:
            continue
        scope = r.get("certificate")
        if scope is not None and certs:
            try:
                about = hashlib.sha256(base64.b64decode(scope, validate=True)).hexdigest()
            except Exception:  # noqa: BLE001  an unreadable certificate: taken to be about this key's
                about = None
            if about is not None and about not in certs:
                continue                    # about another batch's certificate
        about_it.append(r["status"])
    status = _first_untrusted(about_it)
    return reported_words(status) if status is not None else None
