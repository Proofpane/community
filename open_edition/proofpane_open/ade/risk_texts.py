"""The terms at each risk point of an agent's tasks: for each point, its text as the page opens it, the design's own
statements of the risk marked as the design's, the provider's clauses and the Privacy Act's principles it quotes word
for word with their sources, and a digest of all of it. Its route is ade/risk_routes.py, and the page opens the texts
through RiskTerms.tsx, which the teammate track's parts use too; the teammate track's routes return point 13's text
from text(13) with an offer and a custody request.

A text: {"point", "title", "design": [{"case", "text"}], "quotations": [{"id", "case",
"source", "section", "address", "source_date", "read_on", "words", "unread"}], "unread", "digest"}. A quotation not yet
read (the data-use clauses, and the Act's principles) has "words" null and "unread" the words
that say it was not read for this release and where it is, and its text's "unread" is true. The data-use clauses are
the two quotations of DATA_USE_IDS, which every text of DATA_USE_POINTS carries. A part whose "case" is not null is
shown only where the page names that case, one of the point's CASES. The seat-settings text, the design's, is one
text word for word the same in points 1 (case "onto_seat"), 3 (always), 4 (case "onto_seat"), 5 (case "onto_seat",
onto an Enterprise seat) and 6 (case "seat"), its first clause alone Claude Code's own description of its settings;
point 13's text carries point 1's clauses (case "other_group": at an offer's acceptance into an agent in another group,
and at every custody acceptance; with their quotations) and point 2's and point 7's texts (always). Point 16, the
accounts page's read of an account's identity by itself, quotes point 7's two clauses. Point 17, a workspace session
continued in a new one, carries point 2's clauses and point 1's under cases of its own, and the seat-settings text. The
words are the design's, and keep the placeholders of PLACEHOLDERS as written: "{operator}" and "{record_keeping}", which
texts() and text() fill, from operator() and from tasks.record_days() (its words: "for <n> days after the task is
deleted", "for 1 day ..." for one, or "for good"); "{owner}" and "{home_group}", which the page fills, and "{owner}"
which the teammate track's routes fill in `terms`. The digest is task_shapes.tagged_digest(TEXT_TAG, the RFC 8785 bytes,
proofpane_core.canonical.canonicalize, of the text without its "digest", the placeholders as written), so it changes
with the words, their sources and their dates, and not with a deployment's operator or keeping.

Four parts are worded here, not taken from the design's quotation marks, and are still served in "design", which the
page marks as the design's: the design's teammate notice in point 13, turned to the teammate who reads it there (its
words address the owner, and the notice is shown to both); the design's warning to the custodian, worded for every task
handed while custody stands; point 11's words for another vendor's key, whose content the design gives and not its
words; and the organisation notice, which leaves out the design's first sentence, since its two places, which way the
task goes, have no placeholder. Point 1's case "crossing" therefore adds nothing of its own: which way the task went is
on the crossing's move line (TaskMoveLine's "from" and "to"), for the page to show.

Nothing is acknowledged and nothing is kept of a pop-up: the texts open over the person's control
every time, and what the product did by itself opens at the owner's next visit, once for each event in a browser, by
the page's own bookkeeping in that browser's storage. Point 16's text is the one that holds something, the page's own
read of an account by itself, never a person's control: the page holds that read back until the text has opened in the
browser and the owner has closed it, by its own bookkeeping in that browser's storage too. No route knows of a pop-up,
so no call to the API waits on one but that read, which the page alone holds back.

Run as `python -m proofpane_open.ade.risk_texts --release-check YYYY-MM-DD`, the release's date: a release is not made
while it exits non-zero. The release check is not given the operator, a deployment's setting. Besides a text with a
clause not read, it names, on a line of its own, a text whose own words still hold the organisation notice's default
words: its mark, "not read again", or the 2025 announcement they rest on (ITEM_19_MARKS). So the release is held back
until those words are rewritten from the data-use clauses as read; whether the new words do come from the clauses is for
whoever reads them to see. A text's own "unread" stays as it is, by its quotations alone."""

import datetime
import os
import re
import sys
from typing import Optional

from .. import paths  # noqa: F401  (paths puts the open packages on sys.path)
from . import task_shapes as shapes
from proofpane_core.canonical import canonicalize

