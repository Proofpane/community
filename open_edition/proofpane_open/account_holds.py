"""The account's cancel rule, as a pure function over sets of credentials. The rule reads no database and never calls
encryption's rule
(e2e_keys.cancellers): the account's rule is its own function, and a test compares the two where they govern the same
situation (tests/test_account_hold_rule.py). A thin wrapper at the end keeps a waiting change in account_pending, the
sets of its placement (Placed) and the change as the rule reads it in one exact form each, and reads them back for the
rule (tests/test_account_pending.py). Nothing in the app calls this module yet; the change that places a
held account change will keep its row through put_pending, and the cancel will read it through pending_cancellers.

The credentials are the password ("password"), each passkey (its id), and the recovery code ("recovery"). The admin,
who makes the admin's recovery, is "admin" and is no credential. In the standard tier the password, every passkey and
the code count; in the high tier the hardware keys and the code. Only a credential that counts yet counts: one an
ordinary addition added does not until a lock-out hold has passed since it was asked for, and the sets are
read as they stood when the change was placed, so a credential that comes to count while the change waits gains
nothing.

Who cancels a waiting change C:
(i)   the recovery code in force when C was placed, if it counted: always;
(ii)  if C is not code-backed, a counting credential C does not take away;
(iii) if C is not code-backed, and what C leaves (the counting credentials but the code, less what C takes away) holds
      nothing but C's maker, or nothing, a counting credential C takes away too. The code does not enter this test;
(iv)  nothing else: never a credential that does not count, never the sign-in alone.
A code-backed change (made by the code, or with the code's proof) is cancelled by the code alone.
"""

import dataclasses
import json
import typing

STANDARD, HIGH = "standard", "high"
TIERS = (STANDARD, HIGH)
PASSWORD, RECOVERY, ADMIN = "password", "recovery", "admin"

# the changes that wait in the security slot (deleting the account is not built; it is named here so
# that the rule can say who would cancel it)
ADD_PASSKEY = "add-passkey"
KEEP_ONLY = "keep-only"                     # removes passkeys, and may change or remove the password and the code
CHANGE_PASSWORD = "change-password"
REMOVE_PASSWORD = "remove-password"
SET_PASSWORD = "set-password"               # a first password
HIGH_ON = "high-security-on"
HIGH_OFF = "high-security-off"
FIRST_CODE = "first-code"
REPLACE_CODE = "replace-code"
REMOVE_CODE = "remove-code"
APPS_ON = "apps-on"
DELETE = "delete-account"
ADMIN_RECOVER = "admin-recover"
KINDS = (ADD_PASSKEY, KEEP_ONLY, CHANGE_PASSWORD, REMOVE_PASSWORD, SET_PASSWORD, HIGH_ON, HIGH_OFF, FIRST_CODE,
         REPLACE_CODE, REMOVE_CODE, APPS_ON, DELETE, ADMIN_RECOVER)


def _flag(value):
    """Whether `value` is True or False itself: 1 and 0 equal them in Python, so `in (True, False)` would take a
    number where a flag was meant."""
    return value is True or value is False


@dataclasses.dataclass(frozen=True)
class Passkey:
    """One of the account's passkeys as the change found it: its id, whether it is a hardware key, and whether it counts
    yet."""
    id: str
    hardware: bool
    counts: bool = True

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id or not _flag(self.hardware) or not _flag(self.counts):
            raise ValueError("A passkey is an id, whether it is a hardware key, and whether it counts yet.")


@dataclasses.dataclass(frozen=True)
class Placed:
    """The sets a change was placed with: the account's tier; whether it had a password and whether that counted yet
    (None: no password); its passkeys; and whether the recovery code in force counted yet (None: no code)."""
    tier: str
    password: typing.Optional[bool]
    passkeys: typing.Tuple[Passkey, ...]
    recovery: typing.Optional[bool]

    def __post_init__(self):
        # read from a stored body on the way to a cancel: a value that is not what it should be is refused, never
        # read as true or false by accident ("false" is true to Python)
        if self.tier not in TIERS:
            raise ValueError(f"No such tier: {self.tier!r}.")
        if not (self.password is None or _flag(self.password)) or not (self.recovery is None or _flag(self.recovery)):
            raise ValueError("The password and the code are None (none), True (counting yet) or False (not yet).")
        object.__setattr__(self, "passkeys", tuple(self.passkeys))
        if not all(isinstance(k, Passkey) for k in self.passkeys):
            raise ValueError("The passkeys are Passkey values.")
        ids = [k.id for k in self.passkeys]
        if len(set(ids)) != len(ids) or {PASSWORD, RECOVERY, ADMIN} & set(ids):
            raise ValueError("A passkey's id is the account's one passkey's, and none is password, recovery or admin.")


