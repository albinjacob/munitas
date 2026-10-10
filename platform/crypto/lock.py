"""Locking one column's value under a person's key.

A locked value is a short piece of text, so it sits in any file format as an ordinary string. It carries a version marker, so a later scheme
can be told apart from this one. The organisation, the person code, the column and the row's own id are bound in as additional
authenticated data: a locked value cannot be moved to another person, another column or another row and still open.
"""

from __future__ import annotations

import base64
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .envelope import NONCE_BYTES

PREFIX = "locked:v1:"


def is_locked(value: object) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def _aad(tenant_id: str, person_code: str, column: str, record_id: str) -> bytes:
    return f"munitas|lock|{tenant_id}|{person_code}|{column}|{record_id}".encode("utf-8")


def lock_value(key: bytes, tenant_id: str, person_code: str, column: str, record_id: str, value: object) -> str:
    """The value, of any JSON type, as locked text. Its type comes back when it is opened."""
    nonce = os.urandom(NONCE_BYTES)
    body = AESGCM(key).encrypt(nonce, json.dumps(value).encode("utf-8"), _aad(tenant_id, person_code, column, record_id))
    return PREFIX + base64.urlsafe_b64encode(nonce + body).decode("ascii")


def unlock_value(key: bytes, tenant_id: str, person_code: str, column: str, record_id: str, locked: str) -> object:
    blob = base64.urlsafe_b64decode(locked[len(PREFIX):])
    return json.loads(AESGCM(key).decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:], _aad(tenant_id, person_code, column, record_id)))
