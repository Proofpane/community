"""What an agent's record and a run's numbers are held to, on both sides of the boundary: the workspace agent and the
app import nothing of each other (tests/test_workspace_boundary.py), so this module is kept as two copies,
proofpane_workspace/shapes.py and proofpane_open/ade/shapes.py, byte for byte the same (tests/test_shared_copies.py),
and imports nothing but the standard library.

An agent's own MCP servers (check_mcp): each an address over http or sse (https, or http on the machine itself), an npm
package at the exact version it runs at, or one of its repository's servers; at most MAX_SERVERS, none named as the
workspace's own server. A run's durations are kept as counts on fixed buckets, OpenTelemetry's default boundaries
(BUCKETS), so the app can add days together and read a percentile from the sum."""

import hashlib
import json
import math
import re

MAX_SERVERS = 20
MAX_PAIRS = 20
KINDS = ("address", "package", "repository")
OWN = "proofpane"                       # the workspace's own in-process server: no agent's server takes its name
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
HEADER_NAME = re.compile(r"[A-Za-z0-9-]{1,128}")
_NPM = re.compile(r"^(@[a-z0-9][a-z0-9._-]{0,63}/)?[a-z0-9][a-z0-9._-]{0,127}\Z")
_PINNED = re.compile(r"^[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}([-+][0-9A-Za-z.-]{1,64})?\Z")
_URL = re.compile(r"^https://[^\s/?#@]{1,253}(/[^\s]{0,1800})?\Z|^http://(localhost|127\.0\.0\.1)(:[0-9]{1,5})?(/[^\s]{0,1800})?\Z")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
# a key in an address's query or a package's argument would be kept in the app's store, as plain configuration
KEY_WORDS = frozenset(("token", "key", "apikey", "secret", "password", "passwd", "pwd", "pass", "auth",
                       "authorization", "credential", "credentials", "sig", "signature", "session", "sid"))


def _names_a_key(name):
    """Whether a query's or a flag's name names a key: a whole part of it (split at - and _) is one of KEY_WORDS."""
    return any(part in KEY_WORDS for part in re.split(r"[-_.]", name.lower().lstrip("-")))
_LINE = re.compile(r"[\x00-\x1f\x7f]")      # a header's or a variable's value, an argument: one line, no control

BUCKETS = (0, 5, 10, 25, 50, 75, 100, 250, 500, 750, 1000, 2500, 5000, 7500, 10000)
TOGETHER = ("dispatches_sent", "dispatches_received", "messages_sent", "messages_received", "questions", "answered",
            "permission_asks", "granted", "denied", "owner_turns")
MAX_SOURCES = 200
MAX_SOURCE = 300


class ShapeError(ValueError):
    pass


# an agent's acceptance checks on a run's final message (the workspace's acceptance.py runs them)
ACCEPTANCE_TIERS = ("low", "medium", "high", "critical")
ACCEPTANCE_ON_FAIL = ("fail", "hitl", "fallback")
MAX_ACCEPTANCE_ITEMS = 20
MAX_ACCEPTANCE_TEXT = 200
_ACCEPTANCE_MODEL = re.compile(r"^[a-z][a-z0-9.-]{0,63}(?:\[1m\])?\Z")
_ACCEPTANCE_PATH = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+){0,9}\Z")


def _json_size(value):
    try:
        return len(json.dumps(value))
    except (TypeError, ValueError):
        return MAX_ACCEPTANCE_TEXT + 1


