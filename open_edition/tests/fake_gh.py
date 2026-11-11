"""A stand-in for GitHub's `gh` command line, for the tests: it answers from a JSON state file what gh would answer from
GitHub, as gh does when its output is not a terminal, and never reaches a network. Its `--version` names 2.101.0, the
gh the workspace image installs; what it does is gh's as its author read gh 2.89.0 and its source.

What it answers. `--version` and `version`. `auth status` (on stdout; --active, --hostname, --show-token); `auth
token` (--hostname, --user, --secure-storage); and `auth git-credential get|store|erase`, git's credential helper,
which for get over https to github.com (or gist.github.com) gives the signed-in user and the token, as gh does for a
sign-in it keeps, and for any other request, or signed out, nothing, exiting 1; store and erase do nothing. `api user`
(`{"login": ...}`, or with --jq .login the login alone). `repo view [<repository>]` (without --json, its name and an
empty description; --json defaultBranchRef, name, nameWithOwner, url and viewerPermission). `pr list` (--repo, --head,
--base, --state open, closed (merged ones too, as gh's own query takes them), merged or all, --limit, and --json
baseRefName, body, headRefName, isDraft, number, state, title and url; without --json, a line each of its number,
title (its runs of spaces made one, as gh's), head and state (DRAFT for an open draft), tab-separated, where gh also
gives when it was made), the highest number first. `pr create` (--repo, --head, --base, --title, --body or --body-file
(- for stdin), --fill and --draft), which adds an open pull request to the state file and prints its address: without
--base it goes into main, every repository's default branch here; with --fill a title not given is the head's name and
a body not given is empty. Each takes the short spellings of those flags, `pr ls` and `pr new` are gh's other names for
list and create, and --jq evaluates `.`, a path of names (`.a.b`), either after `.[]`, and `length`, printing a string
plain, null as an empty line and a list or object as compact JSON, as gh does. A --json answer is compact, its keys
sorted.

What it refuses, each exiting 1 as gh does: a flag gh does not have, in the words of gh's flag parser (gh then prints
the command's usage, which this leaves out); a command gh does not have, as gh says it; a --json field gh does not
have, with gh's list of them as this knows it; and the arguments gh refuses, in its words. A flag, --json field,
command, api endpoint or --jq expression gh has but this does not answer, a request for help, and a command's flags put
before its words are refused with a message starting "fake gh:".

The repository. pr list's and pr create's --repo, else GH_REPO, as gh reads them ([HOST/]OWNER/REPO, or an address);
repo view's argument likewise (a NAME alone is the signed-in user's), where GH_REPO is not read, gh reading it only for
the commands that have --repo; else that of the clone it runs in: the first of its remotes on github.com as `git
remote -v` gives them, in gh's order (upstream, github, origin, then the rest; a remote's gh-resolved is not read), and
pr create's head is that clone's current branch, which is not checked to have been pushed. It answers for github.com
alone. Its checks come in gh's order: the flags and the words; the sign-in; --json's fields; the command's own
(--limit; pr create's body file, then its title and body); the repository's name; GitHub; the repository; then pr
create's head, an open pull request from it into the same base, a blank title, and the permission.

The state is the file $FAKE_GH_STATE names, else $HOME/fake-gh.json (the workspace passes its programs no
FAKE_GH_STATE); a missing file, and any key the file leaves out, is DEFAULT_STATE's: {"user": "octocat", "logged_in":
true, "token": a gho_ token, "repos": {}, "unavailable": false}. GH_TOKEN and GITHUB_TOKEN, which gh would take for a
sign-in, are not read. "repos" holds a repository by owner/name, found whatever the case it is asked in, as GitHub
finds it, as {"permission": "ADMIN", "MAINTAIN", "WRITE", "TRIAGE", "READ" or null, "prs": [{"number", "head", "base"
(main when left out), "title" ("" when left out), "body" ("" when left out), "draft" (false when left out), "state":
"OPEN", "CLOSED" or "MERGED" (OPEN when left out)}]}; one it does not hold, or one whose permission is null, is not
found. Signed out ("logged_in" false, or no token): auth status and auth token exit 1 saying so, git-credential gives
nothing, and api, repo view, pr list and pr create exit 4 in gh's words (which depend on CI and GITHUB_ACTIONS, as
gh's do). With "unavailable": true those four exit 1, unable to connect, and auth status says on stderr that the token
is invalid, exiting 1, as gh does when it cannot reach GitHub; --version, auth token and git-credential, which gh
answers without GitHub, are unaffected. pr create needs WRITE, MAINTAIN or ADMIN, a title that is not blank, and no
open pull request from the same head into the same base; numbers the new one after the repository's highest; keeps
its body and whether it is a draft too; and writes the state file back whole: a temporary file, with the file's mode,
renamed over it, the state's folder locked while it reads and writes.

Each call is written down for the tests to read, once it has answered, as one JSON line in $HOME/fake-gh-calls.jsonl:
{"args", "home" (its HOME), "user" (the state's, null when signed out), "cwd", "at" (seconds), "exit" (its exit
status)}, and for auth git-credential get, store or erase "stdin", the request lines git gave it (read whatever the
operation, which gh does not do), with a password, oauth_refresh_token or credential, a url holding a password, and
any line holding the token written as <redacted>.

Written into a test's bin directory as `gh`, after a shebang line naming the tests' python, and run there."""

import contextlib
import fcntl
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

VERSION = "2.101.0"
HOST = "github.com"
DEFAULT_BRANCH = "main"
DEFAULT_STATE = {"user": "octocat", "logged_in": True, "token": "gho_fake" + "0" * 32, "repos": {},
                 "unavailable": False}
