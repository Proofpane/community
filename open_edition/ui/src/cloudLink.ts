/** The block Linked sessions gives to link a session on claude.ai (CLOUD-LINK, queue 4.317), whose container holds no
 *  link client: pasted into that session whole, it checks that the session's python3 can import cryptography, fetches
 *  the client from the ADE's own address (the route proofpane_open/ade/link_client.py serves), checks it against the
 *  sha256 this page's build computed from open_edition/ade_link.py, puts it where SETUP_A.md installs it only on a
 *  match, then runs setup as the line for a session on this Mac does. The hash comes from this page's bundle, so a
 *  server that is not the ADE cannot change the code the block runs. Where the environment's proxy refuses the ADE's
 *  host, the block says the one setting that lets it through, the same sentence the link client's setup and pair say
 *  (ade_link.py HOST_REFUSED) and the page shows above the block. What the block's own steps print before setup runs
 *  is fixed words, an address the page wrote, the session's own home folder, a status code or an exception's type,
 *  never words a server or a proxy chose; setup prints what it prints for a session on this Mac. Every value goes into
 *  the program as a JSON string, which Python reads as the same text. tests/vectors/cloud_link_page.json keeps the
 *  block as the page gives it, which tests/test_cloud_link.py runs in a shell. */
import { ADE_URL, inForm } from "./shellForms";

// the build's word (vite.config.ts); none where a tool builds this file without it, and then no block is given
declare const __ADE_LINK_SHA256__: string | undefined;

/** The sha256 of open_edition/ade_link.py, as this page's build computed it; empty where the build gave none. */
export const CLIENT_SHA256: string = typeof __ADE_LINK_SHA256__ === "string" ? __ADE_LINK_SHA256__ : "";

/** Where the ADE serves its link client, with no sign-in (proofpane_open/ade/link_routes.py, GET /client). */
export const CLIENT_PATH = "/api/ade/link/client";

/** The one setting a claude.ai environment needs for the ADE at `host`: what the block and the link client say where
 *  the environment's proxy refuses that host, and what the page says above the block. */
export const hostRefused = (host: string) =>
  `A claude.ai environment whose network refuses ${host} needs it allowed: in the environment's settings, under Network `
  + `access, choose Limited and add ${host} to Allowed domains, keeping Allow package managers ticked; a running `
  + "session follows the change within about a minute.";

/** What the block says where the client the ADE served is not the one this page carries. */
export const CLIENT_CHANGED = "The link client the ADE served is not the one the page carries (its sha256 differs), so "
  + "nothing was installed or run: take the block again from the page the ADE itself serves, which carries the client "
  + "of its own release.";

/** What the block says where the session's python3 cannot import cryptography, which the link client needs. */
export const NO_CRYPTOGRAPHY = "This session's python3 cannot import cryptography, which the link client needs, so "
  + "nothing was fetched, installed or run: install it for that python3 with pip, which Allow package managers lets "
  + "through, then paste the block again.";

/** The block's first line, to the session it is pasted into: as SETUP_A.md's step 4 says, run in the background. */
const TO_THE_SESSION = "# To the Claude Code session it is pasted into: run this block as it is with the Bash tool, in the "
  + "background (it waits out the pairing's hold), and tell the person each line it prints that begins with ADE:, "
  + "word for word.";

/** The host of `url`, an address in ADE_URL's form, as a claude.ai environment's Allowed domains takes it: in lower
 *  case, its port left out, as the link client's own (Link.rp_id). Read from the text, which ADE_URL has checked. */
export const hostOf = (url: string) => url.replace(/^[a-z]+:\/\//, "").replace(/:[0-9]+$/, "").toLowerCase();

/** Whether `url` is the ADE on this machine's loopback (ADE_URL's http form), which no claude.ai environment's list of
 *  allowed domains reaches: no setting is said for it. */
export const onThisMachine = (url: string) => url.startsWith("http://");

/** The block for the ADE at `url` that installs the client and then runs `setup`, the line for the session as the
 *  page writes it. Null where the address is not in the form the server writes it, or the build gave no sha256. */
export function cloudBlock(url: string, setup: string): string | null {
  if (!inForm(ADE_URL, url) || !/^[0-9a-f]{64}$/.test(CLIENT_SHA256)) return null;
  const said = (text: string) => JSON.stringify(`ADE: ${text}`);
  return [
    TO_THE_SESSION,
    "python3 -I - <<'ADE_CLIENT' &&",
    "import hashlib, os, sys, urllib.error, urllib.request",
    "try:",
    "    import cryptography",
    "except ImportError:",
    `    sys.exit(${said(NO_CRYPTOGRAPHY)})`,
    `URL = ${JSON.stringify(url + CLIENT_PATH)}`,
    "try:",
    "    with urllib.request.urlopen(URL, timeout=60) as answer:",
    "        client = answer.read(16 * 1024 * 1024)",
    "except urllib.error.HTTPError as e:",
    '    sys.exit(f"ADE: no link client from {URL}: HTTP {e.code}; nothing was installed or run.")',
    "except Exception as e:",
    '    why = getattr(e, "reason", e)',
    '    if str(why).startswith("Tunnel connection failed: 403"):',
    `        sys.exit(${said(hostRefused(hostOf(url)))})`,
    '    sys.exit(f"ADE: no link client from {URL} ({type(why).__name__}); nothing was installed or run.")',
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
