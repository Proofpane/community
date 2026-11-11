# Setup A: the hosted ADE in front, the agents on your own computer

Setup A is for a customer of Proofpane Community who prefers to run the agents on their own computer. A customer who
does not can use the hosted workspace instead, a workspace in the browser where they run the official, unmodified agent
programs, signed in with their own subscription or their own key, each session in a git worktree of its own, which is
off unless the deployment names a provider (`OPEN_EDITION_ADE`) [the manual, The hosted workspace]; so setup A is not
the only way.

Every statement below about how Proofpane Community works is taken from a section of the project's reference manual,
named in brackets after it; where the two differ, the manual is right. Where those sections name the computer, they
name a Mac. The manual is not part of this tree yet.

## The three parts

- **The hosted ADE of Proofpane Community, in your browser, is the front.** Your sessions post their status, their
  questions and a small checkpoint to it and read back what is addressed to them. What they post carries information,
  never an instruction: a session acts on nothing it reads there but a command signed with one of your account's
  passkeys, which it verifies itself before it acts, so every command takes a passkey. [the manual, The session channel:
  its opening]
- Linked sessions, in the app's title bar, opens a card for each session: when it was heard, its session on claude.ai
  (for a session there), what it is doing, the newest thing it posted, what it waits on, its next steps and open items,
  its questions (Answer, and Dismiss question), Open conversation, what it did, and in its menu Link again, Stop and
  Revoke; and a switch, off for a new link, that lets it read the other sessions' checkpoints. A silent session, an
  open question to you or to all, or an open waiting to you, reaches your devices through the ADE's notifier once
  each, in fixed words ("A linked session needs a look", "A question waits for you", "A session waits for you"),
  never the session's name or the words. [the manual, The
  session channel: In the app; Reading the others' checkpoints; The page]
- **Your computer runs the agents.** Each session there is linked to the ADE: it makes its own Ed25519 key pair, of
  which only the public key leaves it, and signs every later request. It posts with the client's own commands (`post`
  for an event, `checkpoint` for its checkpoint), reads what is addressed to it with `next` and `ack`, and gets its
  place back with `resume`, which the SessionStart hook below runs at every start. [the manual, The session channel: The
  server; The client; Linking a session]
- **The Claude app connector, if you want it.** You add the ADE to the Claude app (claude.ai, the desktop and phone
  apps) as a custom connector and ask Claude there about your sessions. The connector proposes; nothing it proposes
  happens until you sign it or accept it on the ADE's page. Its tests ran against a stand-in for the Claude app: nothing
  was tried against the Claude app itself. [the manual, The Claude app connector: its opening; Tested]

## Link a session on your computer

