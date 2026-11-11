"""The secret and personal-data rules, and the one way text leaves: `dlp.safe_copy`.

Most of these pin leaks a first version had: a secret the scan flagged in the
description but the redactor missed once markdown was stripped, one written in underscores that the scan missed, the
body of a private key kept behind a redacted header, placeholder addresses that let real ones through."""

import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from verify_pr import dlp  # noqa: E402
from verify_pr.config import load_config  # noqa: E402
from verify_pr.resolvers import diff_no_pii, diff_no_secrets  # noqa: E402
from verify_pr.verify import verify  # noqa: E402

SECRETS = {
    "aws-access-key": "AKIAIOSFODNN7EXAMPLE",
    "github-token": "ghp_" + "A1b2C3d4" * 5,
    "slack-token": "xoxb-1234567890-abcdefghij",
    "stripe-live-key": "sk_live_" + "a1B2c3D4e5" * 3,
    "google-api-key": "AIza" + "B" * 35,
    "anthropic-key": "sk-ant-api03-" + "x" * 30,
    "openai-key": "sk-proj-" + "Q" * 40,
    "private-key-block": ("-----BEGIN RSA PRIVATE KEY-----"
                          "MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu"),
}
WRAPPERS = {"plain": "{}", "code": "`{}`", "bold": "**{}**", "italic_us": "_{}_", "italic_star": "*{}*"}
ADJACENT = {"none": ("", ""), "lead_word": ("a", ""), "trail_s": ("", "s")}
# Not recognised, by design: a letter glued to the front of a token whose alphabet has both cases cannot be told
# from part of a longer word. The AWS key's alphabet is upper case and digits, so a lower-case letter on either side
# does not hide it, and the Google key has a fixed length, so nothing after it does.
UNRECOGNISED = {(rule, "plain", "lead_word") for rule in SECRETS if rule not in ("private-key-block", "aws-access-key")}


def bundle(body="", title="", files=()):
    return {"repo": "o/r", "number": 1, "title": title, "body": body, "base_sha": "b" * 40, "head_sha": "a" * 40,
            "files": list(files), "check_runs": [], "reviews": [], "issues": {}}


def outcome(m, resolver):
    return [o for o in m["obligations"] if o["resolver"] == resolver][0]


class SecretsInTheDescription(unittest.TestCase):
    def test_a_flagged_secret_is_never_copied(self):
        # 8 rules x 5 markdown wrappings x 3 adjacencies: whatever the scan flags, the manifest does not carry
        cfg = load_config()
        flagged = set()
        for rule, secret in SECRETS.items():
            probe = secret if rule != "private-key-block" else secret[31:]
            for wn, wrap in WRAPPERS.items():
                for an, (pre, post) in ADJACENT.items():
                    m = verify(bundle(body=f"All tests pass with key {pre}{wrap.format(secret)}{post} set."), cfg)
                    text = json.dumps(m)
                    if outcome(m, "diff.no_secrets")["outcome"] == "fail":
                        flagged.add((rule, wn, an))
                        self.assertNotIn(probe, text, (rule, wn, an))
                        self.assertIn(dlp.WITHHELD_SECRET, text, (rule, wn, an))
        every = {(r, w, a) for r in SECRETS for w in WRAPPERS for a in ADJACENT}
        self.assertEqual(every - flagged, UNRECOGNISED)

    def test_underscores_do_not_hide_a_key_from_the_scan(self):
        m = verify(bundle(body="All tests pass with key _AKIAIOSFODNN7EXAMPLE_."), load_config())
        self.assertEqual(outcome(m, "diff.no_secrets")["outcome"], "fail")
        m = verify(bundle(body="All tests pass with key AKIA**IOSFODNN7EXAMPLE**."), load_config())
        self.assertEqual(outcome(m, "diff.no_secrets")["outcome"], "fail")

    def test_a_private_key_on_one_line_is_withheld_whole(self):
        body = ('All tests pass with the CI service account {"type": "service_account", "private_key": '
                '"-----BEGIN PRIVATE KEY-----\\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7\\n'
                '-----END PRIVATE KEY-----\\n"}')
        m = verify(bundle(body=body), load_config())
        self.assertNotIn("MIIEvQIBADANBgkq", json.dumps(m))
        self.assertEqual(outcome(m, "diff.no_secrets")["outcome"], "fail")


