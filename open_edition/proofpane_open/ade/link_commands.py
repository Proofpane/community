"""The session channel's commands (stage 2a): the owner's instructions and answers, signed with one of the account's
passkeys over a payload the page composed, each taken by the one linked session it is addressed to; the owner's
stop of a session with the signed resume that lifts it; and the owner's move of one of a linked machine's Claude Code
sessions into the hosted workspace, signed the same way, held (ade/holds.py) and stored only when its hold ends, then
taken by that machine's link, whose client sends the session's files (ade/moves.py). ade/link.py is stage 1, on which
this stands.

What a passkey signs is one JSON object, its canonical bytes RFC 8785 (proofpane_core.canonical), the WebAuthn
challenge the sha256 of those bytes. The standard wants the challenge's randomness from the relying party; a nonce the
server gave out (passkeys.new_nonce) stands in for it inside the payload, recorded as used when the payload is stored,
so a replay is refused. Before it stores anything the server checks the assertion, the nonce, the account, the
deployment, the addressed link and the clock, and it stores the bytes it computed with the assertion as it came. A
session checks the same signature again itself, offline, against owner keys it pinned (ade_link.py pin, show), and a
command is the owner's to a session only once that session's own take has succeeded first here. A payload whose id is
a connected app's proposal (ade/proposals.py) is that proposal, signed by the owner now: the proposal must still wait,
and propose this very command (its purpose, its session, an answer's question), or the payload is refused.

The payload's fields (each value a string, or an integer below 2**53):

    v            2
    deployment   the host every link signature covers (link.signing_host())
    purpose      instruction | answer | resume | move
    account      the signed-in account's id
    nonce        the id POST /nonce gave, whole: two parts of base64url joined by a dot, at most 600 characters
    id           32 lowercase hex characters, new for each payload; the event's client_id
    to           the addressed link's id, 32 lowercase hex characters, never its name (a later link may reuse a name)
    ref, digest  an answer's only: the question's seq, and "sha256:" with the hex sha256 of the question's text
    text         the words, at most 4,000 characters; "" for a resume; a move has none
    session      a move's only: the Claude Code session's id, 1 to 100 of A-Z a-z 0-9 _ -, starting with a letter or a
                 digit; one the addressed link's History list has, not withheld
    created_at   unix seconds by the server's clock, carried forward by the page from when its nonce came; the
                 server refuses one more than 300 seconds from its own
    expires_at   60 to 86,400 seconds after created_at (a resume: 60 to 600; a move: 60 to 3,600, and at least its
                 hold and moves.HOLD_MARGIN seconds after the server's time as it is posted)

A stop needs only the page's sign-in, since a forged stop can only halt; a resume needs a passkey, so a stolen sign-in
cannot restart what the owner stopped. What a stop holds is enforced by the session's own hook and watcher, which lift
it only on a resume they verify themselves.

A task's grant (ade/task_grants.py) is a payload of a purpose of its own, task-grant: checked here as every signed
payload is (check_grant), its own fields by task_grants.check_fields, and kept there, never as an event a session reads.
"""

import hashlib
import json
import re
import sqlite3
import time
import unicodedata

from .. import passkeys, paths, store  # noqa: F401  (paths puts the open packages on sys.path)
from . import link
from proofpane_core.canonical import CanonicalizationError, canonicalize

VERSION = 2
COMMAND_KINDS = ("instruction", "answer")
MOVE = "move"
SIGNED_KINDS = COMMAND_KINDS + (MOVE,)       # what POST /commands stores and a session takes
# the owner's statements of the account's keys for end-to-end encryption (ade/e2e_keys.py): a content key links seal
# to, and encryption turned off; signed and checked as a command is, but stored by e2e_keys, never as an event
E2E_KEY, E2E_OFF = "e2e-key", "e2e-off"
STATEMENTS = (E2E_KEY, E2E_OFF)
# a key statement's version: 2 names a classical key (32 bytes, X25519), 3 a post-quantum one (1,216 bytes, X-Wing),
# which only a post-quantum account states; e2e-off names no key and stays 2
STATEMENT_CLASSICAL, STATEMENT_HYBRID = 2, 3
STATEMENT_VERSIONS = (STATEMENT_CLASSICAL, STATEMENT_HYBRID)
# a task's grant (ade/task_grants.py): the owner's statement of one task's scope, signed and checked as a command
# is (check_grant), and kept by task_grants, never as an event
TASK_GRANT = "task-grant"
# a linked session made the Manager (ade/manager_link.py): the link it
# names holds the role under the settings whose digest it names, until it expires, a week at most
MANAGER = "manager"
PURPOSES = ("instruction", "answer", "resume", MOVE) + STATEMENTS + (TASK_GRANT, MANAGER)
_NO_TO = STATEMENTS + (TASK_GRANT,)          # the purposes whose payload names no link
_STATEMENT_FIELDS = frozenset({"v", "deployment", "purpose", "account", "nonce", "id", "seq", "prev", "created_at",
                               "expires_at"})
FIELDS = {
    "instruction": frozenset({"v", "deployment", "purpose", "account", "nonce", "id", "to", "text", "created_at",
                              "expires_at"}),
    "answer": frozenset({"v", "deployment", "purpose", "account", "nonce", "id", "to", "ref", "digest", "text",
                         "created_at", "expires_at"}),
    "resume": frozenset({"v", "deployment", "purpose", "account", "nonce", "id", "to", "text", "created_at",
                         "expires_at"}),
    MOVE: frozenset({"v", "deployment", "purpose", "account", "nonce", "id", "to", "session", "created_at",
                     "expires_at"}),
    E2E_KEY: _STATEMENT_FIELDS | {"key", "key_id", "retires"},
    E2E_OFF: _STATEMENT_FIELDS,
    TASK_GRANT: frozenset({"v", "deployment", "purpose", "account", "nonce", "id", "task", "version", "prev", "ask",
                           "requester", "widening", "repositories", "environments", "secrets", "egress", "lease_end",
                           "rungs", "created_at", "expires_at"}),
    MANAGER: frozenset({"v", "deployment", "purpose", "account", "nonce", "id", "to", "settings", "created_at",
                        "expires_at"}),
}
RETIRES = ("all", "grace")           # a revocation retires the earlier keys at once, a routine rotation an hour after
PAYLOAD_MAX = 20000              # the canonical bytes
ASSERTION_MAX = 8192             # the credential as JSON
CLOCK_SKEW = 300
# a payload is made when its nonce comes: the page composes it from the server's time at that moment, so its
# created_at is held to the nonce's issue (a second or two of the page's own counting allowed); else a page could date
# a resume after a stop made later than it signed, which would then lift that stop
NONCE_DRIFT = 30
LIFE_MIN = 60
LIFE_MAX = 86400
RESUME_LIFE_MAX = 600
MOVE_LIFE_MAX = 3600
STATEMENT_LIFE_MAX = 600             # bounds only the server's acceptance of a statement: a link keeps one for good
GRANT_LIFE_MAX = 600                 # and of a task's grant, whose own lease is its lease_end (ade/task_grants.py)
MANAGER_LIFE_MAX = 7 * 86400         # a holder of the Manager's role, renewed by a new signature
SEQ_STATED_MAX = 2 ** 53 - 1         # a statement's seq, as RFC 8785 writes an integer exactly
# what an owner's move event says, for the link's timeline and what a session reads (never acted on: the session's
# client takes the move and sends the session's files)
MOVE_TEXT = "move session {} into the workspace"
SEALED_IN_PLACE = ("That question was sealed as encryption was turned on: its answer carries the digest of the words its "
                   "session asked, not of its sealed text.")
