"""What an agent's run could leave behind to run later, outside its claude, its time limit and its gate: the places
looked at when a run starts and again at its end, before anything of git runs there.

A run runs as the workspace's own user, so what it can write, it can write: the gate's rules for agent runs check
what a call names, and a program a run starts names nothing. What the workspace holds to is narrower and checked: its
own git at a run's end runs nothing the run planted, and what the run changed where code is set to start later is
named to the owner, who looks before the agent runs again.

Two kinds of place:

- git's: the repository's settings (its own config, the worktree's, the user's, and any file they include), its
  hooks and its info folder (the attributes and the excludes git reads), and the worktree's link to the repository
  (its .git file, and the repository's record of each worktree's). A change there can make the next git the workspace
  runs in that repository start a program (a filter, a hook, an fsmonitor) or read another repository: at a run's end,
  a change there means no git at all.
- the programs' own: Claude Code's settings, its MCP servers, commands, agents, skills and plugins, the claude build in
  use and the wrapper the chats start it through, the other agents' settings, the shells' start files, and the
  workspace's records that say what an agent's runs may do. A change there starts in the owner's next sessions or the
  agent's next runs.

A place is read by what lstat gives (its kind, mode, owner, size, times and inode, with the change time no program of
the user's can set back), a folder by every entry under it, up to MAX_ENTRIES each; no link is followed. A record is
read by the part of it that matters (a file the programs rewrite all the time, as Claude Code's own .claude.json, by
its MCP servers alone). A file the workspace itself writes from what it holds (an agent as the app mirrored it, the
chats' wrapper) is told apart by its own last write (wrote): as it left it, it is no change.
"""

import hashlib
import json
import os
import stat
import threading

MAX_ENTRIES = 5000
_OWN = {}                       # path -> what lstat gave just after the workspace wrote it
_own_lock = threading.Lock()


def wrote(path):
    """The workspace wrote `path` itself, from what it holds: as it is now, it is not a run's change."""
    got = _stat(path)
    with _own_lock:
        _OWN[path] = got


def _stat(path):
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as e:
        return ("unreadable", e.errno)
    out = (stat.S_IFMT(st.st_mode), stat.S_IMODE(st.st_mode), st.st_uid, st.st_size, st.st_ino, st.st_mtime_ns,
           st.st_ctime_ns)
    if stat.S_ISLNK(st.st_mode):
        try:
            out += (os.readlink(path),)
        except OSError:
            pass
    return out


def _walk(path, out, budget):
    """Every entry under the folder `path`, by its own lstat, links not followed; at most `budget[0]` of them."""
    try:
        names = sorted(os.listdir(path))
    except OSError:
        return
    for name in names:
        if budget[0] <= 0:
            out[path + os.sep + "..."] = ("more than", MAX_ENTRIES)
            return
        budget[0] -= 1
        child = os.path.join(path, name)
        got = _stat(child)
        out[child] = got
        if got and got[0] == stat.S_IFDIR:
            _walk(child, out, budget)


def look(paths, records=()):
    """{place: what it is now}: each path's lstat (None when there is none), a folder's every entry under it, and each
    record's digest: `records` is [(name, function giving the part that matters)]."""
    out = {}
    for path in dict.fromkeys(paths):
        got = _stat(path)
        out[path] = got
        if got and got[0] == stat.S_IFDIR:
            _walk(path, out, [MAX_ENTRIES])
    for name, read in records:
        try:
            part = read()
        except Exception as e:  # noqa: BLE001  a record that cannot be read now is a change from one that could
            part = f"unreadable: {type(e).__name__}"
        out["record:" + name] = hashlib.sha256(json.dumps(part, sort_keys=True, default=str).encode()).hexdigest()
    return out


def changes(before, after):
    """The places that differ: made, removed or changed, but by the workspace's own last write."""
    with _own_lock:
        own = dict(_OWN)
    return sorted(k for k in set(before) | set(after)
                  if before.get(k, "absent") != after.get(k, "absent") and not (k in own and after.get(k) == own[k]))


def json_part(path, keys):
    """The parts of a JSON file that matter, by key paths: ("mcpServers",), ("projects", "*", "mcpServers"). None for a
    file there is none of."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None

    def pick(node, rest):
        if not rest:
            return node
        if not isinstance(node, dict):
            return None
        if rest[0] == "*":
            return {k: pick(v, rest[1:]) for k, v in sorted(node.items())}
        return pick(node.get(rest[0]), rest[1:])
    return {"/".join(k): pick(data, k) for k in keys}


def named(places, home, root, repository):
    """The places, as the owner reads them: under the home folder from ~, the workspace's own from its root, the
    repository's from its .git; a folder left out when an entry under it is named."""
    places = [p for p in places if p.startswith("record:") or not any(q.startswith(p + os.sep) for q in places)]
    out = []
    for place in places:
        if place.startswith("record:"):
            out.append(place[len("record:"):])
            continue
        if place.endswith(os.sep + "..."):
            place = place[:-4] + " (more entries than are read)"
        for prefix, label in ((repository, "the repository's .git/"), (root, "the workspace's "), (home, "~/")):
            if prefix and (place == prefix or place.startswith(prefix + os.sep)):
                place = (label + place[len(prefix):].lstrip(os.sep)).rstrip("/ ")
                break
        out.append(place)
    return list(dict.fromkeys(out))
