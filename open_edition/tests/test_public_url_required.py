"""OPEN_EDITION_PUBLIC_URL has no default. It is the base of every invitation link, the site passkeys are made for and the
host the session channel's signatures cover, so a deployment that set none of its own would hand out links to another
deployment's address and bind its passkeys to another's name. The app refuses to start without it, the command line
makes no invitation without it, each configuration in this tree that runs the app sets it, and the README's run line
sets it."""

import os
import subprocess
import sys
import tomllib

import pytest

from conftest import ROOT


def _import_the_app(tmp_path, url):
    """Import the app in a process of its own, as a deployment starts it, with every setting it needs but `url` (None:
    OPEN_EDITION_PUBLIC_URL unset): its exit status and standard error."""
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "OPEN_EDITION_DATA": str(tmp_path / "data"),
           "OPEN_EDITION_TOKEN_SECRET": "s" * 32, "OPEN_EDITION_MDS_URL": "", "ADE_LINK_KEEP": "0"}
    if url is not None:
        env["OPEN_EDITION_PUBLIC_URL"] = url
    done = subprocess.run([sys.executable, "-c", "import proofpane_open.main"], cwd=ROOT, env=env,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
    return done.returncode, done.stderr


@pytest.mark.parametrize("url", [None, ""], ids=["unset", "empty"])
def test_without_its_public_address_the_app_refuses_to_start(tmp_path, url):
    status, err = _import_the_app(tmp_path, url)
    assert status != 0 and "OPEN_EDITION_PUBLIC_URL must be set" in err, (status, err[-2000:])


def test_with_its_public_address_the_app_starts(tmp_path):
    status, err = _import_the_app(tmp_path, "https://open.example.test")
    assert status == 0, err[-2000:]


def test_the_command_line_makes_no_invitation_without_the_public_address(monkeypatch, capsys):
    from proofpane_open import __main__ as command, auth
    monkeypatch.delenv("OPEN_EDITION_PUBLIC_URL")
    before = auth.list_invitations()
    status = command.main(["invite", "new@example.com"])
    out, err = capsys.readouterr()
    assert (status, out, auth.list_invitations()) == (1, "", before), (status, out)
    assert "OPEN_EDITION_PUBLIC_URL must be set" in err, err


def test_the_command_line_s_invitation_link_is_on_the_public_address(capsys):
    from proofpane_open import __main__ as command
    assert command.main(["invite", "new@example.com"]) == 0
    assert capsys.readouterr().out.startswith("https://open.example.test/#invite=")


def test_each_configuration_here_that_runs_the_app_sets_its_public_address():
    """fly.toml, and fly.canary.toml where this tree holds it, give the app its address in [env]; the desktop app
    gives its own server one before it imports the app (proofpane_open/desktop.py)."""
    for name in ("fly.toml", "fly.canary.toml"):
        path = os.path.join(ROOT, name)
        if name != "fly.toml" and not os.path.exists(path):
            continue
        with open(path, "rb") as fh:
            env = tomllib.load(fh)["env"]
        assert env.get("OPEN_EDITION_PUBLIC_URL", "").startswith("https://"), name
    with open(os.path.join(ROOT, "proofpane_open", "desktop.py"), encoding="utf-8") as fh:
        assert '"OPEN_EDITION_PUBLIC_URL": f"http://127.0.0.1:{port}"' in fh.read()


def test_the_readme_s_run_line_sets_the_public_address_and_its_row_names_no_default():
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    runs = [line for line in lines if "uvicorn proofpane_open.main:app" in line and line.startswith("OPEN_EDITION_")]
    assert runs and all("OPEN_EDITION_PUBLIC_URL=" in line for line in runs), runs
    rows = [line for line in lines if line.startswith("| `OPEN_EDITION_PUBLIC_URL` |")]
    assert len(rows) == 1 and "default" not in rows[0] and "the app does not start without it" in rows[0], rows