OWNER_PER_HOUR = 60              # the owner's own budget, counted by account; not the links' wake cap
QUEUED_PER_LINK = 50
# the owner's answers to a session's own prompts: a budget of their own,
# counted by account, so a day of prompts cannot use up OWNER_PER_HOUR; and the two fixed words of an answer's buttons
PROMPT_ANSWERS_PER_HOUR = 60
PROMPT_APPROVE, PROMPT_DENY = "Approve", "Deny"
PROMPT_WORDS = "Your own words may not read as Approve, Deny or a choice of options: those are the buttons."
_OPTIONS = re.compile(r"[1-9][0-9]{0,2}(,[1-9][0-9]{0,2})*")     # these: fullmatch, never match
_NUMBERS_LIKE = re.compile(r"[\d\s,;.]+")
_READ_AS_DROPPED = re.compile(r"[\s.,;:!?'\"]")
MAX_INSTANCE = 100
SEQ_MAX = 2 ** 63 - 1                # the largest seq SQLite stores: one past it is no event, never a 500
UNTAKEN_AFTER = {"local": 300, "cloud": 1800}
STALE_AFTER = 1800
STOP_ACK_AFTER = 120
CONFLICT_SHOWN = 86400           # a second copy's take is told for a day, whatever became of the command
TAKEN_SHOWN = 86400                  # how long after its life a taken, silent command is told of
_ID = re.compile(r"[0-9a-f]{32}")                         # these: fullmatch, never match
# a nonce as POST /nonce gives it (passkeys.new_nonce): what it signs and its MAC, each base64url, joined by a dot
_NONCE = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_B64U = re.compile(r"[A-Za-z0-9_-]+")
# the variation selectors but the text and emoji ones (VS15 and VS16, which follow a character to choose how it is
# drawn); everything in plane 14 (the tag characters and the variation selectors supplement) is refused as well
# the variation selectors and the characters drawn as nothing, as link.py holds them for every line a session reads
_SELECTORS, _PRESENTATION, _BLANK = link.SELECTORS, link.PRESENTATION, link.BLANK


# marks drawn over the character before them, so a line reads otherwise than its letters say ("not" struck through, "="
# read as "not equal"), or around it (a circle, a prohibition sign, a keycap): Unicode's overlays (canonical combining
# class 1) and its enclosing marks (category Me), as Unicode 15.1 lists them; the page holds the same list (textRules)
OVERLAYS = frozenset([*range(0x0334, 0x0339), 0x1CD4, *range(0x1CE2, 0x1CE9), 0x20D2, 0x20D3, *range(0x20D8, 0x20DB),
                      0x20E5, 0x20E6, 0x20EA, 0x20EB, 0x10A39, *range(0x16AF0, 0x16AF5), 0x1BC9E,
                      *range(0x1D167, 0x1D16A)])
ENCLOSING = frozenset([0x0488, 0x0489, 0x1ABE, *range(0x20DD, 0x20E1), *range(0x20E2, 0x20E5), *range(0xA670, 0xA673)])
DRAWN_OVER = OVERLAYS | ENCLOSING
MARKS_MAX = 2       # the non-spacing marks one character may carry in what a connected app proposes (proposals.py)


def drawn_over(value):
    """The first character of `value` drawn over or around the one before it (DRAWN_OVER), as its code, or None."""
    return next((ord(ch) for ch in value if ord(ch) in DRAWN_OVER), None)


def stacked(value, most=MARKS_MAX):
    """The first character of `value` that more than `most` non-spacing marks (category Mn) follow, as its code (a
    mark's own where the text begins with them), or None: such a stack is drawn over the lines around it."""
    run, base = 0, None
    for ch in value:
        if unicodedata.category(ch) == "Mn":
            run += 1
            if run > most:
                return base if base is not None else ord(ch)
        else:
            run, base = 0, ord(ch)
    return None


def hidden_character(value):
    """The first character of `value` that does not show what it is, or None: a format character (category Cf: the
    bidi controls, the zero-width space, non-joiner and joiner, the word joiner and the invisible operators, the soft
    hyphen, the byte order mark, the tag characters), private use (Co), a line or paragraph separator (Zl, Zp),
    anything in plane 14, one of the characters drawn as nothing in _BLANK, a variation selector but VS15 and VS16,
    or one of those two at the start or right after another; the page refuses the same set."""
    prev = None
    for ch in value:
        code = ord(ch)
        if unicodedata.category(ch) in ("Cf", "Co", "Zl", "Zp") or 0xE0000 <= code <= 0xEFFFF or code in _SELECTORS \
                or code in _BLANK or (code in _PRESENTATION and (prev is None or prev in _PRESENTATION)):
            return code
        prev = code
    return None
# the owner's commands, a move too: counted in the owner's budget and a link's queue, taken and withdrawn as a command
# is, told of when not taken; _COMMAND_SQL leaves out the owner's answers to prompts, which only the prompts' hook
# claims, never take, and which count in a budget of their own, in no queue and in no notice
_SIGNED_SQL = "kind IN ('instruction', 'answer', 'move') AND link_id = 'owner'"
_COMMAND_SQL = _SIGNED_SQL + " AND prompt_answer = 0"


def _canonical_options(text):
    """Whether `text` is option numbers as the page writes them: 1 to 999, joined by commas alone, strictly
    ascending."""
    if not _OPTIONS.fullmatch(text):
        return False
    numbers = [int(n) for n in text.split(",")]
    return all(a < b for a, b in zip(numbers, numbers[1:]))


def reads_as_fixed(text):
    """Whether the owner's own words read as one of an answer's buttons: Approve or Deny (approved, denied) whatever
    their case, spaces and punctuation, or numbers that are not the options' own form (canonical_options)."""
    folded = _READ_AS_DROPPED.sub("", text).casefold()
    return folded in ("approve", "approved", "deny", "denied") \
        or (_NUMBERS_LIKE.fullmatch(text) is not None and not _canonical_options(text))


def prompt_answer_form(text):
    """What an answer to a prompt says, by its text alone, since a signed answer carries no field saying which button
    made it: "approve" (exactly PROMPT_APPROVE), "deny" (exactly PROMPT_DENY), "numbers" (the options chosen, as
    _canonical_options writes them) or "words" (the owner's own, which the hook gives the model as the denial's
    message). Words that read as one of the others are refused (422): those are the buttons."""
    if text == PROMPT_APPROVE:
        return "approve"
    if text == PROMPT_DENY:
        return "deny"
    if _canonical_options(text):
        return "numbers"
    if reads_as_fixed(text):
        raise link.LinkError(422, PROMPT_WORDS)
    return "words"


def _now(now):
    return int(now if now is not None else time.time())


