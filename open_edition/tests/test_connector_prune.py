"""What the Claude app connector keeps past its use, cleared on a timer (oauth.prune, oauth.start), never inside a
request's write lock: codes and tokens a day after their end, by an index on that end; connector events after 90 days;
requests a day after they ended; and the counts kept in the app's memory once they have left their windows."""

import threading
import time
import types

import pytest

from conftest import signed_up
from connector_fixtures import allowed, exchange
from proofpane_open import oauth, store

NOW = 2_000_000_000


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_CONNECTOR", "on")
    monkeypatch.delenv("OPEN_EDITION_ADE_CONNECTOR_HOLD", raising=False)
    oauth._failed.clear()
    oauth._document_reads.clear()
    oauth._document_failures.clear()


def rows(table, column):
    with store.connect() as db:
        return sorted(r[0] for r in db.execute(f"SELECT {column} FROM {table}"))


def put_token(db, name, expires_at):
    db.execute("INSERT INTO ade_connector_tokens (hash, grant_id, kind, created_at, expires_at) VALUES (?, 'g', "
               "'access', 1, ?)", (name, expires_at))


def test_the_prune_clears_tokens_events_and_requests_past_their_use():
    with store.connect() as db:
        put_token(db, "old", NOW - 86400)
        put_token(db, "kept", NOW - 86399)
        for at in (NOW - oauth.EVENTS_KEPT, NOW - oauth.EVENTS_KEPT + 1):
            db.execute("INSERT INTO ade_connector_events (user_id, grant_id, at, kind, outcome) VALUES ('u', 'g', ?, "
                       "'tool', 'ok')", (at,))
        for rid, expires in (("old", NOW - oauth.KEEP_UNUSED), ("kept", NOW - oauth.KEEP_UNUSED + 1)):
            db.execute("INSERT INTO ade_connector_requests (id, client_id, redirect_uri, code_challenge, scopes, "
                       "asked_net, created_at, expires_at) VALUES (?, 'c', 'r', 'x', 'ade.read', 'n', 1, ?)",
                       (rid, expires))
    oauth.prune(now=NOW)
    assert rows("ade_connector_tokens", "hash") == ["kept"]
    assert rows("ade_connector_events", "at") == [NOW - oauth.EVENTS_KEPT + 1]
    assert rows("ade_connector_requests", "id") == ["kept"]


def put_client(db, client_id, kind, created_at, fresh_until=None):
    db.execute("INSERT INTO ade_connector_clients (id, kind, name, redirect_uris, created_at, fresh_until) VALUES "
               "(?, ?, 'An app', '[]', ?, ?)", (client_id, kind, created_at, fresh_until))


def test_the_prune_clears_registrations_and_descriptions_no_connection_uses():
    """A registration clears others' as it is made, but an ADE nobody registers with again keeps what no connection
    uses until the prune clears it: a registration a day after it was made, a description a day after it went stale."""
    old, kept, used = ("ppcl-" + c * 32 for c in "123")
    with store.connect() as db:
        put_client(db, old, "registered", NOW - oauth.KEEP_UNUSED)
        put_client(db, kept, "registered", NOW - oauth.KEEP_UNUSED + 1)
        put_client(db, used, "registered", 1)
        put_client(db, "https://claude.ai/old", "document", 1, NOW - oauth.KEEP_UNUSED)
        put_client(db, "https://claude.ai/kept", "document", 1, NOW - oauth.KEEP_UNUSED + 1)
        db.execute("INSERT INTO ade_connector_grants (id, user_id, client_id, client_name, redirect_uri, scopes, "
                   "resource, allowed_at, ends_at) VALUES ('g', 'u', ?, 'Claude', 'r', 'ade.read', 'r', 1, 2)", (used,))
    oauth.prune(now=NOW)
    assert rows("ade_connector_clients", "id") == sorted([kept, used, "https://claude.ai/kept"])


def test_a_code_exchange_clears_nothing_and_the_prune_does_it(client):
    """An exchange takes the write lock only for its own code: what is past its use waits for the prune."""
    owner = signed_up(client)
    got = allowed(client, owner)
    with store.connect() as db:
        put_token(db, "old", int(time.time()) - 2 * 86400)
        db.execute("INSERT INTO ade_connector_events (user_id, grant_id, at, kind, outcome) VALUES ('u', 'g', 1, "
                   "'tool', 'ok')")
    assert exchange(client, got).status_code == 200
    assert "old" in rows("ade_connector_tokens", "hash") and 1 in rows("ade_connector_events", "at")
    oauth.prune()
    assert "old" not in rows("ade_connector_tokens", "hash") and 1 not in rows("ade_connector_events", "at")


