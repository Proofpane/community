"""A software CTAP2 security key for the second screen's tests, behind
python-fido2's own USB transport: SoftKey answers CTAP2 commands as a FIDO2 key does, and SoftHid frames them as
CTAPHID packets, so python-fido2's CtapHidDevice talks to it as to a key plugged in over USB, its silent retry of a
busy channel included. No hardware, no network.

What it does, as a key does it:
- getInfo: versions FIDO_2_0 and FIDO_2_1, extensions, an AAGUID, options (rk, up, clientPin, uv when it plays a
  fingerprint key, pinUvAuthToken), PIN/UV protocols 2 and 1;
- clientPin: retries, key agreement, a PIN token with permissions and an rpId (by python-fido2's own PinProtocolV2 and
  PinProtocolV1 functions, on the key's side, with its own ECDH), a UV token, wrong PINs counted: three in a row block
  the PIN until the key is plugged in again (replug), eight in all block it for good;
- getAssertion and getNextAssertion: an allow list, the silent probe (up false), pinUvAuthParam checked, the user's
  touch from a switch the test sets (given, refused, or never: then it waits until the client cancels), real ES256 and
  EdDSA signatures over authenticatorData || clientDataHash, a counter;
- makeCredential, so a test makes the account's passkey through the server's real registration: with no attestation,
  or, given a fido_fixtures.Maker, as a key of that maker's model with its packed attestation (a hardware key);
- import, so a passkey registered the page's way (test_passkeys.Authenticator) signs the Mac's way;
- a busy mode that answers CHANNEL_BUSY, as a key does while another program's request holds it: to the next commands
  (busy, a count), or to a command named (busy_at: a CTAP2 command's name, or "INIT"), the first time it comes; as a
  CTAPHID error, or as a CTAP2 status byte in a CBOR answer (busy_as "status");
- another program's traffic, which a key sends to every program that has it open (other, (when, what), once): from
  this program's INIT, a CTAP2 command named, or its wait for a touch, another program's keep-alive, its INIT answered
  (a greeting, on the broadcast channel with another nonce), or the key's CHANNEL_BUSY to it;
- a key that speaks no CTAP2 (cbor False, a U2F key), and a key taken out (unplugged: every packet fails);
- hmac-secret (CTAP 2.1, section 12.5), the extension WebAuthn's PRF is made of: a passkey made with it holds two
  random secrets, one for assertions with the user verified (CredRandomWithUV) and one for those without
  (CredRandomWithoutUV); an assertion that asks for it (one with no touch, up false, too, as a disk unlocked with no
  touch asks it), with salts encrypted to a shared secret and their MAC, gets HMAC-SHA-256 of each salt under the
  secret its user verification chose, encrypted back, as its authenticator data's extension output (the ED flag). A
  passkey made without it, or imported, gives none.

Every command it is sent is kept in `requests`, decoded, so a test can say what a client asked of it, an extension
asked for included: no signing touch may ask for one. No other extension is computed: credProtect is listed, as keys
list it, and not applied.
"""

import errno
import hashlib
import hmac
import os
import struct
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fido2 import cbor
from fido2.ctap import CtapError
from fido2.ctap2.pin import PinProtocolV1, PinProtocolV2
from fido2.hid import CTAPHID, CtapHidDevice
from fido2.hid.base import CtapHidConnection, HidDescriptor

ERR = CtapError.ERR
AAGUID = bytes.fromhex("8a1f2c3d4e5f60718293a4b5c6d7e8f9")       # a model of the tests' own
PACKET = 64
MAX_MSG = 1200
PIN_RETRIES = 8
UV_RETRIES = 3
IN_A_ROW = 3                    # wrong PINs in a row that block the PIN until the key is plugged in again
GA, MC = 0x02, 0x01             # a token's permissions: getAssertion, makeCredential
PROTOCOLS = {2: PinProtocolV2, 1: PinProtocolV1}
FLAG_UP, FLAG_UV, FLAG_AT, FLAG_ED = 0x01, 0x04, 0x40, 0x80
HMAC_SECRET = "hmac-secret"
SALT = 32                       # bytes of each hmac-secret salt, and of each secret and output
CMD = {0x01: "makeCredential", 0x02: "getAssertion", 0x04: "getInfo", 0x06: "clientPin", 0x07: "reset",
       0x08: "getNextAssertion", 0x0B: "selection"}
