"""The FIDO metadata blob (fido_mds.py): trusted only when its chain verifies up to the FIDO root as the metadata
service's own certificate and its signature verifies; kept on disk and verified again when read; used through its
nextUpdate's day and fetched again from its start, by a thread of its own, never in a request's path. Blobs, roots
and chains are the tests' own (tests/fido_fixtures.py); a fetch is a function of the test's, never the network."""

import base64
import json
import os
import threading
import time
import uuid

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from conftest import signed_up
from fido_fixtures import DER, Signer, add_hardware, as_v4, day, jwt, keep, signer_for
from fido_fixtures import maker, metadata, no_metadata  # noqa: F401  (fixtures)
from proofpane_open import config, fido_mds, passkeys
from test_passkeys import b64u

GLOBALSIGN_R3 = "cbb522d7b7f127ad6a0113865bdf1cd4102e7d0759af635a7cf4720dc963c53b"
URL = "https://mds.test.invalid/"


def test_the_bundled_root_is_globalsign_root_ca_r3():
    root = x509.load_pem_x509_certificate(fido_mds.ROOT_PEM)
    assert root.fingerprint(hashes.SHA256()).hex() == GLOBALSIGN_R3
    assert root.subject.rfc4514_string() == "CN=GlobalSign,O=GlobalSign,OU=GlobalSign Root CA - R3"


def test_the_address_is_the_fido_alliance_s_and_the_tests_fetch_nothing(monkeypatch):
    assert config.mds_url() == ""                                   # conftest: no test reaches the service
    monkeypatch.delenv("OPEN_EDITION_MDS_URL")
    assert config.mds_url() == "https://mds3.fidoalliance.org/"


# ---- what a blob must be -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("alg", ["RS256", "ES256"])
def test_a_blob_signed_under_the_fido_root_by_the_service_s_certificate_verifies(monkeypatch, maker, alg):
    signer = signer_for(alg)
    monkeypatch.setattr(fido_mds, "ROOT_PEM", signer.root_pem())
    blob = fido_mds.verify(signer.blob([maker.entry(), {"aaguid": "not one"}, {"aaid": "4e4e#4005"}], no=7))
    assert (blob.no, blob.next_update) == (7, day(30))
    assert list(blob.entries) == [str(maker.model)]                 # only an AAGUID's entry is indexed


def test_a_blob_of_the_service_s_other_name_verifies_too(monkeypatch, maker):
    signer = Signer(host="mds3.fidoalliance.org")
    monkeypatch.setattr(fido_mds, "ROOT_PEM", signer.root_pem())
    assert fido_mds.verify(signer.blob([maker.entry()])).no == 1


def _refused(data, why=None):
    with pytest.raises(fido_mds.BlobError) as refused:
        fido_mds.verify(data)
    if why:
        assert why in str(refused.value), str(refused.value)


def test_a_certificate_the_same_ca_gave_another_site_signs_no_blob(monkeypatch, maker):
    # the root is a public CA's: any site can have a certificate under it, so the signer's name is what counts
    signer = signer_for()
    monkeypatch.setattr(fido_mds, "ROOT_PEM", signer.root_pem())
    for host in ("evil.example", "mds.fidoalliance.org.evil.example", "fidoalliance.org"):
        key = ec.generate_private_key(ec.SECP256R1())
        _refused(signer.blob([maker.entry()], chain=[signer.signing_cert(host, key), signer.inter], key=key,
                             alg="ES256"), "does not verify up to the FIDO root")


def test_a_certificate_that_is_no_ca_cannot_vouch_for_the_service_s(monkeypatch, maker):
    # a site's own certificate, under the root, issuing one for mds.fidoalliance.org with its key
    signer = signer_for()
    monkeypatch.setattr(fido_mds, "ROOT_PEM", signer.root_pem())
    site_key, key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    site = signer.signing_cert("evil.example", site_key, ca=False)
    forged = signer.signing_cert("mds.fidoalliance.org", key, issuer=site, issuer_key=site_key)
    _refused(signer.blob([maker.entry()], chain=[forged, site, signer.inter], key=key, alg="ES256"),
             "does not verify up to the FIDO root")