WRITERS = ("ADMIN", "MAINTAIN", "WRITE")
PR_STATES = ("OPEN", "CLOSED", "MERGED")
# what each --state lists: closed takes merged ones too, as gh's own query does
LISTED = {"open": ("OPEN",), "closed": ("CLOSED", "MERGED"), "merged": ("MERGED",), "all": PR_STATES}
# the scopes GitHub reports for a token `gh auth login` asked for
SCOPES = "'gist', 'read:org', 'repo', 'workflow'"
# the user gh gives git for a token whose user it was not told
TOKEN_USER = "x-access-token"
# the keys of git's credential lines that carry a secret
SECRET_KEYS = ("password", "credential", "oauth_refresh_token")
HOME = os.environ.get("HOME") or os.path.expanduser("~")

# the --json fields gh has, and those of them this stand-in answers
PR_FIELDS = ("additions", "assignees", "author", "autoMergeRequest", "baseRefName", "baseRefOid", "body",
             "changedFiles", "closed", "closedAt", "closingIssuesReferences", "comments", "commits", "createdAt",
             "deletions", "files", "fullDatabaseId", "headRefName", "headRefOid", "headRepository",
             "headRepositoryOwner", "id", "isCrossRepository", "isDraft", "labels", "latestReviews",
             "maintainerCanModify", "mergeCommit", "mergeStateStatus", "mergeable", "mergedAt", "mergedBy", "milestone",
             "number", "potentialMergeCommit", "projectCards", "projectItems", "reactionGroups", "reviewDecision",
             "reviewRequests", "reviews", "state", "statusCheckRollup", "title", "updatedAt", "url")
PR_ANSWERED = ("baseRefName", "body", "headRefName", "isDraft", "number", "state", "title", "url")
REPO_FIELDS = ("archivedAt", "assignableUsers", "codeOfConduct", "contactLinks", "createdAt", "defaultBranchRef",
               "deleteBranchOnMerge", "description", "diskUsage", "forkCount", "fundingLinks", "hasDiscussionsEnabled",
               "hasIssuesEnabled", "hasProjectsEnabled", "hasWikiEnabled", "homepageUrl", "id", "isArchived",
               "isBlankIssuesEnabled", "isEmpty", "isFork", "isInOrganization", "isMirror", "isPrivate",
               "isSecurityPolicyEnabled", "isTemplate", "isUserConfigurationRepository", "issueTemplates", "issues",
               "labels", "languages", "latestRelease", "licenseInfo", "mentionableUsers", "mergeCommitAllowed",
               "milestones", "mirrorUrl", "name", "nameWithOwner", "openGraphImageUrl", "owner", "parent",
               "primaryLanguage", "projects", "projectsV2", "pullRequestTemplates", "pullRequests", "pushedAt",
               "rebaseMergeAllowed", "repositoryTopics", "securityPolicyUrl", "squashMergeAllowed", "sshUrl",
               "stargazerCount", "templateRepository", "updatedAt", "url", "usesCustomOpenGraphImage",
               "viewerCanAdminister", "viewerDefaultCommitEmail", "viewerDefaultMergeMethod", "viewerHasStarred",
               "viewerPermission", "viewerPossibleCommitEmails", "viewerSubscription", "visibility", "watchers")
REPO_ANSWERED = ("defaultBranchRef", "name", "nameWithOwner", "url", "viewerPermission")

# gh's commands, and the subcommands of those this stand-in answers some of; gh's other names for some of them
COMMANDS = ("accessibility", "actions", "agent-task", "alias", "api", "attestation", "auth", "browse", "cache",
            "codespace", "completion", "config", "copilot", "cs", "ext", "extension", "extensions", "gist", "gpg-key",
            "help", "issue", "label", "licenses", "org", "pr", "preview", "project", "release", "repo", "ruleset",
            "run", "search", "secret", "ssh-key", "status", "variable", "version", "workflow")
SUBCOMMANDS = {"auth": ("git-credential", "login", "logout", "refresh", "setup-git", "status", "switch", "token"),
               "pr": ("checkout", "checks", "close", "comment", "create", "diff", "edit", "list", "lock", "merge",
                      "ready", "reopen", "revert", "review", "status", "unlock", "update-branch", "view"),
               "repo": ("archive", "autolink", "clone", "create", "credits", "delete", "deploy-key", "edit", "fork",
                        "garden", "gitignore", "license", "list", "rename", "set-default", "sync", "unarchive",
                        "view")}
ALIASES = {("pr", "ls"): "list", ("pr", "new"): "create", ("pr", "co"): "checkout", ("repo", "ls"): "list",
           ("repo", "new"): "create"}

# each command's flags as gh's flag parser (pflag) takes them: those this stand-in answers, (long name, shorthand or
# "", kind), kind "string", "bool", "int", "fields" (--json's: comma-separated, adding up when it is given again) or
# the tuple of values it takes; and those gh has that this stand-in does not answer, (long name, shorthand or "")
JSON_FLAGS = (("json", "", "fields"), ("jq", "q", "string"))
FLAGS = {"auth status": (("active", "a", "bool"), ("hostname", "h", "string"), ("show-token", "t", "bool")),
         "auth token": (("hostname", "h", "string"), ("secure-storage", "", "bool"), ("user", "u", "string")),
         "auth git-credential": (),
         "api": (("jq", "q", "string"),),
         "repo view": JSON_FLAGS,
         "pr list": (("repo", "R", "string"), ("base", "B", "string"), ("head", "H", "string"), ("limit", "L", "int"),
                     ("state", "s", ("open", "closed", "merged", "all"))) + JSON_FLAGS,
         "pr create": (("repo", "R", "string"), ("base", "B", "string"), ("body", "b", "string"),
                       ("body-file", "F", "string"), ("draft", "d", "bool"), ("fill", "f", "bool"),
                       ("head", "H", "string"), ("title", "t", "string"))}