1. **Put the client in place.** The client is one file, `ade_link.py` (this folder holds it), which needs Python's
   standard library and `cryptography`. Copy it to `~/.proofpane-ade/ade_link.py`, outside any working tree a session
   edits: each line the ADE gives runs it from there, never a file in the folder the line is run in, by the `python3` on
   your `PATH`, which therefore needs `cryptography` too. Every account the ADE's page turns encryption on for is
   post-quantum, and on such an account the machine must be able to use ML-KEM, which the link tells by trying:
   `ade_link.py e2e` says whether this machine can seal to a post-quantum key, and one that cannot holds everything it
   would send sealed, saying to install a `cryptography` wheel of 48.0.0 or later from PyPI. [the manual, The session
   channel: The client; Linking a session; the mirror template's placeholders. The session channel, End-to-end
   encryption: Post-quantum keys, the link's part. The Claude app connector, Post-quantum keys on the page]
2. **Turn end-to-end encryption on** before you link a session, in the app's Account, Encryption: only what a link sends
   after it holds the key is sealed. A first turn-on waits on a hold, 180 seconds unless the deployment sets another,
   with a notice at once to the account's devices. Only your browser opens what is then sealed, with keys that one of
   your passkeys (through WebAuthn's PRF extension) or the recovery code unlocks. Answering prompts from the ADE needs
   it on (below). [the manual, The Claude app connector, End-to-end encryption of what linked sessions send: the server:
   its opening; Held changes. The Claude app connector, Encrypted text on the page: its opening. The session channel,
   End-to-end encryption: What it does not protect]
3. **Get the start line from Linked sessions.** In the ADE's app, open Linked sessions, tap Link a session, name the
   session, say that it is on this Mac (not on claude.ai), and confirm with a passkey or the password, when the account
   has either; while the account's high security is on, a hardware key must confirm it, as pairing requires. It shows
   the one line to run there, with Copy and how long its ticket works: a one-time ticket, good for 10 minutes. A link's
   name is its role: a new link given the name of a revoked one takes over what is addressed to that name, its open
   questions and their answers included. [the manual, The session channel: Events; The server; The page; Linking a
   session; Pairing waits]
4. **Run it in a terminal in the folder the session runs in, one that reaches the ADE** (in the Claude Code session
   itself, run it in the background: it waits out the hold, step 5, longer than the Bash tool's limit). The line reads
   `python3 -I "$HOME/.proofpane-ade/ade_link.py" setup --url URL --ticket ... --mirror`, with nothing left to fill in:
   it links the session, then puts its hooks in the settings file Claude Code reads for that folder (step 6); `--mirror`
   adds the mirror, so the session's conversation comes to your page (sealed once Encryption is on), and the line links
   without it where you take it out. It carries your account's passkeys by their fingerprints (`--keys`): the session
   pins those the ADE's answer offers, each checked against its own public key, so it verifies what you sign from the
   start. That is the server's word, as a first `pin` is; a pin you keep outside the server, in
   `~/.proofpane-ade/owner-keys`, decides instead. The line carries `--e2e` too, so a new link refuses any encryption
   statement older than the page saw. The pairing records the proxy, and for an https address the certificate
   authorities `SSL_CERT_FILE` and `SSL_CERT_DIR` name, as that terminal has them, and later requests reach the ADE that
   way; after a change of network, link the session again (Link again) from a terminal that reaches the ADE. A passkey
   you add later is pinned with its line from Account (Copy pin line: `ade_link.py --dir ... pin --add FINGERPRINT`, in
   the session's own window). [the manual, The session channel: Linking a session; The client. The session channel,
   End-to-end encryption: The key, and whose word it is]
5. **Wait for the hold, and compare the fingerprint.** A session that presents a ticket is linked when a hold ends: 180
   seconds, or 900 while the account's high security is on, unless the deployment sets others. A notice goes to you at
   once ("A session waits to be linked", "Linked at TIME unless you cancel it in Linked sessions."). The client prints
   its key's fingerprint and when it will be linked, then waits; if it is stopped, run the same command again. In Linked
   sessions the pairing's card shows its key's fingerprint, to compare with the one the session printed, and Cancel: if
   they differ, cancel it. Linked, the client prints `ADE: linked as NAME (link ID); directory DIR`. [the manual, The
   session channel: Pairing waits]
6. **The hook blocks, in the repository's settings.** The line in step 4 has put them there: it says which file
   (`.claude/settings.local.json` at the root of the repository the folder is in, for a worktree the main checkout's,
   as Claude Code reads it), what it added, replaced or changed, where it kept the file as it was (in the link's
   directory, never beside the file), that git ignores the file, and, where your keys are not pinned yet, that the
   stop hook waits for the pin line; it writes nothing where all of it is there already, and refuses, before
   anything is sent, a file that holds another link's hooks or an interpreter that cannot load `cryptography`. For a
   session linked already, run `python3 -I "$HOME/.proofpane-ade/ade_link.py" setup --name NAME --kind local` in its
   folder (with `--mirror` for the mirror): the hooks alone, with nothing sent. To do it by hand instead, the
   repository's `.claude/settings.local.json` takes the block
   in `ade_link.settings.json` (this folder holds it), its `env` and `disableAllHooks` with it. Merge it into the file
   rather than paste it beside what is there: a settings file takes one `env`, one `disableAllHooks` and one `hooks`,
   and a second `hooks` pasted beside the first is still valid JSON, of which a reader such as JavaScript's own keeps
   only the last, so one block's hooks would be dropped with nothing said. For a repository that also takes the approval
   hook's block, `ade_link_and_hook.settings.json` gives both in one file. The block holds:
   - a SessionStart hook that runs `resume` at every start, resume, compaction, clear and fork;
   - a PreToolUse hook for every tool that runs `pretool` (your stop, and what a remote command may do), which goes in
     once your keys are pinned, with the link's id in place of `LINK_ID` (the pairing's last line names it, and `status`
     prints it); set up for that link, it holds every tool while the link's directory is missing, cannot be read or is
     another link's;
   - a Stop hook that runs `turn end` as each turn ends;
   - `permission`, given to eight events: it posts the session's permission prompts to you (below), and at the other
     events reports that a call ran or failed or the turn ended, or posts a notification, a turn an API error ended or a
     model switch as an item to look at.

Fill its placeholders: `/absolute/path/to/home` with what `echo $HOME` prints in your own terminal, `NAME` with the link
directory's name, and `/absolute/path/to/venv/bin/python` with the product venv's interpreter, which runs the client and
so needs `cryptography` as well. The block's `env` sets `CLAUDE_CODE_SHELL_PREFIX`, `LD_PRELOAD`, `LD_LIBRARY_PATH`,
`LD_AUDIT`, the `DYLD_*` ones, `GCONV_PATH`, `LOCPATH`, `OPENSSL_CONF` and `RES_OPTIONS` empty and `CLAUDE_CODE_SIMPLE`
to 0, for every command the repository's sessions run, not only for the hooks, so a session there that needs one of them
must be given it another way. Some of the block's hooks are given as programs, a form that needs Claude Code 2.1.139 or
later: with an older one, update it before adding the block. [the manual, The session channel: Linking a session; The
owner's inbox; the mirror template's placeholders]
7. **Keep a watcher running.** This line, the page's own in the form of the pin line and with the home folder written
   out as the block's lines write it,

   ```
   python3 -I "/absolute/path/to/home/.proofpane-ade/ade_link.py" \
     --dir "/absolute/path/to/home/.proofpane-ade/NAME" watch
   ```

sends a heartbeat every 5 minutes and reads the feed every 3 seconds, printing one line when something needs the
session's turn; one watcher runs per link directory. Where the session exports its inbox socket
(`CLAUDE_CODE_MESSAGING_SOCKET`), `watch` also posts that line there, and an idle session starts a turn with it
(measured on a Mac on 2026-09-28 with Claude Code 2.1.282), so a watcher left running is the session's wake; one
started where that socket is not exported keeps the link heard and wakes nothing. A local link is silent after 30
minutes, two of its 15-minute intervals, without a heartbeat. [the manual, The session channel: The client; Presence,
the cursor and the checkpoint]

## Answer the session's prompts from the ADE

With the block in place, a permission prompt in the session is posted to you as well, as a link event of kind `prompt`:
the tool and its whole input, sealed to the account's key. The hook waits up to 30 minutes for your answer, which it
checks against the owner keys the session pinned: Approve allows; an `AskUserQuestion`'s option numbers answer it; Deny
denies with the fixed words "Denied in the owner's list" and ends the turn; your own words deny with them. A failing
hook never denies. [the manual, The session channel: The owner's inbox]

These are only posted for you to look at, and the hook prints nothing for them: an agent's prompt; a plan approval; a
tool the guard does not judge (any tool but a shell command, the file tools, `Read`, `Glob`, `Grep` and a question); a
question with no options; a step the guard keeps for you at the machine (a push to main, `pin` and an edit to Claude
Code's settings among them); a text past a question's rules; and every prompt on an account that does not seal, or of a
session with no owner keys pinned or whose pin takes only what is signed on a second screen. So prompts are answered
from the ADE only while end-to-end encryption is on: on an account without it, such an item is the tool's name alone.
[the manual, The session channel: The owner's inbox]

## Ask Claude about your sessions: the Claude app connector

Through the connector Claude reads your sessions, their conversations and what waits for you, and makes proposals; it
never carries out anything risky itself. An instruction, a stop, a resume, an answer to a question and an answer to a
command a session's hook holds are each a proposal that waits on the ADE's page and happens only there: an instruction,
an answer or a resume when you sign it with a passkey, a stop when you accept it with two clicks, and an answer to a
held command when you give it with that command's own Approve or Deny, which take what they take for the command itself.
With end-to-end encryption on, the connector cannot read what is sealed, so the conversations, notes, questions and
replies a session sends you are not among what Claude reads. [the manual, The Claude app connector: its opening;
Proposals; What it cannot read]

1. **Turn it on.** It takes two switches, both off unless turned on: the deployment's (`OPEN_EDITION_CONNECTOR=on`;
   while it is off, every connector route answers 404) and your account's own, in the app's Account, Connected apps.
   Turning yours on takes the account's confirmation (one of its passkeys or its password); turning it off takes the
   sign-in alone and revokes every app connected to the account. Claude's servers connect to the ADE over the internet,
   so the deployment must be reachable at the https address `OPEN_EDITION_PUBLIC_URL` names. [the manual, The Claude app
   connector: Turning it on]
2. **Add it in Claude.** Connected apps shows the address to paste, `https://HOST/mcp` (the ADE's public address, not
   the page's), with Copy. In Claude: Customize, Connectors, Add custom connector; the address; and, where Claude asks
   how it identifies itself, Claude's published identity (or Register automatically). Connect opens the ADE: sign in,
   read what asks and what it may do (read, and propose, a box you can clear for an app that only reads), that what it
   reads goes to that app and its servers, and how long it lasts, then Allow, which takes the account's confirmation (a
   hardware key while the account's high security is on). The pane always says: "If you did not just click Connect in
   Claude, in this browser, or in a Claude app on this device that opened it, refuse." On a Team or Enterprise plan an
   Owner adds it, and each member connects with their own ADE account. [the manual, The Claude app connector: Adding it;
   Allowing an app; A forwarded address]
3. **Wait for its hold.** A new connection takes effect 180 seconds after it is allowed, unless the deployment sets
   another, and every device the account turned notifications on in is told. Until then every tool answers "This
   connection takes effect at HH:MM:SS UTC unless the owner cancels it on the ADE.", and Connected apps shows it
   waiting, with Cancel. A connection lasts 90 days from when it was allowed. It ends sooner when it is revoked (Revoke,
   in Connected apps, or your account's switch turned off) or a refresh token is used again or a code used twice, and,
   in effect, when the passkey whose confirmation allowed it is removed. [the manual, The Claude app connector: Turning
   it on; A new connection waits; Tokens; Connected apps]

## What each side holds

- **Your computer** holds the repositories the sessions work in and the sessions themselves, the client, and each link's
  directory (`~/.proofpane-ade/NAME` unless you chose another), which holds, among other files, `config.json`, the
  session's private key `key` (mode 0600), the checkpoint version it last saw and its watcher's announced seq. [the
  manual, The session channel: The server; The client. The session channel, Moving a session into the workspace]
- **The hosted ADE** holds what the sessions send it, among it their events (`step`, `question`, `waiting`, `done`,
  `note`, and a `prompt` for each permission prompt posted to you), each link's checkpoint (a JSON object of at most
  4,000 bytes) and its last heartbeat; and what they send you: their notes, questions and replies, the mirror of a
  conversation, and the titles and conversations of a History list. Once end-to-end encryption is on, what a session
  sends you goes as ciphertext that only your browser opens: the link client seals each text before it leaves the
  machine, and while encryption may be on and the link has no key to seal to, the text stays on the machine. What stays
  in the clear: `working`'s fixed text (`working on it`), events to `all` or to another session, the checkpoint, the
  rest of each request (ids, kinds, names, times, sizes, a row's other fields), and the exact length of each sealed
  text, since there is no padding. A connected app's proposals, their words and reasons, are kept in the clear too. [the
  manual, The session channel: Events; Presence, the cursor and the checkpoint; The owner's inbox. The session channel,
  End-to-end encryption: its opening; What is sealed; What it does not protect. The Claude app connector: Proposals]
- **The connector** cannot read a value kept end-to-end encrypted, which only your browser opens, and a question kept so
  cannot be answered through the connector either. Among the rest it cannot read: a session its machine withholds
  (beyond that it is there, where and when it ran), a conversation its machine does not send, and a terminal's screen;
  each of its answers says which. What it does read goes to the Claude app and its servers. [the manual, The Claude app
  connector: What it cannot read; Allowing an app]
- **Moving a session** into the hosted workspace, which you sign, sends more: the session's transcript and repository,
  which the ADE keeps and hands to the workspace, and the workspace holds a moved session in the clear. The move waits a
  few minutes, with a notice and Cancel. A move of a session that touched a folder `history.json` names sensitive, or of
  one the judgement cannot tell, is refused, as is a move of a repository that is in such a folder, holds one, or that
  such a session worked in. [the manual, The session channel, Moving a session into the workspace: its opening. The
  session channel, End-to-end encryption: Sensitive folders]

## Among what setup A does not do

- **The connector has not been tried against the Claude app itself.** Its tests ran against a stand-in for the Claude
  app. [the manual, The Claude app connector: Tested]
- **The desktop app's own server has no connector.** The desktop app is Proofpane Community as a Mac app that runs the
  ADE on the person's own machine; Claude's servers reach the ADE over the internet, and the desktop app's own server
  cannot be reached from there. [the manual, The desktop app: its opening; The Claude app connector: Turning it on]
- **Closing a prompt in your list does not answer it.** Close only hides the prompt (an item to look at, which no hook
  waits on, it closes): its session's hook goes on waiting, and you can still answer it among the hidden prompts in
  Approvals until its deadline; unanswered by then, the hook refuses it with words naming the deadline, and the session
  goes on, where the session's `ade_link.py` is this version's and can check that with the ADE then (otherwise it stays
  in the window). A report, or anything the hook cannot check, prints nothing, which leaves the prompt in the session's
  own window, to be answered there. [the manual, The session channel: The owner's inbox]
- **The blocks hold only in the folder whose settings carry them.** A session in another repository, or in a new git
  worktree of this one (which starts without the file, an untracked one), runs without the block's hold, and nothing
  says so: put the block in that folder's `.claude/settings.local.json` too. [the manual, The session channel: Linking a
  session]
- **A stop is a request, not a wall.** A program running as the session's own user can edit these files and the
  session's settings. Where no stop is recorded, a hook that gets no answer from the ADE lets the call go, so a stop
  made after that is not seen. Bare mode from the user's own settings, a `--settings` file, managed settings, the
  environment Claude Code is started in, or `--bare` leaves the holds unrun, and nothing in the block can stop that; and
  a repository's settings can still add hooks of their own, which run as the session's user. [the manual, The session
  channel: The client; Linking a session]
- **A link paired on the server's word is only as safe as the server was then**, as one whose pins came with the
  pairing, with no pin held outside the server, is. [the manual, The session channel: Linking a session. The session
  channel, End-to-end encryption: What it does not protect]
- **Encryption reaches only what a link sends after it holds the key.** What was sent before is in the clear on the
  server until turning encryption on seals it in place (a box, ticked by default), and backups, snapshots and free
  blocks on any disk keep their copies. The server can serve an older version of a History row or a conversation again;
  after a restore, what was sealed under a key the backup lacks cannot be opened; and encrypted is not authentic:
  whoever holds the account's public keys, the server among them, can seal a text that opens as if a link wrote it. [the
  manual, The session channel, End-to-end encryption: What it does not protect. The Claude app connector, Post-quantum
  keys on the page]