# the risk points, each a key of the texts: point 16 is the accounts page's read of an account by itself, and point
# 17 a session continued in a new one
RISK_POINTS = tuple(range(1, 18))
TEXT_TAG = b"proofpane-risk-text/1"
# the names, in braces, that a text's words keep for its filling: "operator" and "record_keeping" by texts() and text(),
# "owner" and "home_group" by the page ("owner" by the teammate track's routes too)
PLACEHOLDERS = ("owner", "operator", "home_group", "record_keeping")
# the conditional parts of a point's text, each shown only where the page names it
CASES = {1: ("crossing", "back_home", "back_home_off", "onto_seat"), 4: ("onto_seat",), 5: ("onto_seat",),
         6: ("seat",), 11: ("anthropic_key", "other_vendor"), 12: ("custody_cross",),
         13: ("offer", "custody", "adopted_check", "other_group"),
         17: ("other_account", "other_group", "onto_seat", "codex")}
# the texts that must carry the data-use clauses: the release check fails on one that lacks either
DATA_USE_POINTS = (1, 3, 4, 5, 12)
DATA_USE_IDS = ("data-use-consumer", "data-use-commercial")
OPERATOR_SETTING = "OPEN_EDITION_OPERATOR"

# ---- the quotations -------------------------------------------------------------------------------------------------
#
# Each as the design holds it: fetched read-only on 2026-10-10, the curly apostrophes made straight. Each is read again
# before the release and then carries that date (read_on), which the release check holds against the release's date. A
# clause not read has no words and says so, naming where it is; reading it means filling in its words, its section and
# the dates, and setting unread to None. Reading the data-use clauses also means setting the organisation notice's words
# from them, until which the release check names each text that holds its default words (ITEM_19_MARKS). Each quotation
# is pinned word for word, with its dates, in tests/test_risk_texts.py as the design holds it, and that test also holds
# the texts as built (six with a clause not read and the notice's default words, and the release check failing on them):
# a new reading changes those copies, and rewrites those tests, too.

READ_2026_10_10 = "2026-10-10"
CONSUMER_TERMS = "https://www.anthropic.com/legal/consumer-terms"
COMMERCIAL_TERMS = "https://www.anthropic.com/legal/commercial-terms"
CLAUDE_CODE_LEGAL = "https://code.claude.com/docs/en/legal-and-compliance"
USAGE_POLICY_IN_FORCE = "https://www.anthropic.com/legal/archive/22742366-2ef0-4c7a-a833-6523f10d3944"
USAGE_POLICY_LATER = "https://www.anthropic.com/legal/aup"
USAGE_CREDITS_ARTICLE = ("https://support.claude.com/en/articles/"
                         "11145838-using-claude-code-with-your-pro-or-max-plan")
# the Act on New Zealand's legislation site: an address of this module's own, not a document's, since the design did
# not fetch the Act's text and gives none, and not opened, since this build fetched nothing but the
# installs it needs; each principle's words say so, and claim nothing of the Act that the design does not give
PRIVACY_ACT = "https://www.legislation.govt.nz/act/public/2020/0031/latest/LMS23342.html"


def _quoted(source, section, address, source_date, words, read_on=READ_2026_10_10):
    return {"source": source, "section": section, "address": address, "source_date": source_date,
            "read_on": read_on, "words": words, "unread": None}


def _not_read(source, section, address, unread):
    return {"source": source, "section": section, "address": address, "source_date": None, "read_on": None,
            "words": None, "unread": unread}


def _principle(n):
    return _not_read(
        "Privacy Act 2020 (New Zealand)", f"information privacy principle {n}", PRIVACY_ACT,
        f"New Zealand's Privacy Act 2020, information privacy principle {n}, was not read for this release; it is in "
        f"the Act, at {PRIVACY_ACT}, an address not opened for this release.")


