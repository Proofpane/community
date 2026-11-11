"""A stand-in for the Messages API, run inside the workspace container so the check reaches it on the container's own
loopback (ANTHROPIC_BASE_URL=http://127.0.0.1:PORT) and works the same on a Mac and on Linux, where
host.docker.internal does not resolve. It answers each request that offers tools and carries no tool result with one
tool call, and every other request with the text "done"; it writes each request's body, one JSON line, to STANDIN_LOG,
so the test can read with docker exec whether the tool's output (a marker in the sign-in file) ever came back as a
tool result.

The tool it asks for is set by the environment: STANDIN_TOOL is "Read" (its file_path is STANDIN_PATH, the default a
sign-in file) or "Bash" (its command is STANDIN_COMMAND). Read is a plain file read, which a CLAUDE_CODE_SHELL_PREFIX
does not touch; a Bash command that same prefix wraps, so /bin/true leaves it with no output.

    python3 messages_stand_in.py        binds 127.0.0.1:0, writes the port to STANDIN_PORTFILE, serves until killed

It is the in-container twin of _FakeMessages in test_workspace_image_docker.py; it needs only the standard library.
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = os.environ.get("STANDIN_LOG", "messages-standin.log")
PORTFILE = os.environ.get("STANDIN_PORTFILE", "")
TOOL = os.environ.get("STANDIN_TOOL", "Read")
PATH = os.environ.get("STANDIN_PATH", "/home/ws/.claude/.credentials.json")
COMMAND = os.environ.get("STANDIN_COMMAND", "cat ~/.claude/.credentials.json")


def _tool_use():
    if TOOL == "Bash":
        return {"type": "tool_use", "id": "toolu_pp_1", "name": "Bash",
                "input": {"command": COMMAND, "description": "run it"}}
    return {"type": "tool_use", "id": "toolu_pp_1", "name": "Read", "input": {"file_path": PATH}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(body if isinstance(body, dict) else {}) + "\n")
        if "count_tokens" in self.path:
            return self._json({"input_tokens": 10})
        results = [c for m in body.get("messages", []) if isinstance(m.get("content"), list)
                   for c in m["content"] if isinstance(c, dict) and c.get("type") == "tool_result"]
        if body.get("tools") and not results:
            blocks = [_tool_use()]
            stop = "tool_use"
        else:
            blocks, stop = [{"type": "text", "text": "done"}], "end_turn"
        if body.get("stream"):
            self._stream(blocks, stop)
        else:
            self._json({"id": "msg_pp", "type": "message", "role": "assistant", "model": "fake",
                        "content": blocks, "stop_reason": stop, "stop_sequence": None,
                        "usage": {"input_tokens": 10, "output_tokens": 5}})

    def _json(self, obj):
        raw = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _stream(self, blocks, stop):
        events = [("message_start", {"type": "message_start", "message": {
            "id": "msg_pp", "type": "message", "role": "assistant", "model": "fake", "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 1}}})]
        for i, b in enumerate(blocks):
            if b["type"] == "text":
                events.append(("content_block_start", {"type": "content_block_start", "index": i,
                                                       "content_block": {"type": "text", "text": ""}}))
                events.append(("content_block_delta", {"type": "content_block_delta", "index": i,
                                                       "delta": {"type": "text_delta", "text": b["text"]}}))
            else:
                events.append(("content_block_start", {"type": "content_block_start", "index": i,
                                                       "content_block": dict(b, input={})}))
                events.append(("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {
                    "type": "input_json_delta", "partial_json": json.dumps(b["input"])}}))
            events.append(("content_block_stop", {"type": "content_block_stop", "index": i}))
        events.append(("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop,
                                                                            "stop_sequence": None},
                                         "usage": {"output_tokens": 5}}))
        events.append(("message_stop", {"type": "message_stop"}))
        raw = "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    if PORTFILE:
        tmp = PORTFILE + ".tmp"
        with open(tmp, "w", encoding="ascii") as fh:
            fh.write(str(port))
        os.replace(tmp, PORTFILE)
    print(port, flush=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Event().wait()
