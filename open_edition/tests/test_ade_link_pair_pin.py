"""A session linked from the page pins its owner's passkeys as it links (ade_link.py pair --keys): the ticket carries
the account's passkey fingerprints, the page puts them in the pair command the owner runs in the session, and pair
pins the keys the ADE's answer offers by those fingerprints, each checked against its own public key. No one is asked
to tell a session to pin. Against the app served on a local port, with the tests' own software authenticator."""

import json

import pytest

import ade_link
from test_ade_link import at_once, confirmed
from test_ade_link_client import link_row, pair, run, served, ticket  # noqa: F401  (served: a fixture)
from test_ade_link_client_commands import command, device, no_inbox, owner  # noqa: F401  fixtures
from test_passkeys import Authenticator, add


def _ticket(client, owner):
    r = client.post("/api/ade/link/tickets", headers=owner, json={"confirmation": confirmed(client, owner)})
    assert r.status_code == 200, r.text
    return r.json()


def _fingerprints(client, owner):
    return [k["fingerprint"] for k in client.get("/api/account/passkeys", headers=owner).json()["passkeys"]]


def _pair(capsys, served, tk, d, keys=None):
    argv = ["pair", "--url", served, "--ticket", tk["ticket"], "--name", "mac", "--kind", "local", "--dir", d]
    if keys is not None:
        argv += ["--keys", keys]
    with at_once():                         # linked as it pairs: the wait is test_ade_link_pair_wait.py's
        return run(capsys, *argv)


def test_the_ticket_carries_the_account_s_passkey_fingerprints(client, owner, served, device):
    tk = _ticket(client, owner)
    assert tk["keys"] == _fingerprints(client, owner) and len(tk["keys"]) == 1


def test_a_session_linked_from_the_page_verifies_its_owner_s_commands_without_being_told_to_pin(client, owner,
                                                                                                  served, device,
                                                                                                  capsys, tmp_path):
    tk = _ticket(client, owner)
    d = tmp_path / "mac"
    code, out, err = _pair(capsys, served, tk, d, ",".join(tk["keys"]))
    assert code == 0, err
    assert f"ADE: pinned {tk['keys'][0]}" in out and "commands signed with them are verified" in out
    kept = json.loads((d / "owner_keys.json").read_text())
    assert kept["source"] == "pair" and [k["fingerprint"] for k in kept["keys"]] == tk["keys"]
    seq = command(client, owner, served, device, link_row("mac")["id"])
    assert run(capsys, "--dir", d, "show", seq)[0] == 0                # verified: no pin step came between


def test_pairing_pins_only_the_keys_its_command_names(client, owner, served, device, capsys, tmp_path):
    second = Authenticator(alg=-8, rp="127.0.0.1", origin=served)
    assert add(client, owner, second, confirm_with=device).status_code == 200
    first, other = _fingerprints(client, owner)
    tk = _ticket(client, owner)
    d = tmp_path / "mac"
    code, out, _ = _pair(capsys, served, tk, d, f"{first.upper()}, {'0' * 32}")       # as typed, and one not the ADE's
    assert code == 0 and f"ADE: pinned {first}" in out and f"not pinned {'0' * 32}" in out
    assert [k["fingerprint"] for k in json.loads((d / "owner_keys.json").read_text())["keys"]] == [first]
    seq = command(client, owner, served, second, link_row("mac")["id"])
    assert run(capsys, "--dir", d, "show", seq)[0] == 7                # the key it did not name is not pinned


def test_a_key_whose_fingerprint_the_ade_misstates_gets_nothing_pinned_and_pairing_stands(client, owner, served,
                                                                                        device, capsys, tmp_path,
                                                                                        monkeypatch):
    tk = _ticket(client, owner)
    real = ade_link.Link.request

    def lying(self, method, path, body=None, **k):
        answer = real(self, method, path, body, **k)
        if path == "/pair":
            answer["owner_keys"]["passkeys"][0]["fingerprint"] = tk["keys"][0][::-1]
        return answer
    monkeypatch.setattr(ade_link.Link, "request", lying)
    d = tmp_path / "mac"
    code, out, err = _pair(capsys, served, tk, d, tk["keys"][0][::-1])
    assert code == 0 and out.startswith("ADE: paired as mac") and "nothing was pinned" in err
    assert not (d / "owner_keys.json").exists() and json.loads((d / "config.json").read_text())["link_id"]


def test_a_pin_held_outside_the_server_decides_at_pairing_too(client, owner, served, device, capsys, tmp_path,
                                                              monkeypatch):
    tk = _ticket(client, owner)
    monkeypatch.setenv("ADE_OWNER_KEYS", "a" * 32)
    d = tmp_path / "mac"
    code, out, _ = _pair(capsys, served, tk, d, tk["keys"][0])
    assert code == 0 and "ADE: pinned" not in out and "nothing was pinned" in out
    assert not (d / "owner_keys.json").exists()                          # the pin outside says which, when pin runs


