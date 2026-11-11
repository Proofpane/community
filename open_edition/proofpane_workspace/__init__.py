"""The workspace agent: runs inside a user's workspace, holds its repositories, worktrees and terminals, and answers
only the app that holds the workspace's secret. It imports nothing from `proofpane_open`, which holds the accounts
and the signing key: nothing of those is in a workspace."""

VERSION = "0.1.0.dev0"
