# Proofpane Community

An agentic development environment on the open packages under `oss/`: coding agents (Claude Code, Codex, Gemini CLI,
OpenCode, Grok Build, Qwen Code, Kimi Code, MiniMax Code, or a Claude chat) run side by side, each in a git worktree of
its own; the person reviews and comments on their changes, ships them as pull requests and merges them; the workspace's
gate sees the tool calls of most of the agents and refuses those that reach for their sign-ins, a session of the
person's own can hold its gated commands for their approval, and every finished change is checked by Verify and signed
by the deployment, or recorded as skipped where it is empty or its diff is over 1 MB, and as an error where Verify
cannot read it. It runs on a person's own Mac (the desktop app), on a server they host, or on a hosted workspace, and a
phone drives a hosted one. Its code keeps its working names (`open_edition/`, `proofpane_open`, `OPEN_EDITION_*`).

This file says what it does, what each client gets, how to run it and how to set it. Two guides stand beside it:
`ADE_FOR_SESSIONS.md`, for a coding session that is linked to the ADE, and `SETUP_A.md`, for a person who uses a
hosted ADE and runs the agents on their own computer. The project's reference manual, which this file and the guides
call the manual, is not part of this tree yet: it says each part in turn, what it does and does not do, and what was
measured. The tests that read numbers back from it are skipped here and say so.

## What a signed-in user can do

- **Verify**: paste a title, a description and a `git diff`; get the Verification Manifest v0 that `verify_pr` builds,
  its decision re-derived by `proofpane_core`, and this deployment's signature on it (`proofpane_sign`, ML-DSA-65 and
  Ed25519 together). A pasted diff has no commits and no CI: the manifest records the repository as `pasted/diff` unless
  one is given, each commit as 40 zeros, and a claim that needs CI as unverified. The diff is read as git writes it: a
  byte order mark or a Markdown code fence around it is removed, and anything else git would not write (an indent, a
  non-breaking or invisible character, a dash or capital a phone typed, terminal colour codes, line breaks that are not
  newlines) is refused with the line and what is wrong with it.
- **Check**: upload a manifest or a hash chain v2 file and get the offline verifier's own verdict (`proofpane_verify`),
  or a manifest and its signature and check it against this deployment's public key.
- **Public key**: served without sign-in. A signature is checked against it on the Check page; checking one away from
  this deployment needs the signing package (`oss/sign`, beside this folder in `oss/`).
- **ADE**: the control room for the coding sessions you run yourself; see "The ADE" in the manual.
- **Account**: passkeys (hardware keys and device passkeys), high security, and signing out everywhere, in the app's
  Account (the older page links to it); see "Accounts" in the manual.
- **Connected apps**: where the deployment turns it on, the Claude app connects to the ADE as a custom connector, reads
  your sessions and proposes what you then sign or refuse; see "The Claude app connector" in the manual.

## What a linked session gets, client by client

A linked session is a coding session the person runs themselves, in its vendor's own program, linked to the ADE with
`ade_link.py` (the session channel, in the manual). What the ADE holds of it depends on the client:

- **Claude Code**, built and checked first. On the person's own machine, `ade_link.py setup` writes the link's hooks
  into the session's `.claude/settings.local.json`: the session resumes its link at each start, its turns' ends and its
  permission prompts reach the owner's inbox, and a stop hook before every tool holds its tools once the owner stops it
  (that hook goes in once the owner's keys are pinned). The approval hook, `ade_hook.py`, its block from
  `ade_hook.settings.json` merged into the same file as one block with the link's (`ade_link_and_hook.settings.json`
  holds both, since a second block pasted beside the first drops one block's hooks with nothing said:
  `ADE_FOR_SESSIONS.md`, section 2) and a hook token of the owner's, holds a Bash command `ade_hook.GATE` names (a push,
  a merge, a deploy, a publish, `rm -rf` and the others) until the owner answers it on a screen of theirs; the same act
  in other words (`git -C dir push`, `rm -r -f`) and every MCP tool are not held. A Claude Code session in the cloud
  reads no `.claude/settings.local.json`, so no hook of the ADE's runs there: its commands are not held, and it is heard
  only when it calls the client or through the keeper the client starts for it.
- **Codex**: the ADE writes no block for Codex's own configuration, so neither the approval hook nor the link's stop
  hook holds a Codex session's commands; keep Codex's own approval policy on for anything that pushes, deploys or
  deletes (`ADE_FOR_SESSIONS.md`, section 4). That is not for want of hooks in Codex, which runs managed hooks in Claude
  Code's form: the hosted workspace gives it its gate that way (measured with Codex 0.163.0, in the manual, "The hosted
  workspace"), and there it fails open when a hook crashes, times out, prints nothing or cannot start. A Codex session
  is seen on the board through its log and its commits.
