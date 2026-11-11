"""Where a channel's files and a branch's commits come from. Two sources answer the same six questions:

- `GitHub`: GitHub's REST API over HTTPS, for the hosted site, which has no `git` and keeps no clone. A public
  repository needs no token; a private one needs the fine-grained token the owner creates and enters themselves, which
  the ledger keeps encrypted. Answers are cached with their ETags, so an unchanged file costs a `304`, which GitHub
  does not count against its rate limit.
- `LocalGit`: a clone on the machine the app runs on, read with `git`. Only an admin names such clones, in
  `OPEN_EDITION_ADE_LOCAL_REPOS`; a user picks one by name and never gives a path, so no request can make the app read
  a directory it was not told about. It is for a single-user install and the tests, never for the hosted site.

Both read at one commit: the board resolves the branch's head once and asks everything else at that commit, so one
board shows one moment of the branch. Every name is checked before it is used: a repository `owner/name`, a branch,
and paths that cannot leave the repository. Neither source writes anything.
"""

import hashlib
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict

from . import commits as commits_mod

MAX_READ = 2 * 1024 * 1024
TIMEOUT = 20

_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9_.-]{1,100}")
_LOCAL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
_PATH = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_./-]{0,299}")
_SHA = re.compile(r"[0-9a-f]{40}")


class SourceError(ValueError):
    """Something the owner can act on (a name that does not exist, a token GitHub refused, the rate limit): shown as
    it is."""


class NotFound(SourceError):
    """The repository, branch, path or file does not exist (or the token cannot see it)."""


class _Moved(Exception):
    """The branch moved while a commit was being made: build the change again on the new head."""


def check_repo(name):
    if not isinstance(name, str) or not _REPO.fullmatch(name) or ".." in name:
        raise SourceError("A repository is written owner/name, as on GitHub.")
    return name


def check_branch(name):
    bad = (not isinstance(name, str) or not _BRANCH.fullmatch(name) or ".." in name or "//" in name
           or name.endswith(("/", ".", ".lock")) or "@{" in name)
    if bad:
        raise SourceError("That is not a branch name git accepts.")
    return name


def check_path(path):
    """A directory or file inside the repository, as `a/b/c`: no leading slash, no `..`, no empty part."""
    if path in ("", "."):
        return ""
    parts = path.split("/") if isinstance(path, str) else [""]
    if not isinstance(path, str) or not _PATH.fullmatch(path) or any(p in ("", ".", "..") for p in parts):
        raise SourceError(f"A path is written a/b/c inside the repository: {str(path)[:80]!r} is not.")
    return path


def join(directory, name):
    return f"{directory}/{name}" if directory else name


def check_sha(sha):
    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
        raise SourceError("A commit is named by its full id.")
    return sha


