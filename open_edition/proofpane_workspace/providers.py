"""Other providers' models in the unmodified Claude Code: Kimi K3 (Moonshot AI), MiniMax M3 and DeepSeek, each through
its own Anthropic-compatible endpoint with the person's own key, the way each vendor documents it (read 2026-10-01:
platform.kimi.ai/docs/guide/claude-code-kimi, platform.minimax.io/docs/token-plan/claude-code,
api-docs.deepseek.com/quick_start/agent_integrations/claude_code). DeepSeek's page was read again on 2026-10-18 and
gave the same values, which tests/test_workspace_providers.py holds as the page gives them. A session of one of them is
a `claude` session, under Claude Code's own settings and so under the workspace's gate, as any other.

The key is kept in the workspace user's home, in ~/.proofpane/model-keys.json (0600, its folder 0700), and never
returned by the workspace: the page learns only whether one is set. It does not go into the session's environment,
which every command the agent runs inherits; Claude Code takes it from `apiKeyHelper` (model_key.py), which it runs
itself and sends as the `X-Api-Key` and `Authorization: Bearer` headers, above a Claude sign-in (Claude Code's
authentication reference, read 2026-10-02). The vendor's endpoint and models are pinned in the same settings file:
a settings file's `env` overwrites the launch environment, and a repository's own `.claude` settings may set
`ANTHROPIC_BASE_URL`, which would send the key elsewhere; the session's `--settings` file outranks them. The gate keeps
the file and the helper from a session, by name, as it does the programs' sign-ins.

Stdlib only: the agent runs it, and model_key.py, which Claude Code runs, imports it.
"""

import json
import os
import re
import shlex

CATALOG = {
    "kimi": {
        "label": "Kimi K3", "vendor": "Moonshot AI", "docs": "https://platform.kimi.ai/docs/guide/claude-code-kimi",
        "env": {"ANTHROPIC_BASE_URL": "https://api.moonshot.ai/anthropic", "ANTHROPIC_MODEL": "kimi-k3[1m]",
                "ANTHROPIC_DEFAULT_OPUS_MODEL": "kimi-k3[1m]", "ANTHROPIC_DEFAULT_SONNET_MODEL": "kimi-k3[1m]",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": "kimi-k2.7-code", "ANTHROPIC_DEFAULT_FABLE_MODEL": "kimi-k3[1m]",
                "CLAUDE_CODE_SUBAGENT_MODEL": "kimi-k3[1m]", "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "1000000",
                "CLAUDE_CODE_EFFORT_LEVEL": "max"}},
    "minimax": {
        "label": "MiniMax M3", "vendor": "MiniMax", "docs": "https://platform.minimax.io/docs/token-plan/claude-code",
        "env": {"ANTHROPIC_BASE_URL": "https://api.minimax.io/anthropic", "ANTHROPIC_MODEL": "MiniMax-M3[1m]",
                "ANTHROPIC_DEFAULT_OPUS_MODEL": "MiniMax-M3[1m]", "ANTHROPIC_DEFAULT_SONNET_MODEL": "MiniMax-M3[1m]",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": "MiniMax-M3[1m]", "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "1000000"}},
    "deepseek": {
        "label": "DeepSeek", "vendor": "DeepSeek",
        "docs": "https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code/",
        "env": {"ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic", "ANTHROPIC_MODEL": "deepseek-flash[1m]",
                "ANTHROPIC_DEFAULT_OPUS_MODEL": "deepseek-flash[1m]", "ANTHROPIC_DEFAULT_SONNET_MODEL": "deepseek-flash[1m]",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-flash", "CLAUDE_CODE_SUBAGENT_MODEL": "deepseek-flash",
                "CLAUDE_CODE_EFFORT_LEVEL": "max", "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "786432"}},
}
# a key kept only for a task's attempt pressed onto it (task_runs.py): Anthropic's own API, its address
# pinned in the attempt's settings as each provider's is; not among the other providers' models a session offers
KEY_ONLY = {
    "anthropic": {"label": "Anthropic API", "vendor": "Anthropic", "env": {"ANTHROPIC_BASE_URL": "https://api.anthropic.com"}},
}
STORE = os.path.join(".proofpane", "model-keys.json")      # in the workspace user's home
MAX_KEY = 400
_KEY = re.compile(r"^[\x21-\x7e]{8,%d}$" % MAX_KEY)          # printable, no spaces: what an API key is


class ProviderError(ValueError):
    """A request the person can correct: shown as it is."""


def store_path(home):
    return os.path.join(home, STORE)


def keys(home):
    """The keys kept, by provider; a file that cannot be read, or holds anything else, keeps none."""
    try:
        with open(store_path(home), encoding="utf-8") as fh:
            got = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(got, dict):
        return {}
    return {p: k for p, k in got.items() if (p in CATALOG or p in KEY_ONLY) and isinstance(k, str) and _KEY.match(k)}


def _write(home, kept):
    folder = os.path.dirname(store_path(home))
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    tmp = store_path(home) + ".tmp"
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(kept, fh)
    os.replace(tmp, store_path(home))


def save(home, provider, key):
    if provider not in CATALOG and provider not in KEY_ONLY:
        raise ProviderError("No such model provider here.")
    key = key.strip() if isinstance(key, str) else key
    if not isinstance(key, str) or not _KEY.match(key):
        raise ProviderError(f"An API key is 8 to {MAX_KEY} printable characters, with no spaces.")
    kept = keys(home)
    kept[provider] = key
    _write(home, kept)
    return listing(home)


def remove(home, provider):
    if provider not in CATALOG and provider not in KEY_ONLY:
        raise ProviderError("No such model provider here.")
    kept = keys(home)
    if kept.pop(provider, None) is not None:
        _write(home, kept)
    return listing(home)


def listing(home):
    """What the page may know: each provider, and whether a key is set; never the key."""
    kept = keys(home)
    return {"providers": [{"id": p, "label": c["label"], "vendor": c["vendor"], "docs": c["docs"],
                           "model": c["env"]["ANTHROPIC_MODEL"], "set": p in kept} for p, c in CATALOG.items()]}


def helper_command(python, directory, provider):
    """The `apiKeyHelper` that gives Claude Code the key, run by Claude Code itself through /bin/sh, with Python's -E,
    -s and -X utf8, as the hooks are (settings.ISOLATED): nothing a repository's settings or a session's files set runs
    in it, and a locale a settings file names whose character set Python has no codec for does not end its
    interpreter's start (measured on Linux with three such glibc locales, not on a Mac)."""
    return " ".join([shlex.quote(python), "-E", "-s", "-X", "utf8",
                     shlex.quote(os.path.join(directory, "model_key.py")), shlex.quote(provider)])
