#!/bin/sh
# Start the app on localhost and Caddy in front of it. If either stops, the machine stops, and Fly restarts it;
# a stop signal (Fly sends SIGINT, docker SIGTERM) is passed on to both, so they shut down cleanly.
set -eu
umask 077   # the database, the signing key and the certificates are readable by this user only
mkdir -p "${OPEN_EDITION_DATA:-/data}/caddy"
export XDG_DATA_HOME="${OPEN_EDITION_DATA:-/data}/caddy"   # certificates survive a restart on the volume
# a socket message is at most 1 MiB: the workspace agent takes a terminal's at most 256 KB, and the first one, the
# sign-in, is read before anyone is known
python -m uvicorn proofpane_open.main:app --host 127.0.0.1 --port 8000 --proxy-headers --ws-max-size 1048576 &
APP=$!
caddy run --config /app/open_edition/Caddyfile --adapter caddyfile &
PROXY=$!
stop() {
    kill -TERM "$APP" "$PROXY" 2>/dev/null || true
    wait "$APP" "$PROXY" 2>/dev/null || true
    exit 0
}
trap stop INT TERM
# wait for whichever exits first, then stop the other
while kill -0 "$APP" 2>/dev/null && kill -0 "$PROXY" 2>/dev/null; do sleep 1; done
kill "$APP" "$PROXY" 2>/dev/null || true
exit 1
