# The workspace image: one user's machine under the fly provider.
#
# It holds the official `claude`, `codex`, `gemini`, `opencode`, `grok` (Grok Build), `qwen` (Qwen Code), `kimi` (Kimi
# Code) and `mcode` (MiniMax Code) programs, installed from npm as published at the versions below and not changed,
# and GitHub's CLI, `gh`, as GitHub publishes it; the workspace agent (proofpane_workspace/) and the proofpane-core plugin
# it gives each claude start once the owner pairs the workspace with Proofpane (core_plugin/); Caddy in front of the agent (workspace_image/Caddyfile); and the first
# gates and the status line as Claude Code managed settings, and the same gates as Gemini CLI and Qwen Code system
# settings, as Codex managed hooks (/etc/codex/requirements.toml) and as Grok Build enforced hooks
# (/etc/grok/requirements.toml), owned by root, which a session cannot override.
# The user signs in inside a terminal, in each program's own flow; the programs keep their credentials in the user's
# home, on the machine's volume, and nothing of this image reads them.
#
# Built from open_edition/, since it needs the agent's package:
#     docker build -f open_edition/workspace_image/Dockerfile open_edition
FROM caddy:2.11.4-alpine AS caddy

FROM node:22.23.3-trixie-slim
ARG CLAUDE_CODE_VERSION=2.1.274
ARG CODEX_VERSION=0.163.0
ARG GEMINI_CLI_VERSION=0.62.0
ARG OPENCODE_VERSION=1.18.34
ARG GROK_VERSION=1.0.62
ARG QWEN_CODE_VERSION=0.24.7
ARG KIMI_CODE_VERSION=2.1.1
ARG MINIMAX_CODE_VERSION=0.6.1
ARG GH_VERSION=2.101.0
ARG GH_SHA256=9bca2d1c16825f109907a23307628a2f0698fbf99662b73a5cf0b020293072b8

# setpriv, which start.sh uses to run each program as its own user, comes with util-linux, which Debian always has
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git less procps curl openssh-client ripgrep \
        python3 python3-venv nftables \
    && rm -rf /var/lib/apt/lists/* \
    && setpriv --version && nft --version
# GitHub's CLI, as GitHub publishes it, checked against the digest its release lists: a `github` session signs in with
# it, and git clones the user's private repositories through its credential helper
RUN curl -fsSL -o /tmp/gh.tar.gz \
        "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz" \
    && echo "${GH_SHA256}  /tmp/gh.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/gh.tar.gz -C /tmp \
    && install -m 0755 "/tmp/gh_${GH_VERSION}_linux_amd64/bin/gh" /usr/local/bin/gh \
    && rm -rf /tmp/gh.tar.gz "/tmp/gh_${GH_VERSION}_linux_amd64" \
    && gh --version
RUN npm install -g --no-fund --no-audit "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
        "@openai/codex@${CODEX_VERSION}" "@google/gemini-cli@${GEMINI_CLI_VERSION}" "opencode-ai@${OPENCODE_VERSION}" \
        "@xai-official/grok@${GROK_VERSION}" "@qwen-code/qwen-code@${QWEN_CODE_VERSION}" \
    && npm cache clean --force \
    && claude --version && codex --version && gemini --version && opencode --version && grok --version \
    && qwen --version
# Kimi Code (Moonshot AI's) and MiniMax Code (MiniMax's) run their makers' own models on the person's own sign-in.
# Neither reads settings a root-owned file holds above the user's: their hooks are the user's own to change, so the
# gate does not count on them, and keeps their sign-ins from the other agents instead. MiniMax Code needs SQLite's
# prebuilt module for this Node and glibc, which its own check confirms as it installs.
RUN npm install -g --no-fund --no-audit "@moonshot-ai/kimi-code@${KIMI_CODE_VERSION}" \
        "@minimax-ai/code@${MINIMAX_CODE_VERSION}" \
    && npm cache clean --force \
    && kimi --version && mcode --version
# The Caddy image gives its binary the capability to bind low ports. Here Caddy binds 8443 and runs with no
# capabilities at all, and the kernel refuses to start a binary that asks for one it cannot have, so it is copied
# without it: a plain copy leaves the file's capability behind.
COPY --from=caddy /usr/bin/caddy /tmp/caddy
RUN install -m 0755 /tmp/caddy /usr/local/bin/caddy && rm /tmp/caddy && caddy version

# Caddy runs as pp-tls, the only user that can read the machine's TLS key; sessions run as ws, whose home is the
# volume. The image's own `node` user (1000) is left as it is.
RUN groupadd --system pp-tls \
    && useradd --system --gid pp-tls --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin pp-tls \
    && groupadd --gid 1001 ws \
    && useradd --uid 1001 --gid ws --home-dir /home/ws --no-create-home --shell /bin/bash ws

COPY proofpane_workspace/requirements.txt /opt/proofpane/requirements.txt
# The Claude Agent SDK's wheel carries a copy of Claude Code; the chat drives the claude installed above instead (the
# same version, with this image's managed settings and gates), so that copy is removed: one Claude Code on the machine
RUN python3 -m venv /opt/proofpane/venv \
    && /opt/proofpane/venv/bin/pip install --no-cache-dir -r /opt/proofpane/requirements.txt \
    && rm -f /opt/proofpane/venv/lib/python3*/site-packages/claude_agent_sdk/_bundled/claude \
    && /opt/proofpane/venv/bin/python -c "import claude_agent_sdk; print('claude-agent-sdk', claude_agent_sdk.__version__)"