QUOTATIONS = {
    "automated-use": _quoted(
        "Consumer Terms of Service", "3", CONSUMER_TERMS, "Effective October 8, 2025",
        "Except when you are accessing our Services via an Anthropic API Key or where we otherwise explicitly permit "
        "it, to access the Services through automated or non-human means, whether through a bot, script, or "
        "otherwise."),
    "sharing": _quoted(
        "Consumer Terms of Service", "2", CONSUMER_TERMS, "Effective October 8, 2025",
        "You may not share your Account login information, Anthropic API key, or Account credentials with anyone "
        "else. You also may not make your Account available to anyone else."),
    "interference": _quoted(
        "Consumer Terms of Service", "3", CONSUMER_TERMS, "Effective October 8, 2025",
        "You also must not abuse, harm, interfere with, or disrupt our Services, including, for example, introducing "
        "viruses or malware, spamming or DDoSing Services, or bypassing any of our systems or protective measures."),
    "ordinary-use": _quoted(
        "Claude Code, Legal and compliance", None, CLAUDE_CODE_LEGAL, None,
        "Advertised usage limits for Pro and Max plans assume ordinary, individual usage of Claude Code and the Agent "
        "SDK."),
    "oauth": _quoted(
        "Claude Code, Legal and compliance", None, CLAUDE_CODE_LEGAL, None,
        "OAuth authentication is intended exclusively for purchasers of Claude Free, Pro, Max, Team, and Enterprise "
        "subscription plans and is designed to support ordinary use of Claude Code and other native Anthropic "
        "applications."),
    "own-subscription": _quoted(
        "Claude Code, Legal and compliance", None, CLAUDE_CODE_LEGAL, None,
        "Nor does it prevent an end user from signing in to the unmodified Claude Code binary with their own Claude "
        "subscription, including where a platform hosts Claude Code as described under Can customers offer Claude "
        "Code in their products? above."),
    # the Usage Policy's multiple-accounts clause, as in force when read and as posted to take effect later: both are
    # shown, each with its date
    "multiple-accounts-2025": _quoted(
        "Usage Policy", None, USAGE_POLICY_IN_FORCE, "Effective September 15, 2025",
        "Coordinate malicious activity across multiple accounts to avoid detection or circumvent product guardrails or "
        "generating identical or similar inputs that otherwise violate our Usage Policy"),
    "multiple-accounts-2026": _quoted(
        "Usage Policy", None, USAGE_POLICY_LATER, "Effective November 12, 2026",
        "Coordinate malicious activity across multiple accounts, or create multiple accounts, to avoid detection, "
        "circumvent product guardrails, or generate identical or similar inputs that otherwise violate our Usage "
        "Policy"),
    "bypass": _quoted(
        "Usage Policy", None, USAGE_POLICY_LATER, "Effective November 12, 2026",
        "Intentionally bypass or interfere with capabilities, restrictions, or guardrails established within our "
        "products, including by circumvention of the account sign-up or log-in process or by instructing the model to "
        "produce harmful outputs (e.g., jailbreaking or prompt injection), without prior authorization from "
        "Anthropic"),
    "responsibility": _quoted(
        "Commercial Terms of Service", "D.5", COMMERCIAL_TERMS, "Effective June 17, 2025",
        "Customer is responsible for all activity under its account."),
    "usage-credits": _quoted(
        "Help Center, Use Claude Code with your Pro or Max plan", None, USAGE_CREDITS_ARTICLE,
        "dateModified 2026-10-08T19:00:45Z",
        "Enable usage credits to keep using Claude with your Pro plan after you reach your five-hour session limit or "
        "your weekly limit."),
    # the data-use clauses: never read; a text that needs them says so, naming where they are
    "data-use-consumer": _not_read(
        "Consumer Terms of Service", None, CONSUMER_TERMS,
        "Anthropic's Consumer Terms of Service on whether this work may be used to train models were not read for this "
        f"release; they are at {CONSUMER_TERMS}."),
    "data-use-commercial": _not_read(
        "Commercial Terms of Service", None, COMMERCIAL_TERMS,
        "Anthropic's Commercial Terms of Service on whether this work may be used to train models were not read for "
        f"this release; they are at {COMMERCIAL_TERMS}."),
    # the Act's principles the teammate's notice rests on (3, telling a person what is collected; 6
    # and 7, seeing and correcting it; 9, keeping it no longer than its purpose needs), cited from the design's own
    # reading, to be read with the data-use terms
    "privacy-principle-3": _principle(3),
    "privacy-principle-6": _principle(6),
    "privacy-principle-7": _principle(7),
    "privacy-principle-9": _principle(9),
}

# ---- the design's own statements ------------------------------------------------------------------------------------

