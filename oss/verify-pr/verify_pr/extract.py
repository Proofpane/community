"""Pattern-based claim extraction from a pull request's title and description.

A recognised claim is one of a fixed set of patterns. Each pattern either names the resolver that can check it, or
none, in which case the claim is recognised but unverifiable in v0. Sentences no pattern recognises are counted and
never examined further, so a summary cannot imply the whole description was checked.

The extractor errs towards missing a claim rather than inventing one: a sentence that hedges ("should", "will",
"TODO") or negates a positive claim ("tests do not pass") yields nothing, and an unticked checkbox is not an
assertion.
"""

import bisect
import re

# (resolver or None, pattern id, regular expression). Order matters only for the report, not for matching.
PATTERNS = [
    # the first repetition is bounded: unbounded, a line of "all all all ..." cost quadratic time, because a run of
    # it starts at every word; the second is entered only after "test", so it cannot
    ("ci.checks_green", "tests_pass", r"\b(?:(?:all|the|existing|unit|integration|e2e|remaining)\s+){0,4}tests?\s+"
                                      r"(?:(?:are|now|still|all)\s+)*(?:pass(?:es|ing|ed)?|green|succeed(?:s|ed)?)\b"),
    ("ci.checks_green", "passes_tests", r"\bpass(?:es|ing|ed)?\s+(?:all\s+)?(?:the\s+)?(?:existing\s+)?(?:unit\s+)?tests\b"),
    ("ci.checks_green", "ci_green", r"\bci\s+(?:is\s+|are\s+)?(?:green|passing|passes|passed)\b"),
    ("ci.checks_green", "checks_pass", r"\ball\s+(?:ci\s+)?checks\s+(?:are\s+)?(?:pass(?:ing|ed)?|green)\b"),
    # a pasted run summary ("5880 passed") reports a run the author did, usually locally; CI may not reproduce it
    (None, "reported_test_run", r"\b\d+\s+passed\b"),
    ("ci.checks_green", "lint_build", r"\b(?:lint(?:er|ing)?|ruff|flake8|eslint|mypy|tsc|type[-\s]?checks?|"
                                      r"typecheck(?:ing)?|build)\s+(?:is\s+|are\s+)?(?:clean|passes|passing|green|"
                                      r"succeeds|ok)\b"),
    ("diff.no_schema_change", "no_schema", r"\bno\s+(?:database\s+|db\s+)?(?:schema\s+(?:changes?|changed|"
                                           r"updates?|modifications?)|migrations?)\b"),
    ("diff.no_schema_change", "schema_untouched", r"\b(?:does\s+not|doesn't|do\s+not|don't)\s+(?:change|touch|"
                                                  r"modify|alter)\s+(?:the\s+)?(?:database\s+|db\s+)?schema\b"),
    ("diff.no_schema_change", "no_db_change", r"\bno\s+db\s+changes?\b|\bschema\s+(?:is\s+)?unchanged\b"),
    ("diff.no_dependency_change", "no_new_deps", r"\bno\s+new\s+(?:runtime\s+|external\s+|third[-\s]party\s+)?"
                                                 r"(?:dependenc(?:y|ies)|packages?|libraries|deps)\b"),
    ("diff.no_dependency_change", "no_dep_change", r"\bno\s+dependency\s+(?:changes?|updates?|bumps?)\b|"
                                                   r"\bdependencies\s+(?:are\s+)?unchanged\b|"
                                                   r"\b(?:does\s+not|doesn't)\s+add\s+(?:any\s+)?(?:new\s+)?"
                                                   r"(?:dependenc(?:y|ies)|packages?)\b"),
    ("diff.docs_only", "docs_only", r"\b(?:docs?|documentation)[-\s]only\b|\bonly\s+(?:touches\s+|changes\s+)?"
                                    r"(?:the\s+)?(?:docs|documentation|readme|markdown)\b|\bno\s+code\s+changes?\b"),
    ("diff.tests_added", "tests_added", r"\b(?:adds?|added|adding|new|includes?|included|wrote|writes|written)\s+"
                                        r"(?:(?:unit|regression|integration|e2e|end-to-end|more|a|an|the|some|"
                                        r"several|\d+)\s+){0,2}tests?\b(?!\s+pass)"),
    ("diff.tests_added", "tests_were_added", r"\btests?\s+(?:were\s+|are\s+|have\s+been\s+)?added\b|"
                                             r"\badds?\s+(?:test\s+)?coverage\b"),
    ("issue.linked", "fixes_issue", r"\b(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?)\s+(?:issue\s+)?"
                                    r"(?:(?P<repo>[\w.-]+/[\w.-]+))?#(?P<num>\d{1,10})\b"),
    # recognised, but nothing in v0 can check them
    (None, "backwards_compatible", r"\bbackwards?[-\s]compatib(?:le|ility)\b|\bno\s+breaking\s+changes?\b|"
                                   r"\bnon[-\s]breaking\b|\bfully\s+compatible\b"),
    (None, "no_behaviour_change", r"\bno\s+(?:functional|behaviou?r(?:al)?|user[-\s]facing|visible)\s+changes?\b|"
                                  r"\bbehaviou?r\s+(?:is\s+)?unchanged\b|\b(?:pure|purely|just\s+a|only\s+a)\s+"
                                  r"refactor\b|\brefactor(?:ing)?\s+only\b"),
    (None, "fixes_defect", r"\bfix(?:es|ed)?\s+(?:a|an|the)\s+(?:[\w-]+\s+){0,3}?(?:bug|race|crash|leak|deadlock|"
                           r"regression|error|panic|flake|flaky)\b"),
    (None, "performance", r"\b(?:faster|speeds?\s+up|sped\s+up|improves?\s+performance|performance\s+improvements?|"
                          r"reduces?\s+latency)\b"),
    (None, "safety_property", r"\bthread[-\s]safe\b|\bidempotent\b|\bno\s+security\s+impact\b"),
    (None, "manually_verified", r"\bmanually\s+tested\b|\btested\s+(?:locally|manually)\b|"
                                r"\bverified\s+(?:locally|manually)\b"),
]
_COMPILED = [(r, pid, re.compile(rx, re.I)) for r, pid, rx in PATTERNS]

