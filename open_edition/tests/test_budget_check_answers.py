"""Proofpane's budget check as the workspace agent reads its answer (proofpane_workspace/budget_check.py): by the
rule the plugin's hook reads it by (core_plugin/hooks/proofpane_hook.py, prompt_check), so that a kind of
spend the product refuses is refused whatever else its answer carries, in the product's words where it gave some and in
the workspace's own where it gave none; a room no float holds is an answer not of the route's shape, never an error out
of the check, which raises nothing whatever breaks in reading an answer; and an answer whose body comes a byte at a time
is given up once the check's wait has passed and its connection closed, however long its bytes would keep coming.
Proofpane is the stand-in (proofpane_stand_in.py), and a local one here that sends its answer in pieces (_Pieces); no
real Proofpane, nothing beyond this machine."""

import json
import os
import select
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from proofpane_stand_in import DEVICE, TOKEN, Proofpane, budget_answer, proofpane  # noqa: F401  (a fixture)
from proofpane_workspace import budget_check

KIND = "subscription"
# the product's own words for a person's spent budget, as the stand-in gives them (budget_check._words)
WORDS = budget_answer(refused=(KIND,))["kinds"][KIND]["message"]
NO_WORDS = "Proofpane's budget check refused this start's kind of spend ({}), and gave no words for it."
NOT_A_CHECK = ("Proofpane's budget check could not be made: Proofpane's answer is not a budget check's. This start "
               "went on without it.")
GONE = object()                 # a field taken out of an answer (_answer)


def _pair(home, cloud):
    """The workspace's pairing as core_plugin.pair writes it."""
    folder = os.path.join(str(home), ".proofpane", "proofpane-core")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "config.json"), "w") as fh:
        json.dump({"cloud_url": cloud, "device_id": DEVICE, "token": TOKEN, "name": "ws", "paired_at": None,
                   "holds": []}, fh)


def _answer(allowed, top=None, entry=None, kind=KIND):
    """The stand-in's answer for a start of `kind`, refusing it in the product's words (`allowed` False) or letting it
    start, with its kind's fields changed as `entry` says and then its own as `top` says (GONE: taken out)."""
    answer = budget_answer(refused=() if allowed else (kind,))
    for fields, changes in ((answer["kinds"][kind], entry), (answer, top)):
        for name, value in (changes or {}).items():
            if value is GONE:
                fields.pop(name)
            else:
                fields[name] = value
    return answer


def _word(home, cloud, answer, kind=KIND):
    """The workspace's word on a start of `kind` (budget_check.check), paired with the stand-in answering `answer`."""
    _pair(home, cloud)
    Proofpane.budget = answer
    return budget_check.check(str(home), kind)


def _said(word):
    """What a Word says: (paired, allowed, room_usd, message, note)."""
    return word.paired, word.allowed, word.room_usd, word.message, word.note


# ---- a refusal, read by one rule with the plugin's hook --------------------------------------------------------------

@pytest.mark.parametrize("top", [{"unread": [None]}, {"unread": ["scopes", 7]}, {"unread": "scopes"}, {"unread": GONE},
                                 {"checked": False, "unread": [None]}],
                         ids=["unread a null", "a number among its names", "unread a word", "unread absent",
                              "not all read and unread a null"])
def test_a_refusal_stands_whatever_else_the_answer_carries(tmp_path, proofpane, top):
    word = _word(tmp_path, proofpane, _answer(False, top=top))
    assert _said(word) == (True, False, 0.0, WORDS, None), word


@pytest.mark.parametrize("room", ["0", -1, True, 10 ** 400, float("inf"), float("nan")],
                         ids=["a word", "below nothing", "a truth", "too large for a float", "infinite",
                              "not a number"])
def test_a_refusal_whose_room_is_off_its_shape_still_stands_with_no_room(tmp_path, proofpane, room):
    word = _word(tmp_path, proofpane, _answer(False, entry={"room_usd": room}))
    assert _said(word) == (True, False, None, WORDS, None), word


@pytest.mark.parametrize("message", [None, "", "   ", 5, GONE], ids=["null", "empty", "spaces", "a number", "absent"])
def test_a_refusal_with_no_words_is_refused_in_the_workspace_s_own(tmp_path, proofpane, message):
    word = _word(tmp_path, proofpane, _answer(False, entry={"message": message}))
    assert _said(word) == (True, False, 0.0, NO_WORDS.format(KIND), None), word


def test_the_workspace_s_words_for_a_refusal_name_the_start_s_kind_of_spend(tmp_path, proofpane):
    word = _word(tmp_path, proofpane, _answer(False, entry={"message": None}, kind="own_key"), kind="own_key")
    assert _said(word) == (True, False, 0.0, NO_WORDS.format("own_key"), None), word


