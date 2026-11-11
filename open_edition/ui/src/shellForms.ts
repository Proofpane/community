/** The forms in which the server writes the values the page puts into a line the owner is told to run, each matched
 *  whole. A line built from a value in any other form is not given: the page in the desktop and phone apps is the
 *  app's own, which no server can change, so without this a server that is not the ADE (or one that was taken over)
 *  could put a command of its choosing into a line that is run as copied. */

/** The ADE's address in a pair or ask line: an https origin (a host of letters, digits, dots and dashes, and a port), or
 *  http on this machine's loopback, as the server's connector allows it (oauth.check_config). An address with a path,
 *  or with an IPv6 host, gives no line. */
export const ADE_URL = /^(?:https:\/\/[A-Za-z0-9.-]+|http:\/\/(?:localhost|127\.0\.0\.1))(?::[0-9]{1,5})?$/;

/** A ticket, as link.py makes it: PP- and eight base32 letters and digits. */
export const TICKET = /^PP-[A-Z2-7]{8}$/;

/** An account's ask address, as link_asks.py makes it. */
export const ASK_ADDRESS = /^ask-[a-z2-7]{20}$/;

/** A passkey's fingerprint, as passkeys.fingerprint writes it and the link client's --keys and --add take it. */
export const FINGERPRINT = /^[0-9a-f]{32}$/;

/** A link's name, as link.py takes it. */
export const LINK_NAME = /^[A-Za-z0-9][A-Za-z0-9 ._-]{0,59}$/;

/** Whether `value` is a string in `form`. */
export const inForm = (form: RegExp, value: unknown): value is string => typeof value === "string" && form.test(value);

/** The link client as SETUP_A.md installs it on a machine: in ~/.proofpane-ade, outside any working tree a session
 *  edits, run isolated (python -I), so a standard-library name dropped beside it is never imported in its place. Every
 *  line the page gives to run starts with it: one that named open_edition/ade_link.py ran whatever file of that name the
 *  folder it was run in holds, which in a session's own window is its repository's. */
export const CLIENT = `python3 -I "$HOME/.proofpane-ade/ade_link.py"`;
