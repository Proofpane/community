"""Proofpane, as its routes under /api/v1/devices and /api/v1/mcp answer a device: pairing with a
code, a device's events, and approval requests and their answers, each request kept for the test to read. A stand-in on
127.0.0.1, for the tests of Proofpane's core joined as a plugin, and of the app's own pairing,
for which a second code pairs a second device (CODE_B), whose events it takes as the first's. It answers the check a
paired device asks before a start (/api/v1/devices/budget-check) as `budget` says: by default
every kind of spend allowed with no limit counted; `budget_answer(...)` for another answer; "down" for no answer."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

CODE = "ABC-DEF-GHJ"
TOKEN = "t0k3n_" + "q" * 37
DEVICE = "6f1c2d3e-0000-4000-8000-00000000abcd"
CODE_B = "BCD-EFG-HJK"
TOKEN_B = "t0k3n_" + "b" * 37
DEVICE_B = "6f1c2d3e-0000-4000-8000-00000000beef"
DEVICES = {CODE: (DEVICE, TOKEN), CODE_B: (DEVICE_B, TOKEN_B)}     # each code's device and its token


class Proofpane(BaseHTTPRequestHandler):
    asked = []
    hold = "approved"           # what an approval request comes to: approved, rejected, expired, pending, or "down"
    already = None              # a status the request's own answer carries (Proofpane's consume-once approvals)
    redirect = None             # an address every request is redirected to
    garbled = ()                # the paths answered with a line that is no HTTP status line at all
    deep = ()                   # the paths answered 200 with JSON nested deeper than Python's json can read
    slow = ()                   # the paths answered only after SLOW seconds: a Proofpane slow to answer
    SLOW = 4
    revoked = False             # True: the device's token refused, 401, as Proofpane refuses a revoked or paused one;
                                # "poll": refused only when the answer is asked for, as when it is revoked during a wait
    budget = None               # the budget check's answer: None, every kind allowed; a dict, that answer; "down", 503

    def log_message(self, *a):
        pass

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n)) if n else None

    def _garble(self):
        # what a broken proxy might answer: Python's client raises http.client.BadStatusLine, which is no URLError
        self.wfile.write(b"NOT-HTTP garbled\r\n\r\n")
        self.wfile.flush()
        self.close_connection = True

    def do_POST(self):
        path = urlsplit(self.path).path
        body = self._body()
        Proofpane.asked.append({"method": "POST", "path": path, "body": body, "agent": self.headers.get("User-Agent")})
        if path in Proofpane.slow:
            time.sleep(Proofpane.SLOW)
        if Proofpane.redirect:
            return self._redirect()
        if path in Proofpane.garbled:
            return self._garble()
        if path in Proofpane.deep:
            return self._deep()
        if path == "/api/v1/devices/pair":
            if body.get("code") in DEVICES:
                device, token = DEVICES[body["code"]]
                return self._answer(200, {"device_id": device, "token": token, "name": body.get("name"),
                                          "paired_at": "2026-10-18T01:02:03.456789Z"})
            if body.get("code") == "USE-DAG-AIN":
                return self._answer(400, {"detail": "Pairing code already used"})
            return self._answer(404, {"detail": "Invalid pairing code"})
        if ((body or {}).get("device_id"), (body or {}).get("token")) not in DEVICES.values() \
                or Proofpane.revoked is True:
            return self._answer(401, {"detail": "Invalid device credentials"})
        if path == "/api/v1/mcp/event":
            return self._answer(200, {"ok": True})
        if path == "/api/v1/devices/budget-check":
            if set(body) - {"device_id", "token"}:       # budget_check.py's CheckBody: extra="forbid"
                return self._answer(422, {"detail": "Extra inputs are not permitted"})
            if Proofpane.budget == "down":
                return self._answer(503, {"detail": "unavailable"})
            return self._answer(200, Proofpane.budget if isinstance(Proofpane.budget, dict) else budget_answer())
        if path == "/api/v1/mcp/hitl/request":
            if Proofpane.hold == "down":
                return self._answer(503, {"detail": "unavailable"})
            if len(body.get("arguments_preview") or "") > 2000:      # Proofpane's approval route takes at most 2000
                return self._answer(422, {"detail": "arguments_preview: at most 2000 characters"})
            return self._answer(200, {"request_id": "req-1", "status": Proofpane.already or "pending",
                                      "expires_at": "2026-10-18T01:07:03Z"})
        return self._answer(404, {"detail": "Not Found"})

    def do_GET(self):
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        Proofpane.asked.append({"method": "GET", "path": url.path, "query": query})
        if Proofpane.redirect:
            return self._redirect()
        if url.path == "/api/v1/mcp/hitl/req-1":
            if query.get("token") != [TOKEN] or query.get("device_id") != [DEVICE] or Proofpane.revoked:
                return self._answer(401, {"detail": "Invalid device credentials"})     # as Proofpane's route answers it
            return self._answer(200, {"request_id": "req-1", "status": Proofpane.hold, "tool_name": "x",
                                      "expires_at": "2026-10-18T01:07:03Z"})
        return self._answer(404, {"detail": "Not Found"})

    def _deep(self):
        raw = b"[" * 100000            # 100 kB, under what the clients read; json raises RecursionError, no ValueError
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _redirect(self):
        # 302: an opener that follows redirects follows it for a POST too (as a GET) and for a poll, the query and all
        self.send_response(302)
        self.send_header("Location", Proofpane.redirect)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _answer(self, code, value):
        raw = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def proofpane():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Proofpane)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    Proofpane.asked, Proofpane.hold, Proofpane.already, Proofpane.redirect, Proofpane.garbled = \
        [], "approved", None, None, ()
    Proofpane.deep, Proofpane.revoked, Proofpane.slow, Proofpane.budget = (), False, (), None
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


KINDS = ("subscription", "own_key", "gateway")     # the kinds of spend the product's check answers for


def budget_answer(refused=(), room=None, checked=True, unread=()):
    """An answer as budget_check.py gives one: for each kind of spend whether it may start, the room left where a
    blocking limit counts it (`room`: one figure for every kind, or a dict by kind; None, no limit counted), and for a
    kind in `refused` the product's own words for a person's spent budget (budget_check._words)."""
    kinds = {}
    for kind in KINDS:
        if kind in refused:
            kinds[kind] = {"allowed": False, "room_usd": 0.0,
                           "refused_by": {"scope_type": "user", "scope_id": "u-1", "period": "month",
                                          "spent_usd": 5.0, "limit_usd": 5.0},
                           "message": ("the person's budget is spent: 5.0000 of 5.0000 dollars this month; "
                                       "ask an admin for room")}
        else:
            r = room.get(kind) if isinstance(room, dict) else room
            kinds[kind] = {"allowed": True, "room_usd": r, "refused_by": None, "message": None}
    return {"organisation": "org-1", "person_id": "u-1", "department_id": None, "checked": checked,
            "unread": list(unread), "kinds": kinds}


def posted(path):
    """The bodies POSTed to `path`, in order."""
    return [a["body"] for a in Proofpane.asked if a["method"] == "POST" and a["path"] == path]
