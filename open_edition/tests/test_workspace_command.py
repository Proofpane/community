"""proofpane, the command an agent uses to ask the person for a session (proofpane_workspace/command.py), against a stand-in
for the workspace agent: what it sends, with the session's own token, and what it says back."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from proofpane_workspace import command as pp


class Workspace(BaseHTTPRequestHandler):
    seen = []
    polls = 0
    answer = "started"
    accepted = ("codex",)       # the agents whose asks it takes
    asked = {}                  # laid over the ask /ask/asks gives (a claude ask's efforts)
    listing = None              # the asks /ask/asks gives instead, when set
    states = None               # the state each look at /ask/asks finds, in turn, the last one after, when set
    delay = 0                   # seconds it takes to answer an ask (a first read of its claude)
    sessions = [{"id": "0123456789ab", "program": "claude", "state": "running", "task": "Build it\nall", "you": True},
                {"id": "ba9876543210", "program": "codex", "state": "idle", "task": "Write the tests"}]

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.seen.append(("GET", self.path, self.headers.get("Authorization"), None))
        if self.headers.get("Authorization") != "Bearer tok-0123456789abcdefghij":
            return self._send(401, {"detail": "This session's token is not one."})
        if self.path == "/ask/sessions":
            return self._send(200, {"sessions": self.sessions})
        if self.path == "/ask/sessions/ba9876543210":
            return self._send(200, dict(self.sessions[1], waits_for=None))
        if self.path == "/ask/sessions/ba9876543210/diff":
            return self._send(200, {"diff": "diff --git a/x b/x\n", "too_large": False, "files": ["x"]})
        if self.path == "/ask/asks":
            if Workspace.listing is not None:
                return self._send(200, {"asks": Workspace.listing})
            Workspace.polls += 1
            # the first look finds it waiting, the next one answered as the stand-in was told to answer it; or each look
            # the next of the states it was told
            state = "waiting" if Workspace.polls < 2 else Workspace.answer
            if Workspace.states is not None:
                state = Workspace.states[min(Workspace.polls, len(Workspace.states)) - 1]
            return self._send(200, {"asks": [dict({"id": "a1b2c3d4e5f6", "program": "codex",
                                                   "task": "Write the parser's tests", "state": state, "at": 1,
                                                   "session": "fedcba987654" if state == "started" else None},
                                                  **Workspace.asked)]})
        return self._send(404, {"detail": "No such session."})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        self.seen.append(("POST", self.path, self.headers.get("Authorization"), body))
        if Workspace.delay:
            time.sleep(Workspace.delay)
        if body.get("program") in Workspace.accepted:
            return self._send(200, {"id": "a1b2c3d4e5f6", "program": body["program"], "task": body["task"],
                                    "effort": body.get("effort")})
        return self._send(400, {"detail": "This session has 3 asks waiting for the person already."})

    def log_message(self, *args):
        pass


@pytest.fixture
def workspace(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), Workspace)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("PP_ASK_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("PP_ASK_TOKEN", "tok-0123456789abcdefghij")
    Workspace.seen = []
    Workspace.polls = 0
    Workspace.answer = "started"
    Workspace.accepted = ("codex",)
    Workspace.asked = {}
    Workspace.listing = None
    Workspace.states = None
    Workspace.delay = 0
    yield Workspace
    server.shutdown()


def test_it_lists_reads_and_diffs_with_the_session_s_token(workspace, capsys):
    assert pp.main(["sessions"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("0123456789ab  claude") and out[0].endswith("(this session)")
    assert "Write the tests" in out[1] and "\n" not in out[0]
    assert pp.main(["status", "ba9876543210"]) == 0 and "codex" in capsys.readouterr().out
    assert pp.main(["diff", "ba9876543210"]) == 0 and capsys.readouterr().out == "diff --git a/x b/x\n"
    assert all(auth == "Bearer tok-0123456789abcdefghij" for _, _, auth, _ in workspace.seen)


def test_an_ask_says_the_person_decides_and_a_refusal_says_why(workspace, capsys):
    assert pp.main(["ask", "--agent", "codex", "--task", "Write the parser's tests", "--base", "main"]) == 0
    assert "the person decides in the app" in capsys.readouterr().out
    assert workspace.seen[-1] == ("POST", "/ask/sessions", "Bearer tok-0123456789abcdefghij",
                                  {"program": "codex", "task": "Write the parser's tests", "base": "main"})
    assert pp.main(["ask", "--agent", "claude", "--task", "x"]) == 1
    assert "3 asks waiting" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        pp.main(["ask", "--agent", "shell", "--task", "x"])          # not an agent it may ask for


def test_without_its_token_it_says_why_and_does_nothing(monkeypatch, capsys):
    monkeypatch.delenv("PP_ASK_URL", raising=False)
    monkeypatch.delenv("PP_ASK_TOKEN", raising=False)
    assert pp.main(["sessions"]) == 1
    assert "has let agents ask for sessions" in capsys.readouterr().err


@pytest.mark.parametrize("answer, code, said", [
    ("started", 0, "Started by the person as session fedcba987654"),
    ("refused", 3, "The person refused it."),
    ("gone", 3, "It is gone: not answered within a day, or withdrawn."),
])
def test_an_ask_can_wait_for_the_person_s_answer(workspace, capsys, monkeypatch, answer, code, said):
    monkeypatch.setattr(pp.time, "sleep", lambda s: None)
    workspace.answer = answer
    assert pp.main(["ask", "--agent", "codex", "--task", "Write the parser's tests", "--wait"]) == code
    assert said in capsys.readouterr().out
    assert [p for m, p, _, _ in workspace.seen] == ["/ask/sessions", "/ask/asks", "/ask/asks"]


def test_an_ask_waited_for_waits_while_the_person_is_starting_it(workspace, capsys, monkeypatch):
    # a Start can take minutes, and the workspace says the ask is starting meanwhile (were it said gone, the wait would
    # end there, exit 3, for an ask a refused Start then put back): the command waits on through it, for the person's
    # answer
    monkeypatch.setattr(pp.time, "sleep", lambda s: None)
    workspace.states = ["waiting", "starting", "starting", "started"]
    assert pp.main(["ask", "--agent", "codex", "--task", "Write the parser's tests", "--wait"]) == 0
    assert "Started by the person as session fedcba987654" in capsys.readouterr().out
    assert [p for m, p, _, _ in workspace.seen] == ["/ask/sessions"] + ["/ask/asks"] * 4


def test_this_session_s_asks_say_in_their_help_what_starting_is(workspace, capsys):
    # the state an ask a Start holds is in, in the command's own words: the person is starting it,
    # and it waits again if that start fails
    with pytest.raises(SystemExit):
        pp.main(["asks", "--help"])
    assert "starting, while the person is starting it (it waits again if that start fails)" in \
        " ".join(capsys.readouterr().out.split())
    workspace.listing = [{"id": "a1b2c3d4e5f6", "program": "codex", "task": "Other", "state": "starting", "at": 4,
                          "session": None}]
    assert pp.main(["asks"]) == 0
    assert capsys.readouterr().out.split() == ["a1b2c3d4e5f6", "starting", "codex", "-", "Other"]


def test_an_ask_waited_for_too_long_says_where_its_answer_will_be(workspace, capsys, monkeypatch):
    clock = iter([0.0, 0.0, 100.0, 200.0])
    monkeypatch.setattr(pp.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(pp.time, "sleep", lambda s: None)
    workspace.answer = "waiting"
    assert pp.main(["ask", "--agent", "codex", "--task", "x", "--wait", "--timeout", "50"]) == 1
    assert "`proofpane asks` shows it" in capsys.readouterr().err


def test_it_lists_this_session_s_asks(workspace, capsys):
    workspace.polls = 5
    assert pp.main(["asks"]) == 0
    assert capsys.readouterr().out.split() == ["a1b2c3d4e5f6", "started", "codex", "fedcba987654", "Write", "the",
                                               "parser's", "tests"]


# an ask's effort: the command sends what it is given, and the workspace judges it (asks.py, workspace.ask)

def test_an_ask_sends_its_effort_as_given_and_only_when_given(workspace, capsys):
    workspace.accepted = ("codex", "claude")
    assert pp.main(["ask", "--agent", "claude", "--effort", "max", "--task", "Review it"]) == 0
    assert workspace.seen[-1][3] == {"program": "claude", "task": "Review it", "base": None, "effort": "max"}
    # another agent's too: the workspace, which knows its claude's levels, is what refuses it, whatever posts the ask
    assert pp.main(["ask", "--agent", "codex", "--effort", "max", "--task", "Review it"]) == 0
    assert workspace.seen[-1][3]["effort"] == "max"
    assert pp.main(["ask", "--agent", "claude", "--task", "Review it"]) == 0
    assert "effort" not in workspace.seen[-1][3]


def test_this_session_s_asks_say_a_claude_ask_s_effort_asked_and_the_one_it_started_with(workspace, capsys):
    workspace.listing = [
        {"id": "a1b2c3d4e5f6", "program": "claude", "task": "Review it", "state": "started", "at": 1,
         "session": "fedcba987654", "effort": "max", "started_effort": "low"},
        {"id": "b1b2c3d4e5f6", "program": "claude", "task": "Check it", "state": "started", "at": 2,
         "session": "fedcba987655", "effort": None, "started_effort": None},
        {"id": "c1b2c3d4e5f6", "program": "claude", "task": "Wait", "state": "waiting", "at": 3, "session": None,
         "effort": "high", "started_effort": None},
        {"id": "d1b2c3d4e5f6", "program": "codex", "task": "Other", "state": "refused", "at": 4, "session": None,
         "effort": None, "started_effort": None}]
    assert pp.main(["asks"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].endswith("Review it  (effort asked: max, started: low)")
    assert lines[1].endswith("Check it  (effort asked: none, started: default)")
    assert lines[2].endswith("Wait  (effort asked: high)")
    assert lines[3].split() == ["d1b2c3d4e5f6", "refused", "codex", "-", "Other"]      # another agent's names none
    assert pp.main(["--json", "asks"]) == 0
    assert json.loads(capsys.readouterr().out)["asks"] == workspace.listing


def test_an_ask_waited_for_says_the_effort_its_session_started_with(workspace, capsys, monkeypatch):
    monkeypatch.setattr(pp.time, "sleep", lambda s: None)
    workspace.accepted = ("codex", "claude")
    workspace.asked = {"program": "claude", "effort": "max", "started_effort": "xhigh"}
    assert pp.main(["ask", "--agent", "claude", "--effort", "max", "--task", "Review it", "--wait"]) == 0
    assert "Started by the person as session fedcba987654 (effort asked: max, started: xhigh): `proofpane wait " \
           "fedcba987654` waits for its turn to end." in capsys.readouterr().out
    workspace.polls = 0
    assert pp.main(["--json", "ask", "--agent", "claude", "--effort", "max", "--task", "Review it", "--wait"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert (got["effort"], got["started_effort"], got["session"]) == ("max", "xhigh", "fedcba987654")


def test_an_ask_the_workspace_does_not_answer_in_time_says_it_may_still_be_taken(workspace, capsys, monkeypatch):
    # a first read of what the workspace's Claude Code offers, for an ask that names an effort, can outlast the wait,
    # and the workspace may take the ask after it: the command says so, and
    # where to look before asking again, not that the workspace could not be reached
    monkeypatch.setattr(pp, "ASK_TIMEOUT", 0.5, raising=False)
    workspace.accepted = ("claude",)
    workspace.delay = 2.0
    assert pp.main(["ask", "--agent", "claude", "--effort", "max", "--task", "Review it"]) == 1
    # where to look, and, when the ask is not there, to ask again: the same ask asked while the first is still being
    # taken is refused in words saying so, and an ask the workspace refused, which `proofpane asks` never shows, is
    # refused again with its reason, so an agent that does what it is told does not wait for an ask that will not come.
    # And they say that the same ask once the first is taken is not refused for being the same: it is a second ask, so
    # a first taken between the agent's look and its asking again leaves two (words saying that nothing is asked twice
    # would be untrue)
    assert capsys.readouterr().err.splitlines()[0] == (
        "proofpane: the workspace did not answer within 0.5 seconds. It may still take your ask: look at `proofpane "
        "asks`, and if it is not there, ask again. The same ask, asked while the first is still being taken, is "
        "refused in words saying so, and an ask the workspace refused is refused again, with its reason; but if the "
        "first was taken after you looked, the same ask is not refused for being the same.")
    assert workspace.seen[-1][3] == {"program": "claude", "task": "Review it", "base": None, "effort": "max"}


def test_a_workspace_the_command_cannot_connect_to_in_time_is_not_said_to_have_the_ask(workspace, capsys, monkeypatch):
    # urllib gives a connection, or the sending of a request, that times out as a URLError whose reason is the timeout
    # (measured with Python 3.13: a listening socket whose queue is full), and an answer that does not come once the
    # request is sent as the timeout itself, the case above. The first never reached the workspace, so the command
    # says it could not be reached, and not that the workspace may still take the ask
    import urllib.error

    def timed_out(request, timeout):
        raise urllib.error.URLError(TimeoutError("timed out"))
    monkeypatch.setattr(pp.urllib.request, "urlopen", timed_out)
    assert pp.main(["ask", "--agent", "claude", "--effort", "max", "--task", "Review it"]) == 1
    err = capsys.readouterr().err
    assert err.splitlines()[0] == "proofpane: the workspace could not be reached: <urlopen error timed out>"
    assert "may still take" not in err


def test_a_workspace_that_refuses_the_connection_is_not_said_to_have_the_ask(monkeypatch, capsys):
    # nothing listening where the workspace's address points: urllib's refused connection is said as a workspace that
    # could not be reached, never as one that did not answer in time and may still take the ask
    import socket
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))           # bound and not listening: a connection to it is refused
    try:
        monkeypatch.setenv("PP_ASK_URL", f"http://127.0.0.1:{closed.getsockname()[1]}")
        monkeypatch.setenv("PP_ASK_TOKEN", "tok-0123456789abcdefghij")
        assert pp.main(["ask", "--agent", "claude", "--effort", "max", "--task", "Review it"]) == 1
        err = capsys.readouterr().err
    finally:
        closed.close()
    assert err.startswith("proofpane: the workspace could not be reached: "), err
    assert "did not answer" not in err and "may still take" not in err
