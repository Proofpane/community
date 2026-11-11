"""Each request's body is checked before anything reads it (main.py, _NestingLimit): one larger than MAX_BODY is
refused (413), and JSON in anything but UTF-8 or nested past MAX_NESTING is refused (400), on every Python (3.13's
reader ran out of stack on a body thousands deep, 3.14's reads it whole). Every body is checked, whatever its type
says, but a file's bytes sent to a session."""

import json

from conftest import signed_up

TOO_DEEP = {"detail": "The request nests too deep."}


def test_how_deep_a_body_nests_is_read_from_its_brackets_outside_its_strings():
    from proofpane_open import main
    assert not main.nests_too_deep(b"")
    assert not main.nests_too_deep(b'{"a": [1, {"b": []}]}', limit=4)      # an object, an array, an object, an array
    assert main.nests_too_deep(b'{"a": [1, {"b": []}]}', limit=3)
    deep = b"[" * 65 + b"]" * 65
    assert main.nests_too_deep(deep) and not main.nests_too_deep(b"[" * 64 + b"]" * 64)
    # brackets inside strings, escaped quotes among them, are text, not nesting
    text = b'{"text": "' + b"[{" * 200 + b'\\" still text ' + b"}]" * 200 + b'"}'
    assert not main.nests_too_deep(text)
    assert main.nests_too_deep(b'["\\\\", ' + b"[" * 70 + b"]" * 70 + b"]")      # an escaped backslash ends the string
    assert not main.nests_too_deep(b'["' + b"[" * 70)                              # a string never closed is all string
    assert main.nests_too_deep(b"[" * 65 + b'"')                                     # but not what came before it
    # what is not a quote, a backslash or a bracket is passed over, multibyte UTF-8 among it
    assert main.nests_too_deep(("[\u00e9" * 65).encode("utf-8"))


def test_a_large_body_is_checked_quickly_whatever_is_in_it():
    import time
    from proofpane_open import main
    shallow = (b"[" * 64 + b"]" * 64) * (main.MAX_BODY // 128)              # as many brackets as fit, none too deep
    began = time.monotonic()
    assert not main.nests_too_deep(shallow)
    assert time.monotonic() - began < 10
    as_text = json.dumps({"text": json.dumps([[1, {"a": [2]}]] * 100000)}).encode()
    began = time.monotonic()
    assert not main.nests_too_deep(as_text)                                  # the brackets are in a string
    assert time.monotonic() - began < 2


def test_a_body_nested_too_deep_is_refused_on_any_route_before_it_is_read(client):
    headers = signed_up(client)
    # 100 deep: past the limit, and well within what any Python's reader takes, so it is this check that refuses it
    deep = b"[" * 100 + b"]" * 100
    for method, path in (("POST", "/api/ade/workspace/sessions"), ("POST", "/api/auth/login"),
                         ("PUT", "/api/ade/workspace/sessions/0123456789ab/file")):
        r = client.request(method, path, content=deep, headers=dict(headers, **{"Content-Type": "application/json"}))
        assert (r.status_code, r.json()) == (400, TOO_DEEP), (path, r.text)
    # whatever its type says, or with none (which FastAPI reads as JSON), or with two
    for kind in ({}, {"Content-Type": "text/plain"}, {"Content-Type": "application/octet-stream"}):
        r = client.post("/api/auth/login", content=deep, headers=kind)
        assert (r.status_code, r.json()) == (400, TOO_DEEP), kind
    r = client.post("/api/auth/login", content=deep,
                    headers=[("Content-Type", "application/json"), ("Content-Type", "text/plain")])
    assert (r.status_code, r.json()) == (400, TOO_DEEP)
    # the session channel's routes read their bodies as JSON whatever they say
    r = client.post("/api/ade/link/pair", content=deep, headers={"Content-Type": "text/plain"})
    assert (r.status_code, r.json()) == (400, TOO_DEEP)
    # a JSON body that nests no deeper than the limit goes on
    r = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "correct horse battery"})
    assert r.status_code == 200 and r.json()["token"]


