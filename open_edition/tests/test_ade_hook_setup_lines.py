"""The lines the page gives with a new hook token (web/app.js, the Approvals panel's setup text), run through node with
an answer of the test's own. The approval block's `env` outranks the session's environment, so the address goes in the
block, as the line to put in its `env`, and not in an `export`, which would not reach the hook; the interpreter is
named by the real binary's path; and the block holds only in the folder it is put in, the two blocks in one file named
for a folder that takes both."""

import json
import os
import re
import shutil
import subprocess

import pytest

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
NODE = shutil.which("node")
START = '$("ade-hook-setup").textContent = '


def _setup_lines(out):
    with open(os.path.join(HERE, "web", "app.js"), encoding="utf-8") as fh:
        script = fh.read()
    start = script.index(START + "[")
    array = script[start + len(START):script.index('].join("\\n");', start) + 1]
    probe = f"const out = {json.dumps(out)};\nprocess.stdout.write(JSON.stringify({array}.join('\\n')));\n"
    run = subprocess.run([NODE, "-e", probe], capture_output=True, text=True, check=True, timeout=60)
    return json.loads(run.stdout).split("\n")


@pytest.mark.skipif(NODE is None, reason="needs node to run the page's lines")
def test_the_setup_lines_put_the_address_in_the_block_and_say_where_the_block_holds():
    lines = _setup_lines({"token": "adeh_test", "url": "https://ade.example.test"})
    assert lines[0] == "mkdir -p ~/.proofpane-ade && umask 077 && printf %s 'adeh_test' > ~/.proofpane-ade/hook-token"
    assert all(line.startswith("#") for line in lines[1:])      # pasted into a shell, only the first line runs
    text = "\n".join(lines)
    assert '"PROOFPANE_ADE_URL": "https://ade.example.test",' in text
    assert "export " not in text
    assert "python3 -c 'import os,sys; print(os.path.realpath(sys.executable))'" in text
    assert "holds only in that folder" in text
    named = set(re.findall(r"\b[\w.]+\.settings\.json\b", text))
    assert named == {"ade_hook.settings.json", "ade_link_and_hook.settings.json"}
    assert all(os.path.exists(os.path.join(HERE, name)) for name in named)