def test_the_prune_prunes_the_apps_proposals_as_the_notifier_does_where_the_notifier_does_not_run():
    """The notifier's round prunes the apps' proposals (push.tick), and does not run where OPEN_EDITION_ADE_PUSH_EVERY
    is 0, which would keep their words in the clear for good: the connector's prune prunes them too."""
    from proofpane_open.ade import proposals
    with store.connect() as db:
        for pid, state, expires, decided in (("waiting", "proposed", NOW + 1, None), ("late", "proposed", NOW, None),
                                             ("refused", "refused", NOW, NOW - proposals.WORDS_KEPT),
                                             ("old", "sent", NOW, NOW - proposals.ROWS_KEPT)):
            db.execute("INSERT INTO ade_proposals (id, user_id, grant_id, kind, text, why, state, created_at, "
                       "expires_at, decided_at) VALUES (?, 'u', 'g', 'instruction', 'words', 'a reason', ?, 1, ?, ?)",
                       (pid, state, expires, decided))
    oauth.prune(now=NOW)
    with store.connect() as db:
        got = {r["id"]: (r["state"], r["text"]) for r in db.execute("SELECT id, state, text FROM ade_proposals")}
    assert got == {"waiting": ("proposed", "words"), "late": ("expired", "words"), "refused": ("refused", None)}


def test_the_prune_deletes_tokens_by_an_index_on_their_end_in_a_store_made_before_it_too():
    with store.connect() as db:
        plan = " ".join(r[3] for r in db.execute("EXPLAIN QUERY PLAN DELETE FROM ade_connector_tokens WHERE "
                                                 "expires_at <= ?", (NOW,)))
        assert "ade_connector_tokens_expires" in plan, plan
        db.execute("DROP INDEX ade_connector_tokens_expires")         # a store made before the index
    with store.connect() as db:
        names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = "
                                          "'ade_connector_tokens'")]
    assert "ade_connector_tokens_expires" in names


def garbage(client_id, network, now):
    with pytest.raises(oauth.OAuthError):
        oauth.token({"grant_type": "refresh_token", "refresh_token": "x", "client_id": client_id}, client=network,
                    now=now)


def test_counts_that_left_their_window_are_cleared_by_the_prune_not_by_a_request():
    garbage("a", "203.0.113.5", NOW)
    garbage("b", "198.51.100.7", NOW + oauth.FAILED_WINDOW)          # a request clears nothing
    assert set(oauth._failed) == {("client", "a"), ("network", "203.0.113.5"), ("client", "b"),
                                  ("network", "198.51.100.7")}
    oauth.prune(now=NOW + oauth.FAILED_WINDOW)
    assert set(oauth._failed) == {("client", "b"), ("network", "198.51.100.7")}


def test_at_most_failed_keys_counts_are_kept_the_one_counted_least_lately_going(monkeypatch):
    monkeypatch.setattr(oauth, "FAILED_KEYS", 4)
    garbage("a", "203.0.113.5", NOW)
    garbage("b", "203.0.113.5", NOW + 1)                # that network's count, counted again, is kept last
    assert list(oauth._failed) == [("client", "a"), ("client", "b"), ("network", "203.0.113.5")]
    garbage("c", "198.51.100.7", NOW + 2)
    assert list(oauth._failed) == [("client", "b"), ("network", "203.0.113.5"), ("client", "c"),
                                   ("network", "198.51.100.7")]
    assert oauth._failed[("network", "203.0.113.5")] == [NOW, NOW + 1]


def test_reads_of_documents_and_failed_reads_past_their_time_are_cleared():
    oauth._document_reads.extend([(NOW - 3600, "n", "w", True), (NOW - 3599, "n", "w", False)])
    oauth._document_failures.update({"https://claude.ai/a": (NOW, "gone"), "https://claude.ai/b": (NOW + 1, "kept")})
    oauth.prune(now=NOW)
    assert oauth._document_reads == [(NOW - 3599, "n", "w", False)]
    assert list(oauth._document_failures) == ["https://claude.ai/b"]


def test_the_prune_runs_on_a_timer_in_a_daemon_thread_and_outlives_a_failing_round(monkeypatch):
    rounds, second, park = [], threading.Event(), threading.Event()

    def prune(now=None):
        rounds.append(1)
        if len(rounds) == 1:
            raise RuntimeError("a round fails")
        second.set()
        park.wait()                                  # hold the thread here: it must not run on after the test
    slept = []
    monkeypatch.setattr(oauth, "prune", prune)
    monkeypatch.setattr(oauth, "time", types.SimpleNamespace(sleep=slept.append, time=time.time))
    thread = oauth.start(every=7)
    assert thread.daemon and thread.name == "oauth-prune"
    assert second.wait(5) and len(rounds) == 2 and slept == [7, 7]


def test_serving_the_app_starts_the_prune(monkeypatch):
    from fastapi.testclient import TestClient
    from proofpane_open import main
    started = []
    monkeypatch.setattr(main.oauth, "start", lambda every=None: started.append(every))
    monkeypatch.setattr(main.ade_push, "start", lambda every=None: None)
    monkeypatch.setattr(main.ade_moves, "start", lambda every=None: None)
    monkeypatch.setattr(main.ade_e2e_keys, "start", lambda every=None: None)
    with TestClient(main.app):
        pass
    assert started == [None]
