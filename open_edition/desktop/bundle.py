"""What the desktop app carries (the manual, "The desktop app"), made in
desktop/build/resources/ for the Tauri bundle to copy into the app:

- python/: CPython 3.13.15 from python-build-standalone (release 20260929), the build for this machine's architecture,
  checked against the digest pinned here before it is unpacked, with the server's and the workspace agent's packages
  installed in it exactly as desktop/requirements.lock holds them (each version the build was tested with, each file
  by its sha256: --require-hashes --no-deps, then pip check), from wheels only but for one (`cryptography` 50 has no
  wheel for an Intel Mac: it is built from its pinned sdist against a static OpenSSL 3.5.9, itself built here from its
  pinned source for
  macOS 11, since the build machine's own OpenSSL is not in the app and Homebrew's is built for the machine's own
  macOS), and the copy of Claude Code the Agent SDK's wheel carries taken out (the chat drives the
  person's own claude, as the workspace image's does). What it never runs is left out (Tcl/Tk, IDLE, pip once it
  has installed, the headers, the shared libpython nothing links, and scripts whose first line names the build
  folder), which is less to carry and less to sign.
- Every Mach-O file it carries is checked before anything is signed: none may link a library outside the system's
  own (/usr/lib, /System), or need a macOS newer than the app's 11.0.
- app/open_edition/ and app/oss/: the server, the workspace agent and the built ADE (web/app, which the server serves
  to a browser), and the open packages the server signs and checks with, laid out as the hosted image lays them out
  (proofpane_open/paths.py finds oss/ beside open_edition/), without tests. Everything is compiled to bytecode here,
  so nothing in the app is written to when it runs.
- desktop/build/ui/: the ADE built for the app's own window (`npm run build:desktop`), which talks to this Mac's
  server and to hosted ones; Tauri carries it as the app's pages (tauri.conf.json, frontendDist).
- licenses/: what the app carries under whose license (open_edition/NOTICE): Proofpane Community's own LICENSE and
  NOTICE, and the third-party licenses of its pages (THIRD_PARTY_LICENSES-page.txt, which the UI's build writes), of
  its Python and of its Rust crates (THIRD_PARTY_LICENSES-python.txt and -rust.txt, desktop/licenses.py), with the
  Rust standard library's notices (THIRD_PARTY_LICENSES-rust-std.html). A package or crate under a license the app
  does not take, or with no license text, stops the build.

    python3 desktop/bundle.py                     from open_edition/; builds ui/ first, both ways, always, so the
                                                  app never carries a stale build of it
    python3 desktop/bundle.py --sign IDENTITY     and then signs every Mach-O file it carries with the hardened
                                                  runtime and a timestamp (`-` signs ad hoc, with no timestamp, to
                                                  try the hardened runtime locally); the shell is signed by Tauri
    python3 desktop/bundle.py --list-macho        only list the Mach-O files already in build/resources, one a line,
                                                  for a signer that is not codesign, such as rcodesign

Standard library only.
"""

import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import licenses  # noqa: E402  (desktop/licenses.py, beside this file)

OPEN_EDITION = os.path.dirname(HERE)
REPO = os.path.dirname(OPEN_EDITION)
BUILD = os.path.join(HERE, "build")
OUT = os.path.join(BUILD, "resources")
DOWNLOADS = os.path.join(BUILD, "downloads")

RELEASE = "20260929"
PYTHON = "3.13.15"
# the release's own digests (its SHA256SUMS, and GitHub's for each asset), read 2026-10-02
BUILDS = {
    "x86_64": ("x86_64-apple-darwin", "73b503a2d3f47f0601265d7936744b77dc4ee75d2a3e88a470594a014dcf6822"),
    "arm64": ("aarch64-apple-darwin", "d66c67f16148c7454b1509c32747175f7669c8b8e105b97b92a0000d66af6e6e"),
}
# CPython's own source, for its license texts (licenses.py python-sources): the sha256 of python.org's file, as
# downloaded from https://www.python.org/ftp/python/3.13.15/Python-3.13.15.tar.xz on 2026-10-10
CPYTHON_SOURCE = "1e66a7945a48390ee4c2a4268a0e4185884059a13c4aab6d148aa208deea4a76"
# OpenSSL's long-term line, for cryptography where it has no wheel: the release's own digest, read 2026-10-02
OPENSSL = ("3.5.9", "603f5602e2eef00d77fbd429d34dcd5822bb301757a1bc9cdb24c670f1eb859a")
MACOS_MIN = "11.0"          # the app's minimumSystemVersion (src-tauri/tauri.conf.json)
# the server, the workspace agent with the plugin it gives each claude start (beside it, where core_plugin.PLUGIN finds
# it), and the built ADE
APP_PARTS = ("proofpane_open", "proofpane_workspace", "core_plugin", "web")
OSS_PARTS = ("core", "verify", "verify-pr", "sign")
# build.json: what proofpane_open/build.py keeps, which a run of it may leave in the checkout (git ignores it); the app
# keeps no release of its own, so its server says "unknown" rather than a checkout's
LEAVE_OUT = shutil.ignore_patterns("__pycache__", "tests", ".pytest_cache", "node_modules", "*.pyc", "build.json")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url, name, digest):
    """`url`, kept in build/downloads as `name`, downloaded once and checked against `digest` every time."""
    path = os.path.join(DOWNLOADS, name)
    if not (os.path.exists(path) and _sha256(path) == digest):
        os.makedirs(DOWNLOADS, exist_ok=True)
        print(f"downloading {url}", file=sys.stderr)
        with urllib.request.urlopen(url, timeout=120) as resp, open(path + ".part", "wb") as fh:
            shutil.copyfileobj(resp, fh)
        os.replace(path + ".part", path)
    got = _sha256(path)
    if got != digest:
        os.unlink(path)
        raise SystemExit(f"{name}: its digest is {got}, not the pinned {digest}")
    return path


