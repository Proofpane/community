"""The tiers of an agent's knowledge files: the Markdown files under its
repository's `knowledge/` folder, each with frontmatter naming its `status` (current, historical or deprecated), when it
was true (`as_of`), where it came from (`source`) and when it is to be checked again (`verify_by`, else `as_of` and 90
days); a file whose name carries a date (2026-09-29) is historical unless it says otherwise. They are the repository's
files, changed through a run's commit like any other. The sweep reads them, changes nothing, and says which current
files are past their check and which carry no tier."""

import datetime
import os
import re
import stat

FOLDER = "knowledge"
TIERS = ("current", "historical", "deprecated")
VERIFY_DAYS = 90
MAX_FILES = 2000
MAX_HEAD = 4096
_DATED = re.compile(r"\d{4}-\d{2}-\d{2}")
_LINE = re.compile(r"^([a-z_]+):\s*(.*?)\s*$")


def _date(text):
    try:
        return datetime.date.fromisoformat(str(text).strip().strip("\"'")[:10])
    except ValueError:
        return None


def front(text):
    """A file's frontmatter as {key: value}: the lines between a first line `---` and the next."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return out
        m = _LINE.match(line)
        if m:
            out[m.group(1)] = m.group(2).strip("\"'")
    return {}


def scan(root, today=None):
    """{files, current, historical, deprecated, untiered, stale: [{path, verify_by, source}], untiered_paths}: the
    knowledge files under `root`/knowledge, links not followed."""
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    base = os.path.join(root, FOLDER)
    out = {"files": 0, "current": 0, "historical": 0, "deprecated": 0, "untiered": 0, "stale": [], "untiered_paths": []}
    if not os.path.isdir(base) or os.path.islink(base):
        return out
    for dirpath, dirs, names in os.walk(base, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(dirpath, d)))
        for name in sorted(names):
            path = os.path.join(dirpath, name)
            if not name.endswith(".md") or not stat.S_ISREG(os.lstat(path).st_mode):
                continue
            if out["files"] >= MAX_FILES:
                return out
            out["files"] += 1
            rel = os.path.relpath(path, root)
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    meta = front(fh.read(MAX_HEAD))
            except OSError:
                meta = {}
            status = meta.get("status")
            if status not in TIERS:
                status = "historical" if _DATED.search(name) else None
            if status is None:
                out["untiered"] += 1
                out["untiered_paths"].append(rel)
                continue
            out[status] += 1
            if status != "current":
                continue
            as_of = _date(meta.get("as_of"))
            verify_by = _date(meta.get("verify_by")) or (as_of + datetime.timedelta(days=VERIFY_DAYS) if as_of else None)
            if verify_by is None or verify_by < today:
                out["stale"].append({"path": rel, "verify_by": verify_by.isoformat() if verify_by else None,
                                     "source": meta.get("source") or None})
    out["stale"] = out["stale"][:200]
    out["untiered_paths"] = out["untiered_paths"][:200]
    return out
