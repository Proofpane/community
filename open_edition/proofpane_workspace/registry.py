"""The official MCP registry (registry.modelcontextprotocol.io), searched: what a server is and how it can be run, as
an agent's Tools panel shows it (through the app) and as a run's search_mcp tool gives it. The registry is unvetted:
anyone can publish there. Only what an agent can be given is offered as a way to run it: an npm package over stdio, at
the version the entry names, or an address over http or sse; another registry's package is shown and not offered.

Standard library only, so the app and the workspace both use this one copy.
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://registry.modelcontextprotocol.io"
TIMEOUT = 15
MAX_REPLY = 2 * 1024 * 1024
MAX_QUERY = 200
MAX_CURSOR = 300
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class RegistryError(Exception):
    pass


def base():
    """The registry's address: a test's own, else the official one."""
    return os.environ.get("PP_MCP_REGISTRY") or BASE


def _text(value, most):
    return _CONTROL.sub(" ", value)[:most].strip() if isinstance(value, str) else ""


def _fields(given):
    """An entry's environment variables or headers: each with its name, whether it is secret or required, and what
    it is for."""
    out = []
    for f in given if isinstance(given, list) else []:
        if isinstance(f, dict) and isinstance(f.get("name"), str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", f["name"]):
            out.append({"name": f["name"], "secret": f.get("isSecret") is True, "required": f.get("isRequired") is True,
                        "description": _text(f.get("description"), 300)})
    return out[:20]


def row(entry):
    """One registry entry, as the panel shows it; None for one that is not an entry."""
    s = entry.get("server") if isinstance(entry, dict) else None
    if not isinstance(s, dict) or not isinstance(s.get("name"), str):
        return None
    ways = []
    for p in s.get("packages") if isinstance(s.get("packages"), list) else []:
        if not isinstance(p, dict):
            continue
        transport = (p.get("transport") or {}).get("type") if isinstance(p.get("transport"), dict) else "stdio"
        identifier, version = p.get("identifier"), p.get("version")
        if p.get("registryType") == "npm" and transport == "stdio" and isinstance(identifier, str) \
                and isinstance(version, str):
            ways.append({"kind": "package", "registry": "npm", "identifier": _text(identifier, 200),
                         "version": _text(version, 80), "env": _fields(p.get("environmentVariables"))})
        else:
            ways.append({"kind": "other", "registry": _text(p.get("registryType"), 40),
                         "identifier": _text(identifier, 200), "transport": _text(transport, 40)})
    for r in s.get("remotes") if isinstance(s.get("remotes"), list) else []:
        if not isinstance(r, dict) or not isinstance(r.get("url"), str):
            continue
        transport = {"streamable-http": "http", "http": "http", "sse": "sse"}.get(r.get("type"))
        if transport:
            ways.append({"kind": "address", "transport": transport, "url": _text(r["url"], 2000),
                         "headers": _fields(r.get("headers"))})
    repository = s.get("repository") if isinstance(s.get("repository"), dict) else {}
    return {"name": _text(s["name"], 200), "description": _text(s.get("description"), 500),
            "version": _text(s.get("version"), 80), "repository": _text(repository.get("url"), 300) or None,
            "ways": ways[:10]}


def search(query, cursor=None, limit=20):
    """{servers: [row], next: the cursor for more, or None}: the registry's latest version of each server matching
    `query`. RegistryError when it cannot be asked or does not answer as the registry does."""
    query = _text(query, MAX_QUERY)
    if not query:
        raise RegistryError("Search for something.")
    params = {"search": query, "limit": max(1, min(int(limit), 50)), "version": "latest"}
    if cursor:
        if not isinstance(cursor, str) or len(cursor) > MAX_CURSOR or _CONTROL.search(cursor):
            raise RegistryError("That is not a cursor the registry gave.")
        params["cursor"] = cursor
    request = urllib.request.Request(f"{base()}/v0/servers?{urllib.parse.urlencode(params)}",
                                     headers={"Accept": "application/json", "User-Agent": "proofpane-community"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as r:
            raw = r.read(MAX_REPLY + 1)
    except urllib.error.HTTPError as e:
        raise RegistryError(f"The registry answered {e.code}.") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise RegistryError(f"The registry could not be reached ({type(e).__name__}).") from None
    if len(raw) > MAX_REPLY:
        raise RegistryError("The registry's answer was too large.")
    try:
        doc = json.loads(raw)
    except ValueError:
        raise RegistryError("The registry's answer was not JSON.") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("servers"), list):
        raise RegistryError("The registry's answer was not a list of servers.")
    rows = [r for r in (row(e) for e in doc["servers"]) if r]
    meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    nxt = meta.get("nextCursor")
    return {"servers": rows, "next": nxt if isinstance(nxt, str) and len(nxt) <= MAX_CURSOR else None}


def entry(name):
    """The registry's latest entry named exactly `name`, or None."""
    for r in search(name, limit=20)["servers"]:
        if r["name"] == name:
            return r
    return None
