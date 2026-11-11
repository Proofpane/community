"""One pseudo-terminal per session.

The program runs on the terminal's far side as the leader of its own session, with the terminal as its controlling
terminal, so Ctrl-C and a window resize reach it as they would in a terminal on the user's own machine. The child is
started through a separate interpreter that takes the terminal and then execs the program, rather than by forking the
agent, whose event loop and threads would otherwise be copied into it; and its exit is waited for on no thread of the
event loop's own pool, which the agent's requests run on (wait_for_exit).

Recent output is kept, up to BACKLOG bytes, so a browser that reconnects is shown the screen again; every viewer gets
the same bytes in the same order. Given a file to keep it in (`save`), a terminal also writes it there every
SAVE_EVERY seconds while it changes and once more when the program exits, with when it started and ended and its exit
status, so the screen outlives the agent: a workspace that slept, or an agent that restarted, shows each program's last
screen (SavedScreen) until it is started again. The file is the user's own, private, beside the session's record. A viewer that falls more than MAX_QUEUED chunks behind is dropped, so one slow
browser cannot hold the terminal's memory; it can reconnect and is shown the backlog.

The backlog a viewer is shown leaves out the questions a program asked its terminal (its colours, the cursor's
position, what the terminal is): a terminal answers what it is shown, and answers to questions asked minutes ago
would reach the program as keystrokes (GitHub's sign-in read them at its prompt and ended, 2026-09-28). A question
asked while a viewer is attached reaches that viewer as it is written, and is answered as usual.
"""

import asyncio
import fcntl
import json
import os
import re
import signal
import stat
import struct
import subprocess
import sys
import termios
import threading
import time

BACKLOG = 256 * 1024
READ = 64 * 1024
MAX_INPUT = 64 * 1024
MAX_QUEUED = 1024
MAX_COLS = 500
MAX_ROWS = 300
STOP_GRACE = 3.0
SAVE_EVERY = float(os.environ.get("PP_WORKSPACE_SCREEN_SAVE_EVERY", "5"))   # seconds between saves of a changed screen

# Run with `python -I -c`: take the terminal on stdin as the controlling terminal, then become the program.
_SHIM = "import os,sys,fcntl,termios; fcntl.ioctl(0, termios.TIOCSCTTY, 0); os.execvp(sys.argv[1], sys.argv[1:])"

# what a program writes to ask its terminal something; a terminal writes the answer back as input
QUERIES = re.compile(
    rb"\x1b\]\d+(?:;\d+)*;\?(?:\x07|\x1b\\)"   # a colour: OSC 10;? 11;? 12;? 4;N;?
    rb"|\x1b\[\??[56]n"                            # the device's status, the cursor's position (and DEC's form)
    rb"|\x1b\[[>=]?0?c"                             # device attributes: primary, secondary, tertiary
    rb"|\x1b\[>0?q"                                 # the terminal's name and version
    rb"|\x1b\[\?u"                                  # the keyboard protocol's flags
    rb"|\x1b\[\??\d+\$p"                            # a mode's state
    rb"|\x1b\[(?:1[4689]|2[01])t"                    # the window's and a cell's size, the titles
    rb"|\x1bP[+$]q[^\x1b]*\x1b\\"                    # a capability or a setting
)


def without_queries(data):
    """`data` less the questions to the terminal in it, which no one is waiting to hear answered any more."""
    return QUERIES.sub(b"", data)


EXITED = object()   # put on a viewer's queue when the program has exited
DROPPED = object()  # put on a viewer's queue when it fell too far behind and was dropped


class TerminalError(ValueError):
    pass


def check_size(cols, rows):
    if (not isinstance(cols, int) or isinstance(cols, bool) or not isinstance(rows, int) or isinstance(rows, bool)
            or not 1 <= cols <= MAX_COLS or not 1 <= rows <= MAX_ROWS):
        raise TerminalError(f"A terminal is 1 to {MAX_COLS} columns by 1 to {MAX_ROWS} rows.")
    return cols, rows


def _set_size(fd, cols, rows):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _settle(future, value):
    """`value` as `future`'s result, unless it has one already: cancelled, with the task that awaited it (its loop
    ending)."""
    if not future.done():
        future.set_result(value)


