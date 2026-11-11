"""What tasks are held to on both sides:
the app (proofpane_open/ade/task_shapes.py) and the workspace, which runs every attempt
(proofpane_workspace/task_shapes.py). The two copies are byte for byte the same and import nothing but the standard
library (tests/test_shared_copies.py), since the app and the workspace import nothing of each other
(tests/test_workspace_boundary.py). A change is made to both at once.

The ids, the plan-string table and the groups it gives, the bounds of a task and of its packet, the outcomes of the
owner's Resolve, the windows, the effect classes, the resume checks, the task's tools, the variables no attempt is
given, the Claude Code a task's attempt runs, and the two tagged digests."""

import hashlib
import re

# ---- ids (fullmatch only) -------------------------------------------------------------------------------------------

# a task, an offer, a custody row, an account-owner row, a checkpoint and an effect: secrets.token_hex(16), so that
# every id a passkeys.CHANGE purpose names matches its _ID (passkeys.py:159)
TASK_ID = re.compile(r"[0-9a-f]{32}")
# a run (an attempt is a run) and an agent: secrets.token_hex(6), as ade/agents.py makes them
RUN_ID = re.compile(r"[0-9a-f]{12}")
# a Claude Code account of the workspace: its default folder, or an added one (the workspace's accounts.py)
ACCOUNT = re.compile(r"default|[0-9a-f]{12}")
COMMIT = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")

KEY_PROVIDERS = ("anthropic", "kimi", "minimax", "deepseek")

# ---- plans and groups -----------------------------------------------------------------------------------------------

# a start's report gives the plan as a display string; `claude auth status --json` gives a short word: one table, used
# both ways, so a confirmation keeps an orgId only where the two agree
PLAN_WORDS = {"Claude Pro": "pro", "Claude Max": "max", "Claude Team": "team", "Claude Enterprise": "enterprise"}
PERSONAL_PLANS = ("Claude Pro", "Claude Max")
SEAT_PLANS = {"Claude Team": "team", "Claude Enterprise": "enterprise"}
PERSONAL = "personal"


def plan_word(display):
    """The short word `auth status` gives for the report's display string, or None for any other plan (`Claude API`,
    none): such an account is in no group."""
    return PLAN_WORDS.get(display) if isinstance(display, str) else None


def group_of(display, org_id):
    """(group, kind) of a confirmed account: ("personal", "personal") for a Pro or Max plan, whatever its address or
    organisation; ("org:<orgId>", "team" or "enterprise") for a seat, one group per confirmed orgId; None for any other
    plan, or a seat with no orgId kept (not eligible)."""
    if display in PERSONAL_PLANS:
        return PERSONAL, PERSONAL
    if display in SEAT_PLANS and isinstance(org_id, str) and org_id:
        return "org:" + org_id, SEAT_PLANS[display]
    return None


# ---- a task's bounds ------------------------------------------------------------------------------------------------

STEPS_MAX = 20
TITLE_MAX = 120                         # characters
INSTRUCTIONS_MAX = 24576                # bytes of the goal, steps and decisions as the packet holds them (canonical)
LIST_MAX, LIST_ITEM_MAX = 20, 200       # protected paths, external commands: how many, and characters each
TEXT_MAX = 2000                         # bytes of one note, question, decision or last words
NAME_MAX = 300                          # bytes of one path or target in a packet
CHECKPOINTS_PER_ATTEMPT, CHECKPOINTS_PER_TASK = 20, 200
CHECK_RUNS_PER_ATTEMPT, CHECK_SECONDS, CHECK_TAIL = 10, 600, 4096
ACK_TURNS, ACK_SECONDS = 3, 300

PACKET_SCHEMA = "ade.resume.v1"
MANIFEST_SCHEMA = "ade.checkpoint.v1"
PACKET_MAX = 49152
PACKET_BUDGETS = {"instructions": 24576, "fixed": 2048, "model": 10240, "observed": 12288}

# ---- the owner's Resolve --------------------------------------------------------------------------------------------

# an unknown effect settled by the owner, and the worktree's state after an attempt kept or discarded
EFFECT_OUTCOMES = ("happened", "not_happened")
WORKTREE_OUTCOMES = ("keep", "discard")

# ---- time -----------------------------------------------------------------------------------------------------------

FRESH_SECONDS = 900                     # room "on fresh evidence": an allowed window or a working attempt this recent

# ---- windows --------------------------------------------------------------------------------------------------------

WINDOWS = ("five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet", "overage")
WEEKLY = ("seven_day", "seven_day_opus", "seven_day_sonnet")
WINDOW_STATUSES = ("allowed", "allowed_warning", "rejected")

# ---- checkpoints, effects and the resume check ----------------------------------------------------------------------

CHECKPOINT_KINDS = ("zero", "progress", "step")
CHECKPOINT_STATES = ("pending", "complete", "dropped")
EFFECT_CLASSES = ("read", "local", "unclassified", "git_remote", "github", "own_tool", "external")
OUTWARD = ("git_remote", "github", "external")
EFFECT_STATES = ("done", "not_done", "unknown", "ran")
RESUME_CHECKS = ("fence", "packet", "backend", "watch", "worktree", "effects", "memory", "ack", "start", "settings")
# which failure of a start it was: the account's own (the relay moves on), the same on every account
# (the owner looks), or what the settings loaded at the unfreezing restart named (the owner removes it)
START_FAILURES = ("account", "same_everywhere", "settings")

# the task's tools on the workspace's own in-process MCP server, named as Claude Code names them: the
# four the task adds, and ask_owner, which a run has already
TASK_TOOLS = ("mcp__proofpane__task_state", "mcp__proofpane__resume_ack", "mcp__proofpane__save_progress",
              "mcp__proofpane__complete_step", "mcp__proofpane__ask_owner")
FROZEN_TOOLS = ("mcp__proofpane__task_state", "mcp__proofpane__resume_ack")

# never in an attempt's environment, whatever started the workspace
REFUSED_ENV = ("CLAUDE_SECURESTORAGE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY",
               "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE", "ANTHROPIC_BASE_URL")

# ---- the Claude Code a task's attempt runs --------------------------------------------------------------------------

# the workspace's image's own claude, never the newer build its updater keeps in use; under the local provider the Agent
# SDK's bundled one at the workspace's pin (proofpane_workspace/requirements.txt), never the PATH's
TASK_CLAUDE_VERSION = "2.1.274"

# ---- digests --------------------------------------------------------------------------------------------------------

CHECKPOINT_TAG = b"proofpane-checkpoint/1"
RESUME_TAG = b"proofpane-resume/1"


def tagged_digest(tag, data):
    """sha256(tag || 0x00 || data) as hex: a checkpoint's manifest (CHECKPOINT_TAG) and a packet (RESUME_TAG), each
    over its RFC 8785 bytes; a risk point's text too, under the app's own tag (risk_texts.TEXT_TAG)."""
    return hashlib.sha256(tag + b"\x00" + data).hexdigest()
