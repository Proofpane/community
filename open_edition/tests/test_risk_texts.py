"""The risk points' texts: each risk point has its text; each quotation of a provider's terms carries its source's
address, the date the source gives and the date it was read, word for word as this file holds it until it is read again;
a text with a clause marked not read says so, its "unread" true; a text's digest changes with its words; the release
check fails on an old read date, an unread clause and the dropped data-use clauses, naming each, and passes when every
source was read on or after the release's date; the Privacy Act's principles are among the sources it checks. As built,
every clause was read on READ, the organisation notice's words on training are set from the data-use clauses as read,
and the release check passes for that day and holds back a release the day after it. Then the route,
GET /api/ade/risks, which serves the texts and nothing else: no route knows of a pop-up.

The quoted words and the texts' statements below are pinned here, not read from the module: a change to the module's
words fails here until this file changes too, and so do the four parts the module words itself (its docstring names
them). The statements write a place the texts fill as <...>; as_placeholders turns each into the placeholder the texts
keep there. Every test here but one gives tasks.record_days a fake of its own, and that one, of a deployment's own
keeping, reads the module's own. Point 16, the accounts page's read of an account by itself, is held as every other
point is: its words pinned here, its two clauses point 7's, and the release check naming it as it names point 7. So is
point 17, a workspace session continued in a new one: its words pinned here, point 2's clauses under its case
"other_account", point 1's under "other_group", and the release check naming it as it names point 13."""

import collections
import copy
import datetime
import os
import re
import subprocess
import sys
import urllib.parse

import pytest

from conftest import ROOT, signed_up
from proofpane_open import paths  # noqa: F401  (puts the open packages, proofpane_core's among them, on sys.path)
from proofpane_open.ade import risk_texts, task_shapes, tasks
from proofpane_core.canonical import canonicalize

OPERATOR = "OPEN_EDITION_OPERATOR"
READ = "2026-11-12"                     # when the quoted documents were fetched: sixteen quotations, six pages
RELEASE = "2026-11-13"                  # a release after that reading

CONSUMER_TERMS = "https://www.anthropic.com/legal/consumer-terms"
COMMERCIAL_TERMS = "https://www.anthropic.com/legal/commercial-terms"
CLAUDE_CODE_LEGAL = "https://code.claude.com/docs/en/legal-and-compliance"
USAGE_POLICY_LATER = "https://www.anthropic.com/legal/aup"
USAGE_CREDITS_ARTICLE = ("https://support.claude.com/en/articles/"
                         "11145838-use-claude-code-with-your-pro-max-or-ultra-plan")
PRIVACY_ACT = "https://www.legislation.govt.nz/act/public/2020/31/en/latest/#LMS23342"

# ---- the pinned words -----------------------------------------------------------------------------------------------

Quoted = collections.namedtuple("Quoted", "document section address source_date words")

# Each quotation: words that name its document; its section where it has one; its address; the date the source gives
# (none on the Claude Code page; the Help Center article's own dateModified); and its words, the curly apostrophes
# made straight.
QUOTED = {
    "automated-use": Quoted(
        ("Consumer Terms of Service",), "3", CONSUMER_TERMS, "Effective Oct 8, 2025",
        "Except when you are accessing our Services via an Anthropic API Key or where we otherwise explicitly permit "
        "it, to access the Services through automated or non-human means, whether through a bot, script, or "
        "otherwise."),
    "sharing": Quoted(
        ("Consumer Terms of Service",), "2", CONSUMER_TERMS, "Effective Oct 8, 2025",
        "You may not share your Account login information, Anthropic API key, or Account credentials with anyone else. "
        "You also may not make your Account available to anyone else."),
    "interference": Quoted(
        ("Consumer Terms of Service",), "3", CONSUMER_TERMS, "Effective Oct 8, 2025",
        "You also must not abuse, harm, interfere with, or disrupt our Services, including, for example, introducing "
        "viruses or malware, spamming or DDoSing Services, or bypassing any of our systems or protective measures."),
    "ordinary-use": Quoted(
        ("Claude Code", "Legal and compliance"), None, CLAUDE_CODE_LEGAL, None,
        "Advertised usage limits for Pro, Max, and Ultra plans assume ordinary, individual usage of Claude Code and "
        "the Agent SDK."),
    "oauth": Quoted(
        ("Claude Code", "Legal and compliance"), None, CLAUDE_CODE_LEGAL, None,
        "OAuth authentication is intended exclusively for purchasers of Claude Free, Pro, Max, Ultra, Team, and "
        "Enterprise subscription plans and is designed to support ordinary use of Claude Code and other native "
        "Anthropic applications."),
    "own-subscription": Quoted(
        ("Claude Code", "Legal and compliance"), None, CLAUDE_CODE_LEGAL, None,
        "Nor does it prevent an end user from signing in to the unmodified Claude Code binary with their own Claude "
        "subscription, including where a platform hosts Claude Code as described under Can customers offer Claude "
        "Code in their products? above."),
    "multiple-accounts-2026": Quoted(
        ("Usage Policy",), None, USAGE_POLICY_LATER, "Effective Nov 12, 2026",
        "Coordinate malicious activity across multiple accounts, or create multiple accounts, to avoid detection, "
        "circumvent product guardrails, or generate identical or similar inputs that otherwise violate our Usage "
        "Policy"),
    "bypass": Quoted(
        ("Usage Policy",), None, USAGE_POLICY_LATER, "Effective Nov 12, 2026",
        "Intentionally bypass or interfere with capabilities, restrictions, or guardrails established within our "
        "products, including by circumvention of the account sign-up or log-in process or by instructing the model "
        "to produce harmful outputs (e.g., jailbreaking or prompt injection), without prior authorization from "
        "Anthropic"),
    "responsibility": Quoted(
        ("Commercial Terms of Service",), "D.5", COMMERCIAL_TERMS, "Effective Jun 17, 2025",
        "Customer is responsible for all activity under its account."),
    "usage-credits": Quoted(
        ("Help Center", "Use Claude Code with your Pro, Max, or Ultra plan"), None, USAGE_CREDITS_ARTICLE,
        "dateModified 2026-10-30T19:29:25Z",
        "Enable usage credits to keep using Claude with your Pro plan after you reach your five-hour session limit or "
        "your weekly limit."),
    "data-use-consumer": Quoted(
        ("Consumer Terms of Service",), "4", CONSUMER_TERMS, "Effective Oct 8, 2025",
        "We may use Materials to provide, maintain, and improve the Services and to develop other products and "
        "services, including training our models, unless you opt out of training through your account settings. Even "
        "if you opt out, we will use Materials for model training when: (1) you provide Feedback to us regarding any "
        "Materials, or (2) your Materials are flagged for safety review to improve our ability to detect harmful "
        "content, enforce our policies, or advance our safety research."),
    "data-use-commercial": Quoted(
        ("Commercial Terms of Service",), "B", COMMERCIAL_TERMS, "Effective Jun 17, 2025",
        "Anthropic may not train models on Customer Content from Services."),
    "privacy-principle-3": Quoted(
        ("Privacy Act 2020",), "information privacy principle 3", PRIVACY_ACT, "Version as at 8 October 2026",
        "(1) If an agency collects personal information from the individual concerned, the agency must take any steps "
        "that are, in the circumstances, reasonable to ensure that the individual concerned is aware of— (a) the "
        "fact that the information is being collected; and (b) the purpose for which the information is being "
        "collected; and (c) the intended recipients of the information; and (d) the name and address of— (i) the "
        "agency that is collecting the information; and (ii) the agency that will hold the information; and (e) if "
        "the collection of the information is authorised or required by or under law,— (i) the particular law by or "
        "under which the collection of the information is authorised or required; and (ii) whether the supply of the "
        "information by that individual is voluntary or mandatory; and (f) the consequences (if any) for that "
        "individual if all or any part of the requested information is not provided; and (g) the rights of access "
        "to, and correction of, information provided by the IPPs. (2) The steps referred to in subclause (1) must be "
        "taken before the information is collected or, if that is not practicable, as soon as practicable after the "
        "information is collected. (3) An agency is not required to take the steps referred to in subclause (1) in "
        "relation to the collection of information from an individual if the agency has taken those steps on a "
        "recent previous occasion in relation to the collection, from that individual, of the same information or "
        "information of the same kind. (4) It is not necessary for an agency to comply with subclause (1) if the "
        "agency believes, on reasonable grounds,— (a) that non-compliance would not prejudice the interests of the "
        "individual concerned; or (b) that non-compliance is necessary— (i) to avoid prejudice to the maintenance of "
        "the law by any public sector agency, including prejudice to the prevention, detection, investigation, "
        "prosecution, and punishment of offences; or (ii) for the enforcement of a law that imposes a pecuniary "
        "penalty; or (iii) for the protection of public revenue; or (iv) for the conduct of proceedings before any "
        "court or tribunal (being proceedings that have been commenced or are reasonably in contemplation); or (c) "
        "that compliance would prejudice the purposes of the collection; or (d) that compliance is not reasonably "
        "practicable in the circumstances of the particular case; or (e) that the information— (i) will not be used "
        "in a form in which the individual concerned is identified; or (ii) will be used for statistical or research "
        "purposes and will not be published in a form that could reasonably be expected to identify the individual "
        "concerned."),
    "privacy-principle-6": Quoted(
        ("Privacy Act 2020",), "information privacy principle 6", PRIVACY_ACT, "Version as at 8 October 2026",
        "(1) An individual is entitled to receive from an agency upon request— (a) confirmation of whether the agency "
        "holds any personal information about them; and (b) access to their personal information. (2) If an "
        "individual concerned is given access to personal information, the individual must be advised that, under "
        "IPP 7, the individual may request the correction of that information. (3) This IPP is subject to the "
        "provisions of Part 4."),
    "privacy-principle-7": Quoted(
        ("Privacy Act 2020",), "information privacy principle 7", PRIVACY_ACT, "Version as at 8 October 2026",
        "(1) An individual whose personal information is held by an agency is entitled to request the agency to "
        "correct the information. (2) An agency that holds personal information must, on request or on its own "
        "initiative, take such steps (if any) that are reasonable in the circumstances to ensure that, having regard "
        "to the purposes for which the information may lawfully be used, the information is accurate, up to date, "
        "complete, and not misleading. (3) When requesting the correction of personal information, or at any later "
        "time, an individual is entitled to— (a) provide the agency with a statement of the correction sought to the "
        "information (a statement of correction); and (b) request the agency to attach the statement of correction "
        "to the information if the agency does not make the correction sought. (4) If an agency that holds personal "
        "information is not willing to correct the information as requested and has been provided with a statement "
        "of correction, the agency must take such steps (if any) that are reasonable in the circumstances to ensure "
        "that the statement of correction is attached to the information in a manner that ensures that it will "
        "always be read with the information. (5) If an agency corrects personal information or attaches a statement "
        "of correction to personal information, that agency must, so far as is reasonably practicable, inform every "
        "other person to whom the agency has disclosed the information. (6) Subclauses (1) to (4) are subject to the "
        "provisions of Part 4."),
    "privacy-principle-9": Quoted(
        ("Privacy Act 2020",), "information privacy principle 9", PRIVACY_ACT, "Version as at 8 October 2026",
        "An agency that holds personal information must not keep that information for longer than is required for the "
        "purposes for which the information may lawfully be used."),
}

