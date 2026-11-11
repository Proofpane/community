"""Notices carry no content: every notice the ADE sends is made by
push.words from push.WORDS, a fixed list, with at most a time, a count, or the id of the workspace session it opens,
and a tag that is a digest of what it is about, never its key. Nothing else reaches a push service: send and notice
refuse what words did not make, and nothing but words makes a notice (checked in the source). Then the walk: each
sender, fed rows whose names and texts all carry a mark, sends only those words, and the mark in none of them."""

import ast
import copy
import dataclasses
import hashlib
import hmac
import os
import re
import time

import pytest

from conftest import signed_up
from proofpane_open import config, store
from proofpane_open.ade import approvals, holds, push
from test_ade_push import FCM, Device, PushService

MARK = "Zq7mark"            # in every name and every text the walk's rows hold
HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HERE, "..", "proofpane_open")
TIME = r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} UTC"
AT = 1_790_000_000
SESSION = "0123456789ab"


def shapes():
    """Each kind's title, and patterns of its body and of the page it opens, from push.WORDS alone."""
    out = {}
    for kind, (title, body, url) in push.WORDS.items():
        body = re.escape(body).replace(re.escape("{at}"), TIME).replace(re.escape("{count}"), "[0-9]+")
        page = re.escape(url) + ("#session=[0-9a-f]{12}" if kind == "workspace" else "")
        out[kind] = (title, re.compile(body), re.compile(page))
    return out


def kind_of(message):
    """The kind of push.WORDS a message, as a device reads it, is: its four fields and nothing more, its tag a digest;
    None when it is no such notice."""
    if set(message) != {"title", "body", "tag", "url"} or not re.fullmatch("ade-[0-9a-f]{16}", message["tag"]):
        return None
    for kind, (title, body, url) in shapes().items():
        if message["title"] == title and body.fullmatch(message["body"]) and url.fullmatch(message["url"]):
            return kind
    return None


