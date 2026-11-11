"""What claude is writing, as it writes it (chat.py's _stream): sent to the pages listening on the chat socket, never
written to the log, where each block is still written whole when it ends; gathered between sends, each piece saying
where in its block it starts, as a page counts; and relayed by the app re-made from its known parts."""

import asyncio
import json
import time

import pytest

from proofpane_workspace import chat as chats
from test_workspace_chat import _chat, _kinds, _runs, _until, cagent  # noqa: F401  (cagent is a fixture)
from workspace_fixtures import SECRET

pytestmark = pytest.mark.skipif(not chats.available(), reason="needs the Claude Agent SDK (requirements.txt)")

E_ACUTE, GRIN = chr(0xE9), chr(0x1F600)   # one UTF-16 unit, and two: a page counts in UTF-16 units


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def _start(kind, **block):
    return {"type": "content_block_start", "index": 0, "content_block": dict(block, type=kind)}


def _text(t):
    return {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": t}}


def test_text_is_gathered_sent_in_order_and_counted_as_a_page_counts(tmp_path):
    q = asyncio.Queue()
    c = chats.Chat("a" * 12, str(tmp_path), str(tmp_path), {}, "claude", listeners={q})

    async def go():
        c._stream(_start("text", text=""))
        for piece in (E_ACUTE, GRIN, "x"):
            c._stream(_text(piece))
        assert q.empty()                        # gathered, not yet sent
        await asyncio.sleep(chats.STREAM_EVERY * 4)
        c._stream(_text("y"))
        c._emit("text", text=E_ACUTE + GRIN + "xy")    # what was gathered reaches the page before what is logged
        await asyncio.sleep(chats.STREAM_EVERY * 2)

    asyncio.run(go())
    got = _drain(q)
    assert got[0] == ("a" * 12, {"after": 0, "block": 1, "offset": 0, "text": E_ACUTE + GRIN + "x"}, "delta")
    assert got[1] == ("a" * 12, {"after": 0, "block": 1, "offset": 4, "text": "y"}, "delta")
    assert len(got[2]) == 2 and got[2][1]["kind"] == "text" and got[2][1]["seq"] == 1
    assert len(got) == 3                        # nothing more: the timer that was waiting was cancelled
    with open(tmp_path / "chat.jsonl") as fh:   # the log has the logged event only
        assert [json.loads(line)["kind"] for line in fh] == ["text"]


def test_a_block_of_thinking_or_of_a_tool_call_is_said_as_it_begins_and_a_new_block_counts_from_nothing(tmp_path):
    q = asyncio.Queue()
    c = chats.Chat("b" * 12, str(tmp_path), str(tmp_path), {}, "claude", listeners={q})

    async def go():
        c._stream(_start("thinking", thinking=""))
        c._stream({"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "x"}})
        c._stream(_start("text", text=""))
        c._stream(_text("abc"))
        c._stream(_start("tool_use", id="t1", name="Bash\nnow", input={}))   # sends what was gathered first
        c._stream(_start("text", text=""))
        c._stream(_text("d"))
        c._stream({"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta",
                                                                         "partial_json": "{}"}})
        for junk in (None, [], {"type": "content_block_delta", "delta": "x"}, {"type": "content_block_start"},
                     {"type": "content_block_delta", "delta": {"type": "text_delta", "text": 5}}):
            c._stream(junk)
        await asyncio.sleep(chats.STREAM_EVERY * 3)

    asyncio.run(go())
    got = [d for _, d, _ in _drain(q)]
    assert got == [{"after": 0, "block": 1, "thinking": True},
                   {"after": 0, "block": 2, "offset": 0, "text": "abc"},
                   {"after": 0, "block": 3, "tool": "Bash now"},
                   {"after": 0, "block": 4, "offset": 0, "text": "d"}]


def test_a_page_that_has_fallen_behind_misses_the_writing_and_is_not_dropped_for_it(tmp_path):
    full, fine = asyncio.Queue(), asyncio.Queue()
    for _ in range(chats.MAX_QUEUED):
        full.put_nowait(("x", {}))
    c = chats.Chat("c" * 12, str(tmp_path), str(tmp_path), {}, "claude", listeners={full, fine})

    async def go():
        c._stream(_start("text", text=""))
        c._stream(_text("hi"))
        await asyncio.sleep(chats.STREAM_EVERY * 3)

    asyncio.run(go())
    assert full.qsize() == chats.MAX_QUEUED and full in c.listeners
    assert [d for _, d, _ in _drain(fine)] == [{"after": 0, "block": 1, "offset": 0, "text": "hi"}]


def _socket(agent):
    from websockets.sync.client import connect
    ws = connect(f"ws://127.0.0.1:{agent.port}/chat", additional_headers={"Authorization": "Bearer " + SECRET},
                 open_timeout=10)
    assert json.loads(ws.recv(timeout=10)) == {"type": "ready"}
    return ws


def _listen(ws, sid, until, timeout=30):
    """The socket's messages for one session until `until(message)` says so."""
    got, deadline = [], time.time() + timeout
    while True:
        msg = json.loads(ws.recv(timeout=max(0.1, deadline - time.time())))
        if msg.get("session") == sid:
            got.append(msg)
            if until(msg):
                return got


def _idle(msg):
    return msg["type"] == "event" and msg["event"]["kind"] == "status" and msg["event"]["state"] == "idle"


def test_a_chat_s_writing_reaches_the_socket_as_claude_writes_it_and_the_log_is_unchanged(cagent):
    words = "the quick brown fox jumps over the lazy dog"
    ws = _socket(cagent)
    try:
        s = _chat(cagent, "stream " + words)
        got = _listen(ws, s["id"], _idle)
    finally:
        ws.close()
    deltas = [m["delta"] for m in got if m["type"] == "delta"]
    texts = [d for d in deltas if "text" in d]
    assert deltas[0] == {"after": 3, "block": 1, "thinking": True}     # after the user, status and session events
    assert "".join(d["text"] for d in texts) == words and "aside" not in json.dumps(deltas)   # no sub-agent's
    assert [d["offset"] for d in texts] == [sum(len(x["text"]) for x in texts[:i]) for i in range(len(texts))]
    assert {d["block"] for d in texts} == {2} and {d["after"] for d in texts} == {3}
    assert 1 <= len(texts) <= 15                # gathered: the stand-in wrote it in 15 pieces
    # every piece came before the block was logged, and the log is what it always was
    kinds = [m["type"] if m["type"] == "delta" else m["event"]["kind"] for m in got]
    assert kinds.index("text") > max(i for i, k in enumerate(kinds) if k == "delta")
    log = _until(cagent, s["id"], lambda log: log["state"] == "idle")
    assert _kinds(log) == [("user", "stream " + words), ("status", "running"), ("session", None), ("text", words),
                           ("result", "success"), ("status", "idle")]
    assert "--include-partial-messages" in _runs(cagent)[0]["args"]


def test_a_tool_call_is_said_as_it_begins_before_its_permission_is_asked(cagent):
    ws = _socket(cagent)
    try:
        s = _chat(cagent, "run echo hi")
        got = _listen(ws, s["id"], lambda m: m["type"] == "event" and m["event"]["kind"] == "ask")
    finally:
        ws.close()
    kinds = [("delta", m["delta"].get("tool")) if m["type"] == "delta" else m["event"]["kind"] for m in got]
    assert kinds.index(("delta", "Bash")) < kinds.index("tool") < kinds.index("ask")


def test_the_app_passes_on_what_claude_writes_re_made_from_its_known_parts():
    from proofpane_open.ade import workspace_client as client
    sid = "a" * 12

    def relay(delta, session=sid):
        out = client.chat_message(json.dumps({"type": "delta", "session": session, "delta": delta, "extra": 1}))
        return None if out is None else json.loads(out)

    piece = {"after": 3, "block": 2, "offset": 4, "text": "fox" + GRIN, "thinking": True, "x": 1}
    assert relay(piece) == {"type": "delta", "session": sid,
                            "delta": {"after": 3, "block": 2, "offset": 4, "text": "fox" + GRIN}}
    assert relay({"after": 0, "block": 1, "thinking": True})["delta"] == {"after": 0, "block": 1, "thinking": True}
    assert relay({"after": 0, "block": 1, "tool": "B" * 500})["delta"] == {"after": 0, "block": 1, "tool": "B" * 200}
    for bad in ({"after": True, "block": 1, "thinking": True}, {"after": -1, "block": 1, "thinking": True},
                {"block": 1, "thinking": True}, {"after": 0, "block": "1", "thinking": True},
                {"after": 0, "block": 1, "text": "x"}, {"after": 0, "block": 1, "thinking": "yes"},
                {"after": 0, "block": 1}, "text"):
        assert relay(bad) is None, bad
    assert relay({"after": 0, "block": 1, "thinking": True}, session="../x") is None