UNANSWERED = {"api": (("cache", ""), ("field", "F"), ("header", "H"), ("hostname", ""), ("include", "i"),
                      ("input", ""), ("method", "X"), ("paginate", ""), ("preview", "p"), ("raw-field", "f"),
                      ("silent", ""), ("slurp", ""), ("template", "t"), ("verbose", "")),
              "repo view": (("branch", "b"), ("template", "t"), ("web", "w")),
              "pr list": (("app", ""), ("assignee", "a"), ("author", "A"), ("draft", "d"), ("label", "l"),
                          ("search", "S"), ("template", "t"), ("web", "w")),
              "pr create": (("assignee", "a"), ("dry-run", ""), ("editor", "e"), ("fill-first", ""),
                            ("fill-verbose", ""), ("label", "l"), ("milestone", "m"), ("no-maintainer-edit", ""),
                            ("project", "p"), ("recover", ""), ("reviewer", "r"), ("template", "T"), ("web", "w"))}
TRUE = ("1", "t", "T", "TRUE", "true", "True")
FALSE = ("0", "f", "F", "FALSE", "false", "False")
URL_SCHEMES = ("ssh:", "git+ssh:", "git:", "http:", "git+https:", "https:")
# a line of `git remote -v`, read as gh reads it
REMOTE = re.compile(r"(.+)\s+(\S+)\s+\((push|fetch)\)")
# gh's order of a clone's remotes: these first, the rest after
REMOTE_ORDER = {"upstream": 3, "github": 2, "origin": 1}
# the --jq expressions this stand-in evaluates, besides `.` and `length`: a path of names, either after `.[]`
JQ = re.compile(r"(\.\[\])?((?:\.[A-Za-z_][A-Za-z0-9_]*)*)")


class Fail(Exception):
    """gh stopping: what it says on stderr (nothing for None), and its exit status."""

    def __init__(self, said, code=1):
        super().__init__(said)
        self.said, self.code = said, code


def fake(said):
    """A refusal of something gh does but this stand-in does not."""
    return Fail(f"fake gh: {said}")


def no_help():
    return fake("this stand-in prints no help")


def _bytes(text):
    try:
        return text.encode("utf-8", "surrogateescape")
    except UnicodeEncodeError:
        return text.encode("utf-8", "replace")


def out(text):
    sys.stdout.buffer.write(_bytes(text))
    sys.stdout.buffer.flush()


def err(text):
    sys.stderr.buffer.write(_bytes(text))
    sys.stderr.buffer.flush()


def go_quote(text):
    """text as Go's %q writes it."""
    escapes = {"\a": "\\a", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t", "\v": "\\v",
               '"': '\\"', "\\": "\\\\"}
    quoted = []
    for ch in text:
        if ch in escapes:
            quoted.append(escapes[ch])
        elif ch.isprintable():
            quoted.append(ch)
        elif ord(ch) < 0x80:
            quoted.append("\\x%02x" % ord(ch))
        elif ord(ch) < 0x10000:
            quoted.append("\\" + "u%04x" % ord(ch))
        else:
            quoted.append("\\" + "U%08x" % ord(ch))
    return '"' + "".join(quoted) + '"'


# ---- the state -------------------------------------------------------------------------------------------------------

def state_path():
    return os.environ.get("FAKE_GH_STATE") or os.path.join(HOME, "fake-gh.json")


def load():
    """(the state file as it is written, the state it means: its keys over DEFAULT_STATE's)"""
    path = state_path()
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        raw = {}
    except (OSError, ValueError) as e:
        raise fake(f"cannot read the state file {path}: {e}")
    if not isinstance(raw, dict):
        raise fake(f"the state file {path} is not a JSON object")
    state = {**DEFAULT_STATE, "repos": {}, **raw}
    if not isinstance(state["repos"], dict):
        raise fake(f'the state file {path} has "repos" that is not an object')
    return raw, state


def save(raw):
    """The state file written back whole: a temporary file beside it, with the file's mode, renamed over it."""
    path = os.path.abspath(state_path())
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except FileNotFoundError:
        umask = os.umask(0)
        os.umask(umask)
        mode = 0o666 & ~umask
    fd, temporary = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".fake-gh-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(raw, fh, indent=2)
            fh.write("\n")
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


@contextlib.contextmanager
def locked():
    """The state's folder locked, so that two pull requests made at once are both kept."""
    folder = os.open(os.path.dirname(os.path.abspath(state_path())), os.O_RDONLY)
    try:
        fcntl.flock(folder, fcntl.LOCK_EX)
        yield
    finally:
        os.close(folder)


def signed_in(state):
    return bool(state["logged_in"]) and isinstance(state["token"], str) and bool(state["token"])


def require_sign_in(state):
    """gh's check before a command that needs GitHub: signed out, it stops there, exiting 4, in these words."""
    if signed_in(state):
        return
    if os.environ.get("GITHUB_ACTIONS") == "true":
        said = ("gh: To use GitHub CLI in a GitHub Actions workflow, set the GH_TOKEN environment variable. Example:\n"
                "  env:\n    GH_TOKEN: ${{ github.token }}")
    elif os.environ.get("CI"):
        said = "gh: To use GitHub CLI in automation, set the GH_TOKEN environment variable."
    else:
        said = ("To get started with GitHub CLI, please run:  gh auth login\n"
                "Alternatively, populate the GH_TOKEN environment variable with a GitHub API authentication token.")
    raise Fail(said, 4)


