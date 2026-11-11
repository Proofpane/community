"""Command line, run on the machine that holds the data directory:

    python -m proofpane_open invite EMAIL      print a one-time invitation link for EMAIL
    python -m proofpane_open public-key        print this deployment's public key as JSON
    python -m proofpane_open ade-status EMAIL  an account's ADE connections, each read once into a board summary
    python -m proofpane_open account-record list PERSON        the tasks' account record entries, and the offers'
                                                               and custody's rows, that name PERSON (an email or
                                                               their user id) as a teammate, one JSON line each
    python -m proofpane_open account-record reduce PERSON      each of them reduced at once to "a teammate"
    python -m proofpane_open account-record statement SEQ TEXT the person's statement attached to entry SEQ

The first admin has no account to invite anyone from, so their own invitation comes from here; on the fly image,
whose `fly ssh console` session may have no PATH, through its wrapper:

    fly ssh console -a <app> -C "/usr/local/bin/proofpane-open invite you@example.com"

The link is printed once; the token in it is stored only as its hash. The account record's three forms answer a
person's request under the Privacy Act's principles 6 and 7; each exits 0, or 1 with one line on standard error.
"""

import argparse
import json
import sys

from . import auth, config, tools


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m proofpane_open")
    sub = parser.add_subparsers(dest="cmd", required=True)
    inv = sub.add_parser("invite")
    inv.add_argument("email")
    sub.add_parser("public-key")
    st = sub.add_parser("ade-status")
    st.add_argument("email")
    rec = sub.add_parser("account-record")
    forms = rec.add_subparsers(dest="form", required=True)
    forms.add_parser("list").add_argument("person")
    forms.add_parser("reduce").add_argument("person")
    said = forms.add_parser("statement")
    said.add_argument("seq", type=int)
    said.add_argument("text")
    args = parser.parse_args(argv)
    if args.cmd == "ade-status":
        return ade_status(args.email)
    if args.cmd == "account-record":
        return account_record(args)
    if args.cmd == "invite":
        try:
            url = config.public_url()        # read before an invitation is made: without it, none is
            token = auth.create_invitation(args.email, "command line")
        except (RuntimeError, auth.AuthError) as e:
            print(e, file=sys.stderr)
            return 1
        print(f"{url}/#invite={token}")
        print(f"one use, expires in {config.INVITE_TTL_SECONDS // 3600} hours", file=sys.stderr)
        return 0
    print(json.dumps(tools.public_key(), indent=1))
    return 0


def account_record(args):
    """The operator's command on the account record: a person's entries listed,
    reduced, or given a statement; the offers' and custody's rows that name them listed and reduced with them, a row
    that still stands left as it is (task_offers.person_rows, reduce_person)."""
    import time

    from . import store
    from .ade import task_offers, tasks
    now = int(time.time())
    if args.form == "statement":
        try:
            with store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                entry = tasks.attach_statement(db, args.seq, args.text, now)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        print(json.dumps(entry, sort_keys=True))
        return 0
    try:
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # a person with no account here may still be named: by an entry, as the id of a teammate whose account was
            # removed since, or by an offer or a custody row, at the address an owner typed for them; so each is asked
            known = tasks.person_known(db, args.person)
            entries = tasks.person_entries(db, args.person)
            rows = task_offers.person_rows(db, args.person)
            named = known or bool(entries) or bool(rows)
            if args.form == "reduce" and named:
                # all of it or none: a failure in the offers' part keeps the record's reduction from being kept
                entries = tasks.reduce_person(db, args.person, now)
                rows = task_offers.reduce_person(db, args.person, now)
    except Exception as e:  # noqa: BLE001  nothing was changed; the operator is told what failed
        print(f"Nothing was changed: the person's entries, offers and custody rows could not all be read "
              f"({type(e).__name__}).", file=sys.stderr)
        return 1
    if not named:
        print("No such person: no account has that address or id, and no entry, offer or custody row names them.",
              file=sys.stderr)
        return 1
    if not known:
        print("No account has that address or id: what still names it, an entry by the id of an account removed since "
              "or an offer or custody row by the address an owner typed, is taken all the same.", file=sys.stderr)
    if args.form == "list":
        for entry in entries:
            print(json.dumps(entry, sort_keys=True))
        for row in rows:
            print(json.dumps(row, sort_keys=True))
    else:
        print(json.dumps({"entries": entries, "offers": rows.get("offers", 0), "custody": rows.get("custody", 0)},
                         sort_keys=True))
    return 0


def ade_status(email):
    """What an operator can see of an account's ADE without signing in as it: each connection (no token), and a
    board read once from it, summarised to counts and names. Nothing a file says is printed."""
    from datetime import datetime, timezone

    from . import signing, store
    from .ade import board, ledger

    try:
        email = auth.normalise_email(email)
    except auth.AuthError as e:
        print(e, file=sys.stderr)
        return 1
    with store.connect() as db:
        row = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if row is None:
        print("No account has that address.", file=sys.stderr)
        return 1
    conns = ledger.list_for(row["id"])
    print(f"{len(conns)} connection(s)")
    for c in conns:
        print(json.dumps({k: c[k] for k in ("id", "source", "repo", "branch", "dirs", "verify", "has_token")}))
        try:
            source = ledger.source_for(row["id"], c["id"])
            b = board.build_board(c, source, datetime.now(timezone.utc), None, signing.public_key())
        except Exception as e:  # an operator's check: say what failed and go on
            print(f"  board: failed, {type(e).__name__}: {str(e)[:200]}")
            continue
        conv = b["conversation"]
        print(f"  board: head {b['head'][:7]}; {len(b['channels'])} channel(s); cloud pane {len(conv['cloud']['items'])} "
              f"items, local pane {len(conv['local']['items'])} items; {len(b['alerts'])} alert(s)")
        print("  sessions: " + "; ".join(f"{s['kind']} {s['name']}" for s in b["sessions"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
