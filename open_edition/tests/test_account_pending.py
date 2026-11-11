"""The wrapper (proofpane_open/account_holds.py, its last part): a waiting account change kept in account_pending
and read back for the cancel rule.

1. Round trip: over every account of a small universe and every change that could be placed on it, the change is kept
   and read back equal, and the wrapper's cancellers and (v) are the pure rule's.
2. Strictness: every stored value read back in any form but the one it is written in refuses the cancel with a
   ValueError, never a guess, whichever column is wrong.
3. Writing: a change that could not have been placed so is refused before anything is written; the account keeps one.
"""

import itertools
import json
import sqlite3

import pytest

from proofpane_open import account_holds as ah
from proofpane_open import store

STD, HIGH, P, R = ah.STANDARD, ah.HIGH, ah.PASSWORD, ah.RECOVERY
USER, HOLD = "u1", "h1"


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    with store.connect(str(tmp_path_factory.mktemp("pending") / "app.db")) as connection:
        yield connection


@pytest.fixture()
def db(conn):
    conn.execute("DELETE FROM account_pending")
    return conn


def subsets(names):
    names = sorted(names)
    return [frozenset(c) for n in range(len(names) + 1) for c in itertools.combinations(names, n)]


def universe():
    """Every account of the tiers, a password and a code each absent, counting yet or not, and a device and a
    hardware passkey each absent, counting yet or not."""
    keys = []
    for d in (None, True, False):
        for h in (None, True, False):
            keys.append(tuple(ah.Passkey(n, hw, counts) for n, hw, counts in (("D", False, d), ("H", True, h))
                              if counts is not None))
    for tier, password, recovery, passkeys in itertools.product((STD, HIGH), (None, True, False), (None, True, False),
                                                                keys):
        yield ah.Placed(tier, password, passkeys, recovery)


def changes(placed):
    """Every (kind, change) on `placed`: each kind, a keep-only for each set of what the account has, each maker and
    the admin, with and without the code's proof; impossible ones among them (the rule refuses those)."""
    have = ah._has(placed)
    for kind in ah.KINDS:
        for removes in subsets(have) if kind == ah.KEEP_ONLY else (frozenset(),):
            taken = ah.takes(placed, kind, removes)
            for maker in sorted(have) + [ah.ADMIN]:
                for by_code in (False, True):
                    yield kind, ah.Change(maker, taken, by_code)


def possible(placed, change):
    try:
        ah.cancellers(placed, change)
    except ValueError:
        return False
    return True


def test_a_kept_change_reads_back_as_it_was_and_the_rule_gives_what_it_gives(db):
    kept = refused = 0
    for placed in universe():
        for kind, change in changes(placed):
            db.execute("DELETE FROM account_pending")
            if not possible(placed, change):
                with pytest.raises(ValueError):
                    ah.put_pending(db, USER, HOLD, kind, {}, placed, change, "net", 1)
                assert db.execute("SELECT COUNT(*) FROM account_pending").fetchone()[0] == 0
                refused += 1
                continue
            ah.put_pending(db, USER, HOLD, kind, {"adds": "x"}, placed, change, "net", 1)
            got = ah.pending(db, USER)
            assert got == ah.Pending(HOLD, kind, placed, change)
            assert ah.pending_cancellers(db, USER, HOLD) == ah.cancellers(placed, change)
            for given in subsets(ah._has(placed))[:8]:
                assert got.may_take_place(given) == ah.may_take_place(placed, change, given)
            assert got.may_take_place(set(), by_admin=True) == ah.may_take_place(placed, change, set(), by_admin=True)
            kept += 1
    assert kept > 2_000 and refused > 2_000


def test_the_forms_written():
    # the passkeys in their order (not sorted), every key sorted, in ASCII
    accented = "D" + chr(0xE9)
    placed = ah.Placed(STD, True, (ah.Passkey("H1", True, counts=False), ah.Passkey(accented, False)), None)
    text = ah.placed_text(placed)
    assert text == ('{"passkeys":[{"counts":false,"hardware":true,"id":"H1"},{"counts":true,"hardware":false,'
                    '"id":"D\\u00e9"}],"password":true,"recovery":null,"tier":"standard"}')
    assert ah.read_placed(text) == placed
    assert ah.takes_away_text({P, "D1", R}) == '["D1","password","recovery"]'
    assert ah.takes_away_text(set()) == "[]"


