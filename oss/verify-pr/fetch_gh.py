#!/usr/bin/env python3
"""Sample agent-authored pull requests and build their bundles with the GitHub CLI (`gh`), on a machine that has it.

    python3 fetch_gh.py all     --exclude-owner OWNER [--exclude-owner OWNER ...] --out DIR
    python3 fetch_gh.py sample  --exclude-owner OWNER --out sample.json [--since DAY] [--until DAY] [--agents FILE]
    python3 fetch_gh.py configs --sample sample.json --out configs/
    python3 fetch_gh.py bundles --sample sample.json --out bundles/
    python3 fetch_gh.py bundles --repo owner/name --prs 12,15 --out bundles/

`all` runs the other three into DIR: `sample.json`, `search-results.jsonl`, `configs/` and `bundles/`.

`sample` searches for the agents' pull requests and chooses repositories by their counts. The window is the
90 complete UTC days before the day of the run unless `--since` and `--until` say otherwise. GitHub's search returns
at most 1,000 results for one query, so a window with more is split by date, down to single days, and a day with more
is read one page deep and marked partial. If any slice was partial, the repositories the search saw are ranked by
those partial counts only to choose the 30 to count exactly, with searches limited to one repository each, and the
choice is made on the exact counts. A qualifier GitHub rejects (a bot name that does not exist) is recorded and its
query contributes nothing; any other failure stops the draw, which writes the queries it finished, their results and
the error, and exits 1, to be run again: the results of the query it was reading when it failed are not written.

What is recorded: `sample.json` holds the window, the agents searched for, every slice of every query with its total
and how much of it was read, the counts, why each passed-over repository was passed over, and the choice;
`search-results.jsonl` holds every result of every finished query, one per line (repository, number, merge time,
author and its type, the query, and whether it qualifies). Descriptions are not recorded there: they are in the
bundles.

`configs` writes one draft configuration per chosen repository, with the job names found in its workflow files and
its top-level entries, for a person to complete and freeze before anything runs. `bundles` writes one JSON bundle per
pull request for `python3 -m verify_pr summarize`, skipping bundles already written, so a failed run can be repeated.

Tested against a fake `gh` in tests/test_fetch.py; nothing here has been run against GitHub itself.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_pr.extract import extract  # noqa: E402

# Search qualifiers for agent-authored pull requests. The bot names are unconfirmed: a name GitHub rejects is recorded
# as rejected, so check the record before relying on the sample.
DEFAULT_AGENTS = {
    "bot_authors": ["app/copilot-swe-agent", "app/devin-ai-integration", "app/claude",
                    "app/chatgpt-codex-connector", "app/cursor"],
    "footers": ["Generated with [Claude Code]"],
}
PER_REPO = 20
REPOS = 5
MIN_QUALIFYING = 20
WINDOW_DAYS = 90
SEARCH_CAP = 1000  # GitHub's search returns at most 1,000 results for one query
PAGE = 100
CANDIDATES = 30  # repositories counted exactly when the search was partial
SEARCH_INTERVAL = 2.1  # seconds between search requests: the search API allows 30 a minute
MAX_ISSUE_REFS = 50  # issue references looked up per pull request, besides those its claims make

_sleep = time.sleep


class GhError(RuntimeError):
    """A `gh api` call that failed; `status` is the HTTP status when gh reported one, and `retryable` says whether it
    was a rate limit or an outage that outlasted the retries, which a later run can get past."""

    def __init__(self, path, status, message, retryable=False):
        super().__init__(f"gh api {path}" + (f" (HTTP {status})" if status else "") + f": {message}")
        self.path, self.status, self.retryable = path, status, retryable


_RATE_LIMITED = re.compile(r"rate limit|secondary rate|abuse detection", re.I)


def _gh(args, path, tries=6):
    """Run `gh api ARGS PATH`, waiting and retrying while GitHub says it is rate limited or briefly unavailable."""
    for attempt in range(tries):
        out = subprocess.run(["gh", "api", *args, path], capture_output=True, encoding="utf-8", errors="replace")
        if out.returncode == 0:
            return out.stdout
        m = re.search(r"\(HTTP (\d{3})\)", out.stderr)
        status = int(m.group(1)) if m else None
        retry = (status in (403, 429) and _RATE_LIMITED.search(out.stderr)) or status in (500, 502, 503, 504)
        if retry and attempt < tries - 1:
            _sleep(min(30 * 2 ** attempt, 600))
            continue
        raise GhError(path, status, out.stderr.strip()[:300], retryable=bool(retry))
    raise AssertionError("unreachable")


def gh_json(path, paginate=False, jq=None):
    args = ["-H", "Accept: application/vnd.github+json"]
    if paginate:
        args.append("--paginate")
    if jq:
        args += ["--jq", jq]
    text = _gh(args, path)
    try:
        if jq:  # one JSON value per line; split on "\n" only, since a JSON string may hold U+2028 unescaped
            return [json.loads(line) for line in text.split("\n") if line.strip()]
        return json.loads(text)
    except ValueError as e:  # a response that is not JSON is a failed call, not a crash
        raise GhError(path, None, f"the response is not JSON: {e}") from e


def gh_text(path):
    return _gh(["-H", "Accept: application/vnd.github.raw"], path)


_last_search = [float("-inf")]


def search(query, page):
    """One page of an issue search, newest first, keeping to the search API's rate."""
    wait = SEARCH_INTERVAL - (time.monotonic() - _last_search[0])
    if wait > 0:
        _sleep(wait)
    _last_search[0] = time.monotonic()
    q = urllib.parse.quote(query, safe="")
    return gh_json(f"search/issues?q={q}&per_page={PAGE}&page={page}&sort=created&order=desc")


