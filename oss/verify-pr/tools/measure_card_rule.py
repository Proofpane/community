"""How often the card rule takes numbers that are not cards for cards, and how many cards it finds, on generated
data. The README quotes what this prints under Python 3.11; the data comes from `random` with fixed seeds.

    python3 tools/measure_card_rule.py        (from oss/verify-pr)
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from verify_pr import dlp  # noqa: E402


def cards(text):
    return [m for rule, m in dlp.pii_matches(text) if rule == "card-number"]


def rate(name, items):
    hits = sum(1 for x in items if cards(x))
    print(f"{name:52s} {hits:6d} of {len(items)}")


def luhn_complete(prefix, length):
    while True:
        body = prefix + "".join(random.choice("0123456789") for _ in range(length - len(prefix) - 1))
        for d in "0123456789":
            if dlp.luhn(body + d):
                return body + d


def main():
    random.seed(1)
    n = 1000
    rate("floats (repr of random())", [repr(random.random()) for _ in range(n)])
    rate("13-digit millisecond timestamps (90 days from 2024-07-01)",
         [str(1719792000000 + random.randrange(90 * 86400 * 1000)) for _ in range(n)])
    rate("19-digit integers below 2^63", [str(random.randrange(10 ** 18, 2 ** 63)) for _ in range(n)])
    rate("strings of 40 hex characters", ["".join(random.choice("0123456789abcdef") for _ in range(40))
                                          for _ in range(20000)])
    rate("lines of six four-digit numbers", [" ".join(str(random.randrange(1000, 10000)) for _ in range(6))
                                            for _ in range(n)])
    random.seed(2)
    rate("lines of six years from 1990 to 2030", [" ".join(str(random.randrange(1990, 2031)) for _ in range(6))
                                                  for _ in range(n)])
    rate("lines of eight numbers of one to four digits",
         [" ".join(str(random.randrange(0, 10 ** random.randrange(1, 5))) for _ in range(8)) for _ in range(5000)])
    rate("lines of date and time (YYYY MM DD hh mm ss)",
         [f"{random.randrange(1990, 2031)} {random.randrange(1, 13):02d} {random.randrange(1, 29):02d} "
          f"{random.randrange(24):02d} {random.randrange(60):02d} {random.randrange(60):02d}" for _ in range(n)])
    rate("16-digit integers", [str(random.randrange(10 ** 15, 10 ** 16)) for _ in range(n)])
    rate("20-digit integers", [str(random.randrange(10 ** 19, 10 ** 20)) for _ in range(n)])
    rate("UUIDs without dashes", [f"{random.getrandbits(128):032x}" for _ in range(n)])
    rate("JSON arrays of four floats", ["[" + ", ".join(repr(random.uniform(-100, 100)) for _ in range(4)) + "]"
                                        for _ in range(n)])
    random.seed(3)
    found, total = 0, 0
    for prefix, length, groups in (("4", 16, (4, 4, 4, 4)), ("51", 16, (4, 4, 4, 4)), ("2221", 16, (4, 4, 4, 4)),
                                   ("34", 15, (4, 6, 5)), ("37", 15, (4, 6, 5)), ("6011", 16, (4, 4, 4, 4)),
                                   ("35", 16, (4, 4, 4, 4)), ("36", 14, (4, 6, 4)), ("62", 16, (4, 4, 4, 4)),
                                   ("4", 13, (13,)), ("4", 19, (19,))):
        for _ in range(100):
            number = luhn_complete(prefix, length)
            parts, i = [], 0
            for g in groups:
                parts.append(number[i:i + g])
                i += g
            for sep in (" ", "-", ".", ""):
                if sep == "" or len(parts) > 1:
                    total += 1
                    found += bool(cards(f"card {sep.join(parts)} on file"))
    print(f"{'card numbers written in card formats, found':52s} {found:6d} of {total}")


if __name__ == "__main__":
    main()
