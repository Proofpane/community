"""Hash chain v2 for local evidence: the line format, a locked append, and verification.

A chain is a sequence of lines. Each line is the RFC 8785 form of one object followed by a single "\\n":

    {"event":{...},"hash":"sha256:<64 hex>","prev":"sha256:<64 hex>","seq":0,"ts":"2026-09-24T00:00:00.000000Z","v":2}

- `hash` is the sha256 of the RFC 8785 bytes of the same object without `hash`.
- `prev` is the previous line's `hash`; on the first line of a chain it is `GENESIS`.
- `seq` is 0 on the first line of a chain and goes up by one on every line.
- `event` is any JSON object RFC 8785 accepts. `ts` is when the line was written, in UTC. It is informational: order is
  `seq`, and a clock that went backwards does not invalidate a chain.

A stored line must be exactly its canonical form, so each line has one valid spelling and verification compares
bytes. Canonical JSON escapes every control character, so a line never contains a raw newline.

A window of a chain verifies on its own when the verifier is told the `seq` and `prev` its first line continues from
(`start_seq`, `start_prev`); an export states both.

What a chain shows, and what it does not: checked against a public key that reached the
verifier by another route, that the record was not altered after it was signed; checked alone, only that it is
internally consistent. Anyone who can write the file can rebuild the chain from any point, so a chain on the same
machine as the agent it records is not evidence against that agent. This module neither signs nor exports.

Every check has a rule id, listed in `CHAIN_RULES`. Verification stops at the first line that fails and reports it as
`[rule-id] line N: what`, N counting from 1; nothing after it is checked, because nothing after it can be trusted to
continue the chain.

Appends take an exclusive lock on the file (`fcntl.flock`, or `msvcrt.locking` on Windows), read the last line, write
the new one with O_APPEND and fsync it before releasing the lock, so several processes can append to one chain.
Local filesystems only: `flock` is not reliable over NFS. The Windows path has not been run.
"""

import datetime
import os
import re

from .canonical import CanonicalizationError, canonicalize, digest, digest_bytes, loads_strict, printable

VERSION = 2
GENESIS = "sha256:" + "0" * 64
MAX_LINE_BYTES = 1 << 20  # without the newline; an event larger than this belongs in a file the event names by digest

KEYS = ("event", "hash", "prev", "seq", "ts", "v")

CHAIN_RULES = (
    "line-size",         # longer than MAX_LINE_BYTES
    "line-newline",      # the last line has no newline: a torn write
    "line-utf8",         # not UTF-8
    "line-json",         # not JSON, a duplicate key, NaN, too deep, or a value RFC 8785 refuses
    "line-canonical",    # valid JSON, but not the canonical spelling of its value
    "line-object",       # not an object
    "line-keys",         # keys other than exactly KEYS
    "line-version",      # v is not 2
    "line-seq",          # seq is not the next number
    "line-ts",           # ts is not an RFC 3339 UTC time
    "line-event",        # event is not an object
    "line-prev-format",  # prev is not sha256:<64 hex>
    "line-prev",         # prev is not the previous line's hash
    "line-hash-format",  # hash is not sha256:<64 hex>
    "line-hash",         # hash is not the digest of the line without it
)

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_SIDE_TAG = re.compile(r"[0-9a-f]{12}")
PENDING = ".pending"  # the suffix of a copy whose record is not yet on the chain
_TS = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z")


class ChainError(ValueError):
    """A chain cannot be appended to, or a line cannot be built. The message starts with a rule id where one applies."""