class LocalGit:
    """A clone on this machine, read with `git`, at commits the caller resolved first."""

    kind = "local"

    def __init__(self, name, path):
        self.name = name
        self.path = path
        self.label = f"local/{name}"

    def _git(self, *args, text=True):
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", LC_ALL="C")
        try:
            out = subprocess.run(["git", "-C", self.path, "-c", "core.quotepath=off", *args], capture_output=True,
                                 timeout=TIMEOUT, env=env, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise SourceError(f"git could not read the clone {self.name}: {type(e).__name__}") from None
        if out.returncode != 0:
            return None
        return out.stdout.decode("utf-8", "replace") if text else out.stdout

    def head(self, branch):
        check_branch(branch)
        sha = self._git("rev-parse", "--verify", "--quiet", "--end-of-options", f"{branch}^{{commit}}")
        if not sha or not _SHA.fullmatch(sha.strip()):
            raise SourceError(f"The clone {self.name} has no branch {branch}.")
        return sha.strip()

    def list_dir(self, sha, directory):
        check_sha(sha)
        check_path(directory)
        spec = f"{sha}:{directory}" if directory else f"{sha}:"
        out = self._git("ls-tree", "-z", spec)
        if out is None:
            raise SourceError(f"{directory or 'The top of the repository'} is not a directory at {sha[:7]}.")
        names = []
        for item in out.split("\0"):
            if not item:
                continue
            meta, _, name = item.partition("\t")
            if meta.split(" ")[1:2] == ["blob"]:
                names.append(name)
        return sorted(names)

    def read_file(self, sha, path):
        check_sha(sha)
        check_path(path)
        size = self._git("cat-file", "-s", f"{sha}:{path}")
        if size is None:
            raise NotFound(f"{path} does not exist at {sha[:7]}.")
        if int(size.strip() or 0) > MAX_READ:
            raise SourceError(f"{path} is larger than {MAX_READ // (1024 * 1024)} MB.")
        data = self._git("cat-file", "blob", f"{sha}:{path}", text=False)
        return data.decode("utf-8", "replace")

    def _log(self, *args):
        fmt = "%H%x1f%P%x1f%an%x1f%ae%x1f%aI%x1f%B%x1e"
        out = self._git("log", f"--format={fmt}", *args)
        if out is None:
            raise SourceError(f"git log failed in the clone {self.name}.")
        found = []
        for record in out.split("\x1e"):
            record = record.strip("\n")
            if not record:
                continue
            sha, parents, name, email, when, message = (record.split("\x1f") + [""] * 6)[:6]
            found.append(commits_mod.make_commit(sha, parents.split(), name, email, when, message))
        return found

    def file_commits(self, sha, path, limit):
        check_sha(sha)
        check_path(path)
        return self._log(f"-n{int(limit)}", sha, "--", path)

    def branch_commits(self, sha, limit):
        check_sha(sha)
        return self._log(f"-n{int(limit)}", sha)

    def commit_checks(self, sha):
        """A clone has no CI: no check runs, no statuses."""
        check_sha(sha)
        return [], []

    def commit_diff(self, sha, parent):
        check_sha(sha)
        check_sha(parent)
        out = self._git("diff", "--no-color", "--no-ext-diff", "--no-textconv", parent, sha)
        if out is None:
            raise SourceError(f"git diff failed for {sha[:7]}.")
        return out


def _default_opener(url, headers, method="GET", data=None):
    request = urllib.request.Request(url, headers=headers, method=method, data=data)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, dict(response.headers), response.read(MAX_READ + 1)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read(64 * 1024)
    except (urllib.error.URLError, OSError) as e:
        raise SourceError(f"GitHub could not be reached ({type(e).__name__}).") from None


class _Cache:
    """Answers with their ETags, by URL, media type and token, so an unchanged answer is asked for with
    `If-None-Match` and comes back as a free `304`. Bounded; the oldest entry goes first."""

    def __init__(self, size=512):
        self.size = size
        self.items = OrderedDict()

    def get(self, key):
        item = self.items.get(key)
        if item is not None:
            self.items.move_to_end(key)
        return item

    def put(self, key, etag, body):
        self.items[key] = (etag, body)
        self.items.move_to_end(key)
        while len(self.items) > self.size:
            self.items.popitem(last=False)


_SHARED_CACHE = _Cache()


class GitHub:
    """One repository on GitHub, read through its REST API, with or without a token."""

    kind = "github"

    def __init__(self, repo, token=None, opener=None, cache=None):
        self.API = api_base()
        self.repo = check_repo(repo)
        self.label = repo
        self.token = token or None
        self.opener = opener or _default_opener
        self.cache = cache if cache is not None else _SHARED_CACHE
        self._who = hashlib.sha256((token or "").encode("utf-8")).hexdigest()[:16]

    def _get(self, path, params=None, accept="application/vnd.github+json"):
        url = f"{self.API}/repos/{self.repo}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = self._headers(accept)
        key = (url, accept, self._who)
        cached = self.cache.get(key)
        if cached and cached[0]:
            headers["If-None-Match"] = cached[0]
        status, response_headers, body = self.opener(url, headers)
        lowered = {k.lower(): v for k, v in (response_headers or {}).items()}
        if status == 304 and cached:
            return cached[1]
        if status == 200:
            if len(body) > MAX_READ:
                raise SourceError("GitHub's answer is larger than the ADE reads.")
            if lowered.get("etag"):
                self.cache.put(key, lowered["etag"], body)
            return body
        if status == 404:
            raise NotFound(self._error(status, lowered))
        raise SourceError(self._error(status, lowered))

    def _headers(self, accept="application/vnd.github+json"):
        headers = {"Accept": accept, "User-Agent": "proofpane-community-ade", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _send(self, method, path, body):
        """A write, never cached. A branch that moved under a fast-forward-only update raises `_Moved`."""
        headers = dict(self._headers(), **{"Content-Type": "application/json"})
        status, response_headers, raw = self.opener(f"{self.API}/repos/{self.repo}{path}", headers, method,
                                                    json.dumps(body).encode("utf-8"))
        lowered = {k.lower(): v for k, v in (response_headers or {}).items()}
        if status in (200, 201):
            try:
                return json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise SourceError("GitHub's answer was not JSON.") from None
        if status == 422 and b"fast forward" in (raw or b"").lower():
            raise _Moved()
        if status == 403 and lowered.get("x-ratelimit-remaining") != "0":
            raise SourceError("GitHub refused the write: the token needs Contents: read and write on this repository.")
        raise SourceError(self._error(status, lowered))

    def commit_files(self, branch, build, message, tries=3):
        """One commit on `branch` that sets each path that `build(head)` returns to its text, or deletes it where
        the text is None. It is made on the branch's current head and moved there only as a fast-forward: if a
        session pushed in between, the change is built again on the new head, up to `tries` times, and nothing is
        ever force-pushed. Returns the new commit's id."""
        if not self.token:
            raise SourceError("Writing to a repository needs a token with Contents: read and write.")
        check_branch(branch)
        for _ in range(tries):
            head = self.head(branch)
            changes = build(head)
            tree = self._json(f"/git/commits/{head}")["tree"]["sha"]
            entries = []
            for path, text in changes.items():
                check_path(path)
                entry = {"path": path, "mode": "100644", "type": "blob"}
                entry.update({"content": text} if text is not None else {"sha": None})
                entries.append(entry)
            new_tree = self._send("POST", "/git/trees", {"base_tree": tree, "tree": entries})
            commit = self._send("POST", "/git/commits", {"message": message, "tree": new_tree["sha"],
                                                         "parents": [head]})
            try:
                self._send("PATCH", f"/git/refs/heads/{urllib.parse.quote(branch, safe='/')}",
                           {"sha": commit["sha"], "force": False})
                return commit["sha"]
            except _Moved:
                continue
        raise SourceError("The branch kept moving while the ADE wrote to it: nothing was written. Try again.")

    def _error(self, status, headers):
        if status in (403, 429) and headers.get("x-ratelimit-remaining") == "0":
            reset = headers.get("x-ratelimit-reset", "")
            when = ""
            if reset.isdigit():
                # how long, not a clock time: the server knows no reader's time zone, and a UTC time reads wrong for most
                minutes = max(1, round((int(reset) - time.time()) / 60))
                when = f" It resets in {minutes} minute{'' if minutes == 1 else 's'}."
            return ("GitHub's rate limit for this connection is used up." + when +
                    ("" if self.token else " A token raises it from 60 to 5,000 requests an hour."))
        if status == 401:
            return "GitHub refused the token: it may have expired or been revoked."
        if status == 404:
            return ("GitHub has no such repository, branch or path, or the token cannot read it"
                    + ("." if self.token else "; a private repository needs a token."))
        return f"GitHub answered {status}."

    def _json(self, path, params=None):
        body = self._get(path, params)  # outside the try: a SourceError is a ValueError, and must keep its words
        try:
            return json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise SourceError("GitHub's answer was not JSON.") from None

    def head(self, branch):
        check_branch(branch)
        ref = self._json(f"/git/ref/heads/{urllib.parse.quote(branch, safe='/')}")
        sha = ((ref or {}).get("object") or {}).get("sha") if isinstance(ref, dict) else None
        if not isinstance(sha, str) or not _SHA.fullmatch(sha):
            raise SourceError(f"GitHub did not give a commit for the branch {branch}.")
        return sha

    def list_dir(self, sha, directory):
        check_sha(sha)
        check_path(directory)
        items = self._json(f"/contents/{urllib.parse.quote(directory)}", {"ref": sha})
        if not isinstance(items, list):
            raise SourceError(f"{directory or 'The top of the repository'} is not a directory at {sha[:7]}.")
        return sorted(i["name"] for i in items if isinstance(i, dict) and i.get("type") == "file"
                      and isinstance(i.get("name"), str))

    def read_file(self, sha, path):
        check_sha(sha)
        check_path(path)
        body = self._get(f"/contents/{urllib.parse.quote(path)}", {"ref": sha}, accept="application/vnd.github.raw")
        return body.decode("utf-8", "replace")

    def file_commits(self, sha, path, limit):
        check_sha(sha)
        check_path(path)
        items = self._json("/commits", {"sha": sha, "path": path, "per_page": int(limit)})
        return [commits_mod.from_github(i) for i in items if isinstance(i, dict)] if isinstance(items, list) else []

    def branch_commits(self, sha, limit):
        check_sha(sha)
        items = self._json("/commits", {"sha": sha, "per_page": int(limit)})
        return [commits_mod.from_github(i) for i in items if isinstance(i, dict)] if isinstance(items, list) else []

    def commit_checks(self, sha):
        """The check runs and commit statuses GitHub reports for `sha`, the first hundred of each, in the shape Verify's
        bundle takes. A private repository's token needs Checks and Commit statuses: read."""
        check_sha(sha)
        runs = self._json(f"/commits/{sha}/check-runs", {"per_page": 100})
        status = self._json(f"/commits/{sha}/status", {"per_page": 100})
        check_runs = [{"name": r.get("name"), "app": (r.get("app") or {}).get("slug"), "status": r.get("status"),
                       "conclusion": r.get("conclusion")}
                      for r in (runs.get("check_runs") if isinstance(runs, dict) else None) or [] if isinstance(r, dict)]
        statuses = [{"context": s.get("context"), "state": s.get("state"),
                     "creator": (s.get("creator") or {}).get("login")}
                    for s in (status.get("statuses") if isinstance(status, dict) else None) or [] if isinstance(s, dict)]
        return check_runs, statuses

    def commit_diff(self, sha, parent):
        check_sha(sha)
        check_sha(parent)
        body = self._get(f"/compare/{parent}...{sha}", accept="application/vnd.github.diff")
        return body.decode("utf-8", "replace")


def api_base():
    """GitHub's API, or a GitHub Enterprise Server's (`https://HOST/api/v3`), from `OPEN_EDITION_GITHUB_API`, an
    admin setting. Only HTTPS, or plain HTTP to this machine for a test server, so the setting cannot point the app's
    requests, and a token, at an address it should not reach in the clear."""
    base = os.environ.get("OPEN_EDITION_GITHUB_API", "").strip().rstrip("/") or "https://api.github.com"
    if ".." in base or not re.fullmatch(r"https://[A-Za-z0-9.-]+(?::\d+)?(?:/[A-Za-z0-9._/-]*)?|http://(?:127\.0\.0\.1|localhost):\d+",
                        base):
        raise SourceError("OPEN_EDITION_GITHUB_API is not an HTTPS address (or HTTP to this machine).")
    return base


def local_repos():
    """The clones an admin named, {name: path}, from `OPEN_EDITION_ADE_LOCAL_REPOS` (`name=/path,name2=/path2`).
    Unset on the hosted site, where it gives none."""
    out = {}
    for item in os.environ.get("OPEN_EDITION_ADE_LOCAL_REPOS", "").split(","):
        name, sep, path = item.strip().partition("=")
        if sep and _LOCAL_NAME.fullmatch(name.strip()) and os.path.isabs(path.strip()):
            out[name.strip()] = path.strip()
    return out
