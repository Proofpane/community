"""Settings, read from the environment each time they are needed, so a test can set them per test."""

import os

TOKEN_TTL_SECONDS = 7 * 24 * 3600
INVITE_TTL_SECONDS = 24 * 3600
DEVICE_SIGNIN_TTL_SECONDS = 600     # a device's request to be signed in by another, signed-in one
DEVICE_SIGNIN_POLL_SECONDS = 2      # how often that device may ask whether it was approved
DEVICE_SIGNINS_PENDING = 1000       # requests open at once, across the deployment
DEVICE_SIGNINS_PER_NETWORK = 5      # and from one network (an IPv4 address, an IPv6 /64)
DEVICE_SIGNINS_PER_WIDE_NETWORK = 25  # and from a wider one (an IPv4 /24, an IPv6 /48)
MIN_PASSWORD = 10
MAX_PASSWORD = 256
MAX_TEXT = 64 * 1024          # a title or description
MAX_DIFF = 1024 * 1024        # a pasted unified diff
MAX_EVIDENCE = 4 * 1024 * 1024  # an uploaded manifest or chain file


def data_dir():
    path = os.environ.get("OPEN_EDITION_DATA", "/data")
    os.makedirs(path, exist_ok=True)
    return path


def db_path():
    return os.path.join(data_dir(), "open_edition.db")


def token_secret():
    """The secret tokens are signed with. At least 32 bytes; the app refuses to start without it."""
    secret = os.environ.get("OPEN_EDITION_TOKEN_SECRET", "")
    if len(secret.encode("utf-8")) < 32:
        raise RuntimeError("OPEN_EDITION_TOKEN_SECRET must be set, at least 32 bytes")
    return secret.encode("utf-8")


def admins():
    """Emails that may create invitations. Being listed gives no account: an admin registers from an invitation too."""
    raw = os.environ.get("OPEN_EDITION_ADMINS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


# the pages of the apps built from this repository's UI (Tauri's own: tauri://localhost on iOS and macOS,
# http(s)://tauri.localhost on Android and Windows)
APP_ORIGINS = ("tauri://localhost", "http://tauri.localhost", "https://tauri.localhost")


def app_origins():
    """The origins, besides the deployment's own site, whose pages may call its API and open its sockets: the phone
    app's. OPEN_EDITION_APP_ORIGINS (comma-separated) replaces them; empty, there are none. Every request still needs
    a sign-in in its Authorization header (a socket in its first message), and no answer sets a cookie, so this lets
    such a page read what its own token may read, and nothing more."""
    raw = os.environ.get("OPEN_EDITION_APP_ORIGINS")
    if raw is None:
        return APP_ORIGINS
    return tuple(o.strip() for o in raw.split(",") if o.strip())


def public_url():
    """The deployment's own address, as its people reach it: the base of every invitation link, the site passkeys are
    made for and the host the session channel's signatures cover. It has no default (a deployment that set none would
    hand out links to another deployment's address and bind its passkeys to another's name): the app refuses to start
    without it."""
    url = os.environ.get("OPEN_EDITION_PUBLIC_URL", "").rstrip("/")
    if not url:
        raise RuntimeError("OPEN_EDITION_PUBLIC_URL must be set: this deployment's own address, such as "
                           "https://proofpane.example.org")
    return url


def proofpane_addresses():
    """The Proofpane addresses an account may pair this deployment with (ade/proofpane.py), as the operator wrote them:
    OPEN_EDITION_PROOFPANE_ADDRESSES, comma-separated; unset, Proofpane's own, https://api.proofpane.com; empty, none.
    The deployment names them, never the person pairing: this server spends the code at the address, and later posts
    each linked reply's usage there, so an address typed on the page would send its requests wherever it said, this
    machine and its private network among them. ade/proofpane.py keeps those of the form an address may have."""
    raw = os.environ.get("OPEN_EDITION_PROOFPANE_ADDRESSES")
    if raw is None:
        return ("https://api.proofpane.com",)
    return tuple(a.strip().rstrip("/") for a in raw.split(",") if a.strip().rstrip("/"))


def mds_url():
    """Where the FIDO Metadata Service's blob is fetched from (fido_mds.py); empty, nothing is fetched."""
    return os.environ.get("OPEN_EDITION_MDS_URL", "https://mds3.fidoalliance.org/").strip()
