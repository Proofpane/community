#!/bin/sh
# A workspace machine's start. The machine runs this as root. It puts the
# four boot files Fly writes from the machine's configuration where only their one user can read them, and the two of
# the sessions' approval hook where the workspace user reads them and can change nothing, makes the volume the
# workspace user's home on the first boot, then starts Caddy as its own user and the workspace agent as the workspace
# user, each with an environment built from nothing.
#
# When the agent stops by itself (after its idle time, code 0), Caddy is stopped and this exits with the agent's
# code: 0 stops the machine, which the fly provider starts again when the user comes back; any other code, and
# Caddy stopping, make Fly restart it. A stop signal (Fly sends SIGINT, docker SIGTERM) stops both cleanly.
set -eu
umask 077

BOOT=/etc/proofpane/boot            # tls.crt, tls.key, app.crt, secret, and ade-url and ade-hook-token when the
                                    # provider gives them: written by Fly at each boot
RUN=/run/proofpane
IDLE="${PP_WORKSPACE_IDLE_SECONDS:-1800}"
case "$IDLE" in
    ''|*[!0-9]*) echo "PP_WORKSPACE_IDLE_SECONDS is a whole number of seconds" >&2; exit 1 ;;
esac

# The firewall (firewall.nft), before anything else runs: in, only Caddy's port and Fly's own SSH, so a server a user
# starts in a session cannot be reached from another machine; out, nothing new into Fly's private network but DNS,
# so the user's code cannot reach the organization's other machines. If it cannot be loaded, the machine does not
# start.
nft -f /opt/proofpane/firewall.nft

for f in tls.crt tls.key app.crt secret; do
    [ -s "$BOOT/$f" ] || { echo "this machine was started without $BOOT/$f" >&2; exit 1; }
done
mkdir -p "$RUN/tls" "$RUN/caddy" "$RUN/agent"
chmod 755 "$RUN"
chown pp-tls:pp-tls "$RUN/tls" "$RUN/caddy"
chown ws:ws "$RUN/agent"
chmod 700 "$RUN/tls" "$RUN/caddy" "$RUN/agent"
install -o pp-tls -g pp-tls -m 0400 "$BOOT/tls.key" "$RUN/tls/tls.key"
install -o pp-tls -g pp-tls -m 0444 "$BOOT/tls.crt" "$RUN/tls/tls.crt"
install -o pp-tls -g pp-tls -m 0444 "$BOOT/app.crt" "$RUN/tls/app.crt"
install -o ws -g ws -m 0400 "$BOOT/secret" "$RUN/agent/secret"   # the agent reads it and deletes it
rm -f "$BOOT/tls.key" "$BOOT/secret"

# The sessions' approval hook: this app's address and the workspace's hook token, as the fly provider
# writes them (ade-url, ade-hook-token), put in a folder of root's that the workspace user reads and can change nothing
# in, the token's boot copy deleted. The agent is named the folder whether or not they came: its claude terminals and
# the owner's chats are given the hook either way, which refuses each gated command it has no address or token for.
mkdir -p "$RUN/ade"
chown root:ws "$RUN/ade"
chmod 750 "$RUN/ade"
for f in ade-url ade-hook-token; do
    if [ -s "$BOOT/$f" ]; then install -o root -g ws -m 0440 "$BOOT/$f" "$RUN/ade/$f"; fi
done
rm -f "$BOOT/ade-hook-token"

# The volume is the user's home: theirs from the first boot on. Only the directory itself is changed, never anything
# inside it, so nothing a session made there can turn this into a change elsewhere.
mkdir -p /home/ws
[ "$(stat -c %u /home/ws)" = "$(id -u ws)" ] || chown ws:ws /home/ws
chmod 700 /home/ws

setpriv --reuid=pp-tls --regid=pp-tls --clear-groups --inh-caps=-all --bounding-set=-all --no-new-privs \
    env -i PATH=/usr/local/bin:/usr/bin:/bin HOME="$RUN/caddy" XDG_DATA_HOME="$RUN/caddy" \
        XDG_CONFIG_HOME="$RUN/caddy" PP_TLS_DIR="$RUN/tls" \
    caddy run --config /opt/proofpane/Caddyfile --adapter caddyfile &
PROXY=$!
setpriv --reuid=ws --regid=ws --init-groups --inh-caps=-all --bounding-set=-all --no-new-privs \
    env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/home/ws USER=ws LOGNAME=ws SHELL=/bin/bash LANG=C.UTF-8 \
        PYTHONPATH=/opt/proofpane PYTHONDONTWRITEBYTECODE=1 PP_WORKSPACE_ADE_HOOK="$RUN/ade" \
    /opt/proofpane/venv/bin/python -m proofpane_workspace --root /home/ws/workspace --port 7000 \
        --secret-file "$RUN/agent/secret" --idle-exit "$IDLE" --idle-exit-after-runs 120 --claude-first-run \
        --claude-updates &
AGENT=$!

stop() {
    kill -TERM "$AGENT" "$PROXY" 2>/dev/null || true
    wait "$AGENT" "$PROXY" 2>/dev/null || true
    exit 0
}
trap stop INT TERM

set +e
while kill -0 "$AGENT" 2>/dev/null && kill -0 "$PROXY" 2>/dev/null; do sleep 1; done
if kill -0 "$PROXY" 2>/dev/null; then
    wait "$AGENT"
    CODE=$?
    kill -TERM "$PROXY" 2>/dev/null
    wait "$PROXY"
    exit "$CODE"
fi
kill -TERM "$AGENT" 2>/dev/null
wait "$AGENT"
exit 1
