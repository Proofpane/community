"""The ports a session's app is served on, for its preview (the desktop app's design mode): TCP sockets listening on this
machine whose process works in the session's worktree, its current directory in it. That takes in a dev server however
it was started: by the agent in its terminal, by a chat's tool, or in one of the session's shells.

Read from `lsof` on macOS and from /proc on Linux, as the workspace's own user, so only that user's processes are seen;
nothing where neither can be read. Standard library only.
"""

import os
import re
import subprocess
import sys

LSOF = "/usr/sbin/lsof"
MOST = 20


def parse_lsof_listen(text):
    """`lsof -nP -iTCP -sTCP:LISTEN -Fpcn`: [(pid, command, address, port)]."""
    out, pid, command = [], None, ""
    for line in text.splitlines():
        if not line:
            continue
        kind, value = line[0], line[1:]
        if kind == "p":
            pid, command = int(value) if value.isdigit() else None, ""
        elif kind == "c":
            command = value
        elif kind == "n" and pid is not None:
            m = re.match(r"^(\*|\[[0-9a-fA-F:.]+\]|[0-9.]+|[\w.-]+):(\d+)$", value)
            if m:
                out.append((pid, command, m.group(1), int(m.group(2))))
    return out


def parse_lsof_cwd(text):
    """`lsof -a -d cwd -Fpn -p PIDS`: {pid: its current directory}."""
    out, pid = {}, None
    for line in text.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("n") and pid is not None:
            out[pid] = line[1:]
    return out


def parse_proc_net_tcp(text, v6=False):
    """/proc/net/tcp or tcp6: {socket inode: (address, port)} of the sockets that listen (state 0A)."""
    out = {}
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 10 or fields[3] != "0A":
            continue
        address, _, port = fields[1].rpartition(":")
        try:
            port = int(port, 16)
            raw = bytes.fromhex(address)
        except ValueError:
            continue
        if v6 and len(raw) == 16:
            words = [raw[i:i + 4][::-1] for i in range(0, 16, 4)]     # each 32-bit word is in host order
            host = "[" + ":".join(b"".join(words)[i:i + 2].hex() for i in range(0, 16, 2)) + "]"
        elif not v6 and len(raw) == 4:
            host = ".".join(str(b) for b in raw[::-1])
        else:
            continue
        out[fields[9]] = (host, port)
    return out


def _within(path, root):
    try:
        path = os.path.realpath(path)
    except OSError:
        return False
    return path == root or path.startswith(root + os.sep)


def _macos(root):
    try:
        listen = subprocess.run([LSOF, "-nP", "-iTCP", "-sTCP:LISTEN", "-Fpcn"], capture_output=True, text=True,
                                timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    found = parse_lsof_listen(listen)
    pids = sorted({pid for pid, *_ in found})
    if not pids:
        return []
    try:
        cwd = parse_lsof_cwd(subprocess.run([LSOF, "-a", "-d", "cwd", "-Fpn", "-p", ",".join(map(str, pids))],
                                            capture_output=True, text=True, timeout=10).stdout)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [f for f in found if f[0] in cwd and _within(cwd[f[0]], root)]


def _linux(root, proc="/proc"):
    sockets = {}
    for name, v6 in (("tcp", False), ("tcp6", True)):
        try:
            with open(os.path.join(proc, "net", name), encoding="ascii") as fh:
                sockets.update(parse_proc_net_tcp(fh.read(), v6))
        except OSError:
            pass
    if not sockets:
        return []
    found = []
    for entry in os.listdir(proc):
        if not entry.isdigit():
            continue
        base = os.path.join(proc, entry)
        try:
            if not _within(os.readlink(os.path.join(base, "cwd")), root):
                continue
            fds = os.listdir(os.path.join(base, "fd"))
        except OSError:
            continue
        command = ""
        try:
            with open(os.path.join(base, "comm"), encoding="utf-8", errors="replace") as fh:
                command = fh.read().strip()
        except OSError:
            pass
        for fd in fds:
            try:
                target = os.readlink(os.path.join(base, "fd", fd))
            except OSError:
                continue
            m = re.match(r"^socket:\[(\d+)\]$", target)
            if m and m.group(1) in sockets:
                found.append((int(entry), command) + sockets[m.group(1)])
    return found


def listening(worktree, system=sys.platform):
    """[{port, address, pid, command}] for the worktree's app, a port once, by port, at most MOST."""
    root = os.path.realpath(worktree)
    if system == "darwin":
        found = _macos(root)
    elif system.startswith("linux"):
        found = _linux(root)
    else:
        found = []
    seen, out = set(), []
    for pid, command, address, port in sorted(found, key=lambda f: (f[3], f[0])):
        if port in seen:
            continue
        seen.add(port)
        out.append({"port": port, "address": address, "pid": pid, "command": command[:40]})
    return out[:MOST]
