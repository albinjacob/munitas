"""The key service: the one place the platform asks for, opens and destroys a person's key.

The platform never holds a person key in the clear for longer than one operation, and never stores one except wrapped. What holds the keys
sits behind this interface, so the built-in service below can be replaced by a cloud key service without the platform changing: an
organisation key is that service's master encryption key, a person key is a data encryption key, and destroying a person key is deleting our
own row, which takes effect at once.

Four keys are involved. The master key is a file that is mounted into the process, never an environment variable and never in the database.
An organisation key is random and is stored wrapped by the master key (it is not derived from it, so one organisation's key can be
destroyed on its own). The lookup secret, used for blind indexes, is derived from the organisation key. A person key is random and is
stored wrapped by the organisation key. The person key is the only one that touches data.

Every use is logged: making a key, opening one, destroying one.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Protocol

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .envelope import KEY_BYTES, NONCE_BYTES, DestroyedKeyError, UnknownRecordError


class KeyService(ABC):
    """What the platform needs from whatever holds the keys."""

    @abstractmethod
    def lookup_secret(self, tenant_id: str) -> bytes:
        """The secret the blind index is computed with. Stable for an organisation."""

    @abstractmethod
    def make_person_key(self, tenant_id: str, person_code: str, actor: str) -> bytes:
        """Make a key for a person, store it wrapped, and return it for the one operation that needs it."""

    @abstractmethod
    def unwrap(self, tenant_id: str, person_code: str, actor: str, purpose: str, audit: bool = True) -> bytes:
        """Open a person's key. Raises DestroyedKeyError once destroyed, UnknownRecordError when never made.

        Every opening is written to the key log. `audit=False` is for the later reads of something already opened and logged (a recording read a
        block at a time): the key is still checked for destruction every time, and the log is not filled with one line per block.
        """

    @abstractmethod
    def unwrap_many(self, tenant_id: str, person_codes: list[str], actor: str, purpose: str) -> dict[str, bytes | None]:
        """Open several people's keys in one call. A destroyed key is None, not an error, so one erased person never fails a whole table."""

    @abstractmethod
    def destroy(self, tenant_id: str, person_code: str, actor: str) -> bool:
        """Destroy a person's key. True when this call destroyed it, False when it was already gone."""

    @abstractmethod
    def status(self, tenant_id: str, person_code: str) -> tuple[str, datetime | None]:
        """('live' | 'destroyed' | 'unknown', when it was destroyed)."""


class KeyStore(Protocol):
    """Where the built-in service keeps what it stores. The platform supplies one backed by its database."""

    def get_org_key(self, tenant_id: str) -> bytes | None: ...
    def put_org_key(self, tenant_id: str, wrapped: bytes) -> bytes:
        """Store the wrapped organisation key unless one exists. Returns whichever is stored, so two racing callers agree."""
    def get_person_key(self, tenant_id: str, person_code: str) -> tuple[bytes | None, datetime | None] | None: ...
    def put_person_key(self, tenant_id: str, person_code: str, wrapped: bytes) -> None: ...
    def destroy_person_key(self, tenant_id: str, person_code: str) -> bool: ...
    def log(self, tenant_id: str, person_code: str | None, action: str, actor: str, purpose: str | None) -> None: ...


