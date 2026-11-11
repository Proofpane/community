"""Proofpane's MCP server for a session the workspace starts: the Proofpane daemon's own (`airgov_daemon mcp`), run in
the name the workspace was paired with. The workspace names this plugin (proofpane-core-daemon) with --plugin-dir only
on the person's own machine, only once it is paired, and only where it finds the daemon at one of the places the
daemon's installer looks for it first (DAEMONS, as the installer lists them; the installer falls back to wherever the
daemon runs from, its Mac app's own folder say, which is not looked for here); in a workspace machine, which has no
daemon, never.

This finds the daemon the same way and becomes it, with AIRGOV_CONFIG_DIR naming the workspace's pairing (the daemon's
config.json there is the workspace's record, ~/.proofpane/proofpane-core/), so that the tools the installed daemon lists
(bash, read, write, edit, glob, grep, listdir, search_compliance_docs, ingest_to_rag, and, where its Hermes module
loads, session_search, skills_list, skill_view and skill_manage) are governed and audited in that organisation: each
call audited, and what a tool returns put through its secret patterns. Before anything else, as the daemon does for any
paired server, each call is put to the organisation's Proofpane policy for the agent the server runs as: `default` here,
since nothing here sets CP_AGENT, and where the organisation has no policy for that agent Proofpane gives a placeholder
that refuses every tool (rule no_policy), so every call is refused, the daemon saying to add one in Proofpane's
/mcp-setup, Agent Policies. Bash, write and edit are held by default (the daemon's CP_HITL_TOOLS): a held call asks for
the owner's approval in Proofpane and is refused at once, and runs when it is made again after the owner approved it
there, the request living 300 seconds (the daemon does not wait for its answer). ingest_to_rag, which sends a file to
the organisation's documents, is not held. The plugin's hook holds this server's calls as it holds any (its holds read a
call's command, whatever the tool), so a command one of the owner's holds names, sent through this server's bash, is
asked about three times, as read from the code and not run: the hook holds the call, the daemon then holds its own
request and refuses the call, and the call made again, with a new id, is held by the hook again. The commands its tools
run have the daemon's environment, AIRGOV_CONFIG_DIR with it, so a command the owner approves can name the record's
folder. CP_VENDOR says which client called them. The server reads the pairing at each call: once the owner unpairs, a
server already running runs its tools with no approval and no audit until its session's claude starts again.

Two things the daemon does in this mode on its own are switched off (OFF), since nothing here asks for them and the
owner is told of neither when pairing: its import, at each start, of every MCP server the machine's other AI clients
(Claude Desktop, Cursor, Codex and the rest) configure, with their commands, arguments and environment values, into the
organisation's registry (AIRGOV_AUTO_IMPORT_MCP); and its poll of that registry every 10 seconds, which writes every
member's entries beside the pairing and starts the enabled ones on this machine (CP_REGISTRY_POLL_INTERVAL_S). No daemon
there, and this ends at once, saying so. Standard library only.
"""

import os
import sys

# where the daemon installs itself (the installer's own places, and ~/.local/bin), in its order
DAEMONS = ("/opt/proofpane/airgov_daemon", "/opt/homebrew/bin/airgov_daemon", "/usr/local/bin/airgov_daemon",
           "~/.local/bin/airgov_daemon")
RECORD = os.path.join(".proofpane", "proofpane-core")       # in the workspace user's home
OFF = {"AIRGOV_AUTO_IMPORT_MCP": "0", "CP_REGISTRY_POLL_INTERVAL_S": "0"}


def find(home=None):
    """The first of DAEMONS that is a file this user can run, or None."""
    home = home if home is not None else os.path.expanduser("~")
    for place in DAEMONS:
        path = os.path.join(home, place[2:]) if place.startswith("~/") else place
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def main():
    home = os.path.expanduser("~")
    daemon = find(home)
    if daemon is None:
        sys.stderr.write("The Proofpane daemon is not installed on this machine.\n")
        return 1
    env = dict(os.environ, AIRGOV_CONFIG_DIR=os.path.join(home, RECORD), CP_VENDOR="claude_code", **OFF)
    os.execve(daemon, [daemon, "mcp"], env)
    return 1


if __name__ == "__main__":
    sys.exit(main())
