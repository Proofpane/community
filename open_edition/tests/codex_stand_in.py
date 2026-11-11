"""A stand-in for the OpenAI Responses API, for running a real `codex`, `grok` or `qwen` with no account: the first turn calls the
shell tool the request offers with STANDIN_COMMAND, every later turn says "done". Each request's tool names and the
last tool output Codex sent back are written, a JSON line each, to STANDIN_LOG. STANDIN_HEADERS, a JSON object, adds
headers to every answer (the rate-limit headers a subscription's answers carry, for one).

    python codex_stand_in.py [PORT]      prints the port it listens on (127.0.0.1), then serves until killed
"""

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

LOG = os.environ.get("STANDIN_LOG", "standin.log")
COMMAND = os.environ.get("STANDIN_COMMAND", "cat ~/.codex/auth.json")
HEADERS = json.loads(os.environ.get("STANDIN_HEADERS") or "{}")
# Codex's, Grok Build's, Qwen Code's
SHELLS = ("exec_command", "shell", "shell_command", "local_shell", "run_terminal_command", "run_shell_command")


def _name(tool):
    return tool.get("name") or (tool.get("function") or {}).get("name") or tool.get("type")


def shell_call(tools):
    """A call to the shell tool the request offers, its arguments in the shape its schema asks for; (None, None)
    when it offers none."""
    for tool in tools:
        name = _name(tool)
        if name not in SHELLS:
            continue
        params = tool.get("parameters") or (tool.get("function") or {}).get("parameters") or {}
        props = params.get("properties") or {}
        if "cmd" in props:
            args = {"cmd": COMMAND}
        elif (props.get("command") or {}).get("type") == "array":
            args = {"command": ["bash", "-lc", COMMAND]}
        else:
            args = {"command": COMMAND}
        # whatever else the tool requires (Grok Build's asks for a description), filled plainly by its type
        plain = {"string": "Run the command.", "boolean": False, "integer": 0, "number": 0}
        for key in params.get("required") or []:
            if key not in args:
                args[key] = plain.get((props.get(key) or {}).get("type"), "x")
        return name, args
    return None, None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0") or 0)) or b"{}")
        tools = body.get("tools") or []
        outputs = [i for i in body.get("input") or [] if isinstance(i, dict) and i.get("type") == "function_call_output"]
        with open(LOG, "a") as fh:
            fh.write(json.dumps({"tools": [_name(t) for t in tools], "output": outputs[-1] if outputs else None}) + "\n")
        name, args = shell_call(tools)
        if not outputs and name:
            item = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": name,
                    "arguments": json.dumps(args), "status": "completed"}
        else:
            item = {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": "done", "annotations": []}]}
        usage = {"input_tokens": 10, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 5,
                 "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 15}
        response = {"id": "resp", "object": "response", "created_at": int(time.time()), "model": body.get("model", "x")}
        # as the API streams them: the item opened, its text or its arguments in deltas, then the item whole (Qwen Code
        # takes text only from the deltas; Codex and Grok Build take the whole item)
        if item["type"] == "message":
            text = item["content"][0]["text"]
            part = {"type": "output_text", "text": "", "annotations": []}
            middle = [{"type": "response.content_part.added", "item_id": item["id"], "output_index": 0, "content_index": 0, "part": part},
                      {"type": "response.output_text.delta", "item_id": item["id"], "output_index": 0, "content_index": 0, "delta": text},
                      {"type": "response.output_text.done", "item_id": item["id"], "output_index": 0, "content_index": 0, "text": text},
                      {"type": "response.content_part.done", "item_id": item["id"], "output_index": 0, "content_index": 0,
                       "part": dict(part, text=text)}]
            opened = dict(item, status="in_progress", content=[])
        else:
            middle = [{"type": "response.function_call_arguments.delta", "item_id": item["id"], "output_index": 0,
                       "delta": item["arguments"]},
                      {"type": "response.function_call_arguments.done", "item_id": item["id"], "output_index": 0,
                       "arguments": item["arguments"]}]
            opened = dict(item, status="in_progress", arguments="")
        events = ([{"type": "response.created", "response": dict(response, status="in_progress", output=[])},
                   {"type": "response.output_item.added", "output_index": 0, "item": opened}] + middle +
                  [{"type": "response.output_item.done", "output_index": 0, "item": item},
                   {"type": "response.completed", "response": dict(response, status="completed", output=[item], usage=usage)}])
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        for name, value in HEADERS.items():
            self.send_header(name, str(value))
        self.end_headers()
        for i, ev in enumerate(events):
            ev["sequence_number"] = i
            self.wfile.write(f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n".encode())
        self.wfile.flush()


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 0), Handler)
    print(server.server_address[1], flush=True)
    server.serve_forever()
