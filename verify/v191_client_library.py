r"""U207: the platform offers the Munitas client library for download, and what it offers installs and is the current code.

Runs on the host against the running platform, with no sign-in anywhere, because `pip` cannot sign in. It proves:

  * `GET /client` answers with no session and names the version, the file and its checksum, and the version is the one in the library's source;
  * the file downloads with no session, and its bytes have exactly the size and SHA-256 the platform said;
  * only that one file is offered: any other name, and the usual ways of reaching outside the folder, are refused;
  * the running platform serves the library as it is now (a rebuild of the wheel from the source gives the same checksum), so an image built before the
    library last changed is reported, not trusted;
  * the two modules inside the file are the library's own source, with nothing added or left out;
  * `pip install <the platform's own address>/client/<file>` works in a clean virtual environment, the package imports at the offered version, the reader of a
    file is found inside it, and `pip check` finds nothing broken.

    .venv\Scripts\python.exe verify\v191_client_library.py

It makes a throwaway virtual environment in the system's temporary folder and removes it, and rebuilds the wheel in platform/client_wheel (the same bytes).
It needs network access to install `httpx` from the package index; without it the install is reported as skipped, and a skip is not a pass.
"""

from __future__ import annotations

import hashlib
import io
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT))

import os  # noqa: E402

import httpx  # noqa: E402

os.environ.setdefault("MUNITAS_PG_DSN", "postgresql://munitas:munitas@localhost:5432/platform")
os.environ.setdefault("MUNITAS_VERIFY_KRATOS", "http://localhost:4433")
os.environ.setdefault("MUNITAS_S3_ENDPOINT", "http://localhost:8333")

from common import API, check, heading, skip, summary  # noqa: E402

CLIENT_SOURCE = ROOT / "scripts" / "client" / "munitas_client.py"
STEP_SOURCE = ROOT / "worker" / "step_sdk" / "munitas_step.py"
BUILD = ROOT / "scripts" / "build-client-wheel.py"


def lf(path: Path) -> bytes:
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n").encode("utf-8")


def source_version() -> str:
    found = re.search(rb'^CLIENT_VERSION = "([0-9.]+)"$', lf(CLIENT_SOURCE), re.M)
    return found.group(1).decode() if found else ""


def run(args: list[str], timeout: int = 300) -> tuple[int, str]:
    done = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return done.returncode, (done.stdout + done.stderr).strip()


def main() -> int:
    heading("The platform offers the library, with no sign-in")
    info = httpx.get(f"{API}/client", timeout=20)
    body = info.json() if info.status_code == 200 else {}
    check("GET /client answers without a session", info.status_code == 200, f"HTTP {info.status_code}")
    check("it says the library is available", body.get("available") is True, str(body)[:120])
    for key in ("name", "version", "filename", "path", "sha256", "size", "requires_python", "requires", "extras"):
        check(f"it names the {key}", key in body and body[key] not in (None, "", []) or key == "extras", str(body.get(key))[:60])
    check("the offered version is the one in the library's source", body.get("version") == source_version(), f"{body.get('version')} against {source_version()}")
    check("the path is under /client and is the file's name", body.get("path") == f"/client/{body.get('filename')}", str(body.get("path")))

    heading("The file downloads, and is what was announced")
    got = httpx.get(f"{API}{body.get('path', '/client/none')}", timeout=60)
    check("the file downloads without a session", got.status_code == 200, f"HTTP {got.status_code}")
    data = got.content
    check("its size is the size announced", len(data) == body.get("size"), f"{len(data)} against {body.get('size')}")
    check("its SHA-256 is the checksum announced", hashlib.sha256(data).hexdigest() == body.get("sha256"))

    heading("Only that one file is offered")
    for label, name in (("another file name", "other.whl"), ("the metadata file beside it", "client.json"), ("a parent folder", "..%2Fclient.json"),
                        ("the offered name followed by a parent folder", f"{body.get('filename')}%2F..%2Fclient.json"), ("a backslash path", "..%5Cclient.json"),
                        ("an absolute path", "%2Fetc%2Fpasswd"), ("an empty name", "%20")):
        r = httpx.get(f"{API}/client/{name}", timeout=20)
        check(f"{label} is refused", r.status_code == 404, f"HTTP {r.status_code}")

    heading("It is the current code")
    code, out = run([sys.executable, str(BUILD)])
    rebuilt = re.search(r"sha256 ([0-9a-f]{64})", out)
    check("the wheel rebuilds from the source", code == 0 and rebuilt is not None, out[-100:])
    check("the running platform serves the library as it is now", bool(rebuilt) and rebuilt.group(1) == body.get("sha256"),
          "if this fails, rebuild the API image: scripts\\admin\\rebuild-image.ps1")
    try:
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        wheel = zipfile.ZipFile(io.BytesIO(data))
        check("the file is a wheel with the package and its metadata", "munitas/__init__.py" in names and any(n.endswith(".dist-info/METADATA") for n in names), ", ".join(names)[:100])
        check("munitas/client.py is the library's source", wheel.read("munitas/client.py") == lf(CLIENT_SOURCE))
        check("munitas/step.py is the reader's source", wheel.read("munitas/step.py") == lf(STEP_SOURCE))
        meta = next(wheel.read(n).decode() for n in names if n.endswith(".dist-info/METADATA"))
        check("the metadata carries the same version", f"Version: {body.get('version')}\n" in meta)
        check("the metadata asks for httpx and offers a tables extra", "Requires-Dist: httpx" in meta and 'extra == "tables"' in meta)
    except zipfile.BadZipFile:
        check("the file is a wheel", False, "not a zip file")

    heading("pip installs it from the platform's own address")
    with tempfile.TemporaryDirectory(prefix="munitas-client-check-") as tmp:
        code, out = run([sys.executable, "-m", "venv", tmp])
        if code != 0:
            skip("a clean virtual environment", out[-120:])
            return summary("U207")
        python = str(Path(tmp) / ("Scripts" if sys.platform == "win32" else "bin") / ("python.exe" if sys.platform == "win32" else "python"))
        code, out = run([python, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", f"{API}{body.get('path')}"], timeout=600)
        if code != 0 and re.search(r"(Could not find a version|No matching distribution|Connection|Max retries|NameResolution)", out):
            skip("installing from the platform", f"the package index could not be reached for httpx: {out[-120:]}")
            return summary("U207")
        check("pip install <the platform's address>/client/<file> succeeds", code == 0, out[-160:])
        probe = (
            "import munitas, munitas.client as c, importlib.metadata as m\n"
            "print('version', munitas.__version__, m.version('munitas'))\n"
            "print('reader', c._reader().__name__)\n"
            "print('signin', callable(munitas.Munitas.sign_in), 'names', ','.join(sorted(munitas.__all__)))\n"
        )
        code, out = run([python, "-c", probe])
        check("the package imports", code == 0, out[-160:])
        check("it reports the offered version, twice over", f"version {body.get('version')} {body.get('version')}" in out, out.splitlines()[0] if out else "")
        check("the reader of a file is found inside the package", "reader munitas.step" in out)
        check("the sign-in call and the exported names are there", "signin True names FileErased,Munitas,MunitasError,__version__" in out, out.splitlines()[-1] if out else "")
        code, out = run([python, "-m", "pip", "check"])
        check("pip check finds nothing broken", code == 0, out[-120:])
    return summary("U207")


if __name__ == "__main__":
    sys.exit(main())
