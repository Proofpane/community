"""This deployment's signing key: a composite ML-DSA-65 + Ed25519 key from the open package `proofpane_sign`.

It is created on first use in the data directory, mode 0600, and kept there; its public key is served so anyone can
check what this deployment signed, offline, with the open package.
"""

import os
import threading

from . import config, paths  # noqa: F401  (paths puts the open packages on sys.path)
from proofpane_sign import composite
from proofpane_sign import document

_lock = threading.Lock()


def _key_paths():
    root = config.data_dir()
    return os.path.join(root, "signing.key"), os.path.join(root, "signing.pub")


def private_key():
    priv, pub = _key_paths()
    with _lock:
        if not os.path.exists(priv):
            sk = composite.generate()
            document.write_private_key(priv, sk)
            document.write_public_key(pub, composite.public_key(sk))
        return document.read_private_key(priv)


def public_key():
    sk = private_key()
    return composite.public_key(sk)
