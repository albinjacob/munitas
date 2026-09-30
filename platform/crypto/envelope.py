"""Envelope encryption with per-record data keys.

This exists to make one specific claim testable: that deleting a record is
possible without violating dataset immutability. A record is encrypted under its
own data key, that key is wrapped by a tenant key and stored in `record_key`, and
deletion destroys the wrapped key rather than the ciphertext. The ciphertext
stays where it is, in every sealed dataset version that ever held it, and becomes
unreadable everywhere at once. V10 asserts exactly that.

DEMO GRADE. Read this before believing anything about it:

  * The master key comes from an environment variable, so it sits in the
    process image and in `docker inspect` output.
  * Tenant keys are derived from the master key, so there is one root of trust
    and no per-tenant isolation of key material.
  * There is no key rotation, no HSM, no split knowledge, no audit of key use.

A production deployment replaces this module with a real key manager. Because
Vault moved to BUSL and OpenBao is MPL, neither cleared the permissive licence
bar for this build, so nothing is wired in yet. The module boundary is drawn so
that swapping it means reimplementing four methods.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# AES-GCM nonces are 96 bits. They are generated per operation and never reused
# under the same key, which is what GCM requires and what makes reuse fatal.
NONCE_BYTES = 12
KEY_BYTES = 32


class DestroyedKeyError(Exception):
    """The record's key was destroyed. The ciphertext is unrecoverable."""


class UnknownRecordError(Exception):
    """No key was ever stored for this record."""


@dataclass(frozen=True)
class SealedRecord:
    """Ciphertext plus the wrapped key that opens it.

    The wrapped key goes to `record_key` in PostgreSQL and the ciphertext goes to
    object storage. They are separated on purpose: destroying a row in a database
    is reliable, and overwriting every copy of an object in every snapshot is
    not.
    """

    ciphertext: bytes
    wrapped_key: bytes


class EnvelopeCrypto:
    """Per-record data keys wrapped by a per-tenant key.

    The tenant key is derived rather than stored, so there is no second thing to
    keep in sync. The cost is that the master key can regenerate any tenant key,
    which is the main reason this is not production grade.
    """

    def __init__(self, master_key: bytes | None = None) -> None:
        if master_key is None:
            raw = os.environ.get("MUNITAS_MASTER_KEY")
            if not raw:
                raise RuntimeError(
                    "MUNITAS_MASTER_KEY is not set. Refusing to invent one, "
                    "because a default key that works silently is worse than a "
                    "startup failure."
                )
            master_key = raw.encode("utf-8")
        self._master = master_key

    def _tenant_key(self, tenant_id: str) -> bytes:
        """Derive a tenant key. Same tenant, same key, every time."""
        kdf = HKDF(
            algorithm=hashes.SHA256(),
            length=KEY_BYTES,
            salt=None,
            info=b"munitas/tenant/" + tenant_id.encode("utf-8"),
        )
        return kdf.derive(self._master)

    def seal(self, tenant_id: str, record_id: str, plaintext: bytes) -> SealedRecord:
        """Encrypt a record under a fresh data key and wrap that key.

        `record_id` and `tenant_id` are bound into both layers as additional
        authenticated data, so a wrapped key from one record cannot be
        substituted for another's even by someone holding the database.
        """
        data_key = AESGCM.generate_key(bit_length=256)
        ciphertext = self._encrypt(data_key, plaintext, self._aad(tenant_id, record_id))
        wrapped = self._encrypt(
            self._tenant_key(tenant_id), data_key, self._aad(tenant_id, record_id)
        )
        return SealedRecord(ciphertext=ciphertext, wrapped_key=wrapped)

    def open(
        self,
        tenant_id: str,
        record_id: str,
        ciphertext: bytes,
        wrapped_key: bytes | None,
    ) -> bytes:
        """Decrypt a record.

        A `None` wrapped key means the key was destroyed. That is the deletion
        path, not an error condition in the system, so it raises a distinct
        exception the caller is expected to handle rather than a generic one.
        """
        if wrapped_key is None:
            raise DestroyedKeyError(
                f"key for record {record_id} was destroyed; ciphertext is unrecoverable"
            )
        aad = self._aad(tenant_id, record_id)
        data_key = self._decrypt(self._tenant_key(tenant_id), wrapped_key, aad)
        return self._decrypt(data_key, ciphertext, aad)

    @staticmethod
    def _aad(tenant_id: str, record_id: str) -> bytes:
        return f"munitas|{tenant_id}|{record_id}".encode("utf-8")

    @staticmethod
    def _encrypt(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)

    @staticmethod
    def _decrypt(key: bytes, blob: bytes, aad: bytes) -> bytes:
        nonce, body = blob[:NONCE_BYTES], blob[NONCE_BYTES:]
        return AESGCM(key).decrypt(nonce, body, aad)
