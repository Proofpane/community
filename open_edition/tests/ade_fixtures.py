"""A small git repository shaped like the owner's: a channel directory the cloud session writes, a log the session
on their machine writes, and code commits, one of them a merge. Made with the `git` on the test machine, with fixed
authors, trailers and times, so a test can say exactly what the ADE should see."""

import os
import subprocess

CLOUD = "Claude-Session: https://claude.ai/code/session_01TESTsessionABCDEF"
AGENT = "Co-Authored-By: Claude <noreply@anthropic.com>"
BRANCH = "work"

HANDOFF = """# Handoff to the session on the owner's machine

## What to do

Run step 1.

## Added by the cloud session at 10:00 UTC: step 2

Run step 2.
"""

NEXT = "# Demo task: what to do next\n\n## Do, in order\n\n1. Set up.\n"

WINDOW = """## GO: demo deploy
window: 2026-09-27T10:00Z to 2026-09-27T11:00Z

Written by the cloud session. The record, not instructions.
"""

QUESTIONS = """# Demo task: questions for the owner

## Waiting

WAITING FOR OWNER (Q-001): may the session deploy?
"""

ANSWERS = "# Demo task: the owner's answers\n\nOne line each.\n\n---\n"

CODE = "def f():\n    return 1\n"


def git(path, *args, env=None, who=("Owner", "owner@example.org"), when="2026-09-27T09:00:00+00:00"):
    full_env = dict(os.environ, GIT_AUTHOR_NAME=who[0], GIT_AUTHOR_EMAIL=who[1], GIT_COMMITTER_NAME=who[0],
                    GIT_COMMITTER_EMAIL=who[1], GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when,
                    GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    full_env.update(env or {})
    out = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, env=full_env)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def write(path, name, text):
    full = os.path.join(str(path), name)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(text)


def commit(path, message, when, who=("Owner", "owner@example.org")):
    git(path, "add", "-A", who=who, when=when)
    git(path, "commit", "-q", "-m", message, who=who, when=when)
    return git(path, "rev-parse", "HEAD")


def make_repo(root):
    """The repository, on branch `work`. Returns {name: full commit id} for the commits a test names."""
    repo = os.path.join(str(root), "demo")
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", BRANCH)
    shas = {}
    write(repo, "README.md", "# demo\n")
    write(repo, "src/app.py", CODE)
    shas["first"] = commit(repo, "Start the demo", "2026-09-27T08:00:00+00:00")
    cloud = ("Claude", "noreply@anthropic.com")
    for name, text in (("NEXT.md", NEXT), ("HANDOFF-TO-LOCAL-SESSION.md", HANDOFF), ("GO.md", WINDOW),
                       ("questions-for-owner.md", QUESTIONS), ("owner-answers.md", ANSWERS)):
        write(repo, f"tasks/demo/{name}", text)
    write(repo, "tasks/demo/run.sh", "#!/bin/sh\necho step\n")
    shas["handoff"] = commit(repo, f"Handoff: the demo task\n\n{CLOUD}", "2026-09-27T09:55:00+00:00", cloud)
    short = shas["handoff"][:7]
    log = ("# Demo task: the log\n\n"
           f"- 2026-09-27T09:58Z · read HANDOFF-TO-LOCAL-SESSION.md @ {short} and NEXT.md @ {short}\n"
           "- 2026-09-27T10:00Z · heartbeat · waiting for the window; heartbeat every 20 minutes"
           " · no unread update\n"
           "- 2026-09-27T10:05Z · step 1 · WAITING FOR OWNER (Q-001): may the session deploy?\n"
           "- 2026-09-27T10:20Z · heartbeat · still waiting · no unread update\n")
    write(repo, "tasks/demo/local-run-log.md", log)
    shas["log"] = commit(repo, f"Log: read the handoff\n\n{AGENT}", "2026-09-27T10:20:30+00:00")
    write(repo, "src/app.py", CODE.replace("return 1", "return 2"))
    write(repo, "tests/test_app.py", "from src.app import f\n\n\ndef test_f():\n    assert f() == 2\n")
    shas["code"] = commit(repo, f"Change f to return 2 and add a test for it\n\nThe change adds tests.\n\n{AGENT}",
                          "2026-09-27T10:30:00+00:00")
    git(repo, "checkout", "-q", "-b", "side")
    write(repo, "docs/side.md", "a side note\n")
    shas["side"] = commit(repo, "A side note", "2026-09-27T10:40:00+00:00")
    git(repo, "checkout", "-q", BRANCH)
    git(repo, "merge", "-q", "--no-ff", "-m", "Merge the side note", "side", when="2026-09-27T10:45:00+00:00")
    shas["merge"] = git(repo, "rev-parse", "HEAD")
    return repo, shas