- **Grok Build**: not measured in a linked session. Grok Build reads hooks from a repository's `.claude/settings.json`
  (measured with Grok Build 1.0.46 and 1.0.62: in a git repository whose `.claude/settings.json` holds a hook, it asks
  whether to trust the folder before any hook runs); whether it runs the link's hooks from
  `.claude/settings.local.json`, where `ade_link.py setup` writes them, was not measured, so do not count on them
  holding a Grok Build session's commands. The ADE writes no block for Grok Build's own settings, and a Grok Build
  session is seen on the board through its log and its commits. In the hosted workspace its calls reach the gate through
  enforced hooks in root's `/etc/grok/requirements.toml` (measured with Grok Build 1.0.46 and 1.0.62, in the manual,
  "The hosted workspace"), and there it fails open when a hook crashes, times out or prints nothing.
- **Gemini CLI, OpenCode, Qwen Code, Kimi Code and MiniMax Code**: `ade_link.py setup` writes Claude Code's settings
  alone and the ADE writes no block for theirs; whether any of them runs hooks from Claude Code's settings files in a
  linked session was not measured, so do not count on the ADE's hooks holding their commands. Each is seen on the board
  through its log and its commits. What the hosted workspace does for each is said in "The hosted workspace", in the
  manual.
- **Proofpane's daemon**, 1.5.26 and later, where the person runs its MCP server beside a session: its fixed baseline
  refuses the dangerous commands and a protected path (`~/.ssh`, cloud credentials, a `.env` and the rest) that a tool's
  path argument names or reaches, whatever the client's monitoring switch says and whether or not the daemon is paired,
  and its own grep and glob leave a protected file out of their answers (grep still reads it). It is a check on
  arguments, not a sandbox: the words of a shell command, the programs `app_cli` runs, what a routed tool's search or
  listing reaches under a folder it names, and a routed tool whose arguments the daemon does not know are not
  path-checked, so with the client's monitoring off or the daemon unpaired, where no per-agent policy runs either, they
  read the protected files unrefused.

## Run it

The checks run on Python 3.12 and Node 22.

The ADE on your own machine, in your own browser, in one command (the desktop app's server, without its window: one
account, signed in, its data in `~/.proofpane-community`; the agents you have installed run in worktrees of your
repositories; Ctrl-C stops it and them):

```bash
cd open_edition
pip install -r requirements.txt -r proofpane_workspace/requirements.txt
(cd ui && npm ci && npm run build)
python -m proofpane_open.desktop --open
```

The deployment as a hosted site runs it, and the tests. The app does not start without `OPEN_EDITION_TOKEN_SECRET`
(at least 32 bytes) and `OPEN_EDITION_PUBLIC_URL` (its own address, the base of the invitation links it makes):

```bash
cd open_edition
pip install -r requirements-dev.txt
python -m pytest -q tests
OPEN_EDITION_TOKEN_SECRET=... OPEN_EDITION_PUBLIC_URL=http://localhost:8000 OPEN_EDITION_DATA=/tmp/oe uvicorn proofpane_open.main:app --port 8000
```

On a server, accounts come only from one-time invitation links, which an admin (an address in
`OPEN_EDITION_ADMINS`) makes. The first admin has no account to invite anyone from, so that invitation is made on the
machine that holds the data directory, with the same settings: `python -m proofpane_open invite you@example.com`
prints the link once.

Some tests skip rather than fail where they cannot run, and a skipped test checks nothing: run them with `-rs` and
read the skips before taking a green run as theirs. No test runs a coding agent installed where the tests run.
The image is `Dockerfile`'s, built from the repository root; `fly.toml` is an example deployment.

The phone app's sign-in, and a hosted place the desktop app adds, offer the hosted Community's address,
`https://proofpane-oss.fly.dev` (`DEFAULT_SERVER`, in `ui/src/components/TitleBar.tsx`), and take another typed in
their Server field. A fork that serves its own deployment changes `DEFAULT_SERVER` to that deployment's address before
it builds the apps, and the example address in the two messages that refuse a server that is not https
(`ui/src/components/SignIn.tsx`).

## Settings