@dataclasses.dataclass(frozen=True)
class Change:
    """A held change as the rule reads it: its maker (a passkey's id, "password", "recovery" or "admin"), what it takes
    away (passkey ids, "password", "recovery"), and whether the recovery code's proof came with it."""
    maker: str
    takes_away: typing.FrozenSet[str]
    by_code: bool = False

    def __post_init__(self):
        if isinstance(self.takes_away, str):
            raise ValueError("What a change takes away is a set of credentials, not one name.")
        object.__setattr__(self, "takes_away", frozenset(self.takes_away))
        if not isinstance(self.maker, str) or not _flag(self.by_code) \
                or not all(isinstance(c, str) for c in self.takes_away):
            raise ValueError("A change is its maker, what it takes away, and whether the code's proof came with it.")


def counting(placed):
    """counting(T): the credentials that count in the change's tier, the code among them when it counts yet."""
    out = set()
    if placed.tier == STANDARD and placed.password:
        out.add(PASSWORD)
    for key in placed.passkeys:
        if key.counts and (placed.tier == STANDARD or key.hardware):
            out.add(key.id)
    if placed.recovery:
        out.add(RECOVERY)
    return frozenset(out)


def code_backed(change):
    """Whether the recovery code made the change or its proof came with it."""
    return change.maker == RECOVERY or change.by_code


def _has(placed):
    """Every credential the account had, counting yet or not."""
    return frozenset({k.id for k in placed.passkeys} | ({PASSWORD} if placed.password is not None else set())
                     | ({RECOVERY} if placed.recovery is not None else set()))


def _check(placed, change):
    # a maker always counts, the code backs a change only while it counts, and the admin makes only the admin's
    # recovery, which takes everything and never carries the code's proof: anything else is no change
    # that could have been placed, and a rule that guessed at it could hand the account to whoever built it
    if change.maker != ADMIN and change.maker not in counting(placed):
        raise ValueError(f"The maker {change.maker!r} did not count when the change was placed.")
    if change.by_code and RECOVERY not in counting(placed):
        raise ValueError("The recovery code's proof came with the change, but no code counted when it was placed.")
    if not change.takes_away <= _has(placed):
        raise ValueError("The change takes away a credential the account did not have.")
    if change.maker == ADMIN and (change.by_code or change.takes_away != _has(placed)):
        raise ValueError("The admin makes only the admin's recovery: it takes everything, and never the code's proof.")


def cancellers(placed, change):
    """Who may cancel `change`, placed with `placed`: a frozenset of passkey ids, "password" and "recovery", by
    the rule's (i) to (iv). ValueError for a change that could not have been placed so (_check)."""
    _check(placed, change)
    every = counting(placed)
    root = every & {RECOVERY}
    if root and code_backed(change):
        return root                                     # code-backed: the code alone
    others = every - root
    takes = change.takes_away & others                  # only what counted can cancel through (iii)
    left = others - takes                               # left(C): the code does not enter (iii)
    may = left | root                                   # (ii) and (i)
    if left <= {change.maker}:
        may |= takes                                    # (iii)
    return frozenset(may)


def takes(placed, kind, removes=frozenset()):
    """takes(C) for a change of `kind` on the account as placed: a keep-only takes what it `removes`
    (passkey ids, "password" for a password it changes or removes, "recovery" for a code it replaces or removes);
    turning high security on takes the password and every device passkey; deleting the account and the admin's
    recovery take everything; an addition and turning high security off take nothing."""
    removes = frozenset(removes)
    if kind != KEEP_ONLY and removes:
        raise ValueError("Only a keep-only names what it removes.")
    have = _has(placed)
    if kind == KEEP_ONLY:
        if not removes <= have:
            raise ValueError("A keep-only removes only what the account has.")
        return removes
    if kind in (CHANGE_PASSWORD, REMOVE_PASSWORD):
        return frozenset({PASSWORD})
    if kind in (REPLACE_CODE, REMOVE_CODE):
        return frozenset({RECOVERY})
    if kind == HIGH_ON:
        return frozenset(({PASSWORD} if placed.password is not None else set())
                         | {k.id for k in placed.passkeys if not k.hardware})
    if kind in (DELETE, ADMIN_RECOVER):
        return have
    if kind in (ADD_PASSKEY, SET_PASSWORD, FIRST_CODE, APPS_ON, HIGH_OFF):
        return frozenset()
    raise ValueError(f"No such change: {kind!r}.")