def test_a_blob_under_another_root_or_with_no_chain_is_refused(monkeypatch, maker):
    monkeypatch.setattr(fido_mds, "ROOT_PEM", signer_for().root_pem())
    _refused(signer_for("ES256").blob([maker.entry()]), "does not verify up to the FIDO root")
    _refused(signer_for().blob([maker.entry()], chain=[]), "no certificate chain")
    _refused(signer_for().blob([maker.entry()], header={"x5c": "MIIB"}), "no certificate chain")
    _refused(signer_for().blob([maker.entry()], header={"x5c": ["not base64!"]}), "cannot be read")


def test_the_real_root_vouches_for_none_of_the_tests_blobs(maker):
    # the bundled GlobalSign root, as the app runs: a test chain is not under it
    _refused(signer_for().blob([maker.entry()]), "does not verify up to the FIDO root")


def test_a_blob_changed_after_it_was_signed_is_refused(monkeypatch, maker):
    signer = signer_for()
    monkeypatch.setattr(fido_mds, "ROOT_PEM", signer.root_pem())
    head, body, sig = signer.blob([maker.entry()]).split(b".")
    changed = json.loads(base64.urlsafe_b64decode(body + b"=" * (-len(body) % 4)))
    changed["no"] = 99
    _refused(b".".join((head, b64u(json.dumps(changed).encode()).encode(), sig)), "signature does not verify")
    _refused(b".".join((head, body, sig[:-4] + (b"AAAA" if sig[-4:] != b"AAAA" else b"BBBB"))),
             "signature does not verify")


@pytest.mark.parametrize("header, why", [
    ({"alg": "none"}, "no algorithm"), ({"alg": "HS256"}, "no algorithm"), ({"alg": "PS256"}, "no algorithm"),
    ({"crit": ["exp"]}, "crit"),
])
def test_an_algorithm_or_an_extension_it_does_not_take_is_refused(monkeypatch, maker, header, why):
    signer = signer_for()
    monkeypatch.setattr(fido_mds, "ROOT_PEM", signer.root_pem())
    _refused(signer.blob([maker.entry()], header=header), why)


def test_an_algorithm_must_be_the_signing_key_s(monkeypatch, maker):
    # RS256 named over a P-256 signer's key, ES256 over an RSA one
    for named, alg in (("ES256", "RS256"), ("RS256", "ES256")):
        signer = signer_for(named)
        monkeypatch.setattr(fido_mds, "ROOT_PEM", signer.root_pem())
        _refused(signer.blob([maker.entry()], header={"alg": alg}))


@pytest.mark.parametrize("data", [b"", b"a.b", b"a.b.c.d", b"\xff.\xfe.\xfd", b"!!.e30.AA", b"e30.e30.AA"])
def test_what_is_not_a_jwt_is_refused(data):
    _refused(data)


@pytest.mark.parametrize("body, why", [
    ({"nextUpdate": "2099-01-01", "entries": []}, "serial number"),
    ({"no": True, "nextUpdate": "2099-01-01", "entries": []}, "serial number"),
    ({"no": 1, "nextUpdate": "next month", "entries": []}, "nextUpdate"),
    ({"no": 1, "nextUpdate": "2099-02-30", "entries": []}, "nextUpdate"),
    ({"no": 1, "nextUpdate": "2099-01-01", "entries": {}}, "entries"),
    ([1, 2], "not an object"),
])
def test_a_body_without_what_the_blob_holds_is_refused(monkeypatch, body, why):
    signer = signer_for()
    monkeypatch.setattr(fido_mds, "ROOT_PEM", signer.root_pem())
    _refused(signer.blob([], body=body), why)


def test_a_blob_is_used_through_its_next_update_s_day_and_no_longer(monkeypatch, maker):
    signer = signer_for()
    monkeypatch.setattr(fido_mds, "ROOT_PEM", signer.root_pem())
    blob = fido_mds.verify(signer.blob([maker.entry()], next_update=day(0)))         # today's: due, still used
    assert blob.valid(time.time()) and time.time() >= blob.refresh_at
    assert blob.expires_at - blob.refresh_at == 86400
    _refused(signer.blob([maker.entry()], next_update=day(-1)), "time has passed")
    assert not fido_mds.verify(signer.blob([], next_update=day(5))).valid(time.time() + 7 * 86400)


