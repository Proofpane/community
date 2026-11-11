"""Chain v2: the published vectors, a second implementation over them, append and recovery, and concurrent appends."""

import io
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from _paths import CORE, VECTORS
from proofpane_core import chain  # noqa: E402
from proofpane_core.canonical import CanonicalizationError, digest_bytes  # noqa: E402

CHAIN_VECTORS = os.path.join(VECTORS, "chain")


def _index():
    with open(os.path.join(CHAIN_VECTORS, "index.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _verify_vector(entry):
    with open(os.path.join(CHAIN_VECTORS, entry["file"]), "rb") as fh:
        return chain.verify(fh, entry["start_seq"], entry["start_prev"],
                            entry.get("max_line_bytes", chain.MAX_LINE_BYTES))


class Vectors(unittest.TestCase):
    def test_valid_vectors_verify_as_recorded(self):
        valid = [e for e in _index() if e["valid"]]
        self.assertGreaterEqual(len(valid), 5)
        for e in valid:
            r = _verify_vector(e)
            self.assertEqual((r["ok"], r["lines"], r["head"], r["next_seq"], r["problem"]),
                             (True, e["lines"], e["head"], e["next_seq"], None), e["file"])

    def test_invalid_vectors_fail_by_their_recorded_rule_and_line(self):
        invalid = [e for e in _index() if not e["valid"]]
        for e in invalid:
            r = _verify_vector(e)
            self.assertFalse(r["ok"], e["file"])
            self.assertTrue(r["problem"].startswith(f"[{e['rejected_by']}] line {e['line']}:"), (e["file"], r))
            self.assertEqual(r["lines"], e["line"] - 1, e["file"])

    def test_every_rule_has_a_vector(self):
        covered = {e["rejected_by"] for e in _index() if not e["valid"]}
        self.assertEqual(sorted(set(chain.CHAIN_RULES) - covered), [])
        self.assertLessEqual(covered, set(chain.CHAIN_RULES))

    def test_every_problem_names_a_declared_rule(self):
        for e in _index():
            problem = _verify_vector(e)["problem"]
            if problem:
                self.assertIn(problem[1:problem.index("]")], chain.CHAIN_RULES)

    def test_a_second_implementation_agrees(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed; tools/check_chain_vectors.mjs was not run")
        p = subprocess.run([node, os.path.join("tools", "check_chain_vectors.mjs")], cwd=CORE, capture_output=True,
                           text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


class Lines(unittest.TestCase):
    def test_a_line_is_its_canonical_form_with_its_own_digest(self):
        line = chain.make_line({"b": 1, "a": "x"}, 0, chain.GENESIS, "2026-09-24T00:00:00Z")
        data = chain.encode(line)
        self.assertTrue(data.startswith(b'{"event":{"a":"x","b":1},"hash":"sha256:'))
        self.assertTrue(data.endswith(b'"seq":0,"ts":"2026-09-24T00:00:00Z","v":2}\n'))
        self.assertEqual(line["hash"], chain.line_hash(line))
        self.assertEqual(chain.check_line(data[:-1], 0, chain.GENESIS), line)

    def test_make_line_refuses_what_verify_would_refuse(self):
        for bad in ([], "x", None):
            with self.assertRaisesRegex(chain.ChainError, r"^\[line-event\]"):
                chain.make_line(bad, 0, chain.GENESIS, "2026-09-24T00:00:00Z")
        for seq in (-1, True, 1.0):
            with self.assertRaisesRegex(chain.ChainError, r"^\[line-seq\]"):
                chain.make_line({}, seq, chain.GENESIS, "2026-09-24T00:00:00Z")
        with self.assertRaisesRegex(chain.ChainError, r"^\[line-prev-format\]"):
            chain.make_line({}, 0, "sha256:" + "0" * 63, "2026-09-24T00:00:00Z")
        for ts in ("2026-09-24 00:00:00Z", "2026-09-24T00:00:00", "2026-13-01T00:00:00Z", "2026-09-24T00:00:00.Z"):
            with self.assertRaisesRegex(chain.ChainError, r"^\[line-ts\]"):
                chain.make_line({}, 0, chain.GENESIS, ts)

    def test_the_line_limit_must_be_a_positive_integer(self):
        raw = chain.encode(chain.make_line({}, 0, chain.GENESIS, "2026-09-24T00:00:00Z"))[:-1]
        for bad in (0, -1, True, 1.5, float("inf")):
            with self.assertRaisesRegex(chain.ChainError, "^max_line_bytes", msg=bad):
                chain.verify(io.BytesIO(b""), max_line_bytes=bad)
            with self.assertRaisesRegex(chain.ChainError, "^max_line_bytes", msg=bad):
                chain.check_line(raw, max_line_bytes=bad)

    def test_a_problem_quotes_nothing_that_could_rewrite_a_terminal_line(self):
        line = chain.make_line({}, 0, chain.GENESIS, "2026-09-24T00:00:00Z")
        line["\rOK 1 lines, head " + "a" * 64 + "\x1b[8m"] = 1
        line["k" * 100000] = 1  # a long key too: the list of keys is cut, not only escaped
        r = chain.verify(io.BytesIO(json.dumps(line, sort_keys=True, separators=(",", ":")).encode() + b"\n"))
        self.assertTrue(r["problem"].startswith("[line-keys] line 1:"), r)
        self.assertNotIn("\r", r["problem"])
        self.assertNotIn("\x1b", r["problem"])
        self.assertLess(len(r["problem"]), 300)

    def test_a_problem_quotes_a_value_escaped_and_cut(self):
        base = chain.make_line({}, 0, chain.GENESIS, "2026-09-24T00:00:00Z")
        for key in ("seq", "ts"):
            line = dict(base, **{key: "\rOK\x1b[8m" + "9" * 100000})
            r = chain.verify(io.BytesIO(json.dumps(line, sort_keys=True, separators=(",", ":")).encode() + b"\n"))
            self.assertTrue(r["problem"].startswith(f"[line-{key}] line 1:"), r["problem"][:80])
            self.assertNotIn("\r", r["problem"])
            self.assertNotIn("\x1b", r["problem"])
            self.assertLess(len(r["problem"]), 300)

    def test_now_is_a_valid_ts(self):
        chain.make_line({}, 0, chain.GENESIS, chain.now())

    def test_an_event_too_deep_to_read_back_is_refused_when_written(self):
        event = {}
        for _ in range(70):
            event = {"a": event}
        with self.assertRaises((chain.ChainError, CanonicalizationError)):
            chain.encode(chain.make_line(event, 0, chain.GENESIS, "2026-09-24T00:00:00Z"))

    def test_verify_reads_a_long_line_in_bounded_memory(self):
        class Counting(io.BytesIO):
            read_bytes = 0

            def readline(self, size=-1):
                out = super().readline(size)
                Counting.read_bytes += len(out)
                return out

        stream = Counting(b"x" * (3 * chain.MAX_LINE_BYTES) + b"\n")
        r = chain.verify(stream)
        self.assertTrue(r["problem"].startswith("[line-size] line 1:"), r)
        self.assertLessEqual(Counting.read_bytes, chain.MAX_LINE_BYTES + 1)

    def test_a_window_verifies_only_from_the_point_it_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "spool.jsonl")
            lines = [chain.append(path, {"i": i}) for i in range(6)]
            with open(path, "rb") as fh:
                data = fh.read().split(b"\n")
            window = b"\n".join(data[3:])
            r = chain.verify(io.BytesIO(window), 3, lines[2]["hash"])
            self.assertEqual((r["ok"], r["lines"], r["head"], r["next_seq"]), (True, 3, lines[5]["hash"], 6))
            r = chain.verify(io.BytesIO(window))
            self.assertTrue(r["problem"].startswith("[line-seq] line 1:"), r)


class Append(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "spool.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def read(self):
        with open(self.path, "rb") as fh:
            return fh.read()

    def test_appends_make_a_chain_that_verifies(self):
        written = [chain.append(self.path, {"i": i, "tool": "Read"}) for i in range(5)]
        self.assertEqual(self.read(), b"".join(chain.encode(line) for line in written))
        r = chain.verify_file(self.path)
        self.assertEqual((r["ok"], r["lines"], r["head"], r["next_seq"]), (True, 5, written[-1]["hash"], 5))
        self.assertEqual([line["seq"] for line in written], list(range(5)))
        self.assertEqual(written[0]["prev"], chain.GENESIS)
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), oct(0o600))

    def test_an_empty_chain_verifies(self):
        open(self.path, "wb").close()
        self.assertEqual(chain.verify_file(self.path),
                         {"ok": True, "lines": 0, "head": chain.GENESIS, "next_seq": 0, "problem": None})

    def test_refusals_leave_the_file_unchanged(self):
        chain.append(self.path, {"i": 0})
        before = self.read()
        cases = [({"x": float("nan")}, CanonicalizationError), ({"x": 2 ** 60}, CanonicalizationError),
                 (["not", "an", "object"], chain.ChainError), ({"x": "y" * chain.MAX_LINE_BYTES}, chain.ChainError)]
        for event, error in cases:
            with self.assertRaises(error):
                chain.append(self.path, event)
            self.assertEqual(self.read(), before)

    def test_a_torn_tail_is_refused_until_recovered(self):
        chain.append(self.path, {"i": 0})
        with open(self.path, "ab") as fh:
            fh.write(b'{"event":{"i":1},"ha')
        with self.assertRaisesRegex(chain.ChainError, r"^\[line-newline\]"):
            chain.append(self.path, {"i": 1})
        line = chain.recover(self.path)
        torn = b'{"event":{"i":1},"ha'
        self.assertEqual(line["event"]["kind"], "chain.recovered")
        self.assertEqual(line["event"]["removed_bytes"], len(torn))
        self.assertEqual(line["event"]["removed_digest"], digest_bytes(torn))
        with open(os.path.join(self.tmp, line["event"]["moved_to"]), "rb") as fh:
            self.assertEqual(fh.read(), torn)
        self.assertEqual(line["event"]["moved_to"], chain.side_file_name(self.path, 1, line["prev"], torn))
        self.assertEqual([f for f in os.listdir(self.tmp) if f.endswith(chain.PENDING)], [])  # renamed once recorded
        chain.append(self.path, {"i": 1})
        r = chain.verify_file(self.path)
        self.assertEqual((r["ok"], r["lines"]), (True, 3))
        self.assertIsNone(chain.recover(self.path))

    def test_recovery_of_a_file_that_is_all_torn_starts_the_chain(self):
        with open(self.path, "wb") as fh:
            fh.write(b'{"eve')
        line = chain.recover(self.path)
        self.assertEqual((line["seq"], line["prev"]), (0, chain.GENESIS))
        self.assertTrue(chain.verify_file(self.path)["ok"])

    def pending(self, seq, prev, data):
        return os.path.join(self.tmp, chain.side_file_name(self.path, seq, prev, data) + chain.PENDING)

    def test_an_interrupted_recovery_can_run_again(self):
        first = chain.append(self.path, {"i": 0})
        torn = b'{"event":'
        with open(self.path, "ab") as fh:
            fh.write(torn)
        aside = self.pending(1, first["hash"], torn)
        with open(aside, "wb") as fh:  # the copy was made, then the process stopped before cutting
            fh.write(torn)
        self.assertEqual(chain.recover(self.path)["event"]["moved_to"], os.path.basename(aside)[:-len(chain.PENDING)])
        self.assertTrue(chain.verify_file(self.path)["ok"])
        self.assertFalse(os.path.exists(aside))

    def test_an_incomplete_copy_is_written_again_from_the_chain(self):
        # the copy's name records the digest of the torn bytes, which are still in the chain: anything else in it is
        # a copy cut short, or not a copy of them at all, so it is written again rather than trusted or refused
        torn = b'{"event":'
        for wrong in (torn[:3], torn + b"and more"):
            for f in os.listdir(self.tmp):
                os.remove(os.path.join(self.tmp, f))
            first = chain.append(self.path, {"i": 0})
            with open(self.path, "ab") as fh:
                fh.write(torn)
            aside = self.pending(1, first["hash"], torn)
            with open(aside, "wb") as fh:
                fh.write(wrong)
            line = chain.recover(self.path)
            with open(os.path.join(self.tmp, line["event"]["moved_to"]), "rb") as fh:
                self.assertEqual(fh.read(), torn, wrong)
            self.assertEqual((line["event"]["removed_digest"], chain.verify_file(self.path)["ok"]),
                             (digest_bytes(torn), True))

    def test_a_file_whose_name_only_begins_like_a_copy_is_ignored(self):
        first = chain.append(self.path, {"i": 0})
        stem = chain.side_file_name(self.path, 1, first["hash"], b"")[:-12] + "0" * 12
        for name in (stem + chain.PENDING + ".orig", stem + ".pendingx", stem + "0" + chain.PENDING):
            with open(os.path.join(self.tmp, name), "wb") as fh:
                fh.write(b"an editor's backup, say")
        line = chain.append(self.path, {"i": 1})
        self.assertEqual((line["seq"], line["event"]), (1, {"i": 1}))

    def rotate(self):
        os.rename(self.path, self.path + ".1")

    def test_a_copy_left_by_an_earlier_chain_at_the_same_path_is_not_recorded(self):
        # the first chain's recovery stopped after the cut: its copy waits for line 2 after that chain's line 1. A new
        # chain at the same path reaches line 2 after a different line 1, so the copy is not its own
        for i in range(2):
            chain.append(self.path, {"i": i})
        with open(self.path, "ab") as fh:
            fh.write(b'{"torn":')
        code = subprocess.run([sys.executable, "-c", _DIE_AT_FSYNC, CORE, self.path, "3"], timeout=60).returncode
        self.assertEqual(code, 9)
        waiting = [f for f in os.listdir(self.tmp) if f.endswith(chain.PENDING)]
        self.assertEqual(len(waiting), 1)
        self.rotate()
        for i in range(4):
            chain.append(self.path, {"new": i})
        events = [json.loads(x)["event"] for x in self.read().splitlines()]
        self.assertEqual(events, [{"new": i} for i in range(4)])
        self.assertTrue(os.path.exists(os.path.join(self.tmp, waiting[0])))

    def test_a_first_line_recovered_and_recorded_is_not_recorded_again_by_a_new_chain(self):
        # every chain starts from the same point, seq 0 after GENESIS, so the name alone cannot tell two first lines
        # apart; a copy is renamed once its record is on the chain, and only a pending copy is ever recorded
        with open(self.path, "wb") as fh:
            fh.write(b'{"eve')
        chain.recover(self.path)
        self.rotate()
        line = chain.append(self.path, {"i": 0})
        self.assertEqual((line["seq"], line["event"]), (0, {"i": 0}))

    def test_the_states_two_chains_share_at_seq_0_are_a_documented_limit(self):
        # every chain at a path starts from the same point, so a copy made for a first line names a point any of
        # them reaches. A recovery of a first line stopped anywhere before its rename, then the chain moved away
        # before any other call: the next chain at that path records the copy as its own; stopped before the cut, the
        # old chain still holds the torn bytes, and its own recovery records them again. Pinned so that a fix shows
        for at, left in ((1, b'{"eve'), (2, b'{"eve'), (3, b""), (4, None)):
            for f in os.listdir(self.tmp):
                os.remove(os.path.join(self.tmp, f))
            with open(self.path, "wb") as fh:
                fh.write(b'{"eve')
            code = subprocess.run([sys.executable, "-c", _DIE_AT_FSYNC, CORE, self.path, str(at)],
                                  timeout=60).returncode
            self.assertEqual(code, 9)
            moved = self.path + ".1"
            os.replace(self.path, moved)
            line = chain.append(self.path, {"i": 0})
            events = [json.loads(x)["event"] for x in self.read().splitlines()]
            self.assertEqual([e.get("kind") for e in events], ["chain.recovered", None], at)
            self.assertEqual(line["seq"], 1)
            with open(moved, "rb") as fh:
                old = fh.read()
            if left is not None:
                self.assertEqual(old, left, at)
            else:
                self.assertIn(b'"kind":"chain.recovered"', old)

    def test_a_copy_that_does_not_match_its_name_is_refused(self):
        first = chain.append(self.path, {"i": 0})
        stem = chain.side_file_name(self.path, 1, first["hash"], b"")[:-12] + "0" * 12
        with open(os.path.join(self.tmp, stem + chain.PENDING), "wb") as fh:
            fh.write(b"not those bytes")
        before = self.read()
        with self.assertRaisesRegex(chain.ChainError, r"^side file .* does not hold the bytes its name records"):
            chain.append(self.path, {"i": 1})
        self.assertEqual(self.read(), before)

    def test_a_recorded_copy_left_with_its_pending_name_is_renamed_by_the_next_call(self):
        for finish in (lambda: chain.append(self.path, {"i": 2}), lambda: chain.recover(self.path)):
            for f in os.listdir(self.tmp):
                os.remove(os.path.join(self.tmp, f))
            chain.append(self.path, {"i": 0})
            with open(self.path, "ab") as fh:
                fh.write(b'{"torn":')
            code = subprocess.run([sys.executable, "-c", _DIE_AT_FSYNC, CORE, self.path, "4"], timeout=60).returncode
            self.assertEqual(code, 9)
            record = json.loads(self.read().splitlines()[-1])["event"]
            self.assertTrue(os.path.exists(os.path.join(self.tmp, record["moved_to"] + chain.PENDING)))
            finish()
            self.assertTrue(os.path.exists(os.path.join(self.tmp, record["moved_to"])))
            self.assertEqual([f for f in os.listdir(self.tmp) if f.endswith(chain.PENDING)], [])
            kinds = [json.loads(x)["event"].get("kind") for x in self.read().splitlines()]
            self.assertEqual(kinds.count("chain.recovered"), 1)

    def test_a_pending_copy_that_does_not_hold_what_its_record_says_is_refused(self):
        chain.append(self.path, {"i": 0})
        with open(self.path, "ab") as fh:
            fh.write(b'{"torn":')
        code = subprocess.run([sys.executable, "-c", _DIE_AT_FSYNC, CORE, self.path, "4"], timeout=60).returncode
        self.assertEqual(code, 9)
        record = json.loads(self.read().splitlines()[-1])["event"]
        with open(os.path.join(self.tmp, record["moved_to"] + chain.PENDING), "wb") as fh:
            fh.write(b"something else")
        with self.assertRaisesRegex(chain.ChainError, r"^side file .* does not hold the bytes the last line records"):
            chain.append(self.path, {"i": 2})

    def test_a_record_naming_a_file_this_module_would_not_name_renames_nothing(self):
        first = chain.append(self.path, {"i": 0})
        outside = os.path.join(os.path.dirname(self.tmp), os.path.basename(self.tmp) + "-x")
        with open(outside + chain.PENDING, "wb") as fh:
            fh.write(b"x")
        self.addCleanup(os.remove, outside + chain.PENDING)
        for name in ("../" + os.path.basename(outside), "other.jsonl.torn-1-" + "0" * 12 + "-" + "0" * 12):
            line = chain.make_line({"kind": "chain.recovered", "removed_bytes": 1, "removed_digest": digest_bytes(b"x"),
                                    "moved_to": name}, 1, first["hash"], chain.now())
            with open(self.path, "ab") as fh:
                fh.write(chain.encode(line))
            chain.append(self.path, {"after": name})
            first = json.loads(self.read().splitlines()[-1])
        self.assertTrue(os.path.exists(outside + chain.PENDING))

    def test_a_last_line_that_does_not_check_is_not_appended_to_or_repaired(self):
        chain.append(self.path, {"i": 0})
        data = self.read().replace(b'"i":0', b'"i":1')  # its hash no longer matches
        with open(self.path, "wb") as fh:
            fh.write(data)
        for step in (lambda: chain.append(self.path, {"i": 2}), lambda: chain.recover(self.path)):
            with self.assertRaisesRegex(chain.ChainError, r"^\[line-hash\]"):
                step()
        self.assertEqual(self.read(), data)

    def test_a_recovery_cut_short_after_the_cut_is_recorded_by_the_next_call(self):
        # a process that dies between cutting the torn bytes and appending the record leaves a clean chain and an
        # unrecorded copy; whichever call comes next records it before anything else
        for finish in (lambda: chain.append(self.path, {"i": 1}), lambda: chain.recover(self.path)):
            for f in os.listdir(self.tmp):
                os.remove(os.path.join(self.tmp, f))
            chain.append(self.path, {"i": 0})
            torn = b'{"event":{"i":1},"ha'
            with open(self.path, "ab") as fh:
                fh.write(torn)
            code = subprocess.run([sys.executable, "-c", _DIE_AT_FSYNC, CORE, self.path, "3"], timeout=60).returncode
            self.assertEqual(code, 9)
            self.assertEqual(self.read().count(b"\n"), 1)  # cut, and nothing recorded yet
            self.assertTrue(self.read().endswith(b"\n"))
            finish()
            r = chain.verify_file(self.path)
            lines = [json.loads(x) for x in self.read().splitlines()]
            self.assertTrue(r["ok"], r)
            self.assertEqual(lines[1]["event"]["kind"], "chain.recovered")
            self.assertEqual(lines[1]["event"]["removed_digest"], digest_bytes(torn))
            with open(os.path.join(self.tmp, lines[1]["event"]["moved_to"]), "rb") as fh:
                self.assertEqual(fh.read(), torn)

    def test_a_recovery_killed_at_any_fsync_loses_nothing(self):
        # killed after the copy, after its directory entry, after the cut, after the record or after the rename: the
        # torn bytes are still in the chain or in a complete copy, and the next recover finishes the job, recording
        # them once, under the name the record gives
        torn = b'{"event":{"i":1},"ha'
        for at in (1, 2, 3, 4, 5):
            for f in os.listdir(self.tmp):
                os.remove(os.path.join(self.tmp, f))
            chain.append(self.path, {"i": 0})
            with open(self.path, "ab") as fh:
                fh.write(torn)
            code = subprocess.run([sys.executable, "-c", _DIE_AT_FSYNC, CORE, self.path, str(at)],
                                  timeout=60).returncode
            self.assertEqual(code, 9, at)
            chain.recover(self.path)
            chain.append(self.path, {"i": 2})
            self.assertTrue(chain.verify_file(self.path)["ok"], at)
            records = [json.loads(x)["event"] for x in self.read().splitlines()]
            records = [e for e in records if e.get("kind") == "chain.recovered"]
            self.assertEqual([e["removed_digest"] for e in records], [digest_bytes(torn)], at)
            with open(os.path.join(self.tmp, records[0]["moved_to"]), "rb") as fh:
                self.assertEqual(fh.read(), torn, at)
            self.assertEqual([f for f in os.listdir(self.tmp) if f.endswith(chain.PENDING)], [], at)

    def test_a_torn_tail_longer_than_a_line_is_still_found_whole(self):
        for i in range(3):
            chain.append(self.path, {"i": i})
        with open(self.path, "ab") as fh:
            fh.write(b"x" * (chain.MAX_LINE_BYTES + 10))
        line = chain.recover(self.path)
        self.assertEqual((line["seq"], line["event"]["removed_bytes"]), (3, chain.MAX_LINE_BYTES + 10))
        self.assertEqual(chain.verify_file(self.path)["lines"], 4)

    def test_a_new_file_s_directory_entry_is_made_durable_before_its_first_line(self):
        from unittest import mock
        calls = []
        with mock.patch.object(chain, "_fsync_dir", side_effect=lambda p: calls.append(os.path.exists(p) and
                                                                                         os.path.getsize(p))):
            chain.append(self.path, {"i": 0})
        self.assertEqual(calls, [0])  # called once, while the file was still empty
        with mock.patch.object(chain, "_fsync_dir", side_effect=OSError("EIO")):
            other = os.path.join(self.tmp, "other.jsonl")
            with self.assertRaises(OSError):
                chain.append(other, {"i": 0})
            self.assertEqual(os.path.getsize(other), 0)  # nothing written, so nothing to record twice on a retry

    def test_append_needs_only_the_last_line_to_check(self):
        # the documented limit: append does not re-verify the whole file, so an edited earlier line is found by verify
        chain.append(self.path, {"i": 0})
        chain.append(self.path, {"i": 1})
        lines = self.read().split(b"\n")
        edited = json.loads(lines[0])
        edited["event"]["i"] = 9
        edited["hash"] = chain.line_hash(edited)
        with open(self.path, "wb") as fh:
            fh.write(chain.encode(edited) + lines[1] + b"\n")
        chain.append(self.path, {"i": 2})
        self.assertTrue(chain.verify_file(self.path)["problem"].startswith("[line-prev] line 2:"))


# kills the process at the n-th fsync of a recovery (argv[3]): 1 after the copy, 2 after its directory entry, 3 after
# the cut, 4 after the record, 5 after the directory entry of the copy's rename
_DIE_AT_FSYNC = """
import os, sys
sys.path.insert(0, sys.argv[1])
from proofpane_core import chain
real, n, at = os.fsync, [0], int(sys.argv[3])
def fsync(fd):
    real(fd)
    n[0] += 1
    if n[0] == at:
        os._exit(9)
os.fsync = fsync
chain.recover(sys.argv[2])
"""


def _appender(path, worker, count):
    for i in range(count):
        chain.append(path, {"worker": worker, "i": i})


class Concurrency(unittest.TestCase):
    def test_several_processes_append_to_one_unbroken_chain(self):
        workers, count = 8, 40
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "spool.jsonl")
            ctx = multiprocessing.get_context("spawn")
            procs = [ctx.Process(target=_appender, args=(path, w, count)) for w in range(workers)]
            for p in procs:
                p.start()
            for p in procs:
                p.join(120)
                self.assertEqual(p.exitcode, 0)
            r = chain.verify_file(path)
            self.assertEqual((r["ok"], r["lines"]), (True, workers * count), r)
            with open(path, "rb") as fh:
                events = [json.loads(line)["event"] for line in fh]
            for w in range(workers):
                self.assertEqual([e["i"] for e in events if e["worker"] == w], list(range(count)))


if __name__ == "__main__":
    unittest.main()
