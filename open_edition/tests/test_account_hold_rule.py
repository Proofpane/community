"""The account's cancel rule (proofpane_open/account_holds.py) against a reference model written from the design's words
and its scenario table, never
from the implementation: nothing in the model calls account_holds or e2e_keys to work out what it expects.

1. The model is the table: for every row of the scenario table, the model's outcome is the row's.
2. The code is the model: over a small universe, account_holds.cancellers, takes and may_make give what the model's do.
3. The rule's properties, checked on the model over a universe of accounts with holders.
4. The agreement test: where the account's rule and encryption's rule v2 (e2e_keys.cancellers) govern the same
   situation they give the same cancellers; outside it, the differences, exactly.

Where this departs from the design, and why. The properties run over P, D1, D2, H1 and R with holders (118,098
accounts, about twenty seconds), not over the design's seven credentials, which with holders are 9.6 million accounts;
the code-against-model check uses the design's seven. The qualifier "while no passkey or password is held by nobody"
(lost()), which the design first gave the second property alone, was added to the third, sixth and seventh and to
the second half of the fifth, after this model found each failing without it; the tests assert each with
it, and that it fails without it.

What the model leaves out: each side makes one move, and placement's own checks (the last
hardware key while high security is on, a way to sign in left) are not modelled. The table's rows name one move too,
except row 19's path without the code and the encryption-on paths of rows 3, 12 and 23, which take two; the model gives
those rows the table's outcome. Encryption is off throughout.
"""

import itertools
import json

import pytest

from proofpane_open import account_holds as ah

# ---- the reference model --------------------------------------------------------------------------------------------
#
# An account, for the model: its tier, and for each credential its name, its kind (password, device, hardware, code),
# who holds it (owner, thief, both, nobody) and whether it counts yet. The names are the rule's: "password", "recovery",
# and a passkey's own name.

PW, CODE, DEV, HW = "password", "code", "device", "hardware"
OWNER, THIEF, BOTH, NOBODY = "owner", "thief", "both", "nobody"
STD, HIGH = "standard", "high"
P, R = "password", "recovery"


class Cred:
    def __init__(self, kind, holder=NOBODY, counts=True):
        self.kind, self.holder, self.counts = kind, holder, counts


def ref_counts(tier, cred):
    """A credential counts: it counts yet, and it is the code, or, in the standard tier, the password or any passkey, or,
    in the high tier, a hardware key."""
    if not cred.counts:
        return False
    if cred.kind == CODE:
        return True
    if tier == STD:
        return True
    return cred.kind == HW


def ref_cancellers(tier, creds, maker, takes, by_code):
    """The cancel rule in the design's own words. `creds`: name to Cred (holders unused)."""
    counting = [n for n, c in creds.items() if ref_counts(tier, c)]
    code_counts = R in counting
    backed = maker == R or by_code
    if code_counts and backed:
        return {R}                                  # a code-backed change: the code alone
    result = set()
    if code_counts:
        result.add(R)                               # (i)
    leaves = [n for n in counting if n != R and n not in takes]
    result.update(leaves)                           # (ii)
    if all(n == maker for n in leaves):             # (iii): nothing left but the maker, or nothing at all
        result.update(n for n in counting if n != R and n in takes)
    return result


def ref_takes(creds, kind, removes=()):
    """takes(C), from the words."""
    if kind == ah.KEEP_ONLY:
        return set(removes)
    if kind in (ah.CHANGE_PASSWORD, ah.REMOVE_PASSWORD):
        return {P}
    if kind in (ah.REPLACE_CODE, ah.REMOVE_CODE):
        return {R}
    if kind == ah.HIGH_ON:
        return {n for n, c in creds.items() if c.kind in (PW, DEV)}
    if kind in (ah.DELETE, ah.ADMIN_RECOVER):
        return set(creds)
    return set()


# One row per change: who may make it in the standard tier and in the high tier. "password!" is the
# password only while encryption is off; "code!" the code only while encryption is off; "password0" the password only
# for a keep-only that removes no passkey; "password-" the password only when the account has no passkey.
MAKERS = {
    ah.ADD_PASSKEY: ({"password!", "passkey", "code"}, {"hardware", "code"}),
    ah.KEEP_ONLY: ({"passkey", "code!", "password0"}, {"hardware", "code!"}),
    ah.CHANGE_PASSWORD: ({"password", "passkey", "code"}, {"hardware", "code"}),
    ah.REMOVE_PASSWORD: ({"password", "passkey", "code"}, {"hardware", "code"}),
    ah.SET_PASSWORD: ({"passkey", "code"}, {"hardware", "code"}),
    ah.HIGH_ON: ({"hardware"}, set()),
    ah.HIGH_OFF: (set(), {"hardware"}),
    ah.FIRST_CODE: ({"password", "passkey"}, {"hardware"}),
    ah.REPLACE_CODE: ({"password", "passkey", "code"}, {"hardware", "code"}),
    ah.REMOVE_CODE: ({"password", "passkey", "code"}, set()),
    ah.APPS_ON: ({"password", "passkey"}, {"hardware"}),
    ah.DELETE: ({"passkey", "password-"}, {"hardware"}),
}