def check_acceptance(given):
    """An agent's acceptance checks as kept, or None for none."""
    if given in (None, {}):
        return None
    if not isinstance(given, dict) or set(given) - {"required_keys", "json_path_equals", "max_risk_tier",
                                                    "forbidden_substrings", "on_fail", "fallback_model"}:
        raise ShapeError("Acceptance is required_keys, json_path_equals, max_risk_tier, forbidden_substrings, "
                              "on_fail and fallback_model.")
    keys = given.get("required_keys") or []
    paths = given.get("json_path_equals") or {}
    words = given.get("forbidden_substrings") or []
    tier = given.get("max_risk_tier")
    on_fail = given.get("on_fail") or "fail"
    model = given.get("fallback_model")
    if not isinstance(keys, list) or len(keys) > MAX_ACCEPTANCE_ITEMS or not all(isinstance(k, str) and 0 < len(k) <= MAX_ACCEPTANCE_TEXT
                                                                       for k in keys):
        raise ShapeError(f"required_keys are at most {MAX_ACCEPTANCE_ITEMS} names.")
    if not isinstance(paths, dict) or len(paths) > MAX_ACCEPTANCE_ITEMS or not all(
            isinstance(p, str) and _ACCEPTANCE_PATH.match(p) and _json_size(v) <= MAX_ACCEPTANCE_TEXT
            for p, v in paths.items()):
        raise ShapeError("json_path_equals maps a path such as a.b.0.c to the value it must have, at most "
                         f"{MAX_ACCEPTANCE_TEXT} characters of JSON.")
    if not isinstance(words, list) or len(words) > MAX_ACCEPTANCE_ITEMS or not all(isinstance(w, str) and 0 < len(w) <= MAX_ACCEPTANCE_TEXT
                                                                         for w in words):
        raise ShapeError(f"forbidden_substrings are at most {MAX_ACCEPTANCE_ITEMS} pieces of text.")
    if tier is not None and tier not in ACCEPTANCE_TIERS:
        raise ShapeError("max_risk_tier is low, medium, high or critical.")
    if on_fail not in ACCEPTANCE_ON_FAIL:
        raise ShapeError("on_fail is fail, hitl or fallback.")
    if on_fail == "fallback" and not (isinstance(model, str) and _ACCEPTANCE_MODEL.match(model)):
        raise ShapeError("A fallback needs its model, as Claude Code names it.")
    if not (keys or paths or words or tier):
        return None
    return {"required_keys": keys, "json_path_equals": paths, "max_risk_tier": tier, "forbidden_substrings": words,
            "on_fail": on_fail, "fallback_model": model if on_fail == "fallback" else None}


def _pairs(given, names, what):
    """Names and values, checked: at most MAX_PAIRS, each name as `names` allows, each value a line of text."""
    if given in (None, ""):
        return {}
    if not isinstance(given, dict) or len(given) > MAX_PAIRS or not all(
            isinstance(k, str) and names.fullmatch(k) and isinstance(v, str) and len(v) <= 2000
            and not _LINE.search(v) for k, v in given.items()):
        raise ShapeError(f"A server's {what} are at most {MAX_PAIRS} names, each with a line of text.")
    return dict(given)


def _names_of(given, names, what):
    if given in (None, ""):
        return []
    if not isinstance(given, list) or len(given) > MAX_PAIRS or not all(
            isinstance(n, str) and names.fullmatch(n) for n in given):
        raise ShapeError(f"A server's secret {what} are at most {MAX_PAIRS} names.")
    return sorted(set(given))