def _int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def text_digest(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def check_text(value, what):
    """A command's, an answer's or a question's words: at most 4,000 characters, in Unicode normal form C, with no
    control character but a newline and a tab, no lone surrogate, no character that changes how the text reads
    without showing itself and no mark drawn over or around the character before it (DRAWN_OVER); nothing shaped like
    a secret; and not beginning e2e1., as a text sealed end to end does (ade/e2e.py), so text in the clear is never
    read as sealed (a sealed text is read as one before this is asked of it, on the paths that take one). 422 names
    the character."""
    from .e2e import PREFIX
    if not isinstance(value, str) or not value.strip():
        raise link.LinkError(422, f"The {what} is text that is not empty.")
    if len(value) > link.MAX_TEXT:
        raise link.LinkError(422, f"The {what} is at most {link.MAX_TEXT} characters.")
    if value.startswith(PREFIX):
        raise link.LinkError(422, f"The {what} begins {PREFIX}, as an encrypted text does: one in the clear may not, "
                                  "so it is never read as one.")
    for ch in value:
        code = ord(ch)
        if 0xD800 <= code <= 0xDFFF:
            raise link.LinkError(422, f"The {what} holds a lone surrogate (U+{code:04X}).")
        if (code < 0x20 and ch not in "\n\t") or 0x7F <= code <= 0x9F:
            raise link.LinkError(422, f"The {what} holds a control character (U+{code:04X}).")
    code = hidden_character(value)
    if code is not None:
        raise link.LinkError(422, f"The {what} holds U+{code:04X}, which is not shown as itself, so it could change "
                                  f"how the text reads.")
    code = drawn_over(value)
    if code is not None:
        raise link.LinkError(422, f"The {what} holds U+{code:04X}, a mark drawn over or around the character before "
                                  f"it, so it could change how the text reads.")
    if unicodedata.normalize("NFC", value) != value:
        raise link.LinkError(422, f"The {what} is not in Unicode normal form C.")
    return link.check_secrets(value, what)


def command_state(row, now):
    """queued, taken, answered, expired or withdrawn, from a command's own columns."""
    if row["withdrawn_at"] is not None:
        return "withdrawn"
    if row["closed_by"] is not None:
        return "answered"
    if row["taken_at"] is not None:
        return "taken"
    if row["expires_at"] is not None and row["expires_at"] <= now:
        return "expired"
    return "queued"


# ---- a signed payload ---------------------------------------------------------------------------------------------

def _signed_body(data):
    if not isinstance(data, dict) or not isinstance(data.get("payload"), dict) \
            or not isinstance(data.get("credential"), dict):
        raise link.LinkError(422, "A signed request is {payload, credential}.")
    return data["payload"], data["credential"]


def _canonical(payload, credential):
    try:
        raw = canonicalize(payload)
    except (CanonicalizationError, TypeError, ValueError) as e:
        raise link.LinkError(422, f"The payload has no canonical form: {e}") from None
    if len(raw) > PAYLOAD_MAX:
        raise link.LinkError(422, f"A payload is at most {PAYLOAD_MAX} bytes.")
    if len(json.dumps(credential, separators=(",", ":"))) > ASSERTION_MAX:
        raise link.LinkError(422, f"A passkey's answer is at most {ASSERTION_MAX} bytes.")
    return raw


def _check_payload(payload, user_id, purpose, now, timed=True):
    """The fields every signed payload of `purpose` must have, and nothing else; each checked before the signature.
    `timed` False, for a statement a link sends in after a restore (e2e_keys.upload), whose life bounded only its first
    acceptance: its times are whole numbers, and no more is asked of them."""
    want = FIELDS[purpose]
    if set(payload) != want:
        missing, extra = sorted(want - set(payload)), sorted(set(payload) - want)
        raise link.LinkError(422, f"A {purpose} payload has exactly the fields {', '.join(sorted(want))}"
                                  + (f"; missing {', '.join(missing)}" if missing else "")
                                  + (f"; not expected {', '.join(map(str, extra))}" if extra else "") + ".")
    if not _int(payload["v"]) or payload["v"] not in (STATEMENT_VERSIONS if purpose == E2E_KEY else (VERSION,)):
        raise link.LinkError(422, "A key statement's v is 2 for a classical key and 3 for a post-quantum one."
                             if purpose == E2E_KEY else f"A payload's v is {VERSION}.")
    if payload["purpose"] != purpose:
        raise link.LinkError(422, f"The payload's purpose is not {purpose}.")
    host = link.signing_host()
    if payload["deployment"] != host:
        raise link.LinkError(422, f"The payload is for another deployment; this ADE signs for {host}.")
    if payload["account"] != user_id:
        raise link.LinkError(403, "The payload names another account.")
    # a statement names no link: it is the account's, numbered and chained to the one before; a task's grant names its
    # task, not a link
    for name in ("nonce", "id") if purpose in _NO_TO else ("nonce", "id", "to"):
        if not isinstance(payload[name], str) or not payload[name]:
            raise link.LinkError(422, f"The payload's {name} is text.")
    if not _ID.fullmatch(payload["id"]):
        raise link.LinkError(422, "The payload's id is 32 lowercase hex characters.")
    # each in its own form before anything else reads it: never a control sequence that a terminal or a log would act on
    if not _NONCE.fullmatch(payload["nonce"]) or len(payload["nonce"]) > passkeys.MAX_CHALLENGE_ID:
        raise link.LinkError(422, "The payload's nonce is the one POST /nonce gave, whole: two parts of base64url "
                                  f"joined by a dot, at most {passkeys.MAX_CHALLENGE_ID} characters.")
    if purpose not in _NO_TO and not _ID.fullmatch(payload["to"]):
        raise link.LinkError(422, "The payload's to is a linked session's id: 32 lowercase hex characters.")
    created, expires = payload["created_at"], payload["expires_at"]
    if not _int(created) or not _int(expires):
        raise link.LinkError(422, "The payload's created_at and expires_at are whole unix seconds.")
    if timed:
        if abs(created - now) > CLOCK_SKEW:
            raise link.LinkError(422, "The payload's time is more than 5 minutes from the ADE's.", server_time=now)
        most = {"resume": RESUME_LIFE_MAX, MOVE: MOVE_LIFE_MAX, E2E_KEY: STATEMENT_LIFE_MAX,
                E2E_OFF: STATEMENT_LIFE_MAX, TASK_GRANT: GRANT_LIFE_MAX,
                MANAGER: MANAGER_LIFE_MAX}.get(purpose, LIFE_MAX)
        if not LIFE_MIN <= expires - created <= most:
            raise link.LinkError(422, f"A {purpose} expires {LIFE_MIN} to {most} seconds after it is made.")
        if expires <= now:
            raise link.LinkError(422, "The payload has expired.")
    if purpose in STATEMENTS:
        _check_statement(payload)
    elif purpose == TASK_GRANT:
        from . import task_grants
        task_grants.check_fields(payload)
    elif purpose == MOVE:
        if not isinstance(payload["session"], str) or not link._SESSION_ID.fullmatch(payload["session"]):
            raise link.LinkError(422, "A move's session is a Claude Code session's id: 1 to 100 letters, digits, - "
                                      "and _, starting with a letter or a digit.")
    elif purpose == "resume":
        if payload["text"] != "":
            raise link.LinkError(422, "A resume carries no text.")
    elif purpose == MANAGER:
        if not isinstance(payload["settings"], str) or not _DIGEST.fullmatch(payload["settings"]):
            raise link.LinkError(422, "A Manager's holder names its settings by their digest: sha256: and 64 "
                                      "lowercase hex characters.")
    else:
        check_text(payload["text"], purpose)
    if purpose == "answer":
        if not _int(payload["ref"]) or payload["ref"] <= 0:
            raise link.LinkError(422, "An answer's ref is the question's seq.")
        if not isinstance(payload["digest"], str) or not _DIGEST.fullmatch(payload["digest"]):
            raise link.LinkError(422, "An answer's digest is sha256: and 64 lowercase hex characters.")


def statement_key(text):
    """The raw bytes of a statement's key, given as base64url without padding written as its bytes give it: 32 bytes (a
    classical key, 43 characters) or 1,216 (a post-quantum one, 1,622 characters); None for anything else. Which a
    statement may name is its version's (_check_statement)."""
    import base64
    import binascii
    if not isinstance(text, str) or not _B64U.fullmatch(text) or len(text) not in (43, 1622):
        return None
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, binascii.Error):
        return None
    return raw if base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") == text else None