def ref_may(tier, creds, kind, maker, removes=(), enc=False):
    """Who may make a change, from MAKERS and its notes."""
    if kind != ah.KEEP_ONLY and removes:
        return False                                # only a keep-only names what it removes
    if any(n not in creds for n in removes):
        return False
    if kind == ah.ADMIN_RECOVER:
        return maker == ah.ADMIN
    if maker not in creds or not ref_counts(tier, creds[maker]):
        return False                                # a provisional credential makes nothing; nor the admin
    kinds = {n: c.kind for n, c in creds.items()}
    if kind in (ah.CHANGE_PASSWORD, ah.REMOVE_PASSWORD) and P not in creds:
        return False
    if kind == ah.SET_PASSWORD and P in creds:
        return False
    if kind == ah.FIRST_CODE and R in creds:
        return False
    if kind in (ah.REPLACE_CODE, ah.REMOVE_CODE) and R not in creds:
        return False
    if kind == ah.HIGH_ON and R not in creds:
        return False                                # turning high security on needs a recovery code
    if kind == ah.REMOVE_CODE and (enc or tier == HIGH):
        return False
    took = ref_takes(creds, kind, removes)
    if kinds[maker] == PW and any(kinds[n] in (DEV, HW) for n in took):
        return False                                # the password never takes a passkey away
    names = MAKERS[kind][0 if tier == STD else 1]
    what = kinds[maker]
    ok = set()
    if what in (DEV, HW):
        ok.add("passkey")
    if what == HW:
        ok.add("hardware")
    if what == CODE:
        ok.add("code")
        if not enc:
            ok.add("code!")
    if what == PW:
        ok.add("password")
        if not enc:
            ok.add("password!")
        if not any(kinds[n] in (DEV, HW) for n in removes):
            ok.add("password0")
        if not any(k in (DEV, HW) for k in kinds.values()):
            ok.add("password-")
    return bool(names & ok)


# The changes that wait in the security slot, named here from the design, not read from the module
# (test_takes_and_may_make_are_the_models checks that the module has exactly these)
MODEL_KINDS = ("add-passkey", "keep-only", "change-password", "remove-password", "set-password", "high-security-on",
               "high-security-off", "first-code", "replace-code", "remove-code", "apps-on", "delete-account",
               "admin-recover")
SIDES = (OWNER, THIEF)
MOVES = [k for k in MODEL_KINDS if k not in (ah.DELETE, ah.ADMIN_RECOVER)]


def held(creds, side):
    return {n for n, c in creds.items() if c.holder in (side, BOTH)}


def ref_wins(tier, creds, side, enc=False):
    """Whether `side` has a move that leaves it a credential that counts and the other side none, which nothing the
    other side holds may cancel. A change adds its maker's side the password it sets or changes and the code it makes or
    replaces, which count once the change is final, and a passkey it adds, which counts at once only when the code made
    or backed the change. The other side's credentials that do not count yet are undone by any credential that counts."""
    other = THIEF if side == OWNER else OWNER
    mine, theirs = held(creds, side), held(creds, other)
    backing = [False, True] if R in mine and ref_counts(tier, creds[R]) else [False]
    for kind in MOVES:
        for maker in sorted(mine):
            if kind == ah.KEEP_ONLY:
                options = [set(s) for r in range(len(creds) + 1) for s in itertools.combinations(sorted(creds), r)]
            else:
                options = [set()]
            for removes in options:
                if not ref_may(tier, creds, kind, maker, removes, enc):
                    continue
                took = ref_takes(creds, kind, removes)
                after = HIGH if kind == ah.HIGH_ON else STD if kind == ah.HIGH_OFF else tier
                for by_code in backing:
                    gained = set()
                    if kind in (ah.CHANGE_PASSWORD, ah.SET_PASSWORD) or kind == ah.KEEP_ONLY and P in removes:
                        gained.add(PW)
                    if kind in (ah.REPLACE_CODE, ah.FIRST_CODE) or kind == ah.KEEP_ONLY and R in removes:
                        gained.add(CODE)
                    if kind == ah.ADD_PASSKEY and (by_code or maker == R):
                        gained.add(DEV)
                    keep = {n for n in mine if n not in took and ref_counts(after, creds[n])}
                    keep_new = {g for g in gained if ref_counts(after, Cred(g))}
                    if not keep and not keep_new:
                        continue
                    if any(n not in took and ref_counts(after, creds[n]) for n in theirs):
                        continue
                    if theirs & ref_cancellers(tier, creds, maker, took, by_code):
                        continue
                    return True
    return False


def ref_outcome(tier, creds, enc=False):
    """owner, thief, race or stalemate; "admin" for a stalemate in which the owner holds nothing that counts."""
    o, t = ref_wins(tier, creds, OWNER, enc), ref_wins(tier, creds, THIEF, enc)
    if o and t:
        return "race"
    if o:
        return OWNER
    if t:
        return THIEF
    if not any(ref_counts(tier, creds[n]) for n in held(creds, OWNER)):
        return "admin"
    return "stalemate"


# ---- 1. the model is the table --------------------------------------------------------------------------------------

def C(kind, holder, counts=True):
    return Cred(kind, holder, counts)