class FakeGitHubRepo:
    """One repository behind the few GitHub REST endpoints the ADE reads and writes, held in memory, for tests that
    write. `race` is called once, just before the next branch update, to play a session that pushes in between."""

    def __init__(self, repo="owner/repo", branch="main", files=None):
        import hashlib
        import itertools
        self._hash = lambda text: hashlib.sha1(text.encode("utf-8")).hexdigest()
        self._n = itertools.count()
        self.repo, self.branch = repo, branch
        self.trees, self.commits, self.requests = {}, {}, []
        self.race = None
        self.head = self._commit(self._tree(dict(files or {})), [], "the first commit")

    def _tree(self, files):
        tid = self._hash("tree %d" % next(self._n))
        self.trees[tid] = files
        return tid

    def _commit(self, tree, parents, message):
        sha = self._hash("commit %d" % next(self._n))
        self.commits[sha] = {"tree": tree, "parents": parents, "message": message}
        return sha

    def files(self, sha=None):
        return self.trees[self.commits[sha or self.head]["tree"]]

    def push(self, changes, message="a session's commit"):
        files = dict(self.files())
        files.update(changes)
        self.head = self._commit(self._tree(files), [self.head], message)

    def __call__(self, url, headers, method="GET", data=None):
        import json
        import urllib.parse
        self.requests.append((method, url, data))
        base = f"https://api.github.com/repos/{self.repo}"
        if not url.startswith(base):
            return 404, {}, b"{}"
        path, _, query = url[len(base):].partition("?")
        q = dict(urllib.parse.parse_qsl(query))
        ok = lambda obj, status=200: (status, {}, json.dumps(obj).encode("utf-8"))  # noqa: E731
        if method == "GET" and path == f"/git/ref/heads/{self.branch}":
            return ok({"object": {"sha": self.head}})
        if method == "GET" and path.startswith("/git/commits/"):
            sha = path.rsplit("/", 1)[1]
            return ok({"sha": sha, "tree": {"sha": self.commits[sha]["tree"]}})
        if method == "GET" and path.startswith("/contents/"):
            target = urllib.parse.unquote(path[len("/contents/"):])
            files = self.files(q.get("ref"))
            if headers.get("Accept") == "application/vnd.github.raw":
                return (200, {}, files[target].encode("utf-8")) if target in files else (404, {}, b"{}")
            prefix = target + "/"
            names = [k[len(prefix):] for k in files if k.startswith(prefix) and "/" not in k[len(prefix):]]
            if not names:
                return 404, {}, b"{}"
            return ok([{"name": n, "type": "file"} for n in names])
        if method == "GET" and path == "/commits":
            return ok([])
        if method == "POST" and path == "/git/trees":
            body = json.loads(data)
            files = dict(self.trees[body["base_tree"]])
            for e in body["tree"]:
                if "sha" in e and e["sha"] is None:
                    files.pop(e["path"], None)
                else:
                    files[e["path"]] = e["content"]
            return ok({"sha": self._tree(files)}, 201)
        if method == "POST" and path == "/git/commits":
            body = json.loads(data)
            return ok({"sha": self._commit(body["tree"], body["parents"], body["message"])}, 201)
        if method == "PATCH" and path == f"/git/refs/heads/{self.branch}":
            body = json.loads(data)
            if self.race:
                race, self.race = self.race, None
                race()
            if body.get("force") or self.commits[body["sha"]]["parents"] != [self.head]:
                return 422, {}, b'{"message": "Update is not a fast forward"}'
            self.head = body["sha"]
            return ok({"object": {"sha": self.head}})
        return 404, {}, b"{}"