class PrivateKeyFingerprints(unittest.TestCase):
    KEY_A = ["-----BEGIN RSA PRIVATE KEY-----", "MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu",
             "-----END RSA PRIVATE KEY-----"]
    KEY_B = ["-----BEGIN RSA PRIVATE KEY-----", "MIIBOwIBAAJBAMn4KQs2QpX8PSxS1QeD0kJYyR5kA6T2pdvK3hXPzVvS7cU9q1bY",
             "-----END RSA PRIVATE KEY-----"]

    def patch(self, lines):
        return {"path": "keys/k.pem", "status": "added",
                "patch": f"@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{x}\n" for x in lines)}

    def test_a_key_written_in_backticks_has_one_fingerprint(self):
        lines = ["`" + self.KEY_A[0] + "`", self.KEY_A[1], "`" + self.KEY_A[2] + "`"]
        r = diff_no_secrets(bundle(files=[self.patch(lines)]), load_config())
        fps = {e["fingerprint"] for e in r["evidence"]}
        self.assertEqual(len(fps), 1, r)
        cfg = load_config(overrides={"suppress_secret_fingerprints": list(fps)})
        self.assertEqual(diff_no_secrets(bundle(files=[self.patch(lines)]), cfg)["outcome"], "pass")

    def test_a_key_on_one_line_with_markup_inside_has_one_fingerprint(self):
        line = self.KEY_A[0] + "`" + self.KEY_A[1] + "`" + self.KEY_A[2]  # the forms differ inside the block
        r = diff_no_secrets(bundle(files=[self.patch([line])]), load_config())
        self.assertEqual(len({e["fingerprint"] for e in r["evidence"]}), 1, r)

    def test_two_keys_on_one_line_each_have_their_own_fingerprint(self):
        fps = []
        for key in (self.KEY_A, self.KEY_B):
            line = '"private_key": "' + "\\n".join(key) + '\\n"'
            fps.append(diff_no_secrets(bundle(files=[self.patch([line])]), load_config())["evidence"][0]["fingerprint"])
        self.assertNotEqual(fps[0], fps[1])

    def test_one_suppression_covers_one_key(self):
        cfg = load_config()
        a = diff_no_secrets(bundle(files=[self.patch(self.KEY_A)]), cfg)
        b = diff_no_secrets(bundle(files=[self.patch(self.KEY_B)]), cfg)
        self.assertEqual((a["outcome"], b["outcome"]), ("fail", "fail"))
        self.assertNotEqual(a["evidence"][0]["fingerprint"], b["evidence"][0]["fingerprint"])
        cfg = load_config(overrides={"suppress_secret_fingerprints": [a["evidence"][0]["fingerprint"]]})
        self.assertEqual(diff_no_secrets(bundle(files=[self.patch(self.KEY_A)]), cfg)["outcome"], "pass")
        self.assertEqual(diff_no_secrets(bundle(files=[self.patch(self.KEY_B)]), cfg)["outcome"], "fail")


