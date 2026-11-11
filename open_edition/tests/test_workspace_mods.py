"""Claude Code's mods in a workspace machine (proofpane_workspace/settings.py, whose docstring says what each setting
does in which build). A mod is a plugin whose code Claude Code runs in its own process, with the user's permissions;
the image's managed settings refuse the code of every mod that is not the organisation's. These tests check the
settings the module writes: the built-in guard's `allowManagedModsOnly` option, which Claude Code reads from managed
settings from 2.1.286, and `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS` set to "0", the early-access switch that builds 2.1.274
to 2.1.286 read and 2.1.287 ignores; that the local provider's --settings file carries neither; and that the managed
settings leave out the switches that reach further than mods. What the settings do inside Claude Code was read in its
code, not measured here."""

from proofpane_workspace import settings

PY, DIR = "/usr/bin/python3", "/opt/proofpane/proofpane_workspace"
GUARD = "cc-plugin-sec-default@builtin"


def test_managed_settings_set_the_guard_s_option_to_refuse_users_mods():
    managed = settings.claude_settings(PY, DIR, managed=True)
    # the one id under which Claude Code reads the guard's options (not the older sec-default@builtin), the
    # options' own key, and the boolean true itself
    assert managed["pluginConfigs"] == {GUARD: {"options": {"allowManagedModsOnly": True}}}
    assert managed["pluginConfigs"][GUARD]["options"]["allowManagedModsOnly"] is True


def test_managed_settings_set_the_early_switch_off():
    # the switch "0", and beside it the updater's switch, bare mode's off (settings.SIMPLE_ENV) and the loader
    # variables set empty (settings.LOADER_ENV), nothing else; both switches come after the pins here, so a pin of
    # either name, which would empty it in the file, fails the test
    managed = settings.claude_settings(PY, DIR, managed=True)
    assert managed["env"]["CLAUDE_CODE_ENABLE_FUNCTION_HOOKS"] == "0"
    assert managed["env"] == dict({name: "" for name in settings.LOADER_ENV}, **settings.SIMPLE_ENV,
                                  DISABLE_AUTOUPDATER="1", CLAUDE_CODE_ENABLE_FUNCTION_HOOKS="0")


def test_the_local_file_sets_neither():
    # the guard reads its options from managed settings only, the variable would reach only builds before 2.1.287,
    # and a gate in a --settings file does not hold against a mod in any case; the file's `env` holds the loader
    # variables set empty and bare mode's off (settings.SIMPLE_ENV), and nothing of the mods', and the file
    # holds nothing at its top level but its four keys
    local = settings.claude_settings(PY, DIR, managed=False)
    assert "pluginConfigs" not in local and "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS" not in local["env"]
    assert local["env"] == dict({name: "" for name in settings.LOADER_ENV}, **settings.SIMPLE_ENV)
    assert set(local) == {"hooks", "statusLine", "env", "disableAllHooks"}


def test_the_managed_settings_leave_out_the_switches_that_reach_further():
    managed = settings.claude_settings(PY, DIR, managed=True)
    # allowManagedHooksOnly also stops the users' and repositories' own settings hooks; disableAllHooks there stops the
    # gate itself; disableSideloadFlags also refuses a --mcp-config holding servers that are not in-process, which an
    # agent's run passes; a prependPlugins list leaves the guard out unless it names it; appendPlugins orders an
    # organisation's own mods, and the workspace has none; a managed enabledPlugins entry is one of the conditions for
    # a plugin to count as the organisation's, whose mod the guard's option does not refuse; strictKnownMarketplaces
    # decides which plugins install at all
    for key in ("allowManagedHooksOnly", "disableAllHooks", "disableSideloadFlags", "prependPlugins",
                "appendPlugins", "enabledPlugins", "strictKnownMarketplaces"):
        assert key not in managed, key
    # and the guard's other option, which would let a user's mod approve a call a deny rule refuses, stays unset
    assert list(managed["pluginConfigs"]) == [GUARD]
    assert list(managed["pluginConfigs"][GUARD]["options"]) == ["allowManagedModsOnly"]
    # nor anything else at the top level: a key this test does not name (a second place for the guard's option,
    # another plugin setting) fails it, whatever the key
    assert set(managed) == {"hooks", "statusLine", "pluginConfigs", "env"}
