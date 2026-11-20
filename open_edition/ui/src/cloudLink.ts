/** The block Linked sessions gives to link a session on claude.ai (CLOUD-LINK, queue 4.317), whose container holds no
 *  link client: pasted into that session whole, it checks that the session's python3 can import cryptography, fetches
 *  the client from the ADE's own address (the route proofpane_open/ade/link_client.py serves), checks it against the
 *  sha256 this page's build computed from open_edition/ade_link.py, puts it where SETUP_A.md installs it only on a
 *  match, then runs setup as the line for a session on this Mac does. The hash comes from this page's bundle, so a
 *  server that is not the ADE cannot change the code the block runs. Where the environment's proxy refuses the ADE's
 *  host, the block says the one setting that lets it through, the same sentence the link client's setup and pair say
 *  (ade_link.py HOST_REFUSED) and the page gives as its step 1, before the block (CLOUD-LINK-2, queue 4.319); it says
 *  so once and waits for that change, trying again every ten seconds until the ticket's end the page writes into it,
 *  then goes on by itself, so a block pasted before the setting is changed needs no second paste (CLOUD-LINK-3, queue
 *  4.320). What the block's own steps print before setup runs is fixed words, an address the page wrote, the session's
 *  own home folder, a status code or an exception's type, never words a server or a proxy chose; setup prints what it
 *  prints for a session on this Mac. Every value goes into the program as a JSON string or, the ticket's end, as a
 *  whole number, which Python reads as the same.
 *  tests/vectors/cloud_link_page.json keeps the block as the page gives it, which tests/test_cloud_link.py runs in a
 *  shell. */
import { ADE_URL, inForm } from "./shellForms";

// the build's word (vite.config.ts); none where a tool builds this file without it, and then no block is given
declare const __ADE_LINK_SHA256__: string | undefined;

/** The sha256 of open_edition/ade_link.py, as this page's build computed it; empty where the build gave none. */
export const CLIENT_SHA256: string = typeof __ADE_LINK_SHA256__ === "string" ? __ADE_LINK_SHA256__ : "";

/** Where the ADE serves its link client, with no sign-in (proofpane_open/ade/link_routes.py, GET /client). */
export const CLIENT_PATH = "/api/ade/link/client";

/** The one setting a claude.ai environment needs for the ADE at `host`: what the block and the link client say where
 *  the environment's proxy refuses that host, and what the page says as the card's step 1, before the block. */
export const hostRefused = (host: string) =>
  `A claude.ai environment whose network refuses ${host} needs it allowed: in the environment's settings, under Network `
  + `access, choose Limited and add ${host} to Allowed domains, keeping Allow package managers ticked; a running `
  + "session follows the change within about a minute.";

/** Anthropic's page on cloud environments, at the part of its Network access that says how a host is let through
 *  ("Allow specific domains": Limited, then the host in Allowed domains), once per environment (MANUAL.md, Linking a
 *  session on claude.ai): the page links it beside the setting, the card's step 1. */
export const ENV_DOCS = "https://code.claude.com/docs/en/cloud-environments#allow-specific-domains";

/** What the block says where the client the ADE served is not the one this page carries. */
export const CLIENT_CHANGED = "The link client the ADE served is not the one the page carries (its sha256 differs), so "
  + "nothing was installed or run: take the block again from the page the ADE itself serves, which carries the client "
  + "of its own release.";

/** What the block says where the session's python3 cannot import cryptography, which the link client needs. */
export const NO_CRYPTOGRAPHY = "This session's python3 cannot import cryptography, which the link client needs, so "
  + "nothing was fetched, installed or run: install it for that python3 with pip, which Allow package managers lets "
  + "through, then paste the block again.";

/** How often, in seconds, the block tries the ADE's host again while its environment refuses it (CLOUD-LINK-3, queue
 *  4.320): a running session follows a changed setting within about a minute (Anthropic's page), and a refused try is
 *  answered at once. The block's words and the card's say this one number. */
export const AGAIN_EVERY = 10;

/** The seconds the block leaves between its last try and the ticket's end, for the fetch, setup's start and the pair,
 *  which the server refuses from the ticket's end on. */
const ROOM = 20;

/** What the block says after the setting, where the environment's proxy refuses the ADE's host and the ticket leaves
 *  time to wait: that it waits for the change while its ticket lives, so the block can be pasted before the setting is
 *  changed and nothing is pasted twice. Said once, however often it tries. */
export const BLOCK_WAITS = `This block waits for that change: it tries again every ${AGAIN_EVERY} seconds while its `
  + "ticket lives and goes on by itself once the host is let through, so nothing needs pasting again.";

/** What the block says where the environment still refuses the ADE's `host` and the ticket leaves too little time for
 *  another try and the link (where it never could wait, after the setting's sentence alone). */
export const ticketEnded = (host: string) =>
  `The ADE's host, ${host}, is still refused and the ticket has too little time left to link, so nothing was installed `
  + "or run: make a new ticket in Linked sessions and paste its block.";

/** How long, in seconds, the card waits for a pairing after it shows the block before it puts the setting back above
 *  the block (CLOUD-LINK-3, item 4): a block pasted before the setting says so in the session, which the person may not
 *  be watching, and the page is where they look. */
export const REMIND_AFTER = 120;

