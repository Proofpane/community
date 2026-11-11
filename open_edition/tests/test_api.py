"""The HTTP surface: who may call what, and that each tool's answer is the open packages' answer."""

import json
import os
import re

import pytest

from conftest import ADMIN, DIFF, ROOT, signed_up


def test_health_and_the_public_key_need_no_account(client):
    assert client.get("/api/health").json()["ok"] is True
    k = client.get("/api/public-key").json()
    assert k["alg"] == "id-MLDSA65-Ed25519-SHA512" and k["key"].startswith("sha256:")


@pytest.mark.parametrize("method,path,body", [
    ("post", "/api/verify", {"diff": DIFF}),
    ("post", "/api/check/manifest", {"text": "{}"}),
    ("post", "/api/check/chain", {"text": "x"}),
    ("post", "/api/check/signature", {"value": {}, "signature": {}}),
    ("get", "/api/auth/me", None),
    ("get", "/api/admin/invitations", None),
    ("post", "/api/admin/invitations", {"email": "x@example.org"}),
])
def test_every_tool_needs_an_account(client, method, path, body):
    kwargs = {"json": body} if body is not None else {}
    assert getattr(client, method)(path, **kwargs).status_code == 401
    bad = {"Authorization": "Bearer not.a-token"}
    assert getattr(client, method)(path, headers=bad, **kwargs).status_code == 401


def test_only_an_admin_creates_invitations(client):
    member = signed_up(client, "member@example.org")
    assert client.post("/api/admin/invitations", json={"email": "x@example.org"}, headers=member).status_code == 403
    assert client.get("/api/admin/invitations", headers=member).status_code == 403
    admin = signed_up(client, ADMIN)
    r = client.post("/api/admin/invitations", json={"email": "x@example.org"}, headers=admin)
    assert r.status_code == 200
    link = r.json()["link"]
    assert link.startswith("https://open.example.test/#invite=")
    token = link.split("#invite=")[1]
    assert client.post("/api/auth/invitation", json={"token": token}).json() == {"email": "x@example.org"}
    listed = client.get("/api/admin/invitations", headers=admin).json()["invitations"]
    assert any(i["email"] == "x@example.org" for i in listed)
    assert token not in json.dumps(listed)


def test_the_invitation_link_signs_the_invitee_up_once(client):
    admin = signed_up(client, ADMIN)
    token = client.post("/api/admin/invitations", json={"email": "y@example.org"}, headers=admin).json()["link"].split("=")[1]
    r = client.post("/api/auth/register", json={"token": token, "password": "a good long password"})
    assert r.status_code == 200 and r.json()["user"]["email"] == "y@example.org"
    assert client.post("/api/auth/register", json={"token": token, "password": "a good long password"}).status_code == 400
    assert client.post("/api/auth/invitation", json={"token": token}).status_code == 404
    me = client.get("/api/auth/me", headers={"Authorization": "Bearer " + r.json()["token"]}).json()
    assert me["email"] == "y@example.org" and me["admin"] is False
    assert me["can_sign_in_again"] is True                 # with its password


def test_verify_returns_the_manifest_the_open_verifier_rederives(client):
    h = signed_up(client)
    out = client.post("/api/verify", json={"title": "Fix f", "body": "Adds tests. No schema change. All tests pass.",
                                           "diff": DIFF}, headers=h).json()
    m = out["manifest"]
    assert m["subject"]["repo"] == "pasted/diff" and m["subject"]["head_sha"] == "0" * 40
    by_id = {o["id"]: o for o in m["obligations"]}
    claims = {o["claim"]["pattern"]: o["outcome"] for o in by_id.values() if o["origin"] == "claimed"}
    assert claims["tests_added"] == "pass" and claims["no_schema"] == "pass"
    assert claims["tests_pass"] == "unverified"  # a pasted diff has no CI: never passed by default
    assert out["decision"]["verify"] == "failure"
    checked = client.post("/api/check/manifest", json={"text": json.dumps(m)}, headers=h).json()
    assert checked["ok"] is True and checked["decision"] == out["decision"] and checked["digest"] == out["digest"]
    sig = client.post("/api/check/signature", json={"value": m, "signature": out["signature"]}, headers=h).json()
    assert sig["ok"] is True
    m["obligations"][0]["outcome"] = "fail"
    assert client.post("/api/check/signature", json={"value": m, "signature": out["signature"]}, headers=h).json()["ok"] is False


