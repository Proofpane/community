"""A stand-in for the Anthropic Messages API, for running a real `claude` with no account against "another provider's
endpoint": every POST to /v1/messages is answered "done" (streamed when asked), and each request's path, model and
the credential it carried (its Authorization and X-Api-Key headers) are written, a JSON line each, to STANDIN_LOG.

    python anthropic_stand_in.py [PORT]      prints the port it listens on (0.0.0.0), then serves until killed
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = os.environ.get("STANDIN_LOG", "anthropic-standin.log")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _log(self, body):
        with open(LOG, "a") as fh:
            fh.write(json.dumps({"path": self.path, "model": body.get("model"),
                                 "authorization": self.headers.get("Authorization"),
                                 "x_api_key": self.headers.get("X-Api-Key")}) + "\n")

    def do_GET(self):
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        self._log(body if isinstance(body, dict) else {})
        if not self.path.startswith("/v1/messages") or self.path.startswith("/v1/messages/count_tokens"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"input_tokens": 10}).encode())
            return
        model = body.get("model", "x")
        usage = {"input_tokens": 10, "output_tokens": 1}
        if not body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"id": "msg_1", "type": "message", "role": "assistant", "model": model,
                                         "content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn",
                                         "stop_sequence": None, "usage": usage}).encode())
            return
        events = [("message_start", {"type": "message_start", "message": {
                      "id": "msg_1", "type": "message", "role": "assistant", "model": model, "content": [],
                      "stop_reason": None, "stop_sequence": None, "usage": usage}}),
                  ("content_block_start", {"type": "content_block_start", "index": 0,
                                           "content_block": {"type": "text", "text": ""}}),
                  ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                           "delta": {"type": "text_delta", "text": "done"}}),
                  ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                  ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                                     "usage": {"output_tokens": 1}}),
                  ("message_stop", {"type": "message_stop"})]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for name, data in events:
            self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
        self.wfile.flush()


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1]) if len(sys.argv) > 1 else 0), Handler)
    print(srv.server_address[1], flush=True)
    srv.serve_forever()
