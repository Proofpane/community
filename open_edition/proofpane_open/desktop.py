"""The desktop app's server (the manual, "The desktop app"): the open edition for the one
person at this machine, which the desktop shell (`desktop/`) starts as its child and stops.

    python -m proofpane_open.desktop [--data DIR] [--email EMAIL] [--open]

`--open` is the way to run it without the desktop shell: it opens the person's own browser at the address, signed in.
The address carries a sign-in, so it is not put on the browser's command line, which another user of the machine can
read while it runs: a page in DIR that only this user can read sends the browser there, and is deleted after 30
seconds. DIR is ~/.proofpane-community unless named.

- Its data is DIR, the app's data folder. The secret its sign-ins are signed with is made there on the first run
  (`token-secret`, readable by this user only) and read on later ones.
- One account, EMAIL, made on the first run with no password, so it cannot sign in by one: only with the token this
  gives the shell. It is the deployment's admin, which is what the `local` provider asks of an account it gives a
  workspace to.
- The workspace is the `local` provider's: sessions on this machine, as this machine's user, under its gate settings.
  It is started with the server, so the app opens ready to work; if it cannot start, the page says why as it
  would anywhere.
- It listens on 127.0.0.1 only, on a port the system picks, and once it does, prints one line of JSON to standard
  output, `{"server": "http://127.0.0.1:PORT", "token": TOKEN, "url": "http://127.0.0.1:PORT/app/#signin=TOKEN",
  "port": PORT}`: the shell gives the server and the token to the app's own page, which talks to it as it talks to a
  hosted server (the app's origin is one the server allows already, OPEN_EDITION_APP_ORIGINS), and `url` is the page
  this server serves itself, signed in, for a browser (`--open`). Another program on the machine can reach the port,
  but not without a token.
- Then, every few seconds, what needs the person, as the hosted site's notifier gathers it (ade/push.py `items_for`:
  an approval, a question on the session channel, a chat's question, an agent that says it waits), each new one once,
  as a line `{"notify": {"title", "body", "url"}}` for the shell to show as the system's own notification. What one
  says is what a Web Push one says, in the same fixed words (push.words): what kind of thing waits and where, never a
  name or what anyone wrote. An item that goes away and comes back is new again.
- `PATH` is the person's login shell's, asked for once: started from the Finder, an app gets a short one, without the
  folders npm and Homebrew put the agents in.
- It stops when its standard input closes (the shell ended, however it ended) or on SIGTERM or SIGINT, and stops the
  workspace with it.
- One server a data folder: it holds the folder's lock (`desktop.lock`) while it runs, and a second one refuses to
  start, since it would run a second workspace over the first one's sessions. The lock goes with the process that
  holds it, however that ends, and the workspace agent does not inherit it.

Its own log, and the server's, go to `desktop.log` in DIR; standard output carries only the line above. With
PROOFPANE_DESKTOP_ACCESS_LOG=1 the log also has each request's method, path and answer (never a token: the page
sends its sign-in in a header, and the terminal and chat sockets in their first message).
"""

import argparse
import json
import logging
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid

DEFAULT_EMAIL = "you@this.desktop"
SECRET_FILE = "token-secret"
LOCK_FILE = "desktop.lock"
LOG_FILE = "desktop.log"
NOTIFY_EVERY = 4.0
STOP_WAIT = 10.0            # for the notifier, once the server has stopped: the shell kills it after 25 seconds
PATH_MARK = "__PROOFPANE_PATH__"
# where the agents' installers put them, kept after the login shell's own PATH in case it could not be asked
USUAL_BINS = ("/opt/homebrew/bin", "/usr/local/bin", "~/.local/bin", "~/.npm-global/bin", "~/.bun/bin",
              "/usr/bin", "/bin", "/usr/sbin", "/sbin")