def test_a_value_of_another_type_is_refused_as_such():
    """A row handed over by another path than sqlite3 (a flag where 0 or 1 is kept, bytes where text is): ValueError,
    not another exception the cancel would not expect."""
    row = {"hold_id": HOLD, "kind": ah.CHANGE_PASSWORD, "placed": ah.placed_text(BASE), "maker": "D",
           "takes_away": '["password"]', "by_code": 0}
    assert ah.read_pending(row) == ah.Pending(HOLD, ah.CHANGE_PASSWORD, BASE, CHANGE)
    for by_code in (True, False, 1.0, "1", None):
        with pytest.raises(ValueError):
            ah.read_pending(dict(row, by_code=by_code))
    for over in ({"placed": None}, {"placed": ah.placed_text(BASE).encode()}, {"takes_away": b'["password"]'},
                 {"takes_away": None}):
        with pytest.raises(ValueError):
            ah.read_pending(dict(row, **over))


def test_the_row_as_written(db):
    placed = ah.Placed(HIGH, None, (ah.Passkey("H", True),), True)
    change = ah.Change(R, frozenset({"H"}), True)
    ah.put_pending(db, USER, HOLD, ah.KEEP_ONLY, {"remove": ["H"], "b": 1}, placed, change, "198.51.100.0/24", 1234)
    row = db.execute("SELECT * FROM account_pending").fetchone()
    assert dict(row) == {"user_id": USER, "hold_id": HOLD, "kind": ah.KEEP_ONLY, "body": '{"b":1,"remove":["H"]}',
                         "placed": ah.placed_text(placed), "maker": R, "by_code": 1, "takes_away": '["H"]',
                         "net": "198.51.100.0/24", "created_at": 1234}


def test_the_table_has_section_10s_columns(db):
    names = [r["name"] for r in db.execute("PRAGMA table_info(account_pending)")]
    assert names == ["user_id", "hold_id", "kind", "body", "placed", "maker", "by_code", "takes_away", "net",
                     "created_at"]


# ---- strictness: each stored value in any other form ------------------------------------------------------------------

BASE = ah.Placed(STD, True, (ah.Passkey("D", False), ah.Passkey("H", True)), True)
CHANGE = ah.Change("D", frozenset({P}))                  # a password change made with D: cancelled by D, H and the code


def placed_with(**over):
    data = json.loads(ah.placed_text(BASE))
    data.update(over)
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def key_with(**over):
    data = json.loads(ah.placed_text(BASE))
    data["passkeys"][0].update(over)
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def key_without(name):
    data = json.loads(ah.placed_text(BASE))
    del data["passkeys"][0][name]
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


GOOD_PLACED = ah.placed_text(BASE)

BAD = [
    ("placed", "not json"),
    ("placed", "[]"),
    ("placed", '{"tier":"standard"}'),
    ("placed", GOOD_PLACED[:-1] + ',"tier":"standard"}'),                             # a key given twice
    ("placed", placed_with(extra=1)),
    ("placed", placed_with(password=1)),
    ("placed", placed_with(password="true")),
    ("placed", placed_with(recovery=0)),
    ("placed", placed_with(tier="STANDARD")),
    ("placed", placed_with(tier=None)),
    ("placed", placed_with(passkeys={})),
    ("placed", placed_with(passkeys=["D"])),
    ("placed", key_with(extra=True)),
    ("placed", key_without("counts")),
    ("placed", key_with(hardware=0)),
    ("placed", key_with(counts="false")),
    ("placed", key_with(id=7)),
    ("placed", key_with(id="")),
    ("placed", key_with(id="H")),                                                     # two passkeys of one id
    ("placed", key_with(id=P)),
    ("placed", GOOD_PLACED.replace('"password":true', '"password":NaN')),
    ("placed", GOOD_PLACED.replace(",", ", ")),                                        # not the form written
    ("placed", "{ " + GOOD_PLACED[1:]),
    ("placed", '{"tier":"standard","passkeys":[{"counts":true,"hardware":false,"id":"D"},{"counts":true,"hardware":true,'
               '"id":"H"}],"password":true,"recovery":true}'),                         # keys out of order
    ("takes_away", "["),
    ("takes_away", '"password"'),
    ("takes_away", "[1]"),
    ("takes_away", '["D",1]'),                                                         # a name and a number
    ("takes_away", '["password","password"]'),
    ("takes_away", '["password","D"]'),                                                # out of order
    ("takes_away", '[ "password"]'),
    ("takes_away", '["X"]'),                                                           # a credential it never had
    ("takes_away", "[]"),                                                              # not what a password change takes
    ("by_code", 2),
    ("by_code", -1),
    ("by_code", "yes"),                                                                # stays text: no integer in it
    ("by_code", 1.5),                                                                  # stays a real number
    ("maker", "X"),                                                                    # did not count
    ("maker", ah.ADMIN),                                                               # the admin's is a recovery
    ("maker", 5),                                                                      # kept as "5": did not count
    ("kind", "nope"),
    ("kind", ah.REMOVE_CODE),                                                          # takes the code, not the password
]