def _check_statement(payload):
    """A statement's own fields: its seq from 1, its prev (the hex sha256 of the statement before, "" for the first),
    and, for a key, the key (32 bytes at version 2; at version 3 1,216 bytes that pass a post-quantum key's check), its
    id (the first 16 bytes of the key's sha256, as 32 lowercase hex) and what it retires (all, or grace). A server
    without ML-KEM refuses a version-3 key with 503, saying why: it cannot check one."""
    from . import e2e
    seq, prev = payload["seq"], payload["prev"]
    if not _int(seq) or not 0 < seq <= SEQ_STATED_MAX:
        raise link.LinkError(422, "A statement's seq is a whole number from 1.")
    if not isinstance(prev, str) or (prev != "" and not _HEX64.fullmatch(prev)) or (prev == "") != (seq == 1):
        raise link.LinkError(422, "A statement's prev is the sha256 of the statement before it, as 64 lowercase hex, "
                                  "and empty for the first.")
    if payload["purpose"] == E2E_KEY:
        raw = statement_key(payload["key"])
        hybrid = payload["v"] == STATEMENT_HYBRID
        if raw is None or len(raw) != (e2e.HYBRID_KEY if hybrid else 32):
            raise link.LinkError(422, "A version-3 statement's key is 1,216 bytes, a post-quantum key, in base64url "
                                      "without padding." if hybrid else "A version-2 statement's key is 32 bytes, in "
                                      "base64url without padding.")
        if hybrid:
            try:
                if not e2e.ready():
                    raise e2e.MLKEMMissing(e2e.MLKEM_MISSING_WORDS)
                usable = e2e.usable_hybrid_key(raw)
            except e2e.MLKEMMissing:
                raise link.LinkError(503, e2e.MLKEM_REFUSED) from None
            if not usable:
                raise link.LinkError(422, "A statement's key: " + e2e.HYBRID_KEY_WORDS)
        if payload["key_id"] != e2e.key_id(raw):
            raise link.LinkError(422, "A statement's key_id is the first 16 bytes of the sha256 of its key, as 32 "
                                      "lowercase hex characters.")
        if payload["retires"] not in RETIRES:
            raise link.LinkError(422, "A statement retires all the earlier keys, or retires them after a grace.")


def check_statement(user_id, data, purposes=STATEMENTS, now=None):
    """A statement of the account's encryption keys the owner signed ({payload, credential}), checked as a command is:
    its fields, the nonce given for its purpose, its time, and the passkey's assertion over its canonical bytes; one of
    `purposes`. (payload, canonical bytes, credential, passkey row, authenticator data): the nonce is used and the
    counter moved in the transaction that stores it (commit_statement), where a hardware key's word is required while
    high security is on."""
    now = _now(now)
    purpose = data.get("payload", {}).get("purpose") if isinstance(data, dict) and isinstance(data.get("payload"),
                                                                                                dict) else None
    if purpose not in purposes:
        raise link.LinkError(422, "The statement's purpose is " + " or ".join(purposes) + ".")
    try:
        return _verified(user_id, data, purpose, now)
    except passkeys.PasskeyError as e:
        raise link.LinkError(e.status, str(e)) from None


def check_grant(user_id, data, now=None):
    """A task's grant the owner signed ({payload, credential}; ade/task_grants.py), checked as every signed step is
    (_verified): its fields, exactly (task_grants.check_fields says each grant's own), the nonce given for task-grant,
    its time, and the passkey's assertion over its canonical bytes. (payload, canonical bytes, credential, passkey row,
    authenticator data): the nonce is used and the counter moved in the transaction that keeps it (commit_statement),
    where a hardware key's word is required while high security is on."""
    now = _now(now)
    try:
        return _verified(user_id, data, TASK_GRANT, now)
    except passkeys.PasskeyError as e:
        raise link.LinkError(e.status, str(e)) from None


def check_manager(user_id, data, now=None):
    """A holder of the Manager's role the owner signed ({payload, credential}; ade/manager_link.py), checked as every
    signed step is (_verified): its fields, exactly, the nonce given for manager, its time and life (a week at most),
    and the passkey's assertion over its canonical bytes. (payload, canonical bytes, credential, passkey row,
    authenticator data): the nonce is used and the counter moved in the transaction that keeps it (commit_statement),
    where a hardware key's word is required while high security is on. Which link it names, and the settings, are its
    caller's to check."""
    now = _now(now)
    try:
        return _verified(user_id, data, MANAGER, now)
    except passkeys.PasskeyError as e:
        raise link.LinkError(e.status, str(e)) from None


def check_instruction(user_id, data, now=None):
    """An instruction the owner signed ({payload, credential}), checked as post_command checks one before its
    transaction (_verified): for a goal to the linked session that holds the Manager's role (ade/manager_link.py),
    stored by its caller (store_signed) once the rest holds."""
    now = _now(now)
    try:
        return _verified(user_id, data, "instruction", now)
    except passkeys.PasskeyError as e:
        raise link.LinkError(e.status, str(e)) from None


def check_room(db, user_id, target, now):
    """Inside the storing transaction: the owner's commands an hour (OWNER_PER_HOUR, the moves held among them), and
    what the session `target` holds untaken (QUEUED_PER_LINK); 429 past either."""
    from . import moves
    hour = now - 3600
    if db.execute(f"SELECT COUNT(*) FROM ade_link_events WHERE user_id = ? AND {_COMMAND_SQL} AND "
                  "received_at > ?", (user_id, hour)).fetchone()[0] + moves.held_since(db, user_id, hour) \
            >= OWNER_PER_HOUR:
        raise link.LinkError(429, f"The owner sends at most {OWNER_PER_HOUR} commands an hour.")
    if queued(db, user_id, target["id"], now) + moves.held_open(db, user_id, target["id"], now) >= QUEUED_PER_LINK:
        raise link.LinkError(429, f"A session holds at most {QUEUED_PER_LINK} commands it has not taken.")


def commit_statement(db, user_id, payload, row, auth, now):
    """Inside the transaction that stores a statement checked by check_statement, or places its hold: its nonce used and
    its passkey's counter moved, a passkey that is not a hardware key refused while high security is on."""
    try:
        passkeys.use_nonce(db, payload["nonce"], user_id, payload["purpose"], payload["expires_at"], now)
        passkeys.commit_counter(db, row, auth, now)
    except passkeys.PasskeyError as e:
        raise link.LinkError(e.status, str(e)) from None


def check_uploaded(user_id, payload, credential):
    """A statement a link sends in after a restore (e2e_keys.upload): canonical, its fields and its chain fields as
    when it was made, for this deployment and account, and signed by one of the account's passkeys over its canonical
    bytes. Its time, its nonce and the passkey's counter, which bounded only its first acceptance, are not asked. Its
    canonical bytes, and the passkey row."""
    if not isinstance(payload, dict) or payload.get("purpose") not in STATEMENTS or not isinstance(credential, dict):
        raise link.LinkError(422, "A statement sent in is {payload, credential}, the payload one of the owner's "
                                  "statements of the account's keys.")
    raw = _canonical(payload, credential)
    _check_payload(payload, user_id, payload["purpose"], 0, timed=False)
    try:
        row, _ = passkeys.check_command(user_id, raw, credential, counted=False)
    except passkeys.PasskeyError as e:
        raise link.LinkError(422, f"The statement's signature does not hold: {e}.") from None
    return raw, row