def keyed(key):
    """The tag words makes for `key`: an HMAC under a key derived from the token secret for tags alone."""
    derived = hmac.new(config.token_secret(), b"proofpane push notice tag v1", hashlib.sha256).digest()
    return "ade-" + hmac.new(derived, key.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def facts(kind):
    """What a kind's words take: a time, a count, a session's id."""
    body = push.WORDS[kind][1]
    return dict({"at": AT} if "{at}" in body else {}, **({"count": 3} if "{count}" in body else {}),
                **({"session": SESSION} if kind == "workspace" else {}))


# ---- the words ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", sorted(push.WORDS))
def test_each_kind_makes_its_own_fixed_words(kind):
    title, body, url = push.WORDS[kind]
    made = push.words(kind, key="approval:k1", **facts(kind))
    assert made.title == title
    assert made.body == body.format(at=holds.utc(AT), count=3)
    assert made.url == url + ("#session=" + SESSION if kind == "workspace" else "")
    assert made.tag == keyed("approval:k1")
    assert kind_of({f: getattr(made, f) for f in ("title", "body", "tag", "url")}) is not None


def test_a_held_change_s_words_say_when_it_takes_effect():
    assert push.words("held-link", at=AT).body == f"Takes effect at {holds.utc(AT)} unless you cancel it."
    assert push.words("held-e2e-code", at=AT).body == (
        f"Takes effect at {holds.utc(AT)} unless you cancel it. Made with your recovery code: only the recovery code "
        "cancels it.")
    assert push.words("more", count=4).body == "4 more things need you in the ADE."


def test_a_change_held_for_no_time_is_told_as_approved_never_as_done():
    """Told as it is placed, before it is applied (holds.settle), whose own checks may still refuse
    it, a change held for no time says what was approved and when, never that it took effect."""
    assert {k: push.WORDS[k][0] for k in push.WORDS if k.endswith("-now")} == {
        "held-link-now": "A link was approved", "held-link-replace-now": "A link to replace another was approved",
        "held-pair-now": "A session was approved to be linked",
        "held-pair-again-now": "A linked machine was approved to move to a new key",
        "held-move-now": "A move was signed", "held-app-now": "An app was allowed to connect",
        "held-e2e-now": "A change to encryption was approved", "held-other-now": "An approval was given"}
    assert {push.WORDS[k][1] for k in push.WORDS if k.endswith("-now")} == {
        "Approved at {at}, to take effect at once: this deployment holds no approval."}


@pytest.mark.parametrize("call", [
    lambda: push.words("nothing-of-the-kind"),
    lambda: push.words(MARK),
    lambda: push.words("approval", at=AT),                          # a fact its words do not take
    lambda: push.words("approval", count=1),
    lambda: push.words("approval", session=SESSION),
    lambda: push.words("held-link"),                                # one they take, left out
    lambda: push.words("held-link", at=AT, count=1),
    lambda: push.words("held-link", at=True),                       # a fact of another form
    lambda: push.words("held-link", at=str(AT)),
    lambda: push.words("held-link", at=float(AT)),
    lambda: push.words("held-link", at=-1),
    lambda: push.words("more", count="3"),
    lambda: push.words("more", count=False),
    lambda: push.words("workspace"),
    lambda: push.words("workspace", session="../evil/123"),
    lambda: push.words("workspace", session="0123456789AB"),
    lambda: push.words("workspace", session=SESSION + "\n"),
    lambda: push.words("workspace", session=int("0123456789")),
    lambda: push.words("approval", key=b"approval:k1"),
    lambda: push.words("approval", key=7),
])
def test_anything_but_a_kind_and_its_own_facts_is_refused(call):
    with pytest.raises(ValueError):
        call()


def test_the_tag_is_a_digest_of_the_key_never_the_key():
    first = push.words("link-ask", key=f"link-ask:{MARK}")
    assert MARK not in first.tag and first.tag == push.words("link-ask", key=f"link-ask:{MARK}").tag
    assert first.tag != push.words("link-ask", key="link-ask:other").tag
    assert push.words("link-ask").tag != push.words("link-ask").tag        # with no key, a tag of its own each time
    assert re.fullmatch("ade-[0-9a-f]{16}", push.words("link-ask").tag)


def test_the_tag_is_keyed_with_the_deployment_s_secret_so_no_one_can_try_keys_against_it(monkeypatch):
    """An unkeyed digest of keys built from sequence numbers, ids and names can be turned back into its
    key by trying keys, by a browser that hears the notice (one a thief subscribed, among them). The tag is an HMAC
    under a key derived from the token secret for this use alone."""
    key = "link-question:1"
    tag = push.words("link-question", key=key).tag
    assert tag != "ade-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    assert tag == keyed(key)
    # another deployment, or this one with another secret: another tag for the same key
    monkeypatch.setenv("OPEN_EDITION_TOKEN_SECRET", "another-deployment-s-secret-0123456789abcdef")
    assert push.words("link-question", key=key).tag != tag


def test_nothing_words_did_not_make_reaches_a_push_service(env):
    device, service = Device(), PushService()
    push.subscribe("u1", FCM, device.p256dh, push.b64u(device.auth), now=AT)
    (sub,) = push.subscriptions("u1")
    given = {"title": MARK, "body": MARK, "tag": "t", "url": "/app/"}
    with pytest.raises(TypeError):
        push.send(sub, given, AT, service)
    with pytest.raises(TypeError):
        push.notice("u1", given, AT, service)
    with pytest.raises(TypeError):
        push.notify("u1", [("k", given)], AT, service)
    with pytest.raises(TypeError):        # a notice made otherwise than by words, words or not
        push.send(sub, push.Words(MARK, MARK, "ade-0123456789abcdef", "/app/"), AT, service)
    # a copy of one words made, with other fields, carries no mark either
    made = push.words("link-ask", key="link-ask:k")
    with pytest.raises(TypeError):
        push.send(sub, dataclasses.replace(made, title=MARK, tag="link-ask:raw-key", url="https://evil.example/"), AT,
                  service)
    if hasattr(copy, "replace"):          # Python 3.13
        with pytest.raises(TypeError):
            push.notice("u1", copy.replace(made, body=MARK), AT, service)
    with pytest.raises(TypeError):        # nor can the constructor be given it
        push.Words(MARK, MARK, "ade-0123456789abcdef", "/app/", made.made)
    assert service.posts == []
    assert push.send(sub, push.words("test", key="test"), AT, service) == 201
    assert kind_of(device.decrypt(service.posts[0][2])) == "test"


# ---- the source: nothing but words makes a notice, and every kind a sender names is one ----------------------------

def _sources():
    for top, _, files in os.walk(SOURCE):
        for name in files:
            if name.endswith(".py"):
                path = os.path.join(top, name)
                with open(path, encoding="utf-8") as fh:
                    yield os.path.relpath(path, SOURCE), ast.parse(fh.read())


def _called(node, name):
    f = node.func
    return isinstance(f, ast.Name) and f.id == name or isinstance(f, ast.Attribute) and f.attr == name


def _kinds(node):
    """The kinds an expression names: a string, or a choice between expressions that name them; None for any other
    expression, which a sender may not give."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.IfExp):
        a, b = _kinds(node.body), _kinds(node.orelse)
        return None if a is None or b is None else a + b
    return None


# where a kind is handed on, not named: the notifier's items (each from an alert, checked below) and a hold's notice
# (from its kind's registration, test_every_held_kind_names_words_for_its_notice)
HANDED_ON = {(os.path.join("ade", "push.py"), "_item"), (os.path.join("ade", "push.py"), "items_for"),
             (os.path.join("ade", "holds.py"), "tell")}


def test_only_words_makes_a_notice_and_each_kind_a_sender_names_is_one():
    made, named, functions = [], [], set()
    for path, tree in _sources():
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                if _called(node, "Words"):
                    made.append((path, fn.name))
                given = node.args[0] if _called(node, "words") and node.args else \
                    node.args[1] if _called(node, "_item") and len(node.args) == 2 else None
                if given is not None and not (isinstance(given, ast.Name) and (path, fn.name) in HANDED_ON):
                    named.append((path, fn.name, _kinds(given)))
            if fn.name == "alerts":
                functions.add(path)
                # an alert is (its key, the kind of its notice), the kind named as words takes it: never a word of
                # its own, nor a name; a tuple a value is compared with is no alert
                compared = {id(c) for n in ast.walk(fn) if isinstance(n, ast.Compare) for c in n.comparators}
                for node in ast.walk(fn):
                    if not isinstance(node, ast.Tuple) or id(node) in compared:
                        continue
                    if len(node.elts) == 2 and _kinds(node.elts[1]) is not None:
                        named.append((path, fn.name, _kinds(node.elts[1])))
                    if len(node.elts) > 2 and isinstance(node.elts[0], ast.JoinedStr):
                        named.append((path, fn.name, None))         # (key, title, text), as alerts were
    assert made == [(os.path.join("ade", "push.py"), "words")]
    assert named and all(kinds is not None and set(kinds) <= set(push.WORDS) for _, _, kinds in named), [
        n for n in named if n[2] is None or not set(n[2]) <= set(push.WORDS)]
    assert {os.path.join("ade", f) for f in ("link.py", "link_asks.py", "link_commands.py", "agents.py",
                                             "proposals.py", "triggers.py")} <= functions


def _in_functions(tree):
    """Each node of a module's tree, with the name of the function it is in (None at the module's top)."""
    out = []

    def visit(node, fn):
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
            out.append((child, name))
            visit(child, name)
    visit(tree, None)
    return out


def test_nothing_but_words_gives_a_notice_its_mark_and_nothing_copies_or_changes_one():
    """A copy made with dataclasses.replace once kept words' mark, so any title, body, tag or url passed
    send and notice, and the walk above looks only for calls named Words. Nothing outside words names the mark or sets
    an attribute with __setattr__, and nothing in the server copies with copy or dataclasses.replace; a new use of either
    is to be judged here first."""
    found = []
    for path, tree in _sources():
        for node, fn in _in_functions(tree):
            if isinstance(node, ast.Name) and node.id == "_MADE" or isinstance(node, ast.Attribute) and \
                    node.attr == "_MADE":
                found.append((path, fn, "_MADE"))
            elif isinstance(node, ast.Attribute) and node.attr == "__setattr__":
                found.append((path, fn, "__setattr__"))
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and (
                    node.value.id == "copy" or node.value.id == "dataclasses" and node.attr == "replace"):
                found.append((path, fn, f"{node.value.id}.{node.attr}"))
            elif isinstance(node, ast.ImportFrom) and (node.module == "copy" or node.module == "dataclasses" and any(
                    a.name == "replace" for a in node.names)):
                found.append((path, fn, f"from {node.module}"))
    here = os.path.join("ade", "push.py")
    # judged: the account rule's frozen types (account_holds.py)
    # set their own fields as they are made, Placed.passkeys to a tuple and Change.takes_away to a frozenset; the
    # module imports nothing that makes a notice (held below), so neither use can reach a Words
    rule = "account_holds.py"
    assert sorted(found, key=str) == sorted([(here, None, "_MADE"), (here, "_made", "_MADE"),
                                             (here, "words", "__setattr__"), (here, "words", "_MADE"),
                                             (rule, "__post_init__", "__setattr__"),
                                             (rule, "__post_init__", "__setattr__")], key=str)
    imported = set()
    for path, tree in _sources():
        if path == rule:
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported |= {a.name for a in node.names}
                elif isinstance(node, ast.ImportFrom):
                    imported.add("." * node.level + (node.module or ""))
    assert imported == {"dataclasses", "json", "typing"}, imported      # a new import is to be judged here too


def test_every_held_kind_names_words_for_its_notice():
    from proofpane_open.ade import e2e_keys, link_asks, moves  # noqa: F401  (each registers its kinds)
    from proofpane_open import oauth  # noqa: F401
    for kind, k in holds._KINDS.items():
        if callable(k["notice"]):
            continue
        assert k["notice"] in push.WORDS and k["notice"] + "-now" in push.WORDS, kind
    # a request to link, and a change to encryption, name their words from what they hold (the tests of each)
    assert callable(holds._KINDS[link_asks.HOLD]["notice"]) and callable(holds._KINDS[e2e_keys.HOLD]["notice"])
    assert holds._KINDS[moves.HOLD]["notice"] == "held-move"
    assert holds._KINDS[oauth.HOLD]["notice"] == "held-app"


# ---- the walk ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def heard(env, monkeypatch):
    """The account's one device, and what reaches it, read as the device reads it: [messages]."""
    device, service = Device(), PushService()
    monkeypatch.setattr(push, "_default_post", service)
    monkeypatch.setattr(push, "_spawn", lambda work: work())        # a notice goes before the request returns

    def subscribe(user_id):
        push.subscribe(user_id, FCM, device.p256dh, push.b64u(device.auth))

    def messages():
        return [device.decrypt(body) for _, _, body in service.posts]
    return subscribe, messages