DATA_USE = ("data-use-consumer", "data-use-commercial")
# the Act's principles the teammate's notice rests on: 3, telling a person what is collected; 6 and
# 7, seeing and correcting it; 9, keeping it no longer than its purpose needs
PRINCIPLES = tuple(f"privacy-principle-{n}" for n in (3, 6, 7, 9))
IDS = tuple(QUOTED)

ORGANISATION_CLAUSES = (("responsibility", None),) + tuple((qid, None) for qid in DATA_USE)
RELAY_CLAUSES = tuple((qid, None) for qid in ("ordinary-use", "multiple-accounts-2026", "bypass", "interference"))
UNWATCHED_CLAUSES = (("automated-use", None), ("oauth", None))


def _cased(clauses, case):
    return tuple((qid, case) for qid, _ in clauses)


# what each point quotes, with the case it is shown under:
# point 1's clauses are the responsibility clause and the data-use clauses, which points 3, 4 and 5 show always, 12
# for custody's crossing and 13 at an acceptance into another group; 13 shows points 2's and 7's always
QUOTES = {
    1: ORGANISATION_CLAUSES,
    2: RELAY_CLAUSES,
    3: ORGANISATION_CLAUSES,
    4: ORGANISATION_CLAUSES,
    5: ORGANISATION_CLAUSES,
    6: (("sharing", None), ("ordinary-use", None), ("own-subscription", None), ("responsibility", "seat")),
    7: UNWATCHED_CLAUSES,
    8: (("usage-credits", None),),
    9: (("ordinary-use", None),),
    10: (),
    11: (),
    12: (("sharing", None),) + _cased(ORGANISATION_CLAUSES, "custody_cross"),
    13: ((("sharing", None),) + UNWATCHED_CLAUSES + RELAY_CLAUSES + _cased(ORGANISATION_CLAUSES, "other_group")
         + tuple((principle, None) for principle in PRINCIPLES)),
    14: (),
    15: (),
    16: UNWATCHED_CLAUSES,
    17: _cased(RELAY_CLAUSES, "other_account") + _cased(ORGANISATION_CLAUSES, "other_group"),
}
CARRIERS ={qid: {point for point, quoted in QUOTES.items() if any(i == qid for i, _ in quoted)} for qid in IDS}

# The texts' own statements, and the parts of a few of them
SEAT_SETTINGS = (
    "If this organisation has its own settings for Claude Code, Claude Code applies them to the work done on its seats "
    "in place of this workspace's own; that much is Claude Code's own description of its settings, not tested here. "
    "So their hooks run in your workspace, as you, and can read every account's sign-in here. This workspace's checks "
    "still judge a task's own calls, but in a hosted workspace an agent's own runs and chats on these seats run "
    "without them.")
BACK_HOME = "This task started on <its home group> and goes back there, now that an account there has room again."
BACK_HOME_OFF = ("This task started on <its home group> and goes back there, since crossing was turned off while it "
                 "was away; it may wait there until an account has room.")
RELAY = ("Nothing of Anthropic's that was read allows using several of your own accounts in turn to go past one "
         "account's limits; whether a plan's limits are among the restrictions the Usage Policy of 12 November 2026 "
         "means is a reading, not its words. A task moves at most 10 times in an hour before it waits for you. The "
         "relay is off unless this deployment sets it on; with it off, a task whose account runs out of room waits for "
         "that account's reset, going on there by itself while Auto-resume is on, or waits for you where no reset is "
         "known, and you can move it with Continue on... or turn the relay on, with the task's own Relay switch once "
         "this deployment sets the relay on.")
CROSS_SWITCH = (
    "With this on, when no account of this task's own group has room, the relay may take it from your personal plans "
    "onto the seats of one Team organisation, which it is then bound to, or from those seats onto your personal "
    "plans, and back home once an account there has room. It never takes the task into a second organisation, nor "
    "into or out of an Enterprise organisation's seats. After you press it onto another organisation's seats, or onto "
    "or off an Enterprise seat, it crosses nowhere by itself, whatever this switch says, nor do custody and the "
    "custodian's relay take it into another group.")
OTHER_GROUP = (
    "A task bound to no organisation yet becomes bound to this one, and may still cross between your personal plans "
    "and its seats by itself where the relay is on; a task bound to another organisation already crosses nowhere by "
    "itself after this, and neither custody nor the custodian's relay takes it into another group. Onto your "
    "personal plans, a task keeps the organisation it is bound to.")
ENTERPRISE = (
    "An Enterprise organisation may have an agreement of its own with Anthropic, which this text cannot quote, and "
    "nothing here was measured on an Enterprise seat. After this the task crosses nowhere by itself, and neither "
    "custody nor the custodian's relay takes it into another group.")
ADD_ACCOUNT = (
    "Use only your own accounts here, never a colleague's. The workspace cannot tell whose account a folder holds. "
    "Code a task runs here, a repository's own settings among it, runs as you, outside the workspace's checks, which "
    "judge only the task's own calls, and can read every account's sign-in in this workspace. This deployment keeps "
    "the record of which of your accounts did each task's work for <its setting: a number of days, or for good> "
    "after the task is deleted.")
UNWATCHED = ("Nothing of Anthropic's that was read explicitly permits a program to run Claude Code on your "
             "subscription while nobody is there.")
USAGE_CREDITS = ("If usage credits are on for this account, work past its limit may be billed before the relay can "
                 "move on.")
TO_THE_LIMIT = ("Unattended work may then use this account's week to its limit, which claude.ai and your own Claude "
                "Code share.")
API_KEY = ("A key kept in the workspace is exposed to runs: the gate refuses calls that name the key store, and it is "
           "not a sandbox. Set a spending limit for this key at Anthropic.")
CAP = ("The cap counts Claude Code's estimate, and the attempt stops at the end of the turn that passes it, so one "
       "turn can go over; the key's own spending limit at its provider is the hard one.")
HANDED = ("Your code goes to your teammate's agent and to an account of theirs, under that account's terms; it stays "
          "in the group they take it on, unless you ticked custody's crossing. Who took it, and on which plan and "
          "organisation, is kept in this task's record.")
ADOPTED_CHECK = ("This command runs in your workspace, as your user, with the network and outside the gate, as your "
                 "own check.")
OFFER_NOTICE = (
    "If you accept, <the owner>'s record of this task keeps your user in this deployment until 90 days after the task "
    "is deleted, when it becomes 'a teammate', or until this deployment deletes the record, if that is sooner; and it "
    "keeps the plan of the account your agent takes it on, with that account's organisation only where it is a Team or "
    "Enterprise organisation's seat, and when, for as long as this deployment keeps such records (<its setting: a "
    "number of days after the task is deleted, or for good>). Your own "
    "record of the task you make by accepting keeps which of your accounts did each part, with their emails, as long "
    "as this deployment keeps records; you can delete it. <The owner>'s record does not keep your email, but your user "
    "here does, and the commits your agent makes carry your email as their author and stay in <the owner>'s "
    "repository. It is kept so that <the owner> can say which accounts, under which terms, did the work on their "
    "code; <the owner> and this deployment's operator can see it. Accepting is your choice; if you decline, or let it "
    "expire, nothing of yours is kept. <The operator, as `OPEN_EDITION_OPERATOR` names them, with their contact> holds "
    "it; you may ask them to see it, to correct it, or to have your user in it reduced now.")
OFFER_BEGINS = "If you accept, <the owner>'s record of this task keeps"
CUSTODY_BEGINS = "If you accept custody, then for every task handed to you while it stands, <the owner>'s record of " \
                 "that task keeps"
CUSTODY_RECORD = "<The owner>'s custody record names you until 90 days after custody ends."
LOCAL_PROVIDER = ("On this machine an agent's runs could reach the secret its records are signed with, so the record "
                  "of what a task did holds against an agent that follows the rules, not against code written to "
                  "forge it.")
DELETE_RECORD = ("This deletes, for good, the record of which accounts did this task's work. The task's journal names "
                 "the same accounts until 90 days after the task is deleted; after that, nothing in the app keeps "
                 "them.")
# point 16: one text, ending in point 7's own sentence
READ_BY_ITSELF = (
    "When a Claude Code account in this workspace signs in and is neither confirmed as yours nor waiting to join, this "
    "page starts that account's Claude Code once by itself, without your press, to read who it is: its email, its plan "
    "and its organisation. That start sends Claude no message, and runs in this workspace as any start of that account "
    "does. Nothing of Anthropic's that was read explicitly permits a program to run Claude Code on your subscription "
    "while nobody is there.")
# point 17: the handover's own words, shown always; point 2's
# words turned to a session, on another Claude Code account; and the Codex notice, on Codex
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
# the organisation notice says this in those words, set from the data-use clauses as read
CROSSING_TERMS = (
    "On a Team seat, work falls under the organisation's commercial terms, under which Anthropic may not train models "
    "on Customer Content from Services. On a personal plan, it falls under the consumer terms, under which Anthropic "
    "may use Materials to train its models unless the account holder opts out of training through the account's "
    "settings; even if the account holder opts out, Anthropic will use Materials for model training when the account "
    "holder gives Anthropic Feedback regarding any Materials, or when the account holder's Materials are flagged for "
    "safety review to improve Anthropic's ability to detect harmful content, enforce its policies, or advance its "
    "safety research. As this notice reads the two, a crossing onto a personal plan may put work started under the "
    "commercial terms where Anthropic may use it to train its models.")
