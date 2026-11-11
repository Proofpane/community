"""Write vectors/composite/id-MLDSA65-Ed25519-SHA512.json from the text of draft-ietf-lamps-pq-composite-sigs-19.

    python3 oss/sign/tools/extract_composite_vector.py draft-ietf-lamps-pq-composite-sigs-19.txt

The draft's Appendix E prints its test vectors word-wrapped, with page footers and headers between the lines. This
strips those, joins each field's pieces, checks every field decodes as base64 to the length the draft gives it, and
records where the vector came from and the sha256 of the text it was taken from. The vector was made by the draft's
reference implementation, not by this package, which is what makes it a check on this package.
"""

import base64
import hashlib
import json
import os
import re
import sys

URL = "https://www.ietf.org/archive/id/draft-ietf-lamps-pq-composite-sigs-19.txt"
CASE = "id-MLDSA65-Ed25519-SHA512"
# Section 6.d's text of the IETF Trust's Legal Provisions Relating to IETF Documents, its year 2026, word for word: the
# vector carries it as its "legend"
LEGEND = ("Copyright (c) 2026 IETF Trust and the persons identified as authors of the code. All rights reserved. "
          "Redistribution and use in source and binary forms, with or without modification, is permitted pursuant to, "
          "and subject to the license terms contained in, the Revised BSD License set forth in Section 4.c of the IETF "
          "Trust’s Legal Provisions Relating to IETF Documents (http://trustee.ietf.org/license-info).")
LENGTHS = {"pk": 1984, "sk": 64, "s": 3373, "sWithContext": 3373}
OUT = os.path.join(os.path.dirname(__file__), "..", "vectors", "composite", CASE + ".json")


def _field(chunk, name):
    m = re.search(r'"%s":\s*"([^"]*)"' % re.escape(name), chunk)
    if not m:
        raise SystemExit(f"no {name!r} in the text")
    return re.sub(r"\s+", "", m.group(1))


def main(path):
    raw = open(path, "rb").read()
    lines = [ln for ln in raw.decode("utf-8").splitlines()
             if not ln.startswith("Ounsworth, et al.") and not ln.startswith("Internet-Draft ") and "\f" not in ln]
    body = "\n".join(lines)
    start = body.index('"m": "')
    case_at = body.index(f'"tcId": "{CASE}"')
    case = body[case_at:body.index('"tcId":', case_at + 10)]
    vector = {"source": "draft-ietf-lamps-pq-composite-sigs-19, Appendix E", "url": URL,
              "source_sha256": hashlib.sha256(raw).hexdigest(), "tcId": CASE, "legend": LEGEND,
              "m": _field(body[start:start + 2000], "m"), "ctx": _field(body[start:start + 2000], "ctx")}
    for name, length in LENGTHS.items():
        vector[name] = _field(case, name)
        got = len(base64.b64decode(vector[name], validate=True))
        if got != length:
            raise SystemExit(f"{name} decodes to {got} bytes, not {length}")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(vector, fh, indent=1)
        fh.write("\n")
    print(f"wrote {os.path.normpath(OUT)}")


if __name__ == "__main__":
    main(sys.argv[1])