def _carries_nothing(messages):
    assert messages
    for m in messages:
        assert MARK.lower() not in repr(m).lower(), m
        assert kind_of(m) is not None, m


def test_the_notifier_s_items_name_nothing_they_are_about(client, heard, monkeypatch):
    from proofpane_open.ade import workspace_client, workspaces
    from test_ade_link import paired, post
    from test_ade_link_asks import address, ask
    subscribe, messages = heard
    owner = signed_up(client)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    subscribe(user_id)
    # a command an approval token asks for, the token named and the command marked
    approvals.request(approvals.hook_identity(approvals.create_token(user_id, f"the Mac {MARK}")[1]),
                      f"git push origin {MARK}")
    # a linked session named with the mark, its question, its silence; and a session asking under the mark
    s = paired(client, owner, name=f"mac {MARK}")
    assert post(s, "question", f"Which tag, {MARK}?", to="owner").status_code == 200
    with store.connect() as db:
        db.execute("UPDATE ade_links SET last_seen_at = ?, last_heartbeat_at = ? WHERE id = ?",
                   (int(time.time()) - 86400, int(time.time()) - 86400, s.link_id))
    assert ask(client, address(client, owner), name=f"cloud {MARK}")[1].status_code == 200
    # a workspace whose sessions wait, every name and word in them marked
    monkeypatch.setattr(workspaces, "for_user", lambda user: type("P", (), {"endpoint": lambda self, u: "here"})())
    sessions = [{"id": "a" * 12, "repo": MARK, "chat": {"asks": [{"id": "t1", "name": f"Bash {MARK}",
                                                                  "summary": MARK}]}},
                {"id": "b" * 12, "repo": MARK, "program": "claude", "task": MARK, "running": True,
                 "activity": {"state": "waiting", "at": AT, "tool": MARK, "kind": MARK, "message": MARK}}]
    asks = [{"id": "c" * 12, "repo": MARK, "program": "codex", "task": MARK,
             "by": {"id": "d" * 12, "title": MARK, "program": "claude"}}]
    monkeypatch.setattr(workspace_client, "call", lambda endpoint, method, path, timeout=30:
                        {"asks": asks} if path == "/asks" else {"sessions": sessions})
    # a board alert whose words are marked
    from proofpane_open.ade import ledger
    ledger.add({"id": user_id, "admin": False}, "github", f"owner/{MARK}", "main", ["t"], False)
    push._board_cache.clear()
    build = lambda conn: {"alerts": [{"level": "alert", "kind": "silence", "channel": MARK, "file": MARK,  # noqa: E731
                                      "text": MARK}]}
    # the pairing's own notice, told at once (its hold is none here), names nothing of the session either
    paired_told = messages()
    _carries_nothing(paired_told)
    assert [kind_of(m) for m in paired_told] == ["held-pair-now"]
    sent = push.tick(post=push._default_post, build=build)
    got = messages()[len(paired_told):]
    _carries_nothing(got)
    assert sent[user_id] == len(got) == push.MAX_PER_TICK            # more than a round sends become "more"
    kinds = {kind_of(m) for m in got}
    assert "more" in kinds and "approval" in kinds
    # every item, sent over rounds with no limit, carries nothing of what it is about either
    monkeypatch.setattr(push, "MAX_PER_TICK", 100)
    with store.connect() as db:
        db.execute("DELETE FROM ade_push_seen")
    before = len(messages())
    push.tick(post=push._default_post, build=build)
    every = messages()[before:]
    _carries_nothing(every)
    assert {kind_of(m) for m in every} >= {"approval", "board", "link-question", "link-look", "link-ask", "workspace",
                                           "workspace-ask"}


