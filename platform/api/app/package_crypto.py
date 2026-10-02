"""The file format of a legal export package, and the signature on its manifest.

A package is one file. Its contents are a zip, encrypted with a passphrase that only the hold's custodian is
given. The format is written down here and read by an independent tool (`scripts/client/open_legal_package.py`),
which a recipient uses and which has no code in common with this module. A format that only its own writer can
read proves nothing, and a check (U103) opens a real package with the recipient's tool.

THE FORMAT, `MLEP1`

    magic      5 bytes  "MLEP1"
    salt       16 bytes  random, for the key derivation
    prefix     8 bytes   random, the first part of every chunk's nonce
    chunks     repeated until the last:
                 length   4 bytes, big-endian: how many bytes of ciphertext follow
                 flag     1 byte: 0 for a chunk with more after it, 1 for the last
                 ciphertext   AES-256-GCM of up to 1 MiB, tag included

    key         scrypt(passphrase, salt, n=2**15, r=8, p=1), 32 bytes
    nonce       prefix, then the chunk number as 4 bytes, big-endian (12 bytes)
    associated  the flag byte, so a chunk cannot be moved, reordered or have the end cut off without the
    data        authentication failing

The package is read in chunks, so neither writing nor reading needs the whole of it in memory.
"""

from __future__ import annotations

import hashlib
import os
import struct
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from . import config

MAGIC = b"MLEP1"
CHUNK = 1024 * 1024
SCRYPT_N = 2 ** 15


def _key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=SCRYPT_N, r=8, p=1).derive(passphrase.encode("utf-8"))


def encrypt_file(source: Path, target: Path, passphrase: str) -> tuple[int, str]:
    """Encrypt `source` into `target`. Returns the size of the package and its SHA-256."""
    salt, prefix = os.urandom(16), os.urandom(8)
    aead = AESGCM(_key(passphrase, salt))
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as src, target.open("wb") as out:
        def put(data: bytes) -> None:
            nonlocal size
            out.write(data)
            digest.update(data)
            size += len(data)

        put(MAGIC + salt + prefix)
        number = 0
        block = src.read(CHUNK)
        while True:
            following = src.read(CHUNK)
            last = not following
            flag = b"\x01" if last else b"\x00"
            ciphertext = aead.encrypt(prefix + struct.pack(">I", number), block, flag)
            put(struct.pack(">I", len(ciphertext)) + flag + ciphertext)
            if last:
                break
            block, number = following, number + 1
    return size, digest.hexdigest()


def decrypt_file(source: Path, target: Path, passphrase: str) -> None:
    """The reverse, used by the platform's own checks. A recipient uses the standalone tool instead."""
    with source.open("rb") as src, target.open("wb") as out:
        if src.read(5) != MAGIC:
            raise ValueError("not a legal export package")
        salt, prefix = src.read(16), src.read(8)
        aead = AESGCM(_key(passphrase, salt))
        number = 0
        while True:
            header = src.read(5)
            if len(header) < 5:
                raise ValueError("the package ends before its last block")
            length, flag = struct.unpack(">I", header[:4])[0], header[4:5]
            out.write(aead.decrypt(prefix + struct.pack(">I", number), src.read(length), flag))
            if flag == b"\x01":
                return
            number += 1


# --------------------------------------------------------------- signing --


def _private() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(hashlib.sha256(("munitas-signing:" + config.SIGNING_KEY).encode()).digest())


def public_key_hex() -> str:
    return _private().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def sign(data: bytes) -> str:
    """The signature, in hex, over the SHA-256 of `data`."""
    return _private().sign(hashlib.sha256(data).digest()).hex()


def verify(public_hex: str, data: bytes, signature_hex: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_hex)).verify(
            bytes.fromhex(signature_hex), hashlib.sha256(data).digest())
        return True
    except Exception:
        return False