async def wait_for_exit(proc):
    """The exit code of `proc`, a program this process started (subprocess.Popen), once it has exited: waited for on no
    thread of the event loop's default pool. The workspace agent's requests run on that pool (asyncio.to_thread), and a
    program runs for as long as its session does: a thread of the pool held for each running program left a workspace
    machine, whose one CPU sized Python's pool at five, answering none of those requests once five programs ran.
    Where the system gives a process a descriptor (os.pidfd_open, Linux 5.3
    and later, a workspace machine's), the loop watches it, readable once the program has exited; elsewhere (a Mac), or
    where the system refuses one (a kernel without it, a sandbox's filter), a thread of the program's own waits for it
    and hands its code to the loop, unless the loop has closed meanwhile."""
    loop = asyncio.get_running_loop()
    exited = loop.create_future()
    pidfd_open = getattr(os, "pidfd_open", None)
    try:
        fd = pidfd_open(proc.pid) if pidfd_open is not None else None
    except OSError:
        fd = None
    if fd is not None:
        try:
            loop.add_reader(fd, _settle, exited, None)
        except Exception:                   # a loop that cannot watch it: the descriptor closed, a thread waits instead
            os.close(fd)
            fd = None
    if fd is not None:
        try:
            await exited
        finally:
            loop.remove_reader(fd)
            os.close(fd)
        return proc.wait()                  # it has exited: reaped at once, its code as Popen gives it

    def wait():
        code = proc.wait()
        try:
            loop.call_soon_threadsafe(_settle, exited, code)
        except RuntimeError:                # the loop closed meanwhile: nothing waits for the code any more
            pass

    threading.Thread(target=wait, name=f"pp-exit-{proc.pid}", daemon=True).start()
    return await exited