def check_mcp(given):
    """An agent's MCP servers, as the app mirrors them: {connectors, servers, accepted, held}. `connectors`: whether its
    runs use the claude.ai connectors (and so cannot run strict); `servers`: its own, each {name, kind, ...};
    `accepted`: each server's tools as the owner last accepted them (a digest; the change watch); `held`: the ones a run
    found changed, kept out of runs until the owner accepts them."""
    given = given if isinstance(given, dict) else {}
    servers = given.get("servers") or []
    if not isinstance(servers, list) or len(servers) > MAX_SERVERS:
        raise ShapeError(f"An agent has at most {MAX_SERVERS} MCP servers of its own.")
    out, seen = [], set()
    for s in servers:
        if not isinstance(s, dict) or not isinstance(s.get("name"), str) or not NAME.fullmatch(s["name"]) \
                or s["name"] == OWN or s["name"] in seen or s.get("kind") not in KINDS:
            raise ShapeError("A server has a name of its own (letters, digits, '.', '_', '-'), and is an address, a "
                             "package or one of the repository's.")
        seen.add(s["name"])
        kept = {"name": s["name"], "kind": s["kind"]}
        if s["kind"] == "address":
            if s.get("transport") not in ("http", "sse") or not isinstance(s.get("url"), str) \
                    or not _URL.match(s["url"]):
                raise ShapeError("An address is an https address (or http on this machine), over http or sse.")
            query = s["url"].split("?", 1)[1].split("#", 1)[0] if "?" in s["url"] else ""
            if any(_names_a_key(part.split("=", 1)[0]) for part in query.split("&") if part):
                raise ShapeError("A key in an address's query is kept as the agent's plain configuration: give it as a "
                                 "secret header instead.")
            kept.update(transport=s["transport"], url=s["url"],
                        headers=_pairs(s.get("headers"), HEADER_NAME, "headers"),
                        secret_headers=_names_of(s.get("secret_headers"), HEADER_NAME, "headers"))
        elif s["kind"] == "package":
            args = s.get("args") or []
            if s.get("registry", "npm") != "npm" or not isinstance(s.get("identifier"), str) \
                    or not _NPM.match(s["identifier"]) or not isinstance(s.get("version"), str) \
                    or not _PINNED.match(s["version"]) or not isinstance(args, list) or len(args) > MAX_PAIRS \
                    or not all(isinstance(a, str) and len(a) <= 500 and not _LINE.search(a) for a in args):
                raise ShapeError("A package is an npm package's name with the exact version it runs at "
                                 "(1.2.3), and its arguments.")
            if any(a.startswith("-") and _names_a_key(a.split("=", 1)[0]) for a in args):
                raise ShapeError("A key in a package's arguments is kept as the agent's plain configuration: give it "
                                 "as a secret variable instead.")
            kept.update(registry="npm", identifier=s["identifier"], version=s["version"], args=list(args),
                        env=_pairs(s.get("env"), ENV_NAME, "environment"),
                        secret_env=_names_of(s.get("secret_env"), ENV_NAME, "environment"))
        out.append(kept)
    accepted = given.get("accepted") or {}
    if not isinstance(accepted, dict) or not all(k in seen and isinstance(v, str) and _DIGEST.match(v)
                                                  for k, v in accepted.items()):
        raise ShapeError("An agent's accepted tools are a digest for each of its servers.")
    held = given.get("held") or []
    if not isinstance(held, list) or not all(isinstance(n, str) and n in seen for n in held):
        raise ShapeError("An agent's held servers are some of its own.")
    return {"connectors": given.get("connectors") is True, "servers": out, "accepted": dict(accepted),
            "held": sorted(set(held))}


def bucket_counts(values):
    """Counts on BUCKETS: the i-th of the first len(BUCKETS) holds the values above the boundary before it and at most
    its own; the last holds those above the last boundary, as OpenTelemetry's explicit buckets do."""
    counts = [0] * (len(BUCKETS) + 1)
    for v in values:
        i = 0
        while i < len(BUCKETS) and v > BUCKETS[i]:
            i += 1
        counts[i] += 1
    return counts


def add_counts(a, b):
    """Two bucket counts added, either one possibly missing or of the wrong length (then it adds nothing)."""
    size = len(BUCKETS) + 1
    a = a if isinstance(a, list) and len(a) == size else [0] * size
    b = b if isinstance(b, list) and len(b) == size else [0] * size
    return [int(x if isinstance(x, int) else 0) + int(y if isinstance(y, int) else 0) for x, y in zip(a, b)]


def percentile(counts, q):
    """{low, high, value}: the q-th quantile from bucket counts, its bucket's bounds and a value found within it by a
    straight line; in the last bucket high is None and the value is its low. None for no values."""
    total = sum(counts) if isinstance(counts, list) else 0
    if not total:
        return None
    rank = q * total
    before = 0
    for i, n in enumerate(counts):
        if n and before + n >= rank:
            low = BUCKETS[i - 1] if i > 0 else 0
            high = BUCKETS[i] if i < len(BUCKETS) else None
            value = low if high is None else round(low + (high - low) * max(0.0, rank - before) / n, 3)
            return {"low": low, "high": high, "value": value}
        before += n
    return None