# its default words before the data-use clauses were read, which the release check names if they come back
DEFAULT_CROSSING_TERMS = (
    "On a Team seat, work falls under the organisation's commercial terms; on a personal plan, under the consumer "
    "terms, where (as Anthropic announced in 2025, not read again here) the account's own setting decides whether "
    "its chats may be used to train models. So a crossing onto a personal plan may put work started under the "
    "commercial terms under that setting.")
# the teammate notice, as the owner reads it
TEAMMATE = ("The task runs on your teammate's own agent and account, which they chose; it is their use of their "
            "account, not yours.")
# the warning to the custodian, the parts it keeps when worded for every task handed while custody stands
CUSTODIAN_ROOM = "on your accounts but never on paid usage or your keys, while their own accounts are out of room."
CUSTODIAN_TRUST = "The workspace's gate matches names and is not a wall: accept custody only from someone you trust"
# point 11's words for another vendor's key
OTHER_VENDOR_CODE = "code and text go to that vendor, whose terms were not read"
OTHER_VENDOR_ESTIMATE = "Anthropic's models"
# the organisation notice and the last sentence of the relay notice, in their own words
ORGANISATION_NOTICE = (
    "This task started on <a personal plan / the seats of an organisation> and would go on <on a seat of an "
    "organisation / on a personal plan>. Work on a Team or Enterprise seat goes into that organisation, under its "
    "agreement with Anthropic ('Customer is responsible for all activity under its account.'); work from such a seat "
    "taken to a personal account leaves it.")
RESPONSIBILITY = "Customer is responsible for all activity under its account."
RELAY_LAST = "Relaying a task across your accounts is your choice."

# The parts the module words itself (its docstring names them): each pinned here word for word, so that a change to
# one is seen, as the other words are by STATEMENTS.
# The teammate notice turned to the teammate who reads it at point 13 (custody shows it to both, and its
# own words, read to the teammate, would put the task on the owner's agent).
TEAMMATE_FOR_THE_TEAMMATE = ("The task runs on your own agent and account, which you choose; it is your use of your "
                             "account, not your teammate's.")
# The warning to the custodian, worded for every task handed while custody stands.
CUSTODIAN = (
    "The tasks handed to you while custody stands, each one your teammate confirmed for custody, will run on the "
    "agents you choose to receive them, with their permissions, on your accounts but never on paid usage or your "
    "keys, while their own accounts are out of room. The workspace's gate matches names and is not a wall: accept "
    "custody only from someone you trust, and give those agents the narrowest allow rules they need.")
# Point 11's words for another vendor's key.
OTHER_VENDOR = (
    "The task's code and text go to that vendor, whose terms were not read for this. Claude Code's estimate of cost "
    "prices by Anthropic's models, so for another vendor it is a guess, and that vendor's own spending limit is the "
    "one to trust.")
# each statement, and the point and case of the one part of that point that holds it, in every point that does
STATEMENTS = {
    "seat-settings": (SEAT_SETTINGS, {1: "onto_seat", 3: None, 4: "onto_seat", 5: "onto_seat", 6: "seat",
                                     17: "onto_seat"}),
    "1-back-home": (BACK_HOME, {1: "back_home"}),
    "1-back-home-off": (BACK_HOME_OFF, {1: "back_home_off"}),
    "1-crossing-terms": (CROSSING_TERMS, {1: None, 3: None, 4: None, 5: None, 12: "custody_cross", 13: "other_group",
                                         17: "other_group"}),
    "2-relay": (RELAY, {2: None, 13: None}),
    "3-cross-switch": (CROSS_SWITCH, {3: None}),
    "4-other-group": (OTHER_GROUP, {4: None}),
    "5-enterprise": (ENTERPRISE, {5: None}),
    "6-keeping": (ADD_ACCOUNT[ADD_ACCOUNT.index("This deployment keeps"):], {6: None}),
    "6-add-account": (ADD_ACCOUNT, {6: None}),
    "7-unwatched": (UNWATCHED, {7: None, 13: None, 16: None}),
    "8-usage-credits": (USAGE_CREDITS, {8: None}),
    "9-to-the-limit": (TO_THE_LIMIT, {9: None}),
    "10-api-key": (API_KEY, {10: None}),
    "11-cap": (CAP, {11: None}),
    "11-other-vendor-code": (OTHER_VENDOR_CODE, {11: "other_vendor"}),
    "11-other-vendor-estimate": (OTHER_VENDOR_ESTIMATE, {11: "other_vendor"}),
    "12-teammate": (TEAMMATE, {12: None}),
    "12-handed": (HANDED, {12: None}),
    "13-adopted-check": (ADOPTED_CHECK, {13: "adopted_check"}),
    "13-offer-notice": (OFFER_NOTICE, {13: "offer"}),
    "13-custody-begins": (CUSTODY_BEGINS, {13: "custody"}),
    "13-custody-record": (CUSTODY_RECORD, {13: "custody"}),
    "13-custodian-room": (CUSTODIAN_ROOM, {13: "custody"}),
    "13-custodian-trust": (CUSTODIAN_TRUST, {13: "custody"}),
    "14-local-provider": (LOCAL_PROVIDER, {14: None}),
    "15-delete-record": (DELETE_RECORD, {15: None}),
    "16-read-by-itself": (READ_BY_ITSELF, {16: None}),
    "17-handover": (HANDOVER, {17: None}),
    "17-other-account": (OTHER_ACCOUNT, {17: "other_account"}),
    "17-codex": (CODEX, {17: "codex"}),
}

# the statements write a place the texts fill as <...>; the texts keep this placeholder there
DESIGN_PLACES = (
    ("for <its setting: a number of days, or for good> after the task is deleted", "{record_keeping}"),
    ("<its setting: a number of days after the task is deleted, or for good>", "{record_keeping}"),
    ("<The operator, as `OPEN_EDITION_OPERATOR` names them, with their contact>", "{operator}"),
    ("<its home group>", "{home_group}"),
    ("<the owner>", "{owner}"),
    ("<The owner>", "{owner}"),
)


def as_placeholders(words):
    """Pinned words with each place they write as <...> turned into the placeholder the texts keep there."""
    for place, placeholder in DESIGN_PLACES:
        words = words.replace(place, placeholder)
    assert "<" not in words and ">" not in words, words
    return words


# ---- helpers --------------------------------------------------------------------------------------------------------

DAYS_MARK = 7321                        # a keeping no text says itself, so its filling can be undone exactly
KEEPING_MARK = "for 7321 days after the task is deleted"
# the tasks module's own reader of OPEN_EDITION_ADE_ACCOUNT_RECORD_DAYS, taken before the fixture below fakes it for
# every test: the test of a deployment's own keeping puts it back
REAL_RECORD_DAYS = tasks.record_days


@pytest.fixture(autouse=True)
def keeping(monkeypatch):
    """The two settings the texts are filled from: the operator unset, and tasks.record_days (REAL_RECORD_DAYS) faked
    to give keeping["days"], 90 to begin with, counting its calls."""
    monkeypatch.delenv(OPERATOR, raising=False)
    state = {"days": 90, "calls": 0}

    def record_days():
        state["calls"] += 1
        return state["days"]

    monkeypatch.setattr(tasks, "record_days", record_days)
    return state


def as_written(state, operator_line=None):
    """Every text as texts() gives it, its two fillings undone: the record's keeping given as DAYS_MARK and put back
    as {record_keeping}, and the operator, where `operator_line` is set in the environment, put back as {operator}."""
    state["days"] = DAYS_MARK
    out = risk_texts.texts()
    for text in out:
        for part in text["design"]:
            words = part["text"].replace(KEEPING_MARK, "{record_keeping}")
            part["text"] = words.replace(operator_line, "{operator}") if operator_line else words
    return out


def digest_of(text):
    """A text's digest: tagged_digest(TEXT_TAG, the RFC 8785 bytes of the text without its digest)."""
    return task_shapes.tagged_digest(b"proofpane-risk-text/1",
                                     canonicalize({k: v for k, v in text.items() if k != "digest"}))


def part_of(text, case):
    """The words of the one design part of `text` shown under `case`."""
    found = [part["text"] for part in text["design"] if part["case"] == case]
    assert len(found) == 1, (text["point"], case, found)
    return found[0]


def notices(text):
    """Point 13's two notices under the Privacy Act: the offer's, and custody's (its custody part that begins so)."""
    custody = [part["text"] for part in text["design"] if part["case"] == "custody"
               and part["text"].startswith("If you accept custody")]
    assert len(custody) == 1, custody
    return part_of(text, "offer"), custody[0]


def changed_quotations(monkeypatch, qid, **fields):
    """risk_texts.QUOTATIONS as a copy with `qid`'s `fields` changed: the module's own dict is never changed."""
    quotations = copy.deepcopy(risk_texts.QUOTATIONS)
    quotations[qid] = dict(quotations[qid], **fields)
    monkeypatch.setattr(risk_texts, "QUOTATIONS", quotations)


def changed_points(monkeypatch, point, change):
    """risk_texts.POINTS as a copy with `change` applied to `point`'s entry: the module's own dict is never changed."""
    points = copy.deepcopy(risk_texts.POINTS)
    change(points[point])
    monkeypatch.setattr(risk_texts, "POINTS", points)