# The table, a row and each variant it names: (row, tier, credentials, outcome). Encryption is off throughout: the
# rows' encryption-on paths take two of encryption's changes, which encryption's rule decides.
ROWS = [
    (1, STD, {"Ds": C(DEV, BOTH), P: C(PW, OWNER)}, OWNER),
    ("1 with the owner's code", STD, {"Ds": C(DEV, BOTH), P: C(PW, OWNER), R: C(CODE, OWNER)}, OWNER),
    (2, STD, {"Ds": C(DEV, BOTH), P: C(PW, BOTH)}, "stalemate"),
    ("2 with a code the owner alone holds", STD, {"Ds": C(DEV, BOTH), P: C(PW, BOTH), R: C(CODE, OWNER)}, OWNER),
    (3, STD, {"Ds": C(DEV, THIEF), P: C(PW, OWNER)}, "stalemate"),
    ("3 with a code the owner alone holds", STD, {"Ds": C(DEV, THIEF), P: C(PW, OWNER), R: C(CODE, OWNER)}, OWNER),
    ("3 with a lost passkey still listed", STD, {"Ds": C(DEV, THIEF), P: C(PW, OWNER), "Dl": C(DEV, NOBODY)}, THIEF),
    (4, STD, {"Dp": C(DEV, THIEF), "D2": C(DEV, OWNER), P: C(PW, OWNER)}, OWNER),
    (5, STD, {P: C(PW, BOTH), "D": C(DEV, OWNER)}, OWNER),
    (6, STD, {P: C(PW, BOTH)}, "stalemate"),
    ("6 with a code the owner alone holds", STD, {P: C(PW, BOTH), R: C(CODE, OWNER)}, OWNER),
    (7, HIGH, {P: C(PW, BOTH), "H1": C(HW, OWNER), R: C(CODE, OWNER)}, OWNER),
    (8, STD, {"H": C(HW, THIEF), "Ds": C(DEV, OWNER), P: C(PW, OWNER)}, OWNER),
    ("8 with the owner's code", STD, {"H": C(HW, THIEF), "Ds": C(DEV, OWNER), P: C(PW, OWNER), R: C(CODE, OWNER)},
     OWNER),
    (9, STD, {"H": C(HW, THIEF), "Ds": C(DEV, OWNER)}, "stalemate"),
    (10, HIGH, {"H1": C(HW, THIEF), "H2": C(HW, OWNER), R: C(CODE, OWNER)}, OWNER),
    ("10 with the code lost", HIGH, {"H1": C(HW, THIEF), "H2": C(HW, OWNER), R: C(CODE, NOBODY)}, "stalemate"),
    ("11 standard", STD, {R: C(CODE, BOTH), "Ds": C(DEV, OWNER), P: C(PW, OWNER)}, "stalemate"),
    ("11 high", HIGH, {R: C(CODE, BOTH), "H1": C(HW, OWNER), P: C(PW, OWNER)}, "stalemate"),
    ("12 standard", STD, {R: C(CODE, THIEF), "Ds": C(DEV, OWNER), P: C(PW, OWNER)}, THIEF),
    ("12 high", HIGH, {R: C(CODE, THIEF), "H1": C(HW, OWNER), P: C(PW, OWNER)}, THIEF),
    (13, STD, {R: C(CODE, BOTH), P: C(PW, BOTH), "Ds": C(DEV, OWNER)}, "stalemate"),
    (14, STD, {"Ds": C(DEV, OWNER), P: C(PW, OWNER), R: C(CODE, OWNER)}, OWNER),
    ("14 high", HIGH, {"H1": C(HW, OWNER), P: C(PW, OWNER), R: C(CODE, OWNER)}, OWNER),
    (15, STD, {"D1": C(DEV, THIEF), "D4": C(DEV, THIEF), "D2": C(DEV, OWNER), "D3": C(DEV, OWNER)}, "race"),
    ("15 with a code the owner alone holds", STD,
     {"D1": C(DEV, THIEF), "D4": C(DEV, THIEF), "D2": C(DEV, OWNER), "D3": C(DEV, OWNER), R: C(CODE, OWNER)}, OWNER),
    ("15 with a code nobody holds", STD,
     {"D1": C(DEV, THIEF), "D4": C(DEV, THIEF), "D2": C(DEV, OWNER), "D3": C(DEV, OWNER), R: C(CODE, NOBODY)}, "race"),
    (16, STD, {"Dp": C(DEV, THIEF), "Dt": C(DEV, THIEF, counts=False), P: C(PW, OWNER)}, "stalemate"),
    (17, STD, {"Ds": C(DEV, BOTH), R: C(CODE, THIEF, counts=False), P: C(PW, OWNER)}, OWNER),
    ("17 missed for a day", STD, {"Ds": C(DEV, BOTH), R: C(CODE, THIEF), P: C(PW, OWNER)}, THIEF),
    (18, STD, {P: C(PW, BOTH), "Dt": C(DEV, THIEF, counts=False)}, "stalemate"),
    (19, STD, {P: C(PW, OWNER), "Dl": C(DEV, NOBODY)}, OWNER),
    ("19 with the owner's code", STD, {P: C(PW, OWNER), "Dl": C(DEV, NOBODY), R: C(CODE, OWNER)}, OWNER),
    (20, HIGH, {P: C(PW, OWNER), "Hl": C(HW, NOBODY), R: C(CODE, NOBODY)}, "admin"),
    (21, STD, {"Dx": C(DEV, THIEF), "D": C(DEV, OWNER)}, "stalemate"),
    ("21 with a forgotten password", STD, {"Dx": C(DEV, THIEF), "D": C(DEV, OWNER), P: C(PW, NOBODY)}, "race"),
    ("21 with a code nobody holds", STD, {"Dx": C(DEV, THIEF), "D": C(DEV, OWNER), R: C(CODE, NOBODY)}, "stalemate"),
    (22, HIGH, {"H1": C(HW, THIEF), "H2": C(HW, OWNER), R: C(CODE, OWNER)}, OWNER),
    (23, STD, {R: C(CODE, OWNER)}, OWNER),
    ("23 with lost passkeys still listed", STD, {R: C(CODE, OWNER), "D1": C(DEV, NOBODY), "D2": C(DEV, NOBODY)}, OWNER),
    ("23 with a lost passkey found", STD, {R: C(CODE, OWNER), "D": C(DEV, THIEF)}, OWNER),
    ("23 high, with a lost key found", HIGH, {R: C(CODE, OWNER), "H": C(HW, THIEF)}, OWNER),
    (24, STD, {"Dl": C(DEV, NOBODY), P: C(PW, NOBODY)}, "admin"),
]


@pytest.mark.parametrize("row,tier,creds,outcome", ROWS, ids=[str(r[0]) for r in ROWS])
def test_the_model_is_the_table(row, tier, creds, outcome):
    assert ref_outcome(tier, creds) == outcome