def connect(state):
    """gh reaching GitHub: out of reach, it stops there, as gh does when it cannot look api.github.com up."""
    if state["unavailable"]:
        raise Fail("error connecting to api.github.com\ncheck your internet connection or https://githubstatus.com")


# ---- the words -------------------------------------------------------------------------------------------------------

def parse(args, command, flags=(), unanswered=(), fields=()):
    """A command's words read as gh's flag parser reads them: (its options by long name, the words that are not
    options). `flags` are the flags this stand-in answers, `unanswered` those gh has that it does not; `fields`,
    --json's, for what gh says when --json comes without them. --help, gh's on every command, and -h where the command
    has no -h of its own ask for help."""
    flags = tuple(flags) + (("help", "", "bool"),)
    by_long = {f[0]: f for f in flags}
    by_short = {f[1]: f for f in flags if f[1]}
    gone_long = {f[0] for f in unanswered}
    gone_short = {f[1]: f[0] for f in unanswered if f[1]}
    opts, words, rest = {}, [], list(args)
    while rest:
        word = rest.pop(0)
        if word == "--":
            words += rest
            break
        if len(word) < 2 or word[0] != "-":
            words.append(word)
        elif word[1] == "-":
            if word[2] in "-=":
                raise Fail(f"bad flag syntax: {word}")
            name, eq, value = word[2:].partition("=")
            if name in gone_long:
                raise fake(f"this stand-in does not answer {command} --{name}")
            if name not in by_long:
                raise Fail(f"unknown flag: --{name}")
            flag = by_long[name]
            if not eq:
                if flag[2] == "bool":
                    value = "true"
                elif rest:
                    value = rest.pop(0)
                elif name == "json":
                    raise Fail("Specify one or more comma-separated fields for `--json`:\n  "
                               + "\n  ".join(sorted(fields)))
                else:
                    raise Fail(f"flag needs an argument: --{name}")
            take(opts, flag, value)
        else:
            shorthands = word[1:]
            while shorthands:
                c = shorthands[0]
                if c not in by_short:
                    if c in gone_short:
                        raise fake(f"this stand-in does not answer {command} -{c} (--{gone_short[c]})")
                    if c == "h":
                        raise no_help()
                    raise Fail(f"unknown shorthand flag: '{c}' in -{shorthands}")
                flag = by_short[c]
                if len(shorthands) > 2 and shorthands[1] == "=":
                    value, shorthands = shorthands[2:], ""
                elif flag[2] == "bool":
                    value, shorthands = "true", shorthands[1:]
                elif len(shorthands) > 1:
                    value, shorthands = shorthands[1:], ""
                elif rest:
                    value, shorthands = rest.pop(0), ""
                else:
                    raise Fail(f"flag needs an argument: '{c}' in -{shorthands}")
                take(opts, flag, value)
    if opts.pop("help", False):
        raise no_help()
    return opts, words


def take(opts, flag, value):
    """A flag's value set as pflag sets it; one the flag does not take refused in pflag's words."""
    name, short, kind = flag
    shown = f"-{short}, --{name}" if short else f"--{name}"

    def invalid(why):
        return Fail(f"invalid argument {go_quote(value)} for {go_quote(shown)} flag: {why}")

    if kind == "bool":
        if value not in TRUE + FALSE:
            raise invalid(f"strconv.ParseBool: parsing {go_quote(value)}: invalid syntax")
        opts[name] = value in TRUE
    elif kind == "int":
        number = go_int(value)
        if isinstance(number, str):
            raise invalid(f"strconv.ParseInt: parsing {go_quote(value)}: {number}")
        opts[name] = number
    elif kind == "fields":
        opts[name] = opts.get(name, []) + (value.split(",") if value else [])
    elif isinstance(kind, tuple):
        if value not in kind:
            raise invalid("valid values are {" + "|".join(kind) + "}")
        opts[name] = value
    else:
        opts[name] = value


def go_int(text):
    """text read as Go's strconv.ParseInt(text, 0, 64) reads it, as pflag's int flags do: the number, or why not."""
    sign, body = (text[0], text[1:]) if text[:1] in ("+", "-") else ("", text)
    try:
        if re.fullmatch(r"0_?[0-7](_?[0-7])*", body):         # a leading 0 alone: octal, as Go reads it
            number = int(body.replace("_", ""), 8)
        elif re.fullmatch(r"[0-9][0-9A-Za-z_]*", body):
            number = int(body, 0)
        else:
            raise ValueError(body)
    except ValueError:
        return "invalid syntax"
    number = -number if sign == "-" else number
    return number if -2 ** 63 <= number < 2 ** 63 else "value out of range"


def exact_args(words, n):
    if len(words) != n:
        raise Fail(f"accepts {n} arg(s), received {len(words)}")


def at_most_args(words, n):
    if len(words) > n:
        raise Fail(f"accepts at most {n} arg(s), received {len(words)}")


def no_args(words, opts, flags):
    """No words but flags, refused as gh refuses them, with its reminder to quote when a flag with a value was given."""
    if not words:
        return
    said = (f"unknown argument {go_quote(words[0])}" if len(words) == 1
            else "unknown arguments [" + " ".join(map(go_quote, words)) + "]")
    kinds = {f[0]: f[2] for f in flags}
    if any(kinds.get(name) != "bool" for name in opts):
        said += "; please quote all values that have spaces"
    raise Fail(said)