def memory_digest(files):
    """An agent's memory folder as one digest, from {path: its bytes}: the same on both sides, so a version the app
    wrote back is known as the same folder when a run finds it unchanged."""
    lines = sorted(f"{path}\0{hashlib.sha256(data).hexdigest()}" for path, data in files.items())
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def exact(values, q):
    """The q-th quantile of the values themselves (nearest rank), for one run's own calls."""
    values = sorted(values)
    if not values:
        return None
    return round(values[min(len(values) - 1, max(0, math.ceil(q * len(values)) - 1))], 3)


# ---- the improve loop's changes ------------------------------------------------------

SKILL_KEYS = ("name", "description", "when_to_use")
SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
MAX_SKILLS = 20
MAX_SKILL = 16 * 1024
CHANGE_TARGETS = ("brief", "skill", "effort", "autocompact_window", "model", "review_prompt")
CHANGE_EFFORTS = ("low", "medium", "high", "xhigh", "max")
MAX_BRIEF = 4000
MAX_REVIEW_PROMPT = 8000
MAX_EVIDENCE_RUNS = 20
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---(?:\n|\Z)", re.S)
_FRONT_KEY = re.compile(r"^([A-Za-z0-9_-]+)[ \t]*:")
_RUN_ID = re.compile(r"^[0-9a-f]{12}$")


def check_skill(name, content):
    """A skill as an agent may have it: its name (the folder's, which Claude Code lists it by: measured on 2.1.274) and
    its SKILL.md, whose frontmatter holds name, description and when_to_use only. Under the SDK a project skill's hooks
    and allowed-tools are used whatever the workspace's trust, so allowed-tools, hooks, model, shell, agent and every
    other key are refused, as is a frontmatter line that is not a plain key; the text, with its line ends made \\n."""
    if not isinstance(name, str) or not SKILL_NAME.match(name):
        raise ShapeError("A skill's name is lowercase letters, digits and dashes, at most 63.")
    if not isinstance(content, str) or not content.strip() or "\x00" in content or \
            len(content.encode("utf-8")) > MAX_SKILL:
        raise ShapeError(f"A skill is its SKILL.md's text, at most {MAX_SKILL // 1024} KB.")
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    m = _FRONTMATTER.match(text)
    if not m:
        raise ShapeError("A skill's SKILL.md begins with its frontmatter, between two --- lines.")
    keys = []
    for line in m.group(1).split("\n"):
        if not line.strip() or line.lstrip().startswith("#") or line[0] in " \t":
            continue                                      # a blank line, a comment, a value's continuation
        k = _FRONT_KEY.match(line)
        if not k:
            raise ShapeError("A skill's frontmatter is lines of a plain key and its value.")
        keys.append(k.group(1))
    beyond = sorted(set(keys) - set(SKILL_KEYS))
    if beyond:
        raise ShapeError(f"A skill's frontmatter holds name, description and when_to_use only, not {', '.join(beyond)}: "
                         "under the SDK a skill's allowed-tools and hooks are used whatever the workspace's trust.")
    if "description" not in keys:
        raise ShapeError("A skill's frontmatter names its description, which tells a run when to use it.")
    return text


def check_skills(given):
    """An agent's skills, {name: SKILL.md text}: at most MAX_SKILLS, each held as check_skill holds it."""
    if given in (None, {}):
        return {}
    if not isinstance(given, dict) or len(given) > MAX_SKILLS:
        raise ShapeError(f"An agent's skills are at most {MAX_SKILLS}, each a name and its SKILL.md.")
    return {name: check_skill(name, content) for name, content in sorted(given.items())}


def _text(value, most, what):
    if not isinstance(value, str) or not value.strip() or "\x00" in value or len(value) > most:
        raise ShapeError(f"{what} is text, at most {most} characters.")
    return value