def all_read(monkeypatch, on, default_words=False, **fields_by_id):
    """risk_texts.QUOTATIONS as a copy in which every quotation was read on `on`: words kept (one a call before left
    without words gets words of the test's own), nothing marked unread; then each id of `fields_by_id` given those
    fields; and risk_texts.POINTS as a copy in which the organisation notice's words on training are the ones set from
    the data-use clauses, or, with `default_words`, its default words again, as they were before the clauses were read,
    whichever a call before left there."""
    quotations = {qid: dict(q, words=q["words"] or f"The words of {qid}, as read.", unread=None, read_on=on)
                  for qid, q in risk_texts.QUOTATIONS.items()}
    for qid, fields in fields_by_id.items():
        quotations[qid] = dict(quotations[qid], **fields)
    monkeypatch.setattr(risk_texts, "QUOTATIONS", quotations)
    points = copy.deepcopy(risk_texts.POINTS)
    held = 0
    for spec in points.values():
        for i, (case, words) in enumerate(spec["design"]):
            held += CROSSING_TERMS in words or DEFAULT_CROSSING_TERMS in words
            words = (words.replace(CROSSING_TERMS, DEFAULT_CROSSING_TERMS) if default_words
                     else words.replace(DEFAULT_CROSSING_TERMS, CROSSING_TERMS))
            spec["design"][i] = (case, words)
    if default_words:
        assert held == 7, held                  # points 1, 3, 4, 5, 12, 13 and 17: else the module's words moved
    monkeypatch.setattr(risk_texts, "POINTS", points)


_NAMED_ID = re.compile(r"(?<![a-z0-9-])(" + "|".join(re.escape(i) for i in sorted(IDS, key=len, reverse=True))
                       + r")(?![a-z0-9-])")


def named(line):
    """(point, quotation id or None) a line of the release check names: one point, and at most one quotation."""
    points = re.findall(r"\bpoint (\d+)\b", line)
    ids = set(_NAMED_ID.findall(line))
    assert len(points) == 1 and len(ids) <= 1, line
    return int(points[0]), (ids.pop() if ids else None)


def day_after(day, n=1):
    return (datetime.date.fromisoformat(day) + datetime.timedelta(days=n)).isoformat()


# the texts that hold the organisation notice, and with it its words on training
HOLDERS = (1, 3, 4, 5, 12, 13, 17)
# what of the notice's default words the release check looks for, in its order: the mark, and the announcement they
# rest on
DEFAULT_MARKS = ("not read again", "announced in 2025")


def default_words_line(point, *marks):
    """The release check's line for a text whose own words still hold the organisation notice's default words, naming
    the marks it holds, and saying no more than that."""
    return (f"point {point}: its own words still hold the organisation notice's default words ("
            + " and ".join(f'"{mark}"' for mark in marks) + "), which are to be set from the data-use clauses as read")


# ---- the module's constants -----------------------------------------------------------------------------------------

def test_the_module_s_names_for_the_texts():
    """Seventeen points, the cases each names, the texts that need the data-use clauses, the placeholders, the
    digest's tag and the operator's setting."""
    assert risk_texts.RISK_POINTS == tuple(range(1, 18))
    assert risk_texts.CASES == {1: ("crossing", "back_home", "back_home_off", "onto_seat"), 4: ("onto_seat",),
                                5: ("onto_seat",), 6: ("seat",), 11: ("anthropic_key", "other_vendor"),
                                12: ("custody_cross",), 13: ("offer", "custody", "adopted_check", "other_group"),
                                17: ("other_account", "other_group", "onto_seat", "codex")}
    assert risk_texts.DATA_USE_POINTS == (1, 3, 4, 5, 12)
    assert risk_texts.DATA_USE_IDS == DATA_USE
    assert set(risk_texts.PLACEHOLDERS) == {"owner", "operator", "home_group", "record_keeping"}
    assert risk_texts.TEXT_TAG == b"proofpane-risk-text/1"
    assert risk_texts.OPERATOR_SETTING == OPERATOR
    # the marks of the organisation notice's default words, by a name that says what it holds
    assert getattr(risk_texts, "DEFAULT_NOTICE_MARKS", None) == DEFAULT_MARKS
    assert not hasattr(risk_texts, "ITEM_19_MARKS")
    # the date every quotation was read, by a name that holds no date of its own
    assert getattr(risk_texts, "READ_ON", None) == READ
    assert not hasattr(risk_texts, "READ_2026_11_11")


# ---- each point's text ----------------------------------------------------------------------------------------------

def test_every_point_has_its_text_in_order_and_in_its_shape():
    texts = risk_texts.texts()
    assert [t["point"] for t in texts] == list(range(1, 18))
    titles = set()
    for t in texts:
        assert set(t) == {"point", "title", "design", "quotations", "unread", "digest"}
        assert isinstance(t["title"], str) and t["title"].strip() and t["title"] not in titles
        titles.add(t["title"])
        assert t["design"] and any(part["case"] is None for part in t["design"]), t["point"]
        for part in t["design"]:
            assert set(part) == {"case", "text"}
            assert isinstance(part["text"], str) and part["text"].strip()
        for q in t["quotations"]:
            assert set(q) == {"id", "case", "source", "section", "address", "source_date", "read_on", "words",
                              "unread"}
            assert q["address"].startswith("https://"), q
            assert (q["words"] is None) != (q["unread"] is None), q
        assert t["unread"] is True or t["unread"] is False
        assert task_shapes.DIGEST.fullmatch(t["digest"])
        assert risk_texts.text(t["point"]) == t


@pytest.mark.parametrize("point", [0, 18, -1, "13", "16", "17", 16.0, 17.0, 13.0, True, None])
def test_text_is_none_for_anything_but_a_risk_point(point):
    assert risk_texts.text(point) is None


def test_each_text_is_fresh_so_a_caller_may_change_what_it_is_given():
    # the routes fill {owner} in what text(13) gives them; nothing they change reaches the next caller
    given = risk_texts.text(13)
    before = risk_texts.text(13)
    given["design"][0]["text"] = "changed"
    given["quotations"][0]["words"] = "changed"
    given["quotations"].clear()
    assert risk_texts.text(13) == before
    assert risk_texts.texts()[12] == before


# ---- the quotations -------------------------------------------------------------------------------------------------

def test_each_quotation_is_word_for_word_as_this_file_holds_it():
    seen = {}
    for t in risk_texts.texts():
        for q in t["quotations"]:
            if q["words"] is None:
                continue
            assert q["id"] in QUOTED, q["id"]
            design = QUOTED[q["id"]]
            for words in design.document:
                assert words in q["source"], (q["id"], q["source"])
            assert q["section"] == design.section, q["id"]
            assert q["address"] == design.address, q["id"]
            assert q["source_date"] == design.source_date, q["id"]
            assert q["read_on"] == READ, q["id"]
            assert q["words"] == design.words, q["id"]
            assert q["unread"] is None
            # the same quotation is the same everywhere it is shown; only the case it is shown under differs
            seen.setdefault(q["id"], {k: v for k, v in q.items() if k != "case"})
            assert {k: v for k, v in q.items() if k != "case"} == seen[q["id"]]
    assert set(seen) == set(QUOTED)


def test_each_point_quotes_its_clauses_under_their_cases():
    for t in risk_texts.texts():
        carried = collections.Counter((q["id"], q["case"]) for q in t["quotations"])
        assert carried == collections.Counter(QUOTES[t["point"]]), t["point"]


def test_every_clause_is_read_as_built_and_none_says_otherwise():
    seen = set()
    for t in risk_texts.texts():
        for q in t["quotations"]:
            assert q["words"] and q["unread"] is None and q["read_on"] == READ, q
            seen.add(q["id"])
    assert seen == set(IDS) and len(IDS) == 16


def test_the_act_s_principles_are_read_at_the_act_s_own_page():
    # where the module's old address led on the day of the reading: the whole Act, at section 22, the information
    # privacy principles, by its own anchor; each principle cited by its number, and nothing more of the Act
    for principle in PRINCIPLES:
        q = risk_texts.QUOTATIONS[principle]
        parts = urllib.parse.urlsplit(q["address"])
        assert (parts.scheme, parts.netloc, parts.path, parts.fragment) == (
            "https", "www.legislation.govt.nz", "/act/public/2020/31/en/latest/", "LMS23342"), q
        assert q["section"] == f"information privacy principle {principle.rsplit('-', 1)[1]}", q
        assert q["source_date"] == "Version as at 8 October 2026", q


def test_no_text_is_unread_as_built_and_one_whose_clause_is_marked_unread_is(monkeypatch):
    texts = risk_texts.texts()
    for t in texts:
        assert t["unread"] == any(q["unread"] is not None for q in t["quotations"]), t["point"]
    assert {t["point"] for t in texts if t["unread"]} == set()
    changed_quotations(monkeypatch, "data-use-consumer", words=None, read_on=None, source_date=None,
                       unread="Not read for this release; it is elsewhere.")
    assert {t["point"] for t in risk_texts.texts() if t["unread"]} == CARRIERS["data-use-consumer"]


def test_every_text_that_needs_them_carries_both_data_use_clauses():
    for point in (1, 3, 4, 5, 12):
        assert set(DATA_USE) <= {q["id"] for q in risk_texts.text(point)["quotations"]}, point


def test_the_privacy_act_s_principles_are_quoted_by_the_teammate_s_text():
    for t in risk_texts.texts():
        principles = {q["id"] for q in t["quotations"] if q["id"].startswith("privacy-principle-")}
        assert principles == (set(PRINCIPLES) if t["point"] == 13 else set()), t["point"]


# ---- the statements and the cases -----------------------------------------------------------------------------------

@pytest.mark.parametrize("name", STATEMENTS)
def test_each_statement_is_in_its_points_under_their_cases(keeping, name):
    words, where = STATEMENTS[name]
    words = as_placeholders(words)
    found = {}
    for t in as_written(keeping):
        holding = [part["case"] for part in t["design"] if words in part["text"]]
        assert len(holding) <= 1, (t["point"], holding)
        if holding:
            found[t["point"]] = holding[0]
    assert found == where


def test_each_case_is_one_its_point_names_and_each_is_shown():
    for t in risk_texts.texts():
        names = risk_texts.CASES.get(t["point"], ())
        for item in t["design"] + t["quotations"]:
            assert item["case"] is None or item["case"] in names, (t["point"], item["case"])
        used = {item["case"] for item in t["design"] + t["quotations"]}
        # Anthropic's own key has no words of its own: under its case the cap's words show alone.
        # A crossing by itself has none either: the organisation notice's sentence that says which way has no
        # placeholder, so under its case point 1 shows the parts it shows always, beside the crossing's line
        assert set(names) - {"anthropic_key", "crossing"} <= used, t["point"]
    assert {part["case"] for part in risk_texts.text(1)["design"]} == {"back_home", "back_home_off", None, "onto_seat"}


