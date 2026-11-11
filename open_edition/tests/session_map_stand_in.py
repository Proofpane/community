"""A stand-in for the workspace agent's two map routes; test_workspace_map_served.py runs the same routes against the
real agent.

It answers in the map's shapes exactly, with their examples (`ui/src/sessionMapExamples.json`, which the
page's tests make their fixtures from too), for the sessions the real workspace agent behind the same endpoint has: each
is asked of that agent first, so a call that reached another person's workspace finds none of this one's sessions, and
that agent's own 404 is the answer. The sessions it lists take the examples' sessions in turn, so a second one is
the case of an effort that is a token budget, and of agents left out (`agents_more`). Every other call goes to the real
agent unchanged."""

import copy
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "..", "ui", "src", "sessionMapExamples.json"), encoding="utf-8") as fh:
    EXAMPLES = json.load(fh)
MAP = EXAMPLES["map"]
SESSION_MAP = EXAMPLES["session_map"]
_ONE = re.compile(r"^/sessions/([0-9a-f]{12})/map$")


def install(monkeypatch, workspace_client):
    """Put the stand-in in front of `workspace_client.call`; the list of (endpoint's address, method, path) of every
    call the app made to a workspace from then on, in order, the map's and any other."""
    real = workspace_client.call
    asked = []

    def call(endpoint, method, path, body=None, timeout=30):
        asked.append((endpoint.http, method, path))
        one = _ONE.match(path)
        if method == "GET" and path == "/map":
            listed = real(endpoint, "GET", "/sessions").get("sessions", [])
            answer = copy.deepcopy(MAP)
            nodes = answer["sessions"]
            open_ = [s for s in listed if not s.get("closed")]
            answer["sessions"] = [dict(copy.deepcopy(nodes[n % len(nodes)]), id=s["id"]) for n, s in enumerate(open_)]
            return answer
        if method == "GET" and one:
            real(endpoint, "GET", f"/sessions/{one.group(1)}")      # the agent's own 404 for a session it has not
            return dict(copy.deepcopy(SESSION_MAP), session=one.group(1))
        return real(endpoint, method, path, body, timeout=timeout)

    monkeypatch.setattr(workspace_client, "call", call)
    return asked