def check_change(given):
    """A change the improve loop proposes, as {target, name (a skill's), value, hypothesis, evidence}: to the brief, a
    skill, the effort, the compaction point, the model or the review prompt only. Never to permissions, tools, code,
    limits, memory or knowledge, which no target names."""
    if not isinstance(given, dict) or given.get("target") not in CHANGE_TARGETS:
        raise ShapeError("A change is to the brief, a skill, the effort, the compaction point, the model or the review "
                         "prompt; never to permissions, tools, code, limits, memory or knowledge.")
    if set(given) - {"target", "name", "value", "hypothesis", "evidence"}:
        raise ShapeError("A change is its target, a skill's name, its value, a hypothesis and its evidence.")
    target, value = given["target"], given.get("value")
    name = None
    if target == "brief":
        value = _text(value, MAX_BRIEF, "A brief")
    elif target == "review_prompt":
        value = _text(value, MAX_REVIEW_PROMPT, "The review prompt")
    elif target == "skill":
        name = given.get("name")
        value = check_skill(name, value)
    elif target == "effort":
        if value not in CHANGE_EFFORTS:
            raise ShapeError(f"An effort is one of {', '.join(CHANGE_EFFORTS)}.")
    elif target == "autocompact_window":
        if not isinstance(value, int) or isinstance(value, bool) or not 100_000 <= value <= 1_000_000:
            raise ShapeError("A compaction point is from 100,000 to 1,000,000 tokens.")
    elif not (isinstance(value, str) and _ACCEPTANCE_MODEL.match(value)):
        raise ShapeError("A model is named as Claude Code names it.")
    hypothesis = given.get("hypothesis")
    if not isinstance(hypothesis, str) or not hypothesis.strip() or len(hypothesis) > 300 or _LINE.search(hypothesis):
        raise ShapeError("A change's hypothesis is one line, at most 300 characters.")
    evidence = given.get("evidence") if isinstance(given.get("evidence"), dict) else {}
    runs = evidence.get("runs") or []
    if not isinstance(runs, list) or len(runs) > MAX_EVIDENCE_RUNS or not all(isinstance(r, str) and _RUN_ID.match(r)
                                                                                for r in runs):
        raise ShapeError(f"A change's evidence names at most {MAX_EVIDENCE_RUNS} runs by their ids.")
    kept = {"runs": list(dict.fromkeys(runs))}
    for key in ("step", "metric"):
        if evidence.get(key) is not None:
            kept[key] = _text(evidence[key], 200, f"The evidence's {key}")
    return {"target": target, "name": name, "value": value, "hypothesis": hypothesis.strip(), "evidence": kept}


# ---- a business skill's SKILL.md, the improve loop's proposals for it ---------------

# the keys a business skill may hold and still be proposed for: read by the builds below, and granting or configuring
# nothing. `context` and `agent` are not among them: in 2.1.42 they run the skill as a sub-agent under
# an agent definition, with its permission mode, hooks, MCP servers and tools
BUSINESS_KEYS = ("name", "description", "when_to_use", "version", "argument-hint", "arguments", "model", "effort",
                 "disable-model-invocation", "user-invocable", "paths")
BUSINESS_NOTE_KEYS = ("model", "effort", "disable-model-invocation", "user-invocable", "paths")
BUSINESS_TEXT_KEYS = ("description", "when_to_use")
BUSINESS_BOOLEAN_KEYS = ("disable-model-invocation", "user-invocable")
BUILDS_READ = ("2.1.42", "2.1.139", "2.1.274", "2.1.308", "2.1.310")
NEWEST_READ = (2, 1, 310)
# the counts a proposal carries, for the whole, the current version and the version before: the calls,
# the typed calls, the failed calls, the tasks that called the skill by how each stands (skill_counts.ENDED's five) and
# the tasks among them whose last round of checks did not pass
SKILL_COUNTS_FIELDS = ("calls", "typed", "failed", "tasks_done", "tasks_failed", "tasks_stopped", "tasks_open",
                       "tasks_unknown", "tasks_checks_failed")
SKILL_COUNTS_PARTS = ("whole", "current", "before")
_BUSINESS_WORDS = ("null", "Null", "NULL", "true", "True", "TRUE", "false", "False", "FALSE")
_BUSINESS_PLAIN = re.compile(r"[A-Za-z][A-Za-z0-9 .,;()'/_+=?-]*\Z")
_BUSINESS_QUOTED = re.compile(r'"[A-Za-z0-9 .,;()\'/_+=?-]+"\Z')
_BUSINESS_LINE = re.compile(r"([A-Za-z_-]+): (.*)\Z")
OBJECT_FORMATS = ("sha1", "sha256")
OBJECT_KINDS = ("blob", "tree", "commit")