# the design has the organisation notice say what crossing onto a personal plan does to training in default words,
# "marked as not read again", until the data-use clauses are read and the notice's words are set from them. Those words
# rest on Anthropic's 2025 announcement: the release check names a text whose own words still hold either the mark or
# that announcement, so that taking the mark out alone does not let the release through. Matched without regard to
# case. Point 11's "whose terms were not read" (another vendor's, which no release reads) holds neither, and neither
# makes a text's "unread" true, which says only whether a quotation of it is unread.
NOT_READ_AGAIN = "not read again"
ITEM_19_MARKS = (NOT_READ_AGAIN, "announced in 2025")
# the design's organisation notice without its first sentence ("This task started on <...> and would go on <...>."),
# whose places say which way the task goes and have no placeholder; the clause it quotes
# is the quotation "responsibility"; and the notice's default words, with its mark (ITEM_19_MARKS)
ORGANISATION = (
    "Work on a Team or Enterprise seat goes into that organisation, under its agreement with Anthropic; work from such "
    "a seat taken to a personal account leaves it. On a Team seat, work falls under the organisation's commercial "
    "terms; on a personal plan, under the consumer terms, where (as Anthropic announced in 2025, not read again here) "
    "the account's own setting decides whether its chats may be used to train models. So a crossing onto a personal "
    "plan may put work started under the commercial terms under that setting.")
# the seat-settings text, word for word the same wherever it is shown
SEAT_SETTINGS = (
    "If this organisation has its own settings for Claude Code, Claude Code applies them to the work done on its seats "
    "in place of this workspace's own; that much is Claude Code's own description of its settings, not tested here. "
    "So their hooks run in your workspace, as you, and can read every account's sign-in here. This workspace's checks "
    "still judge a task's own calls, but in a hosted workspace an agent's own runs and chats on these seats run "
    "without them.")
# point 2's text: the design's words, then what the relay off
# means, since it is off unless the deployment sets it (a task
# with no known reset waits for its owner, and the relay is turned on by the task's own switch once the deployment
# sets it), then the design's last sentence
RELAY = (
    "Nothing of Anthropic's that was read allows using several of your own accounts in turn to go past one account's "
    "limits; whether a plan's limits are among the restrictions the Usage Policy of 12 November 2026 means is a "
    "reading, not its words. A task moves at most 10 times in an hour before it waits for you. The relay is off "
    "unless this deployment sets it on; with it off, a task whose account runs out of room waits for that account's "
    "reset, going on there by itself while Auto-resume is on, or waits for you where no reset is known, and you can "
    "move it with Continue on... or turn the relay on, with the task's own Relay switch once this deployment sets the "
    "relay on. Relaying a task across your accounts is your choice.")
UNWATCHED = ("Nothing of Anthropic's that was read explicitly permits a program to run Claude Code on your "
             "subscription while nobody is there.")
# point 17: a session continued in a new one, by the owner's press. Its
# own words always; under "other_account", a Claude account other than the session's, the design's relay notice
# worded for a session, with its clauses; under "other_group", the organisation notice, with point 1's clauses; under
# "onto_seat", the seat-settings text; under "codex", the design's Codex notice, in its words
HANDOVER = (
    "The new session starts from the commit this session's Finish makes, with your task and what the workspace observed "
    "of this session's work: the ids of its commits and the names of the files they changed. It gets none of this "
    "session's conversation; of what a program wrote, only those names, marked as data. A terminal program or a chat "
    "has no tool to say it read what it was given, as a task's attempt has, so the one check is that the new "
    "session's worktree is that commit.")
OTHER_ACCOUNT = (
    "Nothing of Anthropic's that was read allows using several of your own accounts in turn to go past one account's "
    "limits; whether a plan's limits are among the restrictions the Usage Policy of 12 November 2026 means is a "
    "reading, not its words. Continuing a session on another of your accounts is your choice.")
CODEX = ("OpenAI's terms of use and usage policies were not read for this: their pages refused the reading. Read them "
         "before relying on it.")
# the accounts page's read of an account's identity by itself, in a hosted workspace (point 16): the design's words,
# one text, whose last sentence is point 7's own
READ_BY_ITSELF = (
    "When a Claude Code account in this workspace signs in and is neither confirmed as yours nor waiting to join, this "
    "page starts that account's Claude Code once by itself, without your press, to read who it is: its email, its plan "
    "and its organisation. That start sends Claude no message, and runs in this workspace as any start of that account "
    "does. Nothing of Anthropic's that was read explicitly permits a program to run Claude Code on your subscription "
    "while nobody is there.")
