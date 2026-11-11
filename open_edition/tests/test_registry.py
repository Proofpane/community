"""The MCP registry's entries as an agent's tools show them (registry.py), and an agent's servers checked
(agents.check_mcp)."""

import pytest

from proofpane_workspace import agents, registry, shapes


def test_only_a_pinned_npm_package_over_stdio_or_an_address_is_offered():
    row = registry.row({"server": {"name": "io.example/notes", "description": "Notes\x07.", "version": "2.0.0",
                                   "repository": {"url": "https://github.com/example/notes"},
                                   "packages": [{"registryType": "npm", "identifier": "@ex/notes", "version": "2.0.0",
                                                 "transport": {"type": "stdio"},
                                                 "environmentVariables": [{"name": "TOKEN", "isSecret": True},
                                                                          {"name": "bad name"}]},
                                                {"registryType": "pypi", "identifier": "notes", "version": "2.0.0",
                                                 "transport": {"type": "stdio"}},
                                                {"registryType": "npm", "identifier": "@ex/notes-http", "version": "2.0.0",
                                                 "transport": {"type": "streamable-http"}}],
                                   "remotes": [{"type": "streamable-http", "url": "https://notes.example/mcp",
                                                "headers": [{"name": "Authorization", "isSecret": True}]},
                                               {"type": "websocket", "url": "wss://notes.example/ws"}]}})
    assert row["description"] == "Notes ." and row["repository"] == "https://github.com/example/notes"
    kinds = [(w["kind"], w.get("identifier") or w.get("url")) for w in row["ways"]]
    assert kinds == [("package", "@ex/notes"), ("other", "notes"), ("other", "@ex/notes-http"),
                     ("address", "https://notes.example/mcp")]
    assert row["ways"][0]["env"] == [{"name": "TOKEN", "secret": True, "required": False, "description": ""}]
    assert row["ways"][3]["transport"] == "http" and row["ways"][3]["headers"][0]["secret"] is True
    assert registry.row({"server": {"description": "no name"}}) is None and registry.row("x") is None


def test_a_search_needs_words_and_a_cursor_the_registry_gave():
    with pytest.raises(registry.RegistryError):
        registry.search("   ")
    with pytest.raises(registry.RegistryError):
        registry.search("notes", cursor="x\ny")


@pytest.mark.parametrize("server, why", [
    ({"name": "x", "kind": "package", "identifier": "@ex/x", "version": "latest"}, "exact version"),
    ({"name": "x", "kind": "package", "identifier": "Bad Name", "version": "1.0.0"}, "exact version"),
    ({"name": "x", "kind": "package", "identifier": "@ex/x", "version": "1.0.0", "args": ["a\nb"]}, "exact version"),
    ({"name": "x", "kind": "address", "transport": "ws", "url": "https://x.example/mcp"}, "https address"),
    ({"name": "x", "kind": "address", "transport": "http", "url": "https://user@x.example/mcp"}, "https address"),
    ({"name": "x", "kind": "address", "transport": "http", "url": "https://x.example/mcp", "secret_headers": ["a b"]},
     "secret headers"),
    ({"name": "x", "kind": "package", "identifier": "@ex/x", "version": "1.0.0", "env": {"1BAD": "v"}}, "environment"),
    ({"name": "-x", "kind": "repository"}, "a name of its own"),
    ({"name": "x", "kind": "plugin"}, "a name of its own"),
])
def test_a_server_that_is_not_one_an_agent_can_be_given_is_refused(server, why):
    with pytest.raises(agents.AgentError) as e:
        agents.check_mcp({"servers": [server]})
    assert why in str(e.value)


def test_what_was_accepted_and_held_are_of_its_own_servers():
    ok = {"servers": [{"name": "x", "kind": "repository"}], "accepted": {"x": "a" * 64}, "held": ["x"]}
    assert agents.check_mcp(ok)["held"] == ["x"]
    for bad in (dict(ok, accepted={"y": "a" * 64}), dict(ok, accepted={"x": "short"}), dict(ok, held=["y"]),
                {"servers": [{"name": "x", "kind": "repository"}, {"name": "x", "kind": "repository"}]},
                {"servers": [{"name": f"s{i}", "kind": "repository"} for i in range(shapes.MAX_SERVERS + 1)]}):
        with pytest.raises(agents.AgentError):
            agents.check_mcp(bad)
    assert agents.check_mcp(None) == {"connectors": False, "servers": [], "accepted": {}, "held": []}