def business_frontmatter(text):
    """A business skill's frontmatter in the narrow subset every build's parser reads alike, by its own code:
    {"lines", "keys", "values", "body"}, its lines between the two --- lines, its keys in order,
    each value as a string or a boolean, and all after the closing line; else ShapeError naming the first rule broken.

    The text begins with exactly ---\\n and the frontmatter ends at the first line that is exactly ---, followed by \\n,
    no line between holding --- anywhere. Each line between is `key: value`, a key of BUSINESS_KEYS once, one space after
    the colon, in printable ASCII with no tab: true or false for BUSINESS_BOOLEAN_KEYS alone; a double-quoted run of the
    plain set's characters; or a plain string that begins with a letter, holds only letters, digits, spaces and
    . , ; ( ) ' / _ + = ? -, no two spaces and no two dots together, does not end in a space, and is none of the nine
    words YAML reads as null or a boolean. description is present."""
    if not isinstance(text, str) or not text.startswith("---\n"):
        raise ShapeError("A business skill's SKILL.md begins with exactly ---, then a line break.")
    rest = text[4:]
    if rest.startswith("---\n"):
        raise ShapeError("A business skill's frontmatter holds its description.")
    end = rest.find("\n---\n")
    if end < 0:
        raise ShapeError("A business skill's frontmatter ends at a line that is exactly ---, then a line break.")
    lines = rest[:end].split("\n")
    keys, values = [], {}
    for n, line in enumerate(lines, 2):
        if "---" in line:
            raise ShapeError(f"Line {n} of the frontmatter holds ---, which the builds read as its end.")
        if any(not 0x20 <= ord(c) <= 0x7e for c in line):
            raise ShapeError(f"Line {n} of the frontmatter holds a character outside printable ASCII, a tab among them.")
        m = _BUSINESS_LINE.match(line)
        if not m or ":" in m.group(2):
            raise ShapeError(f"Line {n} of the frontmatter is not a key, one colon, one space and a value.")
        key, value = m.group(1), m.group(2)
        if key not in BUSINESS_KEYS:
            raise ShapeError(f"Line {n}'s key, {key}, is not one a business skill may hold and still be proposed for.")
        if key in values:
            raise ShapeError(f"Line {n} names {key} a second time.")
        if value in ("true", "false") and key in BUSINESS_BOOLEAN_KEYS:
            values[key] = value == "true"
        elif _BUSINESS_QUOTED.match(value):
            values[key] = value[1:-1]
        elif _BUSINESS_PLAIN.match(value) and "  " not in value and ".." not in value and not value.endswith(" ") \
                and value not in _BUSINESS_WORDS:
            values[key] = value
        else:
            raise ShapeError(f"Line {n}'s value for {key} is outside the subset every build reads alike: a plain "
                             "string begins with a letter, holds only letters, digits, spaces and . , ; ( ) ' / _ + = ? "
                             "-, no two spaces or dots together, and ends in no space; or it is double-quoted.")
        keys.append(key)
    if "description" not in values:
        raise ShapeError("A business skill's frontmatter holds its description.")
    return {"lines": lines, "keys": keys, "values": values, "body": rest[end + 5:]}


def business_constructs(text):
    """What a build runs or reads into the conversation from a skill's text, by tests wider than every build's own
    pattern, over the whole file: the names of those found, in a fixed order, or []."""
    found = []
    if "!`" in text:
        found.append("a command run inline (! then a backtick)")
    if "```!" in text:
        found.append("a command run as a block (three backticks then !)")
    if any(c == "@" and (i == 0 or not (text[i - 1].isascii() and text[i - 1].isalnum())) for i, c in enumerate(text)):
        found.append("a mention (an @ that follows no ASCII letter or digit)")
    if "ultra" in text.lower():
        found.append("a keyword (ultra, which ultrathink and ultracode begin with)")
    return found


def business_dollar_lines(text):
    """Every line of the text that holds a $, in order: a proposal may change none."""
    return [line for line in text.split("\n") if "$" in line]