def test_a_blob_is_used_no_longer_than_its_signing_certificate(monkeypatch, maker):
    signer = Signer()
    monkeypatch.setattr(fido_mds, "ROOT_PEM", signer.root_pem())
    blob = fido_mds.verify(signer.blob([maker.entry()], next_update=day(5000)))
    assert blob.expires_at == min(int(c.not_valid_after_utc.timestamp()) for c in signer.chain())
    assert blob.expires_at < blob.refresh_at


def test_an_entry_listed_twice_names_no_entry(monkeypatch, maker):
    signer = signer_for()
    monkeypatch.setattr(fido_mds, "ROOT_PEM", signer.root_pem())
    other = uuid.uuid4()
    blob = fido_mds.verify(signer.blob([maker.entry(), maker.entry(aaguid=other), maker.entry()]))
    assert blob.entries[str(maker.model)] is None and blob.entries[str(other)]["aaguid"] == str(other)


def test_a_blob_larger_than_the_server_takes_is_refused(monkeypatch, maker):
    signer = signer_for()
    monkeypatch.setattr(fido_mds, "ROOT_PEM", signer.root_pem())
    data = signer.blob([maker.entry()])
    monkeypatch.setattr(fido_mds, "MAX_BLOB", len(data) - 1)
    _refused(data, "size")


# ---- the blob kept ---------------------------------------------------------------------------------------------------

def test_the_kept_blob_is_verified_again_whenever_its_file_changes(no_metadata, maker):
    assert fido_mds.kept() is None and fido_mds.status() == {"valid": False, "next_update": None}
    no_metadata.publish()
    assert fido_mds.kept().entries[str(maker.model)] is not None
    head, body, sig = open(fido_mds.path(), "rb").read().split(b".")
    keep(b".".join((head, body, sig[:-8] + b"AAAAAAAA")))           # written over by hand: not verified, not used
    assert fido_mds.kept() is None
    keep(no_metadata.signer.blob([maker.entry()], next_update=day(-3)))
    assert fido_mds.kept() is None                                  # verified, but its time has passed


def test_a_kept_blob_is_used_until_its_time_passes(metadata):
    blob = fido_mds.kept()
    assert fido_mds.kept(blob.expires_at - 1) is blob and fido_mds.kept(blob.expires_at) is None


# ---- fetching -------------------------------------------------------------------------------------------------------