class PersonalData(unittest.TestCase):
    def test_placeholder_addresses(self):
        for address in ("bob@example.com", "a@mail.example.org", "x@users.noreply.github.com", "noreply@acme.io",
                        "someone@host.test"):
            self.assertTrue(dlp.placeholder_email(address), address)
        for address in ("jane.doe@anthropic.com", "bob@example.com.evil.org", "x.noreply@corp.io",
                        "jane@notexample.com"):
            self.assertFalse(dlp.placeholder_email(address), address)

    def test_each_rule_is_redacted_in_claim_text(self):
        cases = {"email": "jane.doe@corp.io", "card-number": "4111 1111 1111 1111", "us-ssn": "123-45-6789",
                 "phone": "+1 415 555 0100"}
        for rule, value in cases.items():
            text = dlp.safe_copy(f"All tests pass for {value} today.")
            self.assertEqual(text, f"All tests pass for [redacted:{rule}] today.", rule)

    def test_what_the_rules_do_not_take(self):
        for text in ("version 1.2.3.4567890123", "call (415) 555-0100", "id 4111111111111112", "on 2026-09-24"):
            self.assertEqual(dlp.safe_copy(text), text)

    def test_data_visible_only_once_markup_is_removed_withholds_the_text(self):
        # the address exists only in the stripped form, so it cannot be cut out of the text as written
        self.assertEqual(dlp.safe_copy("fixtures/jane@**corp**.io.eml"), dlp.WITHHELD_PII)

    def test_a_token_glued_on_after_markup_is_still_cut(self):
        # the source line shows the number on its own; the sentence taken from it glues a digit on
        self.assertEqual(dlp.safe_copy("ssn 123-45-67890 checked", sources=("ssn `123-45-6789`0 checked",)),
                         "ssn [redacted:us-ssn]0 checked")

    def test_a_value_the_source_shows_with_markup_in_it_is_still_cut(self):
        # backticks are allowed in an address's local part, so the source's match carries them; the text holds the
        # address without them, with a character glued on that stops the rule matching it there
        self.assertEqual(dlp.safe_copy("jane@corp.io5 test pass", sources=("jane`@corp.io`5 test pass",)),
                         "[redacted:email]5 test pass")

    def test_a_source_is_the_sentences_own_stretch_of_the_line(self):
        # extraction hands each sentence the stretch of the line it came from, so data in the next sentence is not in
        # this one's source, and data in its source that it cannot cut out withholds it
        from verify_pr.extract import extract
        claims, _ = extract("", "All tests pass. Card 4111 1111 1111 1111 is on file, no new dependencies.")
        self.assertEqual([c["source"] for c in claims],
                         ["All tests pass.", "Card 4111 1111 1111 1111 is on file, no new dependencies."])
        claims, _ = extract("", "**All tests pass.** Next.")  # markup at either end goes with the sentence
        self.assertEqual([c["source"] for c in claims], ["**All tests pass.**"])
        self.assertEqual(dlp.safe_copy(claims[0]["text"], (claims[0]["source"],)), "All tests pass.")
        self.assertEqual(dlp.safe_copy("All tests pass.", sources=("All tests pass for +1 415 555 0100",)),
                         dlp.WITHHELD_PII)
        self.assertEqual(dlp.safe_copy("All tests pass.", sources=("All tests pass. AKIAIOSFODNN7EXAMPLE",)),
                         dlp.WITHHELD_SECRET)

    def test_paths_are_scanned(self):
        files = [{"path": "fixtures/jane.doe@corp.io.eml", "status": "added", "patch": "@@ -0,0 +1 @@\n+x\n"}]
        self.assertEqual(diff_no_pii(bundle(files=files), load_config())["outcome"], "fail")


class EveryOccurrence(unittest.TestCase):
    """Every occurrence goes, even a second one written differently, which redaction by value would leave in place."""

    def test_each_occurrence_is_cut_however_it_is_written(self):
        for text in ("owner jane@corp.io, formerly **jane**@corp.io", "owner jane@corp.io and jane*_@corp.io",
                     "fixtures/jane@corp.io/`jane`@corp.io/x.txt"):
            out = dlp.safe_copy(text)
            self.assertNotIn("jane", out, text)

    def test_repeated_data_is_replaced_not_withheld(self):
        self.assertEqual(dlp.safe_copy("a jane@corp.io b jane@corp.io c"), "a [redacted:email] b [redacted:email] c")

    def test_forms_the_first_rules_missed(self):
        for text in ("ask jane@corp.io--she owns CI", "call +1 (415) 555-0100", "card 4111.1111.1111.1111",
                     "ssn 123 45 6789", "ask rene.dupont@corp.fr", "ask ren\u00e9.dupont@corp.fr", "o'brien@corp.io",
                     "jane*doe@corp.io", "jane@b\u00fccher.de"):
            out = dlp.safe_copy(text)
            self.assertTrue("[redacted:" in out or out == dlp.WITHHELD_PII, (text, out))
            for piece in ("jane", "dupont", "brien", "415", "4111", "6789"):
                self.assertNotIn(piece, out, (text, out))

    def test_a_google_key_followed_by_a_dash_is_found(self):
        key = "AIza" + "Sy" + "Dx9Kq2Lm7Np4Rt8Vw1Yz3Bc6Df0Gh5JkQ"[:33]
        self.assertEqual(len(key), 39)
        self.assertEqual(dlp.safe_copy(f"key-{key}-- see the runbook"), dlp.WITHHELD_SECRET)

    def test_markup_that_takes_two_passes_to_remove_does_not_hide_a_key(self):
        # the first pass removes the `_` and leaves `**`, which only a second pass removes
        self.assertEqual(dlp.safe_copy("AKIA*_*IOSFODNN7EXAMPLE"), dlp.WITHHELD_SECRET)

    def test_markup_a_renderer_would_join_does_not_hide_a_key(self):
        for text in ("AKIA*IOSFODNN7EXAMPLE*", "AKIA~~IOSFODNN7EXAMPLE~~", "AKIA<b>IOSFODNN7EXAMPLE</b>"):
            self.assertEqual(dlp.safe_copy(text), dlp.WITHHELD_SECRET, text)

    def test_the_third_form_is_taken_from_the_text_as_written(self):
        # removing inline markdown also takes the `_` after a `-`, which this key contains; only the bold in the
        # middle has to go for the key to show
        key = "AIza" + "Sy-_" + "Dx9Kq2Lm7Np4Rt8Vw1Yz3Bc6Df0Gh5J"[:31]
        self.assertEqual(len(key), 39)
        self.assertEqual(dlp.safe_copy(f"All tests pass with AI**za**{key[4:]} set."), dlp.WITHHELD_SECRET)

    def test_one_line(self):
        self.assertEqual(dlp.one_line("a\nb\x1b[31mc\r"), "a\\x0ab\\x1b[31mc\\x0d")
        self.assertEqual(len(dlp.one_line("x" * 10000)), 500)


