"""Linear, for a session's task: the person's open issues, and a search of their workspace's, with their own Linear API
key, so a session can start from one as it can from a GitHub issue. Read only: nothing is written to Linear.

The key is kept in the workspace user's home, in ~/.proofpane/linear-key.json (0600, its folder 0700), and never
returned by the workspace: the page learns whether one is set and whose it is. It is not in any session's environment,
and the gate keeps the file from a session by name, as it does the programs' sign-ins.

Linear's GraphQL API (linear.app/developers/graphql): a personal API key goes as the Authorization header as it is,
with no "Bearer". PP_LINEAR_API names another address, for tests only. Stdlib only.
"""

import http.client
import json
import os
import re
import urllib.error
import urllib.request

API = "https://api.linear.app/graphql"
STORE = os.path.join(".proofpane", "linear-key.json")       # in the workspace user's home
MAX_KEY = 400
MAX_ISSUES = 50
MAX_SEARCH = 200
DESCRIPTION = 8000          # an issue's text given to the page, cut there
TIMEOUT = 20
_KEY = re.compile(r"[\x21-\x7e]{8,%d}" % MAX_KEY)       # matched whole (fullmatch): no newline at its end
_IDENTIFIER = re.compile(r"^([A-Za-z][A-Za-z0-9]{0,9})-([0-9]{1,9})$")
_FIELDS = ("identifier title description url updatedAt priorityLabel state { name type } team { key name } "
           "assignee { displayName } labels(first: 10) { nodes { name } }")
_OPEN = {"type": {"nin": ["completed", "canceled"]}}
VIEWER = "query { viewer { name email } }"
MINE = ("query Mine($first: Int!) { viewer { assignedIssues(first: $first, orderBy: updatedAt, "
        "filter: { state: { type: { nin: [\"completed\", \"canceled\"] } } }) { nodes { " + _FIELDS + " } } } }")
SEARCH = ("query Search($first: Int!, $filter: IssueFilter) { issues(first: $first, orderBy: updatedAt, "
          "filter: $filter) { nodes { " + _FIELDS + " } } }")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is refused, never followed: following one would send the key's Authorization header to wherever it
    points."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Linear answered with a redirect, not followed.", headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect)


class LinearError(ValueError):
    """What the person can put right, or what Linear said: shown as it is."""


def store_path(home):
    return os.path.join(home, STORE)


def key(home):
    """The key kept, or None: a file that cannot be read, or holds anything else, keeps none."""
    try:
        with open(store_path(home), encoding="utf-8") as fh:
            got = json.load(fh)
    except (OSError, ValueError):
        return None
    k = got.get("key") if isinstance(got, dict) else None
    return k if isinstance(k, str) and _KEY.fullmatch(k) else None


def _write(home, value):
    folder = os.path.dirname(store_path(home))
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    tmp = store_path(home) + ".tmp"
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(value, fh)
    os.replace(tmp, store_path(home))