| Variable | Meaning |
|---|---|
| `OPEN_EDITION_TOKEN_SECRET` | At least 32 bytes; the app does not start without it |
| `OPEN_EDITION_ADMINS` | Comma-separated addresses that may create invitations |
| `OPEN_EDITION_DATA` | Directory for the SQLite file and the signing key (default `/data`) |
| `OPEN_EDITION_PUBLIC_URL` | This deployment's own address, such as `https://proofpane.example.org`; the app does not start without it. It is the base of invitation links, and its host is also the site passkeys are made for and what the session channel's signatures cover, so changing it leaves every passkey and linked session unusable until added or paired again |
| `OPEN_EDITION_MDS_URL` | Where the FIDO Metadata Service's blob is fetched from (default `https://mds3.fidoalliance.org/`), to judge which passkeys are hardware keys; empty, nothing is fetched |
| `OPEN_EDITION_OSS_ROOT` | Where the open packages are (default `../oss` from the source tree) |
| `OPEN_EDITION_DOMAIN` | The address Caddy serves and gets a certificate for (default `localhost`) |
| `OPEN_EDITION_SIGN_IN_NOTE` | A line the older pages' sign-in (`web/index.html`) shows, the operator's own words about this deployment, such as on whose terms it is offered (`fly.toml`, the example, sets the hosted Community's); unset, or nothing but white space, the page shows none. The ADE app's sign-in, at `/app/` and in the apps, shows none either. Read at each request as one line (white space at either end dropped, each run of it inside made one space) |
| `OPEN_EDITION_KEYSTORE_KEY` | 32 bytes in base64: the key the ADE seals repository tokens with (AES-256-GCM), and each account's pairing with its Proofpane organisation (its device's id and token). Unset, the ADE connects public repositories only and pairs no account with Proofpane; set but not 32 bytes, it refuses to store a token rather than quietly store none |
| `OPEN_EDITION_GITHUB_API` | GitHub's API base (default `https://api.github.com`); a GitHub Enterprise Server's is `https://HOST/api/v3`. HTTPS only, or HTTP to this machine for a test server |
| `OPEN_EDITION_ADE_PUSH_EVERY` | Seconds between the notifier's rounds (default 60); 0 turns the notifier off |
| `OPEN_EDITION_ADE_CONNECT_HOLD` | Seconds a session paired by a ticket, a link moved to a new key by one, and an approved request to link a session wait before the link is made (default 180; a whole number from 0 to 86,400, else the default; 0 links at once): "Pairing waits" and "Asking to be linked", in the manual |
| `OPEN_EDITION_ADE_CONNECT_HOLD_HIGH` | The same while the account's high security is on (default 900; a whole number from 0 to 86,400, else the default; never shorter than `OPEN_EDITION_ADE_CONNECT_HOLD`'s: a value below it takes it) |
| `OPEN_EDITION_ADE_MOVE_HOLD` | Seconds a move the owner signs waits before its machine's link may take it, the owner's devices told at once and Cancel open until then (default 180; a whole number from 0 to 86,400, else the default; a move lives at most an hour and needs 300 seconds past its hold, so past 3,300 every move is refused as it is posted): "Moving a session into the workspace", in the manual |
| `OPEN_EDITION_ADE_E2E_HOLD` | Seconds a change to the account's end-to-end encryption, a first turn-on among them, waits before it takes effect when a passkey confirms it (default 180; a whole number from 0 to 86,400, else the default) |
| `OPEN_EDITION_ADE_E2E_RECOVERY_HOLD` | Seconds such a change waits when the recovery code confirms it, and one that replaces the keys, the recovery code or every other credential (default 86,400; a whole number from 0 to 86,400, else the default) |
| `OPEN_EDITION_ADE_E2E_ROTATE_EVERY` | Seconds in which an account's routine key rotations are at most one (default 86,400; a whole number from 0 to 31,536,000, a year, else the default; 0 sets no bound); a revocation is never bounded |
| `OPEN_EDITION_CONNECTOR` | `on` turns on the Claude app connector: `/mcp`, the OAuth endpoints and Connected apps ("The Claude app connector", in the manual). Anything else, or unset: off, and those routes answer 404; turning it off revokes no connection. With it on, the app refuses to start unless `OPEN_EDITION_PUBLIC_URL` is https (http only on this machine's loopback) |
| `OPEN_EDITION_CONNECTOR_REDIRECTS` | The addresses an app's sign-in may return to, comma-separated (default `https://claude.ai/api/mcp/auth_callback,http://localhost/callback,http://127.0.0.1/callback`; a loopback one matches any port) |
| `OPEN_EDITION_CONNECTOR_CLIENT_HOSTS` | The hosts whose Client ID Metadata Documents the ADE reads, comma-separated (default `claude.ai`) |
| `OPEN_EDITION_CONNECTOR_CLIENT_DOCUMENTS` | Client ID Metadata Documents read whenever they are stale outside the hour's 100 reads of documents not known here, as one read here before is: their exact client_ids, comma-separated (default Claude Code's, `https://claude.ai/oauth/claude-code-client-metadata`; empty for none). Their host must still be one of the hosts above |
| `OPEN_EDITION_ADE_CONNECTOR_HOLD` | Seconds a newly allowed app waits before its connection takes effect (default 180; a whole number from 0 to 86,400, else the default) |
| `OPEN_EDITION_ADE_TASK_RELAY` | Whether a task whose account refused it may move by itself to another of the owner's accounts, or go to the owner's custodian (default off: off unless set, because moving a task between accounts to keep it going is the owner's choice). `1`, `true`, `yes` or `on` is on, and `0`, `false`, `no` or `off` off, in any case and with white space at either end dropped; anything else is the default. Read at each use. A task's own Relay switch starts as this when the task is made, and turns on only while this is on ("Tasks", in the manual). With the relay off, a task whose account runs out of room waits for that account's reset, going on there by itself while Auto-resume is on, or for its owner where no reset is known; its owner moves it with Continue on..., or turns the relay on: this setting first, then the task's own switch, since a task made while this is off keeps its switch off after this is turned on, until its owner turns it on |
| `OPEN_EDITION_ADE_TASK_AUTO_RESUME` | Whether a task waiting for a known reset resumes by itself at it (default on; read as `OPEN_EDITION_ADE_TASK_RELAY` is). A task's own Auto-resume switch turns on only while this is on |
| `OPEN_EDITION_ADE_TASK_CROSS_GROUPS` | Whether a task may cross between the owner's personal plans and an organisation's seats (default off; read as `OPEN_EDITION_ADE_TASK_RELAY` is). A task's own Cross organisations switch turns on only while this is on |
| `OPEN_EDITION_ADE_TASK_TEAMMATE` | Whether a task may be handed to a teammate, by an offer or by custody (default on; read as `OPEN_EDITION_ADE_TASK_RELAY` is). It also needs the fly provider and `OPEN_EDITION_OPERATOR` ("Handing a task to a teammate", in the manual), and custody's hand-off, the relay's second step, needs the relay on as well: this deployment's `OPEN_EDITION_ADE_TASK_RELAY` and the task's own switch |
| `OPEN_EDITION_OPERATOR` | Who operates this deployment: a name and a contact, as one line (white space at either end dropped, each run of it inside made one space), read at each use. Risk point 13's text names them as who holds a teammate's record and whom to ask to see it, to correct it or to have their user in it reduced. Unset, or nothing but white space: offers and custody are off, and the texts keep `{operator}` as written, which no page then shows ("The terms at each risk point", in the manual) |
| `OPEN_EDITION_ADE_ACCOUNT_RECORD_DAYS` | Days a deleted task's account record is kept, counted from the task's deletion: a whole number of up to seven digits, or `forever` (in any case) to keep it for good; unset, or anything else, 90. Read at each use |
| `OPEN_EDITION_ADE_TASK_ACCOUNT_HOLD` | Seconds an account confirmed as the owner's own waits before it joins tasks' relays (default 180; a whole number from 0 to 86,400, else the default) |
| `OPEN_EDITION_ADE_TASK_CROSS_HOLD` | Seconds a task's Cross organisations switch, turned back on, waits before it takes effect (default 180; a whole number from 0 to 86,400, else the default) |
| `OPEN_EDITION_ADE_TASK_OFFER_HOLD` | Seconds an offer of a task to a teammate, and a task's hand-off to the custodian, wait before they go (default 180; a whole number from 0 to 86,400, else the default) |
| `OPEN_EDITION_ADE_TASK_CROSSING_HOLD` | Seconds custody's crossing, ticked on a task, waits before it takes effect (default 180; a whole number from 0 to 86,400, else the default) |
| `OPEN_EDITION_ADE_LOCAL_REPOS` | `name=/absolute/path,...`: clones on this machine an admin may connect by name. For a single-user install and the tests; never set on the hosted site |
| `OPEN_EDITION_PROOFPANE_ADDRESSES` | Comma-separated Proofpane addresses an account may pair the ADE with, for its linked sessions' usage: each `https://` with a host and at most a port, or `http://` to this machine for a Proofpane run on it. Default `https://api.proofpane.com`; empty for none. The server itself spends the pairing code and posts the usage there, so the operator names the addresses, never the person pairing |
| `OPEN_EDITION_APP_ORIGINS` | Comma-separated origins, besides the site's own, whose pages may call the API (CORS) and open its sockets: the phone app's own pages. Default `tauri://localhost,http://tauri.localhost,https://tauri.localhost`; empty for none. Every request still needs a sign-in, and no answer sets a cookie |
| `OPEN_EDITION_ADE` | The hosted workspace's provider: `local` or `fly`. Unset (the default), there is no hosted workspace. `local` gives an admin a shell on this machine and is refused where `FLY_APP_NAME` is set |
| `OPEN_EDITION_WORKSPACE_APP` | The fly provider's Fly app for the workspace machines, never this app's own; required. With `OPEN_EDITION_ADE=fly` the app reads the `OPEN_EDITION_WORKSPACE_*` settings and `OPEN_EDITION_FLY_API` when it starts: a required one missing, or any one wrong, and it does not start; the provider needs `OPEN_EDITION_KEYSTORE_KEY` too |
| `OPEN_EDITION_WORKSPACE_IMAGE` | The workspace image, as Fly's registry names it (`workspace_image/Dockerfile`'s); required |
| `OPEN_EDITION_WORKSPACE_FLY_TOKEN` | A deploy token for the workspace app alone (`fly tokens create deploy -a <app>`); required |
| `OPEN_EDITION_WORKSPACE_OWN_NETWORK` | `yes` where the workspace app and this app are alone on their Fly private network, `no` where they share it with other apps; required, since a workspace runs its user's code (`ade/fly_provider.py` says what each means) |
| `OPEN_EDITION_WORKSPACE_REGION` | The workspace machines' Fly region: default this app's own (`FLY_REGION`), else `syd` |
| `OPEN_EDITION_WORKSPACE_CPUS` | A machine's shared CPUs, 1 to 8 (default 1) |
| `OPEN_EDITION_WORKSPACE_MEMORY_MB` | A machine's memory, 256 to 16,384 and a multiple of 256 (default 2048) |
| `OPEN_EDITION_WORKSPACE_VOLUME_GB` | Each user's volume, their home in the workspace, 1 to 500 (default 5) |
| `OPEN_EDITION_WORKSPACE_IDLE_MINUTES` | Minutes unused after which a machine stops by itself, 5 to 1,440 (default 30) |
| `OPEN_EDITION_WORKSPACE_FOR` | Who may have a workspace: `admins` (the default) or `everyone` |
| `OPEN_EDITION_WORKSPACE_MAX` | The most workspaces this deployment makes, 1 to 1,000 (default 10) |
| `OPEN_EDITION_WORKSPACE_CAP_USD` | The cap on what all the workspace machines together spend in a calendar month (UTC): 50 US dollars, which this lowers (0.01 to 50) and nothing raises. A start is refused when the month's spending and an hour of every machine that would then run passes it, and every started machine is stopped when the spending and a quarter of an hour of them would reach it. The spending is this app's estimate from Fly's published prices, not Fly's bill |
| `OPEN_EDITION_WORKSPACE_USD_PER_HOUR` | What a started machine of this size costs an hour, for that estimate, 0.001 to 100 (default 0.03) |
| `OPEN_EDITION_WORKSPACE_SHARE_USD` | An account's share of the cap a month, unless it is an admin's, whose share is the whole cap: 0.01 to 50, never more than the cap (default the cap divided by `OPEN_EDITION_WORKSPACE_MAX`) |
| `OPEN_EDITION_WORKSPACE_KEEP_DAYS` | Days a workspace is kept after its account leaves the hosted choice, 1 to 3,650 (default 30) |
| `OPEN_EDITION_FLY_API` | Fly's Machines API (default `https://api.machines.dev`): an https address, or an http one to this machine or to Fly's own `_api.internal` (`http://_api.internal:4280`) |
| `OPEN_EDITION_ADE_TRIGGER_HOURS` | On the fly provider, the hours a month a user's machine may run before that user's schedules, hooks and tasks are held back (default 200; a whole number, else the default; one below 0 is read as 0) |

## Licence

Proofpane Community is open source under the Apache License 2.0 (`LICENSE`), as are the open packages under `oss/`
it runs on (`oss/LICENSE`); `NOTICE` says what this tree includes from others
(among them xterm.js, under the MIT License, and test vectors from Project Wycheproof, under the Apache License
2.0, whose text `LICENSE-APACHE-2.0` holds too).