def may_make(placed, kind, maker, removes=frozenset(), encryption_on=False):
    """Whether `maker` may make a change of `kind`: the maker counts, the change is one the account can
    have, and the maker is one the table names for the kind and the tier. The password never makes a change that takes a
    passkey away. Turning high security on needs a recovery code: one the account has, whether or not it counts
    yet. The checks made beside this (the last hardware key, a way to sign in
    left, a keep-only's code replaced and never removed while encryption or high security is on) are placement's, not
    the rule's: a keep-only's "recovery" here is the code it replaces or removes."""
    try:
        taken = takes(placed, kind, removes)
    except ValueError:
        return False
    if kind == ADMIN_RECOVER:
        return maker == ADMIN
    if maker == ADMIN or maker not in counting(placed):
        return False
    keys = {k.id: k for k in placed.passkeys}
    if maker == PASSWORD and taken & set(keys):
        return False
    hardware = maker in keys and keys[maker].hardware
    passkey = maker in keys
    high = placed.tier == HIGH
    has_password, has_code = placed.password is not None, placed.recovery is not None
    if kind == HIGH_ON:
        return not high and hardware and has_code
    if kind == HIGH_OFF:
        return high and hardware
    if kind == SET_PASSWORD and has_password or kind in (CHANGE_PASSWORD, REMOVE_PASSWORD) and not has_password:
        return False
    if kind == FIRST_CODE and has_code or kind in (REPLACE_CODE, REMOVE_CODE) and not has_code:
        return False
    if kind == REMOVE_CODE and (high or encryption_on):
        return False
    # in the high tier only a hardware key or the code counts, so counting() has already kept out the rest
    allowed = {
        ADD_PASSKEY: (passkey, maker == RECOVERY, maker == PASSWORD and not encryption_on),
        KEEP_ONLY: (passkey, maker == RECOVERY and not encryption_on, maker == PASSWORD),
        CHANGE_PASSWORD: (passkey, maker == RECOVERY, maker == PASSWORD),
        REMOVE_PASSWORD: (passkey, maker == RECOVERY, maker == PASSWORD),
        SET_PASSWORD: (passkey, maker == RECOVERY),
        FIRST_CODE: (passkey, maker == PASSWORD),
        REPLACE_CODE: (passkey, maker == RECOVERY, maker == PASSWORD),
        REMOVE_CODE: (passkey, maker == RECOVERY, maker == PASSWORD),
        APPS_ON: (passkey, maker == PASSWORD),
        DELETE: (passkey, maker == PASSWORD),        # only with no passkey: deleting takes them all, and the password
                                                     # never takes a passkey away
    }[kind]
    return any(allowed)


def may_take_place(placed, waiting, given, by_admin=False):
    """Whether a request may place its change in the place of `waiting`, a change placed with `placed`: only when
    something it carries could cancel `waiting` (`given`: its maker, a confirmation given with it, "recovery" for the
    code's proof). A code-backed change is so replaced only with the code's proof, since the code alone cancels it. The
    admin's recovery takes the place of anything, and nothing takes its place."""
    if waiting.maker == ADMIN:
        return False
    if by_admin:
        return True
    return bool(set(given) & cancellers(placed, waiting))


# ---- the wrapper: a waiting change as account_pending keeps it ------------------------------------------------------
#
# The sets a change was placed with and the change as the rule reads it are kept in one exact form each, and read back
# in that form alone: a value read back wrong refuses the cancel (ValueError) instead of being read as another ("false"
# is true to Python, and 1 is True where a flag was meant).


def _json(text, what):
    """`text` as JSON (a JSONDecodeError is a ValueError). Each reader then holds what it read to the one form its
    writer writes, so a key given twice, NaN or a space anywhere is refused there."""
    if not isinstance(text, str):
        raise ValueError(f"{what} is kept as text.")
    return json.loads(text)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def placed_text(placed):
    """The form account_pending.placed keeps `placed` in: compact JSON in ASCII, its keys sorted, its passkeys in their
    order."""
    return _canonical({"tier": placed.tier, "password": placed.password, "recovery": placed.recovery,
                       "passkeys": [{"id": k.id, "hardware": k.hardware, "counts": k.counts} for k in placed.passkeys]})