/** What the card says then, before the setting's sentence and the host's Copy. */
export const NOT_REACHED = "The session has not reached the ADE yet. In a claude.ai session the usual cause is its "
  + "environment's network setting:";

/** The block's first line, to the session it is pasted into: as SETUP_A.md's step 4 says, run in the background (with
 *  a timeout above the longest wait: the ticket's life and the pairing's hold, 15 minutes while high security is on),
 *  and stayed with until it ends, so that the session's turn goes on (an idle session's VM pauses, by Anthropic's page)
 *  and each `ADE:` line is told as it comes: watched with the Monitor tool, whose watch ends at 5 minutes unless longer
 *  is asked (30 at most), so a watch of 30 minutes, started again if it ends first. */
const TO_THE_SESSION = "# To the Claude Code session it is pasted into: run this block as it is with the Bash tool, in the "
  + "background with a timeout of an hour (it waits for its environment to let the ADE's host through, while its "
  + "ticket lives, and out the pairing's hold), and stay with it until it ends, watching it with the Monitor tool (a "
  + "watch of 30 minutes, started again if it ends first), to tell the person each line it prints that begins with "
  + "ADE:, word for word, as it comes.";

/** The host of `url`, an address in ADE_URL's form, as a claude.ai environment's Allowed domains takes it: in lower
 *  case, its port left out, as the link client's own (Link.rp_id). Read from the text, which ADE_URL has checked. */
export const hostOf = (url: string) => url.replace(/^[a-z]+:\/\//, "").replace(/:[0-9]+$/, "").toLowerCase();

/** Whether `url` is the ADE on this machine's loopback (ADE_URL's http form), which no claude.ai environment's list of
 *  allowed domains reaches: no setting is said for it. */
export const onThisMachine = (url: string) => url.startsWith("http://");

/** The block for the ADE at `url` that installs the client and then runs `setup`, the line for the session as the
 *  page writes it. Where the environment's proxy refuses the ADE's host the block says the setting once, with that it
 *  waits, and tries again every AGAIN_EVERY seconds while a try leaves ROOM seconds before `until`, the ticket's end as
 *  the server wrote it (seconds since the epoch): let through, it goes on as before; with too little time left, it says
 *  so in one line (after the setting's sentence alone, where it never could wait). Any other failure, or an answer from
 *  the ADE that is not the client, ends it at once, as before. Null where the address is not in the form the server
 *  writes it, the ticket's end is not a whole positive number, or the build gave no sha256. */
export function cloudBlock(url: string, setup: string, until: number): string | null {
  if (!inForm(ADE_URL, url) || !/^[0-9a-f]{64}$/.test(CLIENT_SHA256)) return null;
  if (typeof until !== "number" || !Number.isSafeInteger(until) || until <= 0) return null;
  const said = (text: string) => JSON.stringify(`ADE: ${text}`);
  const host = hostOf(url);
  return [
    TO_THE_SESSION,
    "python3 -I - <<'ADE_CLIENT' &&",
    "import hashlib, os, sys, time, urllib.error, urllib.request",
    "try:",
    "    import cryptography",
    "except ImportError:",
    `    sys.exit(${said(NO_CRYPTOGRAPHY)})`,
    `URL = ${JSON.stringify(url + CLIENT_PATH)}`,
    `UNTIL = ${until}`,
    "told = False",
    "while True:",
    "    try:",
    "        with urllib.request.urlopen(URL, timeout=60) as answer:",
    "            client = answer.read(16 * 1024 * 1024)",
    "        break",
    "    except urllib.error.HTTPError as e:",
    '        sys.exit(f"ADE: no link client from {URL}: HTTP {e.code}; nothing was installed or run.")',
    "    except Exception as e:",
    '        why = getattr(e, "reason", e)',
    '        if not str(why).startswith("Tunnel connection failed: 403"):',
    '            sys.exit(f"ADE: no link client from {URL} ({type(why).__name__}); nothing was installed or run.")',
    `    if time.time() + ${AGAIN_EVERY} + ${ROOM} > UNTIL:`,
    "        if not told:",
    `            print(${said(hostRefused(host))}, file=sys.stderr, flush=True)`,
    `        sys.exit(${said(ticketEnded(host))})`,
    "    if not told:",
    `        print(${said(`${hostRefused(host)} ${BLOCK_WAITS}`)}, file=sys.stderr, flush=True)`,
    "        told = True",
    `    time.sleep(${AGAIN_EVERY})`,
    `if hashlib.sha256(client).hexdigest() != ${JSON.stringify(CLIENT_SHA256)}:`,
    `    sys.exit(${said(CLIENT_CHANGED)})`,
    'home = os.path.join(os.path.expanduser("~"), ".proofpane-ade")',
    "try:",
    "    os.makedirs(home, mode=0o700, exist_ok=True)",
    '    part = os.path.join(home, f"ade_link.py.{os.getpid()}.part")',
    '    with open(part, "wb") as fh:',
    "        fh.write(client)",
    '    os.replace(part, os.path.join(home, "ade_link.py"))',
    "except OSError as e:",
    '    sys.exit(f"ADE: the link client could not be put in {home} ({type(e).__name__}); nothing was run.")',
    "ADE_CLIENT",
    setup,
  ].join("\n");
}