def repo_of(item):
    # "https://api.github.com/repos/OWNER/NAME" -> "OWNER/NAME"
    return "/".join(item["repository_url"].rstrip("/").split("/")[-2:])


def search_window(query, since, until, run_search):
    """Every result of `query` merged between `since` and `until`, inclusive dates, each pull request once, and a
    record of each slice read.

    A window whose total is over the cap is halved by date until it is not, or is one day; a single day over the cap
    is read one page deep and marked partial."""
    q = f"{query} merged:{since}..{until}"
    first = run_search(q, 1)
    total = first.get("total_count", 0)
    if total > SEARCH_CAP and since != until:
        start, end = date.fromisoformat(since), date.fromisoformat(until)
        mid = start + timedelta(days=((end - start).days + 1) // 2 - 1)
        a_items, a_slices = search_window(query, since, mid.isoformat(), run_search)
        b_items, b_slices = search_window(query, (mid + timedelta(days=1)).isoformat(), until, run_search)
        return a_items + b_items, a_slices + b_slices
    items, seen = [], set()
    incomplete = bool(first.get("incomplete_results"))

    def add(batch):
        for it in batch:
            key = (repo_of(it), it["number"])
            if key not in seen:
                seen.add(key)
                items.append(it)

    add(first.get("items", []))
    page = 1
    # the order can shift between pages; a repeat is dropped above, and reading stops at the cap either way
    while total <= SEARCH_CAP and len(items) < total and page * PAGE < SEARCH_CAP:
        page += 1
        res = run_search(q, page)
        incomplete = incomplete or bool(res.get("incomplete_results"))
        batch = res.get("items", [])
        if not batch:
            break
        add(batch)
    complete = total <= SEARCH_CAP and len(items) >= total and not incomplete
    return items, [{"since": since, "until": until, "total_count": total, "read": len(items), "complete": complete}]


def _last_line(body):
    lines = [x for x in re.split(r"\r\n|\r|\n", body or "") if x.strip()]
    return lines[-1] if lines else ""


def qualifies(item, footers=None):
    """The definition of agent-authored, applied to one search result: merged, and either a bot author (the
    query named it), or, for a footer query, a person as the author and a description whose last non-empty line
    carries the footer."""
    if not (item.get("pull_request") or {}).get("merged_at"):
        return False
    user_type = (item.get("user") or {}).get("type")
    if footers is None:
        return user_type == "Bot"
    return user_type == "User" and any(f in _last_line(item.get("body")) for f in footers)


def _row(item, stage, query, scope, ok):
    user = item.get("user") or {}
    return {"stage": stage, "query": query, "scope": scope, "repo": repo_of(item), "number": item["number"],
            "merged_at": (item.get("pull_request") or {}).get("merged_at"),
            "author": user.get("login"), "author_type": user.get("type"), "qualifies": ok}


def agent_queries(agents):
    base = "is:pr is:merged is:public"
    return ([(f"{base} author:{a}", None) for a in agents["bot_authors"]] +
            [(f'{base} "{f}" in:body', [f]) for f in agents["footers"]])


def _qualifying(queries, since, until, run_search, stage, record, rows, scope=None):
    """{repo: {number: row}} over every query, each pull request once, with the first query that found it. Each
    query's slices go into `record`, each result into `rows`. A query GitHub rejects (HTTP 422) is recorded as
    rejected and contributes nothing; any other failure raises."""
    found = {}
    for query, footers in queries:
        q = f"{query} repo:{scope}" if scope else query
        try:
            items, slices = search_window(q, since, until, run_search)
        except GhError as e:
            if e.status != 422:
                raise
            record[query] = {"rejected": str(e)[:300], "slices": []}
            continue
        record[query] = {"rejected": None, "slices": slices}
        for it in items:
            row = _row(it, stage, query, scope, qualifies(it, footers))
            rows.append(row)
            if row["qualifies"]:
                found.setdefault(row["repo"], {}).setdefault(row["number"], row)
    return found


def unreadable(e):
    """A repository that is gone, blocked or private answers like this; a rate limit or an outage does not."""
    return e.status in (404, 410, 451) or (e.status == 403 and not _RATE_LIMITED.search(str(e)))


def rank(counts, exclude_owners, is_fork, min_qualifying=MIN_QUALIFYING, repos=REPOS):
    """The choice of repositories, from {repo: count}: the first `repos` by count (ties by name) that are not excluded, are not
    forks and reach `min_qualifying`. Returns (chosen, reasons), reasons naming why each passed-over repo was."""
    chosen, reasons = [], {}
    for repo, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        if len(chosen) == repos:
            break
        if repo.split("/")[0].lower() in exclude_owners:
            reasons[repo] = "excluded owner"
            continue
        if n < min_qualifying:
            reasons[repo] = f"{n} qualifying, under {min_qualifying}"
            continue
        try:
            fork = is_fork(repo)
        except GhError as e:
            if not unreadable(e):
                raise
            reasons[repo] = f"could not be read: {str(e)[:200]}"
            continue
        if fork:
            reasons[repo] = "fork"
        else:
            chosen.append(repo)
    return chosen, reasons


def most_recent(prs, n=None):
    return sorted(prs.values(), key=lambda p: (p["merged_at"] or "", p["number"]), reverse=True)[:n or PER_REPO]


def draw_sample(agents, since, until, exclude_owners, run_search, is_fork):
    """The search and the choice. Pure except for `run_search(query, page)` and `is_fork(repo)`.
    Returns (record, rows); on a failure the record carries `error` and no choice."""
    queries = agent_queries(agents)
    record = {"window": {"since": since, "until": until}, "agents": agents,
              "excluded_owners": sorted(exclude_owners), "search": {}, "counts": {}}
    rows = []
    try:
        seen = _qualifying(queries, since, until, run_search, "search", record["search"], rows)
    except GhError as e:
        return {**record, "error": str(e)}, rows
    partial = any(not s["complete"] for q in record["search"].values() for s in q["slices"])
    record["partial"] = partial
    sampled = {repo: len(prs) for repo, prs in seen.items()}
    record["counts"]["sampled"] = sampled
    if partial:
        usable = [(q, f) for q, f in queries if record["search"][q]["rejected"] is None]
        candidates = [r for r, _ in sorted(sampled.items(), key=lambda kv: (-kv[1], kv[0]))
                      if r.split("/")[0].lower() not in exclude_owners][:CANDIDATES]
        record["candidates"] = candidates
        exact, exact_record = {}, {}
        for repo in candidates:
            exact_record[repo] = {}
            try:
                exact[repo] = _qualifying(usable, since, until, run_search, "count", exact_record[repo], rows,
                                          scope=repo).get(repo, {})
            except GhError as e:
                record["count_search"] = exact_record
                return {**record, "error": f"counting {repo}: {e}"}, rows
        record["count_search"] = exact_record
        record["counts"]["exact_complete"] = all(q["rejected"] is None and all(s["complete"] for s in q["slices"])
                                                 for r in exact_record.values() for q in r.values())
        prs_of = exact
    else:
        prs_of = seen
    counts = {r: len(p) for r, p in prs_of.items()}
    if partial:
        record["counts"]["exact"] = counts
    try:
        chosen, reasons = rank(counts, exclude_owners, is_fork)
    except GhError as e:
        return {**record, "error": f"checking for forks: {e}"}, rows
    for repo, searches in record.get("count_search", {}).items():
        if repo in reasons and searches and all(q["rejected"] for q in searches.values()):
            # GitHub rejects a repository-limited search for a repository it no longer knows (renamed, removed)
            reasons[repo] = "could not be counted: every search limited to it was rejected"
    record["passed_over"] = reasons
    record["chosen"] = [{"repo": r, "qualifying": counts[r], "prs": most_recent(prs_of[r])} for r in chosen]
    record["shortfall"] = REPOS - len(chosen)
    return record, rows


def _window(args):
    """(since, until) as dates: by default the WINDOW_DAYS complete UTC days before today."""
    today = datetime.now(timezone.utc).date()
    until = date.fromisoformat(args.until) if args.until else today - timedelta(days=1)
    since = date.fromisoformat(args.since) if args.since else until - timedelta(days=WINDOW_DAYS - 1)
    return since.isoformat(), until.isoformat()


def _fetcher_commit():
    """The commit this file was run from, and whether the fetcher or the prototype differ from it; None outside a
    git checkout."""
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        head = subprocess.run(["git", "-C", here, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30)
        status = subprocess.run(["git", "-C", here, "status", "--porcelain", "--", "."], capture_output=True,
                                text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if head.returncode or status.returncode:
        return None
    return {"commit": head.stdout.strip(), "modified": bool(status.stdout.strip())}


def _write_json(path, obj, indent=1):
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=indent, ensure_ascii=True)
        f.write("\n")
    os.replace(tmp, path)


def cmd_sample(args):
    agents = DEFAULT_AGENTS
    if args.agents:
        with open(args.agents, encoding="utf-8") as f:
            agents = json.load(f)
    since, until = _window(args)
    fork_cache = {}

    def is_fork(repo):
        if repo not in fork_cache:
            fork_cache[repo] = bool(gh_json(f"repos/{repo}").get("fork"))
        return fork_cache[repo]

    drawn_at = datetime.now(timezone.utc).isoformat()
    record, rows = draw_sample(agents, since, until, {o.lower() for o in args.exclude_owner}, search, is_fork)
    results = os.path.splitext(args.out)[0] + ".results.jsonl" if args.results is None else args.results
    record = {"drawn_at": drawn_at, "fetcher": "oss/verify-pr/fetch_gh.py", "fetcher_commit": _fetcher_commit(),
              "results_file": os.path.basename(results), **record}
    tmp = results + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=True) + "\n")
    os.replace(tmp, results)
    _write_json(args.out, record)
    if "error" in record:
        print(f"the draw failed and must be run again: {record['error']}", file=sys.stderr)
        return 1
    rejected = [q for q, r in record["search"].items() if r["rejected"]]
    uncounted = sum(1 for r in record.get("count_search", {}).values() for q in r.values() if q["rejected"])
    print(f"{len(record['chosen'])} repositories chosen, shortfall {record['shortfall']}, "
          f"partial search: {record['partial']}, rejected queries: {len(rejected)}, rejected count searches: "
          f"{uncounted}; written to {args.out}")
    return 0


