"""Secret and personal-data rules, and the one function that makes text from a pull request safe to copy.

A stand-in for a fuller catalogue, which will move into proofpane-core. Two things differ from a plain
regular-expression pass, and both come from leaks a review found:

- Each rule bounds its token by the characters the token is made of, not by `\\b`. `_` is a word character, so `\\b`
  never fires inside `_AKIA..._`, and markdown puts underscores against tokens.
- Text is examined in four forms: as written; with inline markdown removed, repeatedly until nothing more comes off;
  with every `*`, backtick, `~~` and HTML tag removed, since a renderer can join what those separate (`AKIA*IOSF…*`
  renders as one key); and with both removals, one after the other. Removing markup can also glue a neighbouring
  letter onto a token, so a token may show in one form and not another.

`safe_copy` is the only way text from a pull request reaches a manifest, a log or a summary. If a secret rule matches
the text, or any text it was taken from, in any form, the text is withheld whole: a partly redacted secret can still
leak through a boundary the rules did not foresee. Otherwise every occurrence of personal data is replaced by its
rule's name, and the result is scanned again, for secrets and personal data, in every form: if anything matches, the
text is withheld whole. A claim taken from a title or description in which a secret rule matches anywhere is
withheld too (`verify.py`): the stretch it came from may hold part of a key whose header is elsewhere.

What the rules see is what they catch: an unknown key format, a base64 key body on a line without its header, or a
phone number written without a `+` country code is not detected. Nor is a token written so as to hide it, which a
renderer would still show: with an HTML character reference (`&#73;`), an invisible character inside it, or in an
encoding. The rules are for secrets pasted by accident, not for an author hiding one from them.

Each rule's boundary is its token's own alphabet: an AWS key is upper case and digits, so a lower-case letter next to
it does not hide it; the Google key has a fixed length, so what follows it does not either. A token whose alphabet
has both cases (GitHub, Slack, Stripe, Anthropic, OpenAI, Google) is not recognised when a letter or digit is glued
to its front, since it cannot then be told from part of a longer word.
"""

import hashlib
import re

from .extract import markdown_lines, strip_inline