COPY proofpane_workspace /opt/proofpane/proofpane_workspace
# root's, beside the agent's package, where core_plugin.PLUGIN finds it: a session's programs cannot change its hooks
COPY core_plugin /opt/proofpane/core_plugin
# the sessions' approval hook, root's beside the agent's package, where the agent names it to a claude
# terminal's and chat's settings (workspace.ade_hook_settings): standard library only, the owner's own hook's file
COPY ade_hook.py /opt/proofpane/ade_hook.py
COPY workspace_image/Caddyfile workspace_image/start.sh workspace_image/firewall.nft /opt/proofpane/
RUN find /opt/proofpane -name __pycache__ -prune -exec rm -rf {} + \
    && chmod 0755 /opt/proofpane/start.sh \
    && mkdir -p /etc/claude-code /etc/gemini-cli /etc/codex /etc/grok /etc/qwen-code /etc/proofpane/boot \
    && cd /opt/proofpane \
    && /usr/bin/python3 -m proofpane_workspace.settings --managed --python /usr/bin/python3 \
        --dir /opt/proofpane/proofpane_workspace > /etc/claude-code/managed-settings.json \
    && /usr/bin/python3 -m proofpane_workspace.settings --gemini --python /usr/bin/python3 \
        --dir /opt/proofpane/proofpane_workspace > /etc/gemini-cli/settings.json \
    && /usr/bin/python3 -m proofpane_workspace.settings --codex --python /usr/bin/python3 \
        --dir /opt/proofpane/proofpane_workspace > /etc/codex/requirements.toml \
    && /usr/bin/python3 -m proofpane_workspace.settings --grok --python /usr/bin/python3 \
        --dir /opt/proofpane/proofpane_workspace > /etc/grok/requirements.toml \
    && /usr/bin/python3 -m proofpane_workspace.settings --qwen --python /usr/bin/python3 \
        --dir /opt/proofpane/proofpane_workspace > /etc/qwen-code/settings.json \
    && chmod 0644 /etc/claude-code/managed-settings.json /etc/gemini-cli/settings.json /etc/codex/requirements.toml \
        /etc/grok/requirements.toml /etc/qwen-code/settings.json \
    && chmod 0700 /etc/proofpane/boot

WORKDIR /opt/proofpane
CMD ["/opt/proofpane/start.sh"]