def workflow_job_names(text):
    """Job ids and `name:` values under `jobs:` in a workflow file, without a YAML parser."""
    names, in_jobs, job_indent = [], False, None
    for line in text.splitlines():
        if re.match(r"^jobs:\s*$", line):
            in_jobs = True
            continue
        if in_jobs and re.match(r"^\S", line):
            break
        if not in_jobs:
            continue
        m = re.match(r"^(\s+)([\w-]+):\s*$", line)
        if m and (job_indent is None or len(m.group(1)) == job_indent):
            job_indent = len(m.group(1))
            names.append(m.group(2))
            continue
        n = re.match(r"^(\s+)name:\s*['\"]?(.+?)['\"]?\s*$", line)
        if n and job_indent is not None and len(n.group(1)) == job_indent + 2 and names:
            names[-1] = n.group(2)
    return names


def cmd_configs(args):
    with open(args.sample, encoding="utf-8") as f:
        sample = json.load(f)
    os.makedirs(args.out, exist_ok=True)
    for entry in sample["chosen"]:
        repo = entry["repo"]
        jobs, errors, top = [], [], []
        try:
            for item in gh_json(f"repos/{repo}/contents/.github/workflows"):
                if item["name"].endswith((".yml", ".yaml")):
                    text = gh_text(f"repos/{repo}/contents/{item['path']}")
                    jobs += [{"workflow": item["name"], "job": j} for j in workflow_job_names(text)]
        except GhError as e:
            errors.append(str(e)[:300])
        try:
            top = sorted(f"{x['name']}/" if x.get("type") == "dir" else x["name"]
                         for x in gh_json(f"repos/{repo}/contents"))
        except GhError as e:
            errors.append(str(e)[:300])
        draft = {"repo": repo, "notes": "DRAFT: choose expected_checks from candidate_jobs (name and app), set "
                                        "repository-specific globs, then delete this note. Freeze before running.",
                 "expected_checks": [], "candidate_jobs": jobs, "top_level": top, "listing_errors": errors}
        path = os.path.join(args.out, repo.replace("/", "__") + ".json")
        _write_json(path, draft, indent=2)
        print(f"draft configuration: {path}")
    return 0


