"""Where the built-in key service keeps its wrapped keys and its log: the platform's own database.

Only wrapped keys are stored here, never a key in the clear. The master key is not here at all.
"""

from __future__ import annotations

from datetime import datetime

from . import db


class PostgresKeyStore:
    def get_org_key(self, tenant_id: str) -> bytes | None:
        row = db.one("select wrapped_key from org_key where tenant_id = %s", (tenant_id,))
        if row is None:
            return None
        if row["wrapped_key"] is None:
            raise RuntimeError(f"the key of organisation {tenant_id} was destroyed")
        return bytes(row["wrapped_key"])

    def put_org_key(self, tenant_id: str, wrapped: bytes) -> bytes:
        # Two callers making the first key at once must agree, so the insert yields to an existing row and the stored one is read back.
        db.execute("insert into org_key (tenant_id, wrapped_key) values (%s, %s) on conflict (tenant_id) do nothing returning tenant_id",
                   (tenant_id, wrapped))
        return bytes(db.one("select wrapped_key from org_key where tenant_id = %s", (tenant_id,))["wrapped_key"])

    def get_person_key(self, tenant_id: str, person_code: str) -> tuple[bytes | None, datetime | None] | None:
        row = db.one("select wrapped_key, destroyed_at from subject_key where tenant_id = %s and person_code = %s", (tenant_id, person_code))
        if row is None:
            return None
        return (bytes(row["wrapped_key"]) if row["wrapped_key"] is not None else None), row["destroyed_at"]

    def put_person_key(self, tenant_id: str, person_code: str, wrapped: bytes) -> None:
        db.execute("insert into subject_key (tenant_id, person_code, wrapped_key) values (%s, %s, %s) returning person_code",
                   (tenant_id, person_code, wrapped))

    def destroy_person_key(self, tenant_id: str, person_code: str) -> bool:
        row = db.execute(
            "update subject_key set wrapped_key = null, destroyed_at = now() "
            "where tenant_id = %s and person_code = %s and destroyed_at is null returning person_code", (tenant_id, person_code))
        return row is not None

    def log(self, tenant_id: str, person_code: str | None, action: str, actor: str, purpose: str | None) -> None:
        db.execute("insert into key_use_log (tenant_id, person_code, action, actor, purpose) values (%s, %s, %s, %s, %s) returning id",
                   (tenant_id, person_code, action, actor, purpose))
