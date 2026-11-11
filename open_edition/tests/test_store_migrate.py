"""Opening a store made before a column was added (store._migrate): the column is added once, even when two
connections open such a store at the same time."""

import sqlite3

import pytest

from proofpane_open import store


def old_store(path):
    """A store as an earlier release made it: every table, but without ade_connector_requests.browser."""
    with store.connect(str(path)):
        pass
    db = sqlite3.connect(str(path))
    db.execute("ALTER TABLE ade_connector_requests DROP COLUMN browser")
    db.commit()
    db.close()


def columns(path, table):
    db = sqlite3.connect(str(path))
    try:
        return [row[1] for row in db.execute(f"PRAGMA table_info({table})")]
    finally:
        db.close()


class Racing:
    """A connection whose read of a table's columns is followed, before anything else, by another connection adding
    the column: what two connections opening an old store at once can do."""

    def __init__(self, conn, path, table, column, error=None):
        self.conn, self.path, self.table, self.column, self.error = conn, path, table, column, error

    def execute(self, sql, *args):
        if self.error and sql.startswith(f"ALTER TABLE {self.table} "):
            raise self.error
        result = self.conn.execute(sql, *args)
        if sql == f"PRAGMA table_info({self.table})":
            result = list(result)
            other = sqlite3.connect(str(self.path))
            other.execute(f"ALTER TABLE {self.table} ADD COLUMN {self.column} TEXT")
            other.commit()
            other.close()
        return result

    def executescript(self, script):
        return self.conn.executescript(script)


def test_an_old_store_is_given_the_column_it_lacks(tmp_path):
    path = tmp_path / "s.db"
    old_store(path)
    assert "browser" not in columns(path, "ade_connector_requests")
    with store.connect(str(path)):
        pass
    assert columns(path, "ade_connector_requests").count("browser") == 1


def test_a_column_another_connection_added_meanwhile_is_taken_as_there(tmp_path):
    """Two connections opening an old store at once both read that the column is missing; the second to add it was told
    "duplicate column name" and failed, the server's start among them."""
    path = tmp_path / "s.db"
    old_store(path)
    conn = sqlite3.connect(str(path))
    try:
        store._migrate(Racing(conn, path, "ade_connector_requests", "browser"))
        conn.commit()
    finally:
        conn.close()
    assert columns(path, "ade_connector_requests").count("browser") == 1


TEAMMATE_ADDED = (("ade_agent_task_offers", "kept_at"), ("ade_agent_task_offers", "reduce_requested_at"),
                  ("ade_task_custody", "reduce_requested_at"), ("ade_agent_task_offers", "press_dropped_at"),
                  ("ade_agent_task_offers", "reread"))


def test_a_store_made_before_the_teammates_later_columns_gets_them(tmp_path):
    """The teammate part's columns added after its tables were first made (ade/task_offers.py: Keep's own column; the
    mark of a row the operator's command asked to reduce while it stood; and an offer's two marks of a confirmation
    made while it was out, press_dropped_at and reread): a store made before them gets each once, its rows kept, as
    every column added after a table's first deploy (store.AGENTS_ADDED)."""
    path = tmp_path / "s.db"
    with store.connect(str(path)):
        pass
    db = sqlite3.connect(str(path))
    db.execute("INSERT INTO ade_agent_task_offers (id, user_id, task_id, kind, state, fence, created_at, updated_at) "
               "VALUES ('o', 'u', 't', 'custody', 'accepted', 1, 1, 1)")
    db.execute("INSERT INTO ade_task_custody (id, owner_user, custodian_user, state, created_at, updated_at) VALUES "
               "('c', 'u', 'm', 'accepted', 1, 1)")
    for table, column in TEAMMATE_ADDED:
        db.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    db.commit()
    db.close()
    assert not any(column in columns(path, table) for table, column in TEAMMATE_ADDED)
    with store.connect(str(path)) as again:
        assert again.execute("SELECT state FROM ade_agent_task_offers WHERE id = 'o'").fetchone()["state"] == "accepted"
        assert again.execute("SELECT custodian_user FROM ade_task_custody WHERE id = 'c'").fetchone()[0] == "m"
    assert all(columns(path, table).count(column) == 1 for table, column in TEAMMATE_ADDED)


def test_any_other_failure_to_add_a_column_is_not_taken_as_there(tmp_path):
    path = tmp_path / "s.db"
    old_store(path)
    conn = sqlite3.connect(str(path))
    try:
        locked = sqlite3.OperationalError("database is locked")
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            store._migrate(Racing(conn, path, "ade_connector_requests", "browser", error=locked))
    finally:
        conn.close()
