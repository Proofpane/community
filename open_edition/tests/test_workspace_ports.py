"""The ports a session's app is served on (proofpane_workspace/ports.py): read from lsof's and /proc's own output, and,
on this machine, a server started in a worktree found by its port while one started elsewhere is not."""

import os
import socket
import subprocess
import sys
import time

import pytest

from proofpane_workspace import ports

LSOF = """p1234
cnode
f23
n*:5173
f24
n127.0.0.1:3000
p5678
cPython
f3
n[::1]:8000
pnot-a-pid
n*:9
"""

TCP = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:0BB8 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 12345 1 0 100 0 0 10 0
   1: 0100007F:1F90 0100007F:C350 01 00000000:00000000 00:00000000 00000000  1000        0 23456 1 0 20 4 30 10 -1
   2: 00000000:1435 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 34567 1 0 100 0 0 10 0
"""

TCP6 = """  sl  local_address                         remote_address                        st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 00000000000000000000000001000000:1F40 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 45678 1 0 100 0 0 10 0
"""


def test_what_lsof_says_is_read():
    assert ports.parse_lsof_listen(LSOF) == [(1234, "node", "*", 5173), (1234, "node", "127.0.0.1", 3000),
                                             (5678, "Python", "[::1]", 8000)]
    assert ports.parse_lsof_cwd("p1234\nfcwd\nn/work/s1\np5678\nfcwd\nn/elsewhere\n") == {1234: "/work/s1",
                                                                                          5678: "/elsewhere"}


def test_what_proc_says_is_read():
    assert ports.parse_proc_net_tcp(TCP) == {"12345": ("127.0.0.1", 3000), "34567": ("0.0.0.0", 5173)}
    assert ports.parse_proc_net_tcp(TCP6, v6=True) == {"45678": ("[0000:0000:0000:0000:0000:0000:0000:0001]", 8000)}


@pytest.mark.skipif(not (sys.platform == "darwin" or sys.platform.startswith("linux")), reason="lsof or /proc")
def test_a_server_working_in_the_worktree_is_found_and_one_elsewhere_is_not(tmp_path):
    work, elsewhere = tmp_path / "work", tmp_path / "elsewhere"
    work.mkdir()
    elsewhere.mkdir()

    def serve(cwd):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        proc = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"], cwd=cwd,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 20
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                return proc, port
            except OSError:
                assert time.time() < deadline, "the server did not listen"
                time.sleep(0.1)

    inside, inside_port = serve(work)
    outside, outside_port = serve(elsewhere)
    try:
        found = ports.listening(str(work))
        assert [p["port"] for p in found] == [inside_port]
        assert found[0]["pid"] == inside.pid and found[0]["address"] in ("127.0.0.1", "[::ffff:127.0.0.1]")
        assert "ython" in found[0]["command"]
        assert [p["port"] for p in ports.listening(str(elsewhere))] == [outside_port]
        assert ports.listening(str(tmp_path / "nothing-here")) == []
    finally:
        for proc in (inside, outside):
            proc.terminate()
            proc.wait(10)