# ---- 2. the code is the model ---------------------------------------------------------------------------------------

POOL = {P: PW, "Ds": DEV, "D1": DEV, "D2": DEV, "H1": HW, "H2": HW, R: CODE}


def placed_of(tier, creds):
    """The account_holds.Placed for a model account (the one place the test turns the model's terms into the code's)."""
    return ah.Placed(tier=tier,
                     password=creds[P].counts if P in creds else None,
                     passkeys=tuple(ah.Passkey(n, c.kind == HW, c.counts) for n, c in sorted(creds.items())
                                    if c.kind in (DEV, HW)),
                     recovery=creds[R].counts if R in creds else None)


def accounts(pool):
    """Every account over `pool` without holders: each credential absent, counting yet, or not yet; both tiers."""
    names = sorted(pool)
    for states in itertools.product((None, True, False), repeat=len(names)):
        creds = {n: Cred(pool[n], NOBODY, s) for n, s in zip(names, states) if s is not None}
        for tier in (STD, HIGH):
            yield tier, creds


def subsets(names):
    names = sorted(names)
    return [set(s) for r in range(len(names) + 1) for s in itertools.combinations(names, r)]


def test_cancellers_are_the_models():
    compared = refused = 0
    for tier, creds in accounts(POOL):
        placed = placed_of(tier, creds)
        makers = [n for n, c in creds.items() if ref_counts(tier, c)] + [ah.ADMIN]
        backing = [False, True] if R in creds and ref_counts(tier, creds[R]) else [False]
        for took in subsets(creds):
            for maker in makers:
                for by_code in backing:
                    change = ah.Change(maker, frozenset(took), by_code)
                    if maker == ah.ADMIN and (by_code or took != set(creds)):
                        try:                                    # the admin makes only the recovery that takes all
                            ah.cancellers(placed, change)
                        except ValueError:
                            refused += 1
                            continue
                        raise AssertionError(("the admin's change was not refused", took, by_code))
                    got = ah.cancellers(placed, change)
                    assert set(got) == ref_cancellers(tier, creds, maker, took, by_code), \
                        (tier, {n: c.counts for n, c in creds.items()}, maker, took, by_code)
                    compared += 1
    assert compared > 450_000 and refused > 10_000


def test_takes_and_may_make_are_the_models():
    assert len(ah.KINDS) == len(set(ah.KINDS)) and set(ah.KINDS) == set(MODEL_KINDS)
    pool = {P: PW, "D1": DEV, "D2": DEV, "H1": HW, R: CODE}
    compared = 0
    for tier, creds in accounts(pool):
        placed = placed_of(tier, creds)
        for kind in MODEL_KINDS:
            options = subsets(creds) if kind == ah.KEEP_ONLY else [set()]
            for removes in options:
                assert set(ah.takes(placed, kind, removes)) == ref_takes(creds, kind, removes)
                for maker in list(creds) + [ah.ADMIN]:
                    for enc in (False, True):
                        assert ah.may_make(placed, kind, maker, removes, enc) == \
                            ref_may(tier, creds, kind, maker, removes, enc), (tier, kind, maker, removes, enc)
                        compared += 1
            # removing what the account does not have, or naming what is removed for a change that is not a keep-only
            for removes in ({"D9"}, set(creds) | {"D9"}):
                with pytest.raises(ValueError):
                    ah.takes(placed, kind, removes)
                for maker in list(creds) + [ah.ADMIN]:
                    assert not ah.may_make(placed, kind, maker, removes)
                    assert not ref_may(tier, creds, kind, maker, removes)
    assert compared > 100_000


def test_what_the_rule_refuses_to_guess():
    placed = ah.Placed(STD, True, (ah.Passkey("D1", False), ah.Passkey("Dt", False, counts=False)), False)
    with pytest.raises(ValueError, match="did not count"):
        ah.cancellers(placed, ah.Change("Dt", frozenset()))             # a provisional credential makes nothing
    with pytest.raises(ValueError, match="did not count"):
        ah.cancellers(placed, ah.Change(R, frozenset()))                # nor a code that does not count yet
    with pytest.raises(ValueError, match="no code counted"):
        ah.cancellers(placed, ah.Change("D1", frozenset(), by_code=True))
    with pytest.raises(ValueError, match="did not have"):
        ah.cancellers(placed, ah.Change("D1", frozenset({"D9"})))
    bare = ah.Placed(STD, None, (ah.Passkey("D1", False),), None)       # no password, no code
    with pytest.raises(ValueError, match="did not have"):
        ah.cancellers(bare, ah.Change("D1", frozenset({P})))
    with pytest.raises(ValueError, match="did not have"):
        ah.cancellers(bare, ah.Change("D1", frozenset({R})))
    assert ah.cancellers(bare, ah.Change("D1", frozenset({"D1"}))) == {"D1"}
    # the admin makes only the admin's recovery: everything taken, never the code's proof
    everything = ah.takes(placed, ah.ADMIN_RECOVER)
    with pytest.raises(ValueError, match="admin"):
        ah.cancellers(placed, ah.Change(ah.ADMIN, everything - {"Dt"}))
    with pytest.raises(ValueError, match="admin"):
        ah.cancellers(ah.Placed(STD, True, (ah.Passkey("D1", False),), True),
                      ah.Change(ah.ADMIN, frozenset({P, "D1", R}), by_code=True))
    assert ah.cancellers(placed, ah.Change(ah.ADMIN, everything)) == {P, "D1"}
    high = ah.Placed(HIGH, True, (ah.Passkey("D1", False), ah.Passkey("H1", True)), None)
    with pytest.raises(ValueError, match="did not count"):
        ah.cancellers(high, ah.Change(P, frozenset()))                  # the password counts for nothing up there
    with pytest.raises(ValueError, match="did not count"):
        ah.cancellers(high, ah.Change("D1", frozenset()))
    with pytest.raises(ValueError, match="tier"):
        ah.Placed("medium", None, (), None)
    with pytest.raises(ValueError, match="one passkey"):
        ah.Placed(STD, None, (ah.Passkey("D1", False), ah.Passkey("D1", True)), None)
    with pytest.raises(ValueError, match="one passkey"):
        ah.Placed(STD, None, (ah.Passkey(R, False),), None)
    # what a stored body could carry wrong is refused, never read as true or false by accident
    for password, recovery in (("false", None), (None, "yes"), (0.5, None)):
        with pytest.raises(ValueError, match="None"):
            ah.Placed(STD, password, (), recovery)
    with pytest.raises(ValueError, match="Passkey values"):
        ah.Placed(STD, None, ("D1",), None)
    assert ah.Placed(STD, None, [ah.Passkey("D1", False)], None).passkeys == (ah.Passkey("D1", False),)
    for bad in (("", False, True), ("D1", "no", True), ("D1", False, "yes"), (7, False, True)):
        with pytest.raises(ValueError, match="A passkey is"):
            ah.Passkey(*bad)
    assert ah.Change("D1", ["D1"]).takes_away == frozenset({"D1"})
    for bad in (("D1", "D1", False), ("D1", frozenset(), "yes"), (None, frozenset(), False), ("D1", {1}, False)):
        with pytest.raises(ValueError, match="change"):
            ah.Change(*bad)
    with pytest.raises(ValueError, match="names what it removes"):
        ah.takes(placed, ah.ADD_PASSKEY, {"D1"})
    with pytest.raises(ValueError, match="only what the account has"):
        ah.takes(placed, ah.KEEP_ONLY, {"D9"})
    with pytest.raises(ValueError, match="No such change"):
        ah.takes(placed, "rename", ())
    assert not ah.may_make(placed, "rename", "D1")


