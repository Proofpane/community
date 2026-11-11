"""The workspace agent and the app are kept apart: a workspace holds none of the app's accounts, secrets or signing
key, so the agent imports nothing of the app's, and the app starts the agent as a process, never by importing it."""

import os

from conftest import ROOT
from test_diff_and_boundary import _imports

APP_ONLY = {"proofpane_open", "proofpane_sign", "proofpane_core", "proofpane_verify", "verify_pr"}


def test_the_workspace_agent_imports_nothing_of_the_app():
    found = [(p, n) for p, n in _imports(os.path.join(ROOT, "proofpane_workspace")) if n in APP_ONLY]
    assert found == []


def test_the_app_imports_nothing_of_the_workspace_agent():
    found = [(p, n) for p, n in _imports(os.path.join(ROOT, "proofpane_open")) if n == "proofpane_workspace"]
    assert found == []


def test_the_scan_sees_the_agents_own_imports():
    names = {n for _, n in _imports(os.path.join(ROOT, "proofpane_workspace"))}
    assert {"starlette", "uvicorn"} <= names
