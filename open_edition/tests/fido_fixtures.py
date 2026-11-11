"""What the hardware-key tests are made of, all made here with `cryptography` and nothing fetched: a FIDO root of the
tests' own with a TLS server's chain under it for the metadata's signer (mds.fidoalliance.org), metadata blobs signed
with it (JWTs, RS256 or ES256), a maker's attestation root and batch certificate for a model of the tests' own, and
software security keys that answer navigator.credentials.create() with a packed attestation, as a certified key
does (test_passkeys.Authenticator otherwise)."""

import base64
import datetime
import hashlib
import json
import os
import secrets
import struct
import uuid

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from proofpane_open import fido_mds
from test_passkeys import Authenticator, add, b64u, cbor, unb64u

DAY = datetime.timedelta(days=1)
MODEL = uuid.UUID("2fc0579f-8113-47ea-b116-bb5a8db9202a")      # a model of the tests' own
AAGUID_OID = x509.ObjectIdentifier("1.3.6.1.4.1.45724.1.1.4")
DER = serialization.Encoding.DER


def now():
    return datetime.datetime.now(datetime.timezone.utc)


def day(offset=0):
    """A date as the metadata writes one, `offset` days from today (UTC)."""
    return (now() + offset * DAY).date().isoformat()


def name(cn, country=None, org=None, unit=None):
    attrs = [x509.NameAttribute(oid, value) for oid, value in ((NameOID.COUNTRY_NAME, country),
                                                                (NameOID.ORGANIZATION_NAME, org),
                                                                (NameOID.ORGANIZATIONAL_UNIT_NAME, unit),
                                                                (NameOID.COMMON_NAME, cn)) if value is not None]
    return x509.Name(attrs)


def key_usage(cert_sign):
    return x509.KeyUsage(digital_signature=not cert_sign, content_commitment=False, key_encipherment=False,
                         data_encipherment=False, key_agreement=False, key_cert_sign=cert_sign, crl_sign=cert_sign,
                         encipher_only=False, decipher_only=False)


def certificate(subject, key, issuer, issuer_key, ca=None, extensions=(), not_before=None, not_after=None,
                aki=True):
    """A certificate for `key` named `subject`, signed by `issuer_key` as `issuer`; `ca` adds basic constraints (True
    or False), each of `extensions` is (extension, critical)."""
    b = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(key.public_key())
         .serial_number(x509.random_serial_number()).not_valid_before(not_before or now() - DAY)
         .not_valid_after(not_after or now() + 365 * DAY)
         .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False))
    if aki and issuer_key is not key:
        b = b.add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()),
                            critical=False)
    if ca is not None:
        b = b.add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        b = b.add_extension(key_usage(ca), critical=True)
    for ext, critical in extensions:
        b = b.add_extension(ext, critical=critical)
    return b.sign(issuer_key, hashes.SHA256())


def jwt(body, chain, key, alg="RS256", header=None):
    """A JWS as the metadata service writes its blob: the header names the algorithm and carries the chain (x5c)."""
    head = dict({"alg": alg, "typ": "JWT", "x5c": [base64.b64encode(c.public_bytes(DER)).decode() for c in chain]},
                **(header or {}))
    signed = f"{b64u(json.dumps(head).encode())}.{b64u(json.dumps(body).encode())}"
    if alg == "RS256":
        signature = key.sign(signed.encode(), padding.PKCS1v15(), hashes.SHA256())
    elif alg == "ES256":
        r, s = utils.decode_dss_signature(key.sign(signed.encode(), ec.ECDSA(hashes.SHA256())))
        signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    else:
        signature = b"\x00" * 32
    return f"{signed}.{b64u(signature)}".encode()


class Signer:
    """The tests' FIDO root, an intermediate under it, and a signing certificate under that for `host`, as a TLS
    server's: what the real blob's x5c holds, below GlobalSign Root CA - R3."""

    def __init__(self, host="mds.fidoalliance.org", alg="RS256", leaf_ca=None):
        self.alg = alg
        self.root_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.root = certificate(name("Test FIDO Root"), self.root_key, name("Test FIDO Root"), self.root_key, ca=True)
        self.inter_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.inter = certificate(name("Test OV SSL CA"), self.inter_key, name("Test FIDO Root"), self.root_key,
                                 ca=True)
        self.key = (ec.generate_private_key(ec.SECP256R1()) if alg == "ES256"
                    else rsa.generate_private_key(public_exponent=65537, key_size=2048))
        self.leaf = self.signing_cert(host, self.key, ca=leaf_ca)

    def signing_cert(self, host, key, ca=None, issuer=None, issuer_key=None):
        """A TLS server's certificate for `host`, made as the real signer's is, under this signer's intermediate (or
        `issuer`)."""
        return certificate(name(host), key, (issuer or self.inter).subject, issuer_key or self.inter_key, ca=ca,
                           extensions=((x509.SubjectAlternativeName([x509.DNSName(host)]), False),
                                       (x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)))

    def chain(self):
        return [self.leaf, self.inter]

    def root_pem(self):
        return self.root.public_bytes(serialization.Encoding.PEM)

    def blob(self, entries, no=1, next_update=None, chain=None, key=None, alg=None, header=None, body=None):
        content = body if body is not None else {"legalHeader": "Test blob.", "no": no,
                                                 "nextUpdate": next_update or day(30), "entries": entries}
        return jwt(content, self.chain() if chain is None else chain, key or self.key, alg or self.alg, header)


