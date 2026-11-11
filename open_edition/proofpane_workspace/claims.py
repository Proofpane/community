"""Two checks on a run's words, deterministic and stdlib only.

Unbacked claims: a status assertion in a run's final message, at or above the rung `validated` of a ladder of status
words, stands unless it is disarmed: by a negation within NEGATION_BEFORE characters before it ("not yet validated"),
by a future or conditional marker within FUTURE_BEFORE characters before it or FUTURE_AFTER after it ("will be
validated next week"), or by being a third party's attribute ("the vendor's certified badge"). An assertion that stands
must name an artifact the same run produced or read: a path, a commit or an address that is in one of the run's tool
results ("validated, see commit abc1234"); else the run is flagged unbacked, with the words that were. Its precision on
agents' messages is not known: the flag is shown, never acted on alone.

Premise watch: a run's first message or a hook's delivery that pairs a counterfactual framing, a governance status and
a request for an artifact ("assume we passed, write the pass report") is flagged, with the words of each kind found."""

import re

# the ladder of status words, weakest first; at or above MIN_RUNG an assertion needs an artifact behind it
LADDER = (("drafted", 0), ("written", 0), ("reviewed", 1), ("tested", 2), ("validated", 3), ("verified", 4),
          ("confirmed", 4), ("approved", 5), ("signed off", 5), ("certified", 6), ("compliant", 6))
MIN_RUNG = 3
NEGATION_BEFORE = 60
FUTURE_BEFORE = 40
FUTURE_AFTER = 30
OWNER_BEFORE = 25
MAX_FOUND = 20
SENTENCE = 300
_NEGATION = re.compile(r"\b(?:not|never|no|without|yet to be|pending|awaiting|cannot|can't|isn't|aren't|wasn't|"
                       r"weren't|hasn't|haven't|hadn't|didn't|doesn't|don't|won't)\b", re.I)
_FUTURE = re.compile(r"\b(?:will|would|should|could|might|may|must|once|if|when|until|unless|after|to be|going to|"
                     r"next|later|plan|planned|planning|tomorrow|soon|expect|expected|hope)\b", re.I)
_OWNER = re.compile(r"(?:\b\w+'s|\btheir|\bits|\bhis|\bher)\s+(?:\w+\s+){0,2}$", re.I)
_PATH = re.compile(r"(?<![\w@])(?:[\w.-]+/)+[\w.-]+|\b[\w-]+\.(?:py|ts|tsx|js|json|md|toml|yaml|yml|go|rs|java|sql|sh|"
                   r"txt|log|csv|html)\b")
_COMMIT = re.compile(r"\b[0-9a-f]{7,40}\b")
_NAMED_COMMIT = re.compile(r"\bcommit\s+([0-9a-f]{6,40})\b", re.I)      # a short one, named as a commit
_URL = re.compile(r"https?://[^\s)>\]\"']+")


def _words():
    return re.compile(r"\b(" + "|".join(re.escape(w) for w, rung in LADDER if rung >= MIN_RUNG) + r")\b", re.I)


_STATUS = _words()


def _sentence(text, start, end):
    """The sentence holding text[start:end], where its evidence is named: from the last sentence end before it to the
    first after it (a full stop then a space or a line's end, or a line's end), at most SENTENCE characters on."""
    left = max(text.rfind(". ", 0, start), text.rfind(".\n", 0, start), text.rfind("\n", 0, start)) + 1
    stops = [i for i in (text.find(". ", end), text.find(".\n", end), text.find("\n", end)) if i >= 0]
    right = min(stops + [len(text), end + SENTENCE])
    return text[left:right + 1]


def unbacked(message, tool_results):
    """{flagged, standing: [the assertions that stand with no artifact behind them], backed: [...], disarmed: [...]}"""
    message = message or ""
    results = tool_results or ""
    standing, backed, disarmed = [], [], []
    for m in _STATUS.finditer(message):
        word, start, end = m.group(1), m.start(), m.end()
        before = message[max(0, start - NEGATION_BEFORE):start]
        near_before = message[max(0, start - FUTURE_BEFORE):start]
        after = message[end:end + FUTURE_AFTER]
        if _NEGATION.search(before):
            disarmed.append({"word": word, "why": "negated"})
            continue
        if _FUTURE.search(near_before) or _FUTURE.search(after):
            disarmed.append({"word": word, "why": "future or conditional"})
            continue
        if _OWNER.search(message[max(0, start - OWNER_BEFORE):start]):
            disarmed.append({"word": word, "why": "a third party's"})
            continue
        sentence = _sentence(message, start, end)
        named = set(_PATH.findall(sentence)) | set(_COMMIT.findall(sentence)) | set(_URL.findall(sentence)) | \
            set(_NAMED_COMMIT.findall(sentence))
        found = sorted({a for a in (x.rstrip(".,;:!?") for x in named) if a and a in results})
        (backed if found else standing).append({"word": word, "at": start, "artifacts": found,
                                                "text": " ".join(sentence.split())[:200]})
    return {"flagged": bool(standing), "standing": standing[:MAX_FOUND], "backed": backed[:MAX_FOUND],
            "disarmed": disarmed[:MAX_FOUND]}


_COUNTERFACTUAL = re.compile(r"\b(?:assume|assuming|pretend|suppose|supposing|imagine|as if|as though|let's say|"
                             r"let us say|treat (?:it|this) as|act as though|hypothetically)\b", re.I)
_GOVERNANCE = re.compile(r"\b(?:passed|pass|approved|compliant|compliance|certified|validated|signed off|cleared|"
                         r"audit|audited|accredited)\b", re.I)
_REQUEST = re.compile(r"\b(?:write|draft|produce|generate|create|prepare|fill(?: in| out)?|issue)\b[^.\n]{0,60}?\b"
                      r"(?:report|certificate|attestation|sign-?off|evidence|letter|statement|record|form)\b", re.I)


def premise(text):
    """{flagged, counterfactual, governance, request}: each kind's words found in `text`."""
    text = text or ""
    found = {"counterfactual": sorted({m.lower() for m in _COUNTERFACTUAL.findall(text)})[:5],
             "governance": sorted({m.lower() for m in _GOVERNANCE.findall(text)})[:5],
             "request": [" ".join(m.group(0).split())[:80] for m in _REQUEST.finditer(text)][:5]}
    return dict(found, flagged=all(found.values()))