def _verified(user_id, data, purpose, now):
    """(payload, its canonical bytes, credential, passkey row, authenticator data) for a signed request whose every
    check holds but the ones made in the storing transaction (the nonce's use, the counter's move)."""
    payload, credential = _signed_body(data)
    if payload.get("purpose") != purpose:
        raise link.LinkError(422, f"The payload's purpose is not {purpose}.")
    raw = _canonical(payload, credential)
    _check_payload(payload, user_id, purpose, now)
    _, nonce_expires = passkeys.open_nonce(payload["nonce"], user_id, purpose, now)
    issued = nonce_expires - passkeys.CHALLENGE_TTL
    if not issued - 2 <= payload["created_at"] <= issued + NONCE_DRIFT:
        raise link.LinkError(422, f"The payload's created_at is not the time its nonce was given (within "
                                  f"{NONCE_DRIFT} seconds after it).")
    row, auth = passkeys.check_command(user_id, raw, credential)
    return payload, raw, credential, row, auth


def _passkey_errors(fn):
    """A passkey's refusal as the channel's own (its status, its words, no-store)."""
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except passkeys.PasskeyError as e:
            raise link.LinkError(e.status, str(e)) from None
    wrapped.__name__, wrapped.__doc__ = fn.__name__, fn.__doc__
    return wrapped


# ---- the owner's side -----------------------------------------------------------------------------------------------

def nonce(user_id, purpose, now=None):
    """A nonce for one payload of `purpose`, what the page puts beside it in the payload, and the credentials whose
    signature it asks for (passkeys.signing_credentials: the hardware keys while high security is on, else any)."""
    if purpose not in PURPOSES:
        raise link.LinkError(422, "A nonce's purpose is " + ", ".join(PURPOSES) + ".")
    now = _now(now)
    return {"nonce": passkeys.new_nonce(user_id, purpose, now), "expires_at": now + passkeys.CHALLENGE_TTL,
            "rp_id": passkeys.rp_id(), "deployment": link.signing_host(), "account": user_id, "server_time": now,
            "credentials": passkeys.signing_credentials(user_id)}


def queued(db, user_id, link_id, now):
    """How many commands and moves stored for a link it has not taken and could still take."""
    return db.execute(f"SELECT COUNT(*) FROM ade_link_events WHERE user_id = ? AND {_COMMAND_SQL} AND to_id = ? AND "
                      "taken_at IS NULL AND withdrawn_at IS NULL AND expires_at > ?",
                      (user_id, link_id, now)).fetchone()[0]


def store_signed(db, user_id, purpose, payload, payload_text, signature, passkey_id, target, now, for_prompt=False):
    """A signed command stored as its event, from the owner to `target` (a live link's row), received `now`: the
    payload's canonical text and the passkey's answer kept as they came, marked to wake its session; an answer to a
    prompt (`for_prompt`) marked as one instead, waking nobody, since its hook claims it and never the model. Its seq;
    sqlite3.IntegrityError when a command with its id is stored already. A move is stored here when its hold ends
    (ade/moves.py)."""
    text = MOVE_TEXT.format(payload["session"]) if purpose == MOVE else payload["text"]
    return db.execute(
        "INSERT INTO ade_link_events (user_id, link_id, client_id, kind, to_name, ref, text, needs_turn, "
        "received_at, payload, signature, passkey_id, to_id, expires_at, prompt_answer) VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (user_id, link.OWNER, payload["id"], purpose, target["name"], payload.get("ref"), text, int(not for_prompt),
         now, payload_text, signature, passkey_id, target["id"], payload["expires_at"], int(for_prompt))).lastrowid


def _prompt_answered(db, user_id, payload, target, prompt, now):
    """Inside post_command's transaction, the checks an answer to a prompt takes:
    the prompt open and posted by the session the answer names (409); not an item to look at (409); the answer's
    digest that of the prompt's text as stored (409); the answer in one of its forms (422, prompt_answer_form); and
    the owner's answers to prompts in the last hour fewer than PROMPT_ANSWERS_PER_HOUR (429), a budget of their own."""
    if prompt["link_id"] != target["id"] or not link.item_open(prompt, now):
        raise link.LinkError(409, "The ref is not an open prompt that session posted.")
    if prompt["look"]:
        raise link.LinkError(409, "An item to look at is answered where the session shows it.")
    if payload["digest"] != text_digest(prompt["text"]):
        raise link.LinkError(409, "The answer's digest is not that of the prompt's text as stored.")
    prompt_answer_form(payload["text"])
    if db.execute("SELECT COUNT(*) FROM ade_link_events WHERE user_id = ? AND link_id = ? AND kind = 'answer' AND "
                  "prompt_answer = 1 AND received_at > ?", (user_id, link.OWNER, now - 3600)).fetchone()[0] \
            >= PROMPT_ANSWERS_PER_HOUR:
        raise link.LinkError(429, f"The owner answers at most {PROMPT_ANSWERS_PER_HOUR} prompts an hour.")


def _proposal_signed(db, user_id, payload, purpose, now):
    """Inside post_command's transaction: the linked session's proposal (an event of kind proposal) whose client_id is
    the signed payload's id, which the owner signs now as their own instruction, or None. It must be open (409), be
    signed as an instruction (409), to the session it names (409), and its first line must name who proposed it,
    exactly "Proposed by NAME:", with words after it (409): the signed payload has no field for that, so the words
    do."""
    rows = db.execute("SELECT * FROM ade_link_events WHERE user_id = ? AND kind = 'proposal' AND client_id = ? "
                      "ORDER BY seq", (user_id, payload["id"])).fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        raise link.LinkError(409, "More than one proposal has this id: sign a new instruction instead.")
    proposal = rows[0]
    if not link.item_open(proposal, now):
        raise link.LinkError(409, "That proposal is closed or past its deadline: sign a new instruction instead.")
    if purpose != "instruction":
        raise link.LinkError(409, "A proposal is signed as an instruction.")
    if payload["to"] != proposal["for_id"]:
        raise link.LinkError(409, "This proposal is for another session than the one this instruction names.")
    first = f"Proposed by {link._names(db, user_id).get(proposal['link_id'], '?')}:\n"
    if not payload["text"].startswith(first) or not payload["text"][len(first):].strip():
        raise link.LinkError(409, "A proposal's instruction begins with the line Proposed by and its proposer's name "
                                  "and a colon, exactly, and its words follow on the lines after it.")
    return proposal