class CardsMarkupAndKeyFingerprints(unittest.TestCase):
    """A card next to another digit group, markup of both kinds at once, the rescan after a replacement, one key's
    one fingerprint, and the cases beside them."""

    def test_a_card_next_to_another_digit_group_is_found(self):
        for text, card in (("Card: 4242 4242 4242 4242 123", "4242 4242 4242 4242"),
                           ("card 4111 1111 1111 1111 12/28", "4111 1111 1111 1111"),
                           ("card 4111111111111111 123", "4111111111111111"),
                           ("Invoice 1234 4111 1111 1111 1111", "4111 1111 1111 1111"),
                           ("card 4111 1111 1111 1111 exp 12/28", "4111 1111 1111 1111")):
            out = dlp.safe_copy(text)
            self.assertIn("[redacted:card-number]", out, text)
            self.assertNotIn(card, out, text)
        added = {"path": "o.txt", "status": "added", "patch": "@@ -0,0 +1 @@\n+Card: 4242 4242 4242 4242 123\n",
                 "additions": 1}
        self.assertEqual(diff_no_pii(bundle(files=[added]), load_config())["outcome"], "fail")

    def test_a_digit_run_with_no_card_in_it_is_left_alone(self):
        # no stretch written as a card is written passes the Luhn check in these
        for run in ("5260 1815 9083 0166 1318", "6091 3909 9603 0824 6281"):
            self.assertEqual(dlp.safe_copy(f"order {run} shipped"), f"order {run} shipped")

    def test_the_card_formats_are_found_wherever_they_sit(self):
        for text, card in (("amex 3782 822463 10005", "3782 822463 10005"),
                           ("diners 3056 930902 5904", "3056 930902 5904"),
                           ("visa 4111-1111-1111-1111", "4111-1111-1111-1111"),
                           ("ref 12345 4111 1111 1111 1111 ok", "4111 1111 1111 1111")):
            self.assertEqual(dlp.safe_copy(text), text.replace(card, "[redacted:card-number]"), text)

    def test_the_longest_card_stretch_is_taken(self):
        # a stretch that stays valid with the group after it takes that group too; one that does not stops before it
        self.assertEqual(dlp.safe_copy("amex 3782 822463 10005 1008"), "amex [redacted:card-number]")
        self.assertEqual(dlp.safe_copy("amex 3782 822463 10005 1003"), "amex [redacted:card-number] 1003")

    def test_a_first_group_other_than_four_digits_does_not_start_a_card(self):
        # 4222222222222 passes the Luhn check, and unbroken it is a card; written 5-4-4 it is not written as one
        self.assertEqual(dlp.safe_copy("ref 4222222222222 ok"), "ref [redacted:card-number] ok")
        self.assertEqual(dlp.safe_copy("ref 42222 2222 2222 ok"), "ref 42222 2222 2222 ok")

    def test_numbers_not_written_as_a_card_are_not_one(self):
        # each has a stretch of groups of four or more digits that passes the Luhn check; none is written as a card
        # is, a group of 4 followed by groups of 4 to 6, or one group of 13 to 19
        for text in ("14239017 975936327 899325578", "505041597 580966285 898243645",
                     "932156545 994208269 561861548"):
            self.assertEqual(dlp.safe_copy(text), text)
        # and numeric data of small groups, of which the first version flagged most lines
        for text in ("1.5 2.5 " * 20, "12 345 6 78 901 23 4 567 8 90 12 345 6 78", "2026-09-24 2026-09-25 2026-10-01"):
            self.assertEqual(dlp.pii_matches(text), [], text)

    def test_both_kinds_of_markup_at_once_are_a_form_of_their_own(self):
        k = "AKIA" + "IOSFODNN7EXAMPLE"
        text = f"All tests pass with {k[:4]}<b></b>_{k[4:]}_ set."
        self.assertIn(dlp.unmark(text), dlp.forms(text))
        self.assertEqual(outcome(verify(bundle(body=text), load_config()), "diff.no_secrets")["outcome"], "fail")
        for text, piece in (("for 123-45-678*___9 today", "678"), ("on +1 <i></i>415 _555 0100 today", "555")):
            self.assertNotIn(piece, dlp.safe_copy(text), text)

    def test_a_replacement_that_brings_a_token_together_withholds_the_text(self):
        # the tag between the halves is too long to be removed, until the addresses in it are replaced
        k = "AKIA" + "IOSFODNN7EXAMPLE"
        emails = " ".join(f"firstname.lastname{i}@corporation.io" for i in range(7))
        text = f"cc {k[:4]}<{emails}>{k[4:]} all tests pass"
        self.assertEqual(dlp.secret_matches(text), [])
        self.assertEqual(dlp.safe_copy(text), dlp.WITHHELD_SECRET)

    def test_one_key_has_one_fingerprint_whatever_markup_is_inside_it(self):
        body = PrivateKeyFingerprints.KEY_A[1]
        head, tail = PrivateKeyFingerprints.KEY_A[0], PrivateKeyFingerprints.KEY_A[2]
        cfg = load_config()

        def fingerprints(lines):
            added = {"path": "k.pem", "status": "added", "additions": len(lines),
                     "patch": f"@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{x}\n" for x in lines)}
            return {e["fingerprint"] for e in diff_no_secrets(bundle(files=[added]), cfg)["evidence"]}
        one_line = fingerprints([head + body + tail])
        self.assertEqual(len(one_line), 1)
        self.assertEqual(fingerprints([head + body[:20] + "*_" + body[20:] + tail]), one_line)
        several = fingerprints([head, body, tail])
        self.assertEqual(len(several), 1)
        # markup on a body line, not only on the header's, tags included
        self.assertEqual(fingerprints([head, "`" + body[:10] + "*" + body[10:] + "`", tail]), several)
        self.assertEqual(fingerprints([head, body[:10] + "<b></b>" + body[10:], tail]), several)

    def test_a_key_header_that_looks_like_a_tag_is_still_a_finding(self):
        # deleting tags to fingerprint the key deletes this header too; the match itself is fingerprinted instead
        added = {"path": "k.txt", "status": "added", "additions": 1,
                 "patch": "@@ -0,0 +1 @@\n+<-----BEGIN RSA PRIVATE KEY----->\n"}
        self.assertEqual(diff_no_secrets(bundle(files=[added]), load_config())["outcome"], "fail")

    def test_one_line_escapes_every_control_that_can_break_or_rewrite_a_log_line(self):
        self.assertEqual(dlp.one_line("a\x85b\u2028c\u2029d\x9be\x7f"), "a\\x85b\\u2028c\\u2029d\\x9be\\x7f")

    def test_a_phone_number_with_a_slash_is_found(self):
        self.assertEqual(dlp.safe_copy("ring +49 30/1234 5678 today"), "ring [redacted:phone] today")

    def test_unmark_removes_markdown_first(self):
        # in the other order, removing the `*` first leaves `a_b`, where the `_` is no longer markdown
        self.assertEqual(dlp.unmark("a*_b"), "ab")

    def test_a_tag_of_200_characters_does_not_hide_a_key(self):
        k = "AKIA" + "IOSFODNN7EXAMPLE"
        self.assertEqual(dlp.safe_copy(f"key {k[:4]}<{'x' * 200}>{k[4:]} set"), dlp.WITHHELD_SECRET)


