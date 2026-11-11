"""The app's side of a workspace agent: its HTTP calls, and the terminal proxy between the browser and the agent.

Calls carry the workspace's secret and go only to the address the provider gave; no proxy from the environment is
used and no redirect is followed. The terminal proxy is two WebSockets: the browser's to the app, authenticated by
its first message (the account's token, never a URL, which an access log would keep), and the app's to the agent,
authenticated by the workspace's secret in a header. Output and input pass through as bytes; the only text either
side may send is a small control message, checked here, so the browser never speaks to the agent directly.
"""

import asyncio
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, InvalidURI

MAX_REPLY = 4 * 1024 * 1024
MAX_SHELLS = 4          # a session's shells, numbered from 1 (the agent's own limit)
MAX_FRAME = 256 * 1024
SESSION_ID = re.compile(r"^[0-9a-f]{12}$")
FROM_AGENT = {"exit", "dropped", "error"}
# the agent's own refusals, which carry its reason: a WorkspaceCallError with one of these statuses is the agent saying
# no, in its words; any other is only that it answered so (502), and the same call may get through later
REFUSALS = (400, 404, 409, 412)


class WorkspaceCallError(Exception):
    def __init__(self, status, detail, answered=None):
        super().__init__(detail)
        self.status = status
        # the status the agent answered with where it gave no reason the page can show (_refused's "The workspace
        # answered N."): None for its own refusals and for no answer at all
        self.answered = answered


NOT_ANSWERING = "The workspace is not answering."


class NotAnswering(WorkspaceCallError):
    """No answer at all (no connection, a reset, a time out, or a reply that is not one), unlike a refusal: the same
    call may get through later."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _opener(endpoint):
    handlers = [urllib.request.ProxyHandler({}), _NoRedirect()]
    if endpoint.ssl is not None:
        handlers.append(urllib.request.HTTPSHandler(context=endpoint.ssl))
    return urllib.request.build_opener(*handlers)


def _refused(e):
    """The agent's own reason, when it gave one the page can show (a request it refused, a thing not found, a change
    that came too late); otherwise only that it answered so."""
    try:
        detail = json.loads(e.read(64 * 1024)).get("detail")
    except (ValueError, AttributeError):
        detail = None
    if e.code in REFUSALS and isinstance(detail, str):
        return WorkspaceCallError(e.code, detail)
    return WorkspaceCallError(502, f"The workspace answered {e.code}.", answered=e.code)


def call(endpoint, method, path, body=None, timeout=30):
    """One call to the agent; its JSON reply, or WorkspaceCallError with the agent's own message."""
    # as UTF-8, not escaped: an escaped character is 6 to 12 bytes, and the agent takes a request of 64 KB (a review's
    # 15,000 characters, or a file saved from the editor, fit as UTF-8 whatever they are)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8", "surrogatepass") if body is not None else None
    req = urllib.request.Request(endpoint.http + path, data=data, method=method,
                                 headers={"Authorization": "Bearer " + endpoint.secret,
                                          "Content-Type": "application/json"})
    try:
        with _opener(endpoint).open(req, timeout=timeout) as r:
            return json.loads(r.read(MAX_REPLY + 1)[:MAX_REPLY])
    except urllib.error.HTTPError as e:
        raise _refused(e) from None
    except (urllib.error.URLError, OSError, ValueError):
        raise NotAnswering(502, NOT_ANSWERING) from None


def put_file(endpoint, path, file_path, timeout=120):
    """A file's bytes to the agent with PUT (a move's files, ade/moves.py), read from the file as they are sent, never
    held whole; its reply (empty when it sent none that is a JSON object), or WorkspaceCallError with the agent's own
    message, NotAnswering when it gave no answer."""
    try:
        with open(file_path, "rb") as fh:
            req = urllib.request.Request(endpoint.http + path, data=fh, method="PUT",
                                         headers={"Authorization": "Bearer " + endpoint.secret,
                                                  "Content-Type": "application/octet-stream",
                                                  "Content-Length": str(os.fstat(fh.fileno()).st_size)})
            with _opener(endpoint).open(req, timeout=timeout) as r:
                reply = r.read(MAX_REPLY + 1)[:MAX_REPLY]
    except urllib.error.HTTPError as e:
        raise _refused(e) from None
    except (urllib.error.URLError, OSError, ValueError):
        raise NotAnswering(502, NOT_ANSWERING) from None
    try:
        got = json.loads(reply)
    except ValueError:
        return {}
    return got if isinstance(got, dict) else {}