# token rules: a token starts where no letter or digit precedes it (for a token whose alphabet has only upper-case
# letters and digits, where neither precedes it); `_` and `-` may precede one even when its body uses them, because
# markdown and prose put them there
SECRET_RULES = {
    "aws-access-key": r"(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])",
    "github-token": r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{36,}|(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{50,}",
    "slack-token": r"(?<![A-Za-z0-9])xox[abprs]-[0-9A-Za-z-]{10,}",
    "stripe-live-key": r"(?<![A-Za-z0-9])[rs]k_live_[0-9A-Za-z]{20,}",
    "google-api-key": r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{35}",
    "anthropic-key": r"(?<![A-Za-z0-9])sk-ant-[A-Za-z0-9_-]{20,}",
    "openai-key": r"(?<![A-Za-z0-9])sk-(?!ant-)[A-Za-z0-9_-]{32,}",
    "private-key-block": r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----",
}
# quantifiers are bounded so that a long run of near-misses costs linear time
PII_RULES = {
    # any address a person may have: letters in any script, and the local-part characters RFC 5322 allows
    "email": r"[\w.!#$%&'*+/=?^`{|}~-]{1,64}@(?:[\w-]{1,63}\.){1,8}[^\W\d_]{2,24}(?![^\W_])",
    # a run of digit groups, each separated from the next by one space, dot or dash, with no letter or digit against
    # either end (a run inside a hex digest or a base64 string is not a number); a card is a stretch of it written as
    # cards are, beginning as an issuer's numbers do and passing the Luhn check (`_card_spans`), so one followed by
    # its code or expiry date is still found
    "card-number": r"(?<![^\W_])[0-9]+(?:[ .-][0-9]+)*(?![^\W_])",
    "us-ssn": r"(?<![0-9])(?!000|666|9)[0-9]{3}([- ])(?!00)[0-9]{2}\1(?!0000)[0-9]{4}(?![0-9])",
    # after anything but a letter, a digit or another "+": `_` included, which markdown and identifiers put there
    "phone": r"(?<![^\W_])(?<!\+)\+[1-9](?:[ .\-/()]{0,3}[0-9]){7,14}(?![0-9])",
}
_SECRET_RX = {k: re.compile(v) for k, v in SECRET_RULES.items()}
_PII_RX = {k: re.compile(v) for k, v in PII_RULES.items()}

# a source showing more distinct values than this has its text withheld rather than searched value by value, which
# would cost time in proportion to their number times the text's length
MAX_SOURCE_VALUES = 64

WITHHELD_SECRET = "[withheld: a secret rule matched this text]"
WITHHELD_PII = "[withheld: personal data this text could not be redacted from]"

# reserved for documentation and testing (RFC 2606, RFC 6761), and addresses that reach no mailbox: no-reply local
# parts, and GitHub's no-reply domain, whose addresses name a GitHub account but disclose no address for it
_RESERVED_DOMAINS = ("example.com", "example.org", "example.net", "users.noreply.github.com")
_RESERVED_SUFFIXES = (".example", ".test", ".invalid", ".localhost")
_NO_REPLY = {"noreply", "no-reply", "donotreply", "do-not-reply"}


def placeholder_email(address):
    """An address that reaches no mailbox: a reserved domain or one under it, or a no-reply local part."""
    local, _, domain = address.rpartition("@")
    domain = domain.lower()
    if local.lower() in _NO_REPLY:
        return True
    if any(domain == d or domain.endswith("." + d) for d in _RESERVED_DOMAINS):
        return True
    return domain.endswith(_RESERVED_SUFFIXES) or domain in {s[1:] for s in _RESERVED_SUFFIXES}


def _remote(text, m):
    """Whether an address-shaped match is the user part of a git remote rather than an address: `git@host:path`, the
    form ssh takes, with no space after the colon; or `//git@host`, a URL's user, which the local part's `/` lets the
    match start at."""
    after = text[m.end():m.end() + 2]
    return (len(after) == 2 and after[0] == ":" and not after[1].isspace()) or \
        (m.group(0).startswith("//") and text[m.start() - 1:m.start()] == ":")


def luhn(digits):
    s, alt = 0, False
    for d in reversed(digits):
        n = int(d)
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        s += n
        alt = not alt
    return s % 10 == 0


_GROUP = re.compile(r"[0-9]+")
# the first digits of the card networks' numbers: 3 (American Express, Diners Club, JCB), 4 (Visa), 5 (Mastercard,
# Maestro), 6 (Discover, UnionPay, Maestro), and 22 to 27 (Mir, Mastercard's 2-series); not 1, 7, 8, 9, 20 or 21, so
# a year, a millisecond timestamp or most identifiers do not start one
_ISSUER = re.compile(r"[3-6]|2[2-7]")


def _card(digits):
    return _ISSUER.match(digits) is not None and luhn(digits)


def _card_spans(run, offset):
    """(start, end) of every card number in one run of digit groups, overlapping ones merged into one: every stretch
    of whole groups that is written as a card is and could be one. Written as a card is: one unbroken group of 13 to
    19 digits that no dot joins to another group (that is a decimal number), or a group of 4 followed by groups of 4
    to 6 with one kind of separator (4-4-4-4, 4-6-5, 4-6-4), 13 to 19 digits in all. Could be one: it begins as an
    issuer's number does (`_ISSUER`) and passes the Luhn check. Every such stretch, not only the first: a stretch
    that passes by chance can overlap a real card, and redacting only it would leave the card's other digits. One
    random stretch in ten passes the Luhn check, so numeric data written in groups of four can still look like a
    card; the rates are in the README. Linear in the run: a stretch has at most four groups."""
    groups = [(g.start(), g.end(), g.group(0)) for g in _GROUP.finditer(run)]
    spans = []
    for i, (a, b, first) in enumerate(groups):
        if 13 <= len(first) <= 19 and "." not in run[max(a - 1, 0):a] + run[b:b + 1] and _card(first):
            spans.append((a, b))
        if len(first) == 4:
            digits, sep = first, None
            for j in range(i + 1, len(groups)):
                gap = run[groups[j - 1][1]:groups[j][0]]
                if not 4 <= len(groups[j][2]) <= 6 or sep not in (None, gap):
                    break
                sep, digits = gap, digits + groups[j][2]
                if len(digits) > 19:
                    break
                if len(digits) >= 13 and _card(digits):
                    spans.append((a, groups[j][1]))
    merged = []
    for a, b in sorted(spans):
        if merged and a < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return [(offset + a, offset + b) for a, b in merged]


def _pii_spans(rule, text):
    """(start, end) of every value `rule` accepts in `text`, in order, none overlapping."""
    rx = _PII_RX[rule]
    if rule == "card-number":
        return [span for m in rx.finditer(text) for span in _card_spans(m.group(0), m.start())]
    if rule == "email":
        return [m.span() for m in rx.finditer(text) if not placeholder_email(m.group(0)) and not _remote(text, m)]
    return [m.span() for m in rx.finditer(text)]


_AGGRESSIVE = re.compile(r"[*`]|~~|<[^<>\n]{0,200}>")


def _stripped(text):
    """Inline markdown removed until nothing more comes off. One pass is not enough: removing a `_` can leave two `*`
    together, which the next pass removes (`AKIA*_*IOSF…`). Each pass that changes the text shortens it, so this
    ends; in measurement no text needed more than two passes (300,000 random strings of up to 30 characters over
    letters, spaces and the three markup characters, and every such string of up to 8)."""
    while True:
        after = strip_inline(text)
        if after == text:
            return text
        text = after


def unmark(text):
    """`text` with markup removed in both ways `forms` removes it, as one string: for fingerprints that must not
    depend on how a match was written."""
    return _AGGRESSIVE.sub("", _stripped(text))


def forms(text):
    """The text as written; with inline markdown removed until stable; with every `*`, backtick, `~~` and HTML tag
    removed; and with both, which is `unmark(text)`. Each distinct form once."""
    stripped = _stripped(text)
    out = [text]
    for form in (stripped, _AGGRESSIVE.sub("", text), _AGGRESSIVE.sub("", stripped)):
        if form not in out:
            out.append(form)
    return tuple(out)


def secret_matches(text):
    """[(rule, matched text, the form it was found in, its offset in that form)] over every form."""
    out = []
    for form in forms(text):
        for rule, rx in _SECRET_RX.items():
            for m in rx.finditer(form):
                out.append((rule, m.group(0), form, m.start()))
    return out


def pii_matches(text):
    """[(rule, matched text)] over every form, after each rule's own check (placeholder addresses, Luhn)."""
    out = []
    for form in forms(text):
        for rule in _PII_RX:
            out += [(rule, form[a:b]) for a, b in _pii_spans(rule, form)]
    return out


def has_secret(text):
    """Whether a secret rule matches any line of `text`, in any form: what the scan of a title or description sees."""
    return any(secret_matches(line) for line in markdown_lines(text))


def _redact(text, replaced):
    """Every occurrence of personal data in `text` as written replaced by its rule's name, one rule after another."""
    for rule in _PII_RX:
        spans = _pii_spans(rule, text)
        if not spans:
            continue
        parts, last = [], 0
        for a, b in spans:
            parts.append(text[last:a])
            parts.append(f"[redacted:{rule}]")
            replaced.add(text[a:b])
            last = b
        parts.append(text[last:])
        text = "".join(parts)
    return text


def safe_copy(text, sources=()):
    """`text`, fit to copy out of the pull request. `sources` are what it was taken from (the stretch of a line a claim
    sentence came from, markup included), checked as well because a token can show in a source and not in the text
    taken from it. A source showing more than MAX_SOURCE_VALUES distinct personal-data values withholds the text.
    Non-strings pass through unchanged."""
    if not isinstance(text, str):
        return text
    sources = [s for s in sources if isinstance(s, str)]
    for t in (text, *sources):
        if secret_matches(t):
            return WITHHELD_SECRET  # a secret anywhere in a source withholds all that was taken from it
    replaced = set()
    out = _redact(text, replaced)  # every occurrence in the text as written
    # what a source shows and the text still holds, as written or once its markup is removed: a token the text glued
    # a character onto
    for src in sources:
        found = list(dict.fromkeys(pii_matches(src)))  # each (rule, value) once, though several forms show it
        if len(found) > MAX_SOURCE_VALUES:
            return WITHHELD_PII
        for rule, matched in found:
            for candidate in (matched, unmark(matched)):
                if candidate and candidate in out:
                    out = out.replace(candidate, f"[redacted:{rule}]")
                    replaced.add(candidate)
                    break
            else:
                if not (matched in replaced or unmark(matched) in replaced):
                    return WITHHELD_PII  # in this text's own stretch of the line, and not something it can cut
    if secret_matches(out):
        return WITHHELD_SECRET  # a replacement shortened what was between two halves of a token
    if pii_matches(out):
        return WITHHELD_PII  # still there in some form: markup split an occurrence the pass above could not see
    return out


def one_line(text, limit=500):
    """`text` fit for one line of a log: control characters escaped, so it cannot start a new line or rewrite the
    one it is on, and cut to `limit` characters."""
    text = re.sub(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]", lambda m: f"\\x{ord(m.group(0)):02x}"
                  if ord(m.group(0)) < 256 else f"\\u{ord(m.group(0)):04x}", text)
    return text if len(text) <= limit else text[:limit - 3] + "..."


