"""Build the `munitas` wheel that the platform offers for download, from the two library files, with nothing but the standard library.

    .venv\\Scripts\\python.exe scripts\\build-client-wheel.py

Source of truth stays where it is: `scripts/client/munitas_client.py` (the library) and `worker/step_sdk/munitas_step.py` (the one reader of a file, shared with
custom pipeline steps). This copies them into a package, `munitas/client.py` and `munitas/step.py`, and writes the metadata pip needs. Nothing is edited by hand
in the output, and the same source always gives byte-for-byte the same wheel (fixed timestamps, normalised line endings), so its checksum can be quoted.

Writes to `platform/client_wheel/`, which the API image copies and serves (`GET /client`). The wheel, its checksum and `client.json` there are generated and
ignored by git; only `.gitkeep` is kept, so the image builds even before this has been run (the API then says the library is not built).

Run it before rebuilding the API image: `scripts/admin/rebuild-image.ps1` does. The version comes from `CLIENT_VERSION` in `munitas_client.py`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "scripts" / "client" / "munitas_client.py"
STEP = ROOT / "worker" / "step_sdk" / "munitas_step.py"
LICENSE = ROOT / "LICENSE"
OUT = ROOT / "platform" / "client_wheel"

NAME = "munitas"
SUMMARY = "Work with a Munitas platform from a notebook or a script: sign in as yourself, find datasets, ask for access, read what you were allowed, make a dataset from a query."
REQUIRES_PYTHON = ">=3.10"
TIMESTAMP = (2026, 1, 1, 0, 0, 0)  # fixed, so the same source gives the same wheel

INIT = '''"""Munitas: work with a Munitas platform from a notebook or a script.

    from munitas import Munitas
    me = Munitas.sign_in("https://your-platform-address")

See the docstring of `munitas.client` for everything the library covers.
"""

from .client import CLIENT_VERSION, FileErased, Munitas, MunitasError

__version__ = CLIENT_VERSION
__all__ = ["Munitas", "MunitasError", "FileErased", "__version__"]
'''


def text(path: Path) -> bytes:
    """A source file as UTF-8 with Unix line endings, so the wheel does not depend on which machine built it."""
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n").encode("utf-8")


def version_of(source: bytes) -> str:
    found = re.search(rb'^CLIENT_VERSION = "([0-9]+(?:\.[0-9]+)*)"$', source, re.M)
    if not found:
        raise SystemExit('munitas_client.py has no line CLIENT_VERSION = "x.y.z"')
    return found.group(1).decode()


def record_line(path: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{path},sha256={digest},{len(data)}"


def main() -> int:
    client = text(CLIENT)
    version = version_of(client)
    dist_info = f"{NAME}-{version}.dist-info"
    metadata = (
        "Metadata-Version: 2.1\n"
        f"Name: {NAME}\n"
        f"Version: {version}\n"
        f"Summary: {SUMMARY}\n"
        "License: Apache-2.0\n"
        "Classifier: License :: OSI Approved :: Apache Software License\n"
        "Classifier: Programming Language :: Python :: 3\n"
        f"Requires-Python: {REQUIRES_PYTHON}\n"
        "Requires-Dist: httpx\n"
        "Provides-Extra: tables\n"
        'Requires-Dist: duckdb; extra == "tables"\n'
    ).encode()
    wheel_file = "Wheel-Version: 1.0\nGenerator: munitas scripts/build-client-wheel.py\nRoot-Is-Purelib: true\nTag: py3-none-any\n".encode()
    files = {
        f"{NAME}/__init__.py": INIT.encode(),
        f"{NAME}/client.py": client,
        f"{NAME}/step.py": text(STEP),
        f"{dist_info}/METADATA": metadata,
        f"{dist_info}/WHEEL": wheel_file,
        f"{dist_info}/top_level.txt": f"{NAME}\n".encode(),
        f"{dist_info}/licenses/LICENSE": text(LICENSE),
    }
    record = "\n".join([record_line(p, d) for p, d in files.items()] + [f"{dist_info}/RECORD,,", ""]).encode()
    files[f"{dist_info}/RECORD"] = record

    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob(f"{NAME}-*.whl*"):
        old.unlink()
    filename = f"{NAME}-{version}-py3-none-any.whl"
    target = OUT / filename
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, data in files.items():
            info = zipfile.ZipInfo(path, date_time=TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
    sha = hashlib.sha256(target.read_bytes()).hexdigest()
    (OUT / f"{filename}.sha256").write_text(sha + "\n", encoding="utf-8")
    (OUT / "client.json").write_text(
        json.dumps({"name": NAME, "version": version, "filename": filename, "sha256": sha, "size": target.stat().st_size, "requires_python": REQUIRES_PYTHON,
                    "requires": ["httpx"], "extras": {"tables": ["duckdb"]}}, indent=2) + "\n", encoding="utf-8")
    print(f"built {filename}  {target.stat().st_size} bytes  sha256 {sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