def test_a_change_to_how_the_account_signs_in_names_nothing_of_it(client, heard):
    from test_device_approval import approve, start, word_for
    from test_passkeys import Authenticator, add, remove, sign_out_everywhere
    subscribe, messages = heard
    owner = signed_up(client)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    subscribe(user_id)
    asked = start(client, f"iPad {MARK}")
    assert approve(client, owner, asked["code"], word_for(client, owner, asked["code"])).status_code == 200
    key = Authenticator()
    assert add(client, owner, key, name=f"Laptop {MARK}").status_code == 200
    (listed,) = client.get("/api/account/passkeys", headers=owner).json()["passkeys"]
    assert remove(client, owner, listed["id"]).status_code == 200
    assert sign_out_everywhere(client, owner).status_code == 200
    got = messages()
    _carries_nothing(got)
    assert [kind_of(m) for m in got] == ["device-signed-in", "passkey-added", "passkey-removed", "signed-out"]


def test_a_held_approval_s_notice_names_nothing_it_holds(client, heard):
    from test_ade_link_asks import address, approve, ask
    subscribe, messages = heard
    owner = signed_up(client)
    subscribe(client.get("/api/auth/me", headers=owner).json()["id"])
    _, r = ask(client, address(client, owner), name=f"cloud {MARK}")
    assert r.status_code == 200
    ask_id = client.get("/api/ade/link/sessions", headers=owner).json()["asks"][0]["id"]
    assert approve(client, owner, ask_id).status_code == 200
    got = messages()
    _carries_nothing(got)
    assert [kind_of(m) for m in got] == ["held-link"]
    hold = client.get("/api/ade/link/sessions", headers=owner).json()["asks"][0]
    assert got[0]["body"] == f"Takes effect at {holds.utc(hold['takes_effect_at'])} unless you cancel it."


def test_an_app_s_connection_names_nothing_of_the_app(client, heard, monkeypatch):
    import connector_fixtures as cf
    monkeypatch.setenv("OPEN_EDITION_CONNECTOR", "on")
    subscribe, messages = heard
    owner = signed_up(client)
    subscribe(client.get("/api/auth/me", headers=owner).json()["id"])
    app = cf.register(client, name=f"Claude {MARK}")
    verifier, challenge = cf.pkce()
    rid = cf.request_id(cf.authorize(client, app, challenge))
    cf.turn_on(client, owner)
    assert cf.allow(client, owner, rid).status_code == 200
    got = messages()
    _carries_nothing(got)
    assert [kind_of(m) for m in got] == ["apps-on", "held-app"]