@_passkey_errors
def post_command(user_id, data, now=None):
    """Store an instruction or an answer the owner signed: {seq, expires_at}. A move the owner signed is held instead
    (ade/moves.py): {held: its hold, with told, how many devices its notice went to; expires_at}, and it is stored as
    its event, with its row in ade_link_moves, only when the hold ends. A move is of a session the addressed link's
    History list has, not withheld, and of which no other move, to any link, is held or under way (409 otherwise:
    moves.check_free), for an account the hosted workspace is on for and whose work lives there, as it chose (403
    otherwise, in the words a start is refused with: moves.check_free, and again as its hold ends), and lives its hold
    and moves.HOLD_MARGIN seconds more (422 otherwise). The moves held count in their link's commands not taken, as the stored ones do, until their
    holds end; in the owner's commands an hour a stored move counts as its event, and one not stored for the hour after
    it was signed, held, cancelled or failed, as a withdrawn command still counts. So holding many gets past neither,
    and cancelling gives nothing back. A move of a session whose row is sealed (end-to-end encryption, ade/e2e.py) may
    carry the session's `title` beside the payload, outside what is signed: the page's opened title, in the clear,
    which names the move and the workspace session; at most 120 characters, held to what a title in the clear is."""
    from . import holds, moves
    now = _now(now)
    purpose = data.get("payload", {}).get("purpose") if isinstance(data, dict) and isinstance(data.get("payload"),
                                                                                                dict) else None
    if purpose not in SIGNED_KINDS:
        raise link.LinkError(422, "A command's purpose is instruction, answer or move.")
    payload, raw, credential, row, auth = _verified(user_id, data, purpose, now)
    given_title = None
    if purpose == MOVE:
        moves.check_life(payload["expires_at"], now)
        moves.check_workspace(user_id)
        if data.get("title") is not None:
            given_title = link._shown(link._text(data["title"], "session's title", link.HISTORY_TITLE),
                                      "session's title")
    held = None
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        target = db.execute("SELECT * FROM ade_links WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
                            (payload["to"], user_id)).fetchone()
        if target is None:
            raise link.LinkError(422, "The payload's to is not one of this account's live links.")
        listed = None
        if purpose == MOVE:
            # the server's rows decide, as for a mirrored note: the link lists the session, and not withheld
            listed = db.execute("SELECT title, withheld, sealed FROM ade_link_history WHERE link_id = ? AND "
                                "user_id = ? AND session_id = ?",
                                (target["id"], user_id, payload["session"])).fetchone()
            if listed is None or listed["withheld"]:
                raise link.LinkError(409, "A move is of a session its link's History list has, not withheld; that "
                                          "list " + ("has this one withheld." if listed else "does not have it."))
            moves.check_free(db, user_id, payload["session"], now)
        link.close_due(db, user_id, now)                # a prompt or a proposal past its deadline is closed by it
        question = prompt = None
        if purpose == "answer":
            question = db.execute("SELECT * FROM ade_link_events WHERE seq = ? AND user_id = ?",
                                  (payload["ref"], user_id)).fetchone()
            if question is not None and question["kind"] == "prompt":
                # an answer to a session's own prompt: its own checks and budget, and closed for good once stored
                prompt, question = question, None
                _prompt_answered(db, user_id, payload, target, prompt, now)
            elif question is None or question["kind"] != "question" or question["link_id"] != target["id"] \
                    or question["to_name"] not in (link.OWNER, "all") or not link.question_open(db, question, now):
                raise link.LinkError(409, "The ref is not an open question that session asked the owner.")
            # a question the server sealed in place as encryption was turned on is answered with the digest of the
            # words its session asked in the clear, its own record of them, which the server no longer holds: its
            # session checks that digest (ade_link.py), the server cannot; one of the sealed text never matches it
            elif question["sealed_in_place"]:
                if payload["digest"] == text_digest(question["text"]):
                    raise link.LinkError(409, SEALED_IN_PLACE)
            elif payload["digest"] != text_digest(question["text"]):
                raise link.LinkError(409, "The answer's digest is not that of the question's text as stored.")
        # a payload whose id is a linked session's proposal is that proposal, the owner's instruction now
        session_proposal = _proposal_signed(db, user_id, payload, purpose, now)
        # a payload whose id is a connected app's proposal is that proposal, signed now (ade/proposals.py)
        from . import proposals
        proposal = proposals.bind_signed(db, user_id, payload, purpose, now)
        if prompt is None:
            # the owner's commands, and what a session holds untaken: an answer to a prompt is in neither
            check_room(db, user_id, target, now)
        passkeys.use_nonce(db, payload["nonce"], user_id, purpose, payload["expires_at"], now)
        passkeys.commit_counter(db, row, auth, now)
        signature = json.dumps(credential, separators=(",", ":"))
        if purpose == MOVE:
            # a sealed row's title is inside its envelope (kept as ""): the page's opened one names the move, if given
            held = moves.hold(db, user_id, target, given_title if listed["sealed"] else listed["title"], payload,
                              raw.decode("utf-8"), signature, row["id"], now)
        else:
            try:
                seq = store_signed(db, user_id, purpose, payload, raw.decode("utf-8"), signature, row["id"], target,
                                   now, for_prompt=prompt is not None)
            except sqlite3.IntegrityError:
                raise link.LinkError(409, "A command with this id is stored already.") from None
            if question is not None:                    # open, as checked above under this transaction
                db.execute("UPDATE ade_link_events SET closed_by = ?, closed_at = ? WHERE seq = ?",
                           (seq, now, question["seq"]))
            if prompt is not None:
                # closed for good: an answer withdrawn or expired untaken never opens it again
                db.execute("UPDATE ade_link_events SET closed_by = ?, closed_at = ?, closed_reason = 'answer' WHERE "
                           "seq = ?", (seq, now, prompt["seq"]))
            if session_proposal is not None:
                db.execute("UPDATE ade_link_events SET closed_by = ?, closed_at = ?, closed_reason = 'signed' WHERE "
                           "seq = ?", (seq, now, session_proposal["seq"]))
            if proposal is not None:
                proposals.signed(db, proposal, seq, payload.get("text"), now)
    if held is not None:                                # the notice, once the hold is committed
        return {"held": dict(held, told=holds.tell(held, now)), "expires_at": payload["expires_at"]}
    return {"seq": seq, "expires_at": payload["expires_at"]}


def withdraw(user_id, seq, now=None):
    """Withdraw a command or a move no session has taken yet: {withdrawn_at}; a move's row is failed, saying the owner
    withdrew it (ade/moves.py). 409 with its state otherwise."""
    from . import moves
    now = _now(now)
    if not 0 < seq <= SEQ_MAX:
        raise link.LinkError(404, "No such command.")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        done = db.execute(f"UPDATE ade_link_events SET withdrawn_at = ? WHERE seq = ? AND user_id = ? AND "
                          f"{_SIGNED_SQL} AND taken_at IS NULL AND withdrawn_at IS NULL AND expires_at > ?",
                          (now, seq, user_id, now)).rowcount
        row = db.execute("SELECT * FROM ade_link_events WHERE seq = ? AND user_id = ?", (seq, user_id)).fetchone()
        if done and row["kind"] == MOVE:
            moves.owner_withdrew(db, seq, now)
        if done and row["kind"] == "instruction":
            # a goal sent to a linked Manager: its request goes with it (controller.withdraw_goal_of)
            from . import controller
            controller.withdraw_goal_of(db, user_id, seq, now)
    if done:
        return {"withdrawn_at": now}
    if row is None or row["link_id"] != link.OWNER or row["kind"] not in SIGNED_KINDS:
        raise link.LinkError(404, "No such command.")
    raise link.LinkError(409, f"Only a queued command can be withdrawn; this one is {command_state(row, now)}.",
                         state=command_state(row, now))