@pytest.mark.parametrize("column, value", BAD, ids=[f"{c}-{i}" for i, (c, _) in enumerate(BAD)])
def test_a_value_read_back_in_another_form_refuses(db, column, value):
    ah.put_pending(db, USER, HOLD, ah.CHANGE_PASSWORD, {}, BASE, CHANGE, None, 1)
    assert ah.pending_cancellers(db, USER, HOLD) == {"D", "H", R}
    db.execute(f"UPDATE account_pending SET {column} = ?", (value,))
    with pytest.raises(ValueError):
        ah.pending(db, USER)
    with pytest.raises(ValueError):
        ah.pending_cancellers(db, USER, HOLD)


def test_the_flags_are_true_or_false_themselves():
    """1 and 0 equal True and False in Python: a number is no flag, so a body read back with one is refused."""
    for build in (lambda: ah.Passkey("D", 1), lambda: ah.Passkey("D", True, 0), lambda: ah.Placed(STD, 1, (), None),
                  lambda: ah.Placed(STD, None, (), 0), lambda: ah.Change("D", frozenset(), 1),
                  lambda: ah.Passkey("D", True, None), lambda: ah.Placed(STD, "", (), None)):
        with pytest.raises(ValueError):
            build()
    assert ah.Placed(STD, None, (ah.Passkey("D", False, False),), False).recovery is False


# ---- writing ------------------------------------------------------------------------------------------------------------

def test_nothing_is_written_for_a_change_that_could_not_have_been_placed(db):
    for kind, change in ((ah.CHANGE_PASSWORD, ah.Change("X", frozenset({P}))),             # a maker that did not count
                         (ah.CHANGE_PASSWORD, ah.Change("D", frozenset())),                # not what it takes
                         (ah.HIGH_ON, ah.Change("H", frozenset({P}))),                    # D taken too
                         ("nope", ah.Change("D", frozenset())),
                         (ah.ADD_PASSKEY, ah.Change("D", frozenset(), True))):            # no code counted
        placed = BASE if kind != ah.ADD_PASSKEY else ah.Placed(STD, True, BASE.passkeys, None)
        with pytest.raises(ValueError):
            ah.put_pending(db, USER, HOLD, kind, {}, placed, change, None, 1)
    with pytest.raises(ValueError):                                                       # a body JSON cannot hold
        ah.put_pending(db, USER, HOLD, ah.CHANGE_PASSWORD, {"x": float("nan")}, BASE, CHANGE, None, 1)
    assert db.execute("SELECT COUNT(*) FROM account_pending").fetchone()[0] == 0


def test_the_account_keeps_one_waiting_change(db):
    ah.put_pending(db, USER, HOLD, ah.CHANGE_PASSWORD, {}, BASE, CHANGE, None, 1)
    with pytest.raises(sqlite3.IntegrityError):
        ah.put_pending(db, USER, "h2", ah.ADD_PASSKEY, {}, BASE, ah.Change("H", frozenset()), None, 2)
    assert ah.pending(db, USER) == ah.Pending(HOLD, ah.CHANGE_PASSWORD, BASE, CHANGE)
    ah.put_pending(db, "u2", "h2", ah.ADD_PASSKEY, {}, BASE, ah.Change("H", frozenset()), None, 2)
    assert ah.pending(db, "u2").hold_id == "h2"


def test_none_waits_under_another_hold_or_for_another_account(db):
    assert ah.pending(db, USER) is None and ah.pending_cancellers(db, USER, HOLD) is None
    ah.put_pending(db, USER, HOLD, ah.CHANGE_PASSWORD, {}, BASE, CHANGE, None, 1)
    assert ah.pending_cancellers(db, USER, "h2") is None
    assert ah.pending_cancellers(db, "u2", HOLD) is None
    assert ah.pending(db, "u2") is None
    # a row read back wrong refuses only for the hold it names: another hold is simply not waiting
    db.execute("UPDATE account_pending SET placed = 'x'")
    assert ah.pending_cancellers(db, USER, "h2") is None
