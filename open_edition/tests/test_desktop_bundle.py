"""What the desktop app carries (desktop/bundle.py): the server, the workspace agent and the built ADE laid out as the
hosted image lays them out, so the open packages are found beside them, without tests; the UI built every time; and
the pinned Python, refused when a download's digest is not the pinned one."""

import importlib.util
import io
import os
import re
import subprocess
import sys

import pytest

from conftest import ROOT


def _bundle():
    spec = importlib.util.spec_from_file_location("desktop_bundle", os.path.join(ROOT, "desktop", "bundle.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_app_is_laid_out_as_the_hosted_image_lays_it_out(tmp_path, monkeypatch):
    bundle = _bundle()
    monkeypatch.setattr(bundle, "OUT", str(tmp_path / "resources"))
    ran, real_run = [], subprocess.run          # bundle's subprocess is this one: the check below runs the real one
    monkeypatch.setattr(bundle.subprocess, "run", lambda args, **kw: ran.append((args, kw.get("cwd"))))
    app = bundle.copy_app()
    # the UI is built every time, for a browser and for the app's own window
    assert ran == [(["npm", "run", "build"], os.path.join(ROOT, "ui")), (["npm", "run", "build:desktop"], os.path.join(ROOT, "ui"))]
    root = os.path.join(app, "open_edition")
    for part in ("proofpane_open/desktop.py", "proofpane_open/main.py", "proofpane_workspace/gate.py",
                 "proofpane_workspace/model_key.py", "web", "core_plugin/.claude-plugin/plugin.json",
                 "core_plugin/hooks/hooks.json", "core_plugin/hooks/proofpane_hook.py",
                 "core_plugin/daemon/.claude-plugin/plugin.json", "core_plugin/daemon/.mcp.json"):
        assert os.path.exists(os.path.join(root, part)), part
    for part in bundle.OSS_PARTS:
        assert os.path.isdir(os.path.join(app, "oss", part)), part
    for folder, dirs, files in os.walk(app):
        assert "tests" not in dirs and "__pycache__" not in dirs, folder
        assert not any(f.endswith(".pyc") for f in files), folder
    # the server finds the open packages beside it, as in the hosted image, with nothing set
    env = {k: v for k, v in os.environ.items() if k != "OPEN_EDITION_OSS_ROOT"}
    found = real_run([sys.executable, "-E", "-s", "-B", "-c", "from proofpane_open import paths; print(paths.OSS_ROOT)"],
                     cwd=root, env=env, capture_output=True, text=True, timeout=30, check=True).stdout.strip()
    assert os.path.realpath(found) == os.path.realpath(os.path.join(app, "oss"))


def test_a_build_json_a_run_of_build_py_left_in_the_checkout_is_not_carried(tmp_path):
    """build.py keeps the release and the commit beside itself when it is run, and git ignores that file; the app
    keeps no release of its own, so it never carries a checkout's, and its server says "unknown"."""
    bundle = _bundle()
    tree = tmp_path / "proofpane_open"
    tree.mkdir()
    for name in ("build.py", "build.json", "main.py"):
        (tree / name).write_text("{}" if name.endswith(".json") else "", encoding="utf-8")
    bundle.shutil.copytree(tree, tmp_path / "copied", ignore=bundle.LEAVE_OUT)
    assert sorted(os.listdir(tmp_path / "copied")) == ["build.py", "main.py"]


def test_a_download_whose_digest_is_not_the_pinned_one_is_refused(tmp_path, monkeypatch):
    bundle = _bundle()
    monkeypatch.setattr(bundle, "DOWNLOADS", str(tmp_path))

    class Answer(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(bundle.urllib.request, "urlopen", lambda url, timeout: Answer(b"not the archive"))
    with pytest.raises(SystemExit) as refused:
        bundle.fetch_python("x86_64")
    assert "not the pinned" in str(refused.value)
    assert os.listdir(tmp_path) == []                   # what came is not kept, so the next run downloads again


def test_one_build_is_pinned_for_each_mac_architecture():
    bundle = _bundle()
    assert set(bundle.BUILDS) == {"x86_64", "arm64"}
    for triple, digest in bundle.BUILDS.values():
        assert triple.endswith("-apple-darwin") and re.fullmatch(r"[0-9a-f]{64}", digest)
    assert re.fullmatch(r"\d{8}", bundle.RELEASE) and re.fullmatch(r"3\.\d+\.\d+", bundle.PYTHON)


def _otool(answers):
    """A stand-in for otool: `-L` and `-l` answers by file name."""
    def run(args, **kw):
        out = answers[os.path.basename(args[-1])][args[1]]
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")
    return run


def test_a_file_that_would_not_work_on_another_mac_stops_the_build(tmp_path, monkeypatch):
    bundle = _bundle()
    for name in ("good.so", "homebrew.so", "too-new.so"):
        (tmp_path / name).write_bytes(bytes.fromhex("cffaedfe") + b"rest")       # a 64-bit Mach-O's first bytes
    (tmp_path / "notes.txt").write_text("not a binary")
    system = "x:\n\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n\t@rpath/x.so (compatibility version 0.0.0)\n"
    old_enough = "Load command 9\n      cmd LC_BUILD_VERSION\n  cmdsize 32\n platform 1\n    minos 10.15\n"
    answers = {
        "good.so": {"-L": system, "-l": old_enough},
        "homebrew.so": {"-L": system + "\t/usr/local/opt/openssl@3/lib/libssl.3.dylib (compatibility version 3.0.0)\n",
                        "-l": old_enough},
        "too-new.so": {"-L": system, "-l": old_enough.replace("10.15", "15.0")},
    }
    monkeypatch.setattr(bundle.subprocess, "run", _otool(answers))
    assert sorted(os.path.basename(p) for p in bundle.macho_files(str(tmp_path))) == ["good.so", "homebrew.so",
                                                                                      "too-new.so"]
    with pytest.raises(SystemExit) as stopped:
        bundle.check_portable(str(tmp_path))
    said = str(stopped.value)
    assert "homebrew.so links /usr/local/opt/openssl@3/lib/libssl.3.dylib" in said
    assert "too-new.so needs macOS 15.0" in said and "good.so" not in said
    for name in ("homebrew.so", "too-new.so"):
        os.unlink(tmp_path / name)
    bundle.check_portable(str(tmp_path))                     # what is left is portable


def _lock():
    spec = importlib.util.spec_from_file_location("desktop_lock", os.path.join(ROOT, "desktop", "lock.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_lock_holds_every_package_the_server_and_the_agent_ask_for_each_with_its_hashes():
    lock = _lock()
    assert lock.check() == []
    pins, hashes = lock.read_pins(), lock.read_hashes()
    assert len(pins) >= 30 and all(len(hashes[k]) >= 1 for k in pins)
    # cryptography is held with its sdist's hash too: an Intel Mac builds it (no wheel there)
    assert "cryptography" in pins and len(hashes["cryptography"]) > 1


def test_the_lock_check_names_what_is_missing_or_too_old(tmp_path):
    lock = _lock()
    (tmp_path / "req.txt").write_text("fastapi>=0.136.0\nuvicorn==0.54.0\nmissing-one>=1\n")
    (tmp_path / "requirements.lock").write_text("fastapi==0.130.0 \\\n    --hash=sha256:" + "a" * 64 + "\n"
                                                "uvicorn==0.54.0\n")
    bad = lock.check(str(tmp_path / "requirements.lock"), [str(tmp_path / "req.txt")])
    assert sorted(bad) == ["fastapi 0.130.0 does not meet >=0.136.0", "missing-one is asked for and not in the lock",
                           "uvicorn has no hash"]