class CardStretchesSeparatorsAndKeyBlocks(unittest.TestCase):
    """Every card stretch, not only the first; numbers no issuer starts with, decimal fractions and digits
    glued to letters; one kind of separator; a phone number after `_`; git remotes; key blocks in linear time."""

    def test_every_card_stretch_is_redacted_not_only_the_first(self):
        # 4000 0000 0000 4111 passes by chance and overlaps the card after it; taking only the first stretch left
        # the card's last twelve digits
        self.assertTrue(dlp.luhn("4000000000004111"))
        self.assertEqual(dlp.safe_copy("ref 4000 0000 0000 4111 1111 1111 1111 paid"),
                         "ref [redacted:card-number] paid")

    def test_a_number_no_card_issuer_starts_with_is_not_a_card(self):
        # each passes the Luhn check and is written as a card is
        for number in ("1000000000000008", "2000000000000006", "2100000000000005", "7000000000000005",
                       "8000000000000003", "9000000000000001", "1727222400001"):
            self.assertTrue(dlp.luhn(number), number)
            self.assertEqual(dlp.pii_matches(f"id {number} ok"), [], number)
        years = " ".join(str(y) for y in range(2016, 2026))
        self.assertEqual(dlp.safe_copy(f"years {years}"), f"years {years}")
        for number in ("2223003122003222", "4111111111111111", "5555555555554444", "6011111111111117",
                       "378282246310005", "3566002020360505", "30569309025904"):
            self.assertEqual(dlp.safe_copy(f"card {number} ok"), "card [redacted:card-number] ok", number)

    def test_a_decimal_fraction_is_not_a_card(self):
        for text in ("x = 0.4111111111111111", "4111111111111111.25", "[0.4111111111111111, 2.5]"):
            self.assertEqual(dlp.pii_matches(text), [], text)
        self.assertEqual(dlp.safe_copy("card 4111.1111.1111.1111"), "card [redacted:card-number]")
        self.assertEqual(dlp.safe_copy("card 4111111111111111."), "card [redacted:card-number].")

    def test_digits_glued_to_a_letter_or_digit_are_not_a_card(self):
        for text in ("sha a3f4111111111111111e", "x4111111111111111", "4111111111111111x", "\u00e94111111111111111"):
            self.assertEqual(dlp.pii_matches(text), [], text)
        self.assertEqual(dlp.safe_copy("card _4111111111111111_"), "card _[redacted:card-number]_")

    def test_a_card_has_at_most_nineteen_digits_in_one_group(self):
        self.assertTrue(dlp.luhn("40000000000000000002"))
        self.assertEqual(dlp.pii_matches("id 40000000000000000002 ok"), [])

    def test_the_groups_after_a_group_of_four_have_four_to_six_digits(self):
        # 4111111111111111 passes the Luhn check; written 4-3-3-3-3 or 4-7-5 it is not written as a card
        for text in ("ref 4111 111 111 111 111 ok", "ref 4111 1111111 11111 ok"):
            self.assertEqual(dlp.pii_matches(text), [], text)

    def test_a_card_is_written_with_one_kind_of_separator(self):
        self.assertEqual(dlp.pii_matches("ref 4111 1111-1111 1111"), [])
        self.assertEqual(dlp.safe_copy("ref 4111-1111-1111-1111"), "ref [redacted:card-number]")

    def test_a_phone_number_after_an_underscore_is_found(self):
        self.assertEqual(dlp.safe_copy("x_+1 (415) 555-0100_y"), "x_[redacted:phone]_y")
        for text in ("a+14155550100", "++14155550100", "4+14155550100", "\u00e9+14155550100"):
            self.assertEqual(dlp.pii_matches(text), [], text)

    def test_a_git_remote_is_not_an_address(self):
        for text in ("git@github.com:o/r.git", "ssh://git@github.com/o/r.git", "git+ssh://git@gitlab.com/o/r",
                     "url = `git@github.com:o/r.git`"):
            self.assertEqual(dlp.pii_matches(text), [], text)
        for text, out in (("mailto:jane@corp.io", "mailto:[redacted:email]"),
                          ("jane@corp.io: ping me", "[redacted:email]: ping me"),
                          ("ask jane@corp.io.", "ask [redacted:email]."),
                          ("path //jane@corp.io", "path [redacted:email]"),
                          ("ask jane@corp.io:", "ask [redacted:email]:")):
            self.assertEqual(dlp.safe_copy(text), out, text)

    def test_a_key_block_stops_where_another_key_begins(self):
        header, end = "-----BEGIN RSA PRIVATE KEY-----", "-----END RSA PRIVATE KEY-----"
        lines = list(enumerate([header, "MIIBOgIBAAJBAKj", header, "MIIBOgIBAAJBAKk", end], 1))
        self.assertEqual(dlp.key_blocks(lines, 0), [f"{header}\nMIIBOgIBAAJBAKj"])
        self.assertEqual(dlp.key_blocks(lines, 2), [f"{header}\nMIIBOgIBAAJBAKk\n{end}"])
        # an END marker is kept even when another header follows it on the same line
        lines = list(enumerate([header, "MIIBOgIBAAJBAKj", f"{end} {header}"], 1))
        self.assertEqual(dlp.key_blocks(lines, 0), [f"{header}\nMIIBOgIBAAJBAKj\n{end} {header}"])

    def test_many_key_headers_read_each_line_a_bounded_number_of_times(self):
        # the first version read up to 200 following lines for every header: 200 times the work of the patch
        from unittest import mock
        n = 1000
        lines = list(enumerate([f"-----BEGIN RSA PRIVATE KEY----- {'A' * 30}"] * n, 1))
        real, calls = dlp._pem_text, [0]

        def counted(text):
            calls[0] += 1
            return real(text)
        with mock.patch.object(dlp, "_pem_text", counted):
            for i in range(n):
                dlp.key_blocks(lines, i)
        self.assertLessEqual(calls[0], 2 * n)
        # many headers on one line read the lines after it once, not once each
        lines = list(enumerate(["-----BEGIN RSA PRIVATE KEY----- " * 50] + ["MIIBOgIBAAJBAKj"] * 100, 1))
        calls[0] = 0
        with mock.patch.object(dlp, "_pem_text", counted):
            blocks = dlp.key_blocks(lines, 0)
        self.assertEqual(len(blocks), 50)
        self.assertLessEqual(calls[0], 101)