# A claim that something passed, was added or is clean is not made by a sentence that hedges or negates it.
_POSITIVE = {"tests_pass", "passes_tests", "ci_green", "checks_pass", "reported_test_run", "lint_build",
             "tests_added", "tests_were_added"}
# claims about tests specifically, which need a test check to be checked against
_TEST_CLAIMS = {"tests_pass", "passes_tests"}
_HEDGE = re.compile(r"\b(?:will|should|would|could|might|may|expect(?:s|ed)?|hope(?:fully)?|todo|tbd|unless|"
                    r"once|if|pending|wip)\b", re.I)
_NEGATE = re.compile(r"\b(?:not|never|no\s+longer|cannot|can't|don't|doesn't|didn't|isn't|aren't|won't|"
                     r"fail(?:s|ed|ing|ure|ures)?|broken|skip(?:ped|s)?|flaky|without)\b|n't\b", re.I)
_NEGATE_ADDED = re.compile(r"\b(?:no|none)\b", re.I)
_BENIGN_FAIL = re.compile(r"\b(?:0|zero|no)\s+(?:tests?\s+)?(?:fail(?:ed|ures?|ing)?|errors?|skipped)\b|"
                          r"\b\d+\s+(?:skipped|xfailed|xpassed|deselected|warnings?)\b", re.I)
_LOCAL = re.compile(r"\blocal(?:ly)?\b|\bon\s+my\s+machine\b|\bin\s+(?:my|a\s+local)\s+(?:env|environment)\b", re.I)

_CODE_FENCE = re.compile(r"(^|\n)(```|~~~).*?(\n\2|$)", re.S)
# a list or heading marker needs the space after it, as Markdown does: "+1 415 ..." is a phone number, not a list
_BULLET = re.compile(r"^\s*(?:[-*+](?=\s)|\d+[.)](?=\s)|>+|#+(?=\s))\s*")
_CHECKBOX = re.compile(r"^\[( |x|X)\]\s*")
_INLINE = re.compile(r"`+|\*\*|__|(?<!\w)[*_](?!\s)|(?<!\s)[*_](?!\w)")


_LINE_END = re.compile(r"\r\n|\r|\n")


def markdown_lines(text):
    """`text` split at Markdown's line endings (CommonMark: "\\n", "\\r\\n", "\\r") and nowhere else: U+2028, a form
    feed or a vertical tab inside a line is part of it, as GitHub renders it."""
    return _LINE_END.split(text)


def strip_inline(text):
    """`text` without inline markdown (code spans, emphasis). Removing markup can join what it separated, which is
    why `dlp` looks at text in more than one form."""
    return _INLINE.sub("", text)


def _strip_comments(text):
    """HTML comments replaced by a space, in linear time. An unclosed `<!--` is left as it is: what the regular
    expression `<!--.*?-->` this replaces did, which took quadratic time on a run of `<!--`."""
    out, pos = [], 0
    while True:
        i = text.find("<!--", pos)
        j = text.find("-->", i + 4) if i >= 0 else -1
        if j < 0:
            out.append(text[pos:])
            return "".join(out)
        out.append(text[pos:i] + " ")
        pos = j + 3


def _strip_inline_mapped(text):
    """(`strip_inline(text)`, and for each character it keeps, that character's index in `text`)."""
    kept, index, last = [], [], 0
    for m in _INLINE.finditer(text):
        kept.append(text[last:m.start()])
        index.extend(range(last, m.start()))
        last = m.end()
    kept.append(text[last:])
    index.extend(range(last, len(text)))
    return "".join(kept), index