class Maker:
    """A security-key maker of the tests' own: an attestation root (P-256), and under it, or under an intermediate,
    the batch attestation certificate of one model, as WebAuthn 8.2.1 asks of a packed attestation's."""

    def __init__(self, model=MODEL, cert_model=None, unit="Authenticator Attestation", aaguid_ext=True,
                 aaguid_critical=False, alg=-7, intermediate=False, leaf_ca=False, not_after=None, raw_ext=None):
        self.model, self.alg = model, alg
        self.root_key = ec.generate_private_key(ec.SECP256R1())
        self.root = certificate(name("Test Maker Root", "NZ", "Test Maker"), self.root_key,
                                name("Test Maker Root", "NZ", "Test Maker"), self.root_key, ca=True)
        issuer, issuer_key, self.chain_rest = self.root, self.root_key, []
        if intermediate:
            self.inter_key = ec.generate_private_key(ec.SECP256R1())
            self.inter = certificate(name("Test Maker Batch CA", "NZ", "Test Maker"), self.inter_key,
                                     self.root.subject, self.root_key, ca=True)
            issuer, issuer_key, self.chain_rest = self.inter, self.inter_key, [self.inter]
        self.key = (rsa.generate_private_key(public_exponent=65537, key_size=2048) if alg == -257
                    else ec.generate_private_key(ec.SECP256R1()))
        extensions = []
        if aaguid_ext:
            value = raw_ext if raw_ext is not None else b"\x04\x10" + (cert_model or model).bytes
            extensions.append((x509.UnrecognizedExtension(AAGUID_OID, value), aaguid_critical))
        self.leaf = certificate(name("Test Key Batch 1", "NZ", "Test Maker", unit), self.key, issuer.subject,
                                issuer_key, ca=leaf_ca, extensions=extensions, not_after=not_after)
        self.leaf_der = None            # the leaf as bytes, in place of self.leaf: one this library will not build

    def x5c(self):
        return [self.leaf_der or self.leaf.public_bytes(DER)] + [c.public_bytes(DER) for c in self.chain_rest]

    def statement(self, signed):
        """The packed attestation statement over `signed` (authData and the client data's sha256)."""
        if self.alg == -257:
            sig = self.key.sign(signed, padding.PKCS1v15(), hashes.SHA256())
        else:
            sig = self.key.sign(signed, ec.ECDSA(hashes.SHA256()))
        return {"alg": self.alg, "sig": sig, "x5c": self.x5c()}

    def entry(self, roots=None, statuses=(("FIDO_CERTIFIED_L1", "2025-03-01"), ("FIDO_CERTIFIED", "2025-03-01")),
              extensions=("credProtect", "hmac-secret"), transports=("usb", "nfc"), description="Test Key 5 NFC",
              aaguid=None):
        """The model's entry as the metadata lists it."""
        info = {"versions": ["FIDO_2_0", "FIDO_2_1"], "aaguid": (aaguid or self.model).hex}
        if extensions is not None:
            info["extensions"] = list(extensions)
        if transports is not None:
            info["transports"] = list(transports)
        statement = {"aaguid": str(aaguid or self.model), "description": description, "protocolFamily": "fido2",
                     "attestationRootCertificates": [base64.b64encode(c.public_bytes(DER)).decode()
                                                     for c in (roots if roots is not None else [self.root])],
                     "authenticatorGetInfo": info}
        return {"aaguid": str(aaguid or self.model), "metadataStatement": statement,
                "statusReports": [dict({"status": s}, **({"effectiveDate": d} if d else {})) for s, d in statuses],
                "timeOfLastStatusChange": "2025-03-01"}


V3, V4 = bytes.fromhex("a003020102"), bytes.fromhex("a003020103")      # a certificate's version field: 3, and 4


def as_v4(cert):
    """The certificate's bytes with its version field saying 4, which no X.509 has (cryptography refuses to load it)."""
    der = cert.public_bytes(DER)
    assert der.count(V3) == 1
    return der.replace(V3, V4)


