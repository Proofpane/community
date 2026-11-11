"""An agent's memory: Claude Code's own auto memory, one folder per
agent outside its worktree (agents/<id>/memory, given to each run as autoMemoryDirectory), written and loaded by Claude
Code as everywhere. At each run's end the folder is read as a snapshot (its files' text, within bounds) when it changed,
for the app to keep as a version the owner can roll back to; the files the run read or wrote, and those that changed,
are noted as used at that run. The sweep moves a file unused for the agent's `decay_runs` runs into memory/archive/,
which Claude Code does not load; nothing is deleted, and an archived file comes back on the owner's word. MEMORY.md,
the index Claude Code loads into every session, is never moved.

Every path given from outside (a rollback's files, a file to restore) is a plain relative path under the folder: no
`..`, no absolute path, nothing under archive/, and no link followed on the way. A memory folder or an archive that is
itself a link is left as it is: read as empty, never written through, and no file is opened through a link."""

import hashlib
import json
import os
import shutil
import stat

from .shapes import memory_digest

ARCHIVE = "archive"
INDEX = "MEMORY.md"
USES = "memory-uses.json"               # beside the folder: when each file was last used, by the agent's run count
LAST = "memory-last.json"               # the digest of the last snapshot taken
MAX_FILE = 64 * 1024
MAX_TOTAL = 512 * 1024
MAX_FILES = 200


class MemoryError(ValueError):
    pass


def _regular(path):
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def _plain(path):
    """`path`, a folder of its own (or not there yet), never a link to one elsewhere."""
    if os.path.islink(path):
        raise MemoryError("The memory folder, or its archive, is a link: it is left as it is.")
    return path


def files(folder):
    """The folder's files (not the archive's), as relative paths, links not followed (a folder that is a link holds
    none); at most MAX_FILES."""
    out = []
    if os.path.islink(folder):
        return out
    for root, dirs, names in os.walk(folder, followlinks=False):
        rel_root = os.path.relpath(root, folder)
        dirs[:] = sorted(d for d in dirs if not (rel_root == "." and d == ARCHIVE)
                         and not os.path.islink(os.path.join(root, d)))
        for name in sorted(names):
            path = os.path.join(root, name)
            if _regular(path):
                out.append(os.path.normpath(os.path.join(rel_root, name)))
                if len(out) >= MAX_FILES:
                    return out
    return out