def fingerprint(rule, text):
    """What an allowlist names to suppress one finding. 16 hex characters of sha256(rule, NUL, text)."""
    return hashlib.sha256(f"{rule}\0{text}".encode()).hexdigest()[:16]


_PEM_NOISE = re.compile(r"[*_`~]")
_TAG = re.compile(r"<[^<>\n]{0,200}>")
_KEY_HEADER = _SECRET_RX["private-key-block"]


def _pem_text(text):
    """A line with every `*`, `_`, backtick and `~` deleted, then every HTML tag: PEM uses none of them, so however a
    key was marked up, and whichever form found its header, this gives one text for it."""
    return _TAG.sub("", _PEM_NOISE.sub("", text))


def key_blocks(lines, index):
    """The private keys whose headers are on line `index` of `lines` ([(number, text)]), each as the text its
    fingerprint is taken over: from the header to the END marker on the same line, or across the following lines up
    to the one holding it (at most 200, and not past a line holding another header, where another key begins).
    Fingerprinting the header alone would make one suppression cover every key of that type. Empty when no header
    shows once markup is deleted (a header inside what looks like a tag). The following lines are read once for all
    the headers on the line, so a patch of header lines costs time in proportion to its length."""
    text = _pem_text(lines[index][1])
    out, following = [], None
    for h in _KEY_HEADER.finditer(text):
        tail = text[h.start():]
        end = tail.find("-----END", 11)
        if end >= 0:
            close = tail.find("-----", end + 8)
            out.append(tail[:close + 5] if close >= 0 else tail)
            continue
        if following is None:
            following = []
            for _, line in lines[index + 1:index + 201]:
                line = _pem_text(line)
                if "-----END" in line:
                    following.append(line)
                    break
                if _KEY_HEADER.search(line):
                    break
                following.append(line)
        out.append("\n".join([tail] + following))
    return out
