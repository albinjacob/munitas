"""One person's data in a sealed table: locking it under a key of the person's own, reading it back, and erasing the person.

A sealed table never changes, and a person can still be forgotten. The columns that identify a person are locked under a key made for that
person, and erasing the person destroys that key and nothing else. The sealed file and its fingerprint stay exactly as they were: the locked
columns become unreadable and everything else stays readable.

The unit is a person, found through the contract's subject rule. The customer supplies the identifier (with its kind, the system or number it
comes from) and the platform makes the person code and the key. The identifier itself is never stored: a blind index finds the person.

Everything here asks a `KeyService` for keys and never reaches for key material any other way, so what holds the keys can be replaced.
This first version finds the person by a column of the table; the other ways a contract can name a subject are refused with that said.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

from crypto import BuiltInKeyService, DestroyedKeyError, KeyService, lock_value, unlock_value

from . import config, db, key_store, opa, privacy_contract, storage, versions

FILE_NAME = "table.jsonl"
READ_PURPOSE = "read locked columns"
PERSON_COLUMN = "person_code"


class Refused(Exception):
    """The table or the contract cannot be sealed. Every problem is listed, because one at a time would be one attempt each."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class NotPermitted(Exception):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class ErasureHeld(Exception):
    """A legal hold stands over the organisation, and a destroyed key cannot be brought back."""


class NoSuchVersion(LookupError):
    pass


_service: KeyService | None = None


def key_service() -> KeyService:
    """The platform's key service: the built-in one, with its master key read from the mounted file."""
    global _service
    if _service is None:
        _service = BuiltInKeyService.from_file(Path(config.KEY_SERVICE_MASTER_FILE), key_store.PostgresKeyStore())
    return _service


# Finding a person ------------------------------------------------------------


def blind_index(secret: bytes, kind: str, value: str) -> str:
    """The identifier as the lookup knows it. The kind is part of the input, so identifiers of different kinds never meet, and each part
    is length-prefixed, so no choice of kind or value can be made to read as another pair."""
    kind, value = kind.strip().lower(), str(value).strip()
    return hmac.new(secret, f"{len(kind)}:{kind}{len(value)}:{value}".encode("utf-8"), hashlib.sha256).hexdigest()


def find_person(service: KeyService, tenant_id: str, kind: str, value: str) -> str | None:
    row = db.one("select person_code from subject_lookup where tenant_id = %s and blind_index = %s",
                 (tenant_id, blind_index(service.lookup_secret(tenant_id), kind, value)))
    return row["person_code"] if row else None


def resolve_person(service: KeyService, tenant_id: str, kind: str, value: str, actor: str) -> str:
    """The person code for an identifier, made with its key the first time the identifier is seen."""
    index = blind_index(service.lookup_secret(tenant_id), kind, value)
    row = db.one("select person_code from subject_lookup where tenant_id = %s and blind_index = %s", (tenant_id, index))
    if row:
        return row["person_code"]
    code = "T-" + secrets.token_hex(8)
    service.make_person_key(tenant_id, code, actor)
    made = db.execute("insert into subject_lookup (tenant_id, blind_index, person_code) values (%s, %s, %s) "
                      "on conflict (tenant_id, blind_index) do nothing returning person_code", (tenant_id, index, code))
    if made:
        return code
    # Somebody else made this person between the look and the insert. Theirs stands, and the key made for this attempt goes.
    service.destroy(tenant_id, code, actor)
    return db.one("select person_code from subject_lookup where tenant_id = %s and blind_index = %s", (tenant_id, index))["person_code"]


# Sealing ---------------------------------------------------------------------


def _blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _problems(contract: dict, rows: object) -> list[str]:
    problems = privacy_contract.check(contract)
    if problems:
        return problems
    subject = privacy_contract.subject_rule(contract)
    if not privacy_contract.erasable(contract):
        return ["sealing a table that cannot be erased through a privacy contract is not available in this version"]
    if subject is None or subject.get("foundBy") != "column":
        found = subject.get("foundBy") if subject else None
        return [f"finding the person by {found!r} is not available in this version; this version reads the person from a column"]
    if not isinstance(rows, list) or not rows:
        return ["the table has no rows"]
    cols = privacy_contract.columns(contract)
    names = {c["name"] for c in cols}
    key = next(c["name"] for c in cols if c["primaryKey"])
    problems, seen = [], set()
    for position, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            problems.append(f"row {position} is not a set of columns and values")
            continue
        label = str(row.get(key)) if not _blank(row.get(key)) else f"row {position}"
        for extra in sorted(set(row) - names):
            problems.append(f"{label}: the column {extra} is not in the contract")
        if _blank(row.get(key)):
            problems.append(f"{label}: has no {key}, which is the primary key")
        elif label in seen:
            problems.append(f"{label}: the {key} appears more than once")
        seen.add(label)
        if _blank(row.get(subject["column"])):
            problems.append(f"{label}: names nobody, because {subject['column']} (the {subject['kind']}) is empty")
    return problems