def test_verify_takes_a_repository_and_commits_when_given(client):
    h = signed_up(client)
    out = client.post("/api/verify", json={"diff": DIFF, "repo": "acme/tool", "base_sha": "a" * 40,
                                           "head_sha": "b" * 40}, headers=h).json()
    assert out["manifest"]["subject"]["repo"] == "acme/tool" and out["manifest"]["subject"]["base_sha"] == "a" * 40
    bad = client.post("/api/verify", json={"diff": DIFF, "repo": "not a repo"}, headers=h)
    assert bad.status_code == 400 and "subject-repo" in bad.json()["detail"]


def test_verify_refuses_what_is_not_a_git_diff_or_is_too_big(client):
    h = signed_up(client)
    assert client.post("/api/verify", json={"diff": "hello"}, headers=h).status_code == 400
    assert client.post("/api/verify", json={"diff": ""}, headers=h).status_code == 400
    damaged = client.post("/api/verify", json={"diff": " " + DIFF}, headers=h)
    assert damaged.status_code == 400 and "line 1 starts with a space" in damaged.json()["detail"]
    huge = DIFF + "+x\n" * 400_000
    r = client.post("/api/verify", json={"diff": huge}, headers=h)
    assert r.status_code == 400 and "longer than" in r.json()["detail"]


def test_check_manifest_reports_what_the_offline_verifier_reports(client):
    h = signed_up(client)
    out = client.post("/api/check/manifest", json={"text": '{"a": 1, "a": 2}'}, headers=h).json()
    assert out["ok"] is False and any("not-canonical" in e for e in out["errors"])
    assert all("/tmp" not in e and "upload-" not in e for e in out["errors"])
    assert client.post("/api/check/manifest", json={"text": ""}, headers=h).status_code == 400


def test_check_chain_runs_the_chain_verifier(client):
    import os
    from proofpane_open import paths
    h = signed_up(client)
    vectors = os.path.join(paths.OSS_ROOT, "core", "vectors", "chain")
    with open(os.path.join(vectors, "index.json"), encoding="utf-8") as fh:
        index = json.load(fh)
    case = next(c for c in index if c["file"] == "three.jsonl")
    assert case["valid"] and case["start_seq"] == 0 and case["lines"] == 3
    with open(os.path.join(vectors, case["file"]), encoding="utf-8", newline="") as fh:
        text = fh.read()
    out = client.post("/api/check/chain", json={"text": text}, headers=h).json()
    assert out == {"ok": True, "lines": 3, "head": case["head"], "next_seq": case["next_seq"], "errors": []}
    tampered = text.replace('"path":"~/.ssh/id_ed25519"', '"path":"~/.ssh/id_rsa"', 1)
    assert tampered != text
    broken = client.post("/api/check/chain", json={"text": tampered}, headers=h).json()
    assert broken["ok"] is False and broken["errors"]


def test_pages_are_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Proofpane" in r.text
    assert client.get("/app.js").status_code == 200


def test_the_invitation_token_never_travels_in_a_url(client):
    # an access log writes the request line, query string included; the body it does not
    admin = signed_up(client, ADMIN)
    token = client.post("/api/admin/invitations", json={"email": "z@example.org"}, headers=admin).json()["link"].split("#invite=")[1]
    asked = client.get("/api/auth/invitation", params={"token": token})
    assert asked.status_code != 200 and "z@example.org" not in asked.text
    assert client.post("/api/auth/invitation", json={"token": token}).json() == {"email": "z@example.org"}
    assert client.post("/api/auth/invitation", params={"token": token}).status_code == 422  # the body, not the query
    with open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8") as fh:
        script = fh.read()
    assert re.search(r'api\("POST", "/api/auth/invitation", \{ token: inviteToken \}\)', script)
    assert "?token" not in script and "token=" not in script


def test_pages_are_revalidated_so_a_deploy_replaces_them(client):
    for path in ("/", "/app.js", "/style.css"):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache", path
        again = client.get(path, headers={"If-None-Match": r.headers["etag"]})
        assert again.status_code == 304 and again.headers["cache-control"] == "no-cache", path