class Terminal:
    """A running program on a pseudo-terminal. Made inside the event loop that serves its viewers."""

    def __init__(self, argv, cwd, env, cols=100, rows=30, on_activity=None, save=None):
        cols, rows = check_size(cols, rows)
        self._on_activity = on_activity or (lambda: None)
        # when this run started, with its fraction (the map tells this run's agents from an earlier run's by it, their
        # sightings stamped in whole seconds), taken before the program is started, so that nothing the program does
        # is stamped before it; started_at, the whole second, is what the saved screen and
        # every other reader keep
        self.started = time.time()
        self.started_at = int(self.started)
        master, slave = os.openpty()
        try:
            _set_size(slave, cols, rows)
            self.proc = subprocess.Popen([sys.executable, "-I", "-c", _SHIM] + list(argv), stdin=slave, stdout=slave,
                                         stderr=slave, cwd=cwd, env=env, start_new_session=True, close_fds=True)
        except BaseException:
            os.close(master)
            raise
        finally:
            os.close(slave)
        os.set_blocking(master, False)
        self.fd = master
        self.backlog = bytearray()
        self.viewers = set()
        self.exit_code = None
        self.ended_at = None      # when the exit was seen, in seconds since the epoch
        self._loop = asyncio.get_running_loop()
        self._save_path = save
        self._changed = False
        self._loop.add_reader(master, self._readable)
        self._waiter = self._loop.create_task(self._wait())
        self._saver = self._loop.create_task(self._save_while_running()) if save else None

    @property
    def running(self):
        return self.exit_code is None

    def _readable(self):
        try:
            data = os.read(self.fd, READ)
        except BlockingIOError:
            return
        except OSError:           # EIO: every copy of the far side is closed
            data = b""
        if not data:
            self._loop.remove_reader(self.fd)
            return
        self._output(data)

    def _output(self, data):
        self._on_activity()
        self._changed = True
        self.backlog += data
        if len(self.backlog) > BACKLOG:
            del self.backlog[:len(self.backlog) - BACKLOG]
        for q in list(self.viewers):
            if q.qsize() >= MAX_QUEUED:
                self.viewers.discard(q)
                q.put_nowait(DROPPED)
            else:
                q.put_nowait(data)

    async def _wait(self):
        code = await wait_for_exit(self.proc)
        # what the program wrote just before it exited may still be in the terminal
        while True:
            try:
                data = os.read(self.fd, READ)
            except OSError:
                break
            if not data:
                break
            self._output(data)
        try:
            self._loop.remove_reader(self.fd)
        except (ValueError, OSError):
            pass
        os.close(self.fd)
        self.ended_at = int(time.time())
        self.exit_code = code
        if self._saver is not None:
            self._saver.cancel()
        self._save()
        for q in list(self.viewers):
            q.put_nowait(EXITED)
        self.viewers.clear()

    async def _save_while_running(self):
        while self.running:
            await asyncio.sleep(SAVE_EVERY)
            if self._changed and self.running:
                self._save()

    def _save(self):
        """Write the screen and what is known of the program to the terminal's file, whole or not at all; a failed
        write (a full disk) is let go, never the terminal."""
        if not self._save_path:
            return
        self._changed = False
        head = json.dumps({"started_at": self.started_at, "ended_at": self.ended_at, "exit_code": self.exit_code,
                           "saved_at": int(time.time())}).encode("utf-8")
        tmp = f"{self._save_path}.{os.getpid()}.tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(head + b"\n" + bytes(self.backlog))
            os.replace(tmp, self._save_path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def attach(self):
        """(the backlog, a queue of what comes after it). The queue yields bytes, then EXITED when the program exits,
        or DROPPED if this viewer fell too far behind."""
        q = asyncio.Queue()
        if self.running:
            self.viewers.add(q)
        else:
            q.put_nowait(EXITED)
        return without_queries(bytes(self.backlog)), q

    def detach(self, q):
        self.viewers.discard(q)

    async def write(self, data):
        if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_INPUT:
            raise TerminalError(f"Input comes in pieces of at most {MAX_INPUT // 1024} KB.")
        if not self.running:
            return
        self._on_activity()
        view = memoryview(bytes(data))
        waited = 0.0
        while view:
            try:
                n = os.write(self.fd, view)
                view = view[n:]
            except BlockingIOError:
                if waited > 5.0:
                    raise TerminalError("The program is not reading its input.")
                await asyncio.sleep(0.01)
                waited += 0.01
            except OSError:
                return

    def resize(self, cols, rows):
        cols, rows = check_size(cols, rows)
        if self.running:
            try:
                _set_size(self.fd, cols, rows)
            except OSError:
                pass

    async def stop(self):
        """Hang up, as closing a terminal window does, then kill what is left of the session after STOP_GRACE."""
        if not self.running:
            return
        for sig in (signal.SIGHUP, signal.SIGKILL):
            try:
                os.killpg(self.proc.pid, sig)
            except ProcessLookupError:
                break
            try:
                await asyncio.wait_for(asyncio.shield(self._waiter), STOP_GRACE)
                return
            except asyncio.TimeoutError:
                continue
        await self._waiter


class SavedScreen:
    """A program's last screen as its terminal saved it, read back after the agent restarted: shown to a viewer as an
    ended terminal (its output, then its exit), taking no input. None when there is no saved screen, or it cannot be
    read."""

    running = False

    def __init__(self, backlog, started_at, ended_at, exit_code):
        self.backlog = backlog
        self.started_at = started_at
        self.ended_at = ended_at
        self.exit_code = exit_code

    @staticmethod
    def _open(path):
        """The saved screen, opened to read, only as a regular file and never waiting on a FIFO, which a session's
        programs can put where it goes (they can write the session's folder): the list and the map read it every few
        seconds, on the agent's event loop. OSError otherwise."""
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOCTTY", 0))   # never made the agent's terminal
        # looked at before it is wrapped: wrapping a folder raises, and would leave the descriptor open
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise OSError(f"not a regular file: {path}")
        return os.fdopen(fd, "rb")

    @classmethod
    def load(cls, path):
        try:
            with cls._open(path) as fh:
                data = fh.read(BACKLOG + 4096)
        except OSError:
            return None
        head, sep, backlog = data.partition(b"\n")
        try:
            meta = json.loads(head) if sep else None
        except (ValueError, RecursionError):        # a first line nested too deep: Python 3.11 and older raise
            meta = None
        if not isinstance(meta, dict):
            return None
        whole = lambda k: meta.get(k) if isinstance(meta.get(k), int) and not isinstance(meta.get(k), bool) else None
        # a screen saved while the program ran, by an agent that then stopped without a word: it ended then, or after
        return cls(backlog[-BACKLOG:], whole("started_at"), whole("ended_at") or whole("saved_at"), whole("exit_code"))

    @staticmethod
    def meta(path):
        """What a saved screen says of its program ({started_at, ended_at, exit_code}), from its first line only, for a
        session's view; None when there is none."""
        try:
            with SavedScreen._open(path) as fh:
                head = fh.readline(4096)
        except OSError:
            return None
        try:
            meta = json.loads(head)
        except (ValueError, RecursionError):        # a first line nested too deep
            return None
        if not isinstance(meta, dict):
            return None
        whole = lambda k: meta.get(k) if isinstance(meta.get(k), int) and not isinstance(meta.get(k), bool) else None
        return {"started_at": whole("started_at"), "ended_at": whole("ended_at") or whole("saved_at"),
                "exit_code": whole("exit_code")}

    def attach(self):
        q = asyncio.Queue()
        q.put_nowait(EXITED)
        return without_queries(bytes(self.backlog)), q

    def detach(self, q):
        pass

    async def write(self, data):
        pass

    def resize(self, cols, rows):
        check_size(cols, rows)