@pytest.mark.parametrize("words", [WORDS, "  " + WORDS + "\n"], ids=["as given", "with spaces about it"])
def test_control_a_well_formed_refusal_is_refused_in_the_product_s_words_verbatim(tmp_path, proofpane, words):
    word = _word(tmp_path, proofpane, _answer(False, entry={"message": words}))
    assert _said(word) == (True, False, 0.0, words, None), word


@pytest.mark.parametrize("top, entry", [({"unread": [None]}, {}), ({"unread": "scopes"}, {}), ({"unread": GONE}, {}),
                                        ({}, {"room_usd": "5"}), ({}, {"room_usd": -1}), ({}, {"message": 5})],
                         ids=["unread a null", "unread a word", "unread absent", "room a word", "room below nothing",
                              "words a number"])
def test_control_with_room_an_answer_off_its_shape_is_still_not_a_budget_check_s(tmp_path, proofpane, top, entry):
    word = _word(tmp_path, proofpane, _answer(True, top=top, entry=entry))
    assert _said(word) == (True, True, None, None, NOT_A_CHECK), word


@pytest.mark.parametrize("top, entry", [({"checked": "no"}, {}), ({"checked": GONE}, {}), ({"kinds": [KIND]}, {}),
                                        ({"kinds": {KIND: "refused"}}, {}), ({}, {"allowed": None}),
                                        ({}, {"allowed": "false"}), ({}, {"allowed": 0}), ({}, {"allowed": GONE})],
                         ids=["checked a word", "checked absent", "kinds a list", "the kind's entry a word",
                              "allowed null", "allowed a word", "allowed a number", "allowed absent"])
def test_control_a_refusal_in_an_answer_whose_frame_is_off_is_not_a_budget_check_s(tmp_path, proofpane, top, entry):
    # the hook's frame (_kind_answer): `checked` a truth, `kinds` an object, the kind's entry one, its `allowed` a truth
    word = _word(tmp_path, proofpane, _answer(False, top=top, entry=entry))
    assert _said(word) == (True, True, None, None, NOT_A_CHECK), word


# ---- a room no float holds, and a check that never raises ------------------------------------------------------------

def test_a_room_too_large_for_a_float_is_an_answer_not_of_the_route_s_shape_and_the_start_goes_on(tmp_path,
                                                                                                    proofpane):
    word = _word(tmp_path, proofpane, _answer(True, entry={"room_usd": 10 ** 400}))
    assert _said(word) == (True, True, None, None, NOT_A_CHECK), word


@pytest.mark.parametrize("room, dollars", [(3, 3.0), (0, 0.0), (2.5, 2.5), (10 ** 300, 1e300)],
                         ids=["a whole number", "nothing", "a fraction", "a large whole number a float holds"])
def test_control_a_room_a_float_holds_is_read_as_its_dollars(tmp_path, proofpane, room, dollars):
    word = _word(tmp_path, proofpane, _answer(True, entry={"room_usd": room}))
    assert _said(word) == (True, True, dollars, None, None), word
    assert isinstance(word.room_usd, float), word


@pytest.mark.parametrize("where", ["_read", "_unread_note"])
def test_the_check_raises_nothing_where_reading_its_answer_breaks_and_the_start_goes_on(tmp_path, proofpane,
                                                                                       monkeypatch, where):
    def breaks(*args):
        raise RuntimeError("the reading of the answer broke")
    monkeypatch.setattr(budget_check, where, breaks)
    word = _word(tmp_path, proofpane, budget_answer(checked=False, unread=("scopes",)))
    assert _said(word) == (True, True, None, None, NOT_A_CHECK), word


# ---- an answer that comes in pieces ----------------------------------------------------------------------------------

class _Pieces(BaseHTTPRequestHandler):
    """A Proofpane that answers the check 200 with `body`, `piece` bytes at a time, `every` seconds apart, its length
    said by a Content-Length header or, `chunked`, by its chunks. It notes when the workspace closes its end of the
    connection (`closed`), and sends nothing more then, nor once the test has ended (`stop`)."""

    protocol_version = "HTTP/1.1"
    body, piece, every, chunked = b"", 1, 0.0, False
    closed = stop = None

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        p = _Pieces
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header(*(("Transfer-Encoding", "chunked") if p.chunked else ("Content-Length", str(len(p.body)))))
        self.end_headers()
        try:
            for at in range(0, len(p.body), p.piece):
                if not self._wait(p.every):
                    return
                piece = p.body[at:at + p.piece]
                self.wfile.write(b"%x\r\n%s\r\n" % (len(piece), piece) if p.chunked else piece)
            if p.chunked:
                self.wfile.write(b"0\r\n\r\n")
        except OSError:
            p.closed.set()                      # the workspace closed its end

    def _wait(self, seconds):
        """Whether to send the next piece, `seconds` from now: not once the test has ended. The workspace sends nothing
        after its request, so its end readable means it closed it (an end of the stream, or a reset)."""
        if select.select([self.connection], [], [], seconds)[0] and not self.connection.recv(1):
            raise ConnectionResetError
        return not _Pieces.stop.is_set()