# ---- the repository --------------------------------------------------------------------------------------------------

def git(*args):
    """(git's exit status, its stdout, its stderr) in the folder gh runs in; the status None when git cannot run."""
    try:
        p = subprocess.run(["git", *args], capture_output=True, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL)
    except OSError:
        return None, "", 'exec: "git": executable file not found in $PATH'
    return p.returncode, p.stdout, p.stderr


def normal_host(host):
    return host.removeprefix("www.").lower()


def from_url(text):
    """(host, owner, name) of a repository's address as gh reads one, an scp-like user@host:path too; else why not."""
    if not text.startswith(URL_SCHEMES + ("ftp:", "ftps:", "file:")) and ":" in text and "\\" not in text:
        text = "ssh://" + text.replace(":", "/", 1)
    try:
        parts = urlsplit(text)
        host = parts.hostname or ""
    except ValueError as e:
        return f"parse {go_quote(text)}: {e}"
    if not host:
        return "no hostname detected"
    path = parts.path.strip("/").split("/", 2)
    if len(path) != 2:
        return f"invalid path: {parts.path}"
    return normal_host(host), path[0], path[1].removesuffix(".git")


def full_name(text):
    """(host, owner, name) of a repository as gh's --repo takes it: [HOST/]OWNER/REPO, or an address."""
    if text.startswith(("git@",) + URL_SCHEMES):
        got = from_url(text)
        if isinstance(got, str):
            raise Fail(got)
        return got
    parts = text.split("/", 3)
    if len(parts) not in (2, 3) or not all(parts):
        raise Fail(f'expected the "[HOST/]OWNER/REPO" format, got {go_quote(text)}')
    return (normal_host(parts[0]), parts[1], parts[2]) if len(parts) == 3 else (HOST, parts[0], parts[1])


def github_only(host):
    if host != HOST:
        raise fake(f"this stand-in answers for {HOST} alone, not {host}")


def clone_repository():
    """(host, owner, name) of the clone gh runs in, as gh finds it: of its remotes as `git remote -v` gives them, those
    on github.com, in gh's order, the first."""
    code, said, complaint = git("remote", "-v")
    if code != 0:
        raise Fail(f"failed to run git: {complaint.strip()}")
    remotes = {}
    for line in said.splitlines():
        m = REMOTE.search(line)
        if m:
            remotes.setdefault(m[1].strip(), {})[m[3]] = m[2].strip()
    if not remotes:
        raise Fail("no git remotes found")
    known = []
    for name, addresses in remotes.items():
        # gh reads a remote's fetch address, and its push address only when the fetch address is not one it reads
        got = [from_url(addresses[kind]) for kind in ("fetch", "push") if kind in addresses]
        got = next((g for g in got if isinstance(g, tuple)), None)
        if got is not None and got[0] == HOST:
            known.append((name, got))
    if not known:
        raise Fail("none of the git remotes configured for this repository point to a known GitHub host. To tell gh "
                   "about a new GitHub host, please use `gh auth login`")
    known.sort(key=lambda r: -REMOTE_ORDER.get(r[0].lower(), 0))
    return known[0][1]


def repo_option(opts):
    """(host, owner, name) of the repository a command with --repo works on, as gh finds it."""
    given = opts.get("repo") or os.environ.get("GH_REPO")
    return full_name(given) if given else clone_repository()


def current_branch():
    """The branch the clone gh runs in has checked out, as pr create reads it."""
    code, said, complaint = git("symbolic-ref", "--quiet", "HEAD")
    if code != 0 or not said.strip():
        raise Fail("could not determine the current branch: could not determine current branch: failed to run git: "
                   + (complaint.strip() or "not on any branch"))
    return said.splitlines()[0].strip().removeprefix("refs/heads/")


def lookup(state, owner, name):
    """(the repository's owner/name as the state holds it, its entry): one the signed-in user may see, found whatever
    the case it is asked in, as GitHub finds it; else what GitHub says of one that is not there."""
    wanted = f"{owner}/{name}"
    repos = state["repos"]
    key = wanted if wanted in repos else next(
        (k for k in repos if isinstance(k, str) and k.lower() == wanted.lower()), wanted)
    entry = repos.get(key)
    if not isinstance(entry, dict) or not entry.get("permission"):
        raise Fail(f"GraphQL: Could not resolve to a Repository with the name '{wanted}'. (repository)")
    return key, entry


def prs_of(key, entry):
    """The repository's pull requests as the state holds them, each with what the state leaves out filled in."""
    prs = entry.get("prs") or []
    if not isinstance(prs, list):
        raise fake(f'the state file has "prs" of {key} that is not a list')
    got = []
    for p in prs:
        if not (isinstance(p, dict) and type(p.get("number")) is int and isinstance(p.get("head"), str)):
            raise fake(f"a pull request of {key} in the state file needs a whole number and a head: {json.dumps(p)}")
        state = p.get("state", "OPEN")
        if state not in PR_STATES:
            raise fake(f"pull request {p['number']} of {key} in the state file has the state {json.dumps(state)}, "
                       "not OPEN, CLOSED or MERGED")
        got.append({"base": DEFAULT_BRANCH, "title": "", "body": "", "draft": False, **p, "state": state})
    return got


# ---- --json and --jq -------------------------------------------------------------------------------------------------

