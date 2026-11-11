"""SQLite storage: users and invitations, and the ADE's connected repositories, Verify results, hook tokens,
approval requests, push subscriptions, finished workspace sessions, the person's answers to their agents' asks for
sessions, their own agents' commands, the Claude app connector's clients, grants, tokens (as hashes), events and
proposals, and the agents' tasks with their journals, the accounts their owners confirmed, a teammate's offers and
custody, and each task's account record; each account's pairing with its Proofpane organisation (its token sealed), and
the usage of its linked sessions' replies on its way there. One file on the app's volume; every call opens its own
connection."""

import os
import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    token_gen INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS passkeys (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    credential_id TEXT NOT NULL UNIQUE,
    public_key TEXT NOT NULL,
    alg INTEGER NOT NULL,
    sign_count INTEGER NOT NULL,
    backed_up INTEGER NOT NULL,
    name TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    last_used_at INTEGER
);
CREATE INDEX IF NOT EXISTS passkeys_user ON passkeys (user_id);
CREATE TABLE IF NOT EXISTS passkey_used_challenges (
    challenge_hash TEXT PRIMARY KEY,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS fido_mds_accepted (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    no INTEGER NOT NULL,
    digest TEXT NOT NULL,
    accepted_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS passkey_reconsidered (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    no INTEGER NOT NULL,
    at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS invitations (
    token_hash TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at INTEGER
);
CREATE TABLE IF NOT EXISTS device_signins (
    id TEXT PRIMARY KEY,
    code_hash TEXT NOT NULL UNIQUE,
    poll_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    approved_by TEXT,
    approved_at INTEGER,
    denied_at INTEGER,
    used_at INTEGER,
    last_poll_at INTEGER,
    asked_from TEXT,
    asked_net TEXT,
    asked_wide TEXT,
    confirmed_by TEXT,
    ended_at INTEGER
);
CREATE TABLE IF NOT EXISTS ade_repos (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    source TEXT NOT NULL,
    repo TEXT NOT NULL,
    branch TEXT NOT NULL,
    dirs TEXT NOT NULL,
    verify INTEGER NOT NULL,
    token_box TEXT,
    created_at INTEGER NOT NULL,
    verify_config TEXT
);
CREATE INDEX IF NOT EXISTS ade_repos_user ON ade_repos (user_id);
CREATE TABLE IF NOT EXISTS ade_verifications (
    repo_id TEXT NOT NULL,
    sha TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    verify TEXT,
    claims TEXT,
    rule TEXT,
    because TEXT,
    digest TEXT,
    manifest TEXT,
    signature TEXT,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (repo_id, sha)
);
CREATE TABLE IF NOT EXISTS ade_hook_tokens (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    last_used_at INTEGER,
    revoked_at INTEGER
);
CREATE TABLE IF NOT EXISTS ade_requests (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    command TEXT NOT NULL,
    tool TEXT NOT NULL,
    session TEXT,
    cwd TEXT,
    created_at INTEGER NOT NULL,
    until INTEGER NOT NULL,
    state TEXT NOT NULL,
    decided_at INTEGER,
    words TEXT,
    used_at INTEGER
);
CREATE INDEX IF NOT EXISTS ade_requests_user ON ade_requests (user_id, created_at);
CREATE INDEX IF NOT EXISTS ade_requests_token ON ade_requests (token_id, command);
CREATE INDEX IF NOT EXISTS ade_requests_waiting ON ade_requests (user_id, state, until);
CREATE TABLE IF NOT EXISTS ade_push_subscriptions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    last_sent_at INTEGER,
    failures INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ade_finishes (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    session TEXT NOT NULL,
    repo TEXT NOT NULL,
    branch TEXT NOT NULL,
    base TEXT NOT NULL,
    head TEXT NOT NULL,
    task TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    verify TEXT,
    claims TEXT,
    rule TEXT,
    because TEXT,
    digest TEXT,
    manifest TEXT,
    signature TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ade_finishes_user ON ade_finishes (user_id, created_at);
-- left_at: when the account left the hosted choice (ade/storage.py), which keeps the workspace
-- OPEN_EDITION_WORKSPACE_KEEP_DAYS days from then (ade/fly_provider.py, sweep_left); storage_gb: its volume and machine
-- disk as the meter last read them, which its spending counts
CREATE TABLE IF NOT EXISTS ade_workspaces (
    user_id TEXT PRIMARY KEY,
    app TEXT NOT NULL,
    region TEXT NOT NULL,
    volume_id TEXT,
    machine_id TEXT,
    image TEXT,
    cert TEXT,
    secret_box TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    left_at INTEGER,
    storage_gb REAL
);
-- user_id: the account whose machine ran, as the meter knew it when it read it (null in a store from before it
-- wrote it)
CREATE TABLE IF NOT EXISTS ade_workspace_spend (
    month TEXT NOT NULL,
    machine_id TEXT NOT NULL,
    seconds INTEGER NOT NULL,
    user_id TEXT,
    PRIMARY KEY (month, machine_id)
);
-- a workspace this app destroyed at Fly, with its row in ade_workspaces: kept past its days, or deleted at once by
-- the person (ade/fly_provider.py, remove_workspace)
CREATE TABLE IF NOT EXISTS ade_workspace_removals (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    app TEXT NOT NULL,
    machine_id TEXT,
    volume_id TEXT,
    why TEXT NOT NULL,
    removed_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ade_workspace_removals_user ON ade_workspace_removals (user_id, removed_at);
-- where the account's new work lives, as it chose (ade/storage.py): repository, machine or hosted; with no row, hosted
-- where the account has a workspace, else machine. e2e_not_now: when it said "not now" to turning end-to-end
-- encryption on, so it is not asked again
CREATE TABLE IF NOT EXISTS ade_storage_choice (
    user_id TEXT PRIMARY KEY,
    choice TEXT NOT NULL CHECK (choice IN ('repository', 'machine', 'hosted')),
    chosen_at INTEGER NOT NULL,
    e2e_not_now INTEGER
);
CREATE TABLE IF NOT EXISTS ade_workspace_meter (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_tick REAL NOT NULL,
    storage_gb REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ade_links (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    public_key TEXT NOT NULL,
    key_id TEXT NOT NULL,
    session_ref TEXT,
    interval_min INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    revoked_at INTEGER,
    last_seen_at INTEGER,
    last_heartbeat_at INTEGER,
    acked_seq INTEGER NOT NULL DEFAULT 0,
    checkpoint TEXT,
    checkpoint_version INTEGER NOT NULL DEFAULT 0,
    checkpoint_at INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ade_links_live_key ON ade_links (public_key) WHERE revoked_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ade_links_live_name ON ade_links (user_id, name) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS ade_links_key_id ON ade_links (key_id);
-- an account's links by name, which each feed read asks for (a watcher's every 3 seconds: ade/link.py, _names)
CREATE INDEX IF NOT EXISTS ade_links_user ON ade_links (user_id);
CREATE TABLE IF NOT EXISTS ade_link_tickets (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    link_id TEXT,
    ticket_hash TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at INTEGER
);
CREATE TABLE IF NOT EXISTS ade_link_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    link_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    to_name TEXT NOT NULL,
    ref INTEGER,
    text TEXT NOT NULL,
    needs_turn INTEGER NOT NULL DEFAULT 0,
    closed_by INTEGER,
    closed_at INTEGER,
    received_at INTEGER NOT NULL,
    UNIQUE (user_id, link_id, client_id)
);
CREATE INDEX IF NOT EXISTS ade_link_events_user ON ade_link_events (user_id, seq);
CREATE INDEX IF NOT EXISTS ade_link_events_turn ON ade_link_events (user_id, needs_turn, seq);
CREATE INDEX IF NOT EXISTS ade_link_events_kind ON ade_link_events (user_id, kind, seq);
-- each linked machine's Claude Code sessions, as its link last listed them: titles, folders and times (a withheld one's title
-- and branch stay on its machine); a conversation, where the link lets it go, in ade_link_history_talks
CREATE TABLE IF NOT EXISTS ade_link_history (
    user_id TEXT NOT NULL,
    link_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    title TEXT NOT NULL,
    project TEXT,
    branch TEXT,
    started_at INTEGER,
    last_at INTEGER,
    listed_at INTEGER NOT NULL,
    PRIMARY KEY (link_id, session_id)
);
CREATE INDEX IF NOT EXISTS ade_link_history_user ON ade_link_history (user_id, last_at);
-- a listed session's conversation, as its link sent it: what the owner typed and the session's words, never a tool's
CREATE TABLE IF NOT EXISTS ade_link_history_talks (
    user_id TEXT NOT NULL,
    link_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    messages TEXT NOT NULL,
    earlier INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL,
    key TEXT,
    key_id TEXT,
    PRIMARY KEY (link_id, session_id)
);
CREATE TABLE IF NOT EXISTS ade_link_history_puts (
    link_id TEXT NOT NULL,
    at INTEGER NOT NULL
);
-- each session a History list of the account named withheld (a row withheld, or its id in the list's withheld), and
-- when it was last named so: kept whatever becomes of the link that named it, so the connector never gives the command
-- its hook holds once a revoke or a new link has taken that list away (ade/connector.py)
CREATE TABLE IF NOT EXISTS ade_link_withheld (
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    at INTEGER NOT NULL,
    PRIMARY KEY (user_id, session_id)
);
CREATE INDEX IF NOT EXISTS ade_link_withheld_at ON ade_link_withheld (user_id, at);
-- the newest seq of an account's events deleted (a session's mirrored notes, ade/link.py): its head never goes back
CREATE TABLE IF NOT EXISTS ade_link_heads (
    user_id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ade_link_history_puts_link ON ade_link_history_puts (link_id, at);
-- a move of one of a linked machine's Claude Code sessions into the hosted workspace (ade/moves.py), one row per move by
-- its signed command's seq: where it stands, the sizes of the files its machine sent (kept on disk, never here) and
-- what it said of its repository, and the workspace's session once it started there
CREATE TABLE IF NOT EXISTS ade_link_moves (
    seq INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    link_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    title TEXT,
    state TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    taken_at INTEGER,
    transcript_size INTEGER,
    transcript_at INTEGER,
    bundle_size INTEGER,
    bundle_at INTEGER,
    done TEXT,
    uploaded_at INTEGER,
    workspace_session TEXT,
    reason TEXT,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ade_link_moves_session ON ade_link_moves (user_id, link_id, session_id, seq);
CREATE INDEX IF NOT EXISTS ade_link_moves_state ON ade_link_moves (state);
-- a move the owner signed, held (ade/holds.py) until it is stored as its event and its row above (ade/moves.py), one
-- row per move by its hold: what makes its event when the hold ends (the payload as signed, the passkey's answer), and
-- that event's seq once it is stored. Its link reads nothing of it from here
CREATE TABLE IF NOT EXISTS ade_link_held_moves (
    hold_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    link_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    title TEXT,
    client_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    signature TEXT NOT NULL,
    passkey_id TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    seq INTEGER,
    UNIQUE (user_id, client_id)
);
CREATE INDEX IF NOT EXISTS ade_link_held_moves_session ON ade_link_held_moves (user_id, link_id, session_id,
    created_at);
CREATE INDEX IF NOT EXISTS ade_link_held_moves_passkey ON ade_link_held_moves (passkey_id);
CREATE INDEX IF NOT EXISTS ade_link_held_moves_seq ON ade_link_held_moves (seq);
-- a session that asks to be linked (ade/link_asks.py): each account's ask address, a handle that grants nothing, and
-- each request made to one, kept a day whatever became of it, so the limits are counted from here. A request to an
-- address no account has is kept the same way, with no account, so an answer never says whether an address is one
CREATE TABLE IF NOT EXISTS ade_link_ask_addresses (
    user_id TEXT PRIMARY KEY,
    address TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ade_link_asks (
    id TEXT PRIMARY KEY,
    address TEXT NOT NULL,
    user_id TEXT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    public_key TEXT NOT NULL,
    key_id TEXT NOT NULL,
    session_ref TEXT,
    asked_from TEXT,
    asked_net TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    state TEXT NOT NULL,
    decided_at INTEGER,
    hold_id TEXT,
    link_id TEXT,
    last_poll_at INTEGER
);
CREATE INDEX IF NOT EXISTS ade_link_asks_key ON ade_link_asks (key_id, created_at);
CREATE INDEX IF NOT EXISTS ade_link_asks_address ON ade_link_asks (address, state, expires_at);
CREATE INDEX IF NOT EXISTS ade_link_asks_net ON ade_link_asks (asked_net, created_at);
CREATE INDEX IF NOT EXISTS ade_link_asks_user ON ade_link_asks (user_id, state);
-- an account's pairing with its Proofpane organisation (ade/proofpane.py): one per account, the device's id and token
-- sealed together in box under the key-store key, as a repository's token is; the device's id, its name, the address
-- and when it was paired beside the box, for the page
CREATE TABLE IF NOT EXISTS ade_proofpane_pairings (
    user_id TEXT PRIMARY KEY,
    id TEXT NOT NULL,
    cloud_url TEXT NOT NULL,
    device_id TEXT NOT NULL,
    name TEXT,
    paired_at TEXT NOT NULL,
    box TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
-- each reply of a linked session whose usage its link sent (ade/link_usage.py), once per account and request id: its
-- counts, model and times while it waits to go to the account's Proofpane through the pairing it came under, then
-- whether it went or was dropped, when and why; never a word of the session
CREATE TABLE IF NOT EXISTS ade_link_usage (
    user_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    pairing_id TEXT NOT NULL,
    link_id TEXT NOT NULL,
    link_name TEXT NOT NULL,
    session_id TEXT NOT NULL,
    model TEXT,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_read_input_tokens INTEGER NOT NULL,
    cache_creation_input_tokens INTEGER NOT NULL,
    ts TEXT,
    started_by TEXT,
    received_at INTEGER NOT NULL,
    state TEXT NOT NULL,
    tries INTEGER NOT NULL DEFAULT 0,
    next_at INTEGER,
    done_at INTEGER,
    reason TEXT,
    PRIMARY KEY (user_id, request_id)
);
CREATE INDEX IF NOT EXISTS ade_link_usage_due ON ade_link_usage (state, next_at);
CREATE INDEX IF NOT EXISTS ade_link_usage_link ON ade_link_usage (link_id, received_at);
-- a signer (ade/signers.py): a program on the owner's own machine that signs the owner's steps there with a security
-- key. Its own Ed25519 key signs its requests (ADE-Signer), never a live link's or an asking session's key. confirmed:
-- what confirmed its ticket (hardware, other, or null); polled_at: when it last read the requests waiting for it
CREATE TABLE IF NOT EXISTS ade_signers (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    public_key TEXT NOT NULL,
    key_id TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    last_seen_at INTEGER,
    polled_at INTEGER,
    revoked_at INTEGER,
    confirmed TEXT
);
CREATE INDEX IF NOT EXISTS ade_signers_key ON ade_signers (key_id, revoked_at);
CREATE INDEX IF NOT EXISTS ade_signers_user ON ade_signers (user_id, revoked_at);
-- a one-time ticket to pair a signer, made on the page with the account's confirmation: only its sha256 is kept
CREATE TABLE IF NOT EXISTS ade_signer_tickets (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    ticket_hash TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at INTEGER,
    confirmed TEXT,
    confirmed_by TEXT
);
-- a step the owner's page parked for a signer (the relay, ade/signers.py): its canonical payload and the code its bytes
-- give, a move's title sent beside it, and once a signer hands one back, the assertion over those bytes; deleted 10
-- minutes after its end. parked_from: the address the server saw; parked_network: what it is counted by
CREATE TABLE IF NOT EXISTS ade_sign_requests (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    purpose TEXT NOT NULL,
    payload TEXT NOT NULL,
    code TEXT NOT NULL,
    title TEXT,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    state TEXT NOT NULL,
    credential TEXT,
    signer_id TEXT,
    signed_at INTEGER,
    parked_from TEXT NOT NULL,
    parked_network TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ade_sign_requests_user ON ade_sign_requests (user_id, expires_at);
-- which signer an assertion over a payload came through, by the assertion's signature: the step's own route keeps the
-- assertion as it came, so a stored step whose signature is one of these was signed through that signer
CREATE TABLE IF NOT EXISTS ade_signed_through (
    user_id TEXT NOT NULL,
    payload_id TEXT NOT NULL,
    signature TEXT NOT NULL,
    signer_id TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, payload_id, signature)
);
-- an approved risky action that waits (ade/holds.py): it takes effect at takes_effect_at unless cancelled first
CREATE TABLE IF NOT EXISTS ade_holds (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    summary TEXT NOT NULL,
    detail TEXT,
    url TEXT,
    approved_at INTEGER NOT NULL,
    takes_effect_at INTEGER NOT NULL,
    cancelled_at INTEGER,
    done_at INTEGER,
    failed TEXT,
    told INTEGER
);
CREATE INDEX IF NOT EXISTS ade_holds_open ON ade_holds (takes_effect_at) WHERE cancelled_at IS NULL AND done_at IS NULL;
CREATE INDEX IF NOT EXISTS ade_holds_subject ON ade_holds (kind, subject);
CREATE INDEX IF NOT EXISTS ade_holds_user ON ade_holds (user_id, approved_at);
-- the Claude app connector (oauth.py): its clients, one that registered itself (RFC 7591) or one named by its Client ID
-- Metadata Document (its id the document's URL), kept while a grant uses it, else a day
CREATE TABLE IF NOT EXISTS ade_connector_clients (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    redirect_uris TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    net TEXT,
    fresh_until INTEGER
);
CREATE INDEX IF NOT EXISTS ade_connector_clients_net ON ade_connector_clients (kind, net, created_at);
-- the Client ID Metadata Documents read here and found sound (oauth.py), by URL, kept past the clients' pruning: one
-- known here is read again whenever it is stale, drawing on none of the hour's shared count of reads; the
-- DOCUMENTS_KNOWN read most lately
CREATE TABLE IF NOT EXISTS ade_connector_documents_known (
    url TEXT PRIMARY KEY,
    read_at INTEGER NOT NULL
);
-- a request for the owner's consent from /oauth/authorize, until it is answered on the page or ten minutes pass
CREATE TABLE IF NOT EXISTS ade_connector_requests (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    state TEXT,
    code_challenge TEXT NOT NULL,
    scopes TEXT NOT NULL,
    asked_from TEXT,
    asked_net TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    answer TEXT,
    user_id TEXT,
    grant_id TEXT,
    answered_at INTEGER
);
CREATE INDEX IF NOT EXISTS ade_connector_requests_net ON ade_connector_requests (asked_net, expires_at);
-- a connected app: the owner's consent, what it may do, the hold it waited on, and when it ends
CREATE TABLE IF NOT EXISTS ade_connector_grants (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    client_name TEXT NOT NULL,
    document_host TEXT,
    redirect_uri TEXT NOT NULL,
    scopes TEXT NOT NULL,
    resource TEXT NOT NULL,
    allowed_at INTEGER NOT NULL,
    connected_at INTEGER,
    ends_at INTEGER NOT NULL,
    hold_id TEXT,
    takes_effect_at INTEGER,
    active_at INTEGER,
    revoked_at INTEGER,
    revoked_why TEXT,
    last_used_at INTEGER,
    last_net TEXT
);
CREATE INDEX IF NOT EXISTS ade_connector_grants_user ON ade_connector_grants (user_id, allowed_at);
CREATE INDEX IF NOT EXISTS ade_connector_grants_client ON ade_connector_grants (client_id);
-- every code, access token and refresh token of a grant, as its sha256: none is kept as given
CREATE TABLE IF NOT EXISTS ade_connector_tokens (
    hash TEXT PRIMARY KEY,
    grant_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at INTEGER,
    scopes TEXT,
    code_challenge TEXT,
    redirect_uri TEXT
);
CREATE INDEX IF NOT EXISTS ade_connector_tokens_grant ON ade_connector_tokens (grant_id, kind, created_at);
-- the account's connector events: each use of a grant's tokens and each change to a grant, kept 90 days
CREATE TABLE IF NOT EXISTS ade_connector_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    grant_id TEXT NOT NULL,
    at INTEGER NOT NULL,
    kind TEXT NOT NULL,
    what TEXT,
    subject TEXT,
    outcome TEXT NOT NULL,
    net TEXT
);
CREATE INDEX IF NOT EXISTS ade_connector_events_grant ON ade_connector_events (grant_id, at);
CREATE INDEX IF NOT EXISTS ade_connector_events_at ON ade_connector_events (at);
-- a proposal a connected app made (ade/proposals.py): it waits on the page until the owner signs or accepts it, or
-- refuses it, its app withdraws it, what it is for goes, or its time ends
CREATE TABLE IF NOT EXISTS ade_proposals (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    grant_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    link_id TEXT,
    ref INTEGER,
    request_id TEXT,
    decision TEXT,
    text TEXT,
    why TEXT,
    state TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    decided_at INTEGER,
    seq INTEGER,
    edited INTEGER NOT NULL DEFAULT 0,
    said TEXT
);
CREATE INDEX IF NOT EXISTS ade_proposals_user ON ade_proposals (user_id, state, created_at);
CREATE INDEX IF NOT EXISTS ade_proposals_grant ON ade_proposals (grant_id, created_at);
CREATE TABLE IF NOT EXISTS ade_push_seen (
    user_id TEXT NOT NULL,
    key TEXT NOT NULL,
    sent_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS ade_custom_agents (
    user_id TEXT NOT NULL,
    id TEXT NOT NULL,
    name TEXT NOT NULL,
    command TEXT NOT NULL,
    task TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, id)
);
CREATE TABLE IF NOT EXISTS ade_session_accounts (
    user_id TEXT NOT NULL,
    session TEXT NOT NULL,
    account TEXT NOT NULL,
    at INTEGER NOT NULL,
    PRIMARY KEY (user_id, session)
);
-- a session's task as the owner gave it, kept by the app (the session's own record its programs can write), and each
-- session continued in a new one by the owner's press
CREATE TABLE IF NOT EXISTS ade_session_tasks (
    user_id TEXT NOT NULL,
    session TEXT NOT NULL,
    task TEXT NOT NULL,
    at INTEGER NOT NULL,
    PRIMARY KEY (user_id, session)
);
CREATE TABLE IF NOT EXISTS ade_session_handovers (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    from_session TEXT NOT NULL,
    to_session TEXT NOT NULL,
    head TEXT NOT NULL,
    program TEXT NOT NULL,
    account TEXT,
    finish_id TEXT,
    commits INTEGER NOT NULL,
    files INTEGER NOT NULL,
    at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ade_session_handovers_from ON ade_session_handovers (user_id, from_session);
CREATE INDEX IF NOT EXISTS ade_session_handovers_to ON ade_session_handovers (user_id, to_session);
CREATE TABLE IF NOT EXISTS ade_session_pulls (
    user_id TEXT NOT NULL,
    session TEXT NOT NULL,
    kind TEXT NOT NULL,
    url TEXT NOT NULL,
    at INTEGER NOT NULL,
    PRIMARY KEY (user_id, session, kind)
);
CREATE TABLE IF NOT EXISTS ade_ask_answers (
    user_id TEXT NOT NULL,
    ask_id TEXT NOT NULL,
    from_session TEXT NOT NULL,
    program TEXT NOT NULL,
    answer TEXT NOT NULL,
    session TEXT,
    at INTEGER NOT NULL,
    PRIMARY KEY (user_id, ask_id)
);
-- end-to-end encryption of what linked sessions send (ade/e2e_keys.py): the owner's signed statements of the content
-- keys links seal to, numbered and chained; and what the page keeps here of the account's keys, each sealed or wrapped
-- so that only the owner's credentials or recovery code open it: each credential's own wrap, the recovery key, the
-- master key's grants, the chain of master keys, the roster, the content keys' wraps
CREATE TABLE IF NOT EXISTS ade_e2e_statements (
    user_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    id TEXT NOT NULL,
    purpose TEXT NOT NULL,
    key_id TEXT,
    public_key TEXT,
    retires TEXT,
    payload TEXT NOT NULL,
    credential TEXT,
    passkey_id TEXT,
    created_at INTEGER NOT NULL,
    retired_at INTEGER,
    PRIMARY KEY (user_id, seq)
);
CREATE TABLE IF NOT EXISTS ade_e2e_credentials (
    user_id TEXT NOT NULL,
    passkey_id TEXT NOT NULL,
    credential_id TEXT NOT NULL,
    salt TEXT NOT NULL,
    public_key TEXT NOT NULL,
    wrapped TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    anchor TEXT,
    PRIMARY KEY (user_id, passkey_id)
);
CREATE TABLE IF NOT EXISTS ade_e2e_recovery (
    user_id TEXT PRIMARY KEY,
    public_key TEXT NOT NULL,
    anchor TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ade_e2e_grants (
    user_id TEXT NOT NULL,
    mk_id TEXT NOT NULL,
    recipient TEXT NOT NULL,
    sealed TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, mk_id, recipient)
);
CREATE TABLE IF NOT EXISTS ade_e2e_chain (
    user_id TEXT NOT NULL,
    mk_id TEXT NOT NULL,
    previous TEXT NOT NULL,
    wrapped TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, mk_id)
);
CREATE TABLE IF NOT EXISTS ade_e2e_roster (
    user_id TEXT PRIMARY KEY,
    mk_id TEXT NOT NULL,
    sealed TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ade_e2e_content_keys (
    user_id TEXT NOT NULL,
    key_id TEXT NOT NULL,
    public_key TEXT NOT NULL,
    mk_id TEXT NOT NULL,
    wrapped TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, key_id)
);
-- a change to the account's keys that waits on a hold (ade/holds.py): one at a time, kept whole until the hold ends;
-- made_by: the passkey that signed or confirmed it, "recovery" for the recovery code, null for a link's; by_code: 1
-- when the recovery code's proof came with it (code-backed, with made_by "recovery")
CREATE TABLE IF NOT EXISTS ade_e2e_pending (
    user_id TEXT PRIMARY KEY,
    hold_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    made_by TEXT,
    by_code INTEGER
);
-- a change to how the account signs in that waits on a hold (account_holds.py): one at a time, kept whole until its
-- hold ends. placed: the sets it was placed with, which the cancel rule
-- reads; maker, by_code (1 when the recovery code's proof came with it) and takes_away: the change as the rule reads
-- it; body: what it adds and keeps to undo; net: the network it was asked from. Each is written in one exact form
-- (account_holds.put_pending) and read back only in that form
CREATE TABLE IF NOT EXISTS account_pending (
    user_id TEXT PRIMARY KEY,
    hold_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    body TEXT NOT NULL,
    placed TEXT NOT NULL,
    maker TEXT NOT NULL,
    by_code INTEGER NOT NULL,
    takes_away TEXT NOT NULL,
    net TEXT,
    created_at INTEGER NOT NULL
);
-- the account's encryption apart from its keys: whether its master key must be replaced before encryption is turned on
-- again (a passkey that unlocked it was removed, or left out by high security, while it was off); whether the database
-- may still hold what it sealed in place (a token, until it is compacted); what its last sealing in place sealed;
-- whether its keys are post-quantum (pq: made so by a first turn-on, starting again, or the upgrade, and never back),
-- and when the database is compacted again after the upgrade
CREATE TABLE IF NOT EXISTS ade_e2e_state (
    user_id TEXT PRIMARY KEY,
    rekey_due INTEGER NOT NULL DEFAULT 0,
    compaction_due TEXT,
    sealed TEXT,
    sealed_at INTEGER,
    pq INTEGER NOT NULL DEFAULT 0,
    compact_again_at INTEGER
);
-- statements a link sent in after a restore whose hold the owner cancelled (their sha256): refused if sent again
CREATE TABLE IF NOT EXISTS ade_e2e_refused (
    user_id TEXT NOT NULL,
    digest TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, digest)
);
-- each stored text the owner's page sealed again to a post-quantum key, by its hashes
-- alone, never its content: where it is (RFC 8785 JSON of the batch's where), and the sha256 of the value before and
-- after
CREATE TABLE IF NOT EXISTS ade_e2e_reseal_log (
    user_id TEXT NOT NULL,
    at INTEGER NOT NULL,
    place TEXT NOT NULL,
    old_sha256 TEXT NOT NULL,
    new_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ade_e2e_reseal_log_user ON ade_e2e_reseal_log (user_id, at);
-- what a link last sealed to, when the server last told it encryption is required, the owner keys it says it pins,
-- and the envelope forms it says it can seal (its word, guidance for the owner)
CREATE TABLE IF NOT EXISTS ade_link_e2e (
    link_id TEXT PRIMARY KEY,
    key_id TEXT,
    last_required_at INTEGER,
    pins TEXT,
    seals TEXT
);
"""


# Agents (ade/agents.py): the app keeps them and every run's record, so the page that watches them never starts a
# machine; the workspace keeps their worktrees, memories and live runs (proofpane_workspace/agents.py).
AGENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS ade_agents (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    grp TEXT,
    brief TEXT NOT NULL,
    repo TEXT,
    base_ref TEXT,
    keep_worktree INTEGER NOT NULL DEFAULT 1,
    account TEXT,
    chat_settings TEXT NOT NULL DEFAULT '{}',
    allow TEXT NOT NULL DEFAULT '[]',
    limits TEXT NOT NULL DEFAULT '{}',
    brief_version INTEGER NOT NULL DEFAULT 1,
    paused INTEGER NOT NULL DEFAULT 0,
    paused_reason TEXT,
    retired_at INTEGER,
    from_session TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    mirrored_at INTEGER,
    mcp TEXT NOT NULL DEFAULT '{}',
    mcp_state TEXT,
    unattended_mode TEXT NOT NULL DEFAULT 'dontAsk',
    precheck TEXT,
    max_budget_usd REAL,
    est_usd_per_day REAL,
    paused_until INTEGER,
    autocompact_window INTEGER,
    max_compactions INTEGER NOT NULL DEFAULT 2,
    may_dispatch INTEGER NOT NULL DEFAULT 0,
    accepts_dispatch INTEGER NOT NULL DEFAULT 0,
    max_dispatch_per_run INTEGER NOT NULL DEFAULT 5,
    relations TEXT NOT NULL DEFAULT '{}',
    facts_version INTEGER NOT NULL DEFAULT 0,
    facts_mirrored INTEGER NOT NULL DEFAULT 0,
    decay_runs INTEGER NOT NULL DEFAULT 30,
    reverify INTEGER NOT NULL DEFAULT 0,
    anomaly_pause INTEGER NOT NULL DEFAULT 0,
    judge_runs INTEGER NOT NULL DEFAULT 0,
    acceptance TEXT,
    improve INTEGER NOT NULL DEFAULT 0,
    reviewed_at INTEGER,
    review_skipped TEXT,
    hold_unmirrored INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ade_agents_user ON ade_agents (user_id);
CREATE TABLE IF NOT EXISTS ade_agent_runs (
    resets_at INTEGER,
    context TEXT,
    run_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    state TEXT NOT NULL,
    sent INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    cause TEXT,
    trigger TEXT,
    queued_at INTEGER,
    started_at INTEGER,
    ended_at INTEGER,
    base TEXT,
    head TEXT,
    branch TEXT,
    brief_version INTEGER,
    question TEXT,
    steps INTEGER NOT NULL DEFAULT 0,
    requests INTEGER,
    cost_est_usd REAL,
    tokens TEXT,
    last_text TEXT,
    bytes INTEGER,
    pruned INTEGER NOT NULL DEFAULT 0,
    thumbs TEXT,
    thumbs_note TEXT,
    seq INTEGER,
    parent_run_id TEXT,
    depth INTEGER NOT NULL DEFAULT 0,
    dispatches INTEGER NOT NULL DEFAULT 0,
    waiting_on TEXT,
    metrics TEXT,
    rolled INTEGER NOT NULL DEFAULT 0,
    memory_digest TEXT,
    memory_copied INTEGER NOT NULL DEFAULT 0,
    judge TEXT,
    judge_asked INTEGER NOT NULL DEFAULT 0,
    anomalies TEXT,
    acceptance TEXT,
    replay_of TEXT,
    loop INTEGER NOT NULL DEFAULT 0,
    flag TEXT,
    changes TEXT,
    changes_taken INTEGER NOT NULL DEFAULT 0,
    rewritten TEXT,
    judge_error TEXT,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ade_agent_runs_agent ON ade_agent_runs (agent_id, queued_at);
CREATE INDEX IF NOT EXISTS ade_agent_runs_user ON ade_agent_runs (user_id, state);
CREATE TABLE IF NOT EXISTS ade_agent_totals (
    agent_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    runs INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    blocked INTEGER NOT NULL DEFAULT 0,
    seconds INTEGER NOT NULL DEFAULT 0,
    tokens INTEGER NOT NULL DEFAULT 0,
    cost_est_usd REAL NOT NULL DEFAULT 0,
    last_run_at INTEGER,
    last_state TEXT,
    today TEXT,
    runs_today INTEGER NOT NULL DEFAULT 0,
    est_usd_today REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ade_agent_sync (
    user_id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL DEFAULT 0,
    polled_at INTEGER,
    swept_day TEXT
);
CREATE TABLE IF NOT EXISTS ade_agent_proposals (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    name TEXT NOT NULL,
    entry TEXT NOT NULL,
    why TEXT,
    state TEXT NOT NULL DEFAULT 'open',
    created_at INTEGER NOT NULL,
    UNIQUE (run_id, name)
);
CREATE INDEX IF NOT EXISTS ade_agent_proposals_agent ON ade_agent_proposals (agent_id, state);
CREATE TABLE IF NOT EXISTS ade_agent_triggers (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    cron TEXT,
    tz TEXT,
    prompt TEXT,
    grace_minutes INTEGER NOT NULL DEFAULT 30,
    hook_type TEXT,
    secret TEXT,
    next_at INTEGER,
    last_at INTEGER,
    last_run_id TEXT,
    last_error TEXT,
    missed INTEGER NOT NULL DEFAULT 0,
    on_time INTEGER NOT NULL DEFAULT 0,
    late INTEGER NOT NULL DEFAULT 0,
    refused INTEGER NOT NULL DEFAULT 0,
    window_start INTEGER,
    window_count INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ade_agent_triggers_due ON ade_agent_triggers (kind, enabled, next_at);
CREATE INDEX IF NOT EXISTS ade_agent_triggers_agent ON ade_agent_triggers (agent_id);
CREATE TABLE IF NOT EXISTS ade_agent_trees (
    run_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    tree TEXT NOT NULL,
    copied_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ade_agent_payloads (
    run_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    payload BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS ade_agent_metrics_daily (
    agent_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    day TEXT NOT NULL,
    runs INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    blocked INTEGER NOT NULL DEFAULT 0,
    stopped INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    wall_s REAL NOT NULL DEFAULT 0,
    active_s REAL NOT NULL DEFAULT 0,
    model_s REAL NOT NULL DEFAULT 0,
    tool_s REAL NOT NULL DEFAULT 0,
    waiting_s REAL NOT NULL DEFAULT 0,
    queue_s REAL NOT NULL DEFAULT 0,
    calls INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    refused INTEGER NOT NULL DEFAULT 0,
    dispatches_sent INTEGER NOT NULL DEFAULT 0,
    dispatches_received INTEGER NOT NULL DEFAULT 0,
    messages_sent INTEGER NOT NULL DEFAULT 0,
    messages_received INTEGER NOT NULL DEFAULT 0,
    questions INTEGER NOT NULL DEFAULT 0,
    answered INTEGER NOT NULL DEFAULT 0,
    permission_asks INTEGER NOT NULL DEFAULT 0,
    granted INTEGER NOT NULL DEFAULT 0,
    denied INTEGER NOT NULL DEFAULT 0,
    owner_turns INTEGER NOT NULL DEFAULT 0,
    cache_read INTEGER NOT NULL DEFAULT 0,
    cache_input INTEGER NOT NULL DEFAULT 0,
    requests INTEGER NOT NULL DEFAULT 0,
    cold_requests INTEGER NOT NULL DEFAULT 0,
    retrieval_calls INTEGER NOT NULL DEFAULT 0,
    surfaced INTEGER NOT NULL DEFAULT 0,
    cited INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    wall_hist TEXT,
    PRIMARY KEY (agent_id, day)
);
CREATE INDEX IF NOT EXISTS ade_agent_metrics_daily_user ON ade_agent_metrics_daily (user_id, day);
CREATE TABLE IF NOT EXISTS ade_agent_tool_daily (
    agent_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    day TEXT NOT NULL,
    tool TEXT NOT NULL,
    calls INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    refused INTEGER NOT NULL DEFAULT 0,
    hist TEXT,
    PRIMARY KEY (agent_id, day, tool)
);
CREATE TABLE IF NOT EXISTS ade_agent_suites (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ade_agent_suites_agent ON ade_agent_suites (agent_id);
CREATE TABLE IF NOT EXISTS ade_agent_suite_items (
    id TEXT PRIMARY KEY,
    suite_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    question TEXT NOT NULL,
    sources TEXT NOT NULL,
    grp TEXT NOT NULL DEFAULT 'rag',
    held_out INTEGER NOT NULL DEFAULT 0,
    answer TEXT,
    superseded TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ade_agent_suite_items_suite ON ade_agent_suite_items (suite_id);
CREATE TABLE IF NOT EXISTS ade_agent_evals (
    id TEXT PRIMARY KEY,
    suite_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    brief_version INTEGER,
    held_out INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ade_agent_evals_suite ON ade_agent_evals (suite_id, started_at);
CREATE TABLE IF NOT EXISTS ade_agent_eval_items (
    eval_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    grp TEXT NOT NULL DEFAULT 'rag',
    run_id TEXT,
    state TEXT NOT NULL,
    hit_at_5 INTEGER,
    rr REAL,
    seen TEXT,
    PRIMARY KEY (eval_id, item_id)
);
CREATE INDEX IF NOT EXISTS ade_agent_eval_items_run ON ade_agent_eval_items (run_id);
CREATE TABLE IF NOT EXISTS ade_agent_facts (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    run_id TEXT,
    subject TEXT NOT NULL,
    relation TEXT NOT NULL,
    object TEXT NOT NULL,
    valid_at INTEGER NOT NULL,
    invalid_at INTEGER,
    recorded_at INTEGER NOT NULL,
    expired_at INTEGER,
    superseded_by TEXT,
    source TEXT,
    basis TEXT NOT NULL,
    contradicts TEXT,
    retracted TEXT,
    changed_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ade_agent_facts_key ON ade_agent_facts (agent_id, subject, relation, expired_at);
CREATE TABLE IF NOT EXISTS ade_agent_memory (
    agent_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    run_id TEXT,
    kind TEXT NOT NULL,
    from_version INTEGER,
    digest TEXT NOT NULL,
    files TEXT NOT NULL,
    made_at INTEGER NOT NULL,
    PRIMARY KEY (agent_id, version)
);
CREATE TABLE IF NOT EXISTS ade_agent_freshness (
    agent_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    day TEXT,
    knowledge TEXT,
    archived TEXT,
    stale_since TEXT,
    reverified_at INTEGER
);
CREATE TABLE IF NOT EXISTS ade_dispatch_chats (
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    granted_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, session_id)
);
CREATE TABLE IF NOT EXISTS ade_agent_skills (
    agent_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    content TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (agent_id, name)
);
CREATE TABLE IF NOT EXISTS ade_agent_versions (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    target TEXT NOT NULL,
    version INTEGER NOT NULL,
    value TEXT,
    author TEXT NOT NULL,
    author_model TEXT,
    approver TEXT,
    proposal_id TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ade_agent_versions_agent ON ade_agent_versions (agent_id, target, version);
CREATE TABLE IF NOT EXISTS ade_improve_proposals (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    agent_id TEXT,
    review_run_id TEXT,
    kind TEXT NOT NULL,
    target TEXT NOT NULL,
    name TEXT,
    current TEXT,
    proposed TEXT,
    hypothesis TEXT,
    evidence TEXT,
    edits INTEGER,
    state TEXT NOT NULL,
    reason TEXT,
    sandbox TEXT,
    result TEXT,
    created_at INTEGER NOT NULL,
    decided_at INTEGER,
    approved_at INTEGER,
    watched_at INTEGER,
    rolls_back TEXT,
    withheld INTEGER
);
CREATE INDEX IF NOT EXISTS ade_improve_proposals_user ON ade_improve_proposals (user_id, state);
CREATE TABLE IF NOT EXISTS ade_improve_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    agent_id TEXT,
    kind TEXT NOT NULL,
    run_id TEXT,
    proposal_id TEXT,
    cost_est_usd REAL NOT NULL DEFAULT 0,
    at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ade_improve_sessions_user ON ade_improve_sessions (user_id, at);
CREATE TABLE IF NOT EXISTS ade_improve_prompts (
    user_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    prompt TEXT NOT NULL,
    proposal_id TEXT,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, version)
);
"""


# Tasks (ade/tasks.py): an agent's job in steps, each attempt a run of the
# agent's; its steps, decisions, checkpoints, journal, effects and packets; the workspace's accounts as their owner
# confirmed them, and each backend's windows; a teammate's offers and custody (ade/task_offers.py); and each task's
# account record, kept beside the journal in a table of its own. Every row is a user's own. Every table is made
# here; each module reads and writes the ones it owns. An offer's `commit` is the one column whose name is
# a word of SQL: it is written quoted, "commit", wherever it is named. A task's grant (ade/task_grants.py) is kept
# in two tables of its own: each version the owner signed, as the passkey signed it, and each widening an agent or a
# linked session asked for, which changes nothing until the owner signs it.
TASKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS ade_agent_tasks (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    title TEXT NOT NULL,
    goal TEXT NOT NULL,
    goal_digest TEXT NOT NULL,
    protected TEXT NOT NULL DEFAULT '[]',
    external TEXT NOT NULL DEFAULT '[]',
    repo TEXT,
    repo_slug TEXT,
    repo_address TEXT,
    checkpoint0 TEXT,
    state TEXT NOT NULL,
    cause TEXT,
    reason TEXT,
    waits_until INTEGER,
    state_version INTEGER NOT NULL DEFAULT 0,
    fence INTEGER NOT NULL DEFAULT 0,
    lease_run TEXT,
    step INTEGER NOT NULL DEFAULT 1,
    backend TEXT NOT NULL,
    asked TEXT,
    home_grp TEXT NOT NULL,
    bound_org TEXT,
    crossings_ended INTEGER NOT NULL DEFAULT 0,
    between_steps TEXT NOT NULL DEFAULT 'checked',
    relay INTEGER NOT NULL DEFAULT 1,
    auto_resume INTEGER NOT NULL DEFAULT 1,
    cross_groups INTEGER NOT NULL,
    taken_out TEXT NOT NULL DEFAULT '[]',
    resumed TEXT NOT NULL DEFAULT '{}',
    moves TEXT NOT NULL DEFAULT '[]',
    custody_digest TEXT,
    custody_cross INTEGER NOT NULL DEFAULT 0,
    continued_from TEXT,
    returned INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    ended_at INTEGER,
    deleted_at INTEGER
);
CREATE INDEX IF NOT EXISTS ade_agent_tasks_user ON ade_agent_tasks (user_id, state);
CREATE INDEX IF NOT EXISTS ade_agent_tasks_agent ON ade_agent_tasks (agent_id);
CREATE TABLE IF NOT EXISTS ade_agent_task_steps (
    task_id TEXT NOT NULL,
    n INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    objective TEXT NOT NULL,
    check_cmd TEXT,
    state TEXT NOT NULL DEFAULT 'todo',
    evidence TEXT,
    checkpoint_id TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    reopened INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (task_id, n)
);
CREATE TABLE IF NOT EXISTS ade_agent_task_decisions (
    task_id TEXT NOT NULL,
    n INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    text TEXT NOT NULL,
    author TEXT NOT NULL,
    run_id TEXT,
    at INTEGER NOT NULL,
    PRIMARY KEY (task_id, n)
);
CREATE TABLE IF NOT EXISTS ade_agent_checkpoints (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    n INTEGER NOT NULL,
    kind TEXT NOT NULL,
    run_id TEXT,
    fence INTEGER,
    manifest TEXT NOT NULL,
    digest TEXT NOT NULL,
    state TEXT NOT NULL,
    why TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE (task_id, n)
);
CREATE TABLE IF NOT EXISTS ade_agent_task_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    task_id TEXT,
    kind TEXT NOT NULL,
    run_id TEXT,
    fence INTEGER,
    at INTEGER NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ade_agent_task_events_task ON ade_agent_task_events (task_id, seq);
CREATE TABLE IF NOT EXISTS ade_agent_effects (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    run_id TEXT,
    fence INTEGER,
    call_id TEXT NOT NULL,
    tool TEXT,
    kind TEXT,
    target TEXT,
    intent TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL,
    exit_status INTEGER,
    resolved TEXT,
    resolved_by TEXT,
    resolved_at INTEGER,
    note TEXT,
    unseen INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE (task_id, run_id, call_id)
);
CREATE INDEX IF NOT EXISTS ade_agent_effects_task ON ade_agent_effects (task_id, state);
CREATE TABLE IF NOT EXISTS ade_agent_packets (
    run_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    packet BLOB NOT NULL,
    digest TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ade_account_owners (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    account TEXT NOT NULL,
    name TEXT,
    report TEXT,
    auth_status TEXT,
    org_id TEXT,
    mismatch TEXT,
    reported_at INTEGER,
    state TEXT NOT NULL DEFAULT 'reported',
    confirmed TEXT,
    pending TEXT,
    hold_id TEXT,
    confirmed_at INTEGER,
    grp TEXT,
    grp_kind TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    in_relays INTEGER NOT NULL DEFAULT 1,
    reserve_lifted INTEGER NOT NULL DEFAULT 0,
    ready INTEGER,
    ready_at INTEGER,
    last_start_at INTEGER,
    worked_at INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ade_account_owners_account ON ade_account_owners (user_id, account);
CREATE TABLE IF NOT EXISTS ade_backend_windows (
    user_id TEXT NOT NULL,
    backend TEXT NOT NULL,
    rate_window TEXT NOT NULL,
    status TEXT NOT NULL,
    resets_at INTEGER,
    utilization REAL,
    overage_status TEXT,
    overage_resets_at INTEGER,
    overage_disabled_reason TEXT,
    at INTEGER,
    seen_at INTEGER NOT NULL,
    run_id TEXT,
    lifted_at INTEGER,
    paid INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, backend, rate_window)
);
CREATE TABLE IF NOT EXISTS ade_agent_task_offers (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    teammate_user TEXT,
    address TEXT,
    kind TEXT NOT NULL,
    state TEXT NOT NULL,
    fence INTEGER NOT NULL,
    packet BLOB,
    packet_digest TEXT,
    repo_address TEXT,
    branch TEXT,
    "commit" TEXT,
    checks_adopted TEXT NOT NULL DEFAULT '[]',
    their_task TEXT,
    hold_id TEXT,
    why TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    expires_at INTEGER,
    pushed TEXT,
    secret_findings TEXT,
    kept_at INTEGER,
    reduce_requested_at INTEGER,
    press_dropped_at INTEGER,
    reread TEXT
);
CREATE INDEX IF NOT EXISTS ade_agent_task_offers_task ON ade_agent_task_offers (task_id, state);
CREATE INDEX IF NOT EXISTS ade_agent_task_offers_teammate ON ade_agent_task_offers (teammate_user, state);
CREATE TABLE IF NOT EXISTS ade_task_custody (
    id TEXT PRIMARY KEY,
    owner_user TEXT NOT NULL,
    custodian_user TEXT,
    address TEXT,
    state TEXT NOT NULL,
    confirmed_at INTEGER,
    why TEXT,
    agents TEXT NOT NULL DEFAULT '[]',
    ended_by TEXT,
    ended_at INTEGER,
    reduced_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    reduce_requested_at INTEGER
);
CREATE INDEX IF NOT EXISTS ade_task_custody_owner ON ade_task_custody (owner_user, state);
CREATE INDEX IF NOT EXISTS ade_task_custody_custodian ON ade_task_custody (custodian_user, state);
CREATE TABLE IF NOT EXISTS ade_task_account_record (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    repo_address TEXT,
    branch TEXT NOT NULL,
    title TEXT,
    kind TEXT NOT NULL,
    run_id TEXT,
    fence INTEGER,
    step INTEGER NOT NULL,
    via TEXT,
    accounts TEXT NOT NULL,
    at INTEGER NOT NULL,
    reduced_at INTEGER,
    statement TEXT,
    statement_at INTEGER
);
CREATE INDEX IF NOT EXISTS ade_task_account_record_task ON ade_task_account_record (user_id, task_id, seq);
CREATE INDEX IF NOT EXISTS ade_task_account_record_agent ON ade_task_account_record (user_id, agent_id);
CREATE TABLE IF NOT EXISTS ade_secret_lifts (
    user_id TEXT NOT NULL,
    repo_address TEXT NOT NULL,
    finding TEXT NOT NULL,
    task_id TEXT NOT NULL,
    offer_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    shape TEXT NOT NULL,
    at INTEGER NOT NULL,
    PRIMARY KEY (user_id, repo_address, finding)
);
CREATE TABLE IF NOT EXISTS ade_task_grants (
    task_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    signature TEXT NOT NULL,
    passkey_id TEXT NOT NULL,
    digest TEXT NOT NULL,
    requester_kind TEXT NOT NULL,
    requester_id TEXT,
    widening_id TEXT,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (task_id, version)
);
CREATE TABLE IF NOT EXISTS ade_task_grant_widenings (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    requester_kind TEXT NOT NULL,
    requester_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    run_id TEXT,
    base_version INTEGER NOT NULL,
    adds TEXT NOT NULL,
    scope TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'waiting',
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    decided_at INTEGER,
    version INTEGER,
    UNIQUE (user_id, task_id, requester_kind, requester_id, client_id)
);
CREATE INDEX IF NOT EXISTS ade_task_grant_widenings_user ON ade_task_grant_widenings (user_id, state);
"""

# The controller's records (ade/controller.py): one controller row per owner; the owner's requests and decisions, word
# for word; the requirements, sessions
# and questions the app took from the workspace's record, each once by its id (ade_controller_actions); and the
# controller's journal, which has no UPDATE path and no DELETE path.
CONTROLLER_SCHEMA = """
CREATE TABLE IF NOT EXISTS ade_controllers (
    user_id TEXT PRIMARY KEY,
    state TEXT NOT NULL DEFAULT 'off',
    state_at INTEGER,
    account TEXT,
    cap INTEGER NOT NULL DEFAULT 2,
    models TEXT NOT NULL,
    efforts TEXT NOT NULL,
    chat_model TEXT NOT NULL,
    chat_effort TEXT NOT NULL,
    confirm_plans INTEGER NOT NULL DEFAULT 0,
    session_id TEXT,
    end_all INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 0,
    mirrored_version INTEGER,
    seq INTEGER NOT NULL DEFAULT 0,
    read_at INTEGER,
    read_behind_at INTEGER,
    listed_at INTEGER,
    read_error TEXT,
    read_error_at INTEGER,
    mirror_error TEXT,
    mirror_error_at INTEGER,
    mirror_error_version INTEGER,
    mirror_error_version_at INTEGER,
    refused INTEGER NOT NULL DEFAULT 0,
    refused_at INTEGER,
    packet TEXT,
    packet_digest TEXT,
    packet_at INTEGER,
    packet_error TEXT,
    packet_error_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ade_controller_requests (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    n INTEGER NOT NULL,
    text TEXT NOT NULL,
    digest TEXT NOT NULL,
    go_at INTEGER,
    planned_at INTEGER,
    withdrawn_at INTEGER,
    created_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ade_controller_requests_n ON ade_controller_requests (user_id, n);
-- the requests no requirement was planned from and the owner did not withdraw, read without the owner's others
CREATE INDEX IF NOT EXISTS ade_controller_requests_waiting ON ade_controller_requests (user_id, n)
    WHERE planned_at IS NULL AND withdrawn_at IS NULL;
CREATE TABLE IF NOT EXISTS ade_controller_requirements (
    id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    title TEXT NOT NULL,
    title_cut INTEGER NOT NULL DEFAULT 0,
    title_hidden INTEGER NOT NULL DEFAULT 0,
    words TEXT NOT NULL,
    acceptance TEXT,
    repos TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open',
    hold INTEGER NOT NULL DEFAULT 0,
    confirm INTEGER NOT NULL DEFAULT 0,
    go_at INTEGER,
    planned_at INTEGER NOT NULL,
    closed_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, id)
);
CREATE INDEX IF NOT EXISTS ade_controller_requirements_user ON ade_controller_requirements (user_id, state);
CREATE INDEX IF NOT EXISTS ade_controller_requirements_request ON ade_controller_requirements (user_id, request_id);
-- the requirements closed lately, read from the newest close back, never the owner's whole history
CREATE INDEX IF NOT EXISTS ade_controller_requirements_closed ON ade_controller_requirements (user_id, closed_at);
CREATE TABLE IF NOT EXISTS ade_controller_tasks (
    id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    requirement_id TEXT,
    session_id TEXT,
    repo TEXT,
    branch TEXT,
    base TEXT,
    account TEXT,
    program TEXT,
    model TEXT,
    effort TEXT,
    state TEXT NOT NULL,
    waits_until INTEGER,
    started_at INTEGER NOT NULL,
    stopped_at INTEGER,
    ended_at INTEGER,
    unlisted INTEGER NOT NULL DEFAULT 0,
    continues INTEGER NOT NULL DEFAULT 0,
    flag TEXT,
    flagged_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, id)
);
CREATE INDEX IF NOT EXISTS ade_controller_tasks_requirement ON ade_controller_tasks (user_id, requirement_id, state);
-- the page reads and the cap's count read what is open, ended lately or flagged lately, never the owner's whole
-- history: open sessions by state, ended ones by when they ended, a requirement's newest ended first, the newest flag
CREATE INDEX IF NOT EXISTS ade_controller_tasks_state ON ade_controller_tasks (user_id, state, started_at);
CREATE INDEX IF NOT EXISTS ade_controller_tasks_ended ON ade_controller_tasks (user_id, ended_at);
CREATE INDEX IF NOT EXISTS ade_controller_tasks_requirement_ended ON ade_controller_tasks
    (user_id, requirement_id, ended_at);
CREATE INDEX IF NOT EXISTS ade_controller_tasks_flagged ON ade_controller_tasks (user_id, flagged_at);
CREATE TABLE IF NOT EXISTS ade_controller_questions (
    id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    requirement_id TEXT,
    text TEXT NOT NULL,
    cut INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'open',
    asked_at INTEGER NOT NULL,
    answered_at INTEGER,
    decision_id TEXT,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, id)
);
CREATE INDEX IF NOT EXISTS ade_controller_questions_user ON ade_controller_questions (user_id, state);
CREATE INDEX IF NOT EXISTS ade_controller_questions_answered ON ade_controller_questions (user_id, answered_at);
CREATE TABLE IF NOT EXISTS ade_controller_decisions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    n INTEGER NOT NULL,
    question_id TEXT,
    text TEXT NOT NULL,
    at INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ade_controller_decisions_n ON ade_controller_decisions (user_id, n);
CREATE TABLE IF NOT EXISTS ade_controller_actions (
    user_id TEXT NOT NULL,
    id TEXT NOT NULL,
    kind TEXT NOT NULL,
    taken INTEGER NOT NULL,
    at INTEGER NOT NULL,
    PRIMARY KEY (user_id, id)
);
CREATE TABLE IF NOT EXISTS ade_controller_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    requirement_id TEXT,
    task_id TEXT,
    at INTEGER NOT NULL,
    recorded_at INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ade_controller_events_user ON ade_controller_events (user_id, seq);
CREATE INDEX IF NOT EXISTS ade_controller_events_kind ON ade_controller_events (user_id, kind, recorded_at);
CREATE INDEX IF NOT EXISTS ade_controller_events_at ON ade_controller_events (user_id, kind, at);
-- a board row's newest lines, read from the newest back (_events_for)
CREATE INDEX IF NOT EXISTS ade_controller_events_requirement ON ade_controller_events
    (user_id, requirement_id, at, seq);
CREATE INDEX IF NOT EXISTS ade_controller_events_task ON ade_controller_events (user_id, task_id, at, seq);
-- a requirement's own manager, a durable task of the controller's: one row
-- for each requirement planned with a share. Its state is no column: it is read from these (controller.manager_state)
CREATE TABLE IF NOT EXISTS ade_controller_managers (
    user_id TEXT NOT NULL,
    requirement_id TEXT NOT NULL,
    share INTEGER NOT NULL,
    fence INTEGER NOT NULL DEFAULT 0,
    lease TEXT,
    woken INTEGER NOT NULL DEFAULT 0,
    woken_at INTEGER,
    wakes TEXT NOT NULL DEFAULT '[]',
    more_wakes INTEGER NOT NULL DEFAULT 0,
    waits TEXT,
    waits_until INTEGER,
    waits_since INTEGER,
    waits_models TEXT,
    failures INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    reset_at INTEGER,
    told_at INTEGER,
    for_owner TEXT,
    for_owner_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, requirement_id)
);
-- the managers that wait for something besides a change, read by the sweep without the others
CREATE INDEX IF NOT EXISTS ade_controller_managers_waits ON ade_controller_managers (user_id, waits);
-- each attempt of a manager's: a fresh chat in the workspace, given a packet the app built
CREATE TABLE IF NOT EXISTS ade_controller_attempts (
    user_id TEXT NOT NULL,
    id TEXT NOT NULL,
    requirement_id TEXT NOT NULL,
    n INTEGER NOT NULL,
    fence INTEGER NOT NULL,
    state TEXT NOT NULL,
    outcome TEXT,
    cause TEXT,
    refused TEXT,
    refused_count INTEGER NOT NULL DEFAULT 0,
    refused_models TEXT,
    note TEXT,
    note_cut INTEGER NOT NULL DEFAULT 0,
    note_hidden INTEGER NOT NULL DEFAULT 0,
    woken_by TEXT NOT NULL DEFAULT '[]',
    packet TEXT,
    packet_digest TEXT,
    session_id TEXT,
    waits_until INTEGER,
    late INTEGER NOT NULL DEFAULT 0,
    untaken INTEGER NOT NULL DEFAULT 0,
    queued_at INTEGER NOT NULL,
    sent_at INTEGER,
    started_at INTEGER,
    ended_at INTEGER,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ade_controller_attempts_n ON ade_controller_attempts (user_id, requirement_id, n);
-- the attempts not ended, and those queued in the last day (ATTEMPTS_A_DAY), read without the owner's others
CREATE INDEX IF NOT EXISTS ade_controller_attempts_state ON ade_controller_attempts (user_id, state);
CREATE INDEX IF NOT EXISTS ade_controller_attempts_queued ON ade_controller_attempts (user_id, queued_at);
"""


# The skills the owner's sessions called (ade/skill_counts.py): one row per call, taken once by its own line id
# from the workspace's record of them, and where the app read that record to. A call is the session's word (its hook
# wrote it), kept for display and never as a record that holds against a session.
SKILLS_SCHEMA = """
CREATE TABLE IF NOT EXISTS ade_skill_reads (
    user_id TEXT PRIMARY KEY,
    store_id TEXT,
    since INTEGER NOT NULL DEFAULT 0,
    read_at INTEGER,
    read_behind_at INTEGER,
    read_error TEXT,
    read_error_at INTEGER,
    unserved_at INTEGER,
    polled_at INTEGER,
    missed INTEGER NOT NULL DEFAULT 0,
    dropped INTEGER NOT NULL DEFAULT 0,
    refused INTEGER NOT NULL DEFAULT 0,
    trimmed INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ade_skill_calls (
    user_id TEXT NOT NULL,
    line_id TEXT NOT NULL,
    at INTEGER NOT NULL,
    skill TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    failed INTEGER,
    version TEXT,
    session_id TEXT NOT NULL,
    repo TEXT,
    task_id TEXT,
    taken_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, line_id)
);
-- the page reads the last days, never the owner's whole history: a window by time, one skill's calls by time, and the
-- calls of a task; the round's daily forget deletes by time across every owner, which an index that leads with the
-- owner cannot serve
CREATE INDEX IF NOT EXISTS ade_skill_calls_when ON ade_skill_calls (at);
CREATE INDEX IF NOT EXISTS ade_skill_calls_at ON ade_skill_calls (user_id, at);
CREATE INDEX IF NOT EXISTS ade_skill_calls_skill ON ade_skill_calls (user_id, kind, skill, at);
CREATE INDEX IF NOT EXISTS ade_skill_calls_task ON ade_skill_calls (user_id, task_id) WHERE task_id IS NOT NULL;
"""


# The improve loop's proposals for a repository's business skills (ade/skill_improve.py): each proposal, kept in a
# table of its own and never among
# an agent's (ade_improve_proposals), whose readers are an agent's; each repository's switch, off by default; and the
# app's copy of the workspace's record of proposals, taken only where the workspace said its signature holds and only
# at a generation above the copy's, which the app sends back as its word and from which the owner's Restore writes it.
SKILL_IMPROVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS ade_skill_proposals (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    repo TEXT NOT NULL,
    folder TEXT NOT NULL,
    path TEXT NOT NULL,
    kind TEXT NOT NULL,
    base TEXT,
    head TEXT,
    branch TEXT,
    object_format TEXT,
    mode TEXT,
    current_version TEXT,
    current_blob TEXT,
    blob TEXT,
    fresh INTEGER,
    current_text TEXT,
    proposed_text TEXT,
    hypothesis TEXT,
    counts TEXT,
    notes TEXT,
    state TEXT NOT NULL,
    reason TEXT,
    pr_url TEXT,
    found TEXT,
    rolls_back TEXT,
    heads TEXT,
    ended TEXT,
    ended_at INTEGER,
    created_at INTEGER NOT NULL,
    decided_at INTEGER,
    merged_at INTEGER,
    watched_at INTEGER
);
CREATE INDEX IF NOT EXISTS ade_skill_proposals_user ON ade_skill_proposals (user_id, state);
CREATE INDEX IF NOT EXISTS ade_skill_proposals_folder ON ade_skill_proposals (user_id, repo, folder);
CREATE TABLE IF NOT EXISTS ade_skill_improve_repos (
    user_id TEXT NOT NULL,
    repo TEXT NOT NULL,
    turned_on INTEGER NOT NULL DEFAULT 0,
    account TEXT,
    model TEXT,
    url TEXT,
    branch TEXT,
    changed_at INTEGER NOT NULL,
    reviewed TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (user_id, repo)
);
CREATE TABLE IF NOT EXISTS ade_skill_records (
    user_id TEXT PRIMARY KEY,
    generation INTEGER NOT NULL,
    record TEXT NOT NULL,
    read_at INTEGER NOT NULL
);
"""


# Columns added to a table after it was first deployed. CREATE TABLE IF NOT EXISTS leaves an existing table as it is,
# so a database made before a column was added gets it here, once; nothing is ever removed or rewritten.
# the agents' columns added after their first release (a store made then gets them here)
AGENTS_ADDED = (
    ('ade_agents', 'mcp', "TEXT NOT NULL DEFAULT '{}'"),
    ('ade_agents', 'mcp_state', 'TEXT'),
    ('ade_agents', 'unattended_mode', "TEXT NOT NULL DEFAULT 'dontAsk'"),
    ('ade_agents', 'precheck', 'TEXT'),
    ('ade_agents', 'max_budget_usd', 'REAL'),
    ('ade_agents', 'est_usd_per_day', 'REAL'),
    ('ade_agents', 'paused_until', 'INTEGER'),
    ('ade_agents', 'autocompact_window', 'INTEGER'),
    ('ade_agents', 'max_compactions', 'INTEGER NOT NULL DEFAULT 2'),
    ('ade_agents', 'may_dispatch', 'INTEGER NOT NULL DEFAULT 0'),
    ('ade_agents', 'accepts_dispatch', 'INTEGER NOT NULL DEFAULT 0'),
    ('ade_agents', 'max_dispatch_per_run', 'INTEGER NOT NULL DEFAULT 5'),
    ('ade_agents', 'relations', "TEXT NOT NULL DEFAULT '{}'"),
    ('ade_agents', 'facts_version', 'INTEGER NOT NULL DEFAULT 0'),
    ('ade_agents', 'facts_mirrored', 'INTEGER NOT NULL DEFAULT 0'),
    ('ade_agents', 'decay_runs', 'INTEGER NOT NULL DEFAULT 30'),
    ('ade_agents', 'reverify', 'INTEGER NOT NULL DEFAULT 0'),
    ('ade_agents', 'anomaly_pause', 'INTEGER NOT NULL DEFAULT 0'),
    ('ade_agents', 'judge_runs', 'INTEGER NOT NULL DEFAULT 0'),
    ('ade_agents', 'acceptance', 'TEXT'),
    ('ade_agents', 'improve', 'INTEGER NOT NULL DEFAULT 0'),
    ('ade_agents', 'reviewed_at', 'INTEGER'),
    # why the day's review passed the agent over, and when (improve.maybe_review): its digest too long
    ('ade_agents', 'review_skipped', 'TEXT'),
    # whether a proposal pruned as it was taken holds one of its evidence runs' prompts word for word, so that `tried`
    # lists it with no value (improve._propose, improve.tried): 1 or 0 as it was taken; nothing for any other, and for
    # one kept before this column, which tried checks as it reads it
    ('ade_improve_proposals', 'withheld', 'INTEGER'),
    # holds the workspace made, copied here and not yet mirrored as paused (agents._apply, agents._mirror)
    ('ade_agents', 'hold_unmirrored', 'INTEGER NOT NULL DEFAULT 0'),
    # the last commit a received task's workspace pushed to the owner's task's branch (task_offers._push_theirs)
    ('ade_agent_task_offers', 'pushed', 'TEXT'),
    # the findings, by their ids, that held the offer's push or the teammate's side's (task_offers._findings), which
    # the person whose push it is may lift (task_offers.lift_secret)
    ('ade_agent_task_offers', 'secret_findings', 'TEXT'),
    # when the custodian pressed Keep on a custody task (task_offers.keep): a column of its own, not the offer's `why`,
    # which a note (a secret shape in the teammate's work) writes over
    ('ade_agent_task_offers', 'kept_at', 'INTEGER'),
    # when the operator's command asked to reduce a person in an offer or custody row that still stood, which the
    # scheduler's round then reduces once it has ended (task_offers.reduce_person, task_offers.forget)
    ('ade_agent_task_offers', 'reduce_requested_at', 'INTEGER'),
    ('ade_task_custody', 'reduce_requested_at', 'INTEGER'),
    # when a confirmation moved the account the offered task's press names, while the offer was out: the press is
    # dropped as the offer ends, accepted or not (tasks._presses_dropped, task_offers._drop_marked_press)
    ('ade_agent_task_offers', 'press_dropped_at', 'INTEGER'),
    # where a confirmation read the offered task's organisation otherwise while the offer was out: the organisation
    # and its reading then, the binding applied as the offer ends, untaken or accepted (tasks._organisation_reread,
    # tasks.apply_held_reread)
    ('ade_agent_task_offers', 'reread', 'TEXT'),
    ('ade_agent_runs', 'resets_at', 'INTEGER'),
    ('ade_agent_runs', 'context', 'TEXT'),
    ('ade_agent_runs', 'parent_run_id', 'TEXT'),
    ('ade_agent_runs', 'depth', 'INTEGER NOT NULL DEFAULT 0'),
    ('ade_agent_runs', 'dispatches', 'INTEGER NOT NULL DEFAULT 0'),
    ('ade_agent_runs', 'waiting_on', 'TEXT'),
    ('ade_agent_runs', 'metrics', 'TEXT'),
    ('ade_agent_runs', 'rolled', 'INTEGER NOT NULL DEFAULT 0'),
    ('ade_agent_runs', 'memory_digest', 'TEXT'),
    ('ade_agent_runs', 'memory_copied', 'INTEGER NOT NULL DEFAULT 0'),
    ('ade_agent_runs', 'judge', 'TEXT'),
    ('ade_agent_runs', 'judge_asked', 'INTEGER NOT NULL DEFAULT 0'),
    ('ade_agent_runs', 'anomalies', 'TEXT'),
    ('ade_agent_runs', 'acceptance', 'TEXT'),
    ('ade_agent_runs', 'replay_of', 'TEXT'),
    ('ade_agent_runs', 'loop', 'INTEGER NOT NULL DEFAULT 0'),
    ('ade_agent_runs', 'flag', 'TEXT'),
    ('ade_agent_runs', 'changes', 'TEXT'),
    ('ade_agent_runs', 'changes_taken', 'INTEGER NOT NULL DEFAULT 0'),
    ('ade_agent_runs', 'rewritten', 'TEXT'),
    ('ade_agent_runs', 'judge_error', 'TEXT'),
    ('ade_agent_sync', 'swept_day', 'TEXT'),
    # a run that is a task's attempt (ade/tasks.py): its task, step, attempt number and fence, the backend
    # it ran on (a BackendRef as JSON; NULL for an agent's own run, which runs on its agent's account), its packet's
    # digest, the resume check's outcome, the model served, who chose its backend, and the claude that ran it
    ('ade_agent_runs', 'task_id', 'TEXT'),
    ('ade_agent_runs', 'task_step', 'INTEGER'),
    ('ade_agent_runs', 'task_attempt', 'INTEGER'),
    ('ade_agent_runs', 'fence', 'INTEGER'),
    ('ade_agent_runs', 'backend', 'TEXT'),
    ('ade_agent_runs', 'packet_digest', 'TEXT'),
    ('ade_agent_runs', 'resume', 'TEXT'),
    ('ade_agent_runs', 'served_model', 'TEXT'),
    ('ade_agent_runs', 'chosen_by', 'TEXT'),
    ('ade_agent_runs', 'claude_version', 'TEXT'),
)

ADDED_COLUMNS = (("ade_repos", "verify_config", "TEXT"), ("users", "token_gen", "INTEGER NOT NULL DEFAULT 0"),
                 # when the round last looked at an owner's machine and found it down or the read failed, so that the
                 # round does not look again for an hour (skill_counts.users_to_poll): an older store's table has no
                 # such column, and gets it here
                 ("ade_skill_reads", "polled_at", "INTEGER"),
                 # a passkey's tier (hardware_keys.py): hardware or device, its model's AAGUID and maker, why it is a
                 # device passkey, and, while no valid FIDO metadata was there to judge it, its attestation, kept to
                 # judge it again; the account's high security (passkeys.py)
                 ("passkeys", "tier", "TEXT NOT NULL DEFAULT 'device'"), ("passkeys", "aaguid", "TEXT"),
                 ("passkeys", "maker", "TEXT"), ("passkeys", "tier_why", "TEXT"),
                 ("passkeys", "judge_again", "INTEGER NOT NULL DEFAULT 0"), ("passkeys", "attestation", "TEXT"),
                 # the backup-eligible flag a passkey was added with, which every later use must carry (passkeys.py);
                 # NULL in a row from before, until its next use
                 ("passkeys", "backup_eligible", "INTEGER"),
                 # whether a hardware key's working with a phone rests on the browser's word (hardware_keys.judge)
                 ("passkeys", "phone_by_browser", "INTEGER NOT NULL DEFAULT 0"),
                 # the sha256 of each certificate of a hardware key's attestation, and why one kept as a hardware key
                 # is reported (passkeys.reconsider)
                 ("passkeys", "attestation_certs", "TEXT"), ("passkeys", "tier_report", "TEXT"),
                 ("users", "high_security", "INTEGER NOT NULL DEFAULT 0"),
                 # what confirmed a link's ticket (ade/link.py): a hardware key or another way, and which passkey
                 ("ade_link_tickets", "confirmed", "TEXT"), ("ade_link_tickets", "confirmed_by", "TEXT"),
                 ("ade_links", "reads_others", "INTEGER NOT NULL DEFAULT 0"), ("device_signins", "asked_from", "TEXT"),
                 ("device_signins", "asked_net", "TEXT"), ("device_signins", "asked_wide", "TEXT"),
                 ("users", "asks_on", "INTEGER NOT NULL DEFAULT 0"),
                 # the effort a session started from an agent's ask was started with, as the person chose it at Start
                 # (none for Claude Code's default): the app's own copy, which the session's programs cannot
                 # write, given to the workspace at each later start of it (ade/workspace_routes.py, start_session)
                 ("ade_ask_answers", "effort", "TEXT"),
                 # whether the account lets apps connect through the Claude app connector (oauth.py)
                 ("users", "connector_on", "INTEGER NOT NULL DEFAULT 0"),
                 # the session channel's commands (ade/link_commands.py): a passkey-signed payload and its state on the
                 # event, a stop and its signed resume on the link
                 ("ade_link_events", "payload", "TEXT"), ("ade_link_events", "signature", "TEXT"),
                 ("ade_link_events", "passkey_id", "TEXT"), ("ade_link_events", "to_id", "TEXT"),
                 ("ade_link_events", "expires_at", "INTEGER"), ("ade_link_events", "withdrawn_at", "INTEGER"),
                 ("ade_link_events", "taken_at", "INTEGER"), ("ade_link_events", "taken_by", "TEXT"),
                 ("ade_link_events", "taken_instance", "TEXT"), ("ade_link_events", "renewed_at", "INTEGER"),
                 ("ade_link_events", "retried_at", "INTEGER"),
                 ("ade_link_events", "conflict_at", "INTEGER"), ("ade_link_events", "conflict_instance", "TEXT"),
                 ("ade_link_events", "mirror", "TEXT"),
                 ("ade_links", "stopped_at", "INTEGER"), ("ade_links", "stop_acked_at", "INTEGER"),
                 ("ade_links", "resume_payload", "TEXT"), ("ade_links", "resume_signature", "TEXT"),
                 ("ade_links", "resume_passkey_id", "TEXT"), ("ade_links", "resumed_at", "INTEGER"),
                 # the History list (ade/link.py): where a session ran, whether it was running, whether its title and
                 # conversation stay on its machine; whether the link's list lets conversations go
                 ("ade_link_history", "client", "TEXT"), ("ade_link_history", "live", "TEXT"),
                 ("ade_link_history", "withheld", "INTEGER NOT NULL DEFAULT 0"),
                 ("ade_links", "history_talks", "INTEGER NOT NULL DEFAULT 0"),
                 # a session as its machine's child (ade/link.py): a mirrored note's session, when that session is
                 # registered, and when its words were said; whether a listed session ran this release's start hook in
                 # its process, and the repositories it worked in; the machine a link lists
                 ("ade_link_events", "session_id", "TEXT"), ("ade_link_events", "said_at", "INTEGER"),
                 # the session a mirrored note named and was not tagged with (its link's list did not have it
                 # registered then): never shown, kept so the note goes once that list says the session is withheld
                 ("ade_link_events", "untagged_session", "TEXT"),
                 # a Stop's last note: from when it posted its turn's words whole, in milliseconds, so the ADE tells the
                 # rounds of its live view it replaced (ade/link.py, _posted_again)
                 ("ade_link_events", "whole_since", "INTEGER"),
                 ("ade_link_history", "registered", "INTEGER NOT NULL DEFAULT 0"),
                 ("ade_link_history", "repositories", "TEXT"), ("ade_links", "machine", "TEXT"),
                 # end-to-end encryption (ade/e2e_keys.py): whether a passkey gives the PRF a key is unlocked with, and
                 # a History row sealed whole, its title, project, branch and repositories inside; an event the server
                 # sealed in place as encryption was turned on (its link sent it in the clear); the passkey that signed
                 # or confirmed a change that waits, and whether the recovery code's proof came with it
                 ("passkeys", "prf", "INTEGER NOT NULL DEFAULT 0"), ("ade_link_history", "sealed", "TEXT"),
                 ("ade_link_events", "sealed_in_place", "INTEGER"), ("ade_e2e_pending", "made_by", "TEXT"),
                 # the owner's inbox: a prompt's or a linked session's proposal's deadline, in
                 # unix seconds by the server's clock; whether a prompt is an item to look at, which the list never
                 # decides; why a prompt or a proposal closed (answer, owner, report:<Event>, deadline, signed); whether
                 # an answer of the owner's is to a prompt, which only the prompt's hook claims; the link a proposal is
                 # for; and the owner's switch that lets a link propose
                 ("ade_link_events", "deadline_at", "INTEGER"),
                 ("ade_link_events", "look", "INTEGER NOT NULL DEFAULT 0"),
                 ("ade_link_events", "closed_reason", "TEXT"),
                 ("ade_link_events", "prompt_answer", "INTEGER NOT NULL DEFAULT 0"),
                 ("ade_link_events", "for_id", "TEXT"), ("ade_links", "proposes", "INTEGER NOT NULL DEFAULT 0"),
                 # the owner's Close of a prompt (ade/link.py): when it hid the prompt from their open
                 # list, which leaves it open and answerable until its deadline, and the account that hid it
                 ("ade_link_events", "hidden_at", "INTEGER"), ("ade_link_events", "hidden_by", "TEXT"),
                 ("ade_e2e_pending", "by_code", "INTEGER"),
                 # post-quantum keys: whether the account's keys are post-quantum, and when the
                 # database is compacted a second time after its upgrade; the master key a post-quantum credential's
                 # wrap names (its anchor, in the wrap's AAD); a conversation's key, sealed in form 2, and its id; and
                 # the forms a link last reported it can seal
                 ("ade_e2e_state", "pq", "INTEGER NOT NULL DEFAULT 0"), ("ade_e2e_state", "compact_again_at",
                                                                          "INTEGER"),
                 ("ade_e2e_credentials", "anchor", "TEXT"),
                 ("ade_link_history_talks", "key", "TEXT"), ("ade_link_history_talks", "key_id", "TEXT"),
                 ("ade_link_e2e", "seals", "TEXT"),
                 # a request approved to replace the live cloud link of its name (ade/link_asks.py): that link's id;
                 # the wider network a request came from, which its limits count by; and when it last changed, which
                 # the day it is kept counts from
                 ("ade_link_asks", "replaces", "TEXT"), ("ade_link_asks", "asked_wide", "TEXT"),
                 ("ade_link_asks", "changed_at", "INTEGER"),
                 # a session paired by a ticket waits as an approved request does (ade/link_asks.by_ticket): how the
                 # request was made (ticket; an ask's is null), its ticket, and what confirmed that ticket
                 ("ade_link_asks", "via", "TEXT"), ("ade_link_asks", "ticket_id", "TEXT"),
                 ("ade_link_asks", "confirmed", "TEXT"), ("ade_link_asks", "confirmed_by", "TEXT"),
                 # how a link is heard (ade/link.py heard_through): when a request its client's hook
                 # signed was last heard, and when its client's keeper last beat, which is presence, not a watcher's
                 # heartbeat (last_heartbeat_at, which the app reads as the watcher's)
                 ("ade_links", "hook_heard_at", "INTEGER"), ("ade_links", "keeper_heard_at", "INTEGER"),
                 # the Claude app connector (oauth.py): the wider network a registration, and a request to allow an
                 # app, came from, which their caps count by as they count its own network; and the passkey whose
                 # confirmation allowed a connection, so removing that passkey revokes it (none for the password's)
                 ("ade_connector_clients", "wide", "TEXT"), ("ade_connector_requests", "asked_wide", "TEXT"),
                 ("ade_connector_grants", "confirmed_by", "TEXT"),
                 # how a request to allow an app reached /oauth/authorize, as its browser said: its Sec-Fetch-Site, and
                 # the origin of its Referer
                 ("ade_connector_requests", "fetch_site", "TEXT"), ("ade_connector_requests", "came_from", "TEXT"),
                 # the sha256 of the value of the cookie /oauth/authorize set in the browser that made the request, so
                 # the pane can say when another browser shows it
                 ("ade_connector_requests", "browser", "TEXT"),
                 # what confirmed a device's approval (a passkey's id, or auth.BY_PASSWORD), and when the sign-in it
                 # gave was ended, so removing that passkey ends it (auth.devices_going)
                 ("device_signins", "confirmed_by", "TEXT"), ("device_signins", "ended_at", "INTEGER"),
                 # a task's acceptance as checks: a request's checks and
                 # protected paths as the owner wrote them, the ones a requirement's plan chose and the protected paths
                 # of its repositories, and a task's last round of checks as the app took it, why its end was decided
                 # without one, and whether one of its rounds is queued or running
                 ("ade_controller_requests", "checks", "TEXT"), ("ade_controller_requirements", "checks", "TEXT"),
                 ("ade_controller_requirements", "protected", "TEXT"), ("ade_controller_tasks", "checks", "TEXT"),
                 ("ade_controller_tasks", "why", "TEXT"),
                 ("ade_controller_tasks", "checking", "INTEGER NOT NULL DEFAULT 0"),
                 # a requirement's own manager: the managers' version and
                 # what the workspace holds of it, the managers' route's failure, and the room the newest page gave;
                 # a task's text as its attempt wrote it, the attempt that started it, whether it needs its owner and
                 # its state's change number as the newest page gave them; the requirement a decision given with a
                 # Resume is on
                 ("ade_controllers", "managers_version", "INTEGER NOT NULL DEFAULT 0"),
                 ("ade_controllers", "managers_sent", "INTEGER"), ("ade_controllers", "managers_held", "INTEGER"),
                 ("ade_controllers", "managers_error", "TEXT"), ("ade_controllers", "managers_error_at", "INTEGER"),
                 ("ade_controllers", "room", "TEXT"), ("ade_controllers", "room_at", "INTEGER"),
                 # a linked session that holds the Manager's role (ade/manager_link.py): the link, the id
                 # of the payload the owner signed for it, when it took the role and when its hold ends, and the digest
                 # of the settings it holds the role under
                 ("ade_controllers", "holder_link", "TEXT"), ("ade_controllers", "holder_payload", "TEXT"),
                 ("ade_controllers", "holder_since", "INTEGER"), ("ade_controllers", "holder_until", "INTEGER"),
                 ("ade_controllers", "holder_settings", "TEXT"),
                 # the seq of the signed instruction a goal was sent to a linked Manager as
                 ("ade_controller_requests", "instruction_seq", "INTEGER"),
                 ("ade_controller_tasks", "text", "TEXT"),
                 ("ade_controller_tasks", "text_cut", "INTEGER NOT NULL DEFAULT 0"),
                 ("ade_controller_tasks", "text_hidden", "INTEGER NOT NULL DEFAULT 0"),
                 ("ade_controller_tasks", "attempt_id", "TEXT"),
                 ("ade_controller_tasks", "needs_owner", "INTEGER NOT NULL DEFAULT 0"),
                 ("ade_controller_tasks", "changed", "INTEGER"),
                 # the linked Manager whose own report a task is, run on its machine
                 ("ade_controller_tasks", "reported_by", "TEXT"),
                 ("ade_controller_decisions", "requirement_id", "TEXT"),
                 # a workspace's share of the cap and what leaving the hosted choice keeps (ade/fly_provider.py)
                 ("ade_workspaces", "left_at", "INTEGER"), ("ade_workspaces", "storage_gb", "REAL"),
                 ("ade_workspace_spend", "user_id", "TEXT"),
                 # the hook token the app made for a hosted workspace's sessions (fly_provider.py): its id
                 # alone, the token itself being kept nowhere here, so the next configuration's token can replace it
                 # and its row's removal revoke it (ade_workspaces_token_gone, below)
                 ("ade_workspaces", "hook_token_id", "TEXT"),
                 # a version of a task's grant withdrawn as the passkey that signed it went (ade/task_grants.py,
                 # passkeys_going): when, and why (removed, or high security turned on)
                 ("ade_task_grants", "withdrawn_at", "INTEGER"), ("ade_task_grants", "withdrawn_why", "TEXT"),
                 # the browser's word on the authenticator that made a passkey (passkeys.register: platform or
                 # cross-platform, NULL where it said nothing or in a row from before), which names it and says its kind
                 ("passkeys", "attachment", "TEXT"),
                 # 1 where the server gave a passkey's name (passkeys.named_by_kind) and the person has given none
                 # since, so it is named again when what it is changes (passkeys.rename_by_kind); NULL in a row from
                 # before, whose name is left as it is
                 ("passkeys", "name_by_server", "INTEGER"),
                 # what a linked session's client last reported of its own Claude Code session with a heartbeat, as
                 # JSON (ade/link.py, heartbeat), and when it came; NULL where it reported none
                 ("ade_links", "reported", "TEXT"), ("ade_links", "reported_at", "INTEGER"))


# made once the columns they name are there, in a store made before them too; and the connector's codes and tokens by
# their end, which its prune (oauth.py) deletes by, added after the table was
AFTER_MIGRATE = ("CREATE INDEX IF NOT EXISTS ade_agent_runs_parent ON ade_agent_runs (parent_run_id);\n"
                 "CREATE INDEX IF NOT EXISTS ade_agent_runs_task ON ade_agent_runs (task_id, fence);\n"
                 "CREATE INDEX IF NOT EXISTS ade_link_events_session ON ade_link_events (link_id, session_id) "
                 "WHERE session_id IS NOT NULL;\n"
                 "CREATE INDEX IF NOT EXISTS ade_link_events_untagged ON ade_link_events (link_id, untagged_session) "
                 "WHERE untagged_session IS NOT NULL;\n"
                 # a link's live view's rounds of the hour, and its notes the oldest first (ade/link.py)
                 "CREATE INDEX IF NOT EXISTS ade_link_events_mirror ON ade_link_events (link_id, mirror, "
                 "received_at);\n"
                 # and the Stops' notes that say they posted a turn whole, which the timeline and the cap read
                 "CREATE INDEX IF NOT EXISTS ade_link_events_whole ON ade_link_events (link_id, seq) "
                 "WHERE whole_since IS NOT NULL;\n"
                 # each link's own posts, by account and seq: whether a waiting is its link's newest
                 "CREATE INDEX IF NOT EXISTS ade_link_events_own ON ade_link_events (user_id, link_id, seq) "
                 "WHERE mirror IS NULL;\n"
                 "CREATE INDEX IF NOT EXISTS ade_link_asks_wide ON ade_link_asks (asked_wide, created_at);\n"
                 "CREATE INDEX IF NOT EXISTS ade_connector_tokens_expires ON ade_connector_tokens (expires_at);\n"
                 "CREATE INDEX IF NOT EXISTS ade_connector_clients_wide ON ade_connector_clients (kind, wide, "
                 "created_at);\n"
                 "CREATE INDEX IF NOT EXISTS ade_connector_requests_wide ON ade_connector_requests (asked_wide, "
                 "expires_at);\n"
                 "CREATE INDEX IF NOT EXISTS ade_connector_grants_confirmed ON ade_connector_grants (confirmed_by) "
                 "WHERE confirmed_by IS NOT NULL;\n"
                 # the decisions given with a Resume, which the controller is not given: counted without reading the
                 # owner's others
                 "CREATE INDEX IF NOT EXISTS ade_controller_decisions_requirement ON ade_controller_decisions "
                 "(user_id, requirement_id) WHERE requirement_id IS NOT NULL;\n"
                 # an agent's change time moves on with each change, and past its last mirror: agents._mirror tells the
                 # workspace of what changed after that (mirrored_at < updated_at), and the change times are in
                 # seconds, so a change in the second of the one before it, or of the last mirror, would otherwise
                 # read as told; whoever writes it
                 "CREATE TRIGGER IF NOT EXISTS ade_agents_changed AFTER UPDATE OF updated_at ON ade_agents "
                 "WHEN NEW.updated_at <= MAX(OLD.updated_at, COALESCE(NEW.mirrored_at, 0)) BEGIN "
                 "UPDATE ade_agents SET updated_at = MAX(OLD.updated_at, COALESCE(NEW.mirrored_at, 0)) + 1 "
                 "WHERE id = NEW.id; END;\n"
                 # a hosted workspace's hook token goes with its row: whatever removes the row, the
                 # workspace's destroy or an operator, the token is revoked in the same statement, so no
                 # hook in a machine that is gone keeps asking this deployment's queue
                 "CREATE TRIGGER IF NOT EXISTS ade_workspaces_token_gone AFTER DELETE ON ade_workspaces "
                 "WHEN OLD.hook_token_id IS NOT NULL BEGIN "
                 "UPDATE ade_hook_tokens SET revoked_at = CAST(strftime('%s', 'now') AS INTEGER) "
                 "WHERE id = OLD.hook_token_id AND user_id = OLD.user_id AND revoked_at IS NULL; END;")


def _migrate(conn):
    have = {}
    for table, column, kind in ADDED_COLUMNS + AGENTS_ADDED:
        if table not in have:
            have[table] = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in have[table]:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
            except sqlite3.OperationalError as e:
                # two connections that open a store lacking the column at once both read that it lacks it, and the
                # second to add it is told it is there: it is, which is all this asks (an upgraded store's first
                # connections, a server's start among them, would otherwise fail)
                if not str(e).startswith("duplicate column name"):
                    raise
            have[table].add(column)
    conn.executescript(AFTER_MIGRATE)


def _create_private(path):
    """Create the database file readable by this user only, whatever the umask of the process that first opens it
    (the first invitation is made from a shell); SQLite gives its journal the same permissions as the database."""
    try:
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except FileExistsError:
        pass


@contextmanager
def connect(path=None):
    """A connection to the database (config.db_path, or `path`: a thread that runs after its request, as e2e_keys'
    compaction does, keeps the one its request wrote), committed when the block ends without an error."""
    path = path or config.db_path()
    _create_private(path)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        # what is deleted or written over is overwritten with zeros in the file, not left in its free pages; and the
        # journal is a write-ahead log, which a checkpoint empties (e2e_keys seals texts in place and then truncates
        # it), and which is cut back to nothing whenever a checkpoint resets it, so old pages do not linger in its tail
        conn.execute("PRAGMA secure_delete = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA journal_size_limit = 0")
        conn.executescript(SCHEMA)
        conn.executescript(AGENTS_SCHEMA)
        conn.executescript(TASKS_SCHEMA)
        conn.executescript(CONTROLLER_SCHEMA)
        conn.executescript(SKILLS_SCHEMA)
        conn.executescript(SKILL_IMPROVE_SCHEMA)
        _migrate(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()