def stop(user_id, link_id, now=None):
    """Stop a session: its next tool calls are held until a resume the owner signs (its hook and watcher hold it, not
    this server; what it started before keeps running). A second stop keeps the first time. {stopped_at}."""
    now = _now(now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        return stop_in(db, user_id, link_id, now)


def stop_in(db, user_id, link_id, now):
    """stop, inside the caller's transaction (a stop a connected app proposed is accepted so, ade/proposals.py)."""
    row = db.execute("SELECT stopped_at FROM ade_links WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
                     (link_id, user_id)).fetchone()
    if row is None:
        raise link.LinkError(404, "No such link.")
    if row["stopped_at"] is not None:
        return {"stopped_at": row["stopped_at"]}
    db.execute("UPDATE ade_links SET stopped_at = ?, stop_acked_at = NULL, resume_payload = NULL, "
               "resume_signature = NULL, resume_passkey_id = NULL WHERE id = ?", (now, link_id))
    return {"stopped_at": now}


@_passkey_errors
def resume(user_id, link_id, data, now=None):
    """Lift a stop with a resume the owner signed after it; the assertion is kept, for the session's hook and watcher
    to verify themselves before they lift what they hold. {resumed_at}."""
    now = _now(now)
    payload, raw, credential, row, auth = _verified(user_id, data, "resume", now)
    if payload["to"] != link_id:
        raise link.LinkError(422, "The resume names another link.")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        held = db.execute("SELECT stopped_at FROM ade_links WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
                          (link_id, user_id)).fetchone()
        if held is None:
            raise link.LinkError(404, "No such link.")
        if held["stopped_at"] is None:
            raise link.LinkError(409, "This session is not stopped.")
        if payload["created_at"] <= held["stopped_at"]:
            raise link.LinkError(409, "This resume was made before the stop it would lift; sign a new one.",
                                 stopped_at=held["stopped_at"])
        # a payload whose id is a connected app's proposal is that proposal, signed now (ade/proposals.py)
        from . import proposals
        proposal = proposals.bind_signed(db, user_id, payload, "resume", now)
        passkeys.use_nonce(db, payload["nonce"], user_id, "resume", payload["expires_at"], now)
        passkeys.commit_counter(db, row, auth, now)
        db.execute("UPDATE ade_links SET stopped_at = NULL, stop_acked_at = NULL, resume_payload = ?, "
                   "resume_signature = ?, resume_passkey_id = ?, resumed_at = ? WHERE id = ?",
                   (raw.decode("utf-8"), json.dumps(credential, separators=(",", ":")), row["id"], now, link_id))
        if proposal is not None:
            proposals.signed(db, proposal, None, None, now)
    return {"resumed_at": now}


# ---- a session's side -----------------------------------------------------------------------------------------------

def owner_keys(link_row):
    """The account's passkeys, for a session to pin against what its owner holds outside the server."""
    return {"account": link_row["user_id"], "passkeys": passkeys.owner_keys(link_row["user_id"])}


def take(link_row, data, now=None):
    """Claim a command or a move addressed to this session: the first take wins and posts a `take` event carrying the
    command's digest (a move's row is taken with it); the same instance asking again gets the same time; anything
    else is 409 with why."""
    from . import moves
    now = _now(now)
    seq = data.get("seq") if isinstance(data, dict) else None
    instance = data.get("instance") if isinstance(data, dict) else None
    if not _int(seq) or not 0 < seq <= SEQ_MAX:
        raise link.LinkError(422, "A take is {seq, instance}.")
    if not isinstance(instance, str) or not 0 < len(instance) <= MAX_INSTANCE:
        raise link.LinkError(422, f"A take's instance is text of at most {MAX_INSTANCE} characters.")
    refusal = None
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        held = db.execute("SELECT stopped_at FROM ade_links WHERE id = ?", (link_row["id"],)).fetchone()
        if held["stopped_at"] is not None:
            raise link.LinkError(409, "This session is stopped: nothing is taken until the owner resumes it.",
                                 why="stopped", stopped_at=held["stopped_at"])
        done = db.execute(f"UPDATE ade_link_events SET taken_at = ?, taken_by = ?, taken_instance = ? WHERE seq = ? "
                          f"AND user_id = ? AND {_COMMAND_SQL} AND to_id = ? AND taken_at IS NULL AND "
                          "withdrawn_at IS NULL AND expires_at > ?",
                          (now, link_row["id"], instance, seq, link_row["user_id"], link_row["id"], now)).rowcount
        row = db.execute("SELECT * FROM ade_link_events WHERE seq = ? AND user_id = ?",
                         (seq, link_row["user_id"])).fetchone()
        if done:
            db.execute("INSERT INTO ade_link_events (user_id, link_id, client_id, kind, to_name, ref, text, "
                       "needs_turn, received_at) VALUES (?,?,?,?,?,?,?,?,?)",
                       (link_row["user_id"], link_row["id"], f"take:{seq}", "take", link.OWNER, seq,
                        text_digest(row["payload"]), 0, now))
            if row["kind"] == MOVE:
                moves.taken(db, seq, now)
            return {"seq": seq, "taken_at": now}
        if row is None or row["link_id"] != link.OWNER or row["kind"] not in SIGNED_KINDS \
                or row["to_id"] != link_row["id"]:
            raise link.LinkError(404, "No command to this session has that seq.")
        if row["prompt_answer"]:
            # the owner's answer to one of this session's prompts: its hook claims it (claim_prompt), and the model
            # never acts on it, whatever a text it read asked
            raise link.LinkError(409, "An answer to a prompt is the prompt hook's to claim, never take's.",
                                 why="prompt")
        if row["taken_by"] == link_row["id"] and row["taken_instance"] == instance:
            sent = row["kind"] == MOVE and moves.sent(db, seq)
            if row["closed_by"] is None and row["renewed_at"] is None and row["retried_at"] is None and not sent:
                # the same copy asking again before it said anything of the command: its answer was lost on the way.
                # Once: a copy that asks again after that has lost its record of the take, or was restored in place
                db.execute("UPDATE ade_link_events SET retried_at = ? WHERE seq = ?", (now, seq))
                return {"seq": seq, "taken_at": row["taken_at"], "retry": True}
            # it answered the command, said it was working on it, sent the move or said why it did not, or was given it
            # again once already: it had the take, and a copy that asks again is not given the command another time
            said = ("answered it." if row["closed_by"] is not None else "said it is working on it."
                    if row["renewed_at"] is not None else "sent the move, or said why it did not." if sent
                    else "was given it again once already.")
            raise link.LinkError(409, "Not taken again: this session had the command and " + said,
                                 why="answered" if row["closed_by"] is not None else "taken", taken_at=row["taken_at"],
                                 taken_by=row["taken_by"], withdrawn_at=row["withdrawn_at"],
                                 expires_at=row["expires_at"])
        if row["taken_by"] == link_row["id"]:
            # this session's key, from another instance: two copies of one link, which the owner is told of
            db.execute("UPDATE ade_link_events SET conflict_at = ?, conflict_instance = ? WHERE seq = ?",
                       (now, instance, seq))
        refusal = {"why": "withdrawn" if row["withdrawn_at"] is not None else "taken" if row["taken_at"] is not None
                   else "expired", "taken_at": row["taken_at"], "taken_by": row["taken_by"],
                   "withdrawn_at": row["withdrawn_at"], "expires_at": row["expires_at"]}
    raise link.LinkError(409, f"Not taken: the command is {refusal['why']}.", **refusal)


def claim_prompt(link_row, seq, data, now=None):
    """The prompt hook's claim of the owner's answer to one of its session's prompts, with
    take's checks: the first claim of the answer wins and posts a take event carrying the payload's
    digest, as take does; the same instance asking again gets the same answer; anything else is 409 with why: stopped
    (the link is stopped: nothing is claimed), open (not answered yet), closed (closed without an answer: by the
    owner, the session's report or the deadline, with closed_reason), taken, withdrawn, expired, or deadline (the
    prompt's deadline passed before the claim). So the record shows whether the link claimed the answer before the
    prompt's deadline. 404 unless `seq` is a prompt of this account this link posted. {seq: the answer's, payload,
    signature, passkey_id, taken_at}: the hook verifies the signature itself against the owner's keys it pinned."""
    now = _now(now)
    instance = data.get("instance") if isinstance(data, dict) else None
    if not isinstance(instance, str) or not 0 < len(instance) <= MAX_INSTANCE:
        raise link.LinkError(422, f"A claim is {{instance}}, text of at most {MAX_INSTANCE} characters.")
    if not _int(seq) or not 0 < seq <= SEQ_MAX:
        raise link.LinkError(404, "No prompt of this session has that seq.")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        link.close_due(db, link_row["user_id"], now)
        prompt = db.execute("SELECT * FROM ade_link_events WHERE seq = ? AND user_id = ?",
                            (seq, link_row["user_id"])).fetchone()
        if prompt is None or prompt["kind"] != "prompt" or prompt["link_id"] != link_row["id"]:
            raise link.LinkError(404, "No prompt of this session has that seq.")
        held = db.execute("SELECT stopped_at FROM ade_links WHERE id = ?", (link_row["id"],)).fetchone()
        if held["stopped_at"] is not None:
            raise link.LinkError(409, "This session is stopped: nothing is claimed until the owner resumes it.",
                                 why="stopped", stopped_at=held["stopped_at"])
        if prompt["closed_reason"] is None:
            raise link.LinkError(409, "The prompt is open: the owner has not answered it.", why="open",
                                 deadline_at=prompt["deadline_at"])
        answer = db.execute("SELECT * FROM ade_link_events WHERE seq = ? AND user_id = ? AND kind = 'answer' AND "
                            "prompt_answer = 1", (prompt["closed_by"], link_row["user_id"])).fetchone() \
            if prompt["closed_reason"] == "answer" else None
        if answer is None:
            raise link.LinkError(409, "The prompt was closed without an answer.", why="closed",
                                 closed_reason=prompt["closed_reason"])
        got = {"seq": answer["seq"], "payload": answer["payload"], "signature": json.loads(answer["signature"]),
               "passkey_id": answer["passkey_id"]}
        if answer["taken_at"] is not None:
            if answer["taken_by"] == link_row["id"] and answer["taken_instance"] == instance:
                return dict(got, taken_at=answer["taken_at"])
            why = "taken"
        elif answer["withdrawn_at"] is not None:
            why = "withdrawn"
        elif answer["expires_at"] <= now:
            why = "expired"
        elif now >= prompt["deadline_at"]:
            why = "deadline"
        else:
            db.execute("UPDATE ade_link_events SET taken_at = ?, taken_by = ?, taken_instance = ? WHERE seq = ? AND "
                       "taken_at IS NULL", (now, link_row["id"], instance, answer["seq"]))
            db.execute("INSERT INTO ade_link_events (user_id, link_id, client_id, kind, to_name, ref, text, "
                       "needs_turn, received_at) VALUES (?,?,?,?,?,?,?,?,?)",
                       (link_row["user_id"], link_row["id"], f"take:{answer['seq']}", "take", link.OWNER,
                        answer["seq"], text_digest(answer["payload"]), 0, now))
            return dict(got, taken_at=now)
    raise link.LinkError(409, f"Not claimed: the answer is {why}.", why=why, taken_at=answer["taken_at"],
                         withdrawn_at=answer["withdrawn_at"], expires_at=answer["expires_at"],
                         deadline_at=prompt["deadline_at"])


def state(link_row, now=None):
    """What a session's hook and watcher read: whether it is stopped, the stored resume to verify, the account's
    newest statement of its encryption keys ({seq, key_id}, the key_id null once encryption is off; or null), and the
    link's kind and interval (a client paired before pair recorded the kind asks it here once, which moves nothing a
    watcher's heartbeat does)."""
    from . import e2e_keys
    now = _now(now)
    with store.connect() as db:
        row = db.execute("SELECT stopped_at, resume_payload, resume_signature, resume_passkey_id, resumed_at FROM "
                         "ade_links WHERE id = ?", (link_row["id"],)).fetchone()
        head = link._head(db, link_row["user_id"])
        stated = e2e_keys.said(e2e_keys.newest(db, link_row["user_id"]))
    resume_held = None
    if row["resume_payload"]:
        resume_held = {"payload": row["resume_payload"], "signature": json.loads(row["resume_signature"]),
                       "passkey_id": row["resume_passkey_id"], "resumed_at": row["resumed_at"]}
    return {"link_id": link_row["id"], "name": link_row["name"], "stopped": row["stopped_at"] is not None,
            "stopped_at": row["stopped_at"], "resume": resume_held, "roster_version": None, "head": head,
            "server_time": now, "e2e": stated, "kind": link_row["kind"], "interval_min": link_row["interval_min"]}


def event(link_row, seq):
    """One event for a session: addressed to it, to all, or posted by it, with a command's payload, signature and
    state; 404 for anything else."""
    if not 0 < seq <= SEQ_MAX:
        raise link.LinkError(404, "No such event.")
    with store.connect() as db:
        # the prompt hook polls its prompt here: one past its deadline reads as closed by it
        link.close_due(db, link_row["user_id"], _now(None))
        row = db.execute("SELECT * FROM ade_link_events WHERE seq = ? AND user_id = ?",
                         (seq, link_row["user_id"])).fetchone()
        names = link._names(db, link_row["user_id"])
    if row is None:
        raise link.LinkError(404, "No such event.")
    mine = row["link_id"] == link_row["id"]
    to_me = row["to_id"] == link_row["id"] if row["to_id"] is not None else row["to_name"] in (link_row["name"], "all")
    if not mine and not to_me:
        raise link.LinkError(404, "No such event.")
    view = link._view(row, names)
    view.update({"payload": row["payload"], "signature": json.loads(row["signature"]) if row["signature"] else None,
                 "passkey_id": row["passkey_id"], "client_id": row["client_id"], "taken_by": row["taken_by"]})
    return view


# ---- what the owner is told -----------------------------------------------------------------------------------------

def alerts(user_id, now=None):
    """The notifier's items for commands, each (key, the kind of its notice: push.words): one not taken in time (a move
    too), an instruction taken and silent, a take from a second instance of one link, and a stop a local session has
    not acknowledged. Each is told as a linked session that needs a look, never by its link's name or its seq."""
    now = _now(now)
    with store.connect() as db:
        links = {r["id"]: r for r in db.execute("SELECT id, kind, stopped_at, stop_acked_at FROM ade_links "
                                                "WHERE user_id = ? AND revoked_at IS NULL", (user_id,))}
        # a taken command's silence is told of until a day after its life ended, then no more: the rows read stay
        # those of the last day's commands, not every one ever taken
        rows = db.execute(f"SELECT * FROM ade_link_events WHERE user_id = ? AND {_COMMAND_SQL} AND closed_by IS NULL "
                          "AND withdrawn_at IS NULL AND expires_at > (CASE WHEN taken_at IS NULL THEN ? ELSE ? END) "
                          "ORDER BY seq", (user_id, now, now - TAKEN_SHOWN)).fetchall()
        # a take from a second copy, answered, withdrawn or expired since: a copy that acts is the owner's to know of
        conflicts = db.execute(f"SELECT seq, to_id FROM ade_link_events WHERE user_id = ? AND {_COMMAND_SQL} AND "
                               "conflict_at IS NOT NULL AND conflict_at > ? ORDER BY seq",
                               (user_id, now - CONFLICT_SHOWN)).fetchall()
    items = []
    for c in rows:
        target = links.get(c["to_id"])
        if target is None:
            continue
        if c["taken_at"] is None:
            if now - c["received_at"] >= UNTAKEN_AFTER.get(target["kind"], max(UNTAKEN_AFTER.values())):
                items.append((f"link-untaken:{c['seq']}", "link-look"))
        elif c["kind"] == "instruction" and now - max(c["taken_at"], c["renewed_at"] or 0) >= STALE_AFTER:
            items.append((f"link-stale:{c['seq']}", "link-look"))
    items += [(f"link-take-conflict:{c['seq']}", "link-look") for c in conflicts if c["to_id"] in links]
    items += [(f"link-stop:{r['id']}", "link-look") for r in links.values()
              if r["kind"] == "local" and r["stopped_at"] is not None and r["stop_acked_at"] is None
              and now - r["stopped_at"] >= STOP_ACK_AFTER]
    return items
