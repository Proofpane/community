#!/bin/bash
# open_edition/ holds no Chinese or other CJK text (tests/test_published_texts.py reads it whole). This scans the
# files named, or else the current git worktree's changed and new files, and exits 1 if any holds CJK. Run it from
# inside the worktree whose changes it should scan, before each commit: `bash open_edition/scripts/cjk.sh`. A test
# that needs CJK builds it from code points.
if [ $# -gt 0 ]; then
  files=$(printf '%s\n' "$@")
else
  files=$({ git diff --name-only HEAD; git ls-files --others --exclude-standard; } | sort -u)
fi
printf '%s\n' "$files" | python3 -c '
import os, sys
bad_any = False
for f in (l.rstrip("\n") for l in sys.stdin):
    if not f or not os.path.isfile(f):
        continue
    raw = open(f, "rb").read()
    if b"\0" in raw:                 # a binary file: no text to hold CJK
        continue
    text = raw.decode("utf-8", "replace")
    bad = [i for i, c in enumerate(text) if 0x2E80 <= ord(c) <= 0x9FFF or 0xAC00 <= ord(c) <= 0xD7AF
           or 0xF900 <= ord(c) <= 0xFAFF or 0xFF00 <= ord(c) <= 0xFFEF or 0x20000 <= ord(c) <= 0x3FFFF]
    if bad:
        bad_any = True
        line = text.count("\n", 0, bad[0]) + 1
        print(f"CJK {f}: {len(bad)} character(s), first on line {line}")
    else:
        print(f"clean {f}")
sys.exit(1 if bad_any else 0)
'