def fetch_python(arch):
    """The pinned CPython build for `arch`."""
    triple, digest = BUILDS[arch]
    name = f"cpython-{PYTHON}+{RELEASE}-{triple}-install_only_stripped.tar.gz"
    return download(f"https://github.com/astral-sh/python-build-standalone/releases/download/{RELEASE}/{name}", name,
                    digest)


def builds_openssl(arch):
    """Whether cryptography is built here, against the OpenSSL built here: it has no wheel for an Intel Mac."""
    return arch == "x86_64"


def openssl_source():
    """OpenSSL's pinned source archive, in build/downloads."""
    version, digest = OPENSSL
    name = f"openssl-{version}.tar.gz"
    return download(f"https://github.com/openssl/openssl/releases/download/openssl-{version}/{name}", name, digest)


def openssl_license():
    """OpenSSL's LICENSE.txt, as its pinned source holds it."""
    with tarfile.open(openssl_source()) as tar:
        return tar.extractfile(f"openssl-{OPENSSL[0]}/LICENSE.txt").read().decode("utf-8")


def build_openssl(arch):
    """A static OpenSSL for macOS 11 and later, built from its pinned source, once, into build/openssl-VERSION-ARCH."""
    version, _digest = OPENSSL
    prefix = os.path.join(BUILD, f"openssl-{version}-{arch}")
    if os.path.exists(os.path.join(prefix, "lib", "libcrypto.a")):
        return prefix
    archive = openssl_source()
    src = os.path.join(BUILD, "src")
    shutil.rmtree(os.path.join(src, f"openssl-{version}"), ignore_errors=True)
    os.makedirs(src, exist_ok=True)
    with tarfile.open(archive) as tar:
        tar.extractall(src, filter="data")
    where = os.path.join(src, f"openssl-{version}")
    env = dict(os.environ, MACOSX_DEPLOYMENT_TARGET=MACOS_MIN)
    target = {"x86_64": "darwin64-x86_64-cc", "arm64": "darwin64-arm64-cc"}[arch]
    subprocess.run(["perl", "Configure", target, "no-shared", "no-tests", f"--prefix={prefix}",
                    f"--openssldir={prefix}/ssl", f"-mmacosx-version-min={MACOS_MIN}"], cwd=where, env=env, check=True)
    subprocess.run(["make", f"-j{os.cpu_count() or 2}"], cwd=where, env=env, check=True)
    subprocess.run(["make", "install_sw"], cwd=where, env=env, check=True)
    return prefix


def unpack_python(archive):
    target = os.path.join(OUT, "python")
    shutil.rmtree(target, ignore_errors=True)
    with tarfile.open(archive) as tar:
        tar.extractall(OUT, filter="data")        # the archive's one top folder is python/
    python = os.path.join(target, "bin", "python3")
    if not os.access(python, os.X_OK):
        raise SystemExit(f"{python} is not there after unpacking")
    return python


LOCK = os.path.join(HERE, "requirements.lock")


