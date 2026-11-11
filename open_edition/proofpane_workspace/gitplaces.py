"""Git's places, and the state of them the workspace accepted: what a run could leave in a repository, or in git's
settings outside every repository, for the workspace's own git to start later; checked before every git the workspace
runs (workspace._git_env, and gh, which runs git).

A run runs as the workspace's own user, so it can write any repository of the workspace, not only its own, and the
user's own git settings: the gate refuses only what a call names (gate.py). The watch around a run (watch.py,
agents._places) reads its own repository's git places at its start and its end; a change elsewhere would otherwise be
met first by the workspace's own git for the owner's sessions (the size of a session's change, worked out again in the
background; New session, Finish, Update, Ship), which runs with the settings git finds: a hook, an fsmonitor, a
filter, a signing program, a credential helper, a promisor remote a read fetches a missing object from. So the
workspace keeps the state of these places it last accepted, for each repository and for the settings outside them
(USER), and before any git of its own reads them again:

- the configuration, file by file as written (git config --file, which starts no program): the repository's config
  and every config.worktree; outside the repositories, the files git var names and every file git reads outside any
  repository; and every file any of them includes, whatever its condition, so an include whose condition holds only
  where the workspace's git runs is read too. Every key counts but the QUIET ones, known to start no program and to
  reach no address: a key no list names, a new one included, is a change, so the next dangerous key is not missed as
  a list of them would miss it;
- every entry of the git folder's hooks folder but git's samples, and of a hooks folder core.hooksPath names by an
  absolute path, and every entry of its info folder (the attributes and the excludes git reads), and an attributes
  file the settings name. A hooks folder named by a relative path is in the worktree git runs in, among the files a
  commit brings, which a run can choose (it can move a branch): the owner's own actions run no hooks there
  (hooks_relative);
- objects/info/alternates and http-alternates, which lend objects from elsewhere;
- each worktree's record: its commondir must lead back to its git folder and the .git file its gitdir names back to
  the record; and for each folder the workspace runs git in (a repository's, a session's worktree, an agent's), that
  it still finds there the git folder it was made with, its own and its repository's (expect): a repository's .git
  swapped for a file or a link, or a worktree's .git pointed elsewhere (another worktree's record of the same
  repository among it), is a change of that repository.

A change found is accepted at once when no agent's run has worked since the places were last found as accepted: the
owner's own sessions made it (his hooks, installed by a tool). Otherwise the repository (or every git, for the
settings outside them) is held: no git of the workspace's runs there until the owner resumes one of the agents held
for it (agents.py), which accepts its places as they are then.

What was accepted, and what is held, is kept here and in a file of the workspace's root (RECORD, which the gate refuses
a tool call to name), sealed with a key made from the workspace's secret, which no run can read. A file that does not
verify was written by something other than the workspace: it is not believed, and every repository is held, with
every agent, until the owner resumes one. Reading the places runs git only when one of the files and folders it read,
by what lstat and stat give (the change time among them, which no program of the user's can set back), differs from
the last read that matched, and that read was not racy.

It needs only the standard library and watch.py's record of the workspace's own writes: the workspace imports it,
never the reverse.
"""

import fnmatch
import hashlib
import hmac
import json
import os
import shutil
import stat
import subprocess
import threading
import time

RECORD = "git-places.json"
VERSION = 1                     # of what places() reads: a record of another version is read again from the places
USER = "~"                      # the record's name for git's settings outside every repository
SAMPLE = ".sample"
MAX_ENTRIES = 2000              # entries read of a hooks, info or worktrees folder; more is a change in itself
MAX_HASH = 16 << 20             # bytes read for a file's digest; a longer one is known by its stat, which a write moves
MAX_FILES = 64                  # configuration files read, includes among them
MAX_DEPTH = 10                  # includes within includes
GIT_TIMEOUT = 10
HOLD_TEXT = 300                 # characters of the places named in a hold
RACY = 2.0                      # seconds: a read whose files changed this near its start is not kept as matched
UNTRUSTED = "the workspace's record of accepted git settings was changed by something else"
NEVER = "all of it: never accepted"