def _best(run, times=3):
    """The shortest of a few runs: a busy machine's pause does not count."""
    best = None
    for _ in range(times):
        start = time.perf_counter()
        run()
        took = time.perf_counter() - start
        best = took if best is None else min(best, took)
    return best


class Cost(unittest.TestCase):
    # How a scan's time grows with its text, not how long it takes on this machine (a fixed budget failed on a slower
    # one): four times the text in about four times the time, as a linear scan takes, never eight (n^1.5) or sixteen
    # (quadratic); and a ceiling far above any linear run, for one that would not end.
    def assertGrowsLinearly(self, scan, small, large):
        a = _best(lambda: scan(small))
        b = _best(lambda: scan(large))
        self.assertLess(b, 60.0)
        self.assertLess(b / max(a, 1e-3), 8.0, f"{a:.3f}s, then {b:.3f}s for four times the text")

    def test_a_long_near_miss_costs_linear_time(self):
        self.assertGrowsLinearly(dlp.safe_copy, "a." * 50000, "a." * 200000)

    def test_many_matches_cost_linear_time(self):
        # 32,000 distinct addresses: searched one by one, as the first version did, this took tens of seconds
        def addresses(n):
            return " ".join(f"user{i}@corp.io" for i in range(n))
        small, large = addresses(8000), addresses(32000)
        for sources in (lambda t: (), lambda t: (t,)):
            self.assertGrowsLinearly(lambda t: dlp.safe_copy(t, sources(t)), small, large)
            self.assertNotIn("@corp.io", dlp.safe_copy(large, sources(large)))
        self.assertEqual(dlp.safe_copy(large, (large,)), dlp.WITHHELD_PII)  # a source with that many values withholds its text

    def test_a_source_with_a_few_values_still_has_them_cut(self):
        text = " ".join(f"user{i}@corp.io" for i in range(dlp.MAX_SOURCE_VALUES))
        self.assertNotIn(dlp.WITHHELD_PII, dlp.safe_copy(text, (text,)))


if __name__ == "__main__":
    unittest.main()
