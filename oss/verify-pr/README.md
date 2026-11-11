# verify-pr

Checks, claim by claim, what a pull request says about its own change: "all tests pass", "no schema change", "adds
tests", "fixes #12". A deterministic function decides every outcome; no model output sets one. Claims nothing can
check are reported as unverified, never as passed.

It writes a Verification Manifest v0 through `proofpane-core` (`oss/core`), so `proofpane-verify manifest` re-derives
exactly what it decided.

## Run

```bash
cd oss/verify-pr
python3 -m unittest discover -s tests          # standard library only

python3 -m verify_pr run bundle.json --config repo-config.json
python3 -m verify_pr summarize bundles/ --configs configs/ --out summary.json
```

Bundles are JSON files, one per pull request: title, description, author, base and head commits, how many files
changed and the changed files with their patches and counts of added lines, check runs (with the app that produced
each), commit statuses, reviews (with the commit each was given on), and, for each issue the title or description
references, what GitHub said (`issues`: an issue, a pull request's number, or no such issue) or why that could not be
learned (`issue_errors`). `fetch_gh.py` builds them with the GitHub CLI and draws the validation sample; see its
docstring. It is tested against a fake `gh` and has not been run against GitHub.

## What it reports

Two checks:

- `proofpane/verify`, meant to be required: green only when there is at least one `required` or `security`
  obligation, every one of them passed, and no claim the pull request makes turned out false. Four of the always-on
  checks are `required` or `security`, so there is always one.
- `proofpane/claims`, informational: green only when every obligation passed.

`run` writes the manifest; `python3 -m proofpane_verify manifest` (`oss/verify`) re-derives it offline.

And, for the validation, four counts over recognised claims: checked and false, checked and true, a resolver exists
but could not decide, and no resolver.

## Limits

- Claim extraction is English patterns only. It errs towards missing a claim: hedged or negated sentences and
  unticked checkboxes yield nothing. Sentences it does not recognise are counted, not examined.
- `diff.no_dependency_change` fails on any change to a manifest, including one that touches only scripts.
- The secret and personal-data rules (`verify_pr/dlp.py`) are a small stand-in for a fuller catalogue.
  Secrets: AWS access keys, GitHub, Slack, Stripe live, Google, Anthropic and OpenAI keys, and private-key headers.
  Personal data: email addresses in any script (not reserved or no-reply ones, and not the user part of a git remote,
  `git@host:path` or `ssh://git@host/...`); card numbers of 13 to 19 digits that begin as a card network's numbers do
  (3 to 6, or 22 to 27) and pass the Luhn check, written as cards are, unbroken or as a group of four digits followed
  by groups of four to six (4-4-4-4, 4-6-5, 4-6-4) with one kind of separator (a space, a dot or a dash), and found
  among other digit groups too, such as a card followed by its code or expiry date, every such stretch being
  redacted, not only the first; US social security numbers (dashes or spaces); and phone numbers written with a `+`
  country code (spaces, dots, dashes, slashes or parentheses between the digits) after anything but a letter, a digit
  or another `+`. Digits joined by a dot to another group (a decimal number) or glued to a letter or digit (a hex
  digest) are not a card. Nothing else is detected: another key format, a key body on a line without its header, a
  card written in groups of other sizes (a 19-digit card as 4-4-4-4-3, say) or whose number starts with 1, 7, 8, 9,
  20 or 21, a phone number without the `+`, or a token written so as to hide it (an HTML character reference, an
  invisible character inside it, an encoding), which a renderer would still show. The rules are for secrets pasted by
  accident.
- The card rule still takes some numbers for cards, since one number in ten passes the Luhn check by chance. Measured
  with `tools/measure_card_rule.py`: 54 of 1,000 random 16-digit integers, 66 of 1,000 random 19-digit integers below
  2^63, 150 of 1,000 lines of six random four-digit numbers, and 5 of 5,000 lines of eight numbers of one to four
  digits; none of 1,000 floats, 1,000 millisecond timestamps, 1,000 lines of years, 1,000 lines of date and time,
  1,000 JSON arrays of floats, or 20,000 strings of 40 hex characters; and all of 3,800 card numbers written in card
  formats. A number taken for a card in the added lines fails `diff.no_pii`, which is advisory and turns
  `proofpane/claims` red; `suppress_pii` scopes a rule off a path.
- Each rule is bounded by its token's own alphabet. A token whose alphabet has both cases (GitHub, Slack, Stripe,
  Google, Anthropic, OpenAI) is not recognised when a letter or digit is glued to its front, since it cannot be told
  from part of a longer word; `tests/test_dlp.py` lists the cases.