def build_bundle(repo, pr, files, check_runs, status, reviews, issues, qualified_by=None, issue_errors=None):
    """Assemble a bundle from GitHub API responses. Pure."""
    return {
        "repo": repo,
        "number": pr["number"],
        "title": pr.get("title") or "",
        "body": pr.get("body") or "",
        "author": {"login": (pr.get("user") or {}).get("login"), "type": (pr.get("user") or {}).get("type")},
        "qualified_by": qualified_by,
        "merged": bool(pr.get("merged_at")),
        "base_sha": pr["base"]["sha"],
        "head_sha": pr["head"]["sha"],
        # how many files the pull request changed, against which the list below is checked: the list endpoint
        # returns at most 3,000
        "changed_files": pr.get("changed_files"),
        # patch is null when GitHub leaves it out (a large diff, a binary file); additions, null when GitHub did not
        # say, tells the two apart
        "files": [{"path": f["filename"], "status": f.get("status"), "previous_path": f.get("previous_filename"),
                   "patch": f.get("patch"), "additions": f.get("additions")} for f in files],
        "check_runs": [{"name": c.get("name"), "app": (c.get("app") or {}).get("slug"), "status": c.get("status"),
                        "conclusion": c.get("conclusion")} for c in check_runs],
        "statuses": [{"context": s.get("context"), "state": s.get("state"),
                      "creator": (s.get("creator") or {}).get("login")} for s in (status or {}).get("statuses", [])],
        "reviews": [{"user": (r.get("user") or {}).get("login"), "state": r.get("state"),
                     "commit_id": r.get("commit_id")} for r in reviews],
        # what GitHub said about each referenced issue; a lookup that failed is not there but in issue_errors, so a
        # verifier that reads only `issues` treats it as not fetched
        "issues": issues,
        "issue_errors": issue_errors or {},
    }