def test_the_seat_settings_text_is_one_text_in_points_1_3_4_5_6_and_17_alone():
    seat = as_placeholders(SEAT_SETTINGS)
    at = {}
    for t in risk_texts.texts():
        for part in t["design"]:
            if seat in part["text"]:
                assert part["text"] == seat, t["point"]
                at.setdefault(t["point"], []).append(part["case"])
    assert at == {1: ["onto_seat"], 3: [None], 4: ["onto_seat"], 5: ["onto_seat"], 6: ["seat"], 17: ["onto_seat"]}


def test_points_12_13_and_17_carry_point_1_s_clauses_under_their_cases():
    one, twelve, thirteen, seventeen = (risk_texts.text(n) for n in (1, 12, 13, 17))
    organisation = part_of(one, None)
    assert {(q["id"], q["case"]) for q in one["quotations"]} == set(ORGANISATION_CLAUSES)
    for t, case in ((twelve, "custody_cross"), (thirteen, "other_group"), (seventeen, "other_group")):
        assert part_of(t, case) == organisation, t["point"]
        assert {q["id"] for q in t["quotations"] if q["case"] == case} == {q["id"] for q in one["quotations"]}
    for point in (3, 4, 5):
        assert organisation in [part["text"] for part in risk_texts.text(point)["design"] if part["case"] is None]


def test_point_13_carries_points_2_and_7_whole_for_the_runs_that_follow():
    thirteen = risk_texts.text(13)
    always = [part["text"] for part in thirteen["design"] if part["case"] is None]
    quoted = {q["id"] for q in thirteen["quotations"] if q["case"] is None}
    for point in (2, 7):
        t = risk_texts.text(point)
        for part in t["design"]:
            assert part["case"] is None and part["text"] in always, (point, part)
        assert {q["id"] for q in t["quotations"]} <= quoted, point


def test_point_16_quotes_point_7_s_clauses_with_its_own_words():
    # the accounts page's read of an account by itself: a program starting Claude Code on the
    # subscription without a press is the work point 7 names, so its clauses are point 7's, with the same ids, words,
    # sources and dates; its own words are one part, shown always, ending in point 7's own sentence
    seven, sixteen = risk_texts.text(7), risk_texts.text(16)
    assert [q["id"] for q in sixteen["quotations"]] == ["automated-use", "oauth"]
    assert sixteen["quotations"] == seven["quotations"]
    assert sixteen["design"] == [{"case": None, "text": READ_BY_ITSELF}]
    assert READ_BY_ITSELF.endswith(" " + UNWATCHED)
    assert sixteen["title"] == "Reading who an account is, by itself"
    assert sixteen["unread"] is False
    assert 16 not in risk_texts.CASES and 16 not in risk_texts.DATA_USE_POINTS


def test_point_17_carries_point_2_s_clauses_turned_to_a_session_and_the_codex_notice_on_codex():
    # a workspace session continued in a new one: its own words always; on another Claude Code
    # account, point 2's clauses, the same ids, words, sources and dates, with the first sentence of point 2's own words
    # and a last one of its own; on Codex, the Codex notice, which names no clause, since none of OpenAI's was read
    two, seventeen = risk_texts.text(2), risk_texts.text(17)
    assert seventeen["title"] == "Continuing a session in a new one"
    assert part_of(seventeen, None) == HANDOVER
    assert [q for q in seventeen["quotations"] if q["case"] == "other_account"] == [
        dict(q, case="other_account") for q in two["quotations"]]
    first = RELAY.split(". ", 1)[0] + "."
    assert part_of(seventeen, "other_account") == first + " Continuing a session on another of your accounts is your choice."
    assert part_of(seventeen, "codex") == CODEX and "OpenAI" not in str(seventeen["quotations"])
    assert not [q for q in seventeen["quotations"] if q["case"] in (None, "codex", "onto_seat")]
    # it carries the data-use clauses under its case "other_group", as point 13 does, and it is not among the texts the
    # release check requires them of
    assert set(DATA_USE) <= {q["id"] for q in seventeen["quotations"] if q["case"] == "other_group"}
    assert 17 not in risk_texts.DATA_USE_POINTS


def test_the_notice_under_the_privacy_act_is_the_pinned_one_for_an_offer_and_for_custody(keeping):
    offer, custody = notices(as_written(keeping)[12])
    assert offer == as_placeholders(OFFER_NOTICE)
    assert custody.startswith(as_placeholders(CUSTODY_BEGINS))
    assert custody.startswith("If you accept custody, then for every task handed to you while it stands,")
    sentence = as_placeholders(CUSTODY_RECORD)
    assert sentence == "{owner}'s custody record names you until 90 days after custody ends."
    assert custody.count(sentence) == 1
    # the same notice, but for its beginning and the one sentence more
    rest = " ".join(custody.replace(sentence, " ").split())
    assert rest.replace(as_placeholders(CUSTODY_BEGINS), as_placeholders(OFFER_BEGINS), 1) == offer


# the organisation notice as the texts hold it: without its first sentence, whose two places say which way
# the task goes and have no placeholder, and with the clause it quotes in brackets shown
# as the quotation "responsibility"; then its words on training, set from the data-use clauses as read
_NOTICE_FIRST, _NOTICE_REST = ORGANISATION_NOTICE.split(". ", 1)
ORGANISATION_PART = _NOTICE_REST.replace(f" ('{RESPONSIBILITY}')", "") + " " + CROSSING_TERMS
RELAY_TEXT = RELAY + " " + RELAY_LAST
_OFFER_HEAD, _OFFER_TAIL = OFFER_NOTICE.split(" Your own record", 1)
CUSTODY_NOTICE = (_OFFER_HEAD.replace(OFFER_BEGINS, CUSTODY_BEGINS, 1) + " " + CUSTODY_RECORD + " Your own record"
                  + _OFFER_TAIL)
# every part of every text, whole and in order
EXPECTED_PARTS = {
    1: [("back_home", BACK_HOME), ("back_home_off", BACK_HOME_OFF), (None, ORGANISATION_PART),
        ("onto_seat", SEAT_SETTINGS)],
    2: [(None, RELAY_TEXT)],
    3: [(None, CROSS_SWITCH), (None, ORGANISATION_PART), (None, SEAT_SETTINGS)],
    4: [(None, OTHER_GROUP), (None, ORGANISATION_PART), ("onto_seat", SEAT_SETTINGS)],
    5: [(None, ENTERPRISE), (None, ORGANISATION_PART), ("onto_seat", SEAT_SETTINGS)],
    6: [(None, ADD_ACCOUNT), ("seat", SEAT_SETTINGS)],
    7: [(None, UNWATCHED)],
    8: [(None, USAGE_CREDITS)],
    9: [(None, TO_THE_LIMIT)],
    10: [(None, API_KEY)],
    11: [(None, CAP), ("other_vendor", OTHER_VENDOR)],
    12: [(None, TEAMMATE), (None, HANDED), ("custody_cross", ORGANISATION_PART)],
    13: [(None, TEAMMATE_FOR_THE_TEAMMATE), ("custody", CUSTODIAN), (None, UNWATCHED), (None, RELAY_TEXT),
         ("other_group", ORGANISATION_PART), ("adopted_check", ADOPTED_CHECK), ("offer", OFFER_NOTICE),
         ("custody", CUSTODY_NOTICE)],
    14: [(None, LOCAL_PROVIDER)],
    15: [(None, DELETE_RECORD)],
    16: [(None, READ_BY_ITSELF)],
    17: [(None, HANDOVER), ("other_account", OTHER_ACCOUNT), ("other_group", ORGANISATION_PART),
         ("onto_seat", SEAT_SETTINGS), ("codex", CODEX)],
}


def test_the_organisation_notice_is_the_pinned_one_but_the_sentence_no_placeholder_can_fill():
    # how this file splits its copy of the notice, which EXPECTED_PARTS is built on: its first sentence holds two
    # places, which way the task goes, and is the one sentence with places; none of them is a place written
    # for a placeholder (DESIGN_PLACES, the module's PLACEHOLDERS as the pinned words write them), so
    # as_placeholders cannot turn it into words a text could keep
    assert _NOTICE_FIRST.startswith("This task started on <") and "would go on <" in _NOTICE_FIRST
    assert "<" not in _NOTICE_REST
    with pytest.raises(AssertionError):
        as_placeholders(_NOTICE_FIRST)
    # the clause it quotes in brackets is shown with its source, as the quotation of those words
    assert f" ('{RESPONSIBILITY}')" in _NOTICE_REST
    assert QUOTED["responsibility"].words == RESPONSIBILITY
    # and in the texts the module serves: neither the sentence nor the bracketed clause is in a part, and the part made
    # of the rest is shown only where the quotation of that clause is, under the same case
    for t in risk_texts.texts():
        for part in t["design"]:
            assert "would go on" not in part["text"] and RESPONSIBILITY not in part["text"], t["point"]
        held = {part["case"] for part in t["design"] if part["text"] == ORGANISATION_PART}
        if held:
            assert held <= {q["case"] for q in t["quotations"] if q["id"] == "responsibility"}, t["point"]


def test_each_point_s_parts_are_these_words_in_this_order(keeping):
    # the pinned words, and the module's own wording where it words a part itself (pinned
    # above, each named in the module's docstring): a word changed, a part dropped or added, or a sentence of the
    # module's own put among the others, fails here
    for text in as_written(keeping):
        assert [(part["case"], part["text"]) for part in text["design"]] == [
            (case, as_placeholders(words)) for case, words in EXPECTED_PARTS[text["point"]]], text["point"]
    assert set(EXPECTED_PARTS) == set(risk_texts.RISK_POINTS)


def test_the_numbers_in_the_words_are_the_task_module_s_bounds(keeping):
    from proofpane_open.ade import task_offers
    words = " ".join(part["text"] for t in as_written(keeping) for part in t["design"])
    deleted = re.findall(r"\b(\d+) days? after the task is deleted", words)
    ended = re.findall(r"\b(\d+) days? after custody ends", words)
    moves = re.findall(r"at most (\d+) times in an hour", words)
    assert deleted and set(deleted) == {str(tasks.KEEP_DAYS)}
    assert ended and set(ended) == {str(task_offers.CUSTODY_KEEP_DAYS)}
    assert moves and set(moves) == {str(tasks.MOVES_BOUND)}
    # no other number of days, nor of moves, is said
    assert len(re.findall(r"\b\d+ days?\b", words)) == len(deleted) + len(ended)
    assert len(re.findall(r"\b\d+ times\b", words)) == len(moves)