def test_admin_recovery_and_taking_a_place():
    owner = (ah.Passkey("D1", False), ah.Passkey("H1", True))
    placed = ah.Placed(STD, True, owner, True)
    recover = ah.Change(ah.ADMIN, ah.takes(placed, ah.ADMIN_RECOVER))
    assert ah.cancellers(placed, recover) == {P, "D1", "H1", R}      # (i), and (iii): it leaves nothing
    assert ah.may_make(placed, ah.ADMIN_RECOVER, ah.ADMIN) and not ah.may_make(placed, ah.ADMIN_RECOVER, "D1")
    assert not ah.may_make(placed, ah.ADD_PASSKEY, ah.ADMIN)
    # nothing takes the admin's recovery's place, though every counting credential cancels it
    assert not ah.may_take_place(placed, recover, {R, "D1", P})
    assert not ah.may_take_place(placed, recover, set(), by_admin=True)
    # the admin's recovery takes the place of anything, a code-backed change included
    backed = ah.Change("D1", frozenset({"H1"}), by_code=True)
    assert ah.may_take_place(placed, backed, set(), by_admin=True)
    assert not ah.may_take_place(placed, backed, {"D1", P, "H1"})    # only with the code's proof
    assert ah.may_take_place(placed, backed, {R})
    # row 5: the thief's change of the password, made with it; the owner's D takes its place, the password cannot
    five = ah.Placed(STD, True, (ah.Passkey("D", False),), None)
    change = ah.Change(P, ah.takes(five, ah.CHANGE_PASSWORD))
    assert not ah.may_take_place(five, change, {P})
    assert ah.may_take_place(five, change, {P, "D"})                  # a confirmation given with the request
    assert not ah.may_take_place(five, change, set())
    assert not ah.may_take_place(five, change, {"D9"})                # nothing the rule names


def test_the_cases_encryption_s_first_rule_reused_would_get_wrong():
    """The cases encryption's first rule, reused, would get wrong, each by name."""
    # row 9: passkeys alone, no code; the thief's H removes the owner's Ds and leaves only H: (iii) lets Ds cancel
    nine = ah.Placed(STD, None, (ah.Passkey("Ds", False), ah.Passkey("H", True)), None)
    assert ah.cancellers(nine, ah.Change("H", ah.takes(nine, ah.KEEP_ONLY, {"Ds"}))) == {"H", "Ds"}
    # row 8: turning high security on takes the counting of the password and the device passkeys away, so with H the
    # only hardware key they cancel it, and so does the code; with no code it cannot be made at all
    eight = ah.Placed(STD, True, (ah.Passkey("Ds", False), ah.Passkey("H", True)), True)
    took = ah.takes(eight, ah.HIGH_ON)
    assert took == {P, "Ds"}
    assert ah.cancellers(eight, ah.Change("H", took)) == {"H", "Ds", P, R}
    assert not ah.may_make(ah.Placed(STD, True, eight.passkeys, None), ah.HIGH_ON, "H")
    # a second hardware key that does not count yet does not stop (iii) from firing
    eight_b = ah.Placed(STD, True, eight.passkeys + (ah.Passkey("Hn", True, counts=False),), True)
    assert ah.cancellers(eight_b, ah.Change("H", ah.takes(eight_b, ah.HIGH_ON))) == {"H", "Ds", P, R}
    # the high tier: a device passkey counts for nothing, so it does not keep (iii) from letting the removed H2 cancel
    high = ah.Placed(HIGH, None, (ah.Passkey("H1", True), ah.Passkey("H2", True), ah.Passkey("D", False)), True)
    assert ah.cancellers(high, ah.Change("H1", ah.takes(high, ah.KEEP_ONLY, {"H2", R}))) == {"H1", "H2", R}
    # rows 3 and 5: the password cancels; the rule has a password branch
    three = ah.Placed(STD, True, (ah.Passkey("Ds", False),), None)
    assert ah.cancellers(three, ah.Change("Ds", ah.takes(three, ah.CHANGE_PASSWORD))) == {"Ds", P}
    assert ah.cancellers(three, ah.Change(P, ah.takes(three, ah.ADD_PASSKEY))) == {"Ds", P}
    # an account with no code: the rule holds, and nothing names the code
    assert R not in ah.cancellers(nine, ah.Change("Ds", frozenset()))


