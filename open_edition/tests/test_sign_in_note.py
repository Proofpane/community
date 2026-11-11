"""The older pages' sign-in note (web/index.html) is an operator's setting, OPEN_EDITION_SIGN_IN_NOTE: that page holds
no note of its own and shows one only where it is set, from GET /api/auth/sign-in-note, which needs no sign-in and
reads the setting at each request as one line; the ADE app's sign-in (ui/src/components/SignIn.tsx) holds none either.
The example deployment, fly.toml, sets the hosted Community's words."""

import os
import re
import tomllib

from conftest import ROOT

SETTING = "OPEN_EDITION_SIGN_IN_NOTE"
HOSTED = "This edition is by invitation, free to use, and carries no service commitment."


def _text(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def test_unset_the_route_gives_no_note(client, monkeypatch):
    monkeypatch.delenv(SETTING, raising=False)
    r = client.get("/api/auth/sign-in-note")
    assert r.status_code == 200 and r.json() == {"note": None}


def test_white_space_alone_is_no_note(client, monkeypatch):
    monkeypatch.setenv(SETTING, "  \t \n \r ")
    assert client.get("/api/auth/sign-in-note").json() == {"note": None}


def test_set_the_route_gives_it_as_one_line_read_at_each_request(client, monkeypatch):
    monkeypatch.setenv(SETTING, "  By invitation,\n\t and \r\n  no service commitment.  ")
    assert client.get("/api/auth/sign-in-note").json() == {"note": "By invitation, and no service commitment."}
    monkeypatch.setenv(SETTING, "Another line.")
    assert client.get("/api/auth/sign-in-note").json() == {"note": "Another line."}


def test_the_page_holds_no_note_of_its_own_and_a_place_for_the_operator_s_in_its_sign_in():
    page = re.sub(r"\s+", " ", _text("web", "index.html"))
    assert "service commitment" not in page and "free to use" not in page
    begins = page.index('<section id="signin"')
    sign_in = page[begins:page.index("</section>", begins)]
    assert '<p class="note" id="signin-note" hidden></p>' in sign_in
    assert '<p class="note">If you were invited, open the link you were sent.</p>' in sign_in


def test_the_page_s_script_asks_for_the_note_where_it_runs_and_shows_it_as_text_only_where_one_is_set():
    script = _text("web", "app.js")
    assert script.count('"/api/auth/sign-in-note"') == 1
    # in start(), at its top level, past the visit sent on to the ADE app and before the invitation's branch
    begins = script.index("(async function start() {")
    sent_on = script.index('location.replace("/app/" + location.hash); return; }', begins)
    at = script.index('\n  api("GET", "/api/auth/sign-in-note")', begins)
    assert sent_on < at < script.index("\n  if (m) {", begins)
    near = script[at:at + 400]
    assert 'textContent = r.note' in near and "innerHTML" not in near and "hidden = !r.note" in near


def test_the_ade_app_s_sign_in_holds_no_note_of_its_own():
    sign_in = re.sub(r"\s+", " ", _text("ui", "src", "components", "SignIn.tsx"))
    assert "service commitment" not in sign_in and "free to use" not in sign_in


def test_the_example_deployment_sets_the_hosted_community_s_note():
    assert tomllib.loads(_text("fly.toml"))["env"].get(SETTING) == HOSTED