def test_every_placeholder_is_the_module_s_and_where_it_belongs(keeping):
    where = collections.defaultdict(set)
    for t in as_written(keeping):
        plain = [t["title"]] + [str(q[k]) for q in t["quotations"] for k in q]
        for words in plain:
            assert "{" not in words and "}" not in words, (t["point"], words)
        for part in t["design"]:
            for name in re.findall(r"\{([^{}]*)\}", part["text"]):
                assert name in risk_texts.PLACEHOLDERS, (t["point"], name)
                where[name].add((t["point"], part["case"]))
            assert "{" not in re.sub(r"\{[a-z_]+\}", "", part["text"]), t["point"]
            assert "}" not in re.sub(r"\{[a-z_]+\}", "", part["text"]), t["point"]
    assert {name: {point for point, _ in at} for name, at in where.items()} == {
        "operator": {13}, "record_keeping": {6, 13}, "owner": {13}, "home_group": {1}}
    assert where["operator"] == {(13, "offer"), (13, "custody")}
    assert where["record_keeping"] == {(6, None), (13, "offer"), (13, "custody")}
    assert where["owner"] == {(13, "offer"), (13, "custody")}
    assert where["home_group"] == {(1, "back_home"), (1, "back_home_off")}


# ---- the operator and the record's keeping --------------------------------------------------------------------------

@pytest.mark.parametrize("value,line", [
    ("Acme Ops, ops@acme.test", "Acme Ops, ops@acme.test"),
    ("  Acme Ops,\n  ops@acme.test  \n", "Acme Ops, ops@acme.test"),
    ("Acme Ops,\r\n\tops@acme.test", "Acme Ops, ops@acme.test"),
    ("Acme Ops," + chr(0x2028) + "ops@acme.test", "Acme Ops, ops@acme.test"),     # a line separator
    ("", None),
    ("   ", None),
    (" \n\t\r\n ", None),
])
def test_the_operator_is_one_line_or_none(monkeypatch, value, line):
    monkeypatch.setenv(OPERATOR, value)
    assert risk_texts.operator() == line


def test_the_operator_is_read_at_each_call(monkeypatch):
    assert risk_texts.operator() is None
    monkeypatch.setenv(OPERATOR, "First Ops, first@ops.test")
    assert risk_texts.operator() == "First Ops, first@ops.test"
    monkeypatch.setenv(OPERATOR, "Second Ops, second@ops.test")
    assert risk_texts.operator() == "Second Ops, second@ops.test"
    monkeypatch.delenv(OPERATOR)
    assert risk_texts.operator() is None


def test_the_texts_name_the_operator_and_leave_the_placeholder_while_there_is_none(monkeypatch):
    for offer, custody in (notices(risk_texts.text(13)), notices(risk_texts.texts()[12])):
        assert "{operator} holds it;" in offer and "{operator} holds it;" in custody
    monkeypatch.setenv(OPERATOR, "  Acme Ops,\n ops@acme.test ")
    for given in (risk_texts.text(13), risk_texts.texts()[12]):
        offer, custody = notices(given)
        assert "Acme Ops, ops@acme.test holds it;" in offer and "Acme Ops, ops@acme.test holds it;" in custody
    for t in risk_texts.texts():
        assert "{operator}" not in str(t)
        assert ("Acme Ops" in str(t)) == (t["point"] == 13), t["point"]
    monkeypatch.setenv(OPERATOR, "Other Ops, other@ops.test")
    assert "Other Ops, other@ops.test holds it;" in notices(risk_texts.text(13))[0]


def test_an_operator_s_words_are_put_in_as_they_are_never_filled_again(monkeypatch):
    value = r"Ops {record_keeping} {operator} {owner} \1 \g<0> $1"
    monkeypatch.setenv(OPERATOR, value)
    offer, custody = notices(risk_texts.text(13))
    for notice in (offer, custody):
        assert value + " holds it;" in notice
        assert "(for 90 days after the task is deleted)" in notice
        assert notice.count("{record_keeping}") == 1 and notice.count("{operator}") == 1


@pytest.mark.parametrize("days,words", [(90, "for 90 days after the task is deleted"),
                                        (1, "for 1 day after the task is deleted"),
                                        (2, "for 2 days after the task is deleted"),
                                        (400, "for 400 days after the task is deleted"),
                                        # a keeping of none: the record goes with the task, never kept for good
                                        (0, "for 0 days after the task is deleted"),
                                        (None, "for good")])
def test_the_record_s_keeping_is_said_as_the_setting_gives_it(keeping, days, words):
    keeping["days"] = days
    for six, thirteen in ((risk_texts.text(6), risk_texts.text(13)),
                          (risk_texts.texts()[5], risk_texts.texts()[12])):
        assert part_of(six, None).endswith(f"which of your accounts did each task's work {words}.")
        for notice in notices(thirteen):
            assert f"for as long as this deployment keeps such records ({words})." in notice
        assert "{record_keeping}" not in str(six) + str(thirteen)


def test_the_record_s_keeping_is_read_at_each_call(keeping):
    keeping["days"] = 90
    assert "work for 90 days after the task is deleted." in part_of(risk_texts.texts()[5], None)
    calls = keeping["calls"]
    keeping["days"] = None
    assert part_of(risk_texts.texts()[5], None).endswith("work for good.")
    assert part_of(risk_texts.text(6), None).endswith("work for good.")
    keeping["days"] = 1
    assert "(for 1 day after the task is deleted)." in notices(risk_texts.text(13))[0]
    assert keeping["calls"] >= calls + 3


UNTIL_DELETED = re.compile(r"until (\d+) days? after the task is deleted")
SOONER = ", or until this deployment deletes the record, if that is sooner"


@pytest.mark.parametrize("days", ["30", "1"])
def test_the_teammate_s_notice_promises_no_keeping_past_the_record(monkeypatch, days):
    # a deployment that keeps a task's account record fewer days after the task's deletion than the user's 90
    # (OPEN_EDITION_ADE_ACCOUNT_RECORD_DAYS, read by the tasks module's own reader) deletes the whole record before
    # them, so the notice under the Privacy Act, for an offer and for custody, says the record's keeping as the setting
    # gives it and bounds every keeping it promises after the task's deletion by the record's own end
    assert tasks.RECORD_DAYS_SETTING == "OPEN_EDITION_ADE_ACCOUNT_RECORD_DAYS"
    monkeypatch.setattr(tasks, "record_days", REAL_RECORD_DAYS)
    monkeypatch.setenv("OPEN_EDITION_ADE_ACCOUNT_RECORD_DAYS", days)
    assert tasks.record_days() == int(days)
    for given in (risk_texts.text(13), risk_texts.texts()[12]):
        for notice in notices(given):
            assert f"for as long as this deployment keeps such records (for {days} day" in notice
            promised = list(UNTIL_DELETED.finditer(notice))
            assert [m.group(1) for m in promised] == ["90"], notice              # the user's 90 days, said once
            for m in promised:
                # past the record's days: bounded, in the same clause, by the record's own deletion
                assert int(m.group(1)) > int(days)
                assert notice[m.end():].split(";", 1)[0].endswith(SOONER), notice


def test_point_13_s_digest_is_over_the_notice_that_bounds_the_user_by_the_record(keeping):
    # its digest is the one of its words with the bound: the same text without it has another
    written = as_written(keeping)[12]
    before = copy.deepcopy(written)
    for part in before["design"]:
        part["text"] = part["text"].replace(SOONER, "")
    assert sum(SOONER in part["text"] for part in written["design"]) == 2               # the offer's and custody's
    assert written["digest"] == digest_of(written) != digest_of(before)


# ---- the digest -----------------------------------------------------------------------------------------------------

def test_the_digest_is_over_the_text_as_written_whatever_the_operator_and_the_keeping(monkeypatch, keeping):
    written = as_written(keeping)
    digests = [t["digest"] for t in written]
    assert digests == [digest_of(t) for t in written]
    assert len(set(digests)) == 17
    operator_line = "Zq7 Operator {record_keeping}, zq7@example.test"
    monkeypatch.setenv(OPERATOR, operator_line)
    written = as_written(keeping, operator_line)
    assert [t["digest"] for t in written] == digests == [digest_of(t) for t in written]
    for days in (None, 1, 90):
        keeping["days"] = days
        assert [t["digest"] for t in risk_texts.texts()] == digests
        assert [risk_texts.text(n)["digest"] for n in range(1, 18)] == digests


@pytest.mark.parametrize("field,value", [
    ("words", "You may not share your Account login information with anyone else."),
    ("address", "https://www.anthropic.com/legal/consumer-terms-2026"),
    ("read_on", "2026-10-11"),
    ("source_date", "Effective October 9, 2025"),
    ("source", "Consumer Terms"),
    ("section", "4"),
])
def test_the_digest_changes_with_a_quotation_s_words_address_source_and_dates(monkeypatch, field, value):
    before = {t["point"]: t["digest"] for t in risk_texts.texts()}
    changed_quotations(monkeypatch, "sharing", **{field: value})
    after = {t["point"]: t["digest"] for t in risk_texts.texts()}
    assert {point for point in before if before[point] != after[point]} == CARRIERS["sharing"] == {6, 12, 13}


def _changed_words(spec):
    case, words = spec["design"][0]
    spec["design"][0] = (case, words + " Changed.")


def _changed_title(spec):
    spec["title"] = spec["title"] + " (changed)"


def _changed_case(spec):
    spec["design"][-1] = ("anthropic_key", spec["design"][-1][1])


@pytest.mark.parametrize("point,change", [(10, _changed_words), (15, _changed_title), (11, _changed_case),
                                          (16, _changed_words), (17, _changed_words)])