# the design's teammate notice: its words, which address the owner (point 12), and the same said to the teammate
# who accepts (point 13), worded here, since the notice is shown to both and its words read to the teammate
# would put the task on the owner's agent
TEAMMATE_TO_OWNER = ("The task runs on your teammate's own agent and account, which they chose; it is their use of "
                     "their account, not yours.")
TEAMMATE_TO_TEAMMATE = ("The task runs on your own agent and account, which you choose; it is your use of your "
                        "account, not your teammate's.")
# the notice under the Privacy Act (point 13), after its first words: the same for an offer and
# for custody. The user's 90 days are bounded by the record's own deletion, which a deployment that keeps the record
# fewer days (tasks.record_days()) makes sooner
_NOTICE_REST = (
    "{owner}'s record of this task keeps your user in this deployment until 90 days after the task is deleted, when "
    "it becomes 'a teammate', or until this deployment deletes the record, if that is sooner; and it keeps the plan of "
    "the account your agent takes it on, with that account's organisation only where it is a Team or Enterprise "
    "organisation's seat, and when, for as long as this deployment keeps such records ({record_keeping}).")
_NOTICE_END = (
    "Your own record of the task you make by accepting keeps which of your accounts did each part, with their emails, "
    "as long as this deployment keeps records; you can delete it. {owner}'s record does not keep your email, but your "
    "user here does, and the commits your agent makes carry your email as their author and stay in {owner}'s "
    "repository. It is kept so that {owner} can say which accounts, under which terms, did the work on their code; "
    "{owner} and this deployment's operator can see it. Accepting is your choice; if you decline, or let it expire, "
    "nothing of yours is kept. {operator} holds it; you may ask them to see it, to correct it, or to have your user in "
    "it reduced now.")
NOTICE_OFFER = "If you accept, " + _NOTICE_REST + " " + _NOTICE_END
NOTICE_CUSTODY = ("If you accept custody, then for every task handed to you while it stands, "
                  + _NOTICE_REST.replace("{owner}'s record of this task", "{owner}'s record of that task", 1) + " "
                  + "{owner}'s custody record names you until 90 days after custody ends. " + _NOTICE_END)
# the design's warning to the custodian, worded for every task handed while custody stands (point 13): each one
# the owner confirmed for custody, whenever that was
CUSTODIAN = (
    "The tasks handed to you while custody stands, each one your teammate confirmed for custody, will run on the "
    "agents you choose to receive them, with their permissions, on your accounts but never on paid usage or your "
    "keys, while their own accounts are out of room. The workspace's gate matches names and is not a wall: accept "
    "custody only from someone you trust, and give those agents the narrowest allow rules they need.")

_ORGANISATION_QUOTED = ("responsibility",) + DATA_USE_IDS
_RELAY_QUOTED = ("ordinary-use", "multiple-accounts-2025", "multiple-accounts-2026", "bypass", "interference")
_UNWATCHED_QUOTED = ("automated-use", "oauth")
_PRINCIPLES = ("privacy-principle-3", "privacy-principle-6", "privacy-principle-7", "privacy-principle-9")


def _q(ids, case=None):
    return [(i, case) for i in ids]