# ---- 3. the properties, on the model --------------------------------------------------------------------------------

PROPERTY_POOL = {P: PW, "D1": DEV, "D2": DEV, "H1": HW, R: CODE}


def accounts_with_holders(pool):
    names = sorted(pool)
    each = [None] + [(h, c) for h in (OWNER, THIEF, BOTH, NOBODY) for c in (True, False)]
    for states in itertools.product(each, repeat=len(names)):
        creds = {n: Cred(pool[n], s[0], s[1]) for n, s in zip(names, states) if s is not None}
        for tier in (STD, HIGH):
            yield tier, creds


@pytest.fixture(scope="module")
def universe():
    return [(tier, creds, ref_outcome(tier, creds)) for tier, creds in accounts_with_holders(PROPERTY_POOL)]


def counting_held(tier, creds, side):
    return {n for n in held(creds, side) if ref_counts(tier, creds[n])}


def code_holders(tier, creds):
    if R not in creds or not ref_counts(tier, creds[R]):
        return set()
    return {OWNER, THIEF} if creds[R].holder == BOTH else {creds[R].holder} - {NOBODY}


def lost(creds):
    """Whether a passkey or the password is held by nobody (forgotten, or lost and still listed). The design's second
    property holds only without one, and so do its third, sixth and seventh and the second half of
    its fifth (such a credential keeps (iii) from firing, so a side can take
    its own too and keep a new password or code); the tests below assert each with it and that it fails without it."""
    return any(c.holder == NOBODY and c.kind != CODE for c in creds.values())


def test_whoever_alone_holds_the_code_wins(universe):
    checked = 0
    for tier, creds, outcome in universe:
        holders = code_holders(tier, creds)
        if holders == {OWNER}:
            assert outcome == OWNER, (tier, vars_of(creds))
        elif holders == {THIEF}:
            assert outcome == THIEF, (tier, vars_of(creds))
        elif holders == {OWNER, THIEF}:
            assert outcome in ("stalemate", "admin"), (tier, vars_of(creds))
        else:
            continue
        checked += 1
    assert checked > 10_000


def test_one_stolen_credential_never_takes_over(universe):
    checked = 0
    for tier, creds, outcome in universe:
        if code_holders(tier, creds):
            continue
        thief = held(creds, THIEF)
        if len(thief) != 1 or not counting_held(tier, creds, THIEF):
            continue
        if not counting_held(tier, creds, OWNER) - thief:
            continue
        if lost(creds):
            continue
        assert outcome not in (THIEF, "race"), (tier, vars_of(creds))
        checked += 1
    assert checked > 500


def test_equal_holdings_are_never_a_race_or_a_win(universe):
    checked = broken = 0
    for tier, creds, outcome in universe:
        if held(creds, OWNER) != held(creds, THIEF):
            continue
        if lost(creds):
            broken += outcome not in ("stalemate", "admin")
            continue
        assert outcome in ("stalemate", "admin"), (tier, vars_of(creds))
        checked += 1
    assert checked > 500 and broken > 0                             # a forgotten password listed: a race


def test_with_a_credential_nobody_holds_equal_holdings_are_a_race_and_without_it_a_stalemate():
    """Two cases, standard tier: with a credential nobody holds, equal holdings are a race; without it, a
    stalemate. And the code agrees that nothing either side holds may cancel the winning change."""
    one = {P: C(PW, BOTH), "D1": C(DEV, NOBODY)}                        # the password known to both, a lost passkey
    two = {"H1": C(HW, BOTH), P: C(PW, NOBODY), R: C(CODE, NOBODY)}     # a forgotten password, the code lost
    assert ref_outcome(STD, one) == "race" and ref_outcome(STD, two) == "race"
    assert ref_outcome(STD, {P: C(PW, BOTH)}) == "stalemate"
    assert ref_outcome(STD, {"H1": C(HW, BOTH), R: C(CODE, NOBODY)}) == "stalemate"
    assert ref_outcome(STD, {"H1": C(HW, BOTH), P: C(PW, NOBODY)}) == "stalemate"
    # either side changes the password with it: only the lost passkey could cancel
    placed = ah.Placed(STD, True, (ah.Passkey("D1", False),), None)
    assert ah.cancellers(placed, ah.Change(P, ah.takes(placed, ah.CHANGE_PASSWORD))) == {"D1"}
    # either side removes H1 and replaces the code, in one keep-only made with H1: only the forgotten password and the
    # lost code could cancel
    placed = ah.Placed(STD, True, (ah.Passkey("H1", True),), True)
    assert ah.cancellers(placed, ah.Change("H1", ah.takes(placed, ah.KEEP_ONLY, {"H1", R}))) == {P, R}


def test_the_password_takes_no_passkey_away():
    for tier, creds in accounts({P: PW, "D1": DEV, "H1": HW, R: CODE}):
        for kind in MODEL_KINDS:
            for removes in (subsets(creds) if kind == ah.KEEP_ONLY else [set()]):
                for enc in (False, True):
                    if ref_may(tier, creds, kind, P, removes, enc):
                        assert not ref_takes(creds, kind, removes) & {"D1", "H1"}


