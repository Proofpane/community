"""The workspace image, read from its files. What the image and the fly provider must agree on (the port, the
boot files, the home, the idle time) is held here, as are the image's own promises: the programs installed as
published at pinned versions, the gates as root's managed settings, the network closed but for the TLS front before
anything starts, and no secret on a command line. Building and running the image is checked with Docker
(test_workspace_image_docker.py); `caddy adapt` checks the Caddyfile here when a Caddy binary is at hand."""

import json
import os
import re
import subprocess
import sys

import pytest

from conftest import ROOT
from fly_fixtures import caddy_binary
from proofpane_open.ade import fly_provider as fly
from proofpane_workspace import settings

IMAGE = os.path.join(ROOT, "workspace_image")


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as fh:
        return fh.read()


def code(*parts):
    """A shell script without its comment lines, so a step that is commented out does not count as there."""
    return "\n".join(line for line in read(*parts).splitlines() if not line.lstrip().startswith("#"))


def test_the_image_pins_what_it_installs():
    dockerfile = read(IMAGE, "Dockerfile")
    froms = re.findall(r"^FROM (\S+)", dockerfile, re.M)
    assert froms == ["caddy:2.11.4-alpine", "node:22.23.3-trixie-slim"]
    assert re.search(r"^ARG CLAUDE_CODE_VERSION=\d+\.\d+\.\d+$", dockerfile, re.M)
    assert re.search(r"^ARG CODEX_VERSION=\d+\.\d+\.\d+$", dockerfile, re.M)
    assert '"@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}"' in dockerfile
    assert '"@openai/codex@${CODEX_VERSION}"' in dockerfile
    assert re.search(r"^ARG GEMINI_CLI_VERSION=\d+\.\d+\.\d+$", dockerfile, re.M)
    assert re.search(r"^ARG OPENCODE_VERSION=\d+\.\d+\.\d+$", dockerfile, re.M)
    assert '"@google/gemini-cli@${GEMINI_CLI_VERSION}"' in dockerfile and '"opencode-ai@${OPENCODE_VERSION}"' in dockerfile
    assert re.search(r"^ARG GROK_VERSION=\d+\.\d+\.\d+$", dockerfile, re.M)
    assert '"@xai-official/grok@${GROK_VERSION}"' in dockerfile and "&& grok --version" in dockerfile
    assert re.search(r"^ARG QWEN_CODE_VERSION=\d+\.\d+\.\d+$", dockerfile, re.M)
    assert '"@qwen-code/qwen-code@${QWEN_CODE_VERSION}"' in dockerfile and "&& qwen --version" in dockerfile
    assert re.search(r"^ARG KIMI_CODE_VERSION=\d+\.\d+\.\d+$", dockerfile, re.M)
    assert re.search(r"^ARG MINIMAX_CODE_VERSION=\d+\.\d+\.\d+$", dockerfile, re.M)
    assert '"@moonshot-ai/kimi-code@${KIMI_CODE_VERSION}"' in dockerfile
    assert '"@minimax-ai/code@${MINIMAX_CODE_VERSION}"' in dockerfile and "&& kimi --version && mcode --version" in dockerfile
    for line in read(ROOT, "proofpane_workspace", "requirements.txt").splitlines():
        if line and not line.startswith("#"):
            assert re.fullmatch(r"[A-Za-z0-9_.-]+==[0-9][0-9A-Za-z.]*", line), line


def test_the_readme_and_the_docker_test_name_the_codex_the_image_pins():
    # the pinned Codex where it is named besides, so that a new pin cannot leave one of them behind
    pinned = re.search(r"^ARG CODEX_VERSION=(\d+\.\d+\.\d+)$", read(IMAGE, "Dockerfile"), re.M).group(1)
    assert f"Codex {pinned}," in read(ROOT, "README.md")
    assert f'"codex-cli {pinned}"' in read(ROOT, "tests", "test_workspace_image_docker.py")