# an owner and a repository name as GitHub allows them; anything else (`../user`) is not looked up
_REPO_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/(?!\.\.?\Z)[A-Za-z0-9._-]{1,100}")


def issue_record(repo, target, number, fetch=None):
    """What GitHub says about one issue referenced from a pull request in `repo`, asked of `target`: `{"exists": True}`
    for an issue; `{"exists": False, "is_pull_request": True}` for a pull request's number; `{"exists": False}` when
    GitHub says there is no such issue; or `{"error": why}` when that could not be learned, which is not the same
    thing. `exists` is True only for an issue, so a verifier that reads nothing else cannot pass a pull request.

    A 404 decides only in `repo` itself, which is public: GitHub answers 404 for a repository it does not show, so in
    another repository a 404 may be a private one. A 410 decides anywhere (the issue was deleted, or the repository
    has no issues). A rate limit or an outage that outlasted the retries is raised, so the bundle is not written and a
    later run fetches it rather than keeping the failure."""
    fetch = fetch or gh_json
    if not _REPO_NAME.fullmatch(target):
        return {"error": "not a repository name GitHub allows"}
    try:
        it = fetch(f"repos/{target}/issues/{number}")
    except GhError as e:
        if e.retryable:
            raise
        if e.status == 410 or (e.status == 404 and target.lower() == repo.lower()):
            return {"exists": False}
        if e.status == 404:
            return {"error": "HTTP 404, which another repository gives when it is private or missing"}
        return {"error": f"HTTP {e.status}" if e.status else "no HTTP status"}
    if "pull_request" in it:
        return {"exists": False, "is_pull_request": True}
    return {"exists": True}


# the reference part of extract.py's issue pattern, so a key made here is the key a claim looks up; the repository
# starts where a word does, as there, so a long word is not searched once from each of its characters
_ISSUE_REF = re.compile(r"(?:(?<![\w.-])(?P<repo>[\w.-]+/[\w.-]+))?#(?P<num>\d{1,10})\b")


