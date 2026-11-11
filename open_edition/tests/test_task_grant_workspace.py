"""A task attempt's asks for more in its task's grant, on the workspace's side: the attempt's own tool, ask_grant,
which puts an ask on the attempt's record, and the
workspace's view of a run's record (proofpane_workspace/agents.py, Agents.view), which keeps those asks to the bounds
the app takes them by (proofpane_open/ade/task_grants.py, take_record_asks). No claude runs: the attempt's tools are
read from its server as _server builds it, with a stand-in of the Agent SDK's two functions there, so these run where no
SDK is installed."""

import json
import sys
import types

from proofpane_open.ade import task_grants
from proofpane_workspace import agents as wsagents, task_attempt
from test_task_attempt import _Live, _at_work, _in_loop


def stand_in_sdk(monkeypatch):
    """The Agent SDK as the attempt's _server and _options call it: a tool is its name, its description, its input's
    schema and its handler; a server is the tools it was given; a hook's matcher keeps what it was given."""
    sdk = types.ModuleType("claude_agent_sdk")

    class Tool:
        def __init__(self, name, description, schema, handler):
            self.name, self.description, self.input_schema, self.handler = name, description, schema, handler

    def tool(name, description, schema):
        return lambda handler: Tool(name, description, schema, handler)

    class HookMatcher:
        def __init__(self, matcher=None, hooks=(), timeout=None):
            self.matcher, self.hooks, self.timeout = matcher, list(hooks), timeout

    sdk.tool = tool
    sdk.create_sdk_mcp_server = lambda name, tools: {"type": "sdk", "name": name, "tools": list(tools)}
    sdk.HookMatcher = HookMatcher
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)


def tools_of(a):
    return {t.name: t for t in a._server()["tools"]}


def asked_of(answer):
    return answer["content"][0]["text"], answer.get("is_error") is True


def test_an_attempt_at_work_is_given_ask_grant_its_frozen_start_is_not_and_its_call_is_its_own_write(tmp_path,
                                                                                                    monkeypatch):
    stand_in_sdk(monkeypatch)
    live = _Live(tmp_path)

    async def body():
        a = await live.up()
        a.phase = "frozen"
        assert sorted(tools_of(a)) == ["resume_ack", "task_state"]
        _at_work(a)
        assert "ask_grant" in tools_of(a)
        assert "mcp__proofpane__ask_grant" in a._options(a.chat)["allowed_tools"]
    _in_loop(body)
    # a call that changes the app's records, settled by its id there, as ask_owner's is
    assert task_attempt.classify("mcp__proofpane__ask_grant", {})["kind"] == "own_tool"


def test_ask_grant_puts_each_ask_on_the_attempt_s_record_with_a_client_id_of_its_own(tmp_path, monkeypatch):
    stand_in_sdk(monkeypatch)
    live = _Live(tmp_path)

    async def body():
        a = await live.up()
        _at_work(a)
        tools = tools_of(a)
        assert "ask_grant" in tools, sorted(tools)
        ask = tools["ask_grant"].handler
        words, error = asked_of(await ask({"add": {"egress": ["pypi.org"]}, "lease_end": 1_900_000_000}))
        assert not error and "passkey" in words, words
        words, error = asked_of(await ask({"add": {"secrets": [{"name": "TOKEN", "lease_seconds": 600}]},
                                           "rungs": {"merge": "L2", "deploy": "L3"}, "surplus": 1}))
        assert not error, words
        record = live.records[-1][0]
        made = record["task"]["grant_asks"]
        assert [{k: v for k, v in m.items() if k != "client_id"} for m in made] == [
            {"add": {"egress": ["pypi.org"]}, "lease_end": 1_900_000_000},
            {"add": {"secrets": [{"name": "TOKEN", "lease_seconds": 600}]}, "rungs": {"merge": "L2", "deploy": "L3"}}]
        ids = [m["client_id"] for m in made]
        assert len(set(ids)) == 2 and all(len(i) == 32 and set(i) <= set("0123456789abcdef") for i in ids)
        # an ask with nothing in it, one past the size an ask may have, and one past the asks a record carries: each
        # refused to the model, and none of them on the record
        for given in ({}, {"add": {}}, {"add": "pypi.org"},
                      {"add": {"egress": ["h%d.example.com" % i for i in range(3000)]}}):
            words, error = asked_of(await ask(given))
            assert error, (given, words)
        for i in range(3):
            assert not asked_of(await ask({"add": {"egress": [f"x{i}.example.com"]}}))[1]
        words, error = asked_of(await ask({"add": {"egress": ["late.example.com"]}}))
        assert error and str(wsagents.MAX_GRANT_ASKS) in words, words
        assert len(live.records[-1][0]["task"]["grant_asks"]) == wsagents.MAX_GRANT_ASKS
    _in_loop(body)


def view_of_task(grant_asks):
    record = {"id": "a" * 12, "agent_id": "b" * 12,
              "run": {"state": "working", "task": {"id": "c" * 32, "fence": 2, "step": 1, "attempt": 1,
                                                    "grant_asks": grant_asks}}}
    return wsagents.Agents.view(None, record)["task"]


def test_the_workspace_s_view_keeps_an_attempt_s_grant_asks_to_the_bounds_the_app_takes_them_by():
    good = [{"client_id": f"{i + 1:032x}", "add": {"egress": [f"h{i}.example.com"]}} for i in range(7)]
    seen = view_of_task(good)
    assert seen.get("grant_asks") == good[:5] and (seen["id"], seen["fence"]) == ("c" * 32, 2)
    # the same bounds as the app's: as many asks a record, the same keys
    assert wsagents.MAX_GRANT_ASKS == task_grants.ASKS_PER_RECORD
    assert set(wsagents.GRANT_ASK_KEYS) == set(task_grants.ASK_KEYS)
    one = {"client_id": "d" * 32, "add": {"egress": ["pypi.org"]}, "lease_end": 1, "rungs": {"merge": "L2", "deploy": "L3"}}
    assert view_of_task([one])["grant_asks"] == [one]
    for bad in ("not an ask", {"add": {}}, dict(one, surplus=1), dict(one, client_id="D" * 32),
                dict(one, client_id="d" * 31), dict(one, client_id="d" * 32 + "\n"), dict(one, client_id=7),
                dict(one, add={"egress": ["h%d.example.com" % i for i in range(3000)]})):
        assert view_of_task([bad, one])["grant_asks"] == [one], bad
    # what is not a list of asks carries none
    for bad in ("asks", {"client_id": "d" * 32}, 7, None):
        assert not view_of_task(bad).get("grant_asks"), bad
    # an ask is at most MAX_GRANT_ASK bytes of JSON: one just under it is kept, one just over it is not
    fill = lambda n: dict(one, add={"egress": ["a" * n]})    # noqa: E731
    under = next(n for n in range(wsagents.MAX_GRANT_ASK, 0, -1)
                 if len(json.dumps(fill(n), separators=(",", ":")).encode("utf-8")) <= wsagents.MAX_GRANT_ASK)
    assert view_of_task([fill(under)])["grant_asks"] == [fill(under)]
    assert view_of_task([fill(under + 1)])["grant_asks"] == []