- Text is examined in four forms: as written; with inline markdown removed until nothing more comes off; with every
  `*`, backtick, `~~` and HTML tag of up to 200 characters removed, since a renderer joins what those separate; and
  with both removals, one after the other. Secrets are looked for in the added lines, the changed paths, and the
  title and description; personal data in the added lines and the changed paths. A patch is split into lines at `\n`
  only, as git writes it, and a title or description at Markdown's line endings only (`\n`, `\r\n`, `\r`), so a form
  feed or U+2028 inside a line does not start a new one.
- A changed file whose added lines the bundle does not hold whole is read for the lines it does hold, and what they
  show counts; a check that finds nothing there says `unverified` rather than pass over the rest. That is a file with
  no patch (GitHub leaves it out of a large diff) unless the bundle shows it adds no lines, a patch with no hunk or a
  hunk header that does not parse, or a count of added lines other than GitHub's. That holds for the secret and
  personal-data checks and for the checks on test files. Every check that looks for the absence of something (a
  schema change, a dependency change, a non-documentation path, a changed workflow, a check scoped to paths) says
  `unverified` too when the bundle lists fewer files than the pull request changed: GitHub lists at most 3,000.
- `diff.tests_not_weakened` judges a renamed file by its old path too: a test file renamed to a path no test glob
  matches counts as removed, and a test configuration renamed away counts as changed. A copied file is judged by its
  new path only, since the old one stays. While it is `unverified`, a green test check is held back, with its reason,
  which says whether tests were weakened or the bundle only does not show them.
- Every text taken from the pull request (claim text, reasons, evidence such as paths, check names, conclusions and
  issue references, the summary's titles, error messages) goes out through one function, `dlp.safe_copy`. A text in
  which a secret rule matches, or in the stretch of the line it was taken from, is withheld whole, and so is every
  claim taken from a title or description in which a secret rule matches anywhere: the rest of a key can sit in the
  next sentence or on the next line. Otherwise every occurrence of personal data is replaced by the rule's name, and
  the result is scanned again, for secrets and personal data, in every form: if anything matches, it is withheld
  whole. So is a text whose source shows more than 64 distinct personal-data values, which would otherwise be
  searched for one by one. Claim texts longer than 1,000 characters and evidence strings longer than the manifest's
  cap are cut after that, with a note of their length, and checked again. The repository name is copied as it is: it
  identifies the subject, and comes from the fetcher rather than from the pull request's author.
- Manifests and summaries are written as JSON whose text from the pull request stays readable, with C1 controls,
  U+2028, U+2029 and the controls that reorder displayed text escaped, so printing them cannot act on a terminal or
  change the order in which a claim reads.
- `verifier.ruleset_fingerprint` is a digest over the source of every module that decides an outcome, so any
  edit to them changes it, comments included. Line endings are normalised first, so a checkout with CRLF endings
  gives the same fingerprint.
- `summarize` reads every `*.json` under the directory, hidden files included; it records a bundle it cannot verify
  under `not_verified`, with the reason, and a JSON file that is not a bundle under `skipped`, and carries on with
  the rest. `run` exits 2 on anything it cannot verify, with one line whose control characters are escaped.
- An issue number longer than ten digits is not read as an issue reference, and at most 20 issue references are read
  per pull request. `issue.linked` fails when GitHub says the issue does not exist, and when the number is a pull
  request's: the check is that the issue exists, and a pull request is not one. GitHub answers 404 for a
  private repository as for a missing one, so a 404 decides only in the pull request's own repository; in another it
  is `unverified`, like any other lookup that failed (a 403, a name GitHub does not allow). A rate limit or an outage
  that outlasts the fetcher's retries leaves the bundle unwritten, to be fetched on the next run, rather than written
  with the failure in it.
- `ci.checks_green` needs the expected checks configured per repository. Each can be scoped to `paths` (it applies
  only when the change touches one) and marked `kind: "test"`; a claim that tests pass needs at least one applicable
  test check, because a green secret scan says nothing about tests. Without configuration every such claim is
  "could not decide", which is the honest answer.
- A pasted run summary ("5880 passed") is recorded as a report of a run the author did, with no resolver: CI may
  not reproduce the author's environment, so a red CI does not make the report false.
- Hedges and negations are looked for within a few words of the claim, not across the whole sentence.
- It reads a bundle and a configuration given on the command line. Reading the policy from the base ref, and
  running as a GitHub Action, come next.

A `;` does not end a sentence.