# Each point: its title, its design parts as (case, words) and its quotations as (id, case), in the order the page
# shows them.
POINTS = {
    # a crossing by itself (case "crossing") adds no words of its own: the design's sentence that says which way has
    # no placeholder, and which way is on the crossing's move line (TaskMoveLine's "from" and "to"),
    # for the page to show
    1: {"title": "Crossing between your personal plans and an organisation's seats",
        "design": [("back_home", "This task started on {home_group} and goes back there, now that an account there "
                                 "has room again."),
                   ("back_home_off", "This task started on {home_group} and goes back there, since crossing was "
                                     "turned off while it was away; it may wait there until an account has room."),
                   (None, ORGANISATION),
                   ("onto_seat", SEAT_SETTINGS)],
        "quotations": _q(_ORGANISATION_QUOTED)},
    2: {"title": "Relaying a task across your own accounts",
        "design": [(None, RELAY)],
        "quotations": _q(_RELAY_QUOTED)},
    3: {"title": "Letting a task cross organisations",
        "design": [(None, "With this on, when no account of this task's own group has room, the relay may take it from "
                          "your personal plans onto the seats of one Team organisation, which it is then bound to, or "
                          "from those seats onto your personal plans, and back home once an account there has room. It "
                          "never takes the task into a second organisation, nor into or out of an Enterprise "
                          "organisation's seats. After you press it onto another organisation's seats, or onto or off "
                          "an Enterprise seat, it crosses nowhere by itself, whatever this switch says, nor do custody "
                          "and the custodian's relay take it into another group."),
                   (None, ORGANISATION),
                   (None, SEAT_SETTINGS)],
        "quotations": _q(_ORGANISATION_QUOTED)},
    4: {"title": "Continuing on an account in another group",
        # its first clause says "where the relay is on"
        "design": [(None, "A task bound to no organisation yet becomes bound to this one, and may still cross between "
                          "your personal plans and its seats by itself where the relay is on; a task bound to another "
                          "organisation already crosses nowhere by itself after this, and neither custody nor the "
                          "custodian's relay takes it into another group. Onto your personal plans, a task keeps the "
                          "organisation it is bound to."),
                   (None, ORGANISATION),
                   ("onto_seat", SEAT_SETTINGS)],
        "quotations": _q(_ORGANISATION_QUOTED)},
    5: {"title": "Continuing onto or off an Enterprise seat",
        "design": [(None, "An Enterprise organisation may have an agreement of its own with Anthropic, which this text "
                          "cannot quote, and nothing here was measured on an Enterprise seat. After this the task "
                          "crosses nowhere by itself, and neither custody nor the custodian's relay takes it into "
                          "another group."),
                   (None, ORGANISATION),
                   ("onto_seat", SEAT_SETTINGS)],
        "quotations": _q(_ORGANISATION_QUOTED)},
    6: {"title": "Adding a Claude Code account",
        "design": [(None, "Use only your own accounts here, never a colleague's. The workspace cannot tell whose "
                          "account a folder holds. Code a task runs here, a repository's own settings among it, runs "
                          "as you, outside the workspace's checks, which judge only the task's own calls, and can read "
                          "every account's sign-in in this workspace. This deployment keeps the record of which of "
                          "your accounts did each task's work {record_keeping}."),
                   ("seat", SEAT_SETTINGS)],
        "quotations": _q(("sharing", "ordinary-use", "own-subscription")) + _q(("responsibility",), "seat")},
    7: {"title": "Work while nobody watches",
        "design": [(None, UNWATCHED)],
        "quotations": _q(_UNWATCHED_QUOTED)},
    8: {"title": "An account with usage credits",
        "design": [(None, "If usage credits are on for this account, work past its limit may be billed before the "
                          "relay can move on.")],
        "quotations": _q(("usage-credits",))},
    9: {"title": "Using an account to its limit",
        "design": [(None, "Unattended work may then use this account's week to its limit, which claude.ai and your own "
                          "Claude Code share.")],
        "quotations": _q(("ordinary-use",))},
    10: {"title": "Anthropic's API key in the workspace",
         "design": [(None, "A key kept in the workspace is exposed to runs: the gate refuses calls that name the key "
                           "store, and it is not a sandbox. Set a spending limit for this key at Anthropic.")],
         "quotations": []},
    # Anthropic's own key has no notice of its own: its case shows the cap's words alone. For
    # another vendor's key, the design gives what the words say, not the words: worded here, its last
    # clause as the cap's own words put the key's spending limit
    11: {"title": "Continuing on an API key",
         "design": [(None, "The cap counts Claude Code's estimate, and the attempt stops at the end of the turn that "
                           "passes it, so one turn can go over; the key's own spending limit at its provider is the "
                           "hard one."),
                    ("other_vendor", "The task's code and text go to that vendor, whose terms were not read for this. "
                                     "Claude Code's estimate of cost prices by Anthropic's models, so for another "
                                     "vendor it is a guess, and that vendor's own spending limit is the one to "
                                     "trust.")],
         "quotations": []},
    12: {"title": "Handing a task to a teammate",
         "design": [(None, TEAMMATE_TO_OWNER),
                    (None, "Your code goes to your teammate's agent and to an account of theirs, under that account's "
                           "terms; it stays in the group they take it on, unless you ticked custody's crossing. Who "
                           "took it, and on which plan and organisation, is kept in this task's record."),
                    ("custody_cross", ORGANISATION)],
         "quotations": _q(("sharing",)) + _q(_ORGANISATION_QUOTED, "custody_cross")},
    13: {"title": "Accepting a task from a teammate",
         "design": [(None, TEAMMATE_TO_TEAMMATE),
                    ("custody", CUSTODIAN),
                    (None, UNWATCHED),
                    (None, RELAY),
                    ("other_group", ORGANISATION),
                    ("adopted_check", "This command runs in your workspace, as your user, with the network and outside "
                                      "the gate, as your own check."),
                    ("offer", NOTICE_OFFER),
                    ("custody", NOTICE_CUSTODY)],
         "quotations": (_q(("sharing",)) + _q(_UNWATCHED_QUOTED) + _q(_RELAY_QUOTED)
                        + _q(_ORGANISATION_QUOTED, "other_group") + _q(_PRINCIPLES))},
    14: {"title": "A task under the local provider",
         "design": [(None, "On this machine an agent's runs could reach the secret its records are signed with, so the "
                           "record of what a task did holds against an agent that follows the rules, not against code "
                           "written to forge it.")],
         "quotations": []},
    15: {"title": "Deleting a task's account record",
         "design": [(None, "This deletes, for good, the record of which accounts did this task's work. The task's "
                           "journal names the same accounts until 90 days after the task is deleted; after that, "
                           "nothing in the app keeps them.")],
         "quotations": []},
    # its clauses are point 7's, as a program starting Claude Code on the subscription without a press is the work that
    # point names
    16: {"title": "Reading who an account is, by itself",
         "design": [(None, READ_BY_ITSELF)],
         "quotations": _q(_UNWATCHED_QUOTED)},
    17: {"title": "Continuing a session in a new one",
         "design": [(None, HANDOVER),
                    ("other_account", OTHER_ACCOUNT),
                    ("other_group", ORGANISATION),
                    ("onto_seat", SEAT_SETTINGS),
                    ("codex", CODEX)],
         "quotations": _q(_RELAY_QUOTED, "other_account") + _q(_ORGANISATION_QUOTED, "other_group")},
}