def test_a_credential_that_does_not_count_yet_never_helps_the_thief(universe):
    """Taken away, the thief's credentials that do not count yet leave the thief what it had: the owner, undoing them,
    loses nothing. Where a passkey or the password is held by nobody, a code the thief made that does not count yet can
    still give it a race (it replaces that code in a keep-only and keeps the new one); the owner then holds a counting
    credential, which could have undone it."""
    by_account = {key_of(tier, creds): outcome for tier, creds, outcome in universe}
    checked = broken = 0
    for tier, creds, outcome in universe:
        provisional = {n for n, c in creds.items() if c.holder == THIEF and not c.counts}
        if not provisional or outcome not in (THIEF, "race"):
            continue
        without = {n: c for n, c in creds.items() if n not in provisional}
        if by_account[key_of(tier, without)] in (THIEF, "race"):
            checked += 1
            continue
        assert lost(creds) and counting_held(tier, creds, OWNER), (tier, vars_of(creds))
        broken += 1
    assert checked > 100 and broken > 0


def test_the_owner_with_more_wins(universe):
    checked = broken = 0
    for tier, creds, outcome in universe:
        if THIEF in code_holders(tier, creds):
            continue
        mine, theirs = counting_held(tier, creds, OWNER), counting_held(tier, creds, THIEF)
        if theirs <= mine and mine - theirs:
            if lost(creds):
                broken += outcome != OWNER
                continue
            assert outcome == OWNER, (tier, vars_of(creds))
            checked += 1
    assert checked > 5_000 and broken > 0


def test_a_code_nobody_holds_changes_no_outcome(universe):
    by_account = {key_of(tier, creds): outcome for tier, creds, outcome in universe}
    checked = broken = 0
    for tier, creds, outcome in universe:
        if R in creds and creds[R].holder == NOBODY:
            without = {n: c for n, c in creds.items() if n != R}
            if lost(creds):
                broken += by_account[key_of(tier, without)] != outcome
                continue
            assert by_account[key_of(tier, without)] == outcome, (tier, vars_of(creds))
            checked += 1
    assert checked > 1_000 and broken > 0


def key_of(tier, creds):
    return tier, tuple(sorted((n, c.holder, c.counts) for n, c in creds.items()))


def vars_of(creds):
    return {n: (c.kind, c.holder, c.counts) for n, c in creds.items()}


# ---- 4. the agreement test with encryption's rule v2 ----------------------------------------------------------------

USER = "u1"


@pytest.fixture(scope="module")
def e2e(tmp_path_factory):
    from proofpane_open import store
    from proofpane_open.ade import e2e_keys
    path = str(tmp_path_factory.mktemp("rule") / "app.db")
    with store.connect(path) as db:
        db.execute("INSERT INTO users (id, email, password_hash, created_at) VALUES (?,?,?,?)",
                   (USER, "owner@example.com", "", 0))
        yield db, e2e_keys