@pytest.fixture
def address(monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_MDS_URL", URL)
    fido_mds._state.update(wanted=False, next_try=0, handed=None)
    yield URL
    fido_mds.stop()


def test_refresh_fetches_verifies_and_keeps_the_blob(no_metadata, address, maker):
    calls = no_metadata.served(no=3)
    blob = fido_mds.refresh()
    assert calls == [URL] and blob.no == 3 and fido_mds.kept().no == 3
    assert oct(os.stat(fido_mds.path()).st_mode & 0o777) == "0o600"
    assert [n for n in os.listdir(config.data_dir()) if n.startswith(fido_mds.FILE)] == [fido_mds.FILE]


def test_a_fetched_blob_that_does_not_verify_is_not_kept(no_metadata, address, maker, monkeypatch):
    no_metadata.publish(no=5)
    monkeypatch.setattr(fido_mds, "fetch", lambda url, timeout=None: signer_for("ES256").blob([maker.entry()], no=6))
    with pytest.raises(fido_mds.BlobError):
        fido_mds.refresh()
    assert fido_mds.kept().no == 5


def test_a_blob_whose_serial_number_goes_back_is_not_kept(no_metadata, address):
    no_metadata.publish(no=5)
    no_metadata.served(no=4)
    with pytest.raises(fido_mds.BlobError, match="lower than the kept blob's"):
        fido_mds.refresh()
    assert fido_mds.kept().no == 5
    no_metadata.served(no=5)                                        # the same one again: kept
    assert fido_mds.refresh().no == 5


@pytest.mark.parametrize("loss", ["gone", "unreadable"])
def test_losing_the_kept_blob_lets_no_older_one_in_since_the_highest_accepted_is_in_the_database(no_metadata, address,
                                                                                                loss):
    no_metadata.served(no=5)
    assert fido_mds.refresh().no == 5 and fido_mds.accepted()[0] == 5
    if loss == "gone":
        os.unlink(fido_mds.path())
    else:
        keep(b"not a blob")
    fido_mds.forget()
    no_metadata.served(no=4)                                        # older, and valid: a mirror's, say
    with pytest.raises(fido_mds.BlobError, match=r"lower than the highest accepted here \(5\)"):
        fido_mds.refresh()
    assert fido_mds.kept() is None
    no_metadata.served(no=5)                                        # the one accepted, again: kept again
    assert fido_mds.refresh().no == 5
    no_metadata.served(no=6)
    assert fido_mds.refresh().no == 6 and fido_mds.accepted()[0] == 6


def test_another_blob_with_the_serial_number_accepted_is_refused(no_metadata, address, maker):
    no_metadata.served(no=5)
    fido_mds.refresh()
    no_metadata.served([maker.entry(transports=("nfc",))], no=5)
    with pytest.raises(fido_mds.BlobError, match=r"not the blob accepted here with its serial number \(5\)"):
        fido_mds.refresh()
    assert fido_mds.kept().entries[str(maker.model)]["metadataStatement"]["authenticatorGetInfo"]["transports"] == \
        ["usb", "nfc"]


def test_a_kept_blob_read_counts_as_accepted_and_the_record_never_goes_down(no_metadata, address):
    no_metadata.publish(no=7)                                       # kept before the database recorded any
    assert fido_mds.kept().no == 7 and fido_mds.accepted()[0] == 7
    no_metadata.publish(no=3)                                       # an older one put there by hand
    assert fido_mds.kept().no == 3 and fido_mds.accepted()[0] == 7
    os.unlink(fido_mds.path())
    fido_mds.forget()
    no_metadata.served(no=6)
    with pytest.raises(fido_mds.BlobError, match=r"lower than the highest accepted here \(7\)"):
        fido_mds.refresh()


def test_with_no_address_nothing_is_fetched(no_metadata, monkeypatch):
    monkeypatch.setattr(fido_mds, "fetch", lambda *a, **k: pytest.fail("fetched with no address"))
    with pytest.raises(fido_mds.BlobError, match="OPEN_EDITION_MDS_URL"):
        fido_mds.refresh()
    assert fido_mds.start(lambda now: None, lambda: 1) is None
    assert fido_mds.tick(lambda now: None, lambda: 1) == fido_mds.CHECK_EVERY


@pytest.mark.parametrize("url", ["http://mds3.fidoalliance.org/", "file:///etc/passwd", "ftp://x/"])
def test_the_fetch_takes_only_an_https_address(url):
    with pytest.raises(fido_mds.BlobError, match="not https"):
        fido_mds.fetch(url)


def test_a_fetch_larger_than_the_server_takes_is_cut_off(monkeypatch):
    class Answer:
        def __init__(self):
            self.sent = 0

        def read1(self, n):
            self.sent += n
            return b"x" * n

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(fido_mds.urllib.request, "urlopen", lambda request, timeout: Answer())
    monkeypatch.setattr(fido_mds, "MAX_BLOB", 3 * 1024 * 1024)
    monkeypatch.setattr(fido_mds, "FETCH_DEADLINE", 5)             # an endless answer ends here, either way
    with pytest.raises(fido_mds.BlobError, match="larger"):
        fido_mds.fetch(URL)
    monkeypatch.setattr(fido_mds, "MAX_BLOB", 64 * 1024 * 1024)
    monkeypatch.setattr(fido_mds, "FETCH_DEADLINE", -1)            # a fetch past its deadline is cut off too
    with pytest.raises(fido_mds.BlobError, match="took too long"):
        fido_mds.fetch(URL)


# ---- the refresher --------------------------------------------------------------------------------------------------

def test_nothing_is_fetched_while_no_blob_is_wanted(no_metadata, address):
    calls = no_metadata.served()
    judged = []
    assert fido_mds.tick(judged.append, lambda: 0) == fido_mds.CHECK_EVERY
    assert calls == [] and judged == []


def test_a_passkey_waiting_to_be_judged_wants_a_blob_and_is_judged_once_it_comes(no_metadata, address):
    calls = no_metadata.served()
    judged = []
    fido_mds.tick(judged.append, lambda: 1)
    assert calls == [URL] and len(judged) == 1 and fido_mds.kept() is not None


def test_a_passkey_stored_while_the_fetch_ran_is_judged_as_it_ends(no_metadata, address):
    # a request stored its key to be judged later while the refresher's fetch still ran
    no_metadata.served()
    waiting = iter([0, 1])
    judged = []
    fido_mds.nudge()
    fido_mds.tick(judged.append, lambda: next(waiting))
    assert len(judged) == 1


def test_a_blob_new_to_the_refresher_is_handed_on_once_with_no_key_waiting(no_metadata, address):
    # so the hardware keys are reconsidered against each new blob (passkeys.reconsider), not only when a key waits
    handed = []
    no_metadata.publish(no=1)
    fido_mds.tick(handed.append, lambda: 0)
    fido_mds.tick(handed.append, lambda: 0)
    assert len(handed) == 1
    no_metadata.publish(no=2)
    fido_mds.tick(handed.append, lambda: 0)
    fido_mds.tick(handed.append, lambda: 0)
    assert len(handed) == 2


def test_a_blob_whose_handing_failed_is_handed_again_next_round(no_metadata, address):
    no_metadata.publish(no=1)
    tries = []

    def failing(now):
        tries.append(now)
        raise OSError("the disk is gone")
    with pytest.raises(OSError):
        fido_mds.tick(failing, lambda: 0)
    fido_mds.tick(tries.append, lambda: 0)
    assert len(tries) == 2


def test_a_kept_blob_is_fetched_again_from_its_next_update_s_day(no_metadata, address):
    no_metadata.publish(next_update=day(10))
    calls = no_metadata.served(no=2)
    delay = fido_mds.tick(lambda now: None, lambda: 0)
    assert calls == [] and delay <= fido_mds.CHECK_EVERY
    no_metadata.publish(next_update=day(0))                          # its day has come: fetched again
    fido_mds.tick(lambda now: None, lambda: 0)
    assert calls == [URL] and fido_mds.kept().no == 2


def test_a_failed_fetch_is_tried_again_after_a_while_and_the_kept_blob_stays_in_use(no_metadata, address, monkeypatch):
    no_metadata.publish(next_update=day(0))
    tries = []

    def failing(url, timeout=None):
        tries.append(url)
        raise fido_mds.BlobError("it could not be fetched (OSError)")
    monkeypatch.setattr(fido_mds, "fetch", failing)
    assert fido_mds.tick(lambda now: None, lambda: 0) == fido_mds.RETRY_AFTER
    fido_mds.tick(lambda now: None, lambda: 0)
    assert len(tries) == 1 and fido_mds.kept() is not None          # not again at once; the old one still valid
    fido_mds.tick(lambda now: None, lambda: 0, now=time.time() + fido_mds.RETRY_AFTER + 1)
    assert len(tries) == 2


def test_adding_a_key_never_waits_for_a_fetch_which_the_refresher_makes_and_then_judges_the_key(
        no_metadata, address, monkeypatch, client):
    data = no_metadata.signer.blob([no_metadata.maker.entry()])
    release, asked = threading.Event(), []

    def slow(url, timeout=None):
        asked.append(threading.current_thread().name)
        release.wait(20)
        return data
    monkeypatch.setattr(fido_mds, "fetch", slow)
    owner = signed_up(client)
    fido_mds.start(passkeys.blob_arrived, passkeys.awaiting_judgment)      # as the app's lifespan starts it
    time.sleep(0.2)                                                 # its first round: nothing wanted yet
    started = time.monotonic()
    r = add_hardware(client, owner, no_metadata.maker)[1]
    assert time.monotonic() - started < 1.5                         # a request once waited 2 s for the fetch here
    assert (r.json()["tier"], r.json()["judge_again"]) == ("device", True) and not release.is_set()
    for _ in range(100):                                            # the fetch it woke has begun, or soon will
        if asked:
            break
        time.sleep(0.05)
    assert asked == ["fido-mds"]                                    # the refresher's thread fetches, not the request
    release.set()
    for _ in range(100):
        if passkeys.awaiting_judgment() == 0:
            break
        time.sleep(0.05)
    assert passkeys.awaiting_judgment() == 0                        # judged by the refresher, not by a list
    (k,) = client.get("/api/account/passkeys", headers=owner).json()["passkeys"]
    assert (k["tier"], k["judge_again"]) == ("hardware", False)


def test_a_failing_round_does_not_end_the_refresher(no_metadata, address, monkeypatch):
    rounds = []

    def broken(on_blob, waiting, now=None):
        rounds.append(1)
        raise OSError("the disk is gone")
    monkeypatch.setattr(fido_mds, "tick", broken)
    monkeypatch.setattr(fido_mds, "RETRY_AFTER", 0.05)
    fido_mds.start(lambda now: None, lambda: 0)
    for _ in range(100):
        if len(rounds) > 2:
            break
        time.sleep(0.05)
    assert len(rounds) > 2 and fido_mds.running()


def test_an_rsa_signing_key_under_2048_bits_signs_no_blob(monkeypatch, maker):
    signer = signer_for()
    monkeypatch.setattr(fido_mds, "ROOT_PEM", signer.root_pem())
    small = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    leaf = signer.signing_cert("mds.fidoalliance.org", small)
    data = jwt({"no": 1, "nextUpdate": day(30), "entries": []}, [leaf, signer.inter], small)
    _refused(data, "its signing key is not RSA of at least 2048 bits")      # past the chain, which takes the key


def test_a_blob_whose_chain_cannot_be_read_is_refused_and_a_kept_one_is_no_blob(no_metadata, maker, client):
    signer = no_metadata.signer
    unreadable = [base64.b64encode(as_v4(signer.leaf)).decode(), base64.b64encode(signer.inter.public_bytes(DER)).decode()]
    data = signer.blob([maker.entry()], header={"x5c": unreadable})
    _refused(data, "certificates cannot be read")
    keep(data)
    assert fido_mds.kept() is None and fido_mds.status() == {"valid": False, "next_update": None}
    assert client.get("/api/account/passkeys", headers=signed_up(client)).status_code == 200


def test_a_round_that_fails_any_way_waits_before_it_fetches_again(no_metadata, address, monkeypatch):
    calls = no_metadata.served()

    def full(data):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(fido_mds, "_keep", full)
    assert fido_mds.tick(lambda now: None, lambda: 1) == fido_mds.RETRY_AFTER
    fido_mds.nudge()                                                # a request asks again at once
    fido_mds.tick(lambda now: None, lambda: 1)
    assert calls == [URL]


def test_a_fetch_that_trickles_is_given_up_at_its_deadline(monkeypatch):
    class Trickle:
        def read1(self, n):                                        # what has arrived: one byte at a time
            time.sleep(0.05)
            return b"x"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(fido_mds.urllib.request, "urlopen", lambda request, timeout: Trickle())
    monkeypatch.setattr(fido_mds, "FETCH_DEADLINE", 0.5)
    started = time.monotonic()
    with pytest.raises(fido_mds.BlobError, match="took too long"):
        fido_mds.fetch(URL)
    assert time.monotonic() - started < 3


def test_whatever_keeps_the_kept_blob_from_being_read_leaves_no_blob(no_metadata, monkeypatch):
    no_metadata.publish()

    def unexpected(data, now=None):
        raise TypeError("a failure this module did not foresee")
    monkeypatch.setattr(fido_mds, "parse", unexpected)
    fido_mds.forget()
    assert fido_mds.kept() is None and fido_mds.status()["valid"] is False