def test_gh_is_github_s_own_release_at_a_pinned_version_and_digest():
    dockerfile = read(IMAGE, "Dockerfile")
    assert re.search(r"^ARG GH_VERSION=\d+\.\d+\.\d+$", dockerfile, re.M)
    assert re.search(r"^ARG GH_SHA256=[0-9a-f]{64}$", dockerfile, re.M)
    assert ('"https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz"'
            in dockerfile)
    assert 'echo "${GH_SHA256}  /tmp/gh.tar.gz" | sha256sum -c -' in dockerfile


def test_the_gates_are_root_s_managed_settings_made_by_the_settings_module():
    dockerfile = read(IMAGE, "Dockerfile")
    assert ("/usr/bin/python3 -m proofpane_workspace.settings --managed --python /usr/bin/python3 \\\n"
            "        --dir /opt/proofpane/proofpane_workspace > /etc/claude-code/managed-settings.json") in dockerfile
    assert "chmod 0644 /etc/claude-code/managed-settings.json" in dockerfile
    assert not re.search(r"^USER ", dockerfile, re.M)       # start.sh runs as root and drops to each user
    managed = settings.claude_settings("/usr/bin/python3", "/opt/proofpane/proofpane_workspace", managed=True)
    hook = managed["hooks"]["PreToolUse"][0]["hooks"][0]
    assert (hook["command"], hook["args"]) == ("/usr/bin/python3",
                                               ["-E", "-s", "-X", "utf8", "/opt/proofpane/proofpane_workspace/gate.py"])


def test_the_build_sees_only_the_agent_and_the_image_s_own_files():
    # the agent's package, the plugin it gives each claude start (core_plugin/), the sessions' approval hook
    # (ade_hook.py: one file of the standard library), and the image's own files
    lines = [l for l in read(IMAGE, "Dockerfile.dockerignore").splitlines() if l and not l.startswith("#")]
    assert lines[0] == "*" and {l for l in lines if l.startswith("!")} == {"!proofpane_workspace", "!core_plugin",
                                                                          "!ade_hook.py", "!workspace_image"}


def test_the_image_carries_the_approval_hook_as_root_s_where_the_agent_names_it():
    # the file beside the agent's package, which workspace.ade_hook_settings names from its own place
    dockerfile = read(IMAGE, "Dockerfile")
    assert re.search(r"^COPY ade_hook\.py /opt/proofpane/ade_hook\.py$", dockerfile, re.M)
    assert re.search(r"^COPY proofpane_workspace /opt/proofpane/proofpane_workspace$", dockerfile, re.M)
    assert os.path.isfile(os.path.join(ROOT, "ade_hook.py"))                # the same place in the repository
    imports = {m.group(1) for m in re.finditer(r"^(?:import|from) (\w+)", read(ROOT, "ade_hook.py"), re.M)}
    assert imports and imports <= set(sys.stdlib_module_names)               # the standard library alone
    # and its own program (settings.ADE_HOOK_START), in the package the image copies whole, executable as kept: what
    # the hook's settings name as its command, so that no other hook's command is the hold's
    start = os.path.join(ROOT, "proofpane_workspace", settings.ADE_HOOK_START)
    assert os.path.isfile(start) and os.access(start, os.X_OK)
    assert subprocess.run(["sh", "-n", start]).returncode == 0
    text = read(os.path.dirname(start), settings.ADE_HOOK_START)
    assert 'exec /usr/bin/env -i PROOFPANE_ADE_URL="$3" PROOFPANE_ADE_TOKEN_FILE="$4"' in text