def upload(endpoint, path, name, data, timeout=120, to_chat=False):
    """A file's bytes to the agent (its upload route), attached to a chat's message or not; its reply, or
    WorkspaceCallError with the agent's own message."""
    query = {"name": name, **({"to": "chat"} if to_chat else {})}
    req = urllib.request.Request(endpoint.http + path + "?" + urllib.parse.urlencode(query), data=data,
                                 method="POST", headers={"Authorization": "Bearer " + endpoint.secret,
                                                         "Content-Type": "application/octet-stream"})
    try:
        with _opener(endpoint).open(req, timeout=timeout) as r:
            return json.loads(r.read(MAX_REPLY + 1)[:MAX_REPLY])
    except urllib.error.HTTPError as e:
        raise _refused(e) from None
    except (urllib.error.URLError, OSError, ValueError):
        raise WorkspaceCallError(502, "The workspace is not answering.") from None


def fetch_bytes(endpoint, path, limit, timeout=60):
    """Bytes the agent sends (an image): (them, their type), or WorkspaceCallError with the agent's own message; more
    than `limit` is refused rather than cut."""
    req = urllib.request.Request(endpoint.http + path, method="GET",
                                 headers={"Authorization": "Bearer " + endpoint.secret})
    try:
        with _opener(endpoint).open(req, timeout=timeout) as r:
            data = r.read(limit + 1)
            kind = r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        raise _refused(e) from None
    except (urllib.error.URLError, OSError, ValueError):
        raise WorkspaceCallError(502, "The workspace is not answering.") from None
    if len(data) > limit:
        raise WorkspaceCallError(502, "The workspace sent more than it may.")
    return data, kind.split(";")[0].strip().lower()


def resize_message(obj):
    """A resize the browser asked for, as the agent takes it; None for anything else."""
    if (isinstance(obj, dict) and set(obj) == {"type", "cols", "rows"} and obj["type"] == "resize"
            and all(isinstance(obj[k], int) and not isinstance(obj[k], bool) for k in ("cols", "rows"))
            and 1 <= obj["cols"] <= 500 and 1 <= obj["rows"] <= 300):
        return json.dumps(obj)
    return None


def agent_message(text):
    """A control message from the agent, passed on as the browser should see it; None for anything else."""
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    if not isinstance(obj, dict) or obj.get("type") not in FROM_AGENT:
        return None
    out = {"type": obj["type"]}
    if obj["type"] == "exit" and isinstance(obj.get("code"), int) and not isinstance(obj.get("code"), bool):
        out["code"] = obj["code"]
    if obj["type"] == "error" and isinstance(obj.get("detail"), str):
        out["detail"] = obj["detail"][:300]
    return json.dumps(out)


async def open_terminal(endpoint, session_id, shell=None):
    """The agent's terminal WebSocket, the session's program's or, with `shell` (its number), that shell's, once the
    agent has said it is ready; WorkspaceCallError with the agent's reason when it says there is none."""
    if not SESSION_ID.match(session_id):
        raise WorkspaceCallError(404, "No such session.")
    if shell is not None and (not isinstance(shell, int) or isinstance(shell, bool) or not 1 <= shell <= MAX_SHELLS):
        raise WorkspaceCallError(404, "No such shell.")
    try:
        which = f"shell/{shell}" if shell is not None else "terminal"
        agent = await ws_connect(f"{endpoint.ws}/sessions/{session_id}/{which}", ssl=endpoint.ssl,
                                 additional_headers={"Authorization": "Bearer " + endpoint.secret},
                                 max_size=MAX_FRAME, open_timeout=15, proxy=None)
    except (OSError, InvalidStatus, InvalidURI, asyncio.TimeoutError):
        raise WorkspaceCallError(502, "The workspace's terminal is not answering.") from None
    try:
        first = await asyncio.wait_for(agent.recv(), 15)
        hello = json.loads(first) if isinstance(first, str) else None
    except (ConnectionClosed, asyncio.TimeoutError, ValueError):
        hello = None
    if isinstance(hello, dict) and hello.get("type") == "ready":
        return agent
    await agent.close()
    if isinstance(hello, dict) and hello.get("type") == "error" and isinstance(hello.get("detail"), str):
        raise WorkspaceCallError(404, hello["detail"][:300])
    raise WorkspaceCallError(502, "The workspace's terminal did not say it was ready.")