def bit_string_country(maker):
    """A leaf for `maker`'s key whose subject's country is a BIT STRING: made as an x500UniqueIdentifier (a BIT STRING)
    whose type is then rewritten to countryName. Reading such a subject raises a TypeError in cryptography."""
    from cryptography.x509.name import _ASN1Type
    subject = x509.Name([x509.NameAttribute(NameOID.X500_UNIQUE_IDENTIFIER, b"\x00NZ", _type=_ASN1Type.BitString),
                         x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Test Maker"),
                         x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Authenticator Attestation"),
                         x509.NameAttribute(NameOID.COMMON_NAME, "Test Key Batch 1")])
    der = certificate(subject, maker.key, maker.root.subject, maker.root_key, ca=False, extensions=(
        (x509.UnrecognizedExtension(AAGUID_OID, b"\x04\x10" + maker.model.bytes), False),)).public_bytes(DER)
    unique_id, country = bytes.fromhex("060355042d"), bytes.fromhex("0603550406")
    assert der.count(unique_id) == 1
    return der.replace(unique_id, country)


class HardwareKey(Authenticator):
    """A software security key of `maker`'s model: its own credential id, the model's AAGUID in its authenticator
    data, and a packed attestation by the maker's batch key; `transports` is what the browser reports for it."""

    def __init__(self, maker, alg=-7, counter=0, aaguid=None, transports=None, **kw):
        super().__init__(alg=alg, counter=counter, **kw)
        self.credential_id = secrets.token_bytes(32)
        self.maker = maker
        self.aaguid = (aaguid or maker.model).bytes
        self.transports = transports

    def auth_data(self, flags=0x05, attested=False, count=None, tail=b"", new_credential=False):
        data = hashlib.sha256(self.rp.encode()).digest() + bytes([flags | (0x40 if attested or new_credential else 0)])
        data += struct.pack(">I", self._count() if count is None else count)
        if attested or new_credential:
            data += self.aaguid + struct.pack(">H", len(self.credential_id)) + self.credential_id + cbor(self.cose())
        return data + tail

    def create(self, options, flags=0x05, fmt="packed", att_stmt=None, tail=b"", statement=None, **over):
        pk = options["publicKey"]
        self.user_handle = unb64u(pk["user"]["id"])
        data = self.auth_data(flags=flags, attested=True, tail=tail)
        client = self.client_data("webauthn.create", **dict({"challenge": pk["challenge"]}, **over))
        signed = data + hashlib.sha256(client).digest()
        stmt = att_stmt if att_stmt is not None else (statement or self.maker.statement)(signed)
        response = {"clientDataJSON": b64u(client),
                    "attestationObject": b64u(cbor({"fmt": fmt, "attStmt": stmt, "authData": data}))}
        if self.transports is not None:
            response["transports"] = self.transports
        return {"id": b64u(self.credential_id), "type": "public-key", "response": response}


_signers = {}


def signer_for(alg="RS256"):
    """One Signer per algorithm for the whole run: RSA keys take a while to make."""
    if alg not in _signers:
        _signers[alg] = Signer(alg=alg)
    return _signers[alg]


def keep(data):
    """Write `data` as the blob kept in the data directory, in place of the one there, as the server writes it."""
    tmp = fido_mds.path() + ".test"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, fido_mds.path())


class Metadata:
    """The tests' metadata: the tests' root trusted, a blob kept, and publish() for another."""

    def __init__(self, monkeypatch, signer, maker):
        self.monkeypatch, self.signer, self.maker = monkeypatch, signer, maker
        monkeypatch.setattr(fido_mds, "ROOT_PEM", signer.root_pem())
        fido_mds.forget()

    def publish(self, entries=None, **kw):
        """Keep a blob listing `entries` (the maker's model, certified, by default)."""
        keep(self.signer.blob([self.maker.entry()] if entries is None else entries, **kw))
        fido_mds.forget()

    def served(self, entries=None, **kw):
        """A fetch that answers with a blob listing `entries`; the calls it was asked."""
        data = self.signer.blob([self.maker.entry()] if entries is None else entries, **kw)
        calls = []

        def fetch(url, timeout=None):
            calls.append(url)
            return data
        self.monkeypatch.setattr(fido_mds, "fetch", fetch)
        return calls


@pytest.fixture
def maker():
    return Maker()


@pytest.fixture
def metadata(monkeypatch, maker):
    """A valid blob kept, listing `maker`'s model as certified, under the tests' root."""
    m = Metadata(monkeypatch, signer_for(), maker)
    m.publish()
    return m


@pytest.fixture
def no_metadata(monkeypatch, maker):
    """The tests' root trusted, and no blob kept."""
    return Metadata(monkeypatch, signer_for(), maker)


def add_hardware(client, headers, maker, name="YubiKey", confirm_with=None, **kw):
    """Add a security key of `maker`'s model; returns (the key, the answer)."""
    key = HardwareKey(maker, **{k: kw.pop(k) for k in ("alg", "aaguid", "transports") if k in kw})
    return key, add(client, headers, key, name=name, confirm_with=confirm_with, **kw)