# configuration keys known to start no program and to reach no address: a change of one is not a change of the places.
# git gives a key's section and name in lower case and a subsection as written, so these match keys as git gives them.
QUIET = ("core.repositoryformatversion", "core.filemode", "core.bare", "core.logallrefupdates", "core.ignorecase",
         "core.precomposeunicode", "core.symlinks", "core.autocrlf", "core.eol", "core.safecrlf",
         "branch.*.remote", "branch.*.pushremote", "branch.*.merge", "branch.*.rebase", "branch.*.description",
         "branch.*.vscode-merge-base", "remote.*.fetch", "remote.*.tagopt", "remote.*.prune", "remote.*.gh-resolved",
         "user.name", "user.email", "pull.rebase", "pull.ff", "push.default", "push.autosetupremote", "fetch.prune",
         "init.defaultbranch", "gc.auto", "gc.autodetach", "advice.*", "color.*", "column.*")

# what the reads here run git with: no hook, no fsmonitor, no lazy fetch, no transport (none of them reads a thing
# that needs one, and git config --file starts none)
_NEUTRAL = (("core.hooksPath", "/dev/null"), ("core.fsmonitor", "false"), ("protocol.allow", "never"))


class Held(Exception):
    """A git the workspace would run where git's places changed since they were accepted, while an agent's run worked:
    refused, with the owner's words."""


def config_env(pairs):
    env = {"GIT_CONFIG_COUNT": str(len(pairs))}
    for i, (key, value) in enumerate(pairs):
        env[f"GIT_CONFIG_KEY_{i}"] = key
        env[f"GIT_CONFIG_VALUE_{i}"] = value
    return env


def _env(extra=None):
    env = {k: os.environ[k] for k in ("PATH", "HOME") if k in os.environ}
    env.update({"LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1"})
    env.update(config_env(_NEUTRAL))
    env.update(extra or {})
    return env


def _git(args, env=None, git="git"):
    """git run from the file system's root, which is no repository: its completed process, or None."""
    try:
        return subprocess.run([git, *args], cwd=os.path.sep, env=env or _env(), capture_output=True,
                              timeout=GIT_TIMEOUT, input=b"")
    except (OSError, subprocess.SubprocessError):
        return None


def git_version(git="git"):
    p = _git(["--version"], git=git)
    return p.stdout.decode("utf-8", "replace").strip() if p is not None and p.returncode == 0 else ""