@pytest.fixture
def pieces():
    _Pieces.body, _Pieces.piece, _Pieces.every, _Pieces.chunked = b"", 1, 0.0, False
    _Pieces.closed, _Pieces.stop = threading.Event(), threading.Event()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Pieces)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    _Pieces.stop.set()
    server.shutdown()
    server.server_close()


def test_an_answer_that_comes_a_byte_at_a_time_is_given_up_at_the_wait_and_its_connection_closed(pieces, monkeypatch):
    # 65536 bytes said, sent a byte every 0.2 seconds: each well inside the socket's own timeout (budget_check.WAIT, 1.0
    # here), and the whole of them some three and a half hours, which a read that rested on that timeout waits through
    monkeypatch.setattr(budget_check, "WAIT", 1.0)
    _Pieces.body, _Pieces.piece, _Pieces.every = b" " * 65536, 1, 0.2
    got = []
    exchange = threading.Thread(target=lambda: got.append(budget_check._exchange(
        {"cloud_url": pieces, "device_id": DEVICE, "token": TOKEN})), daemon=True)
    began = time.monotonic()
    exchange.start()
    exchange.join(4.0)
    took = time.monotonic() - began
    assert not exchange.is_alive(), f"the exchange still reading its answer after {took:.1f} seconds"
    assert got == [(None, "no answer came in 1 seconds")], got
    assert 1.0 <= took < 4.0, took
    assert _Pieces.closed.wait(2.0), "the exchange left its connection open"


REFUSAL = json.dumps(budget_answer(refused=(KIND,))).encode()


@pytest.mark.parametrize("chunked", [False, True], ids=["its length said", "in chunks"])
def test_control_an_answer_that_comes_in_pieces_within_the_wait_is_read_whole(tmp_path, pieces, chunked):
    _Pieces.body, _Pieces.piece, _Pieces.every, _Pieces.chunked = REFUSAL, 7, 0.01, chunked
    _pair(tmp_path, pieces)
    word = budget_check.check(str(tmp_path), KIND)
    assert _said(word) == (True, False, 0.0, WORDS, None), word


def _padded(size):
    """A refusal in the product's words as JSON of `size` bytes, made up to them by a field the check does not read."""
    answer = dict(budget_answer(refused=(KIND,)), pad="")
    answer["pad"] = "x" * (size - len(json.dumps(answer).encode()))
    raw = json.dumps(answer).encode()
    assert len(raw) == size
    return raw


@pytest.mark.parametrize("over, said", [(b"", (True, False, 0.0, WORDS, None)),
                                        (b" ", (True, True, None, None, NOT_A_CHECK))],
                         ids=["at its most", "a space over it"])
def test_control_an_answer_is_read_up_to_its_most_and_not_a_byte_over(tmp_path, pieces, over, said):
    # a space after the JSON: an answer read only to its most would read a whole answer, and take it
    _Pieces.body, _Pieces.piece = _padded(budget_check.MAX_ANSWER) + over, budget_check.MAX_ANSWER + 1
    _pair(tmp_path, pieces)
    word = budget_check.check(str(tmp_path), KIND)
    assert _said(word) == said, word


def test_an_answer_one_byte_over_its_most_whose_last_byte_comes_later_is_still_not_a_budget_check_s(tmp_path, pieces):
    # the cap is on the whole answer, not on what one read gets: here its first MAX_ANSWER bytes come together and its
    # last a moment later, so a reader that stops once it holds MAX_ANSWER bytes would take the answer they make
    _Pieces.body = _padded(budget_check.MAX_ANSWER) + b" "
    _Pieces.piece, _Pieces.every = budget_check.MAX_ANSWER, 0.2
    _pair(tmp_path, pieces)
    word = budget_check.check(str(tmp_path), KIND)
    assert _said(word) == (True, True, None, None, NOT_A_CHECK), word


def test_an_answer_that_comes_a_byte_at_a_time_holds_the_check_no_longer_than_its_wait(tmp_path, pieces, monkeypatch):
    # each byte well inside the socket's own timeout (budget_check.WAIT, 1.0 here), so only the start's own wait for the
    # exchange (_ask) ends the check at WAIT: the exchange, left behind, gives up a byte later, nearly twice WAIT in all
    monkeypatch.setattr(budget_check, "WAIT", 1.0)
    _Pieces.body, _Pieces.piece, _Pieces.every = REFUSAL, 1, 0.9
    _pair(tmp_path, pieces)
    began = time.monotonic()
    word = budget_check.check(str(tmp_path), KIND)
    took = time.monotonic() - began
    note = "Proofpane's budget check could not be made: no answer came in 1 seconds. This start went on without it."
    assert _said(word) == (True, True, None, None, note), word
    assert took < 1.6, took