def seal_table(service: KeyService, *, dataset_id: str, identity: dict, contract: dict, rows: list[dict]) -> dict:
    """Seal rows as a version of a dataset, with the identifying columns locked under each person's own key.

    The whole table is refused, with every problem listed, when the contract is wrong or any row names nobody. Nothing is made or written
    for a refusal: the checks run before the first key is made.
    """
    from . import ingest  # the same intake rules as every other way of bringing data in
    dataset = ingest._dataset(dataset_id)
    ingest._mine(dataset, identity)
    ingest._require_intake_role(identity)
    problems = _problems(contract, rows)
    if problems:
        raise Refused(problems)

    tenant = dataset["tenant_id"]
    cols = privacy_contract.columns(contract)
    subject = privacy_contract.subject_rule(contract)
    key_column = next(c["name"] for c in cols if c["primaryKey"])
    actor = identity["id"]

    codes = [resolve_person(service, tenant, subject["kind"], row[subject["column"]], actor) for row in rows]
    keys = service.unwrap_many(tenant, codes, actor, "seal a table")

    lines = []
    for row, code in zip(rows, codes):
        record_id = str(row[key_column])
        sealed_row = {}
        for c in cols:
            value = row.get(c["name"])
            if c["treatment"] == "lock":
                value = lock_value(keys[code], tenant, code, c["name"], record_id, value)
            elif c["treatment"] == privacy_contract.REPLACED:
                value = code
            sealed_row[c["name"]] = value
        sealed_row[PERSON_COLUMN] = code
        lines.append(json.dumps(sealed_row, sort_keys=True))
    payload = ("\n".join(lines) + "\n").encode("utf-8")

    backend = dataset["storage_backend"] or "seaweedfs"
    prefix = versions.next_version(tenant, dataset_id)["storage_prefix"]
    storage.admin_client_for(backend, tenant).put_object(Bucket=storage.bucket_for(backend, tenant), Key=f"{prefix}/{FILE_NAME}", Body=payload)
    manifest = [{"key": f"{prefix}/{FILE_NAME}", "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}]

    schema_id = _register(tenant, contract, cols, key_column)
    version = versions.seal(tenant_id=tenant, dataset_id=dataset_id, schema_id=schema_id, visibility_class="RAW",
                            storage_backend=backend, object_manifest=manifest, record_count=len(rows))
    return {**version, "records": len(rows), "people": len(set(codes)), "sealed_by": actor}


def _register(tenant: str, contract: dict, cols: list[dict], key_column: str) -> str:
    """The schema contract and the privacy contract it belongs to, content addressed so the same contract is the same rows.

    The schema contract's own digest is taken over the whole privacy contract, because two contracts with the same columns can differ in
    what they do to them, and the version records only the schema contract's id.
    """
    digest = versions.content_hash({"tenant": tenant, "privacy": contract})
    existing = db.one("select schema_id from privacy_contract where tenant_id = %s and content_hash = %s", (tenant, digest))
    if existing:
        return str(existing["schema_id"])
    fields = [{"name": c["name"], "type": c["logicalType"], "sensitivity": c["classification"], "treatment": c["treatment"],
               "added_by": "privacy contract"} for c in cols]
    fields.append({"name": PERSON_COLUMN, "type": "string", "sensitivity": "none", "added_by": "privacy contract"})
    schema_id = str(uuid.uuid4())
    with db.cursor(commit=True) as cur:
        cur.execute("insert into schema_contract (id, tenant_id, name, fields, primary_key, content_hash) values (%s, %s, %s, %s, %s, %s)",
                    (schema_id, tenant, contract["name"], json.dumps(fields), [key_column], digest))
        cur.execute("insert into privacy_contract (id, tenant_id, name, version, document, schema_id, content_hash) "
                    "values (%s, %s, %s, %s, %s, %s, %s)",
                    (str(uuid.uuid4()), tenant, contract["name"], contract["version"], json.dumps(contract), schema_id, digest))
    return schema_id


# Reading ---------------------------------------------------------------------


def read_rows(service: KeyService, *, version_id: str, identity: dict) -> list[dict]:
    """The rows of a sealed table with the locked columns opened, for a permitted reader. The decision is recorded either way.

    A person who was erased does not fail the table: their locked columns come back as an erased marker with the date, and every other
    column and every other person reads as before.
    """
    version = db.one("select v.id, v.tenant_id, v.schema_id, v.storage_prefix, v.storage_backend, v.object_manifest, v.visibility_class "
                     "from dataset_version v where v.id = %s", (version_id,))
    if not version or version["tenant_id"] != identity["tenant_id"]:
        raise NoSuchVersion(version_id)
    contract = db.one("select document from privacy_contract where tenant_id = %s and schema_id = %s", (version["tenant_id"], version["schema_id"]))
    if not contract:
        raise NoSuchVersion(f"{version_id} was not sealed under a privacy contract")

    allowed, reasons = opa.may_read_locked_columns({"person": {"id": identity["id"], "roles": identity["roles"]}})
    db.execute(
        """insert into access_decision (principal, principal_kind, principal_roles, tenant_id, dataset_version_id, requested_class,
                                        purpose, allowed, reasons, phase)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'policy') returning principal""",
        (identity["id"], identity.get("kind", "human"), identity["roles"], version["tenant_id"], version_id,
         version["visibility_class"], READ_PURPOSE, allowed, reasons))
    if not allowed:
        raise NotPermitted(reasons)

    tenant = version["tenant_id"]
    entry = next(m for m in version["object_manifest"] if m["key"].endswith(FILE_NAME))
    body = storage.admin_client_for(version["storage_backend"], tenant).get_object(
        Bucket=storage.bucket_for(version["storage_backend"], tenant), Key=entry["key"])["Body"].read()
    rows = [json.loads(line) for line in body.decode("utf-8").splitlines() if line]

    locked = [c["name"] for c in privacy_contract.columns(contract["document"]) if c["treatment"] == "lock"]
    key_column = next(c["name"] for c in privacy_contract.columns(contract["document"]) if c["primaryKey"])
    keys = service.unwrap_many(tenant, [r[PERSON_COLUMN] for r in rows], identity["id"], READ_PURPOSE)
    out = []
    for row in rows:
        code, row = row[PERSON_COLUMN], dict(row)
        if keys[code] is None:
            _, when = service.status(tenant, code)
            marker = {"erased_on": (when or datetime.now(timezone.utc)).date().isoformat()}
            for name in locked:
                row[name] = marker
        else:
            for name in locked:
                row[name] = unlock_value(keys[code], tenant, code, name, str(row[key_column]), row[name])
        out.append(row)
    return out


# Erasing ---------------------------------------------------------------------


def erase_person(service: KeyService, tenant_id: str, kind: str, value: str, identity: dict, reason: str) -> dict:
    """Destroy a person's key, delete the lookup row and leave a tombstone. The sealed files are not touched.

    An internal operation: who may ask, and the approval and waiting period, come later. A legal hold over the organisation refuses it,
    because a destroyed key cannot be brought back. Safe to run again: each step is a no-op once done, so a run that stopped half way
    is finished by running it again.
    """
    if db.one("select tenant_hold_state(%s) as state", (tenant_id,))["state"] != "none":
        raise ErasureHeld(f"a legal hold stands over {tenant_id}, so no person is erased while it does")
    code = find_person(service, tenant_id, kind, value)
    if code is None:
        return {"found": False, "key_destroyed": False, "person_code": None}
    destroyed = service.destroy(tenant_id, code, identity["id"])
    db.execute("delete from subject_lookup where tenant_id = %s and person_code = %s returning person_code", (tenant_id, code))
    db.execute("insert into tombstone (record_id, tenant_id, reason, requested_by) values (%s, %s, %s, %s) "
               "on conflict (record_id) do nothing returning record_id", (f"person:{code}", tenant_id, reason, identity["id"]))
    return {"found": True, "key_destroyed": destroyed, "person_code": code, "tombstoned": True}