def _read(path, most):
    """`path`'s first `most` bytes, opened without blocking (a pipe answers at once) and only if it is a file."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{path} is not a file")
        chunks, left = [], most
        while left > 0:
            got = os.read(fd, min(left, 1 << 20))
            if not got:
                break
            chunks.append(got)
            left -= len(got)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _text(path):
    return _read(path, 4096).decode("utf-8", "replace").strip()


def git_folder(top):
    """(git folder, common git folder) that a git run at `top` uses, both real paths, read from what is at `top`
    without running git; None where `top` holds no repository. As git finds it from a repository's top or a worktree's,
    since every git the workspace runs is at one (workspace._git_env sets GIT_CEILING_DIRECTORIES to its parent)."""
    dot = os.path.join(top, ".git")
    try:
        st = os.stat(dot)                                  # a link followed, as git follows it, to what it leads to
    except OSError:
        # a bare repository, or a git folder itself, is its own
        if os.path.isfile(os.path.join(top, "HEAD")) and os.path.isdir(os.path.join(top, "objects")):
            git_dir = os.path.realpath(top)
        else:
            return None
    else:
        if stat.S_ISREG(st.st_mode):
            # a .git file (or a link to one), its gitdir taken from the folder it is named in, as git takes it
            try:
                text = _text(dot)
            except OSError:
                return None
            if not text.startswith("gitdir:"):
                return None
            git_dir = os.path.realpath(os.path.join(top, text[len("gitdir:"):].strip()))
        elif stat.S_ISDIR(st.st_mode):
            git_dir = os.path.realpath(dot)
        else:
            return None
    common = git_dir
    try:
        common = os.path.realpath(os.path.join(git_dir, _text(os.path.join(git_dir, "commondir"))))
    except OSError:
        pass
    return git_dir, common


def _entries(folder):
    try:
        return sorted(os.listdir(folder))
    except OSError:
        return None


def fingerprint(path):
    """What `path` is, in a few words that change when it does: its kind, and a file's mode, size and content's digest
    (a longer one's stat), a link's target and what it leads to, a folder's entries; None where there is none."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if stat.S_ISLNK(st.st_mode):
        try:
            target = os.readlink(path)
        except OSError:
            target = "?"
        return f"link {target} to {fingerprint(os.path.realpath(path))}"
    if stat.S_ISDIR(st.st_mode):
        names = _entries(path) or []
        return "folder " + hashlib.sha256("\0".join(names[:MAX_ENTRIES]).encode("utf-8", "replace")).hexdigest()
    if stat.S_ISREG(st.st_mode):
        if st.st_size > MAX_HASH:
            return f"file {st.st_mode & 0o777:o} {st.st_size} {st.st_mtime_ns} {st.st_ctime_ns} {st.st_ino}"
        try:
            digest = hashlib.sha256(_read(path, MAX_HASH)).hexdigest()
        except OSError:
            digest = "unreadable"
        return f"file {st.st_mode & 0o777:o} {st.st_size} {digest}"
    return f"other {stat.S_IFMT(st.st_mode):o}"


def quiet(key):
    return any(fnmatch.fnmatchcase(key, pattern) for pattern in QUIET)


def _is_include(key):
    return key == "include.path" or (key.startswith("includeif.") and key.endswith(".path"))


def _file_entries(path, git="git"):
    """[(key, value)] of one configuration file as written in it, its includes not followed; None where git cannot
    read it."""
    p = _git(["config", "--file", path, "--list", "-z"],
             env=_env({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}), git=git)
    if p is None or p.returncode != 0:
        return None
    out = []
    for entry in p.stdout.decode("utf-8", "replace").split("\0"):
        if entry:
            key, _, value = entry.partition("\n")
            out.append((key, value))
    return out


def configuration(roots, git="git"):
    """([(file, key, value)] of every configuration file of `roots` and every file they include, whatever the
    include's condition, each as written; [every file named, there or not]). A file git cannot read gives
    (file, "", why)."""
    entries, files, seen = [], [], set()
    queue = [(path, 0) for path in roots]
    while queue:
        path, depth = queue.pop(0)
        path = os.path.normpath(path)
        if path in seen:
            continue
        seen.add(path)
        files.append(path)
        if len(seen) > MAX_FILES or depth > MAX_DEPTH:
            entries.append((path, "", f"included too deep, or too many: {fingerprint(path)}"))
            continue
        try:
            st = os.stat(path)
        except FileNotFoundError:
            continue                                        # not there: named, so its making is seen
        except OSError:
            entries.append((path, "", "unreadable"))
            continue
        if not stat.S_ISREG(st.st_mode):
            entries.append((path, "", "not a file"))
            continue
        got = _file_entries(path, git)
        if got is None:
            entries.append((path, "", "unreadable"))
            continue
        for key, value in got:
            entries.append((path, key, value))
            if _is_include(key) and value:
                # as git takes it: a relative path from the folder of the file that includes it
                target = os.path.expanduser(value)
                queue.append((target if os.path.isabs(target) else os.path.join(os.path.dirname(path), target),
                              depth + 1))
    return entries, files


def _keys(found, entries):
    values = {}
    for path, key, value in entries:
        if not key:
            found["config file " + path] = value
        elif not quiet(key):
            values.setdefault(key, []).append(value)
    for key, kept in values.items():
        found["config " + key] = json.dumps(kept)


def _folder(found, paths, label, folder, samples=False):
    """Every entry of `folder` (git's samples left out where `samples`), as a place; the folder itself where it is not
    one."""
    paths.append(folder)
    kind = fingerprint(folder)
    if kind is None:
        return
    if not kind.startswith("folder "):
        found[label] = kind                                 # a link, or a file where a folder belongs
        if not kind.startswith("link "):
            return
    names = _entries(folder) or []
    if len(names) > MAX_ENTRIES:
        found[label] = f"{len(names)} entries"
        names = names[:MAX_ENTRIES]
    for name in names:
        path = os.path.join(folder, name)
        paths.append(path)
        if samples and name.endswith(SAMPLE):
            continue
        found[f"{label}/{name}"] = fingerprint(path)


def _named(found, paths, entries):
    """The hooks folders and attributes files the settings name by an absolute path, as places."""
    for path, key, value in entries:
        if key not in ("core.hookspath", "core.attributesfile") or not value:
            continue
        named = os.path.expanduser(value)
        if not os.path.isabs(named):
            continue            # a relative hooks folder is in each worktree, among its own files: not read here
        if key == "core.hookspath":
            _folder(found, paths, "hooksPath " + named, named, samples=True)
        else:
            paths.append(named)
            found["attributes " + named] = fingerprint(named)


def places(common, git="git"):
    """({place: fingerprint}, [every file and folder read]) of the repository whose common git folder is `common`."""
    found, paths = {}, []
    worktrees = os.path.join(common, "worktrees")
    names = _entries(worktrees) or []
    roots = [os.path.join(common, "config"), os.path.join(common, "config.worktree")]
    roots += [os.path.join(worktrees, name, "config.worktree") for name in names[:MAX_ENTRIES]]
    entries, files = configuration(roots, git)
    paths += files
    _keys(found, entries)
    _folder(found, paths, "hooks", os.path.join(common, "hooks"), samples=True)
    _folder(found, paths, "info", os.path.join(common, "info"))
    _named(found, paths, entries)
    for name in ("objects/info/alternates", "objects/info/http-alternates"):
        path = os.path.join(common, name)
        paths.append(path)
        kind = fingerprint(path)
        if kind is not None:
            found[name] = kind
    paths.append(worktrees)
    kind = fingerprint(worktrees)
    if kind is not None and not kind.startswith("folder "):
        found["worktrees"] = kind
    if len(names) > MAX_ENTRIES:
        found["worktrees"] = f"{len(names)} entries"
    for name in names[:MAX_ENTRIES]:
        record = os.path.join(worktrees, name)
        kind = fingerprint(record)
        if kind is not None and not kind.startswith("folder "):
            found[f"worktrees/{name}"] = kind
        paths += [os.path.join(record, "commondir"), os.path.join(record, "gitdir")]
        try:
            back = _text(os.path.join(record, "commondir"))
            if os.path.realpath(os.path.join(record, back)) != common:
                found[f"worktrees/{name}/commondir"] = "points elsewhere"
        except OSError:
            pass
        try:
            dotgit = _text(os.path.join(record, "gitdir"))
        except OSError:
            continue                                        # a worktree gone, which git prunes: not a place
        paths.append(dotgit)
        try:
            text = _text(dotgit)
        except OSError:
            continue
        if not text.startswith("gitdir:") or os.path.realpath(
                os.path.join(os.path.dirname(dotgit), text[len("gitdir:"):].strip())) != os.path.realpath(record):
            found[f"worktrees/{name}: its .git"] = "points elsewhere"
    return found, paths


def _user_roots(home, git="git"):
    """The configuration files git reads outside every repository: those git var names, there or not, and every file a
    git config --list outside any repository says it read (a system file of a git's own among them)."""
    roots = []
    for var in ("GIT_CONFIG_SYSTEM", "GIT_CONFIG_GLOBAL"):
        p = _git(["var", var], git=git)
        if p is not None and p.returncode == 0:
            roots += [line.strip() for line in p.stdout.decode("utf-8", "replace").splitlines() if line.strip()]
    if not roots:                                           # a git before 2.42
        roots = ["/etc/gitconfig", os.path.join(home, ".config", "git", "config"), os.path.join(home, ".gitconfig")]
    p = _git(["config", "--list", "--show-origin", "-z"], git=git)
    if p is not None and p.returncode == 0:
        for origin in p.stdout.decode("utf-8", "replace").split("\0")[0::2]:
            if origin.startswith("file:") and origin[len("file:"):] not in roots:
                roots.append(origin[len("file:"):])
    return roots


def user_places(home=None, git="git"):
    """({place: fingerprint}, [every file and folder read]) of git's settings outside every repository: the system's
    and the user's, which every git the workspace runs reads too."""
    home = home or os.path.expanduser("~")
    found, paths = {}, []
    entries, files = configuration(_user_roots(home, git), git)
    paths += files
    _keys(found, entries)
    _named(found, paths, entries)
    default = os.path.join(home, ".config", "git", "attributes")
    paths.append(default)
    kind = fingerprint(default)
    if kind is not None:
        found["attributes " + default] = kind
    return found, paths


def _stamp(path):
    out = []
    for look in (os.lstat, os.stat):
        try:
            st = look(path)
        except OSError:
            out.append(None)
            continue
        out.append((st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino, st.st_dev))
    return tuple(out)


def signature(paths):
    """What lstat and stat give of every path a read read: equal to the last one, nothing of them changed since."""
    return tuple((path, _stamp(path)) for path in paths)


def _racy(sig, since):
    """A file of `sig` changed so near `since` (a read's start) that a change in the same tick could be missed."""
    edge = int((since - RACY) * 1e9)
    return any(s is not None and (s[2] >= edge or s[3] >= edge) for _, stamps in sig for s in stamps)


def changed(before, after):
    """The places that differ, as the owner reads them."""
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


def _norm(path):
    return os.path.normpath(os.path.abspath(path))


def _entry(top, found):
    """What the record keeps of a folder git runs in: its git folder, its repository's, and where it really is."""
    return {"git_dir": found[0], "common": found[1], "real": os.path.realpath(top)}


class Checker:
    """The workspace's record of the git places it accepted, checked before every git of its own.

    `suspects(since)` gives the ids of the agents whose runs worked at any time from `since` (seconds since the epoch)
    to now, read without any lock of theirs; `hold(name, changes, agent_ids, common)` holds those agents (None: every
    agent) and says why, called in a thread of its own, never under a caller's lock; `known(agent_ids)` says whether
    any of them (None: any agent) is still there; `good(common, changes, found)` whether a change is one the
    workspace knows for harmless. `root` None keeps the record in memory alone (a workspace without its secret).
    `folders` are the workspace's own, where every folder git runs in is one of its repositories or worktrees, known
    to the record or not. The workspace calls first_look() with the folders it runs git in at its start, expect() for
    each it makes, and forget() for each it removes."""

    def __init__(self, root, key, suspects, hold, known=None, good=None, folders=(), clock=time.time):
        self.path = os.path.join(root, RECORD) if root else None
        self._folders = [_norm(f) for f in folders]
        self._key = hmac.new(key, b"proofpane git places", hashlib.sha256).digest()
        self._suspects = suspects
        self._hold = hold
        self._known = known or (lambda agent_ids: True)
        self._good = good or (lambda common, changes, found: False)
        self._clock = clock
        self._lock = threading.RLock()
        self._accepted = {}      # common or USER -> {"places": {...}, "at": seconds, "how": words}
        # common or USER -> {"changes": [...], "at": seconds, "agents": [...] or None, "tops", "reported": the agents a
        # record of whose runs has carried the hold to the app, written (reported)}
        self._held = {}
        self._tops = {}          # a folder git runs in, as the workspace names it -> {"git_dir", "common", "real"}
        self._looked = {}        # common or USER -> when its places were last found as accepted, or accepted
        self._seen = {}          # common or USER -> (paths, signature) of the last read that matched what was accepted
        self.started = clock()
        self.fresh = False       # no record: first_look() accepts the places there at the start
        self.untrusted = False   # a record that does not verify
        # the git the workspace's own runs, found on PATH as the record starts: a later change of PATH (a test's)
        # changes neither
        self._gitbin = shutil.which("git", path=os.environ.get("PATH")) or "git"
        self._basis = {"version": VERSION, "git": git_version(self._gitbin)}
        self._load()

    # the record

    def _mac(self, data):
        return hmac.new(self._key, json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                        hashlib.sha256).hexdigest()

    def _load(self):
        if self.path is None:
            self.fresh = True
            return
        try:
            with open(self.path, encoding="utf-8") as fh:
                given = json.load(fh)
        except FileNotFoundError:
            self.fresh = True
            return
        except (OSError, ValueError):
            self.untrusted = True
            return
        mac = given.pop("mac", None) if isinstance(given, dict) else None
        if not isinstance(mac, str) or not hmac.compare_digest(mac, self._mac(given)) or \
                not all(isinstance(given.get(k), dict) for k in ("accepted", "held", "tops", "looked")):
            self.untrusted = True
            return
        self._held = given["held"]
        self._tops = given["tops"]
        if given.get("basis") != self._basis:
            self.fresh = True       # another version's reading, or another git's: what is there now is read again
            return
        self._accepted = given["accepted"]
        self._looked = {c: at for c, at in given["looked"].items() if c in self._accepted}

    def _save(self):
        if self.path is None:
            return
        data = {"basis": self._basis, "accepted": self._accepted, "held": self._held, "tops": self._tops,
                "looked": self._looked}
        text = json.dumps(dict(data, mac=self._mac(data)), sort_keys=True, indent=1)
        with open(self.path + ".tmp", "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(self.path + ".tmp", 0o600)
        os.replace(self.path + ".tmp", self.path)
        from . import watch
        watch.wrote(self.path)

    # where git runs

    def expect(self, top):
        """git runs in `top` (as the workspace names it), with the git folders it has now: (git folder, common git
        folder), or None where it has none. Later ones there are a change of that repository."""
        found = git_folder(top)
        if found is None:
            return None
        with self._lock:
            self._tops[_norm(top)] = _entry(top, found)
            self._save()
        return found

    def forget(self, top):
        with self._lock:
            if self._tops.pop(_norm(top), None) is not None:
                self._save()

    # the check

    def _places(self, common):
        return user_places(git=self._gitbin) if common == USER else places(common, self._gitbin)

    def accept(self, common, how, read=None):
        """Take the places of `common` as they are now (or as `read` gives them) as accepted, and let its git run."""
        with self._lock:
            found, _ = read or self._places(common)
            now = self._clock()
            self._accepted[common] = {"places": found, "at": now, "how": how}
            self._held.pop(common, None)
            self._seen.pop(common, None)
            self._looked[common] = now
            self._save()
            return found

    def _accept_now(self, common, how, read=None, tops=()):
        """Accept `common` and, for each of `tops`, the git folder it has now as its own."""
        for top in tops:
            found = git_folder(top)
            if found is None:
                self._tops.pop(top, None)
                continue
            self._tops[top] = _entry(top, found)
            if found[1] != common and found[1] not in self._accepted:
                self.accept(found[1], how)
        self.accept(common, how, read)

    def held(self):
        with self._lock:
            return {c: dict(h) for c, h in self._held.items()}

    def check_at(self, cwd=None):
        """Before a git of the workspace's in `cwd` (None: in no repository): nothing when git's places there are as
        accepted, or the change is accepted now; else Held, what changed held."""
        self.check(USER)
        if cwd is None:
            return
        top = _norm(cwd)
        with self._lock:
            entry = self._tops.get(top)
            found = git_folder(top)
            if entry is None:
                if found is None:
                    return              # no repository there: git fails, nothing of it runs
                if found[1] not in self._accepted and not any(top == f or top.startswith(f + os.sep)
                                                              for f in self._folders):
                    return              # a repository outside the workspace's folders that it never accepted
                entry = self._tops[top] = _entry(top, found)
                self._save()
            common = entry["common"]
            if found is not None and _entry(top, found) != entry:
                if common in self._held:
                    raise Held(self._words(common, self._held[common]["changes"]))
                self._decide(common, [f"{self._label(top)}: its git folder is elsewhere"], tops=[top])
                common = self._tops[top]["common"]
        self.check(common)

    def check(self, common):
        """Before a git of the workspace's where the places of `common` count: nothing when they are as accepted, or the
        change is accepted now; else Held, `common` held."""
        with self._lock:
            held = self._held.get(common)
            if held is not None:
                if self._known(held["agents"]):
                    raise Held(self._words(common, held["changes"]))
                self._accept_now(common, "every agent held for it is gone", tops=held.get("tops") or ())
                return
            seen = self._seen.get(common)
            if seen is not None and signature(seen[0]) == seen[1]:
                self._looked[common] = self._clock()
                return
            began = time.time()
            found, paths = self._places(common)
            kept = self._accepted.get(common)
            if kept is not None and kept["places"] == found:
                sig = signature(paths)
                if not _racy(sig, began):
                    self._seen[common] = (paths, sig)
                self._looked[common] = self._clock()
                return
            changes = [NEVER] if kept is None else changed(kept["places"], found)
            if kept is not None and not self.untrusted and self._good(common, changes, found):
                self.accept(common, "a change the workspace knows: " + ", ".join(changes), (found, paths))
                return
            self._decide(common, changes, (found, paths))

    def _decide(self, common, changes, read=None, tops=()):
        """A change of `common`: accepted when no agent's run has worked since its places were last found as accepted;
        else held, its suspects held, and Held raised."""
        if self.untrusted:
            agents = None
            changes = [UNTRUSTED]
        else:
            agents = sorted(set(self._suspects(self._looked.get(common, self.started))))
            if not agents:
                self._accept_now(common, "changed while no agent's run worked", read, tops)
                return
        self._held[common] = {"changes": changes, "at": self._clock(), "agents": agents, "tops": sorted(tops)}
        self._seen.pop(common, None)
        self._save()
        threading.Thread(target=self._hold, args=(self._name(common), changes, agents, common), daemon=True,
                         name="pp-git-places-hold").start()
        raise Held(self._words(common, changes))

    def first_look(self, tops, trusted=True):
        """At the workspace's start, with the folders it runs git in: each recorded with the git folders it has now,
        unless the record has it already (what the record says is what a folder is to have). Where no record was there
        before (the first start with this check), and no run worked when the workspace last stopped (`trusted`), the
        places there are accepted as they are; else the first check of each decides."""
        with self._lock:
            commons = set()
            for top in tops:
                found = git_folder(top)
                if found is not None:
                    commons.add(self._tops.setdefault(_norm(top), _entry(top, found))["common"])
            if self.fresh and trusted and not self.untrusted:
                for common in [USER] + sorted(commons):
                    if common not in self._accepted:
                        self.accept(common, "the first look, at the first start with no record of accepted git "
                                            "settings")
            self.fresh = False
            self._save()

    def look(self, agent_id, tops=()):
        """At an agent's run's end, every place checked, in the folders the record knows and in `tops`, every folder
        git runs in there now (a repository the run made, which the record does not know yet, among them: checked now,
        while its run is still a suspect, it is held for it and kept so across restarts; first met after the next
        start, its suspects would be counted from that start, and it taken as the owner's): [(common, name, changes)]
        for each held for that agent (by this look, or before it with that agent among its suspects). None of them is
        marked reported here: the record of the run's end carries them to the app, and only once that record is
        written are they marked (reported, by agents._places_carried and agents._report_late), so a stop between this
        look and that record leaves them to be reported after the next start."""
        with self._lock:
            known = list(self._tops)
            tops = known + sorted({_norm(t) for t in tops} - set(known))
            commons = sorted(set(self._accepted) | {e["common"] for e in self._tops.values()} | {USER})
        for top in tops:
            try:
                self.check_at(top)
            except Held:
                pass
        for common in commons:
            try:
                self.check(common)
            except Held:
                pass
        out = []
        with self._lock:
            for common, held in sorted(self._held.items()):
                if held["agents"] is None or agent_id in held["agents"]:
                    out.append((common, self._name(common), list(held["changes"])))
            self._save()                    # the looks too, so a start after this one suspects only runs after it
        return out

    def holding(self, agent_id):
        """[(common, name, changes)] for each place held for that agent now: with it among its suspects, or for every
        agent."""
        with self._lock:
            return [(common, self._name(common), list(held["changes"])) for common, held in sorted(self._held.items())
                    if held["agents"] is None or agent_id in held["agents"]]

    def unreported(self, agent_id):
        """[(common, name, changes)] for each place held for that agent (holding) that no written record of that
        agent's runs has carried yet (reported): a hold found after the agent's last run ended, or between an end's
        look and the end's own record, or one whose record was not written (the machine stopped first)."""
        return self.for_report(agent_id)[0]

    def for_report(self, agent_id):
        """(unreported, holding) for the agent, read together under one hold of the record's lock: a hold stored
        between two reads would be named in a report's words and not marked reported."""
        with self._lock:
            holding = self.holding(agent_id)
            return [(common, name, changes) for common, name, changes in holding
                    if agent_id not in (self._held[common].get("reported") or ())], holding

    def reported(self, agent_id, commons):
        """A written record of the agent's runs carries the holds of `commons` now: a run's end's own
        (agents._places_carried), or a hold found after it (agents._report_late)."""
        with self._lock:
            for common in commons:
                held = self._held.get(common)
                if held is not None and agent_id not in (held.get("reported") or ()):
                    held["reported"] = sorted(set(held.get("reported") or ()) | {agent_id})
            self._save()

    def release(self, agent_id):
        """The owner resumed the agent: every place held with it among its suspects (or held for every agent) is
        accepted as it is now; and, as the hold's words say, every place there is no record of (after a start with
        none to compare with, or one that did not verify) that no other agent's run could have changed since, which
        would otherwise be held again for the same runs. [names]."""
        with self._lock:
            commons = [c for c, h in self._held.items() if h.get("agents") is None or agent_id in h["agents"]]
            how = f"the owner resumed the agent {agent_id}, held for it"
            names = [self._name(c) for c in commons]
            for common in commons:
                self._accept_now(common, how, tops=self._held.get(common, {}).get("tops") or ())
            if commons:
                self.untrusted = False
                for common in sorted({USER} | {e["common"] for e in self._tops.values()}):
                    if common in self._accepted or common in self._held:
                        continue
                    if not set(self._suspects(self._looked.get(common, self.started))) - {agent_id}:
                        self.accept(common, how + ", with what there was no record of")
                        names.append(self._name(common))
            return names

    def hooks_relative(self, cwd):
        """Whether the settings accepted for the repository `cwd` is in, or outside every repository, name a hooks
        folder by a relative path: one git takes in the worktree it runs in, among the files a commit brings."""
        with self._lock:
            entry = self._tops.get(_norm(cwd))
            for common in [USER] + ([entry["common"]] if entry else []):
                kept = (self._accepted.get(common) or {}).get("places") or {}
                for value in json.loads(kept.get("config core.hookspath") or "[]"):
                    if value and not os.path.isabs(os.path.expanduser(value)):
                        return True
        return False

    def covers(self, path):
        """Whether `path` is where a folder git runs in really is (a repository the desktop app links to)."""
        real = os.path.realpath(path)
        with self._lock:
            return any(e["real"] == real for e in self._tops.values())

    def _label(self, top):
        return os.path.join(os.path.basename(os.path.dirname(top)), os.path.basename(top))

    def _name(self, common):
        if common == USER:
            return "your git settings"
        for top, entry in sorted(self._tops.items()):
            if entry["common"] == common and os.path.basename(os.path.dirname(top)) == "repos":
                return os.path.basename(top)
        return os.path.basename(os.path.dirname(common)) or common

    def _words(self, common, changes):
        some = ", ".join(changes)
        if len(some) > HOLD_TEXT:
            some = some[:HOLD_TEXT].rsplit(", ", 1)[0] + ", and more"
        where = ("Your git settings outside the repositories changed" if common == USER else
                 f"The git settings of the repository {self._name(common)} changed")
        words = (f"{where} while an agent's run worked ({some}), so no git of the workspace's runs "
                 f"{'anywhere' if common == USER else 'there'} until you look at what changed and resume the agent "
                 "held for it.")
        if NEVER in changes or UNTRUSTED in changes:
            words += (" The workspace has no record of them from before: resuming the agent accepts, as they are then, "
                      "every repository's git settings and yours that it has no record of and that no other agent's "
                      "run could have changed.")
        return words