def json_text(value):
    """value as gh writes JSON: compact, its keys sorted, the two characters Go escapes that Python does not escaped."""
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return text.replace(chr(0x2028), "\\u2028").replace(chr(0x2029), "\\u2029")


def json_fields(opts, known):
    """The fields --json asks for, None without --json; one gh does not have refused, and --jq without --json."""
    if "json" not in opts:
        if "jq" in opts:
            raise Fail("cannot use `--jq` without specifying `--json`")
        return None
    listing = "\n  ".join(sorted(known))
    for field in opts["json"]:
        if field not in known:
            raise Fail(f"Unknown JSON field: {go_quote(field)}\nAvailable fields:\n  {listing}")
    if not opts["json"]:
        raise Fail(f"Specify one or more comma-separated fields for `--json`:\n  {listing}")
    return opts["json"]


def answered(fields, allowed, command):
    """The fields asked for, when this stand-in answers each; else a refusal naming the one it does not."""
    for field in fields:
        if field not in allowed:
            raise fake(f"this stand-in does not answer {command} --json {field} (it answers {', '.join(allowed)})")
    return fields


def export(value, jq):
    """A --json answer: compact, its keys sorted; or, with --jq, each value the expression gives, as gh prints it."""
    if not jq:
        return out(json_text(value) + "\n")
    out("".join(jq_text(v) + "\n" for v in evaluate(value, jq)))


def evaluate(value, expr):
    """The values a --jq expression gives, for those this stand-in evaluates: `.`, a path of names (`.a.b`, null past
    a null), either after `.[]` (each element of a list, or each value of an object), and `length`."""
    text = expr.strip()
    if text == ".":
        return [value]
    if text == "length":
        return [length(value)]
    m = JQ.fullmatch(text)
    if m is None or not text:
        raise fake(f"this stand-in does not evaluate --jq {expr} (it evaluates ., a path such as .a.b, either after "
                   ".[], and length)")
    items = each(value) if m[1] else [value]
    names = m[2].split(".")[1:]
    return [walk(item, names) for item in items]


def typed(value):
    """A value as jq's errors name it: its type and a short look at it."""
    if value is None:
        return "null"
    kind = ("boolean" if isinstance(value, bool) else "number" if isinstance(value, (int, float)) else
            "string" if isinstance(value, str) else "array" if isinstance(value, list) else "object")
    shown = json_text(value)
    return f"{kind} ({shown if len(shown) <= 30 else shown[:27] + '...'})"


def each(value):
    if isinstance(value, list):
        return list(value)
    if isinstance(value, dict):
        return [value[k] for k in sorted(value)]
    raise Fail(f"cannot iterate over: {typed(value)}")


def walk(value, names):
    for name in names:
        if value is None:
            continue
        if not isinstance(value, dict):
            raise Fail(f"expected an object but got: {typed(value)}")
        value = value.get(name)
    return value


def length(value):
    if value is None:
        return 0
    if isinstance(value, bool):
        raise Fail(f"length cannot be applied to: {typed(value)}")
    if isinstance(value, (int, float)):
        return abs(value)
    return len(value)


def jq_text(value):
    """A --jq value as gh prints it: a string plain, null as nothing, a number without a fraction it does not have
    (else with two places), true or false, and a list or object as compact JSON."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else f"{value:.2f}"
    if isinstance(value, str):
        return value
    return json_text(value)


# ---- the commands ----------------------------------------------------------------------------------------------------

def version():
    out(f"gh version {VERSION} (fake)\nhttps://github.com/cli/cli/releases/tag/v{VERSION}\n")


def masked(token):
    """A token as auth status shows it: what follows its last _ hidden."""
    cut = token.rfind("_") + 1
    return token[:cut] + "*" * (len(token) - cut)


def auth_status(args, state, call):
    opts, words = parse(args, "gh auth status", FLAGS["auth status"])
    exact_args(words, 0)
    if not signed_in(state):
        raise Fail("You are not logged into any GitHub hosts. To log in, run: gh auth login")
    host = opts.get("hostname") or HOST
    if host != HOST:
        raise Fail(f"You are not logged into any accounts on {host}")
    user, token = state["user"], state["token"]
    if state["unavailable"]:
        # gh reads the token's scopes from GitHub; out of reach, it takes the token for an invalid one
        raise Fail(f"{HOST}\n  X Failed to log in to {HOST} account {user} (keyring)\n  - Active account: true\n"
                   "  - The token in keyring is invalid.\n"
                   f"  - To re-authenticate, run: gh auth login -h {HOST}\n"
                   f"  - To forget about this account, run: gh auth logout -h {HOST} -u {user}")
    shown = token if opts.get("show-token") else masked(token)
    said = (f"{HOST}\n  {chr(0x2713)} Logged in to {HOST} account {user} (keyring)\n  - Active account: true\n"
            f"  - Git operations protocol: https\n  - Token: {shown}\n")
    if token.startswith(("ghp_", "gho_")):
        said += f"  - Token scopes: {SCOPES}\n"
    out(said)


def auth_token(args, state, call):
    opts, words = parse(args, "gh auth token", FLAGS["auth token"])
    exact_args(words, 0)
    host = opts.get("hostname") or HOST
    held = signed_in(state) and host == HOST
    user = opts.get("user")
    if user and not (held and user == state["user"]):
        raise Fail(f"no token found for '{user}'")
    if not held:
        raise Fail(f"no oauth token found for {host}")
    out(state["token"] + "\n")


def git_request():
    """git's credential request on stdin: its lines, up to a blank one or the end."""
    if sys.stdin is None:
        return []
    lines = []
    for raw in iter(sys.stdin.buffer.readline, b""):
        line = raw.removesuffix(b"\n").removesuffix(b"\r").decode("utf-8", "replace")
        if not line:
            break
        lines.append(line)
    return lines


