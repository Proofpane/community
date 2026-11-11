"""proofpane-open: Proofpane Community, the hosted open edition, a web front on the open packages under oss/.

It imports the open packages and no code of Proofpane's proprietary product. Its shell is its own: accounts created
only from one-time invitation links, sign-in with a token signed by a secret only this app holds.

Modules:
    paths    puts the open packages on sys.path when run from the source tree
    config   settings from the environment
    store    the SQLite tables: users and invitations
    auth     passwords (scrypt), tokens (HMAC-SHA256), invitations
    diff     a unified diff read into the files a Verify bundle lists
    tools    Verify, the offline checks, and signatures, each through the open packages
    signing  this deployment's composite (ML-DSA-65 + Ed25519) signing key
    deps     the access checks every route uses: a signed-in account, an admin
    ade      the ADE: the owner's coding sessions read from their channel and repositories (ade/__init__.py)
    build    the release and the commit this build was made from, kept in the image as it was built
    main     the FastAPI app
"""

VERSION = "0.1.0.dev0"