def install_requirements(python, arch):
    """The lock's packages, its files only. pip's hash checking does not cover what it installs to build an sdist, so
    the one built here (cryptography, on an Intel Mac) has its build tools from PyPI unpinned."""
    env, build = dict(os.environ), []
    if builds_openssl(arch):     # cryptography 50 has no Intel wheel: built against a static OpenSSL for macOS 11
        env.update(OPENSSL_DIR=build_openssl(arch), OPENSSL_STATIC="1", MACOSX_DEPLOYMENT_TARGET=MACOS_MIN)
        build = ["--no-binary", "cryptography"]
    subprocess.run([python, "-E", "-s", "-m", "pip", "install", "--no-cache-dir", "--require-hashes", "--no-deps",
                    "--only-binary=:all:", *build, "--disable-pip-version-check", "--no-warn-script-location",
                    "-r", LOCK], env=env, check=True)
    # every installed package's own requirements are among them
    subprocess.run([python, "-E", "-s", "-m", "pip", "check"], check=True)
    site = subprocess.run([python, "-E", "-s", "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                          capture_output=True, text=True, check=True).stdout.strip()
    bundled = os.path.join(site, "claude_agent_sdk", "_bundled", "claude")
    if os.path.exists(bundled):
        os.unlink(bundled)
    subprocess.run([python, "-E", "-s", "-c", "import fastapi, uvicorn, websockets, cryptography, claude_agent_sdk"],
                   check=True)


# what the app never runs, under python/
LEAVE_OUT_OF_PYTHON = ("include", "share", "lib/pkgconfig", "lib/libpython3.13.dylib", "lib/libtcl9.0.dylib",
                       "lib/libtcl9tk9.0.dylib", "lib/tcl9.0", "lib/tcl9", "lib/tk9.0", "lib/itcl4.3.8",
                       "lib/thread3.0.6", "lib/python3.13/tkinter", "lib/python3.13/idlelib",
                       "lib/python3.13/turtledemo", "lib/python3.13/turtle.py", "lib/python3.13/ensurepip",
                       "lib/python3.13/pydoc_data")


def trim(python_dir):
    """Take out what the app never runs (LEAVE_OUT_OF_PYTHON, Tk's extension, pip, and every script in bin/ but the
    interpreter: their first lines name this build folder)."""
    def gone(path):
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        elif os.path.lexists(path):
            os.unlink(path)

    for part in LEAVE_OUT_OF_PYTHON:
        gone(os.path.join(python_dir, part))
    dynload = os.path.join(python_dir, "lib", "python3.13", "lib-dynload")
    for name in os.listdir(dynload):
        if name.startswith("_tkinter"):
            gone(os.path.join(dynload, name))
    site = os.path.join(python_dir, "lib", "python3.13", "site-packages")
    for name in os.listdir(site):
        if name == "pip" or name.startswith("pip-"):
            gone(os.path.join(site, name))
    bin_dir = os.path.join(python_dir, "bin")
    for name in os.listdir(bin_dir):
        if name not in ("python3", "python3.13"):
            gone(os.path.join(bin_dir, name))


def python_changes():
    """What trim() leaves out of python-build-standalone's Python, in words, for the summary of the changes made to
    Python that its licenses ask the app to carry (licenses.python_notice)."""
    return [", ".join(f"python/{part}" for part in LEAVE_OUT_OF_PYTHON),
            "Tk's extension module (python/lib/python3.13/lib-dynload/_tkinter*)",
            "pip (python/lib/python3.13/site-packages/pip and pip-*)",
            "every script in python/bin/ but the interpreter (python3 and python3.13)"]


def macho_files(folder):
    """Every Mach-O file under `folder` (by its first four bytes), links left out."""
    magics = {bytes.fromhex(m) for m in ("feedfacf", "cffaedfe", "feedface", "cefaedfe", "cafebabe", "bebafeca")}
    for root, _dirs, files in os.walk(folder):
        for name in files:
            path = os.path.join(root, name)
            if os.path.islink(path):
                continue
            with open(path, "rb") as fh:
                if fh.read(4) in magics:
                    yield path


def _version(text):
    return tuple(int(x) for x in text.split("."))


def check_portable(folder=OUT):
    """Refuse a Mach-O file that links a library outside the system's own, or needs a macOS newer than MACOS_MIN:
    either works on the machine that built it and not on another."""
    bad = []
    for path in sorted(macho_files(folder)):
        links = subprocess.run(["otool", "-L", path], capture_output=True, text=True, check=True).stdout.splitlines()[1:]
        for line in links:
            lib = line.strip().split(" (", 1)[0]
            if not (lib.startswith(("/usr/lib/", "/System/Library/", "@rpath/", "@loader_path/", "@executable_path/"))):
                bad.append(f"{os.path.relpath(path, folder)} links {lib}")
        loads = subprocess.run(["otool", "-l", path], capture_output=True, text=True, check=True).stdout
        for minos in re.findall(r"\bminos (\S+)", loads) + re.findall(r"LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version (\S+)", loads):
            if _version(minos) > _version(MACOS_MIN):
                bad.append(f"{os.path.relpath(path, folder)} needs macOS {minos}")
    if bad:
        raise SystemExit("not portable to another Mac:\n  " + "\n  ".join(bad))


def sign(identity, folder=OUT):
    """Sign every Mach-O file the app carries, with the hardened runtime: what notarization asks of each. Ad hoc
    (`-`) has no timestamp, which needs Apple's server and a real identity."""
    stamp = ["--timestamp=none"] if identity == "-" else ["--timestamp"]
    count = 0
    for path in sorted(macho_files(folder)):
        subprocess.run(["codesign", "--force", "--options", "runtime", *stamp, "--sign", identity, path], check=True)
        count += 1
    return count


def copy_app():
    ui = os.path.join(OPEN_EDITION, "ui")
    subprocess.run(["npm", "run", "build"], cwd=ui, check=True)              # web/app, for a browser
    subprocess.run(["npm", "run", "build:desktop"], cwd=ui, check=True)      # build/ui, the window's own pages
    app = os.path.join(OUT, "app")
    shutil.rmtree(app, ignore_errors=True)
    for part in APP_PARTS:
        shutil.copytree(os.path.join(OPEN_EDITION, part), os.path.join(app, "open_edition", part), ignore=LEAVE_OUT)
    for part in OSS_PARTS:
        shutil.copytree(os.path.join(REPO, "oss", part), os.path.join(app, "oss", part), ignore=LEAVE_OUT)
    return app


def compile_all(python, *folders):
    for folder in folders:
        # never checked against the sources, so Python never writes new bytecode into an installed, signed app
        subprocess.run([python, "-E", "-s", "-m", "compileall", "-q", "-j", "0", "--invalidation-mode",
                        "unchecked-hash", folder], check=True)


def write_licenses(arch):
    """build/resources/licenses/ (the app's Resources/licenses/): Proofpane Community's LICENSE and NOTICE, and the
    third-party licenses of the app's pages, of its Python (as build/resources/python has it, trimmed) and of its Rust
    crates (for this machine's architecture) and standard library; the last three beside the window's pages too
    (build/ui), which show them. Stops the build on anything without a license the app takes."""
    triple = BUILDS[arch][0]
    openssl = (OPENSSL[0], openssl_license()) if builds_openssl(arch) else None
    python_text, problems = licenses.python_notice(os.path.join(OUT, "python"), triple, PYTHON, RELEASE,
                                                   python_changes(), openssl, fetch=download)
    rust_text, more = licenses.rust_notice(os.path.join(HERE, "src-tauri"), [triple])
    rust_std, still = licenses.rust_std(os.path.join(HERE, "src-tauri"))
    problems += more + still
    if problems:
        raise SystemExit("third-party licenses the app cannot carry:\n  " + "\n  ".join(problems))
    folder = os.path.join(OUT, "licenses")
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder)
    for name, source in (("LICENSE", os.path.join(OPEN_EDITION, "LICENSE")),
                         ("LICENSE-APACHE-2.0", os.path.join(OPEN_EDITION, "LICENSE-APACHE-2.0")),
                         ("NOTICE", os.path.join(OPEN_EDITION, "NOTICE")),
                         ("THIRD_PARTY_LICENSES-page.txt", os.path.join(BUILD, "ui", "THIRD_PARTY_LICENSES.txt"))):
        shutil.copyfile(source, os.path.join(folder, name))
    for name, text in ((licenses.PYTHON_FILE, python_text), (licenses.RUST_FILE, rust_text)):
        with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    with open(os.path.join(folder, licenses.RUST_STD_FILE), "wb") as fh:
        fh.write(rust_std)
    # and beside the window's own pages (build/ui, which Tauri carries as the app's pages), for their Licenses dialog
    # (ui/src/components/Licenses.tsx); the pages' build put its own notice and Proofpane Community's there already
    for name in (licenses.PYTHON_FILE, licenses.RUST_FILE, licenses.RUST_STD_FILE):
        shutil.copyfile(os.path.join(folder, name), os.path.join(BUILD, "ui", name))
    return folder


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(prog="python3 desktop/bundle.py")
    parser.add_argument("--sign", metavar="IDENTITY", help="sign every Mach-O file it carries (- for ad hoc)")
    parser.add_argument("--list-macho", action="store_true", help="only list the Mach-O files already built")
    args = parser.parse_args(argv)
    if args.list_macho:
        for path in sorted(macho_files(OUT)):
            print(path)
        return 0
    arch = platform.machine()
    if sys.platform != "darwin" or arch not in BUILDS:
        raise SystemExit(f"this builds the macOS app's resources; this machine is {sys.platform} {arch}")
    os.makedirs(OUT, exist_ok=True)
    python = unpack_python(fetch_python(arch))
    install_requirements(python, arch)
    trim(os.path.join(OUT, "python"))
    app = copy_app()
    compile_all(python, os.path.join(OUT, "python", "lib"), app)
    write_licenses(arch)
    check_portable()
    if args.sign:
        print(f"signed {sign(args.sign)} Mach-O files", file=sys.stderr)
    print(OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
