"""Per-repository configuration, with defaults, and the path matcher the resolvers share."""

import json
import re
from functools import lru_cache

DEFAULTS = {
    # checks that must have concluded success on the head commit, each pinned to the app that produces it
    "expected_checks": [],  # e.g. [{"name": "test", "app": "github-actions"}]
    # commit statuses that count, each pinned to the account that posts it; empty means statuses never count
    "expected_statuses": [],  # e.g. [{"context": "ci/jenkins", "creator": "jenkins-bot"}]
    "schema_globs": [
        "**/migrations/**", "**/alembic/versions/**", "**/*.sql", "**/schema.prisma", "**/schema.rb",
        "**/db/schema.*", "**/structure.sql",
    ],
    "doc_globs": [
        "**/*.md", "**/*.mdx", "**/*.rst", "**/*.adoc", "docs/**", "doc/**", "**/LICENSE*", "**/CHANGELOG*",
    ],
    "test_globs": [
        "**/test_*.py", "**/*_test.py", "**/tests/**", "**/test/**", "**/__tests__/**", "**/*.test.*",
        "**/*.spec.*", "**/*_test.go", "**/spec/**", "**/*Test.java", "**/*Tests.cs",
    ],
    "test_config_globs": [
        "**/pytest.ini", "**/conftest.py", "**/tox.ini", "**/jest.config.*", "**/vitest.config.*",
        "**/karma.conf.*", "**/.mocharc*", "**/phpunit.xml*",
    ],
    "dependency_files": [
        "requirements*.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "Pipfile.lock", "poetry.lock",
        "uv.lock", "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Cargo.toml", "Cargo.lock",
        "go.mod", "go.sum", "Gemfile", "Gemfile.lock", "composer.json", "composer.lock", "pom.xml",
        "build.gradle", "build.gradle.kts", "*.csproj", "packages.lock.json",
    ],
    # files whose change needs a named reviewer's approval on the head commit
    "policy_paths": [".proofpane/policy.yml", ".proofpane/allowlist.yml"],
    "workflow_globs": [".github/workflows/**", ".github/actions/**"],
    "local_actions": [],  # other directories a workflow uses as a local action (`uses: ./path`)
    "reviewers": [],  # GitHub logins named individually; teams are not expanded
    "required_statements": [],  # e.g. [{"id": "schema", "pattern": "schema change: (yes|no)"}]
    # suppressions read from the base ref: secrets by fingerprint, PII by rule and path
    "suppress_secret_fingerprints": [],
    "suppress_pii": [],  # e.g. [{"rule": "email", "path": "tests/fixtures/**"}]
}


def load_config(path=None, overrides=None):
    cfg = json.loads(json.dumps(DEFAULTS))  # deep copy
    if path:
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f))
    if overrides:
        cfg.update(overrides)
    unknown = set(cfg) - set(DEFAULTS) - {"repo", "notes"}
    if unknown:
        raise ValueError(f"unknown configuration keys: {sorted(unknown)}")
    return cfg


@lru_cache(maxsize=None)
def _glob_regex(pattern):
    """`**/` matches any number of directories, `**` anything, `*` anything but '/', `?` one non-'/' character.

    A pattern with no '/' matches the file's base name, the way .gitignore does."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def path_matches(path, patterns):
    for p in patterns:
        target = path if "/" in p else path.rsplit("/", 1)[-1]
        if _glob_regex(p).match(target):
            return True
    return False
