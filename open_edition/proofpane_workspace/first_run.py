"""Claude Code's first-run choices, made for a workspace machine's user, so that `claude` in a terminal opens at its
prompt. Otherwise a person signed in from the page who starts `claude` in a terminal meets its wizard's theme list,
on a phone as anywhere; after it Claude Code asks whether the folder is trusted, with "No, exit" chosen. Checked in
the image with its `claude` 2.1.274: `hasCompletedOnboarding` skips the wizard (a theme need not be named: Claude Code
keeps none in the file for its default); a folder is trusted
when it or a folder above it is (`projects[path].hasTrustDialogAccepted`), except a git worktree, which Claude Code
takes as its main repository's: a session's worktree is trusted through its repository (`repos/<name>`), not through
the workspace's root. So the root and the repositories there are trusted when the machine starts (`seed`), and a
repository again when a session is made in it (`trust`), which covers one cloned or made later.

A chat never shows either (Claude Code through the Agent SDK is not interactive), so the trust makes a terminal
`claude` load a repository's project settings as a chat already does; the image's managed settings stay above them.
Everything else in the file is left as it is; a file that is not Claude Code's JSON object is not touched; nothing is
written when nothing would change.
"""

import json
import os
import tempfile
import threading

_LOCK = threading.Lock()                # this process's writers of a ~/.claude.json, one at a time


def _update(home, change):
    with _LOCK:
        return _update_locked(home, change)


def _update_locked(home, change):
    """Read, change and write back, in a temporary file of this write's own, so writers of this process (agents'
    runs starting at once, a session) neither lose nor interleave each other's changes."""
    path = os.path.join(home, ".claude.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    before = json.dumps(data, sort_keys=True)
    change(data)
    if json.dumps(data, sort_keys=True) == before:
        return False
    fd, tmp = tempfile.mkstemp(prefix=".claude.json.", suffix=".proofpane-tmp", dir=home)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


def _trusted(data, paths):
    projects = data.get("projects") if isinstance(data.get("projects"), dict) else {}
    for path in paths:
        entry = projects.get(path) if isinstance(projects.get(path), dict) else {}
        entry["hasTrustDialogAccepted"] = True
        projects[path] = entry
    data["projects"] = projects


def seed(home, root, repos=()):
    """The choices in `home`/.claude.json for the workspace at `root` and its repositories; True when written."""
    def change(data):
        data["hasCompletedOnboarding"] = True
        _trusted(data, [root, *repos])
    return _update(home, change)


def onboarded(home):
    """The first-run wizard's choices alone, with no folder trusted: an account's Claude Code on the person's own
    machine (accounts.py), which asks about each folder as their own does; True when written."""
    return _update(home, lambda data: data.update(hasCompletedOnboarding=True))


def trust(home, repo):
    """One repository trusted, for a session in a worktree of it; True when written."""
    return _update(home, lambda data: _trusted(data, [repo]))