def test_start_sh_puts_the_hook_s_two_files_where_the_workspace_user_reads_them_and_changes_nothing():
    start = code(IMAGE, "start.sh")
    folder = settings.ADE_HOOK_FOLDER
    assert folder == "/run/proofpane/ade" and "RUN=/run/proofpane" in start
    assert 'mkdir -p "$RUN/ade"' in start and 'chown root:ws "$RUN/ade"' in start and 'chmod 750 "$RUN/ade"' in start
    names = (fly.ADE_URL_FILE, fly.ADE_TOKEN_FILE)
    assert names == (settings.ADE_URL_FILE, settings.ADE_TOKEN_FILE) == ("ade-url", "ade-hook-token")
    assert f"for f in {' '.join(names)}; do" in start
    assert 'install -o root -g ws -m 0440 "$BOOT/$f" "$RUN/ade/$f"' in start
    assert re.search(r'^rm -f "\$BOOT/ade-hook-token"$', start, re.M)
    agent = start[start.index("setpriv --reuid=ws"):start.index("AGENT=$!")]
    assert 'PP_WORKSPACE_ADE_HOOK="$RUN/ade"' in agent and "--init-groups" in agent       # ws is in the group
    # the four the provider always writes are still the ones the machine waits for: the hook's two are not
    assert "for f in tls.crt tls.key app.crt secret; do" in start
    assert subprocess.run(["sh", "-n", os.path.join(IMAGE, "start.sh")]).returncode == 0


def test_the_plugin_is_root_s_beside_the_agent_where_the_agent_finds_it():
    dockerfile = read(IMAGE, "Dockerfile")
    assert re.search(r"^COPY core_plugin /opt/proofpane/core_plugin$", dockerfile, re.M)
    from proofpane_workspace import core_plugin
    # the agent's package is copied to /opt/proofpane/proofpane_workspace, so its PLUGIN is the folder copied above
    assert os.path.relpath(core_plugin.PLUGIN, os.path.dirname(core_plugin.HERE)) == "core_plugin"


def _chain(rules, name):
    """The rule lines of one chain of firewall.nft, up to the line that closes it; comments and blank lines left out."""
    lines = [line.strip() for line in rules.splitlines()]
    first = lines.index(f"chain {name} {{") + 1
    last = lines.index("}", first)
    return [line for line in lines[first:last] if line and not line.startswith("#")]


def test_the_firewall_is_loaded_before_anything_starts():
    start = code(IMAGE, "start.sh")
    assert re.search(r"^nft -f /opt/proofpane/firewall\.nft$", start, re.M)
    nft = start.index("nft -f /opt/proofpane/firewall.nft")
    assert nft < start.index("setpriv") and nft < start.index("caddy run")


def test_in_only_the_tls_front_and_fly_s_ssh():
    rules = read(IMAGE, "firewall.nft")
    assert _chain(rules, "input") == [
        "type filter hook input priority filter; policy drop;", 'iif "lo" accept',
        "ct state established,related accept", "ct state invalid drop", "meta l4proto { icmp, ipv6-icmp } accept",
        "tcp dport { 8443, 22 } accept"]


def test_out_nothing_new_into_fly_s_private_network_but_dns():
    rules = read(IMAGE, "firewall.nft")
    assert _chain(rules, "output") == [
        "type filter hook output priority filter; policy accept;", 'oif "lo" accept',
        "ct state established,related accept",
        "ip6 daddr fdaa::3 udp dport 53 accept", "ip6 daddr fdaa::3 tcp dport 53 accept",
        "ip6 daddr fdaa::/16 meta l4proto tcp reject with tcp reset", "ip6 daddr fdaa::/16 reject"]


def test_no_secret_is_on_a_command_line_and_each_program_runs_as_its_own_user():
    start = code(IMAGE, "start.sh")
    assert "PP_WORKSPACE_SECRET" not in start
    agent = start[start.index("setpriv --reuid=ws"):start.index("AGENT=$!")]
    assert '--secret-file "$RUN/agent/secret"' in agent and '--idle-exit "$IDLE"' in agent and "env -i" in agent
    assert "--no-new-privs" in agent and "--bounding-set=-all" in agent
    caddy = start[start.index("setpriv --reuid=pp-tls"):start.index("PROXY=$!")]
    assert "env -i" in caddy and "--no-new-privs" in caddy
    assert 'install -o pp-tls -g pp-tls -m 0400 "$BOOT/tls.key"' in start
    assert 'install -o ws -g ws -m 0400 "$BOOT/secret"' in start
    assert re.search(r'^rm -f "\$BOOT/tls\.key" "\$BOOT/secret"', start, re.M)
    assert subprocess.run(["sh", "-n", os.path.join(IMAGE, "start.sh")]).returncode == 0