def test_a_body_in_another_encoding_or_too_large_is_refused(client):
    login = {"email": "admin@example.com", "password": "correct horse battery"}
    signed_up(client)
    for encoding in ("utf-16", "utf-16-le", "utf-32-be"):
        r = client.post("/api/auth/login", content=json.dumps(login).encode(encoding),
                        headers={"Content-Type": "application/json"})
        assert (r.status_code, r.json()) == (400, {"detail": "The request is not UTF-8."}), encoding
    r = client.post("/api/auth/login", content=b"\xef\xbb\xbf" + json.dumps(login).encode(),
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 200                                              # UTF-8 with its byte order mark is UTF-8
    from proofpane_open import main
    r = client.post("/api/auth/login", content=b" " * (main.MAX_BODY + 1), headers={"Content-Type": "application/json"})
    assert (r.status_code, r.json()) == (413, {"detail": "The request is larger than the server takes."})
    # a large body, under the limit, is checked off the event loop and goes on
    r = client.post("/api/auth/login", content=json.dumps(dict(login, padding="x" * (2 * main.MAX_BODY_INLINE))),
                    headers={"Content-Type": "application/json"})
    assert r.status_code != 413 and r.json() != TOO_DEEP
    # the routes that take a file's text take more; nothing else does, and a 404 is no way round it
    big = json.dumps({"text": "x" * (main.MAX_BODY + 10)})
    assert client.post("/api/check/manifest", content=big, headers={"Content-Type": "application/json"}).status_code != 413
    for path in ("/api/auth/login", "/api/no/such/route", "/api/check/manifest/../../auth/login"):
        assert client.post(path, content=big, headers={"Content-Type": "application/json"}).status_code == 413, path


def test_half_a_pair_is_found_in_any_string_without_building_the_body():
    from proofpane_open import main
    huge = b"[" + b",".join([b'"x"', b"[]"] * 50000) + b', "\\ud800"]'
    assert main.holds_half_a_pair(huge)
    assert not main.holds_half_a_pair(b"[" + b",".join([b"[]"] * 50000) + b', "\\ud83d\\ude80"]')
    calls = []
    real = main.json.loads
    main.json.loads = lambda x, *a, **k: calls.append(len(x)) or real(x, *a, **k)
    try:
        main.holds_half_a_pair(huge)
    finally:
        main.json.loads = real
    assert calls and max(calls) < 100                 # only its strings were read, never the whole body


def test_a_body_holding_half_of_a_character_is_refused(client):
    from proofpane_open import main
    half = {"detail": "The request holds half of a character (an unpaired surrogate), which is not text."}
    signed_up(client)
    for text in (b'"a\\ud83d"', b'"\\ude80b"', b'"\\ude80\\ud83d"', b'"a\xed\xa0\xbd"'):
        body = b'{"email": "admin@example.com", "password": ' + text + b"}"
        r = client.post("/api/auth/login", content=body, headers={"Content-Type": "application/json"})
        assert (r.status_code, r.json()) == (400, half), body
    # a whole pair, escaped or not, is one character; a backslash before "ud83d" is a backslash; text that is not JSON
    # is the route's to judge
    assert not main.holds_half_a_pair(b'{"a": "\\ud83d\\ude80 and ' + "\U0001F680".encode() + b'"}')
    assert not main.holds_half_a_pair(b'{"a": "\\\\ud83d"}')
    assert not main.holds_half_a_pair(b'{"a": "\\ud83d')
    assert main.holds_half_a_pair(b'{"\\udfff": 1}')                     # a key is text too


def test_a_file_sent_to_a_session_is_its_route_s_to_check(client):
    headers = signed_up(client)
    r = client.post("/api/ade/workspace/sessions/0123456789ab/upload?name=deep.json", content=b"[" * 100 + b"]" * 100,
                    headers=dict(headers, **{"Content-Type": "application/json"}))
    assert r.json() != TOO_DEEP