async def open_chat(endpoint):
    """The agent's chat WebSocket, which carries every chat's events as they happen, once it has said it is ready."""
    try:
        agent = await ws_connect(f"{endpoint.ws}/chat", ssl=endpoint.ssl,
                                 additional_headers={"Authorization": "Bearer " + endpoint.secret},
                                 max_size=MAX_FRAME, open_timeout=15, proxy=None)
    except (OSError, InvalidStatus, InvalidURI, asyncio.TimeoutError):
        raise WorkspaceCallError(502, "The workspace's chat is not answering.") from None
    try:
        first = await asyncio.wait_for(agent.recv(), 15)
        hello = json.loads(first) if isinstance(first, str) else None
    except (ConnectionClosed, asyncio.TimeoutError, ValueError):
        hello = None
    if isinstance(hello, dict) and hello.get("type") == "ready":
        return agent
    await agent.close()
    raise WorkspaceCallError(502, "The workspace's chat did not say it was ready.")


def chat_message(text):
    """A message from the agent's chat socket as the browser should see it, re-made from its known parts (a chat's
    event, what claude is writing as it writes it, or that the page fell behind and must read again); None for
    anything else."""
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("type") == "dropped":
        return json.dumps({"type": "dropped"})
    if obj.get("type") == "delta":
        return _delta_message(obj.get("session"), obj.get("delta"))
    sid, ev = obj.get("session"), obj.get("event")
    if obj.get("type") != "event" or not isinstance(sid, str) or not SESSION_ID.match(sid) or not isinstance(ev, dict):
        return None
    return json.dumps({"type": "event", "session": sid, "event": ev}, ensure_ascii=False)


def _delta_message(sid, d):
    """What claude is writing as it writes it (the agent's Chat._stream), re-made from its known parts: the last
    logged event it follows, its block, where in the block it starts, and its text, or that a block of thinking or of
    a tool call began."""
    whole = lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0  # noqa: E731
    if not isinstance(sid, str) or not SESSION_ID.match(sid) or not isinstance(d, dict):
        return None
    if not whole(d.get("after")) or not whole(d.get("block")):
        return None
    out = {"after": d["after"], "block": d["block"]}
    if isinstance(d.get("text"), str) and whole(d.get("offset")):
        out.update(offset=d["offset"], text=d["text"])
    elif d.get("thinking") is True:
        out["thinking"] = True
    elif isinstance(d.get("tool"), str):
        out["tool"] = d["tool"][:200]
    else:
        return None
    return json.dumps({"type": "delta", "session": sid, "delta": out}, ensure_ascii=False)


async def pump(browser, agent, on_activity=None, signed_in=None, every=10, text=None):
    """Pass bytes both ways, and the control messages each side may send, until either side ends. With `signed_in`
    (an async check of the sign-in that opened the terminal), check it every `every` seconds and end when it says the
    sign-in has ended; a check that fails to answer is logged and the terminal goes on, since a passing error is not a
    sign-out. Returns what ended it: "signed-out", "agent" (the workspace's side closed) or "browser". `text` passes
    on the agent's text messages (agent_message, a terminal's, by default)."""
    ended = []
    text = text or agent_message

    async def watch():
        while True:
            await asyncio.sleep(every)
            try:
                still = await signed_in()
            except Exception as e:  # noqa: BLE001  a check that could not answer ends nothing
                print(f"workspace terminal: the sign-in check did not answer: {type(e).__name__}: {e}",
                      file=sys.stderr)
                continue
            if not still:
                ended.append("signed-out")
                return

    async def up():
        while True:
            msg = await browser.receive()
            if msg["type"] == "websocket.disconnect":
                return
            if msg.get("bytes") is not None:
                await agent.send(msg["bytes"])
            elif msg.get("text") is not None:
                try:
                    ctl = resize_message(json.loads(msg["text"]))
                except ValueError:
                    ctl = None
                if ctl is None:
                    return
                await agent.send(ctl)
            if on_activity:
                on_activity()

    async def down():
        async for msg in agent:
            if isinstance(msg, bytes):
                await browser.send_bytes(msg)
            else:
                ctl = text(msg)
                if ctl is not None:
                    await browser.send_text(ctl)

    tasks = [asyncio.ensure_future(up()), asyncio.ensure_future(down())]
    if signed_in is not None:
        tasks.append(asyncio.ensure_future(watch()))
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if not ended:
            ended.append("agent" if tasks[1] in done else "browser")
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await agent.close()
        except ConnectionClosed:
            pass
    return ended[0]