def test_the_digest_changes_with_a_text_s_own_words(monkeypatch, point, change):
    before = {t["point"]: t["digest"] for t in risk_texts.texts()}
    changed_points(monkeypatch, point, change)
    after = {t["point"]: t["digest"] for t in risk_texts.texts()}
    assert {n for n in before if before[n] != after[n]} == {point}


# ---- the release check ----------------------------------------------------------------------------------------------

def test_the_release_check_passes_when_every_source_was_read_on_or_after_the_release_s_date(monkeypatch, capsys):
    all_read(monkeypatch, RELEASE)
    # another vendor's terms, which no release reads, are no clause of the release check's: their words stay
    assert "whose terms were not read for this" in part_of(risk_texts.text(11), "other_vendor")
    assert risk_texts.release_check(RELEASE) == []
    assert risk_texts.main(["--release-check", RELEASE]) == 0
    out = capsys.readouterr()
    assert out.err == ""
    # it says only what it checked
    assert out.out.splitlines() == [f"The release check finds nothing to hold the release of {RELEASE} back."]
    later = {qid: {"read_on": day_after(RELEASE, n)} for n, qid in enumerate(IDS)}
    all_read(monkeypatch, RELEASE, **later)
    assert risk_texts.release_check(RELEASE) == []
    monkeypatch.setattr(sys, "argv", ["risk_texts", "--release-check", RELEASE])
    assert risk_texts.main() == 0


def test_the_release_check_names_every_quotation_read_before_the_release_s_date(monkeypatch):
    all_read(monkeypatch, RELEASE)
    problems = risk_texts.release_check(day_after(RELEASE))
    assert all("before" in line for line in problems), problems
    assert {named(line) for line in problems} == {(point, qid) for qid in IDS for point in CARRIERS[qid]}


@pytest.mark.parametrize("qid", IDS)
def test_the_release_check_names_one_quotation_read_the_day_before(monkeypatch, qid):
    all_read(monkeypatch, RELEASE, **{qid: {"read_on": day_after(RELEASE, -1)}})
    problems = risk_texts.release_check(RELEASE)
    assert all("before" in line and day_after(RELEASE, -1) in line for line in problems), problems
    assert {named(line) for line in problems} == {(point, qid) for point in CARRIERS[qid]}
    assert len(problems) == len(CARRIERS[qid])


@pytest.mark.parametrize("qid", ["sharing", "usage-credits", "data-use-consumer", "privacy-principle-9"])
@pytest.mark.parametrize("fields", [{"words": None, "unread": "Not read for this release; it is elsewhere."},
                                    # a reading half done: its words filled in, its mark not taken away
                                    {"unread": "Not read for this release; it is elsewhere."}])
def test_the_release_check_names_a_clause_not_read_and_its_text(monkeypatch, qid, fields):
    all_read(monkeypatch, RELEASE, **{qid: fields})
    problems = risk_texts.release_check(RELEASE)
    assert {named(line) for line in problems} == ({(point, qid) for point in CARRIERS[qid]}
                                                  | {(point, None) for point in CARRIERS[qid]})
    assert all("not read" in line for line in problems), problems
    # one line for the clause and one for its text, in each point that carries it: a clause marked unread is not
    # also said to have neither its words nor what says it was not read
    assert len(problems) == 2 * len(CARRIERS[qid]), problems


@pytest.mark.parametrize("qid", ["sharing", "data-use-commercial", "privacy-principle-6"])
def test_the_release_check_names_a_clause_whose_mark_was_left_empty(monkeypatch, qid):
    # a mark emptied, not taken away: its text counts the clause as not read (unread not None), and so does the check,
    # naming the clause beside its text in each point that carries it
    all_read(monkeypatch, RELEASE, **{qid: {"unread": ""}})
    assert all(risk_texts.text(point)["unread"] for point in CARRIERS[qid])
    problems = risk_texts.release_check(RELEASE)
    assert {named(line) for line in problems} == ({(point, qid) for point in CARRIERS[qid]}
                                                  | {(point, None) for point in CARRIERS[qid]})
    assert len(problems) == 2 * len(CARRIERS[qid]), problems
    assert all("its mark is empty" in line for line in problems if named(line)[1] == qid), problems


@pytest.mark.parametrize("fields", [
    {"address": None}, {"address": ""},
    {"read_on": None}, {"read_on": ""}, {"read_on": "12 October 2026"}, {"read_on": "2026/10/12"},
    {"words": None}, {"words": ""},
    {"source": None}, {"source": ""},
])
def test_the_release_check_names_a_quotation_without_its_source_address_date_or_words(monkeypatch, fields):
    all_read(monkeypatch, RELEASE, responsibility=fields)
    problems = risk_texts.release_check(RELEASE)
    assert {named(line) for line in problems} == {(point, "responsibility") for point in CARRIERS["responsibility"]}
    assert len(problems) == len(CARRIERS["responsibility"]), problems       # one line each


@pytest.mark.parametrize("point", (1, 3, 4, 5, 12))
@pytest.mark.parametrize("dropped", [DATA_USE, DATA_USE[:1], DATA_USE[1:]])
def test_the_release_check_names_a_data_use_clause_dropped_from_a_text_that_needs_it(monkeypatch, point, dropped):
    all_read(monkeypatch, RELEASE)

    def drop(spec):
        spec["quotations"] = [entry for entry in spec["quotations"] if entry[0] not in dropped]

    changed_points(monkeypatch, point, drop)
    assert not {q["id"] for q in risk_texts.text(point)["quotations"]} & set(dropped)
    problems = risk_texts.release_check(RELEASE)
    assert {named(line) for line in problems} == {(point, qid) for qid in dropped}


def test_the_release_check_names_a_point_with_no_text(monkeypatch):
    all_read(monkeypatch, RELEASE)
    points = copy.deepcopy(risk_texts.POINTS)
    del points[7]
    monkeypatch.setattr(risk_texts, "POINTS", points)
    assert [named(line) for line in risk_texts.release_check(RELEASE)] == [(7, None)]


@pytest.mark.parametrize("principle", PRINCIPLES)
def test_the_release_check_holds_the_act_s_principles_to_the_same_rule(monkeypatch, principle):
    # a reading of the principle left half done: its words gone and a mark that says so, its dates kept
    all_read(monkeypatch, RELEASE,
             **{principle: {"words": None, "unread": "Not read for this release; it is elsewhere."}})
    unread = risk_texts.release_check(RELEASE)
    assert unread and all(principle in line or "point 13:" in line for line in unread), unread
    assert {named(line) for line in unread} == {(13, principle), (13, None)}
    all_read(monkeypatch, RELEASE, **{principle: {"read_on": day_after(RELEASE, -1)}})
    early = risk_texts.release_check(RELEASE)
    assert {named(line) for line in early} == {(13, principle)}
    assert all("before" in line for line in early)