def redacted(line, token):
    """A credential line as the record keeps it: a secret, or the token anywhere in it, written as <redacted>."""
    key, _, value = line.partition("=")
    if token and token in key:
        return "<redacted>"
    if key in SECRET_KEYS or (token and token in value):
        return f"{key}=<redacted>"
    if key == "url":
        try:
            hidden = urlsplit(value).password is not None
        except ValueError:
            hidden = True
        if hidden:
            return f"{key}=<redacted>"
    return line


def token_for(state, host):
    """The token gh keeps for a host: the state's for github.com while signed in, else none."""
    return state["token"] if signed_in(state) and host.lower() == HOST else ""


def git_credential(args, state, call):
    _, words = parse(args, "gh auth git-credential", FLAGS["auth git-credential"])
    exact_args(words, 1)
    operation = words[0]
    if operation not in ("get", "store", "erase"):
        raise Fail(f"gh auth git-credential: {go_quote(operation)} operation not supported")
    lines = git_request()
    call["stdin"] = [redacted(line, state["token"] if isinstance(state["token"], str) else "") for line in lines]
    if operation != "get":
        return
    wants = {}
    for line in lines:
        key, eq, value = line.partition("=")
        if not eq:
            continue
        if key == "url":
            try:
                parts = urlsplit(value)
                parts.port          # a port that is not a number refused, as Go's url.Parse refuses it
            except ValueError as e:
                raise Fail(f"parse {go_quote(value)}: {e}")
            wants.update(protocol=parts.scheme, host=parts.netloc.rpartition("@")[2], path=parts.path,
                         username=parts.username or "", password=parts.password or "")
        else:
            wants[key] = value
    if wants.get("protocol") != "https":
        raise Fail(None)
    host = wants.get("host", "")
    token = token_for(state, host) or (token_for(state, host[len("gist."):]) if host.startswith("gist.") else "")
    if not token:
        raise Fail(None)
    user = str(state["user"] or TOKEN_USER)
    asked = wants.get("username", "")
    if asked and user != TOKEN_USER and asked.casefold() != user.casefold():
        raise Fail(None)
    out(f"protocol=https\nhost={host}\nusername={user}\npassword={token}\n")


def api(args, state, call):
    opts, words = parse(args, "gh api", FLAGS["api"], UNANSWERED["api"])
    exact_args(words, 1)
    require_sign_in(state)
    connect(state)
    endpoint = words[0]
    if endpoint not in ("user", "/user"):
        raise fake(f"this stand-in answers gh api user alone, not gh api {endpoint}")
    jq = opts.get("jq")
    if not jq:
        return out(json_text({"login": state["user"]}) + "\n")
    if jq.strip() != ".login":
        raise fake(f"this stand-in evaluates --jq .login alone for gh api user, not {jq}")
    out(jq_text(state["user"]) + "\n")


def repo_view(args, state, call):
    opts, words = parse(args, "gh repo view", FLAGS["repo view"], UNANSWERED["repo view"], REPO_FIELDS)
    at_most_args(words, 1)
    require_sign_in(state)
    fields = json_fields(opts, REPO_FIELDS)
    if words:
        given = words[0]
        if "/" not in given:            # a name alone is the signed-in user's, whom gh asks GitHub for
            connect(state)
            given = f"{state['user']}/{given}"
        try:
            host, owner, name = full_name(given)
        except Fail as e:
            raise Fail(f"argument error: {e.said}")
    else:
        host, owner, name = clone_repository()
    github_only(host)
    connect(state)
    key, entry = lookup(state, owner, name)
    if fields is None:
        return out(f"name:\t{owner}/{name}\ndescription:\t\n")
    answers = {"defaultBranchRef": {"name": DEFAULT_BRANCH}, "name": key.split("/", 1)[1], "nameWithOwner": key,
               "url": f"https://{HOST}/{key}", "viewerPermission": entry["permission"]}
    export({f: answers[f] for f in answered(fields, REPO_ANSWERED, "gh repo view")}, opts.get("jq"))


def pr_list(args, state, call):
    opts, words = parse(args, "gh pr list", FLAGS["pr list"], UNANSWERED["pr list"], PR_FIELDS)
    no_args(words, opts, FLAGS["pr list"])
    require_sign_in(state)
    fields = json_fields(opts, PR_FIELDS)
    limit = opts.get("limit", 30)
    if limit < 1:
        raise Fail(f"invalid value for --limit: {limit}")
    host, owner, name = repo_option(opts)
    github_only(host)
    connect(state)
    key, entry = lookup(state, owner, name)
    states, head, base = LISTED[opts.get("state", "open")], opts.get("head"), opts.get("base")
    prs = sorted((p for p in prs_of(key, entry) if p["state"] in states and (not head or p["head"] == head)
                  and (not base or p["base"] == base)), key=lambda p: p["number"], reverse=True)[:limit]
    if fields is None:
        return out("".join(f"{p['number']}\t{' '.join(str(p['title']).split())}\t{p['head']}\t"
                           f"{'DRAFT' if p['draft'] and p['state'] == 'OPEN' else p['state']}\n" for p in prs))
    fields = answered(fields, PR_ANSWERED, "gh pr list")
    rows = [{"baseRefName": p["base"], "body": p["body"], "headRefName": p["head"], "isDraft": bool(p["draft"]),
             "number": p["number"], "state": p["state"], "title": p["title"],
             "url": f"https://{HOST}/{key}/pull/{p['number']}"} for p in prs]
    export([{f: row[f] for f in fields} for row in rows], opts.get("jq"))


