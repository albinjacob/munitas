"""Open a legal export package, and check that it is what the platform produced.

For whoever receives a package: a court, a regulator or a law firm. It shares no code with the platform that
wrote it, on purpose, so that the format is the only thing the two have in common and the format is written
down in platform/api/app/package_crypto.py.

You need the package file, the passphrase (it is given to you separately from the file) and, to be sure the
signature is the platform's and not somebody else's, the platform's public key, which the platform serves at
/legal-exports/signing-key.

    python open_legal_package.py HC-2026-0417.mlep --out opened --public-key <hex>

It decrypts the package, writes its contents to the folder, then checks, and reports every one of:
  * the manifest's signature verifies against the public key,
  * every file listed in the manifest is present and has the size and SHA-256 listed,
  * no file is present that the manifest does not list.

Any failure ends with a non-zero exit code. Needs the `cryptography` package.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import struct
import sys
import tempfile
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"MLEP1"


def decrypt(package: Path, target: Path, passphrase: str) -> None:
    with package.open("rb") as src, target.open("wb") as out:
        if src.read(5) != MAGIC:
            raise ValueError("this is not a legal export package")
        salt, prefix = src.read(16), src.read(8)
        key = Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(passphrase.encode("utf-8"))
        aead = AESGCM(key)
        number = 0
        while True:
            header = src.read(5)
            if len(header) < 5:
                raise ValueError("the package ends before its last block, so it is incomplete")
            length, flag = struct.unpack(">I", header[:4])[0], header[4:5]
            ciphertext = src.read(length)
            if len(ciphertext) < length:
                raise ValueError("the package is cut short")
            out.write(aead.decrypt(prefix + struct.pack(">I", number), ciphertext, flag))
            if flag == b"\x01":
                return
            number += 1


def open_package(package: Path, passphrase: str, out_dir: Path, public_key: str | None = None) -> dict:
    """Decrypt, unpack and check. Returns a report: {"ok": bool, "problems": [...], "files": n, ...}."""
    problems: list[str] = []
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "package.zip"
        try:
            decrypt(package, archive, passphrase)
        except Exception as exc:  # a wrong passphrase, a damaged file, a package cut short
            return {"ok": False, "problems": [f"could not open the package: {type(exc).__name__}: {exc}"], "files": 0}
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(out_dir)

    manifest_path = out_dir / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    signature = (out_dir / "manifest.sig").read_text().strip()
    key_hex = public_key or manifest["public_key"]
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(key_hex)).verify(
            bytes.fromhex(signature), hashlib.sha256(manifest_bytes).digest())
        signed = True
    except Exception:
        signed = False
        problems.append("the manifest's signature does not verify against " +
                        ("the public key you gave" if public_key else "the key inside the package"))
    if not public_key:
        problems_note = "no public key was given, so the signature was checked against the key inside the package, " \
                        "which proves the package is consistent and not that the platform made it"
    else:
        problems_note = ""

    listed = {f["path"] for f in manifest["files"]}
    for f in manifest["files"]:
        path = out_dir / f["path"]
        if not path.is_file():
            problems.append(f"{f['path']} is listed and missing")
            continue
        data = path.read_bytes()
        if len(data) != f["bytes"]:
            problems.append(f"{f['path']} is {len(data)} bytes, the manifest says {f['bytes']}")
        if hashlib.sha256(data).hexdigest() != f["sha256"]:
            problems.append(f"{f['path']} does not match its SHA-256")
    present = {p.relative_to(out_dir).as_posix() for p in (out_dir / "data").rglob("*") if p.is_file()} \
        if (out_dir / "data").exists() else set()
    for extra in sorted(present - listed):
        problems.append(f"{extra} is in the package and not in the manifest")
    return {"ok": not problems, "problems": problems, "signed": signed, "files": len(manifest["files"]),
            "matter_number": manifest["matter_number"], "demand_reference": manifest["demand_reference"],
            "produced_at": manifest["produced_at"], "note": problems_note, "manifest": manifest}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("package", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="a folder to unpack into")
    parser.add_argument("--public-key", help="the platform's public key, in hex")
    parser.add_argument("--passphrase", help="the passphrase; asked for if left out")
    args = parser.parse_args()
    passphrase = args.passphrase or getpass.getpass("Passphrase: ")
    report = open_package(args.package, passphrase, args.out, args.public_key)
    if report["ok"]:
        print(f"OK: {report['files']} file{'s' if report['files'] != 1 else ''} for matter {report['matter_number']} "
              f"(demand {report['demand_reference']}, produced {report['produced_at']}). Signature verified.")
        if report["note"]:
            print("Note:", report["note"])
        return 0
    for problem in report["problems"]:
        print("PROBLEM:", problem)
    return 1


if __name__ == "__main__":
    sys.exit(main())