def token_secret(data):
    """The signing secret kept in DIR, made on the first run: 48 random bytes, as text, readable by this user only."""
    path = os.path.join(data, SECRET_FILE)
    try:
        with open(path, encoding="ascii") as fh:
            secret = fh.read().strip()
        if len(secret) >= 32:
            return secret
    except FileNotFoundError:
        pass
    secret = secrets.token_urlsafe(48)
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(secret)
    os.replace(tmp, path)
    return secret


def hold_lock(data):
    """The data folder's lock, held until this process ends, or None when another process holds it."""
    try:
        import fcntl
    except ImportError:          # not a POSIX system: the desktop app is built for macOS first
        return -1
    fd = os.open(os.path.join(data, LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o600)   # not inherited by children
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def login_path(environ, timeout=10.0):
    """The PATH the person's login shell has, with the usual folders after it; the given one when the shell cannot say.
    The shell runs interactive and as a login shell, as a terminal starts it, so its profile and rc files are read;
    what it prints besides is ignored, and one that does not answer in time is left."""
    shell = environ.get("SHELL") or "/bin/zsh"
    found = ""
    try:
        out = subprocess.run([shell, "-ilc", f'printf "\\n{PATH_MARK}%s\\n" "$PATH"'], capture_output=True, text=True,
                             timeout=timeout, stdin=subprocess.DEVNULL, env=dict(environ))
        for line in out.stdout.splitlines():
            if line.startswith(PATH_MARK):
                found = line[len(PATH_MARK):].strip()
    except (OSError, subprocess.SubprocessError, ValueError):
        found = ""
    home = environ.get("HOME") or os.path.expanduser("~")
    usual = [os.path.join(home, b[2:]) if b.startswith("~/") else b for b in USUAL_BINS]
    parts = []
    for part in (found or environ.get("PATH", "")).split(os.pathsep) + usual:
        if part and part not in parts:
            parts.append(part)
    return os.pathsep.join(parts)


def ensure_account(email):
    """The id of the account for EMAIL, made with no password if it is not there."""
    from . import auth, store
    email = auth.normalise_email(email)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if row is not None:
            return row["id"]
        user_id = uuid.uuid4().hex
        # an empty password hash matches no password (auth.check_password): this account signs in by its token only
        db.execute("INSERT INTO users (id, email, password_hash, created_at) VALUES (?,?,?,?)",
                   (user_id, email, "", int(time.time())))
        return user_id


def notify_round(user_id, seen, say):
    """One round of the desktop's notifier: `say` each item that needs the person and was not there last round. Returns
    the keys there now, which are the next round's `seen`; the same `seen` when they could not be read this round."""
    from .ade import push
    try:
        items = push.items_for(user_id, [], int(time.time()))
    except Exception:  # noqa: BLE001  a round that cannot read changes nothing; the next one tries again
        logging.getLogger(__name__).exception("what needs the person could not be read")
        return seen
    for key, message in items:
        if key not in seen:
            say({"notify": {"title": message.title, "body": message.body, "url": message.url}})
    return {key for key, _ in items}


def open_in_browser(data, url, remove_after=30.0):
    """Open the person's browser at `url` through a page in `data` only this user can read, removed after a while."""
    import webbrowser
    path = os.path.join(data, "open.html")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write('<!doctype html><meta charset="utf-8"><title>Proofpane Community</title>'
                 f"<script>location.replace({json.dumps(url)})</script>")

    def remove():
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass

    timer = threading.Timer(remove_after, remove)
    timer.daemon = True
    timer.start()
    webbrowser.open("file://" + path)
    return path


def _listen():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    sock.set_inheritable(False)
    return sock


def _stop_on_eof(server):
    """Stop the server when standard input closes: the shell that started this has ended.

    It reads the descriptor itself, never through sys.stdin: a thread waiting in sys.stdin's read holds that stream's
    lock, and the interpreter, closing sys.stdin as it exits after a SIGTERM or SIGINT, cannot take it and aborts
    (on a Mac, the system then says that Python quit unexpectedly)."""
    try:
        fd = sys.stdin.fileno()
    except (AttributeError, OSError, ValueError):       # no standard input to watch: SIGTERM and SIGINT still stop it
        return

    def watch():
        try:
            while os.read(fd, 4096):
                pass
        except OSError:
            pass
        server.should_exit = True
    threading.Thread(target=watch, name="desktop-stdin", daemon=True).start()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m proofpane_open.desktop")
    parser.add_argument("--data", default=os.path.join(os.path.expanduser("~"), ".proofpane-community"),
                        help="the app's data folder (default ~/.proofpane-community)")
    parser.add_argument("--email", default=DEFAULT_EMAIL, help="the one account's address")
    parser.add_argument("--open", action="store_true", help="open the person's own browser at it, signed in")
    args = parser.parse_args(argv)

    data = os.path.abspath(args.data)
    os.makedirs(data, mode=0o700, exist_ok=True)
    os.chmod(data, 0o700)
    if hold_lock(data) is None:
        print(f"Another Proofpane Community is running with the data folder {data}.", file=sys.stderr)
        return 3
    logging.basicConfig(filename=os.path.join(data, LOG_FILE), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    sock = _listen()
    port = sock.getsockname()[1]
    email = args.email.strip().lower()

    # the app reads its settings from the environment each time; these are set before it is imported, which checks them
    os.environ["PATH"] = login_path(os.environ)
    os.environ.update({"OPEN_EDITION_DATA": data, "OPEN_EDITION_TOKEN_SECRET": token_secret(data),
                       "OPEN_EDITION_PUBLIC_URL": f"http://127.0.0.1:{port}", "OPEN_EDITION_ADMINS": email,
                       "OPEN_EDITION_ADE": "local",
                       # the hosted site's Web Push notifier has no one to send to here: the shell shows them instead
                       "OPEN_EDITION_ADE_PUSH_EVERY": "0"})
    for name in ("OPEN_EDITION_ADE_LOCAL_REPOS", "FLY_APP_NAME"):
        os.environ.pop(name, None)

    import uvicorn
    from . import auth
    from .main import app

    user_id = ensure_account(email)
    access_log = os.environ.get("PROOFPANE_DESKTOP_ACCESS_LOG") == "1"
    # a socket message is at most 1 MiB, as on the hosted site (start.sh)
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=access_log, lifespan="on",
                                           ws_max_size=1024 * 1024))
    _stop_on_eof(server)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: setattr(server, "should_exit", True))
    stopped = threading.Event()          # set once the server has stopped: the notifier's wait ends at once

    def say(line):
        sys.stdout.write(json.dumps(line) + "\n")
        sys.stdout.flush()

    def announce():
        while not server.started and not server.should_exit:
            time.sleep(0.02)
        if server.started:
            from .ade import storage, workspaces
            try:
                workspaces.for_user(auth.user(user_id)).ensure(auth.user(user_id))
            except storage.NotHosted as e:      # its work lives elsewhere, as it chose: expected, so no traceback
                logging.getLogger(__name__).info("the workspace did not start with the app: %s", e)
            except Exception:  # noqa: BLE001  the page offers to start it, and says why it did not
                logging.getLogger(__name__).exception("the workspace did not start with the app")
            token = auth.issue_token(user_id)
            here = f"http://127.0.0.1:{port}"
            url = f"{here}/app/#signin={token}"
            say({"server": here, "token": token, "url": url, "port": port})
            if args.open:
                open_in_browser(data, url)
            seen = set()
            while not server.should_exit:
                seen = notify_round(user_id, seen, say)
                stopped.wait(NOTIFY_EVERY)

    announcer = threading.Thread(target=announce, name="desktop-announce", daemon=True)
    announcer.start()
    server.run(sockets=[sock])
    # the notifier writes to standard output, so it ends before the interpreter closes that: a thread in the middle of
    # a write holds the stream's lock, which the interpreter could not take as it exits
    server.should_exit = True
    stopped.set()
    announcer.join(STOP_WAIT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