def test_a_pairing_without_keys_pins_nothing_as_before(client, owner, served, device, capsys, tmp_path):
    tk = _ticket(client, owner)
    d = tmp_path / "mac"
    code, out, _ = _pair(capsys, served, tk, d)
    assert code == 0 and "pinned" not in out and not (d / "owner_keys.json").exists()


def test_a_later_pin_keeps_what_pairing_pinned(client, owner, served, device, capsys, tmp_path):
    tk = _ticket(client, owner)
    d = tmp_path / "mac"
    _pair(capsys, served, tk, d, tk["keys"][0])
    code, out, _ = run(capsys, "--dir", d, "pin")
    assert code == 0 and "trusting the server" not in out
    kept = json.loads((d / "owner_keys.json").read_text())
    assert kept["source"] == "pair" and [k["fingerprint"] for k in kept["keys"]] == tk["keys"]


def test_linking_again_keeps_what_its_directory_pinned_and_pins_no_key_the_server_added(client, owner, served,
                                                                                         device, capsys, tmp_path):
    """The fingerprints in a link command are the server's word: a directory that pinned keys before (linking it again,
    or a name used before) keeps those, and a key new since is pinned only by pin --add, as pin holds it."""
    tk = _ticket(client, owner)
    d = tmp_path / "mac"
    assert _pair(capsys, served, tk, d, tk["keys"][0])[0] == 0
    second = Authenticator(alg=-8, rp="127.0.0.1", origin=served)
    assert add(client, owner, second, confirm_with=device).status_code == 200
    first, added = _fingerprints(client, owner)
    link_id = link_row("mac")["id"]
    confirmation = confirmed(client, owner, f"link-ticket:{link_id}")       # a re-pair names the link it pairs again
    again = client.post("/api/ade/link/tickets", headers=owner,
                        json={"confirmation": confirmation, "link_id": link_id}).json()
    assert set(again["keys"]) == {first, added}
    code, out, _ = _pair(capsys, served, again, d, ",".join(again["keys"]))
    assert code == 0 and f"not pinned {added}: this directory pinned keys before" in out
    assert [k["fingerprint"] for k in json.loads((d / "owner_keys.json").read_text())["keys"]] == [first]
    code, out, _ = run(capsys, "--dir", d, "pin", "--add", added)          # the owner's word, in the session's window
    assert code == 0 and f"ADE: pinned {added}" in out


def test_linking_again_keeps_the_second_screen_rule_and_never_sets_it(client, owner, served, device, capsys, tmp_path):
    """The second screen's rule (pin --second-screen on) is set in the session alone and kept by a later pin; linking
    the directory again with the page's line (--keys) keeps it too, and says so, so a pair command never lifts it
    unseen; and a directory without the rule gets none from linking again."""
    tk = _ticket(client, owner)
    d = tmp_path / "mac"
    assert _pair(capsys, served, tk, d, tk["keys"][0])[0] == 0
    assert run(capsys, "--dir", d, "pin", "--second-screen", "on")[0] == 0
    link_id = link_row("mac")["id"]

    def again():
        made = client.post("/api/ade/link/tickets", headers=owner, json={
            "confirmation": confirmed(client, owner, f"link-ticket:{link_id}"), "link_id": link_id}).json()
        return _pair(capsys, served, made, d, ",".join(made["keys"]))

    code, out, _ = again()
    kept = json.loads((d / "owner_keys.json").read_text())
    assert code == 0 and kept["second_screen"] is True and "the second screen's rule stays on here" in out
    assert [k["fingerprint"] for k in kept["keys"]] == tk["keys"] and kept["source"] == "pair"
    assert run(capsys, "--dir", d, "pin", "--second-screen", "off")[0] == 0
    code, out, _ = again()
    assert code == 0 and "second_screen" not in json.loads((d / "owner_keys.json").read_text())
    assert "second screen" not in out


def test_a_pairing_that_names_no_key_the_ade_lists_writes_no_pin(client, owner, served, device, capsys, tmp_path):
    tk = _ticket(client, owner)
    d = tmp_path / "mac"
    code, out, _ = _pair(capsys, served, tk, d, "0" * 32)
    assert code == 0 and "nothing was pinned" in out and not (d / "owner_keys.json").exists()
    code, out, _ = run(capsys, "--dir", d, "pin")                          # so a first pin still works as one
    assert code == 0 and "trusting the server for these keys, once" in out


def test_pair_takes_a_fingerprint_of_32_hex_digits_and_spends_no_ticket_on_another(client, owner, served, device, capsys,
                                                                                  tmp_path):
    tk = _ticket(client, owner)
    d = tmp_path / "mac"
    with pytest.raises(SystemExit) as e:
        _pair(capsys, served, tk, d, tk["keys"][0] * 2)                      # a whole sha256 is not a fingerprint
    assert e.value.code == 2 and "is not a passkey's fingerprint (32 hex digits" in capsys.readouterr().err
    assert not d.exists()                                                   # checked before any request
    code, out, err = _pair(capsys, served, tk, d, tk["keys"][0])            # the same ticket, unspent
    assert code == 0 and (d / "owner_keys.json").exists(), err