# ---- the texts ------------------------------------------------------------------------------------------------------

_FILLED = re.compile(r"\{(operator|record_keeping)\}")
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def operator() -> Optional[str]:
    """OPERATOR_SETTING read from the environment at each call, its only reader: the operator's name and contact as one
    line, spaces stripped; None while it is unset or holds nothing but white space, when offers and custody are off
    (task_offers.enabled calls this)."""
    line = " ".join(os.environ.get(OPERATOR_SETTING, "").split())
    return line or None


def _raw(point):
    """Point `point`'s text with its placeholders as written and no digest: what the digest is made over, and what the
    release check reads. Fresh each time, so a caller may change what it is given: a quotation's fields are text or
    None, so a new dict of them is a copy (the copy module is not used in the server, tests/test_push_words.py)."""
    spec = POINTS[point]
    quotations = [dict({"id": qid, "case": case}, **QUOTATIONS[qid]) for qid, case in spec["quotations"]]
    design = [{"case": case, "text": words} for case, words in spec["design"]]
    return {"point": point, "title": spec["title"], "design": design, "quotations": quotations,
            "unread": any(q["unread"] is not None for q in quotations)}


def _item_19_marks(design) -> list:
    """The marks of ITEM_19_MARKS that some part of a text's own words still holds, in that order: the organisation
    notice's default words, not yet rewritten from the data-use clauses as read. Empty for a text that holds neither."""
    return [mark for mark in ITEM_19_MARKS if any(mark in part["text"].lower() for part in design)]


def _digest(raw):
    return shapes.tagged_digest(TEXT_TAG, canonicalize(raw))


def _record_keeping(days):
    """How long this deployment keeps a task's account record, as the texts say it: `days` is tasks.record_days(), a
    whole number of days ("for 1 day ..." for one) or None for good."""
    if days is None:
        return "for good"
    return f"for {days} day{'' if days == 1 else 's'} after the task is deleted"


def _filled(raw, operator_line, keeping):
    """`raw` with its digest, "{operator}" and "{record_keeping}" filled in one pass, so that nothing filled in is read
    again for a placeholder; "{operator}" is left as written while there is no operator."""
    def fill(match):
        if match.group(1) == "operator":
            return operator_line if operator_line is not None else match.group(0)
        return keeping

    out = dict(raw, digest=_digest(raw))
    out["design"] = [dict(part, text=_FILLED.sub(fill, part["text"])) for part in raw["design"]]
    return out