def read_placed(text):
    """The Placed that `text` keeps, read back strictly: exactly the form placed_text writes, its flags true or false
    (the password and the code null as well), never a number or a string. ValueError otherwise."""
    what = "The sets the change was placed with"
    data = _json(text, what)
    if not isinstance(data, dict) or set(data) != {"tier", "password", "recovery", "passkeys"}:
        raise ValueError(f"{what} are its tier, password, recovery code and passkeys, and nothing else.")
    if not isinstance(data["passkeys"], list) or not all(
            isinstance(k, dict) and set(k) == {"id", "hardware", "counts"} for k in data["passkeys"]):
        raise ValueError(f"{what} name each passkey by its id, whether it is a hardware key and whether it counts yet.")
    placed = Placed(data["tier"], data["password"],
                    tuple(Passkey(k["id"], k["hardware"], k["counts"]) for k in data["passkeys"]), data["recovery"])
    if placed_text(placed) != text:
        raise ValueError(f"{what} are not in the form they are written in.")
    return placed


def takes_away_text(taken):
    """The form account_pending.takes_away keeps what a change takes away in: a compact JSON list, sorted."""
    return _canonical(sorted(taken))


def read_takes_away(text):
    """What a change takes away, read back strictly from the form takes_away_text writes: a sorted list of names, each
    once. ValueError otherwise."""
    what = "What the change takes away"
    data = _json(text, what)
    if not isinstance(data, list) or not all(isinstance(c, str) for c in data):
        raise ValueError(f"{what} is a list of credentials.")
    if len(set(data)) != len(data) or takes_away_text(data) != text:
        raise ValueError(f"{what} is not in the form it is written in.")
    return frozenset(data)


def _read_by_code(value):
    # sqlite3 gives an INTEGER column back as int
    if type(value) is not int or value not in (0, 1):
        raise ValueError("Whether the code's proof came with the change is kept as 0 or 1.")
    return value == 1


@dataclasses.dataclass(frozen=True)
class Pending:
    """An account change that waits, as account_pending keeps it: its hold, its kind, the sets it was placed with, and
    the change as the rule reads it."""
    hold_id: str
    kind: str
    placed: Placed
    change: Change

    def cancellers(self):
        return cancellers(self.placed, self.change)

    def may_take_place(self, given, by_admin=False):
        return may_take_place(self.placed, self.change, given, by_admin)


def _consistent(kind, placed, change):
    """ValueError unless `change` could be a change of `kind` placed with `placed`: a kind the rule knows, taking away
    what such a change takes (takes, which refuses a kind it does not know), with a maker and a proof of the code it
    could have had (cancellers' checks)."""
    if takes(placed, kind, change.takes_away if kind == KEEP_ONLY else ()) != change.takes_away:
        raise ValueError(f"The change does not take away what a change of kind {kind} takes away.")
    cancellers(placed, change)


def put_pending(db, user_id, hold_id, kind, body, placed, change, net, now):
    """Keep a change of `kind`, waiting under `hold_id` and placed with `placed`, in account_pending, inside the placing
    transaction: `body` (a dict) is what it adds and keeps to undo, which only the change itself reads; `net`, the
    network it was asked from. ValueError, before anything is written, for a change that could not have been placed
    so; sqlite3.IntegrityError where one waits already, since the account keeps one."""
    _consistent(kind, placed, change)
    db.execute("INSERT INTO account_pending (user_id, hold_id, kind, body, placed, maker, by_code, takes_away, net, "
               "created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (user_id, hold_id, kind, _canonical(body), placed_text(placed), change.maker, int(change.by_code),
                takes_away_text(change.takes_away), net, int(now)))


def read_pending(row):
    """The Pending a row of account_pending keeps, read back strictly (read_placed, read_takes_away, by_code 0 or 1, a
    change consistent with its kind). ValueError otherwise: a row read back wrong refuses the cancel, never guesses."""
    placed = read_placed(row["placed"])
    change = Change(row["maker"], read_takes_away(row["takes_away"]), _read_by_code(row["by_code"]))
    _consistent(row["kind"], placed, change)
    return Pending(row["hold_id"], row["kind"], placed, change)


def pending(db, user_id):
    """The account change that waits for `user_id`, as read_pending reads it, or None when none waits."""
    row = db.execute("SELECT hold_id, kind, placed, maker, by_code, takes_away FROM account_pending WHERE user_id = ?",
                     (user_id,)).fetchone()
    return None if row is None else read_pending(row)


def pending_cancellers(db, user_id, hold_id):
    """Who may cancel the account change waiting under `hold_id` (cancellers, over the sets it was placed with): a
    frozenset, or None when no change of this account waits under that hold. ValueError for a row read back wrong."""
    row = db.execute("SELECT hold_id, kind, placed, maker, by_code, takes_away FROM account_pending "
                     "WHERE user_id = ? AND hold_id = ?", (user_id, hold_id)).fetchone()
    return None if row is None else read_pending(row).cancellers()