def read_body(name):
    """--body-file's text: stdin's for -, else the file's, refused as Go's os.ReadFile refuses it."""
    try:
        if name == "-":
            return sys.stdin.buffer.read().decode("utf-8", "replace") if sys.stdin is not None else ""
        with open(name, "rb") as fh:
            return fh.read().decode("utf-8", "replace")
    except FileNotFoundError:
        raise Fail(f"open {name}: no such file or directory")
    except IsADirectoryError:
        raise Fail(f"read {name}: is a directory")
    except PermissionError:
        raise Fail(f"open {name}: permission denied")
    except OSError as e:
        raise Fail(f"open {name}: {(e.strerror or str(e)).lower()}")


def pr_create(args, state, call):
    opts, words = parse(args, "gh pr create", FLAGS["pr create"], UNANSWERED["pr create"])
    no_args(words, opts, FLAGS["pr create"])
    require_sign_in(state)
    body, body_given = opts.get("body", ""), "body" in opts
    if opts.get("body-file"):
        body, body_given = read_body(opts["body-file"]), True
    if not opts.get("fill") and ("title" not in opts or not body_given):
        raise Fail("must provide `--title` and `--body` (or `--fill` or `fill-first` or `--fillverbose`) when not "
                   "running interactively")
    host, owner, name = repo_option(opts)
    github_only(host)
    connect(state)
    lookup(state, owner, name)
    label = opts.get("head") or current_branch()
    head = label.split(":", 1)[1] if ":" in label else label        # owner:branch names the branch after the colon
    base = opts.get("base") or DEFAULT_BRANCH
    title = opts["title"] if "title" in opts else head
    with locked():
        raw, now = load()
        key, entry = lookup(now, owner, name)
        prs = prs_of(key, entry)
        for p in sorted(prs, key=lambda p: p["number"], reverse=True):
            if p["head"] == head and p["base"] == base and p["state"] == "OPEN":
                raise Fail(f"a pull request for branch {go_quote(label)} into branch {go_quote(base)} already "
                           f"exists:\nhttps://{HOST}/{key}/pull/{p['number']}")
        if title == "":
            raise Fail("pull request title must not be blank")
        if entry["permission"] not in WRITERS:
            raise Fail(f"pull request create failed: GraphQL: {now['user']} does not have the correct permissions to "
                       "execute `CreatePullRequest` (createPullRequest)")
        number = max((p["number"] for p in prs), default=0) + 1
        entry["prs"] = list(entry.get("prs") or []) + [{"number": number, "head": head, "base": base, "title": title,
                                                        "body": body, "state": "OPEN",
                                                        "draft": opts.get("draft", False)}]
        save(raw)
    out(f"https://{HOST}/{key}/pull/{number}\n")


ANSWERED = {("auth", "status"): auth_status, ("auth", "token"): auth_token, ("auth", "git-credential"): git_credential,
            ("repo", "view"): repo_view, ("pr", "list"): pr_list, ("pr", "create"): pr_create}


def run(args, state, call):
    """What gh does with these words: the command they name, answered; anything else, refused."""
    if not args:
        raise no_help()
    first = args[0]
    if first.startswith("-") and first != "-":
        # gh finds a command named after flags; this stand-in reads the words first, and answers gh's own flags
        # (--version, --help) only when no command follows them
        if any(not a.startswith("-") or a == "-" for a in args):
            raise fake("this stand-in reads a command's words before its flags")
        opts, _ = parse(args, "gh", (("version", "", "bool"),))
        if opts.get("version"):
            return version()
        raise no_help()
    if first == "version":
        return version()
    if first == "help":
        raise no_help()
    if first == "api":
        return api(args[1:], state, call)
    if first in SUBCOMMANDS:
        if len(args) == 1:
            raise no_help()
        sub = args[1]
        if sub.startswith("-") and sub != "-":
            if sub in ("--help", "-h"):
                raise no_help()
            raise fake("this stand-in reads a command's words before its flags")
        sub = ALIASES.get((first, sub), sub)
        command = ANSWERED.get((first, sub))
        if command is not None:
            return command(args[2:], state, call)
        if sub in SUBCOMMANDS[first]:
            raise fake(f"this stand-in does not answer gh {first} {sub}")
        raise Fail(f"unknown command {go_quote(sub)} for \"gh {first}\"")
    if first in COMMANDS:
        raise fake(f"this stand-in does not answer gh {first}")
    raise Fail(f"unknown command {go_quote(first)} for \"gh\"")


def record(call):
    """The call written down: one JSON line appended to $HOME/fake-gh-calls.jsonl."""
    line = (json.dumps(call) + "\n").encode("utf-8")
    fd = os.open(os.path.join(HOME, "fake-gh-calls.jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        while line:
            line = line[os.write(fd, line):]
    finally:
        os.close(fd)


def main(args):
    call = {"args": list(args), "home": os.environ.get("HOME"), "user": None, "cwd": None, "at": int(time.time())}
    with contextlib.suppress(OSError):          # the folder it was started in may be gone
        call["cwd"] = os.getcwd()
    code = 1
    try:
        _, state = load()
        call["user"] = state["user"] if signed_in(state) else None
        run(args, state, call)
        code = 0
    except Fail as e:
        if e.said:
            err(e.said if e.said.endswith("\n") else e.said + "\n")
        code = e.code
    finally:
        call["exit"] = code
        record(call)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