def snapshot(folder):
    """{files: {path: text, or None when it is not text or past MAX_FILE}, digest, bytes}: what the folder holds,
    within MAX_TOTAL of text."""
    out, total, read = {}, 0, {}
    for rel in files(folder):
        try:
            fd = os.open(os.path.join(folder, rel), os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as fh:
                data = fh.read(MAX_FILE + 1)
        except OSError:
            continue
        read[rel] = data
        text = None
        if len(data) <= MAX_FILE and total + len(data) <= MAX_TOTAL:
            try:
                text = data.decode("utf-8")
                total += len(data)
            except UnicodeDecodeError:
                text = None
        out[rel] = text
    return {"files": out, "digest": memory_digest(read), "bytes": total}


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            got = json.load(fh)
        return got if isinstance(got, type(default)) else default
    except (OSError, ValueError):
        return default


def _save(path, value):
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(value, fh)
    os.replace(path + ".tmp", path)


def at_run_end(agent_dir, used):
    """A run ended: the agent's run count one more, the files it used (read or wrote, by path relative to the folder)
    and the ones that changed noted as used at it; the snapshot when the folder changed since the last, else None."""
    folder = os.path.join(agent_dir, "memory")
    uses = _load(os.path.join(agent_dir, USES), {})
    count = (uses.get("runs") if isinstance(uses.get("runs"), int) else 0) + 1
    last = uses.get("used") if isinstance(uses.get("used"), dict) else {}
    taken = snapshot(folder)
    before = _load(os.path.join(agent_dir, LAST), {})
    hashes = before.get("hashes") if isinstance(before.get("hashes"), dict) else {}
    now_hashes = {rel: hashlib.sha256((text or "").encode("utf-8")).hexdigest() if text is not None else "binary"
                  for rel, text in taken["files"].items()}
    for rel in taken["files"]:
        if rel in used or hashes.get(rel) != now_hashes[rel] or rel not in last:
            last[rel] = count
    _save(os.path.join(agent_dir, USES), {"runs": count, "used": {k: v for k, v in last.items() if k in taken["files"]
                                                                  or k.startswith(ARCHIVE + os.sep)}})
    if before.get("digest") == taken["digest"]:
        return None
    _save(os.path.join(agent_dir, LAST), {"digest": taken["digest"], "hashes": now_hashes})
    return taken


def sweep(agent_dir, decay_runs):
    """Files unused for `decay_runs` runs moved to the archive (none when decay_runs is 0): the paths moved."""
    if not decay_runs:
        return []
    folder = os.path.join(agent_dir, "memory")
    if os.path.islink(folder) or os.path.islink(os.path.join(folder, ARCHIVE)):
        return []                           # a link: nothing is moved through it
    uses = _load(os.path.join(agent_dir, USES), {})
    count = uses.get("runs") if isinstance(uses.get("runs"), int) else 0
    last = uses.get("used") if isinstance(uses.get("used"), dict) else {}
    moved = []
    for rel in files(folder):
        if rel == INDEX:
            continue
        if count - (last.get(rel) if isinstance(last.get(rel), int) else count) >= decay_runs:
            try:
                _set_aside(folder, rel)
            except MemoryError:
                continue
            moved.append(rel)
    return moved


def _safe(folder, rel, under=""):
    """The absolute path of `rel` under folder/under, refused when it climbs out, is absolute, names the archive from
    the folder itself, or passes through a link."""
    if not isinstance(rel, str) or not rel or os.path.isabs(rel) or "\0" in rel:
        raise MemoryError("A memory file is named by its path in the memory folder.")
    rel = os.path.normpath(rel)
    if rel.startswith("..") or rel == "." or (not under and rel.split(os.sep)[0] == ARCHIVE):
        raise MemoryError("A memory file is named by its path in the memory folder.")
    base = os.path.join(folder, under) if under else folder
    if os.path.islink(folder) or os.path.islink(base):
        raise MemoryError("A memory file is named by its path in the memory folder.")
    path = os.path.join(base, rel)
    walk = base
    for part in rel.split(os.sep)[:-1]:
        walk = os.path.join(walk, part)
        if os.path.islink(walk):
            raise MemoryError("A memory file is named by its path in the memory folder.")
    if os.path.islink(path):
        raise MemoryError("A memory file is named by its path in the memory folder.")
    return rel, path


def restore(agent_dir, rel):
    """An archived file back where it was, and noted as used now."""
    folder = os.path.join(agent_dir, "memory")
    rel, source = _safe(folder, rel, under=ARCHIVE)
    _, target = _safe(folder, rel)
    if not _regular(source):
        raise MemoryError("No such archived file.")
    if os.path.exists(target):
        raise MemoryError("A file of that name is in the memory already.")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    os.replace(source, target)
    uses = _load(os.path.join(agent_dir, USES), {})
    used = uses.get("used") if isinstance(uses.get("used"), dict) else {}
    used[rel] = uses.get("runs") if isinstance(uses.get("runs"), int) else 0
    _save(os.path.join(agent_dir, USES), dict(uses, used=used))
    return rel


def _set_aside(folder, rel):
    """A memory file moved into the archive, beside what is there (a number added to its name when that is taken):
    the archive keeps it, and nothing is lost. Its new path in the archive."""
    stem, ext = os.path.splitext(rel)
    name, n = rel, 1
    while True:
        name, target = _safe(folder, name, under=ARCHIVE)
        if not os.path.lexists(target):
            break
        name, n = f"{stem}.{n}{ext}", n + 1
    os.makedirs(os.path.dirname(target), exist_ok=True)
    os.replace(os.path.join(folder, rel), target)
    return name


def put(agent_dir, given):
    """The memory folder made what a version held: each file's text written, and then each file not in it moved into
    the archive (_set_aside), never deleted: a version keeps a file's text only within its bounds, so a rollback
    to it leaves a larger one there."""
    if not isinstance(given, dict) or len(given) > MAX_FILES:
        raise MemoryError(f"A memory version is at most {MAX_FILES} files, each its path and its text.")
    folder = _plain(os.path.join(agent_dir, "memory"))
    os.makedirs(folder, exist_ok=True)
    wanted, total = {}, 0
    for rel, text in given.items():
        rel, path = _safe(folder, rel)
        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_FILE:
            raise MemoryError(f"A memory file's text is at most {MAX_FILE // 1024} KB.")
        total += len(text.encode("utf-8"))
        wanted[rel] = (path, text)
    if total > MAX_TOTAL:
        raise MemoryError(f"A memory version is at most {MAX_TOTAL // 1024} KB.")
    _plain(os.path.join(folder, ARCHIVE))
    for rel, (path, text) in wanted.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(path + ".tmp", path)
    for rel in files(folder):
        if rel not in wanted:
            _set_aside(folder, rel)
    return sorted(wanted)


def listing(agent_dir):
    """{runs, files: [{path, bytes, runs_ago}], archived: [{path, bytes}]}: the folder now, and how long since each
    file was used."""
    folder = os.path.join(agent_dir, "memory")
    uses = _load(os.path.join(agent_dir, USES), {})
    count = uses.get("runs") if isinstance(uses.get("runs"), int) else 0
    last = uses.get("used") if isinstance(uses.get("used"), dict) else {}
    size = lambda p: os.lstat(p).st_size if _regular(p) else 0  # noqa: E731
    archived = files(os.path.join(folder, ARCHIVE)) if os.path.isdir(os.path.join(folder, ARCHIVE)) and not \
        os.path.islink(os.path.join(folder, ARCHIVE)) else []
    return {"runs": count,
            "files": [{"path": rel, "bytes": size(os.path.join(folder, rel)),
                       "runs_ago": count - last[rel] if isinstance(last.get(rel), int) else None}
                      for rel in files(folder)],
            "archived": [{"path": rel, "bytes": size(os.path.join(folder, ARCHIVE, rel))} for rel in archived]}


def remove_tree(path):
    shutil.rmtree(path, ignore_errors=True)