def test_the_image_and_the_provider_agree():
    start, caddyfile = code(IMAGE, "start.sh"), read(IMAGE, "Caddyfile")
    s = fly.Settings("pp-ws", "img", "tok", "syd", 1, 256, 1, 600, False, 1, fly.API)
    cfg = fly.machine_config(s, "vol_x", fly.material("pp-ws"), fly.material("pp-ws")["cert"], "label")
    # the boot files the provider writes are the ones start.sh waits for, where it looks for them
    names = [os.path.basename(f["guest_path"]) for f in cfg["files"]]
    assert {os.path.dirname(f["guest_path"]) for f in cfg["files"]} == {fly.BOOT} and f"BOOT={fly.BOOT} " in start
    assert f"for f in {' '.join(names)}; do" in start
    # the idle time it sets is the one start.sh reads, and the volume is the home start.sh gives the user
    assert list(cfg["env"]) == ["PP_WORKSPACE_IDLE_SECONDS"] and '"${PP_WORKSPACE_IDLE_SECONDS:-1800}"' in start
    assert cfg["mounts"][0]["path"] == fly.HOME and f"HOME={fly.HOME} " in start and f"mkdir -p {fly.HOME}" in start
    # the port it connects to is Caddy's, and Caddy passes to the port the agent is started on
    assert f":{{$PP_LISTEN_PORT:{fly.PORT}}}" in caddyfile and "--port 7000" in start
    assert "reverse_proxy 127.0.0.1:{$PP_AGENT_PORT:7000}" in caddyfile


def test_the_tls_front_takes_only_tls_1_3_the_post_quantum_exchange_and_the_app_s_certificate():
    caddyfile = read(IMAGE, "Caddyfile")
    assert "admin off" in caddyfile and caddyfile.count("{$PP_LISTEN_PORT") == 1
    assert re.search(r"^\s*curves x25519mlkem768$", caddyfile, re.M)
    assert re.search(r"^\s*protocols tls1.3$", caddyfile, re.M)
    assert "mode require_and_verify" in caddyfile and "trust_pool file {$PP_TLS_DIR:/run/proofpane/tls}/app.crt" \
        in caddyfile


@pytest.mark.skipif(caddy_binary() is None, reason="needs Caddy: set PP_TEST_CADDY or put caddy on PATH")
def test_caddy_reads_the_tls_front_as_intended(tmp_path):
    out = subprocess.run([caddy_binary(), "adapt", "--config", os.path.join(IMAGE, "Caddyfile"), "--adapter",
                          "caddyfile"], capture_output=True, text=True, timeout=30,
                         env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)})
    assert out.returncode == 0, out.stderr
    cfg = json.loads(out.stdout)
    assert cfg["admin"]["disabled"] is True
    (server,) = cfg["apps"]["http"]["servers"].values()
    assert server["listen"] == [":8443"] and server["protocols"] == ["h1"]
    (policy,) = server["tls_connection_policies"]
    assert policy["curves"] == ["x25519mlkem768"] and policy["protocol_min"] == "tls1.3"
    assert policy["client_authentication"]["mode"] == "require_and_verify"
    assert policy["client_authentication"]["ca"]["pem_files"] == ["/run/proofpane/tls/app.crt"]
    (route,) = server["routes"]
    assert route["handle"][0]["upstreams"] == [{"dial": "127.0.0.1:7000"}]