class BuiltInKeyService(KeyService):
    """Master key from a file, random organisation keys wrapped by it, random person keys wrapped by those."""

    def __init__(self, master_key: bytes, store: KeyStore) -> None:
        if len(master_key) != KEY_BYTES:
            raise RuntimeError(f"the master key must be exactly {KEY_BYTES} bytes")
        self._master = master_key
        self._store = store

    @classmethod
    def from_file(cls, path: str | Path, store: KeyStore) -> "BuiltInKeyService":
        """Read the master key from a file. A missing or empty file stops the service starting: a default key that works silently is worse."""
        p = Path(path)
        if not p.is_file():
            raise RuntimeError(f"the master key file {p} does not exist. Refusing to invent a key")
        raw = p.read_bytes().strip()
        if not raw:
            raise RuntimeError(f"the master key file {p} is empty")
        if len(raw) != KEY_BYTES:
            # A text file holding a passphrase or hex is accepted by being stretched to a key, never used as it is.
            raw = HKDF(algorithm=hashes.SHA256(), length=KEY_BYTES, salt=None, info=b"munitas/master-file").derive(raw)
        return cls(raw, store)

    # The organisation's key and the secrets made from it ------------------

    def _org_key(self, tenant_id: str, actor: str = "platform") -> bytes:
        wrapped = self._store.get_org_key(tenant_id)
        if wrapped is None:
            fresh = os.urandom(KEY_BYTES)
            wrapped = self._store.put_org_key(tenant_id, _seal(self._master, fresh, _aad("org", tenant_id)))
            self._store.log(tenant_id, None, "make_org", actor, None)
        return _open(self._master, wrapped, _aad("org", tenant_id))

    def lookup_secret(self, tenant_id: str) -> bytes:
        return HKDF(algorithm=hashes.SHA256(), length=KEY_BYTES, salt=None,
                    info=b"munitas/lookup/" + tenant_id.encode()).derive(self._org_key(tenant_id))

    # A person's key ------------------------------------------------------

    def make_person_key(self, tenant_id: str, person_code: str, actor: str) -> bytes:
        key = os.urandom(KEY_BYTES)
        self._store.put_person_key(tenant_id, person_code, _seal(self._org_key(tenant_id, actor), key, _aad("person", tenant_id, person_code)))
        self._store.log(tenant_id, person_code, "make_person", actor, None)
        return key

    def unwrap(self, tenant_id: str, person_code: str, actor: str, purpose: str, audit: bool = True) -> bytes:
        return self._unwrap(tenant_id, person_code, self._org_key(tenant_id, actor), actor, purpose, audit)

    def _unwrap(self, tenant_id: str, person_code: str, org_key: bytes, actor: str, purpose: str, audit: bool = True) -> bytes:
        row = self._store.get_person_key(tenant_id, person_code)
        if row is None:
            raise UnknownRecordError(f"no key was ever made for {person_code}")
        wrapped, _ = row
        if wrapped is None:
            raise DestroyedKeyError(f"the key for {person_code} was destroyed; what it locked is unrecoverable")
        key = _open(org_key, wrapped, _aad("person", tenant_id, person_code))
        if audit:
            self._store.log(tenant_id, person_code, "unwrap", actor, purpose)
        return key

    def unwrap_many(self, tenant_id: str, person_codes: list[str], actor: str, purpose: str) -> dict[str, bytes | None]:
        org_key = self._org_key(tenant_id, actor)
        out: dict[str, bytes | None] = {}
        for code in dict.fromkeys(person_codes):
            try:
                out[code] = self._unwrap(tenant_id, code, org_key, actor, purpose)
            except DestroyedKeyError:
                out[code] = None
        return out

    def destroy(self, tenant_id: str, person_code: str, actor: str) -> bool:
        destroyed = self._store.destroy_person_key(tenant_id, person_code)
        self._store.log(tenant_id, person_code, "destroy", actor, None)
        return destroyed

    def status(self, tenant_id: str, person_code: str) -> tuple[str, datetime | None]:
        row = self._store.get_person_key(tenant_id, person_code)
        if row is None:
            return "unknown", None
        wrapped, destroyed_at = row
        return ("destroyed", destroyed_at) if wrapped is None else ("live", None)


def _aad(kind: str, *parts: str) -> bytes:
    return ("munitas|" + kind + "|" + "|".join(parts)).encode("utf-8")


def _seal(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    nonce = os.urandom(NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def _open(key: bytes, blob: bytes, aad: bytes) -> bytes:
    return AESGCM(key).decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:], aad)