def check_business_skill(text):
    """A business skill's current SKILL.md, as the loop may propose for it: at most MAX_SKILL bytes, no carriage return,
    its frontmatter in the subset (business_frontmatter), no construct (business_constructs) and no ${ left open on
    its line. Gives business_frontmatter's reading."""
    try:
        size = len(text.encode("utf-8"))
    except (AttributeError, UnicodeError):
        size = None
    if size is None or size > MAX_SKILL:
        raise ShapeError(f"A business skill's SKILL.md is UTF-8 text of at most {MAX_SKILL} bytes.")
    if "\r" in text:
        raise ShapeError("A business skill's SKILL.md holds no carriage return.")
    read = business_frontmatter(text)
    found = business_constructs(text)
    if found:
        raise ShapeError(f"A business skill's SKILL.md holds {found[0]}, which a build runs or reads into the turn.")
    for n, line in enumerate(text.split("\n"), 1):
        at = line.find("${")
        while at >= 0:
            if "}" not in line[at + 2:]:
                raise ShapeError(f"Line {n} holds a ${{ that no }} closes on its line.")
            at = line.find("${", at + 2)
    return read


def check_business_proposal(current, proposed):
    """A proposal against the current version: both pass check_business_skill; every key but
    BUSINESS_TEXT_KEYS keeps its value, and every frontmatter line but theirs stays byte for byte, the keys the same and
    in the same order, so that the proposal gains and loses no key; the lines holding a $ are the same lines, in the
    same order; and the proposal differs from the current version. The mode is the trees', checked from them."""
    now, then = check_business_skill(current), check_business_skill(proposed)
    if now["keys"] != then["keys"]:
        raise ShapeError("A proposal keeps the frontmatter's keys, in their order: it gains and loses none.")
    for key, a, b in zip(now["keys"], now["lines"], then["lines"]):
        if key not in BUSINESS_TEXT_KEYS and a != b:
            raise ShapeError(f"A proposal changes only description and when_to_use, not {key}.")
    if business_dollar_lines(current) != business_dollar_lines(proposed):
        raise ShapeError("A proposal changes, adds, removes or moves no line that holds a $.")
    if current == proposed:
        raise ShapeError("A proposal changes the SKILL.md.")
    return then


def check_skill_hypothesis(text):
    """A review's hypothesis for a proposal: one line of at most 300 characters, as check_change takes an agent's, that
    business_constructs passes, since the critic is given it as text Claude Code reads."""
    if not isinstance(text, str) or not text.strip() or len(text) > 300 or _LINE.search(text):
        raise ShapeError("A proposal's hypothesis is one line, at most 300 characters.")
    found = business_constructs(text)
    if found:
        raise ShapeError(f"A proposal's hypothesis holds {found[0]}.")
    return text.strip()


def check_skill_counts(given):
    """The counts a proposal carries, as the app sends them: {"whole", "current", "before"}, each an object of
    SKILL_COUNTS_FIELDS, each a non-negative integer, the current version's and the one before's possibly null."""
    if not isinstance(given, dict) or set(given) != set(SKILL_COUNTS_PARTS):
        raise ShapeError("A proposal's counts are for the whole, the current version and the version before.")
    kept = {}
    for part in SKILL_COUNTS_PARTS:
        counts = given[part]
        if counts is None and part != "whole":
            kept[part] = None
            continue
        if not isinstance(counts, dict) or set(counts) != set(SKILL_COUNTS_FIELDS) or not all(
                isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in counts.values()):
            raise ShapeError(f"The counts for {part} are {', '.join(SKILL_COUNTS_FIELDS)}, each a whole number.")
        kept[part] = {field: counts[field] for field in SKILL_COUNTS_FIELDS}
    return kept


def object_id(kind, data, fmt):
    """A git object's id, as git computes it: the hash of `<kind> <size>\\0` and the object's bytes, in the repository's
    object format."""
    if kind not in OBJECT_KINDS or fmt not in OBJECT_FORMATS or not isinstance(data, bytes):
        raise ShapeError("An object is a blob, a tree or a commit, as bytes, in sha1 or sha256.")
    return hashlib.new(fmt, f"{kind} {len(data)}\0".encode() + data).hexdigest()