def texts() -> list:
    """Every risk point's text, in the order of RISK_POINTS, each with its digest, "{operator}" filled from operator()
    (left as written while that is None) and "{record_keeping}" from tasks.record_days() (the module docstring's
    shape)."""
    from . import tasks     # here, not above: tasks is the app's task module, and this one is also run on its own
    operator_line, keeping = operator(), _record_keeping(tasks.record_days())
    return [_filled(_raw(point), operator_line, keeping) for point in RISK_POINTS]


def text(point):
    """Risk point `point`'s text, as texts() gives it, or None for a point not in RISK_POINTS."""
    if type(point) is not int or point not in RISK_POINTS:
        return None
    from . import tasks
    return _filled(_raw(point), operator(), _record_keeping(tasks.record_days()))


# ---- the release check ----------------------------------------------------------------------------------------------

def _date(value):
    """An ISO date, YYYY-MM-DD, as a date; None for anything else."""
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value):
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


def release_check(release_date) -> list:
    """Each problem that holds a release back, one line each, naming the point and the quotation: a quotation with no
    source, no address or no read_on, one read before `release_date` (an ISO date, YYYY-MM-DD), one with neither words
    nor unread, one not read (its unread not None, an empty mark among them, as its text's unread counts it), a text
    whose unread is true, a text whose own words still hold the organisation notice's default words (ITEM_19_MARKS, each
    mark it holds named, on a line of its own), a text of DATA_USE_POINTS without both quotations of DATA_USE_IDS, and a
    point of RISK_POINTS with no text. An empty list when there is none; ValueError for a release's date that is not an
    ISO date. It reads the texts with their placeholders as written, so neither the operator nor the record's keeping,
    deployment settings, changes what it finds."""
    release = _date(release_date)
    if release is None:
        raise ValueError("The release's date is an ISO date, YYYY-MM-DD.")
    problems = []
    for point in RISK_POINTS:
        if point not in POINTS:
            problems.append(f"point {point}: has no text")
            continue
        raw = _raw(point)
        for q in raw["quotations"]:
            where = f"point {point}, quotation {q['id']}"
            if not q.get("source"):
                problems.append(f"{where}: names no source")
            if not q.get("address"):
                problems.append(f"{where}: has no address")
            read_on = q.get("read_on")
            if not read_on:
                problems.append(f"{where}: has no date it was read")
            elif _date(read_on) is None:
                problems.append(f"{where}: its date read, {read_on!r}, is not an ISO date")
            elif _date(read_on) < release:
                problems.append(f"{where}: read on {read_on}, before the release's date, {release.isoformat()}")
            if not q.get("words") and not q.get("unread"):
                problems.append(f"{where}: has neither its words nor what says it was not read")
            # marked not read as its text counts it (_raw: unread not None), so a mark left empty is named too
            if q.get("unread") is not None:
                problems.append(f"{where}: not read for this release ({q['unread'] or 'its mark is empty'})")
        if raw["unread"]:
            problems.append(f"point {point}: its text says a clause was not read for this release")
        marks = _item_19_marks(raw["design"])
        if marks:
            problems.append(f"point {point}: its own words still hold the organisation notice's default words ("
                            + " and ".join(f'"{mark}"' for mark in marks)
                            + "), which are to be set from the data-use clauses as read")
        if point in DATA_USE_POINTS:
            carried = {q["id"] for q in raw["quotations"]}
            for qid in DATA_USE_IDS:
                if qid not in carried:
                    problems.append(f"point {point}: lacks the data-use clause {qid}")
    return problems


USAGE = "Run as: python -m proofpane_open.ade.risk_texts --release-check YYYY-MM-DD (the release's date)."


def main(argv=None) -> int:
    """`--release-check YYYY-MM-DD`: print each line of release_check(that date) and return 1 while there is one, 0
    when there is none. Without a date it can read, or with anything else, print how to run it and return 2."""
    args = sys.argv[1:] if argv is None else list(argv)
    if len(args) != 2 or args[0] != "--release-check" or _date(args[1]) is None:
        print(USAGE, file=sys.stderr)
        return 2
    problems = release_check(args[1])
    for line in problems:
        print(line)
    if problems:
        print(f"{len(problems)} problem{' holds' if len(problems) == 1 else 's hold'} the release of {args[1]} back.")
        return 1
    # only what was checked: that the words are the ones meant is tests/test_risk_texts.py's to say, which pins them
    # (the design's, and the four parts worded here), not this check's
    print(f"The release check finds nothing to hold the release of {args[1]} back.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
