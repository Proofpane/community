"""The agents' tables in a store made when agents first came, before their later columns (store.py's AGENTS_ADDED):
it opens, its rows keep what they had, and it gets every later column and the index on one of them."""

import sqlite3

FIRST = """
CREATE TABLE ade_agents (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, grp TEXT, brief TEXT NOT NULL,
    repo TEXT, base_ref TEXT, keep_worktree INTEGER NOT NULL DEFAULT 1, account TEXT,
    chat_settings TEXT NOT NULL DEFAULT '{}', allow TEXT NOT NULL DEFAULT '[]', limits TEXT NOT NULL DEFAULT '{}',
    brief_version INTEGER NOT NULL DEFAULT 1, paused INTEGER NOT NULL DEFAULT 0, paused_reason TEXT, retired_at INTEGER,
    from_session TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, mirrored_at INTEGER);
CREATE TABLE ade_agent_runs (run_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, user_id TEXT NOT NULL,
    state TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0, reason TEXT, cause TEXT, trigger TEXT, queued_at INTEGER,
    started_at INTEGER, ended_at INTEGER, base TEXT, head TEXT, branch TEXT, brief_version INTEGER, question TEXT,
    steps INTEGER NOT NULL DEFAULT 0, requests INTEGER, cost_est_usd REAL, tokens TEXT, last_text TEXT, bytes INTEGER,
    pruned INTEGER NOT NULL DEFAULT 0, thumbs TEXT, thumbs_note TEXT, seq INTEGER, updated_at INTEGER NOT NULL);
CREATE TABLE ade_agent_sync (user_id TEXT PRIMARY KEY, seq INTEGER NOT NULL DEFAULT 0, polled_at INTEGER);
INSERT INTO ade_agents (id, user_id, name, brief, created_at, updated_at) VALUES ('a1a2a3a4a5a6', 'u', 'T', 'b', 1, 1);
INSERT INTO ade_agent_runs (run_id, agent_id, user_id, state, updated_at) VALUES ('aaaaaaaaaaaa', 'a1a2a3a4a5a6', 'u',
    'done', 1);
"""


def test_a_store_made_when_agents_first_came_opens_and_gets_their_later_columns(env):
    from proofpane_open import config, store
    conn = sqlite3.connect(config.db_path())
    conn.executescript(FIRST)
    conn.commit()
    conn.close()
    with store.connect() as db:
        for table, column, _ in store.AGENTS_ADDED:
            assert column in {r[1] for r in db.execute(f"PRAGMA table_info({table})")}, (table, column)
        run = db.execute("SELECT state, loop, depth, judge_asked FROM ade_agent_runs WHERE run_id = 'aaaaaaaaaaaa'").fetchone()
        assert tuple(run) == ("done", 0, 0, 0)
        assert db.execute("SELECT improve, max_dispatch_per_run FROM ade_agents").fetchone()[:] == (0, 5)
        assert "ade_agent_runs_parent" in {r[1] for r in db.execute("PRAGMA index_list(ade_agent_runs)")}
    with store.connect() as db:                                          # and again, as each request opens it
        assert db.execute("SELECT COUNT(*) FROM ade_agent_runs").fetchone()[0] == 1