BROADCAST = 0xFFFFFFFF
OTHER = 0x0BADC0DE              # another program's channel on the key
TYPE_INIT = 0x80
NEVER_WAIT = 0.02               # a key that is never touched answers a keep-alive this often until it is cancelled


class Credential:
    """One passkey the key holds: for an rpId, its id, its private key and algorithm, its user's handle, its counter,
    and its hmac-secret secrets (with the user verified, without), or None when it was made without hmac-secret."""

    def __init__(self, rp_id, credential_id, private_key, alg, user_id=None, discoverable=False):
        self.rp_id, self.id, self.private_key, self.alg = rp_id, credential_id, private_key, alg
        self.user_id, self.discoverable, self.count = user_id, discoverable, 0
        self.secrets = None

    def cose(self):
        public = self.private_key.public_key()
        if self.alg == -8:
            return {1: 1, 3: -8, -1: 6, -2: public.public_bytes(serialization.Encoding.Raw,
                                                                 serialization.PublicFormat.Raw)}
        numbers = public.public_numbers()
        return {1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")}

    def sign(self, message):
        if self.alg == -8:
            return self.private_key.sign(message)
        return self.private_key.sign(message, ec.ECDSA(hashes.SHA256()))


class Pending:
    """A command that waits for the user's touch; `finish` answers it once the touch is given, refused or cancelled."""

    def __init__(self, finish):
        self.finish = finish


class SoftKey:
    """A FIDO2 key with a PIN (or none: pin=None), a fingerprint sensor when `uv` is True, and the user's touch as
    `touch` says: "given", "refused", or "never" (it waits until the client cancels). Given `maker` (a
    fido_fixtures.Maker), it is a key of that maker's model: the model's AAGUID, and a packed attestation by the
    maker's batch key on each credential it makes."""

    def __init__(self, pin="1234", uv=False, protocols=(2, 1), touch="given", name="Soft Key", serial="0001",
                 finger="match", maker=None):
        self.pin, self.uv, self.protocols, self.touch = pin, uv, tuple(protocols), touch
        self.name, self.serial, self.finger, self.maker = name, serial, finger, maker
        self.aaguid = maker.model.bytes if maker is not None else AAGUID
        self.pin_retries, self.uv_retries, self.in_a_row, self.auth_blocked = PIN_RETRIES, UV_RETRIES, 0, False
        self.credentials = []
        self.requests = []              # (command name, its decoded parameters), in order
        self.touches = 0                # times the key waited for a touch
        self.signatures = 0             # assertions made with the user present
        self.busy = 0                   # CBOR commands still to be answered CHANNEL_BUSY
        self.busy_at = None             # a command's name: the first time it is sent, it is answered CHANNEL_BUSY
        self.busy_as = "error"          # "error": a CTAPHID ERROR frame; "status": a CTAP2 status byte, in CBOR
        self.busy_answered = 0          # times the key answered CHANNEL_BUSY
        self.other = None               # (when, what): another program's frame, sent once (see SoftHid)
        self.garble = None              # a CTAP2 command's name: its answer's map sent out of CBOR's canonical order
        self.min_pin = None             # the least PIN length getInfo names (minPINLength), or None: CTAP's 4
        self.versions = ("FIDO_2_0", "FIDO_2_1")    # the versions getInfo names
        self.cancelled = 0              # requests a CANCEL ended, as their answer said
        self.cancels = 0                # CANCELs heard for a request waiting on their channel
        self.cbor = True                # False: a key that speaks no CTAP2 (U2F only), as its INIT answer says
        self.unplugged = False          # True: the key was taken out, and every packet to or from it fails
        self._next = []                 # what getNextAssertion still has to give
        self._token = None              # (protocol, token, permissions, rpId, user present)
        self._agreement = ec.generate_private_key(ec.SECP256R1())

    # ---- what the tests set up --------------------------------------------------------------------------------------

    def import_credential(self, rp_id, credential_id, private_key, alg, user_id=None):
        """A passkey made elsewhere, kept here: a test_passkeys.Authenticator's key and id, so it signs this way."""
        credential = Credential(rp_id, credential_id, private_key, alg, user_id)
        self.credentials.append(credential)
        return credential

    def replug(self):
        """The key taken out and plugged in again: its key agreement and token new, wrong PINs in a row forgotten."""
        self.in_a_row, self.auth_blocked, self._token = 0, False, None
        self._agreement = ec.generate_private_key(ec.SECP256R1())

    def asked(self, name):
        """The parameters of each command `name` the key was sent, in order."""
        return [params for command, params in self.requests if command == name]

    # ---- CTAP2 ------------------------------------------------------------------------------------------------------

    def info(self):
        options = {"rk": True, "up": True, "plat": False, "clientPin": self.pin is not None, "pinUvAuthToken": True}
        if self.uv:
            options["uv"] = True
        info = {1: list(self.versions), 2: ["hmac-secret", "credProtect"], 3: self.aaguid, 4: options,
                5: MAX_MSG, 6: list(self.protocols), 7: 8, 8: 128, 9: ["usb"],
                10: [{"alg": -7, "type": "public-key"}, {"alg": -8, "type": "public-key"}]}
        if self.min_pin is not None:
            info[13] = self.min_pin                 # minPINLength, where the key's owner set one above CTAP's 4
        return info

    def process(self, message):
        """The answer to one CTAPHID CBOR message (its command byte, then its CBOR): its status byte and CBOR, or a
        Pending that waits for a touch."""
        command, body = message[0], message[1:]
        try:
            params = cbor.decode(body) if body else {}
        except Exception:  # noqa: BLE001  whatever a client sent that is not CBOR
            return bytes([ERR.INVALID_CBOR])
        self.requests.append((CMD.get(command, f"0x{command:02x}"), params))
        handler = {0x01: self._make_credential, 0x02: self._get_assertion, 0x04: lambda p: self._ok(self.info()),
                   0x06: self._client_pin, 0x08: self._get_next_assertion}.get(command)
        if handler is None:
            return bytes([ERR.INVALID_COMMAND])
        try:
            return handler(params)
        except CtapError as e:
            return bytes([e.code])

    @staticmethod
    def _ok(value=None):
        return b"\x00" + (cbor.encode(value) if value is not None else b"")

    def _protocol(self, number):
        if number not in self.protocols:
            raise CtapError(ERR.INVALID_PARAMETER)
        return PROTOCOLS[number]()

    def _shared(self, protocol, key_agreement):
        """The shared secret with the client's key agreement (a COSE P-256 key), by the protocol's own KDF."""
        try:
            x, y = int.from_bytes(key_agreement[-2], "big"), int.from_bytes(key_agreement[-3], "big")
            peer = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
        except (KeyError, TypeError, ValueError):
            raise CtapError(ERR.INVALID_PARAMETER) from None
        return protocol.kdf(self._agreement.exchange(ec.ECDH(), peer))

    def _client_pin(self, p):
        protocol, sub = p.get(1), p.get(2)
        if sub == 0x01:                                                         # getPINRetries
            return self._ok({3: self.pin_retries, 4: self.auth_blocked})
        if sub == 0x07:                                                         # getUVRetries
            return self._ok({5: self.uv_retries})
        proto = self._protocol(protocol)
        if sub == 0x02:                                                         # getKeyAgreement
            n = self._agreement.public_key().public_numbers()
            return self._ok({1: {1: 2, 3: -25, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")}})
        if sub in (0x05, 0x09):                                                 # a token with the PIN
            return self._pin_token(proto, p, permissions=p.get(9) if sub == 0x09 else GA | MC,
                                   rp_id=p.get(0x0A) if sub == 0x09 else None)
        if sub == 0x06:                                                         # a token with the finger
            return self._uv_token(proto, p)
        raise CtapError(ERR.INVALID_SUBCOMMAND)

    def _issue(self, proto, shared, permissions, rp_id, present):
        token = os.urandom(32)
        self._token = (proto.VERSION, token, permissions, rp_id, present)
        return self._ok({2: proto.encrypt(shared, token)})

    def _pin_token(self, proto, p, permissions, rp_id):
        if self.pin is None:
            raise CtapError(ERR.PIN_NOT_SET)
        if self.pin_retries <= 0:
            raise CtapError(ERR.PIN_BLOCKED)
        if self.auth_blocked:
            raise CtapError(ERR.PIN_AUTH_BLOCKED)
        if not permissions:
            raise CtapError(ERR.INVALID_PARAMETER)
        if 3 not in p or 6 not in p:
            raise CtapError(ERR.MISSING_PARAMETER)
        shared = self._shared(proto, p[3])
        self.pin_retries -= 1                                                   # counted before it is checked
        given = proto.decrypt(shared, p[6])
        if not hmac.compare_digest(given, hashlib.sha256(self.pin.encode("utf-8")).digest()[:16]):
            self._agreement = ec.generate_private_key(ec.SECP256R1())
            self.in_a_row += 1
            if self.pin_retries <= 0:
                raise CtapError(ERR.PIN_BLOCKED)
            if self.in_a_row >= IN_A_ROW:
                self.auth_blocked = True
                raise CtapError(ERR.PIN_AUTH_BLOCKED)
            raise CtapError(ERR.PIN_INVALID)
        self.pin_retries, self.in_a_row = PIN_RETRIES, 0
        return self._issue(proto, shared, permissions, rp_id, present=False)

    def _uv_token(self, proto, p):
        if not self.uv:
            raise CtapError(ERR.INVALID_SUBCOMMAND)
        if self.uv_retries <= 0:
            raise CtapError(ERR.UV_BLOCKED)
        shared = self._shared(proto, p.get(3) or {})
        if not p.get(9):
            raise CtapError(ERR.INVALID_PARAMETER)

        def finish(outcome):
            if outcome == "cancelled":
                return bytes([ERR.KEEPALIVE_CANCEL])
            if self.finger != "match":
                self.uv_retries -= 1
                return bytes([ERR.UV_BLOCKED if self.uv_retries <= 0 else ERR.UV_INVALID])
            self.uv_retries = UV_RETRIES
            # a finger on the sensor is a touch too: the token carries the user's presence for one assertion
            return self._issue(proto, shared, p[9], p.get(0x0A), present=True)
        self.touches += 1
        return Pending(finish)

    def _verified(self, p, client_hash, permission, rp_id):
        """Whether pinUvAuthParam (p[param]) is the token's MAC of the client data hash, for `permission` and `rp_id`:
        CtapError for one that is not; False when none was given."""
        param, number = (p.get(6), p.get(7)) if permission == GA else (p.get(8), p.get(9))
        if param is None:
            return False
        if self._token is None:
            raise CtapError(ERR.PIN_AUTH_INVALID)
        version, token, permissions, bound, _ = self._token
        proto = self._protocol(number)
        if proto.VERSION != version or not hmac.compare_digest(proto.authenticate(token, client_hash), param):
            raise CtapError(ERR.PIN_AUTH_INVALID)
        if not permissions & permission or (bound is not None and bound != rp_id):
            raise CtapError(ERR.UNAUTHORIZED_PERMISSION)
        return True

    def _present(self):
        """Whether the token in hand carries the user's presence (a finger), used up once read."""
        if self._token is not None and self._token[4]:
            self._token = self._token[:4] + (False,)
            return True
        return False

    def _auth_data(self, credential, flags, attested=False, extensions=None):
        """Authenticator data: the rpId's hash, the flags (ED where there is extension output), the counter, the
        credential when `attested`, and the extension output's CBOR map last."""
        if extensions:
            flags |= FLAG_ED
        data = hashlib.sha256(credential.rp_id.encode("ascii")).digest() + bytes([flags])
        data += struct.pack(">I", credential.count)
        if attested:
            data += self.aaguid + struct.pack(">H", len(credential.id)) + credential.id + cbor.encode(credential.cose())
        if extensions:
            data += cbor.encode(extensions)
        return data

    def _hmac_salts(self, given):
        """What an hmac-secret input asks (its protocol, the shared secret with its key agreement, and its one or two
        salts), or None where it asks nothing: saltAuth must be the shared secret's MAC of saltEnc (PIN_AUTH_INVALID,
        here, where it is not), and saltEnc must decrypt to one or two salts of 32 bytes (INVALID_LENGTH)."""
        if given is None:
            return None
        if not isinstance(given, dict) or not all(k in given for k in (1, 2, 3)):
            raise CtapError(ERR.MISSING_PARAMETER)
        proto = self._protocol(given.get(4, 1))
        shared = self._shared(proto, given[1])
        if not isinstance(given[2], bytes) or not isinstance(given[3], bytes) \
                or not hmac.compare_digest(proto.authenticate(shared, given[2]), given[3]):
            raise CtapError(ERR.PIN_AUTH_INVALID)
        try:
            salts = proto.decrypt(shared, given[2])
        except ValueError:
            raise CtapError(ERR.INVALID_LENGTH) from None
        if len(salts) not in (SALT, 2 * SALT):
            raise CtapError(ERR.INVALID_LENGTH)
        return proto, shared, salts

    @staticmethod
    def _hmac_output(credential, flags, hmac_in):
        """The extension output for an assertion with `flags` by `credential`, given what hmac-secret asked (or None):
        HMAC-SHA-256 of each salt under the secret its user verification chose, encrypted to the shared secret; None
        where nothing was asked or the passkey holds no secrets."""
        if hmac_in is None or credential.secrets is None:
            return None
        proto, shared, salts = hmac_in
        secret = credential.secrets[0 if flags & FLAG_UV else 1]
        outputs = b"".join(hmac.new(secret, salts[i:i + SALT], hashlib.sha256).digest()
                           for i in range(0, len(salts), SALT))
        return {HMAC_SECRET: proto.encrypt(shared, outputs)}

    def _make_credential(self, p):
        client_hash, rp, user, params = p.get(1), p.get(2), p.get(3), p.get(4)
        if not isinstance(client_hash, bytes) or not isinstance(rp, dict) or not isinstance(user, dict) \
                or not isinstance(params, list):
            raise CtapError(ERR.MISSING_PARAMETER)
        options = p.get(7) or {}
        verified = self._verified(p, client_hash, MC, rp.get("id"))
        if not verified and self.pin is not None and not options.get("uv"):
            raise CtapError(ERR.PUAT_REQUIRED)
        alg = next((c.get("alg") for c in params if c.get("alg") in (-7, -8)), None)
        if alg is None:
            raise CtapError(ERR.UNSUPPORTED_ALGORITHM)
        excluded = any(c.id == d.get("id") and c.rp_id == rp["id"] for d in p.get(5) or [] for c in self.credentials)
        secret = (p.get(6) or {}).get(HMAC_SECRET) is True
        present = self._present()

        def finish(outcome):
            if outcome == "cancelled":
                return bytes([ERR.KEEPALIVE_CANCEL])
            if outcome != "given":
                return bytes([ERR.OPERATION_DENIED])
            if excluded:
                return bytes([ERR.CREDENTIAL_EXCLUDED])
            key = ec.generate_private_key(ec.SECP256R1()) if alg == -7 else Ed25519PrivateKey.generate()
            credential = Credential(rp["id"], os.urandom(32), key, alg, user.get("id"), bool(options.get("rk")))
            if secret:
                credential.secrets = (os.urandom(SALT), os.urandom(SALT))
            self.credentials.append(credential)
            data = self._auth_data(credential, FLAG_UP | (FLAG_UV if verified else 0) | FLAG_AT, attested=True,
                                   extensions={HMAC_SECRET: True} if secret else None)
            if self.maker is not None:
                return self._ok({1: "packed", 2: data, 3: self.maker.statement(data + client_hash)})
            return self._ok({1: "none", 2: data, 3: {}})
        if present:
            return finish("given")
        self.touches += 1
        return Pending(finish)

    def _assertion(self, credential, client_hash, flags, many=None, hmac_in=None):
        credential.count += 1
        data = self._auth_data(credential, flags, extensions=self._hmac_output(credential, flags, hmac_in))
        answer = {1: {"id": credential.id, "type": "public-key"}, 2: data,
                  3: credential.sign(data + client_hash)}
        if credential.user_id is not None and credential.discoverable:
            answer[4] = {"id": credential.user_id}
        if many:
            answer[5] = many
        if flags & FLAG_UP:
            self.signatures += 1
        return self._ok(answer)

    def _get_assertion(self, p):
        rp_id, client_hash = p.get(1), p.get(2)
        if not isinstance(rp_id, str) or not isinstance(client_hash, bytes):
            raise CtapError(ERR.MISSING_PARAMETER)
        options = p.get(5) or {}
        verified = self._verified(p, client_hash, GA, rp_id)
        hmac_in = self._hmac_salts((p.get(4) or {}).get(HMAC_SECRET))
        allow = p.get(3)
        if allow:
            ids = [d.get("id") for d in allow]
            found = [c for i in ids for c in self.credentials if c.rp_id == rp_id and c.id == i][:1]
        else:
            found = [c for c in self.credentials if c.rp_id == rp_id and c.discoverable]
        flags = FLAG_UV if verified else 0
        if options.get("up") is False:                                          # the silent probe
            if not found:
                raise CtapError(ERR.NO_CREDENTIALS)
            return self._assertion(found[0], client_hash, flags, hmac_in=hmac_in)
        present = self._present()

        def finish(outcome):
            if outcome == "cancelled":
                return bytes([ERR.KEEPALIVE_CANCEL])
            if outcome != "given":
                return bytes([ERR.OPERATION_DENIED])
            if not found:
                return bytes([ERR.NO_CREDENTIALS])
            self._next = [(c, client_hash, flags | FLAG_UP, hmac_in) for c in found[1:]]
            return self._assertion(found[0], client_hash, flags | FLAG_UP, len(found) if len(found) > 1 else None,
                                   hmac_in)
        if present:
            return finish("given")
        self.touches += 1
        return Pending(finish)

    def _get_next_assertion(self, p):
        if not self._next:
            raise CtapError(ERR.NOT_ALLOWED)
        credential, client_hash, flags, hmac_in = self._next.pop(0)
        return self._assertion(credential, client_hash, flags, hmac_in=hmac_in)


class SoftHid(CtapHidConnection):
    """The USB side of a SoftKey: CTAPHID packets of 64 bytes, a channel given at INIT, CBOR messages to the key and
    its answers back, a keep-alive saying the key waits for a touch, CANCEL (which ends only a request waiting on its
    own channel), ERROR CHANNEL_BUSY in busy mode, and another program's frames where key.other asks for one: (when,
    what), when being "INIT", a CTAP2 command's name or "touch", and what "keep-alive", "greeting" or "busy"."""

    def __init__(self, key):
        self.key = key
        self.channel = 0x5EC0DE01
        self._in = None                 # the message being received: [channel, command, length, bytes]
        self._out = []                  # packets to be read
        self._pending = None            # a CBOR command waiting for a touch
        self._kept_alive = False
        self._cancel = False
        self.closed = False

    def close(self):
        self.closed = True

    def _send(self, channel, command, data):
        frame = struct.pack(">IBH", channel, TYPE_INIT | command, len(data)) + data[:PACKET - 7]
        self._out.append(frame.ljust(PACKET, b"\0"))
        rest, seq = data[PACKET - 7:], 0
        while rest:
            frame = struct.pack(">IB", channel, seq) + rest[:PACKET - 5]
            self._out.append(frame.ljust(PACKET, b"\0"))
            rest, seq = rest[PACKET - 5:], seq + 1

    def write_packet(self, data):
        if self.key.unplugged:
            raise OSError(errno.ENODEV, "the key was taken out")
        channel, kind = struct.unpack_from(">IB", data)
        if kind & TYPE_INIT:
            command, length = kind & 0x7F, struct.unpack_from(">H", data, 5)[0]
            if command == CTAPHID.CANCEL:                   # it ends only a request waiting on its own channel
                if self._pending is not None and self._pending[0] == channel and not self._cancel:
                    self._cancel = True
                    self.key.cancels += 1
                return
            self._in = [channel, command, length, data[7:7 + min(length, PACKET - 7)]]
        else:
            if self._in is None:
                return
            self._in[3] += data[5:5 + min(self._in[2] - len(self._in[3]), PACKET - 5)]
        if self._in is not None and len(self._in[3]) >= self._in[2]:
            channel, command, _, message = self._in
            self._in = None
            self._answer(channel, command, message)

    def _other(self, when):
        """Another program's frame, queued once (key.other), when this program reaches `when`."""
        if self.key.other is None or self.key.other[0] != when:
            return False
        what, self.key.other = self.key.other[1], None
        if what == "keep-alive":                    # the key waits for a touch for another program
            frame = struct.pack(">IBHB", OTHER, TYPE_INIT | CTAPHID.KEEPALIVE, 1, 2)
        elif what == "greeting":                    # another program's INIT answered, on the broadcast channel
            frame = struct.pack(">IBH", BROADCAST, TYPE_INIT | CTAPHID.INIT, 17) + os.urandom(8) + \
                struct.pack(">IBBBBB", OTHER, 2, 1, 0, 0, 0x05)
        else:                                       # "busy": the key answers another program that it is busy
            frame = struct.pack(">IBHB", OTHER, TYPE_INIT | CTAPHID.ERROR, 1, ERR.CHANNEL_BUSY)
        self._out.append(frame.ljust(PACKET, b"\0"))
        return True

    def _answer(self, channel, command, message):
        if command == CTAPHID.INIT:
            self._other("INIT")
            if self.key.busy_at == "INIT":                                  # a key busy with another program's request
                self.key.busy_at = None
                self.key.busy_answered += 1
                self._send(channel, CTAPHID.ERROR, bytes([ERR.CHANNEL_BUSY]))
                return
            capabilities = 0x05 if self.key.cbor else 0x01                  # WINK, and CBOR where it speaks CTAP2
            self._send(BROADCAST if channel == BROADCAST else channel, CTAPHID.INIT,
                       message[:8] + struct.pack(">IBBBBB", self.channel, 2, 1, 0, 0, capabilities))
        elif command == CTAPHID.PING:
            self._send(channel, CTAPHID.PING, message)
        elif command == CTAPHID.WINK:
            self._send(channel, CTAPHID.WINK, b"")
        elif command == CTAPHID.CBOR:
            self._other(CMD.get(message[0]) if message else None)
            at = self.key.busy_at is not None and message[:1] and CMD.get(message[0]) == self.key.busy_at
            if self.key.busy or at:
                if at:
                    self.key.busy_at = None
                else:
                    self.key.busy -= 1
                self.key.busy_answered += 1
                if self.key.busy_as == "status":
                    self._send(channel, CTAPHID.CBOR, bytes([ERR.CHANNEL_BUSY]))
                else:
                    self._send(channel, CTAPHID.ERROR, bytes([ERR.CHANNEL_BUSY]))
                return
            answer = self.key.process(message)
            if isinstance(answer, Pending):
                self._pending, self._kept_alive, self._cancel = (channel, answer), False, False
            else:
                if self.key.garble is not None and self.key.garble == CMD.get(message[0]) and answer[:1] == b"\0":
                    found = cbor.decode(answer[1:])             # the same map, its keys largest first
                    answer = b"\0" + bytes([0xA0 | len(found)]) + b"".join(
                        cbor.encode(k) + cbor.encode(found[k]) for k in sorted(found, reverse=True))
                self._send(channel, CTAPHID.CBOR, answer)
        else:
            self._send(channel, CTAPHID.ERROR, bytes([ERR.INVALID_COMMAND]))

    def read_packet(self):
        if self.key.unplugged:
            raise OSError(errno.ENODEV, "the key was taken out")
        if self._out:
            return self._out.pop(0)
        if self._pending is None:
            raise OSError("the soft key has nothing to send")
        if self._other("touch"):                    # while this program's request waits for the touch
            return self._out.pop(0)
        channel, pending = self._pending
        if self._cancel:
            self.key.cancelled += 1
            outcome = "cancelled"
        elif not self._kept_alive or self.key.touch == "never":
            if self._kept_alive:
                time.sleep(NEVER_WAIT)
            self._kept_alive = True
            return struct.pack(">IBHB", channel, TYPE_INIT | CTAPHID.KEEPALIVE, 1, 2).ljust(PACKET, b"\0")
        else:
            outcome = self.key.touch
        self._pending = None
        self._send(channel, CTAPHID.CBOR, pending.finish(outcome))
        return self._out.pop(0)


def descriptor(key):
    """`key`'s HID descriptor, as python-fido2's list_descriptors gives one for a key on USB."""
    return HidDescriptor(path=f"soft:{key.serial}", vid=0x1209, pid=0x5EC0, report_size_in=PACKET,
                         report_size_out=PACKET, product_name=key.name, serial_number=key.serial)


def device(key):
    """A python-fido2 CtapHidDevice for `key`, as list_devices gives one for a key on USB."""
    return CtapHidDevice(descriptor(key), SoftHid(key))