def issue_refs(repo, title, body):
    """{key: (repo to ask, number)} for every issue a claim references, and for up to MAX_ISSUE_REFS further
    references anywhere in the title and description, so a later version of the extractor finds what it needs."""
    refs = {}

    def add(ref_repo, num):
        key = f"{ref_repo}#{num}" if ref_repo else str(num)
        refs.setdefault(key, (ref_repo or repo, num))

    claims, _ = extract(title, body)
    for c in claims:
        if c["resolver"] == "issue.linked":
            add(c["args"]["repo"], c["args"]["issue"])
    budget = len(refs) + MAX_ISSUE_REFS
    for m in _ISSUE_REF.finditer(f"{title or ''}\n{body or ''}"):
        if len(refs) >= budget:
            break
        add(m.group("repo"), int(m.group("num")))
    return refs


def fetch_bundle(repo, number, qualified_by=None):
    pr = gh_json(f"repos/{repo}/pulls/{number}")
    files = gh_json(f"repos/{repo}/pulls/{number}/files?per_page=100", paginate=True, jq=".[]")
    head = pr["head"]["sha"]
    runs = gh_json(f"repos/{repo}/commits/{head}/check-runs?per_page=100", paginate=True, jq=".check_runs[]")
    # the combined status lists 30 contexts a page unless asked for more
    status = {"statuses": gh_json(f"repos/{repo}/commits/{head}/status?per_page=100", paginate=True,
                                  jq=".statuses[]")}
    reviews = gh_json(f"repos/{repo}/pulls/{number}/reviews?per_page=100", paginate=True, jq=".[]")
    issues, issue_errors = {}, {}
    for key, (target, num) in issue_refs(repo, pr.get("title"), pr.get("body")).items():
        rec = issue_record(repo, target, num)
        if "error" in rec:
            issue_errors[key] = rec["error"]
        else:
            issues[key] = rec
    return build_bundle(repo, pr, files, runs, status, reviews, issues, qualified_by, issue_errors)


def cmd_bundles(args):
    os.makedirs(args.out, exist_ok=True)
    if args.sample:
        with open(args.sample, encoding="utf-8") as f:
            sample = json.load(f)
        jobs = [(e["repo"], p["number"], p.get("query")) for e in sample["chosen"] for p in e["prs"]]
    else:
        jobs = [(args.repo, int(n), None) for n in args.prs.split(",")]
    failed = 0
    for repo, number, qualified_by in jobs:
        path = os.path.join(args.out, f"{repo.replace('/', '__')}__{number}.json")
        if os.path.exists(path):
            continue
        try:
            bundle = fetch_bundle(repo, number, qualified_by)
        # an API error, or a response not shaped as expected: reported and left for a rerun, which skips the bundles
        # already written; anything else (no gh at all) stops the run
        except (GhError, ValueError, KeyError, TypeError) as e:
            failed += 1
            print(f"not fetched: {repo}#{number}: {type(e).__name__}: {str(e)[:300]}", file=sys.stderr)
            continue
        _write_json(path, bundle, indent=2)
        print(f"bundle: {path}")
    if failed:
        print(f"{failed} bundle(s) not fetched; run the same command again to fetch only those", file=sys.stderr)
    return 1 if failed else 0


def cmd_all(args):
    os.makedirs(args.out, exist_ok=True)
    sample = os.path.join(args.out, "sample.json")
    code = cmd_sample(argparse.Namespace(agents=args.agents, since=args.since, until=args.until,
                                         exclude_owner=args.exclude_owner, out=sample,
                                         results=os.path.join(args.out, "search-results.jsonl")))
    if code:
        return code
    cmd_configs(argparse.Namespace(sample=sample, out=os.path.join(args.out, "configs")))
    return cmd_bundles(argparse.Namespace(sample=sample, out=os.path.join(args.out, "bundles"), repo=None, prs=None))


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("sample", "all"):
        s = sub.add_parser(name)
        s.add_argument("--since", help="first day of the window (default: the 90 complete UTC days before today)")
        s.add_argument("--until", help="last day of the window (default: yesterday, UTC)")
        s.add_argument("--exclude-owner", action="append", default=[])
        s.add_argument("--agents")
        s.add_argument("--out", required=True)
        if name == "sample":
            s.add_argument("--results", help="where to write every result read (default: next to --out)")
    c = sub.add_parser("configs")
    c.add_argument("--sample", required=True)
    c.add_argument("--out", required=True)
    b = sub.add_parser("bundles")
    b.add_argument("--sample")
    b.add_argument("--repo")
    b.add_argument("--prs")
    b.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "bundles" and not (args.sample or (args.repo and args.prs)):
        ap.error("bundles needs --sample, or --repo and --prs")
    return {"sample": cmd_sample, "configs": cmd_configs, "bundles": cmd_bundles, "all": cmd_all}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