# not on ";": "5880 passed; the 7 failures are pre-existing" is one claim with its own qualifier; and not between
# "digit." and a digit, where the break is inside a number ("+44 20. 7946 0958" would be copied in two halves, neither
# of which the phone rule recognises)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])(?<![0-9]\.)\s+|(?<=[0-9]\.)\s+(?![0-9])")
# issue references read per pull request; the rest are ignored, so a description cannot make a manifest of any size
MAX_ISSUE_CLAIMS = 20


def _sentences(text, location):
    """(sentence, location, source) for each sentence, where `source` is the stretch of the line as written that the
    sentence came from, markup included: what is checked before any of the sentence is copied."""
    text = _strip_comments(text or "")
    text = _CODE_FENCE.sub("\n", text)
    for raw in markdown_lines(text):
        m = _BULLET.match(raw)
        body = raw[m.end():] if m else raw
        box = _CHECKBOX.match(body.lstrip())
        if box:
            if box.group(1) == " ":
                continue  # an unticked box asserts nothing
            body = body[len(body) - len(body.lstrip()) + box.end():]
        stripped, index = _strip_inline_mapped(body)
        pos = 0
        for part in _SENTENCE_SPLIT.split(stripped):
            start = stripped.index(part, pos)
            pos = start + len(part)
            s = part.strip()
            if not s:
                continue
            a = start + len(part) - len(part.lstrip())
            b = a + len(s)
            lo = index[a - 1] + 1 if a > 0 else 0  # markup just before the sentence goes with it
            hi = index[b] if b < len(index) else len(body)  # and markup just after it
            yield s, location, body[lo:hi]


class _Words:
    """A sentence's whitespace-separated words, found once, so that each match's window costs time in proportion to
    the window and not to the sentence: a sentence repeating a pattern would otherwise cost the square of its length."""

    _WORD = re.compile(r"\S+")

    def __init__(self, sentence):
        self.sentence = sentence
        self.spans = [m.span() for m in self._WORD.finditer(sentence)]
        self.starts = [a for a, _ in self.spans]
        self.ends = [b for _, b in self.spans]

    def window(self, m, before=4, after=6):
        """The match with a few words either side, as `sentence[:start].split()[-before:]` and
        `sentence[end:].split()[:after]` give them: a word the match starts or ends inside is cut at the match. Hedges
        and negations are looked for here, not across the whole sentence: "21 new tests ... no ids in audit payloads"
        is a claim that tests were added."""
        s, (start, end) = self.sentence, m.span()
        i = bisect.bisect_left(self.starts, start)  # the words that start before the match
        left = [s[a:min(b, start)] for a, b in self.spans[max(0, i - before):i]]
        j = bisect.bisect_right(self.ends, end)  # the words that end after it
        right = [s[max(a, end):b] for a, b in self.spans[j:j + after]]
        return " ".join(left + [m.group(0)] + right)


def _blocked(pid, words, m):
    near = words.window(m)
    if _HEDGE.search(near):
        return True
    if pid in _POSITIVE:
        cleaned = _BENIGN_FAIL.sub(" ", near)
        if _NEGATE.search(cleaned):
            return True
        if pid in ("tests_added", "tests_were_added") and _NEGATE_ADDED.search(cleaned):
            return True
    return False


def extract(title, body):
    """Return (claims, unrecognised_sentence_count). Each claim is a dict:
    {"text", "source", "location", "pattern", "resolver" (or None), "args"}, where `source` is the stretch of the
    line the sentence came from, before markup was removed; it is for checking what may be copied and is never copied
    itself. One claim per resolver and argument per pull request, and one per unverifiable pattern, so repeating a
    sentence does not inflate the counts; at most MAX_ISSUE_CLAIMS issue references are read."""
    claims, seen, unrecognised, issues = [], set(), 0, 0
    sources = [(title or "", "pr_title"), (body or "", "pr_body")]
    for text, location in sources:
        for sentence, loc, source in _sentences(text, location):
            hit, words, local = False, _Words(sentence), None
            for resolver, pid, rx in _COMPILED:
                for m in rx.finditer(sentence):
                    if _blocked(pid, words, m):
                        continue
                    args = {}
                    if resolver == "issue.linked":
                        args = {"issue": int(m.group("num")), "repo": m.group("repo")}
                    elif pid in _TEST_CLAIMS:
                        args = {"tests_claim": True}
                    target = resolver
                    if resolver == "ci.checks_green":
                        local = bool(_LOCAL.search(sentence)) if local is None else local  # once per sentence
                    if resolver == "ci.checks_green" and local:
                        target, pid_used = None, "tests_pass_locally"
                    else:
                        pid_used = pid
                    key = (target, pid_used if target is None else None, tuple(sorted(args.items())))
                    hit = True
                    if key in seen:
                        continue
                    if resolver == "issue.linked":
                        if issues == MAX_ISSUE_CLAIMS:
                            continue
                        issues += 1
                    seen.add(key)
                    claims.append({"text": sentence, "source": source, "location": loc, "pattern": pid_used,
                                   "resolver": target, "args": args})
            if not hit and len(sentence.split()) >= 3:
                unrecognised += 1
    return claims, unrecognised