def test_the_release_check_passes_on_the_texts_as_built_for_the_day_of_the_reading(capsys):
    assert risk_texts.release_check(READ) == []
    assert risk_texts.main(["--release-check", READ]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"The release check finds nothing to hold the release of {READ} back."]
    # a release the day after the reading: every quotation read before it is named, in each text that carries it, and
    # nothing else is; the command line prints each of those lines, then their count
    later = risk_texts.release_check(day_after(READ))
    assert {named(line) for line in later} == {(point, qid) for qid in IDS for point in CARRIERS[qid]}
    assert len(later) == sum(len(CARRIERS[qid]) for qid in IDS)
    assert all("before" in line and READ in line for line in later), later
    assert risk_texts.main(["--release-check", day_after(READ)]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out == later + [f"{len(later)} problems hold the release of {day_after(READ)} back."]


@pytest.mark.parametrize("qid", DATA_USE + PRINCIPLES)
def test_one_clause_marked_not_read_on_the_texts_as_built_holds_the_release_back(monkeypatch, qid):
    # the near-miss of the reading: the texts as built, but one clause as it was before it was read
    changed_quotations(monkeypatch, qid, words=None, read_on=None, source_date=None,
                       unread="Not read for this release; it is elsewhere.")
    problems = risk_texts.release_check(READ)
    assert {named(line) for line in problems} == ({(point, qid) for point in CARRIERS[qid]}
                                                  | {(point, None) for point in CARRIERS[qid]})
    assert risk_texts.main(["--release-check", READ]) == 1


@pytest.mark.parametrize("qid", DATA_USE + PRINCIPLES)
def test_one_clause_read_but_still_marked_not_read_holds_the_release_back(monkeypatch, qid):
    # the nearer miss: a reading that filled the words and the date but left the mark; the mark alone holds the release
    # back, on a line that names the clause not read in each text that carries it
    mark = "Not read for this release; it is elsewhere."
    changed_quotations(monkeypatch, qid, unread=mark)
    assert risk_texts.QUOTATIONS[qid]["words"] and risk_texts.QUOTATIONS[qid]["read_on"] == READ
    problems = risk_texts.release_check(READ)
    assert {named(line) for line in problems} == ({(point, qid) for point in CARRIERS[qid]}
                                                  | {(point, None) for point in CARRIERS[qid]})
    for point in CARRIERS[qid]:
        assert f"point {point}, quotation {qid}: not read for this release ({mark})" in problems, point
    assert risk_texts.main(["--release-check", READ]) == 1


def test_the_release_check_names_point_16_as_it_names_point_7(monkeypatch):
    # as built, its clauses read before the release's date: point 16's lines are point 7's, with its own number, one
    # for each clause, and nothing more
    of = lambda point, lines: [line for line in lines if named(line)[0] == point]      # noqa: E731
    built = risk_texts.release_check(RELEASE)
    assert len(of(16, built)) == 2
    assert of(16, built) == [line.replace("point 7,", "point 16,", 1) for line in of(7, built)]
    # each of the two clauses read the day before the release: named in each text that quotes it, point 16 among them
    for qid in ("automated-use", "oauth"):
        all_read(monkeypatch, RELEASE, **{qid: {"read_on": day_after(RELEASE, -1)}})
        assert {named(line) for line in risk_texts.release_check(RELEASE)} == {(7, qid), (13, qid), (16, qid)}
    # every clause read on the release's date: point 16 holds nothing back
    all_read(monkeypatch, RELEASE)
    assert risk_texts.release_check(RELEASE) == []


def test_the_release_check_reads_no_setting_of_the_deployment(monkeypatch):
    def refuse():
        raise AssertionError("the release check read the record's keeping")

    monkeypatch.setattr(tasks, "record_days", refuse)
    unset = risk_texts.release_check(RELEASE)
    assert unset
    monkeypatch.setenv(OPERATOR, "Zq7 Operator {record_keeping}, zq7@example.test")
    assert risk_texts.release_check(RELEASE) == unset
    all_read(monkeypatch, RELEASE)
    assert risk_texts.release_check(RELEASE) == []


@pytest.mark.parametrize("day", ["2026-13-01", "20261012", "2026-10-1", "2026-02-30", "2026-W42-1", " 2026-10-12",
                                 "", None, 20261012])
def test_the_release_check_refuses_a_date_it_cannot_read(day):
    with pytest.raises(ValueError):
        risk_texts.release_check(day)


@pytest.mark.parametrize("argv", [
    [], ["--release-check"], ["--release-check", "2026-13-01"], ["--release-check", "20261012"],
    ["--release-check", "2026-10-1"], ["--release-check", "2026-W42-1"], ["--check", RELEASE], [RELEASE],
    [RELEASE, "--release-check"], ["--release-check", RELEASE, "extra"],
])
def test_main_says_how_to_run_it_and_returns_2_without_a_date_it_can_read(monkeypatch, capsys, argv):
    def refuse(day):
        raise AssertionError("the release check ran without a date it can read")

    monkeypatch.setattr(risk_texts, "release_check", refuse)
    assert risk_texts.main(argv) == 2
    captured = capsys.readouterr()
    assert "--release-check" in captured.err and "YYYY-MM-DD" in captured.err
    assert captured.out == ""


def test_main_prints_each_problem_and_returns_1(monkeypatch, capsys):
    all_read(monkeypatch, RELEASE, **{"usage-credits": {"read_on": READ}, "privacy-principle-6": {"address": ""}})
    problems = risk_texts.release_check(RELEASE)
    assert len(problems) == 2
    assert risk_texts.main(["--release-check", RELEASE]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out == problems + [f"2 problems hold the release of {RELEASE} back."]


def test_main_counts_one_problem_as_one(monkeypatch, capsys):
    all_read(monkeypatch, RELEASE, **{"usage-credits": {"read_on": READ}})          # point 8's alone
    assert len(risk_texts.release_check(RELEASE)) == 1
    assert risk_texts.main(["--release-check", RELEASE]) == 1
    assert capsys.readouterr().out.splitlines()[-1] == f"1 problem holds the release of {RELEASE} back."


def test_the_default_words_hold_the_release_back_until_they_are_set_from_the_data_use_clauses(monkeypatch, capsys):
    # every clause read on the release's date, but the organisation notice's default words put back in place of the
    # words set from the clauses as read: the check names each text that holds them, on
    # a line of its own that says no more than what it found, and the release is held back (it fails while any
    # text says so); a text's "unread" stays as the module gives it, by its quotations alone, so none is unread
    all_read(monkeypatch, RELEASE, default_words=True)
    assert not any(t["unread"] for t in risk_texts.texts())
    assert risk_texts.release_check(RELEASE) == [default_words_line(point, *DEFAULT_MARKS) for point in HOLDERS]
    assert risk_texts.main(["--release-check", RELEASE]) == 1
    assert capsys.readouterr().out.splitlines()[-1] == f"{len(HOLDERS)} problems hold the release of {RELEASE} back."
    # as the module sets them from the clauses, they pass
    all_read(monkeypatch, RELEASE)
    assert risk_texts.release_check(RELEASE) == []


@pytest.mark.parametrize("words,marks", [
    ("On a personal plan (not read again here) the consumer terms decide whether its chats may train models.",
     ("not read again",)),
    ("Not read again: whether the consumer terms let its chats train models.", ("not read again",)),
    ("Whether its chats may train models was NOT READ AGAIN for this release.", ("not read again",)),
    # the mark taken out and nothing else: the words still rest on the announcement the default words rest on
    ("On a personal plan, under the consumer terms, where (as Anthropic announced in 2025) the account's own setting "
     "decides whether its chats may be used to train models.", ("announced in 2025",)),
    ("As ANNOUNCED IN 2025, the account's own setting decides whether its chats may train models.",
     ("announced in 2025",)),
])
def test_a_rewording_that_keeps_a_default_mark_still_holds_the_release_back(monkeypatch, words, marks):
    all_read(monkeypatch, RELEASE)

    def marked_again(spec):
        spec["design"] = [(case, part.replace(CROSSING_TERMS, words)) for case, part in spec["design"]]

    changed_points(monkeypatch, 12, marked_again)
    assert risk_texts.text(12)["unread"] is False
    assert risk_texts.release_check(RELEASE) == [default_words_line(12, *marks)]


@pytest.mark.parametrize("words", [
    "Under the Consumer Terms, read again for this release, the account's own setting decides whether its chats may "
    "be used to train models.",
    "As the Commercial Terms say (read again on the release's date), work on a Team seat is not used to train models.",
])
def test_words_set_from_the_clauses_may_say_they_were_read_again(monkeypatch, words):
    # a reading's own words may well say the clauses were read again: only the default words hold the release
    # back, not every word they share with it
    all_read(monkeypatch, RELEASE)
    points = copy.deepcopy(risk_texts.POINTS)
    for spec in points.values():
        spec["design"] = [(case, part.replace(CROSSING_TERMS, words)) for case, part in spec["design"]]
    monkeypatch.setattr(risk_texts, "POINTS", points)
    assert sum(words in part["text"] for t in risk_texts.texts() for part in t["design"]) == len(HOLDERS)
    assert risk_texts.release_check(RELEASE) == []


def test_the_command_line_passes_on_the_texts_as_built_for_the_day_of_the_reading_and_no_later():
    env = dict(os.environ)
    env.pop(OPERATOR, None)

    def run(*args):
        return subprocess.run([sys.executable, "-m", "proofpane_open.ade.risk_texts", *args], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=120)

    on_the_day = run("--release-check", READ)
    assert on_the_day.returncode == 0, on_the_day.stdout + on_the_day.stderr
    assert on_the_day.stdout.splitlines() == [f"The release check finds nothing to hold the release of {READ} back."]
    later = run("--release-check", day_after(READ))
    assert later.returncode == 1, later.stderr
    assert "data-use-consumer" in later.stdout and "privacy-principle-3" in later.stdout
    assert not [line for line in later.stdout.splitlines() if "default words" in line or "not read" in line]
    bad = run("--release-check", "20261012")
    assert bad.returncode == 2 and "--release-check" in bad.stderr


# ---- the route ------------------------------------------------------------------------------------------------------

def route_paths():
    """(path, methods) of every route the app answers. FastAPI 0.143 keeps an included router as one entry of
    app.routes, its routes reached through it; an older one lists them in app.routes itself."""
    from proofpane_open.main import app
    found = []

    def walk(routes):
        for route in routes:
            contexts = getattr(route, "effective_route_contexts", None)
            if contexts is not None:
                low = getattr(route, "effective_low_priority_routes", None)
                for context in list(contexts()) + (list(low()) if low else []):
                    inner = context.starlette_route
                    path = context.path or getattr(inner, "path", "")
                    methods = context.methods or getattr(inner, "methods", None) or ()
                    found.append((path, frozenset(methods)))
                continue
            found.append((getattr(route, "path", ""), frozenset(getattr(route, "methods", None) or ())))
            walk(getattr(route, "routes", None) or [])

    walk(app.routes)
    return found


def test_no_route_but_the_texts_knows_of_a_risk_or_a_pop_up():
    paths = route_paths()
    # the walk reaches the app's routes, included or not, websockets among them
    assert ("/api/health", frozenset({"GET"})) in paths
    assert any(path == "/api/ade/workspace/terminal" for path, _ in paths)
    assert len(paths) > 100
    assert [(path, methods) for path, methods in paths if "risk" in path.lower()] == [
        ("/api/ade/risks", frozenset({"GET"}))]
    for word in ("acknowledg", "popup", "pop-up", "pop_up"):
        assert not [path for path, _ in paths if word in path.lower()], word


def test_the_route_needs_a_sign_in(client):
    assert client.get("/api/ade/risks").status_code == 401
    assert client.get("/api/ade/risks", headers={"Authorization": "Bearer not.a-token"}).status_code == 401


def test_the_route_serves_every_text_filled_at_request_time(client, monkeypatch, keeping):
    headers = signed_up(client)
    monkeypatch.setenv(OPERATOR, "First Ops, first@ops.test")
    r = client.get("/api/ade/risks", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"texts": risk_texts.texts()}
    assert [t["point"] for t in body["texts"]] == list(range(1, 18))
    assert "First Ops, first@ops.test holds it;" in notices(body["texts"][12])[0]
    assert "(for 90 days after the task is deleted)" in notices(body["texts"][12])[0]
    monkeypatch.setenv(OPERATOR, "Second Ops, second@ops.test")
    keeping["days"] = None
    body = client.get("/api/ade/risks", headers=headers).json()
    assert "Second Ops, second@ops.test holds it;" in notices(body["texts"][12])[0]
    assert part_of(body["texts"][5], None).endswith("work for good.")
    monkeypatch.delenv(OPERATOR)
    body = client.get("/api/ade/risks", headers=headers).json()
    assert body == {"texts": risk_texts.texts()}
    assert "{operator} holds it;" in notices(body["texts"][12])[0]


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_the_route_takes_nothing(client, method):
    headers = signed_up(client)
    # the route is there (the pages' mount answers 405 to any POST, so a 405 alone would not show it)
    assert client.get("/api/ade/risks", headers=headers).status_code == 200
    r = client.request(method, "/api/ade/risks", headers=headers, json={"point": 13})
    assert r.status_code == 405


@pytest.mark.parametrize("path", ["/api/ade/risks/ack", "/api/ade/risks/1", "/api/ade/risks/13",
                                  "/api/ade/risks/acknowledge"])
def test_no_other_path_under_the_route_exists(client, path):
    headers = signed_up(client)
    assert client.get("/api/ade/risks", headers=headers).status_code == 200
    assert client.get(path, headers=headers).status_code == 404
    assert client.post(path, headers=headers, json={"point": 13}).status_code in (404, 405)
