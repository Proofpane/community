"""The link client the ADE serves (CLOUD-LINK, queue 4.317): the exact bytes of its own open_edition/ade_link.py, at
GET /api/ade/link/client (link_routes.py), with no sign-in. A session on claude.ai, whose container holds no client,
fetches it from the host its link needs anyway, at this server's own release: the block the page gives it
(ui/src/cloudLink.ts) checks it against the sha256 the page's build computed from the same file (ui/vite.config.ts)
before it puts it where SETUP_A.md installs the client, so a server that is not the ADE cannot change the code the block
runs."""

import os

CLIENT_FILE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "ade_link.py"))


def client_bytes():
    """The client's bytes as this release holds them, read at each request."""
    with open(CLIENT_FILE, "rb") as fh:
        return fh.read()