def enc_cancellers(e2e, high, keys, code, kind, body, made_by, by_code):
    """e2e_keys.cancellers for an account set up as given, as a set in the account rule's terms."""
    db, e2e_keys = e2e
    db.execute("DELETE FROM passkeys")
    db.execute("DELETE FROM ade_e2e_recovery")
    db.execute("DELETE FROM ade_e2e_pending")
    db.execute("UPDATE users SET high_security = ? WHERE id = ?", (int(high), USER))
    for i, (name, hardware) in enumerate(sorted(keys.items())):
        db.execute("INSERT INTO passkeys (id, user_id, credential_id, public_key, alg, sign_count, backed_up, name, "
                   "created_at, tier) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (name, USER, "cred-" + name, "", -7, 0, 0, name, i, "hardware" if hardware else "device"))
    if code:
        db.execute("INSERT INTO ade_e2e_recovery (user_id, public_key, anchor, created_at) VALUES (?,?,?,?)",
                   (USER, "", "", 0))
    db.execute("INSERT INTO ade_e2e_pending (user_id, hold_id, kind, body, created_at, made_by, by_code) VALUES "
               "(?,?,?,?,?,?,?)", (USER, "h1", kind, json.dumps(body), 0, made_by, int(by_code)))
    row = db.execute("SELECT * FROM ade_e2e_pending WHERE user_id = ?", (USER,)).fetchone()
    may, code_cancels = e2e_keys.cancellers(db, USER, row)
    return set(may) | ({R} if code_cancels else set())


def enc_takes_place(e2e, by, by_code):
    """Whether e2e_keys._in_place_of lets a change signed by `by` (with the code's proof when `by_code`) take the place of
    the change enc_cancellers last set up: (v) in encryption's rule."""
    from proofpane_open.ade import link
    db, e2e_keys = e2e
    try:
        e2e_keys._in_place_of(db, USER, by, by_code)
    except link.LinkError as e:
        assert e.status == 409
        return False
    return True


ENC_KEYS = {"Ds": False, "D1": False, "D2": False, "H1": True, "H2": True}


def coincidence_domain():
    """The two rules' domain: an account change and an encryption change that take away the same passkeys (removing
    passkeys; turning high security on; adding a passkey), made with the same counting passkey or by the code, with or
    without the code's proof, on an account with a code whose password does not count, and on which every credential
    counts yet. Yields (high, keys, account change kind and removes, encryption change kind and body, maker, by_code)."""
    for chosen in subsets(ENC_KEYS):
        keys = {n: ENC_KEYS[n] for n in chosen}
        for high in (False, True):
            counting = {n for n, hw in keys.items() if hw or not high}
            changes = [(ah.ADD_PASSKEY, set(), "passkey", {})]
            changes += [(ah.KEEP_ONLY, gone, "revoke", {"remove": sorted(gone), "high_security": False})
                        for gone in subsets(keys) if gone]
            if not high:
                changes.append((ah.HIGH_ON, set(), "revoke", {"remove": [], "high_security": True}))
            for kind, removes, enc_kind, body in changes:
                for maker in sorted(counting) + [R]:
                    for by_code in (False, True):
                        yield high, keys, kind, removes, enc_kind, body, maker, by_code


def account_placed(high, keys, password=None, provisional=None, code=True):
    """The account's Placed for encryption's `keys` (name to hardware), every one counting yet, with `provisional`
    (name to hardware) beside them not counting yet."""
    return ah.Placed(HIGH if high else STD, password,
                     tuple(ah.Passkey(n, hw) for n, hw in sorted(keys.items()))
                     + tuple(ah.Passkey(n, hw, counts=False) for n, hw in sorted((provisional or {}).items())),
                     code)


def test_the_two_rules_agree_where_they_coincide(e2e):
    """Who cancels, and (v), who may take the place: the same in the domain where the two rules coincide."""
    compared = places = 0
    for high, keys, kind, removes, enc_kind, body, maker, by_code in coincidence_domain():
        expected = enc_cancellers(e2e, high, keys, True, enc_kind, body, maker, by_code)
        for password in (None, True) if high else (None,):              # high on: the password counts for nothing
            placed = account_placed(high, keys, password)
            change = ah.Change(maker, ah.takes(placed, kind, removes) - {P}, by_code)
            assert set(ah.cancellers(placed, change)) == expected, (high, keys, kind, removes, maker, by_code)
            compared += 1
            # (v): encryption takes the place with its signer or the code's proof; the account, with any of what its
            # request carries, so the account's answer for one credential is encryption's, and for two it is either's
            for by in sorted(keys) + [None]:
                for proof in (False, True):
                    given = ({by} if by else set()) | ({R} if proof else set())
                    enc = enc_takes_place(e2e, by, proof)
                    assert ah.may_take_place(placed, change, given) == enc, (keys, kind, removes, maker, by, proof)
                    places += 1
            for by, also in itertools.combinations(sorted(keys), 2):
                assert ah.may_take_place(placed, change, {by, also}) == \
                    (enc_takes_place(e2e, by, False) or enc_takes_place(e2e, also, False))
    assert compared > 1_000 and places > 10_000


def test_where_the_two_rules_differ(e2e):
    """Outside the domain, the differences, exactly: a passkey not counting yet, no recovery code, and the password."""
    password_cases = provisional_cases = no_code_cases = 0
    for high, keys, kind, removes, enc_kind, body, maker, by_code in coincidence_domain():
        enc = enc_cancellers(e2e, high, keys, True, enc_kind, body, maker, by_code)
        backed = maker == R or by_code
        # counting yet: a passkey that does not count yet neither cancels nor keeps (iii) from firing, removed or not;
        # encryption has no such passkey, so the account's rule with one is encryption's without it
        for provisional in ({"Dn": False}, {"Hn": True}, {"Dn": False, "Hn": True}):
            placed = account_placed(high, keys, provisional=provisional)
            for also in (set(), set(provisional)) if kind == ah.KEEP_ONLY else (set(),):
                took = ah.takes(placed, kind, removes | also)
                assert set(ah.cancellers(placed, ah.Change(maker, took, by_code))) == enc, (keys, provisional, also)
                provisional_cases += 1
        if high:
            continue
        # an account with no recovery code (encryption on always has one): a change made with a passkey, with no
        # proof, is cancelled by the passkeys encryption's rule names, and by no code
        if not backed and kind != ah.HIGH_ON:
            bare = enc_cancellers(e2e, high, keys, False, enc_kind, body, maker, False)
            placed = account_placed(high, keys, code=None)
            assert set(ah.cancellers(placed, ah.Change(maker, ah.takes(placed, kind, removes)))) == bare
            assert R not in bare
            no_code_cases += 1
            enc = enc_cancellers(e2e, high, keys, True, enc_kind, body, maker, by_code)
        # the password, in the standard tier, on a change that leaves it and on one that takes it
        placed = account_placed(high, keys, password=True)
        for removing in (removes, removes | {P}) if kind == ah.KEEP_ONLY else (removes,):
            took = ah.takes(placed, kind, removing)
            got = set(ah.cancellers(placed, ah.Change(maker, took, by_code)))
            taken_keys = took - {P}
            if backed:
                assert got == enc == {R}                                # the code's alone: the password plays no part
            elif P not in took:
                assert got == (enc - taken_keys) | {P}                 # 1. P added; removed passkeys out
            else:
                fired = set(keys) - taken_keys <= {maker}               # 2. (iii) reads encryption's passkeys
                assert got == enc | ({P} if fired else set())
            password_cases += 1
    assert password_cases > 500 and provisional_cases > 3_000 and no_code_cases > 100


def test_a_change_made_with_the_password():
    """Encryption has no counterpart here: made with P and taking P, a change leaves the passkeys;
    with one or more only they cancel it (row 5), with none the old password does (row 6)."""
    five = ah.Placed(STD, True, (ah.Passkey("D", False),), None)
    assert ah.cancellers(five, ah.Change(P, ah.takes(five, ah.CHANGE_PASSWORD))) == {"D"}
    six = ah.Placed(STD, True, (), None)
    assert ah.cancellers(six, ah.Change(P, ah.takes(six, ah.CHANGE_PASSWORD))) == {P}
    # and a forgotten password still left keeps (iii) from firing (row 21)
    placed = ah.Placed(STD, True, (ah.Passkey("D", False), ah.Passkey("Dx", False)), None)
    assert ah.cancellers(placed, ah.Change("Dx", frozenset({"D"}))) == {P, "Dx"}