def now():
    """The current UTC time in the format `ts` uses."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def line_hash(line):
    """The `hash` a line must carry: the digest of the line without `hash`."""
    return digest({k: v for k, v in line.items() if k != "hash"})


def make_line(event, seq, prev, ts):
    """The line that records `event` after a line with hash `prev` (GENESIS for the first), at position `seq`."""
    if not isinstance(event, dict):
        raise ChainError("[line-event] the event must be a JSON object")
    if not _is_seq(seq):
        raise ChainError("[line-seq] seq must be a non-negative integer")
    if not (isinstance(prev, str) and _DIGEST.fullmatch(prev)):
        raise ChainError("[line-prev-format] prev must be sha256:<64 hex>")
    if not _is_ts(ts):
        raise ChainError("[line-ts] ts must be an RFC 3339 UTC time ending in Z")
    line = {"v": VERSION, "seq": seq, "ts": ts, "event": event, "prev": prev}
    line["hash"] = line_hash(line)
    return line


def encode(line):
    """The stored bytes of a line: its RFC 8785 form and one newline. The bytes are checked as a verifier would
    check them before they are returned, so nothing this module writes can fail verification on its own."""
    data = canonicalize(line)
    check_line(data)
    return data + b"\n"


def _check_limit(max_line_bytes):
    if isinstance(max_line_bytes, bool) or not isinstance(max_line_bytes, int) or max_line_bytes < 1:
        raise ChainError("max_line_bytes must be a positive integer")


def _is_seq(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_ts(value):
    if not (isinstance(value, str) and _TS.fullmatch(value)):
        return False
    try:
        datetime.datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return False
    return True


def check_line(raw, expect_seq=None, expect_prev=None, max_line_bytes=MAX_LINE_BYTES):
    """Check one stored line, given without its newline. Returns the parsed line, or raises ChainError.

    With `expect_seq` and `expect_prev` it also checks the line continues the chain; without them it checks only that
    the line is well formed and carries its own digest, which is what an append needs of the last line it reads.
    `max_line_bytes` exists so a published vector can exercise the size rule without a megabyte file; verifiers use
    the default."""
    _check_limit(max_line_bytes)
    if len(raw) > max_line_bytes:
        raise ChainError(f"[line-size] {len(raw)} bytes, over the limit of {max_line_bytes}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ChainError(f"[line-utf8] {e.reason} at byte {e.start}") from None
    try:
        line = loads_strict(text)
    except (ValueError, RecursionError) as e:  # CanonicalizationError and JSONDecodeError are both ValueErrors
        raise ChainError(f"[line-json] {e}") from None
    if canonicalize(line) != raw:
        raise ChainError("[line-canonical] the line is not the RFC 8785 form of its value")
    if not isinstance(line, dict):
        raise ChainError("[line-object] a line must be a JSON object")
    if sorted(line) != list(KEYS):
        raise ChainError(f"[line-keys] keys must be exactly {', '.join(KEYS)}; found {printable(sorted(line), 200)}")
    if line["v"] != VERSION or isinstance(line["v"], bool):
        raise ChainError(f"[line-version] v must be {VERSION}")
    if not _is_seq(line["seq"]) or (expect_seq is not None and line["seq"] != expect_seq):
        want = "a non-negative integer" if expect_seq is None else str(expect_seq)
        raise ChainError(f"[line-seq] seq must be {want}; found {printable(line['seq'])}")
    if not _is_ts(line["ts"]):
        raise ChainError(f"[line-ts] not an RFC 3339 UTC time: {printable(line['ts'])}")
    if not isinstance(line["event"], dict):
        raise ChainError("[line-event] event must be a JSON object")
    if not (isinstance(line["prev"], str) and _DIGEST.fullmatch(line["prev"])):
        raise ChainError("[line-prev-format] prev must be sha256:<64 hex>")
    if expect_prev is not None and line["prev"] != expect_prev:
        raise ChainError(f"[line-prev] prev is {line['prev']}, the previous line's hash is {expect_prev}")
    if not (isinstance(line["hash"], str) and _DIGEST.fullmatch(line["hash"])):
        raise ChainError("[line-hash-format] hash must be sha256:<64 hex>")
    if line["hash"] != line_hash(line):
        raise ChainError("[line-hash] hash is not the digest of the line without it")
    return line


def verify(stream, start_seq=0, start_prev=GENESIS, max_line_bytes=MAX_LINE_BYTES):
    """Verify a chain read from a binary stream, line by line, in bounded memory.

    Returns a dict: `ok`; `lines`, the number of lines that verified; `head`, the hash of the last of them (or
    `start_prev` if none did); `next_seq`, the seq a line appended after them would carry; and `problem`, None or
    `[rule-id] line N: what` for the first line that failed."""
    if not _is_seq(start_seq) or not (isinstance(start_prev, str) and _DIGEST.fullmatch(start_prev)):
        raise ChainError("start_seq must be a non-negative integer and start_prev sha256:<64 hex>")
    _check_limit(max_line_bytes)
    seq, prev, n = start_seq, start_prev, 0
    while True:
        raw = stream.readline(max_line_bytes + 1)
        if not raw:
            return {"ok": True, "lines": n, "head": prev, "next_seq": seq, "problem": None}
        where = n + 1
        if not raw.endswith(b"\n"):
            if len(raw) > max_line_bytes:
                problem = f"[line-size] line {where}: over the limit of {max_line_bytes} bytes"
            else:
                problem = f"[line-newline] line {where}: {len(raw)} bytes with no newline, a torn write"
            return {"ok": False, "lines": n, "head": prev, "next_seq": seq, "problem": problem}
        try:
            line = check_line(raw[:-1], seq, prev, max_line_bytes)
        except ChainError as e:
            rule, _, what = str(e).partition("] ")
            return {"ok": False, "lines": n, "head": prev, "next_seq": seq, "problem": f"{rule}] line {where}: {what}"}
        seq, prev, n = seq + 1, line["hash"], n + 1


def verify_file(path, start_seq=0, start_prev=GENESIS, max_line_bytes=MAX_LINE_BYTES):
    with open(path, "rb") as fh:
        return verify(fh, start_seq, start_prev, max_line_bytes)


# ---- appending


if os.name == "nt":  # pragma: no cover - not run
    import msvcrt

    def _lock(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                return
            except OSError:
                continue  # LK_LOCK gives up after ten one-second tries; keep waiting

    def _unlock(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(fd):
        fcntl.flock(fd, fcntl.LOCK_EX)

    def _unlock(fd):
        fcntl.flock(fd, fcntl.LOCK_UN)


def _read_at(fd, offset, size):
    os.lseek(fd, offset, os.SEEK_SET)
    out = b""
    while len(out) < size:
        chunk = os.read(fd, size - len(out))
        if not chunk:
            break
        out += chunk
    return out


def _rfind_newline(fd, before, floor):
    """The offset of the last newline in [floor, before), or -1, reading backwards in blocks."""
    pos = before
    while pos > floor:
        step = min(65536, pos - floor)
        pos -= step
        i = _read_at(fd, pos, step).rfind(b"\n")
        if i >= 0:
            return pos + i
    return -1


def _tail(fd):
    """(start of the last complete line, end of its newline + 1, file size). Bytes after `end` are a torn write.

    The search for the last newline runs back to the start of the file if it has to, so a torn tail of any length
    is found as one; the search for the line before it stops after MAX_LINE_BYTES, and a line longer than that is
    returned whole enough for check_line to refuse it by size."""
    size = os.fstat(fd).st_size
    last_nl = _rfind_newline(fd, size, 0)
    if last_nl < 0:
        return 0, 0, size
    floor = max(0, last_nl - (MAX_LINE_BYTES + 1))
    prev_nl = _rfind_newline(fd, last_nl, floor)
    start = prev_nl + 1 if prev_nl >= 0 else floor
    return start, last_nl + 1, size


def _write_all(fd, data):
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def _fsync_dir(path):
    if os.name == "nt":  # pragma: no cover - directories cannot be opened for fsync on Windows
        return
    fd = os.open(os.path.dirname(os.path.abspath(path)) or ".", os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _last_line(fd):
    """(seq, prev) the next line must carry, and the last line (None for an empty file). Raises ChainError if the
    file does not end cleanly or its last line does not check."""
    start, end, size = _tail(fd)
    if end != size:
        raise ChainError(f"[line-newline] the file ends with {size - end} bytes after its last newline, a torn "
                         f"write; recover() moves them aside and records that it did")
    if size == 0:
        return 0, GENESIS, None
    line = check_line(_read_at(fd, start, end - 1 - start))
    return line["seq"] + 1, line["hash"], line


def side_file_name(path, seq, prev, data):
    """The name a recovery gives the copy of torn bytes `data`, which the line at `seq`, continuing from `prev`, will
    record: `<chain name>.torn-<seq>-<12 hex of prev>-<12 hex of the bytes' digest>`. A copy is first written under
    this name plus PENDING, and renamed to it once its record is on the chain. The seq and prev tie the copy to one
    point of one chain, so a copy another chain at the same path left is not recorded here, with one exception: every
    chain starts from the same point, so a copy made for a first line (seq 0) whose rename never happened can be
    recorded by the next chain at that path. The README lists when that happens."""
    return f"{os.path.basename(path)}.torn-{seq}-{prev[7:19]}-{digest_bytes(data)[7:19]}"


def _pending_copies(path, seq, prev):
    """The copies a recovery made for line `seq`, continuing from `prev`, whose record is not on the chain, as
    [(final name, bytes)]. A copy whose bytes do not match its name is refused."""
    directory = os.path.dirname(os.path.abspath(path))
    prefix = f"{os.path.basename(path)}.torn-{seq}-{prev[7:19]}-"
    found = []
    for name in sorted(os.listdir(directory)):
        tag = name[len(prefix):-len(PENDING)]
        if not (name.startswith(prefix) and name.endswith(PENDING) and _SIDE_TAG.fullmatch(tag)):
            continue
        with open(os.path.join(directory, name), "rb") as fh:
            data = fh.read()
        if digest_bytes(data)[7:19] != tag:
            raise ChainError(f"side file {printable(name)} does not hold the bytes its name records; move it aside "
                             "and run again")
        found.append((name[:-len(PENDING)], data))
    return found


def _rename_recorded(path, line):
    """If `line` records a recovery whose copy still has its pending name, because the process stopped between
    writing the record and renaming the copy, rename it now."""
    event = line.get("event") if line else None
    if not (isinstance(event, dict) and event.get("kind") == "chain.recovered"):
        return
    name = event.get("moved_to")
    base = re.escape(os.path.basename(path))
    if not (isinstance(name, str) and re.fullmatch(base + r"\.torn-[0-9]+-[0-9a-f]{12}-[0-9a-f]{12}", name)):
        return  # not a name this module gives: nothing of ours to finish
    final = os.path.join(os.path.dirname(os.path.abspath(path)), name)
    if os.path.exists(final) or not os.path.exists(final + PENDING):
        return
    with open(final + PENDING, "rb") as fh:
        if digest_bytes(fh.read()) != event.get("removed_digest"):
            raise ChainError(f"side file {printable(name + PENDING)} does not hold the bytes the last line records; "
                             "move it aside and run again")
    os.replace(final + PENDING, final)
    _fsync_dir(path)


def _record_pending(fd, path, seq, prev, ts):
    """Record, as the next lines, every copy made for the next line and not yet recorded (the recovery stopped after
    cutting the torn bytes and before appending the line that says so), renaming each once its record is synced.
    Returns (seq, prev, the last line written or None). Only copies named for the next line are looked for: if two
    copies wait for one line (a record's own write tore while a recovery was recording it) and the process stops
    after recording the first, the second stays pending, named for a line the chain has passed, and is not recorded."""
    last = None
    for name, data in _pending_copies(path, seq, prev):
        last = make_line({"kind": "chain.recovered", "removed_bytes": len(data), "removed_digest": digest_bytes(data),
                          "moved_to": name}, seq, prev, ts)
        _write_all(fd, encode(last))
        os.fsync(fd)
        seq, prev = seq + 1, last["hash"]
        _rename_recorded(path, last)
    return seq, prev, last


def append(path, event, ts=None):
    """Append `event` to the chain at `path`, creating it if absent, and return the line written.

    Takes an exclusive lock for the whole read-then-write, so concurrent appends from several processes produce one
    unbroken chain. Durable when it returns: a new file's directory entry is fsynced before its first line is
    written, and the line is fsynced before the lock is released. If a recovery was cut short after cutting a torn
    write, the line recording it is written first, and a recorded copy left with its pending name is renamed.
    Refuses, with ChainError, when the file ends in a torn write (only `recover` handles that) or its last line does
    not check; it does not verify the lines before that. Raises CanonicalizationError for an event RFC 8785 refuses.
    If it raises after writing, the line is in the file: a caller that retries records the event twice, as two lines
    of an unbroken chain."""
    ts = ts or now()
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        _lock(fd)
        try:
            if os.fstat(fd).st_size == 0:
                _fsync_dir(path)  # before anything is written, so a failure here leaves nothing to retry over
            seq, prev, last = _last_line(fd)
            _rename_recorded(path, last)
            seq, prev, _ = _record_pending(fd, path, seq, prev, ts)
            line = make_line(event, seq, prev, ts)
            _write_all(fd, encode(line))
            os.fsync(fd)
        finally:
            _unlock(fd)
    finally:
        os.close(fd)
    return line


def recover(path, ts=None):
    """Make a chain whose last write was torn appendable again, and put the repair on the chain.

    The bytes after the last newline are copied to `side_file_name(...) + PENDING`, fsynced with its directory entry,
    and only then cut from the chain. The repair is recorded as an event of kind `chain.recovered` carrying their
    length, digest and the copy's name, never their content, which may hold a secret; once the record is synced the
    copy is renamed to that name. The digest, and the 48 bits of it the name carries, are unsalted, so bytes that are
    few or predictable (a short code in a known place) can be found from them by trying the candidates: a chain
    shared without its side files still discloses that much. A recovery cut short is completed by the next call:
    before the cut, by the next `recover`, which writes the copy again from the bytes still in the chain (`append`
    refuses a file that ends in a torn write); after it, by the next `recover` or `append`, which records the copy,
    or renames it if its record is already the last line. Returns the last line it wrote, or None if there was nothing
    to do. A last complete line that does not check is not repaired here: nothing after a line that fails can be
    trusted, so the chain has to be closed and a new one started."""
    ts = ts or now()
    fd = os.open(path, os.O_RDWR | os.O_APPEND)
    try:
        _lock(fd)
        try:
            start, end, size = _tail(fd)
            last = None
            if end == 0:
                seq, prev = 0, GENESIS
            else:
                last = check_line(_read_at(fd, start, end - 1 - start))
                seq, prev = last["seq"] + 1, last["hash"]
            _rename_recorded(path, last)
            if end != size:
                torn = _read_at(fd, end, size - end)
                aside = os.path.join(os.path.dirname(os.path.abspath(path)),
                                     side_file_name(path, seq, prev, torn) + PENDING)
                # the name records the digest of these bytes, so an existing file of that name that holds anything
                # else is an incomplete copy of them, and is written again
                side = os.open(aside, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                try:
                    _write_all(side, torn)
                    os.fsync(side)
                finally:
                    os.close(side)
                _fsync_dir(aside)
                os.ftruncate(fd, end)
                os.fsync(fd)
            _, _, line = _record_pending(fd, path, seq, prev, ts)
            return line
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


__all__ = ["VERSION", "GENESIS", "MAX_LINE_BYTES", "KEYS", "CHAIN_RULES", "PENDING", "ChainError",
           "CanonicalizationError", "now", "line_hash", "make_line", "encode", "check_line", "verify", "verify_file",
           "side_file_name", "append", "recover"]