def _ask(api_key, query, variables=None):
    """One GraphQL request to Linear: its data, or LinearError with what Linear or the network said."""
    body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    req = urllib.request.Request(os.environ.get("PP_LINEAR_API") or API, data=body, method="POST",
                                 headers={"Authorization": api_key, "Content-Type": "application/json",
                                          "User-Agent": "proofpane-workspace"})
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as r:
            got = json.loads(r.read(4 * 1024 * 1024).decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        if e.code in (400, 401, 403):
            try:
                said = json.loads(e.read(65536).decode("utf-8", "replace"))
            except ValueError:
                said = None
            message = _first_error(said)
            if e.code in (401, 403) or (message and "auth" in message.lower()):
                raise LinearError("Linear did not take the key: make a new personal API key in Linear's settings "
                                  "(Security & access).") from None
            raise LinearError(f"Linear refused the request: {message or e.code}") from None
        if 300 <= e.code < 400:
            raise LinearError("Linear answered with a redirect, which is not followed with your key.") from None
        raise LinearError(f"Linear answered {e.code}: try again in a moment.") from None
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
        # the reason's kind only: an error's own text can carry what was sent, the key among it
        why = e.reason if isinstance(e, urllib.error.URLError) and isinstance(e.reason, (str, OSError)) else e
        raise LinearError(f"Linear could not be reached ({type(why).__name__}).") from None
    if not isinstance(got, dict):
        raise LinearError("Linear answered with something that is not a GraphQL answer.")
    if got.get("errors") and not got.get("data"):
        raise LinearError(f"Linear refused the request: {_first_error(got) or 'no reason given'}")
    return got.get("data") or {}


def _first_error(got):
    errors = got.get("errors") if isinstance(got, dict) else None
    first = errors[0] if isinstance(errors, list) and errors and isinstance(errors[0], dict) else {}
    message = first.get("message")
    return message[:300] if isinstance(message, str) else None


def _who(data):
    viewer = data.get("viewer") if isinstance(data, dict) else None
    if not isinstance(viewer, dict):
        raise LinearError("Linear did not say whose key this is.")
    word = lambda k: viewer[k][:200] if isinstance(viewer.get(k), str) else None  # noqa: E731
    return {"name": word("name"), "email": word("email")}


def status(home):
    """Whether a key is set and, when it is, whose: {"set", "user"?, "error"?}; never the key."""
    k = key(home)
    if k is None:
        return {"set": False}
    try:
        return {"set": True, "user": _who(_ask(k, VIEWER))}
    except LinearError as e:
        return {"set": True, "user": None, "error": str(e)}


def save(home, api_key):
    """Keep a key once Linear takes it: a key Linear refuses is not kept."""
    api_key = api_key.strip() if isinstance(api_key, str) else api_key
    if not isinstance(api_key, str) or not _KEY.fullmatch(api_key):
        raise LinearError(f"A Linear API key is 8 to {MAX_KEY} printable characters, with no spaces.")
    user = _who(_ask(api_key, VIEWER))
    _write(home, {"key": api_key})
    return {"set": True, "user": user}


def remove(home):
    try:
        os.unlink(store_path(home))
    except FileNotFoundError:
        pass
    return {"set": False}


def _issue(node):
    """One issue as the page is given it; None for anything that is not one."""
    if not isinstance(node, dict) or not isinstance(node.get("identifier"), str) or not isinstance(node.get("title"), str):
        return None
    ident = node["identifier"]
    if not _IDENTIFIER.match(ident):
        return None
    text = node.get("description") if isinstance(node.get("description"), str) else ""
    url = node.get("url") if isinstance(node.get("url"), str) and node["url"].startswith("https://linear.app/") else None
    state = node.get("state") if isinstance(node.get("state"), dict) else {}
    team = node.get("team") if isinstance(node.get("team"), dict) else {}
    labels = ((node.get("labels") or {}).get("nodes") if isinstance(node.get("labels"), dict) else None) or []
    assignee = node.get("assignee") if isinstance(node.get("assignee"), dict) else {}
    word = lambda d, k, n=200: d[k][:n] if isinstance(d.get(k), str) else None  # noqa: E731
    return {"identifier": ident, "title": node["title"][:500], "description": text[:DESCRIPTION],
            "cut": len(text) > DESCRIPTION, "url": url, "state": word(state, "name"), "state_type": word(state, "type"),
            "team": word(team, "name"), "team_key": word(team, "key", 20), "assignee": word(assignee, "displayName"),
            "priority": word(node, "priorityLabel", 40), "updated_at": word(node, "updatedAt", 40),
            "labels": [l["name"][:60] for l in labels[:10] if isinstance(l, dict) and isinstance(l.get("name"), str)]}


def issues(home, search=None, limit=MAX_ISSUES):
    """The person's open issues, newest change first; or, with `search`, the open issues of their Linear workspace
    whose title or text holds it, or the one it names by its identifier (ENG-123)."""
    k = key(home)
    if k is None:
        raise LinearError("Add a Linear API key first, in Connections.")
    limit = max(1, min(int(limit), MAX_ISSUES))
    if search is not None and (not isinstance(search, str) or len(search) > MAX_SEARCH):
        raise LinearError(f"Search for at most {MAX_SEARCH} characters.")
    search = (search or "").strip()
    if not search:
        nodes = ((_ask(k, MINE, {"first": limit}).get("viewer") or {}).get("assignedIssues") or {}).get("nodes")
    else:
        named = _IDENTIFIER.match(search)
        if named:
            wanted = {"team": {"key": {"eq": named.group(1).upper()}}, "number": {"eq": int(named.group(2))}}
        else:
            wanted = {"state": _OPEN, "or": [{"title": {"containsIgnoreCase": search}},
                                             {"description": {"containsIgnoreCase": search}}]}
        nodes = (_ask(k, SEARCH, {"first": limit, "filter": wanted}).get("issues") or {}).get("nodes")
    out = [i for i in map(_issue, nodes if isinstance(nodes, list) else []) if i]
    return {"issues": out[:limit]}
