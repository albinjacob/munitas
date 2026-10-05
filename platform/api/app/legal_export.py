"""Producing an organisation's records for a legal matter.

A legal hold (lifecycle.py) keeps an organisation's records. It does not let anybody get them out, and a court,
a regulator or the organisation's own lawyers may demand them. This is how some of them leave. The design, with
the reasons for each choice, is in docs/internal/design/legal-export.md.

THE FLOW

    requested -> approved -> confirmed -> producing -> ready -> expired
    (admin A)    (admin B)   (the hold's    (a job)     (a link,   (package
                              custodian)                 once)      deleted)

Three different people, and none of them reads the contents. A platform administrator holds no standing access to
what an organisation contains (the policy's role_floor), and an export does not change that: the package is built by
a job, encrypted with a passphrase that only the custodian is given, and opened by its recipient.

WHAT IT REQUIRES

  * A legal hold in force on the organisation. No hold, no export. That is what stops this being a way to take data
    out of any organisation.
  * The demand it answers: who demanded it, their reference, the date, and what it asks for in their words. A hold
    says keep and a demand says produce, so the two are recorded separately.
  * A scope: whole datasets, every sealed version of each. Nothing narrower than a file is offered, because the
    platform does not decide what is relevant or privileged. That is the lawyers' work.

WHAT A PACKAGE HOLDS

    manifest.json, manifest.sig   every file with its size and SHA-256, signed with the platform's key
    chain-of-custody.json         who asked, approved and confirmed, and when, and the hold and the demand
    audit-trail.csv               who was allowed or refused what, for the named versions (if asked for)
    erased.json                   records and files that no longer exist, and when they went
    data/<dataset>/v<n>/...       the files as stored, byte for byte

FILTERING A TABLE TO THE ROWS FOR NAMED PEOPLE

A demand often asks for one person's records, and a table holds thousands of people's. Handing over the whole table would
send everybody else's data out with it. So a request may name a table dataset and one column that identifies a person
(a patient id, say), and the custodian the hold names, who answers for the records, supplies the values: the people the
demand is about. The package then holds one file per version, a CSV of exactly the rows whose column equals one of the
values. Nothing else of that dataset is included: not the original files, and not the Iceberg table that holds every
row. The custodian sees how many rows each filter matches, and which values matched nothing, before confirming, and never
the rows. The platform administrators never see the values at all.

Matching is exact, as text, and case-sensitive. A person who appears under a spelling variant, or in a free-text column,
or in another table, is not found by it, and that is the custodian's to check: the count and the unmatched values are
shown for that reason. The manifest records the column, how many values, how many rows matched out of how many, and the
fingerprint of the sealed records the rows were taken from.

A file's hash is checked against the hash recorded when its version was sealed. A mismatch stops the production and
says which file, because a package that quietly contains altered bytes is worse than no package.

DELIVERY

The platform sends nothing. A ready package can be downloaded through a link that a platform administrator makes: it
lives a few days and works a few times. The package is encrypted, so a link alone opens nothing.

EVERYTHING IS RECORDED

Each step writes a history row and an audit row (`access_decision`, with the demand's reference as the purpose), so the
seven-year audit trail of a deleted organisation shows every production. Refusals are recorded as carefully as grants.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import secrets
import shutil
import tempfile
import uuid
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from crypto import EnvelopeCrypto
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import config, db, logs, opa, package_crypto, seaweed
from .auth import current_session, current_session_while_closing

log = logs.get_logger("legal_export")

router = APIRouter(prefix="/legal-exports", tags=["legal-export"])

_crypto = EnvelopeCrypto()
# The passphrase is sealed under a key of its own, so that destroying nothing else ever opens it.
_PASSPHRASE_SCOPE = "legal-export-passphrase"

# A job that was producing when the API stopped never finishes by itself. After this long it is failed, which is
# honest, and a new request can be made.
STALLED_AFTER_MINUTES = 30

_PUBLIC = """e.id, e.tenant_id, e.hold_id, e.status, e.demand_authority, e.demand_reference, e.demanded_on,
       e.demand_text, e.dataset_ids, e.include_audit, e.data_from, e.data_to, e.recipient_name,
       e.recipient_organisation, e.recipient_email, e.requested_by, rq.label as requested_by_label, e.requested_at,
       e.approved_by, ap.label as approved_by_label, e.approved_at, e.approval_note,
       e.confirmed_by, cf.label as confirmed_by_label, e.confirmed_at, e.confirm_note,
       e.refused_by, e.refused_at, e.refusal_reason, e.production_started_at, e.produced_at, e.failure,
       e.filters, e.filter_results, e.package_bytes, e.package_sha256, e.manifest_sha256, e.signature, e.file_count,
       e.passphrase_revealed_at, e.expires_at, e.expired_at,
       h.matter_number, h.matter_name, h.custodian_id, h.status as hold_status,
       (select count(*) from legal_export_link l where l.export_id = e.id) as links_made"""
_FROM = """from legal_export e
       join legal_hold h on h.id = e.hold_id
       join directory rq on rq.id = e.requested_by
       left join directory ap on ap.id = e.approved_by
       left join directory cf on cf.id = e.confirmed_by"""


def _refuse(reasons: list[str], status: int = 403):
    raise HTTPException(status, {"reasons": reasons})


def _clean(rows):
    return json.loads(json.dumps(rows, default=str))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_admin(session: dict) -> bool:
    return "platform_admin" in session["roles"]


def _actor(session: dict) -> dict:
    return {"id": session["id"], "tenant_id": session["tenant_id"], "roles": session["roles"]}


def _event(tenant_id: str, actor: str, event: str, hold_id: str | None = None, detail: dict | None = None) -> None:
    db.execute(
        "insert into lifecycle_event (tenant_id, hold_id, actor, event, detail) values (%s, %s, %s, %s, %s::jsonb)",
        (tenant_id, hold_id, actor, event, json.dumps(detail or {}, default=str)),
    )


def _audit(tenant_id: str, principal: str, kind: str, roles: list[str], reference: str, what: str,
           allowed: bool, reasons: list[str] | None = None) -> None:
    """A row in the audit trail, which a deletion keeps for seven years."""
    db.execute(
        """insert into access_decision (principal, principal_kind, principal_roles, tenant_id, purpose, allowed,
                  reasons, phase)
           values (%s, %s, %s, %s, %s, %s, %s, 'grant')""",
        (principal, kind, roles, tenant_id, f"legal export {reference}: {what}", allowed, reasons or []),
    )


def _check(decision: tuple[bool, list[str]]) -> None:
    allowed, reasons = decision
    if not allowed:
        _refuse(reasons)


def _hold(hold_id: str) -> dict:
    try:
        uuid.UUID(hold_id)
    except ValueError:
        _refuse(["that is not a hold id"], status=404)
    row = db.one("select id, tenant_id, status, matter_number, matter_name, custodian_id from legal_hold where id = %s",
                 (hold_id,))
    if not row:
        _refuse(["there is no such hold"], status=404)
    return row


def _export(export_id: str) -> dict:
    try:
        uuid.UUID(export_id)
    except ValueError:
        _refuse(["that is not an export id"], status=404)
    row = db.one(f"select {_PUBLIC} {_FROM} where e.id = %s", (export_id,))
    if not row:
        _refuse(["there is no such export"], status=404)
    return row


def _visible(session: dict, export: dict) -> None:
    if not _is_admin(session) and export["custodian_id"] != session["id"]:
        _refuse(["an export is visible to platform administrators and to the custodian of its hold"])


# --------------------------------------------------------------- filtering --

SCALAR_TYPES = ("string", "int", "float", "bool")


def _scalar_columns(dataset_id: str) -> list[str] | None:
    """The columns that identify a row by one plain value, common to every sealed version of a table dataset, or None
    when the dataset is not stored as a table in every version."""
    versions = db.all_rows(
        """select dv.id, sc.fields, (select count(*) from iceberg_table_ref r where r.dataset_version_id = dv.id) as tabled
             from dataset_version dv join schema_contract sc on sc.id = dv.schema_id
            where dv.dataset_id = %s and dv.sealed""", (dataset_id,))
    if not versions or any(v["tabled"] == 0 for v in versions):
        return None
    common: set[str] | None = None
    for v in versions:
        names = {f["name"] for f in v["fields"] if f.get("type") in SCALAR_TYPES}
        common = names if common is None else common & names
    return sorted(common or [])


def _clean_values(values: list[str]) -> list[str]:
    return sorted({str(v).strip() for v in values if str(v).strip()})


def _table_props() -> dict:
    return {"s3.endpoint": config.S3_ENDPOINT, "s3.access-key-id": config.STORAGE_ADMIN[0],
            "s3.secret-access-key": config.STORAGE_ADMIN[1], "s3.region": "us-east-1",
            "s3.path-style-access": "true"}


def _scan_filtered(metadata_location: str, column: str, values: list[str], target: Path | None) -> tuple[int, int, set[str]]:
    """Read a table in batches and keep the rows whose `column`, as text, is one of `values`. Writes them to `target` as
    CSV when one is given, with nested columns written as JSON. Returns (rows matched, rows in the table, the values
    that matched at least one row)."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.csv as pacsv
    from pyiceberg.table import StaticTable

    table = StaticTable.from_metadata(metadata_location, properties=_table_props())
    wanted = pa.array(values, type=pa.string())
    reader = table.scan().to_arrow_batch_reader()

    def flat(batch: "pa.RecordBatch") -> "pa.RecordBatch":
        arrays, fields = [], []
        for field, array in zip(batch.schema, batch.columns):
            if pa.types.is_nested(field.type):
                array = pa.array([json.dumps(x, default=str) if x is not None else None for x in array.to_pylist()],
                                 type=pa.string())
                field = pa.field(field.name, pa.string())
            arrays.append(array)
            fields.append(field)
        return pa.RecordBatch.from_arrays(arrays, schema=pa.schema(fields))

    writer = None
    matched = total = 0
    seen: set[str] = set()
    try:
        for batch in reader:
            total += batch.num_rows
            keep = pc.is_in(pc.cast(batch.column(column), pa.string()), value_set=wanted)
            hit = batch.filter(keep)
            if target is not None and writer is None:
                writer = pacsv.CSVWriter(str(target), flat(batch.slice(0, 0)).schema)
            if hit.num_rows:
                matched += hit.num_rows
                seen.update(pc.unique(pc.cast(hit.column(column), pa.string())).to_pylist())
                if writer is not None:
                    writer.write_batch(flat(hit))
    finally:
        if writer is not None:
            writer.close()
    if target is not None and writer is None:
        # An empty table still gets its header row, so the file says which columns the filter ran over.
        names = [f.name for f in reader.schema]
        target.write_text(",".join(f'"{n}"' for n in names) + "\n", encoding="utf-8")
    return matched, total, seen


def _tables_of(dataset_id: str) -> list[dict]:
    return db.all_rows(
        """select dv.id as version_id, dv.version, r.table_name, r.metadata_location, r.record_count, r.records_sha256
             from dataset_version dv join iceberg_table_ref r on r.dataset_version_id = dv.id
            where dv.dataset_id = %s and dv.sealed order by dv.version""", (dataset_id,))


# ------------------------------------------------------------------ scope --


@router.get("/scope")
def scope(hold_id: str = Query(...), session: dict = Depends(current_session_while_closing)) -> dict:
    """The datasets of a held organisation that an export could name, for choosing a scope. Names and sizes, never
    contents, and only for an organisation with a legal hold in force."""
    hold = _hold(hold_id)
    if not _is_admin(session) and hold["custodian_id"] != session["id"]:
        _refuse(["the datasets of a held organisation are listed to platform administrators and to the hold's custodian"])
    if hold["status"] != "active":
        _refuse(["records are produced only while a legal hold is in force"], status=409)
    rows = db.all_rows(
        """select d.id, d.name, dv.version, dv.record_count, dv.object_manifest
             from dataset d join dataset_version dv on dv.dataset_id = d.id and dv.sealed
            where d.tenant_id = %s order by d.name, dv.version""", (hold["tenant_id"],))
    datasets: dict[str, dict] = {}
    for r in rows:
        entry = datasets.setdefault(str(r["id"]), {"id": str(r["id"]), "name": r["name"], "versions": 0, "bytes": 0,
                                                    "columns": _scalar_columns(str(r["id"]))})
        entry["tabular"] = entry["columns"] is not None
        entry["versions"] += 1
        entry["bytes"] += sum(int(f.get("bytes", 0)) for f in (r["object_manifest"] or []))
    return {"hold_id": hold_id, "tenant_id": hold["tenant_id"], "datasets": list(datasets.values())}


@router.get("/signing-key")
def signing_key() -> dict:
    """The public half of the key that signs every package's manifest, so a recipient can verify it."""
    return {"algorithm": "ed25519", "public_key": package_crypto.public_key_hex()}


# ---------------------------------------------------------------- request --


class FilterIn(BaseModel):
    dataset_id: str
    column: str


class ExportIn(BaseModel):
    hold_id: str
    demand_authority: str
    demand_reference: str
    demanded_on: date
    demand_text: str
    dataset_ids: list[str]
    include_audit: bool = True
    data_from: date | None = None
    data_to: date | None = None
    recipient_name: str
    recipient_organisation: str
    recipient_email: str
    filters: list[FilterIn] = []


class DecideIn(BaseModel):
    approve: bool
    note: str = ""
    # For a confirmation only: for each filtered dataset, the values the custodian names. Never shown to anybody else.
    values: dict[str, list[str]] = {}


class PreviewIn(BaseModel):
    values: dict[str, list[str]]


@router.post("", status_code=201)
def request_export(body: ExportIn, session: dict = Depends(current_session)) -> dict:
    hold = _hold(body.hold_id)
    payload = body.model_dump(mode="json")
    _check(opa.may_request_export({"actor": _actor(session), "hold": {"status": hold["status"]}, "export": payload}))
    known = {str(r["id"]) for r in db.all_rows("select id from dataset where tenant_id = %s", (hold["tenant_id"],))}
    unknown = [d for d in body.dataset_ids if d not in known]
    if unknown:
        _refuse([f"{unknown[0]} is not a dataset of {hold['tenant_id']}"], status=422)
    seen_filters: set[str] = set()
    for f in body.filters:
        if f.dataset_id not in body.dataset_ids:
            _refuse([f"{f.dataset_id} is filtered but is not one of the datasets named"], status=422)
        if f.dataset_id in seen_filters:
            _refuse([f"{f.dataset_id} is filtered twice. A dataset is filtered by one column"], status=422)
        seen_filters.add(f.dataset_id)
        columns = _scalar_columns(f.dataset_id)
        if columns is None:
            _refuse([f"{f.dataset_id} is not stored as a table in every version, so it cannot be filtered. "
                     "Name it whole, or leave it out"], status=422)
        if f.column not in columns:
            _refuse([f"{f.column!r} is not a plain column in every version of {f.dataset_id}. "
                     f"The columns that can identify a row are: {', '.join(columns) or 'none'}"], status=422)
    export_id = str(uuid.uuid4())
    db.execute(
        """insert into legal_export (id, tenant_id, hold_id, status, demand_authority, demand_reference, demanded_on,
                  demand_text, dataset_ids, include_audit, data_from, data_to, recipient_name, recipient_organisation,
                  recipient_email, requested_by, filters)
           values (%s, %s, %s, 'requested', %s, %s, %s, %s, %s::uuid[], %s, %s, %s, %s, %s, %s, %s, %s::jsonb) returning id""",
        (export_id, hold["tenant_id"], body.hold_id, body.demand_authority.strip(), body.demand_reference.strip(),
         body.demanded_on, body.demand_text.strip(), body.dataset_ids, body.include_audit, body.data_from, body.data_to,
         body.recipient_name.strip(), body.recipient_organisation.strip(), body.recipient_email.strip(), session["id"],
         json.dumps([f.model_dump() for f in body.filters])))
    _event(hold["tenant_id"], session["id"], "export_requested", body.hold_id,
           {"export_id": export_id, "demand_reference": body.demand_reference, "datasets": len(body.dataset_ids),
            "filtered": len(body.filters)})
    _audit(hold["tenant_id"], session["id"], "human", session["roles"], body.demand_reference, "requested", True)
    return _clean(_export(export_id))


@router.get("")
def list_exports(hold_id: str | None = Query(default=None),
                 session: dict = Depends(current_session_while_closing)) -> dict:
    if _is_admin(session):
        rows = db.all_rows(f"select {_PUBLIC} {_FROM} " + ("where e.hold_id = %s " if hold_id else "") +
                           "order by e.requested_at desc", (hold_id,) if hold_id else ())
    else:
        rows = db.all_rows(f"select {_PUBLIC} {_FROM} where h.custodian_id = %s order by e.requested_at desc",
                           (session["id"],))
    return {"exports": _clean(rows)}


@router.get("/{export_id}/manifest")
def manifest(export_id: str, session: dict = Depends(current_session_while_closing)) -> dict:
    export = _export(export_id)
    _visible(session, export)
    files = db.all_rows("select path, dataset, version, bytes, sha256, source_key from legal_export_file "
                        "where export_id = %s order by path", (export_id,))
    return {"export_id": export_id, "manifest_sha256": export["manifest_sha256"], "signature": export["signature"],
            "files": _clean(files)}


# -------------------------------------------------------- the three steps --


def _decide(export: dict, hold: dict, session: dict, body: DecideIn, *, step: str) -> dict:
    """Approval (a different platform administrator) or confirmation (the hold's custodian), or a refusal of either."""
    filter_datasets = [f["dataset_id"] for f in (export["filters"] or [])]
    cleaned = {d: _clean_values(body.values.get(d, [])) for d in filter_datasets}
    # A decline needs no values: the custodian is saying the scope is wrong, not supplying it.
    valued = [d for d in filter_datasets if cleaned[d] or not body.approve]
    payload = {"actor": _actor(session),
               "hold": {"custodian_id": hold["custodian_id"], "status": hold["status"]},
               "export": {"requested_by": export["requested_by"], "status": export["status"],
                          "filter_datasets": filter_datasets},
               "confirm": {"valued_datasets": valued}}
    decision = opa.may_approve_export(payload) if step == "approve" else opa.may_confirm_export(payload)
    if not decision[0]:
        _audit(export["tenant_id"], session["id"], "human", session["roles"], export["demand_reference"],
               f"{step} refused", False, decision[1])
        _refuse(decision[1])
    if not body.approve and not body.note.strip():
        _refuse([f"declining an export needs a note saying why"], status=422)
    if hold["status"] != "active":
        _refuse(["the legal hold is no longer in force, so nothing is produced"], status=409)
    expected = "requested" if step == "approve" else "approved"
    if body.approve:
        if step == "approve":
            done = db.execute("update legal_export set status = 'approved', approved_by = %s, approved_at = now(), "
                              "approval_note = %s where id = %s and status = 'requested' returning id",
                              (session["id"], body.note.strip() or None, export["id"]))
        else:
            done = db.execute("update legal_export set status = 'confirmed', confirmed_by = %s, confirmed_at = now(), "
                              "confirm_note = %s, filter_values = %s::jsonb where id = %s and status = 'approved' returning id",
                              (session["id"], body.note.strip() or None, json.dumps(cleaned) if filter_datasets else None,
                               export["id"]))
    else:
        done = db.execute("update legal_export set status = 'refused', refused_by = %s, refused_at = now(), "
                          "refusal_reason = %s where id = %s and status = %s returning id",
                          (session["id"], f"{step}: {body.note.strip()}", export["id"], expected))
    if not done:
        _refuse(["this export was decided while this was being sent"], status=409)
    word = {"approve": ("export_approved", "export_approval_declined"),
            "confirm": ("export_scope_confirmed", "export_scope_declined")}[step]
    _event(export["tenant_id"], session["id"], word[0] if body.approve else word[1], str(export["hold_id"]),
           {"export_id": str(export["id"]), "note": body.note.strip()})
    _audit(export["tenant_id"], session["id"], "human", session["roles"], export["demand_reference"],
           (step + "d") if body.approve else (step + " declined"), True)
    return _clean(_export(str(export["id"])))


@router.post("/{export_id}/approve")
def approve(export_id: str, body: DecideIn, session: dict = Depends(current_session)) -> dict:
    export = _export(export_id)
    hold = _hold(str(export["hold_id"]))
    return _decide(export, hold, session, body, step="approve")


@router.post("/{export_id}/confirm")
def confirm(export_id: str, body: DecideIn, background: BackgroundTasks,
            session: dict = Depends(current_session_while_closing)) -> dict:
    export = _export(export_id)
    hold = _hold(str(export["hold_id"]))
    result = _decide(export, hold, session, body, step="confirm")
    if result["status"] == "confirmed":
        background.add_task(produce, export_id)
    return result


@router.post("/{export_id}/filter-preview")
def filter_preview(export_id: str, body: PreviewIn, session: dict = Depends(current_session_while_closing)) -> dict:
    """For the custodian, before confirming: how many rows each filter would match, and which values matched none. Counts
    only. The rows are never returned, and the values are not stored by asking."""
    export = _export(export_id)
    hold = _hold(str(export["hold_id"]))
    if hold["custodian_id"] != session["id"]:
        _refuse(["only the custodian the hold names checks what a filter matches"])
    if export["status"] != "approved":
        _refuse(["a filter is checked while the export waits for the custodian to confirm it"], status=409)
    results = []
    for f in export["filters"] or []:
        values = _clean_values(body.values.get(f["dataset_id"], []))
        if not values:
            results.append({"dataset_id": f["dataset_id"], "column": f["column"], "values_given": 0,
                            "rows_matched": 0, "rows_total": 0, "unmatched_values": []})
            continue
        matched = total = 0
        seen: set[str] = set()
        for t in _tables_of(f["dataset_id"]):
            m, n, hit = _scan_filtered(t["metadata_location"], f["column"], values, None)
            matched, total = matched + m, total + n
            seen |= hit
        results.append({"dataset_id": f["dataset_id"], "column": f["column"], "values_given": len(values),
                        "rows_matched": matched, "rows_total": total, "unmatched_values": [v for v in values if v not in seen]})
    _audit(export["tenant_id"], session["id"], "human", session["roles"], export["demand_reference"],
           "filter checked: " + ", ".join(f"{r['rows_matched']} of {r['rows_total']} rows" for r in results), True)
    return {"results": results}


# ------------------------------------------------------------- production --


class IntegrityError(Exception):
    pass


def _bucket_client():
    client = seaweed._admin_boto_client()
    from botocore.exceptions import ClientError
    try:
        client.create_bucket(Bucket=config.LEGAL_EXPORT_BUCKET)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code", "") not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            raise
    return client


def _objects(client, bucket: str, prefix: str):
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        page = client.list_objects_v2(**kwargs)
        yield from page.get("Contents", [])
        if not page.get("IsTruncated"):
            return
        token = page.get("NextContinuationToken")


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name) or "dataset"


def produce(export_id: str) -> None:
    """Build the package for a confirmed export. Safe to call twice: only one caller can claim it."""
    claimed = db.execute(
        "update legal_export set status = 'producing', production_started_at = now() "
        "where id = %s and status = 'confirmed' returning id", (export_id,))
    if not claimed:
        return
    export = _export(export_id)
    try:
        _build(export)
    except Exception as exc:
        log.exception("legal export failed", extra={"tenant_id": export["tenant_id"]})
        reason = str(exc)[:500]
        db.execute("update legal_export set status = 'failed', failure = %s where id = %s", (reason, export_id))
        _event(export["tenant_id"], "the platform", "export_failed", str(export["hold_id"]),
               {"export_id": export_id, "reason": reason})
        _audit(export["tenant_id"], "the platform", "workload", [], export["demand_reference"], "production failed",
               False, [reason])


def _build(export: dict) -> None:
    tenant = export["tenant_id"]
    export_id = str(export["id"])
    backends = {r["backend"] for r in db.all_rows(
        "select backend from tenant_storage_provision where tenant_id = %s", (tenant,))}
    if backends and "seaweedfs" not in backends:
        raise RuntimeError("this organisation's files are on Cloudflare R2, which an export does not read yet")
    client = seaweed._admin_boto_client()
    bucket = seaweed.bucket(tenant)

    work = Path(tempfile.mkdtemp(prefix="legal-export-"))
    try:
        zip_path = work / "package.zip"
        files: list[dict] = []
        gaps: list[dict] = []
        total = 0
        version_ids: list[str] = []
        filter_columns = {f["dataset_id"]: f["column"] for f in (export["filters"] or [])}
        filter_values = (db.one("select filter_values from legal_export where id = %s", (export_id,)) or {}).get("filter_values") or {}
        filter_results: dict[str, dict] = {}
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for dataset_id in [str(d) for d in export["dataset_ids"]]:
                dataset = db.one("select id, name from dataset where id = %s and tenant_id = %s", (dataset_id, tenant))
                if not dataset:
                    raise RuntimeError(f"dataset {dataset_id} no longer exists")
                if dataset_id in filter_columns:
                    column, values = filter_columns[dataset_id], filter_values.get(dataset_id) or []
                    if not values:
                        raise RuntimeError(f"no values were named for {dataset['name']}, which is filtered by {column}")
                    seen_all: set[str] = set()
                    matched_all = total_all = 0
                    for t in _tables_of(dataset_id):
                        version_ids.append(str(t["version_id"]))
                        part = work / f"filtered-{len(files)}.csv"
                        matched, rows_total, seen = _scan_filtered(t["metadata_location"], column, values, part)
                        if rows_total != t["record_count"]:
                            raise IntegrityError(f"{dataset['name']} v{t['version']}: the table holds {rows_total} rows and the "
                                                 f"register says {t['record_count']}")
                        data = part.read_bytes()
                        path = f"data/{_safe(dataset['name'])}/v{t['version']}/{t['table_name']}.filtered.csv"
                        zf.writestr(path, data)
                        files.append({"path": path, "dataset_id": dataset_id, "dataset": dataset["name"],
                                      "version": t["version"], "version_id": str(t["version_id"]), "bytes": len(data),
                                      "sha256": hashlib.sha256(data).hexdigest(),
                                      "source_key": f"table {t['table_name']}, filtered; the sealed original is not included",
                                      "filtered_by": {"column": column, "values": len(values), "rows_matched": matched,
                                                      "rows_total": rows_total, "source_records_sha256": t["records_sha256"]}})
                        matched_all, total_all, seen_all = matched_all + matched, total_all + rows_total, seen_all | seen
                    if not total_all and not _tables_of(dataset_id):
                        raise RuntimeError(f"{dataset['name']} is not stored as a table, so it cannot be filtered")
                    filter_results[dataset_id] = {"column": column, "values_given": len(values), "rows_matched": matched_all,
                                                  "rows_total": total_all,
                                                  "values_unmatched": len([v for v in values if v not in seen_all])}
                    continue
                for version in db.all_rows(
                        """select id, version, storage_prefix, object_manifest from dataset_version
                            where dataset_id = %s and sealed order by version""", (dataset_id,)):
                    version_ids.append(str(version["id"]))
                    recorded = {}
                    for f in version["object_manifest"] or []:
                        if f.get("sha256"):
                            recorded[str(f["key"])] = f["sha256"]
                    prefix = version["storage_prefix"].rstrip("/") + "/"
                    found = 0
                    for obj in _objects(client, bucket, prefix):
                        key = obj["Key"]
                        total += int(obj["Size"])
                        if total > config.LEGAL_EXPORT_MAX_BYTES:
                            raise RuntimeError(f"the files named are larger than the {config.LEGAL_EXPORT_MAX_BYTES} byte limit")
                        relative = key[len(prefix):]
                        path = f"data/{_safe(dataset['name'])}/v{version['version']}/{relative}"
                        digest = hashlib.sha256()
                        body = client.get_object(Bucket=bucket, Key=key)["Body"]
                        with zf.open(path, "w", force_zip64=True) as out:
                            for chunk in body.iter_chunks(1024 * 1024):
                                digest.update(chunk)
                                out.write(chunk)
                        expected = recorded.get(key) or recorded.get(relative)
                        if expected and expected != digest.hexdigest():
                            raise IntegrityError(f"{key}: the stored bytes differ from the hash recorded when version "
                                                 f"{version['version']} of {dataset['name']} was sealed")
                        files.append({"path": path, "dataset_id": dataset_id, "dataset": dataset["name"],
                                      "version": version["version"], "version_id": str(version["id"]),
                                      "bytes": int(obj["Size"]), "sha256": digest.hexdigest(), "source_key": key})
                        found += 1
                    if not found:
                        freed = db.one("select at, reason from storage_reclamation where dataset_version_id = %s",
                                       (version["id"],))
                        gaps.append({"kind": "version has no files", "dataset": dataset["name"],
                                     "version": version["version"],
                                     "why": f"its files were freed on {freed['at']:%Y-%m-%d}: {freed['reason']}"
                                            if freed else "no files are stored for it"})

            produced_at = _now()
            audit_rows = 0
            if export["include_audit"] and version_ids:
                rows = db.all_rows(
                    """select at, principal, principal_kind, principal_roles, dataset_version_id, purpose, allowed, reasons
                         from access_decision
                        where tenant_id = %s and dataset_version_id = any(%s::uuid[])
                          and (%s::date is null or at::date >= %s::date) and (%s::date is null or at::date <= %s::date)
                        order by at""",
                    (tenant, version_ids, export["data_from"], export["data_from"], export["data_to"], export["data_to"]))
                buffer = io.StringIO()
                writer = csv.writer(buffer)
                writer.writerow(["at", "principal", "kind", "roles", "dataset_version_id", "purpose", "allowed", "reasons"])
                for r in rows:
                    writer.writerow([r["at"].isoformat(), r["principal"], r["principal_kind"], ";".join(r["principal_roles"]),
                                     r["dataset_version_id"] or "", r["purpose"] or "", r["allowed"], "; ".join(r["reasons"])])
                zf.writestr("audit-trail.csv", buffer.getvalue())
                audit_rows = len(rows)

            erased = [{"record_id": r["record_id"], "erased_at": f"{r['at']:%Y-%m-%d}"} for r in db.all_rows(
                "select record_id, at from tombstone where tenant_id = %s order by at", (tenant,))]
            zf.writestr("erased.json", json.dumps({"records_erased_by_key_destruction": erased,
                                                   "versions_without_files": gaps}, indent=2))

            people = {r["id"]: r["label"] for r in db.all_rows(
                "select id, label from directory where id = any(%s)",
                ([export["requested_by"], export["approved_by"], export["confirmed_by"], export["custodian_id"]],))}
            hold = db.one("select matter_number, matter_name, issuing_authority, authority_reference, placed_at, "
                          "decided_at, custodian_acknowledged_at from legal_hold where id = %s", (export["hold_id"],))
            custody = {
                "hold": {"matter_number": hold["matter_number"], "matter_name": hold["matter_name"],
                         "issued_by": hold["issuing_authority"], "reference": hold["authority_reference"],
                         "placed_at": hold["placed_at"], "approved_at": hold["decided_at"],
                         "custodian": people.get(export["custodian_id"]),
                         "custodian_acknowledged_at": hold["custodian_acknowledged_at"]},
                "demand": {"authority": export["demand_authority"], "reference": export["demand_reference"],
                           "demanded_on": export["demanded_on"], "asks_for": export["demand_text"]},
                "recipient": {"name": export["recipient_name"], "organisation": export["recipient_organisation"]},
                "filters": [{"dataset": next(f["dataset"] for f in files if f["dataset_id"] == d), "column": c,
                             "values_named_by_the_custodian": filter_values.get(d, []), **filter_results.get(d, {})}
                            for d, c in filter_columns.items() if any(f["dataset_id"] == d for f in files)],
                "steps": [
                    {"step": "asked for", "by": people.get(export["requested_by"]), "at": export["requested_at"]},
                    {"step": "approved", "by": people.get(export["approved_by"]), "at": export["approved_at"],
                     "note": export["approval_note"]},
                    {"step": "scope confirmed", "by": people.get(export["confirmed_by"]), "at": export["confirmed_at"],
                     "note": export["confirm_note"]},
                    {"step": "produced", "by": "the platform", "at": produced_at},
                ],
            }
            zf.writestr("chain-of-custody.json", json.dumps(custody, indent=2, default=str))

            manifest_doc = {
                "format": "munitas-legal-export-1", "export_id": export_id, "matter_number": hold["matter_number"],
                "demand_reference": export["demand_reference"], "produced_at": produced_at.isoformat(),
                "public_key": package_crypto.public_key_hex(), "algorithm": "ed25519 over the SHA-256 of manifest.json",
                "files": files, "audit_trail_rows": audit_rows, "records_erased": len(erased),
                "versions_without_files": len(gaps),
            }
            manifest_bytes = json.dumps(manifest_doc, indent=2, sort_keys=True).encode("utf-8")
            signature = package_crypto.sign(manifest_bytes)
            zf.writestr("manifest.json", manifest_bytes)
            zf.writestr("manifest.sig", signature)

        passphrase = "-".join(secrets.token_hex(3) for _ in range(5))
        package_path = work / "package.mlep"
        size, package_sha = package_crypto.encrypt_file(zip_path, package_path, passphrase)
        key = f"{export_id}.mlep"
        store = _bucket_client()
        with package_path.open("rb") as handle:
            store.put_object(Bucket=config.LEGAL_EXPORT_BUCKET, Key=key, Body=handle)
        sealed = _crypto.seal(_PASSPHRASE_SCOPE, export_id, passphrase.encode())
        manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    finally:
        shutil.rmtree(work, ignore_errors=True)

    for f in files:
        db.execute(
            """insert into legal_export_file (export_id, tenant_id, path, dataset_id, dataset, version, version_id,
                      bytes, sha256, source_key) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (export_id, tenant, f["path"], f["dataset_id"], f["dataset"], f["version"], f["version_id"],
             f["bytes"], f["sha256"], f["source_key"]))
    db.execute(
        """update legal_export set status = 'ready', produced_at = %s, package_key = %s, package_bytes = %s,
                  package_sha256 = %s, manifest_sha256 = %s, signature = %s, file_count = %s,
                  passphrase_ciphertext = %s, passphrase_wrapped_key = %s, filter_results = %s::jsonb,
                  expires_at = now() + make_interval(days => %s)
            where id = %s and status = 'producing' returning id""",
        (produced_at, key, size, package_sha, manifest_sha, signature, len(files), sealed.ciphertext,
         sealed.wrapped_key, json.dumps(filter_results) if filter_results else None, config.LEGAL_EXPORT_KEEP_DAYS, export_id))
    _event(tenant, "the platform", "export_produced", str(export["hold_id"]),
           {"export_id": export_id, "files": len(files), "bytes": size, "manifest_sha256": manifest_sha})
    _audit(tenant, "the platform", "workload", [], export["demand_reference"], f"produced, {len(files)} files", True)
    log.info("legal export produced", extra={"tenant_id": tenant, "count": len(files)})


# --------------------------------------------- the passphrase and the link --


@router.post("/{export_id}/passphrase")
def passphrase(export_id: str, session: dict = Depends(current_session_while_closing)) -> dict:
    """The package's passphrase, to the hold's custodian, once. It is not kept after it is read."""
    export = _export(export_id)
    hold = _hold(str(export["hold_id"]))
    allowed, reasons = opa.may_read_passphrase({
        "actor": _actor(session), "hold": {"custodian_id": hold["custodian_id"]},
        "export": {"status": export["status"], "passphrase_revealed": export["passphrase_revealed_at"] is not None}})
    if not allowed:
        _audit(export["tenant_id"], session["id"], "human", session["roles"], export["demand_reference"],
               "passphrase refused", False, reasons)
        _refuse(reasons)
    held = db.one("select passphrase_ciphertext as c, passphrase_wrapped_key as w from legal_export "
                  "where id = %s and passphrase_revealed_at is null", (export_id,))
    if not held or held["c"] is None:
        _refuse(["the passphrase has been read already, and is not kept after that"], status=409)
    # Taken in one statement, so two people asking at the same moment cannot both be given it.
    taken = db.execute(
        """update legal_export set passphrase_revealed_at = now(), passphrase_revealed_by = %s,
                  passphrase_ciphertext = null, passphrase_wrapped_key = null
            where id = %s and status = 'ready' and passphrase_revealed_at is null returning id""",
        (session["id"], export_id))
    if not taken:
        _refuse(["the passphrase has been read already, and is not kept after that"], status=409)
    text = _crypto.open(_PASSPHRASE_SCOPE, export_id, bytes(held["c"]), bytes(held["w"])).decode()
    _event(export["tenant_id"], session["id"], "export_passphrase_read", str(export["hold_id"]), {"export_id": export_id})
    _audit(export["tenant_id"], session["id"], "human", session["roles"], export["demand_reference"], "passphrase read", True)
    return {"export_id": export_id, "passphrase": text,
            "note": "Shown once. Pass it to the recipient separately from the download link."}


@router.post("/{export_id}/links", status_code=201)
def make_link(export_id: str, session: dict = Depends(current_session)) -> dict:
    export = _export(export_id)
    _check(opa.may_link_export({"actor": _actor(session), "export": {"status": export["status"]}}))
    token = "mlx_" + secrets.token_urlsafe(32)
    link_id = str(uuid.uuid4())
    expires = _now() + timedelta(days=config.LEGAL_EXPORT_LINK_DAYS)
    if export["expires_at"] and export["expires_at"] < expires:
        expires = export["expires_at"]
    db.execute(
        """insert into legal_export_link (id, export_id, tenant_id, token_hash, created_by, expires_at, max_uses)
           values (%s, %s, %s, %s, %s, %s, %s) returning id""",
        (link_id, export_id, export["tenant_id"], hashlib.sha256(token.encode()).hexdigest(), session["id"], expires,
         config.LEGAL_EXPORT_LINK_USES))
    _event(export["tenant_id"], session["id"], "export_link_made", str(export["hold_id"]),
           {"export_id": export_id, "link_id": link_id, "expires_at": expires, "uses": config.LEGAL_EXPORT_LINK_USES})
    _audit(export["tenant_id"], session["id"], "human", session["roles"], export["demand_reference"], "download link made", True)
    return {"link_id": link_id, "token": token, "download_path": f"/legal-exports/download/{token}",
            "expires_at": expires.isoformat(), "uses": config.LEGAL_EXPORT_LINK_USES,
            "note": "Shown once. The package is encrypted: the recipient also needs its passphrase."}


@router.get("/download/{token}")
def download(token: str):
    """Hand over the encrypted package to whoever holds a link, a limited number of times and for a limited time.

    The token is the credential, so this needs no session. It opens nothing by itself: the package is encrypted."""
    refusal = HTTPException(404, {"reasons": ["this link does not exist, has expired, or has been used up"]})
    link = db.one("select id, export_id, tenant_id from legal_export_link where token_hash = %s",
                  (hashlib.sha256(token.encode()).hexdigest(),))
    if not link:
        raise refusal
    export = _export(str(link["export_id"]))
    taken = db.execute(
        """update legal_export_link set uses = uses + 1
            where id = %s and uses < max_uses and expires_at > now() and revoked_at is null returning id""",
        (link["id"],))
    if not taken or export["status"] != "ready":
        _audit(export["tenant_id"], f"link {str(link['id'])[:8]}", "workload", [], export["demand_reference"],
               "download refused", False, ["the link has expired, been used up or been revoked"])
        raise refusal
    _event(export["tenant_id"], f"link {str(link['id'])[:8]}", "export_downloaded", str(export["hold_id"]),
           {"export_id": str(export["id"]), "link_id": str(link["id"])})
    _audit(export["tenant_id"], f"link {str(link['id'])[:8]}", "workload", [], export["demand_reference"], "package downloaded", True)
    package = db.one("select package_key, package_bytes, package_sha256 from legal_export where id = %s", (export["id"],))
    try:
        body = seaweed._admin_boto_client().get_object(Bucket=config.LEGAL_EXPORT_BUCKET, Key=package["package_key"])["Body"]
    except Exception as exc:
        raise HTTPException(410, {"reasons": ["the package is no longer stored"]}) from exc
    return StreamingResponse(body.iter_chunks(1024 * 1024), media_type="application/octet-stream", headers={
        "content-disposition": f'attachment; filename="{export["demand_reference"]}.mlep"',
        "content-length": str(package["package_bytes"]), "x-package-sha256": package["package_sha256"]})


# ------------------------------------------------------------------ sweep --


def delete_package(key: str) -> None:
    try:
        seaweed._admin_boto_client().delete_object(Bucket=config.LEGAL_EXPORT_BUCKET, Key=key)
    except Exception:
        log.warning("a package could not be deleted from storage", extra={"reason": key[:60]})


def sweep_exports() -> dict:
    """What the sweep does about exports: build the confirmed ones, fail the stalled ones, delete packages whose
    retention has ended, and carry out erasures that a hold had held back."""
    stalled = db.all_rows(
        "update legal_export set status = 'failed', failure = 'production did not finish, and may have been stopped by a restart' "
        "where status = 'producing' and production_started_at < now() - make_interval(mins => %s) returning id, tenant_id",
        (STALLED_AFTER_MINUTES,))
    for r in stalled:
        _event(r["tenant_id"], "the platform", "export_failed", None, {"export_id": str(r["id"]), "reason": "stalled"})
    produced = []
    for r in db.all_rows("select id from legal_export where status = 'confirmed' order by confirmed_at"):
        produce(str(r["id"]))
        produced.append(str(r["id"]))
    expired = []
    for r in db.all_rows("select id, tenant_id, hold_id, package_key from legal_export "
                         "where status = 'ready' and expires_at <= now()"):
        delete_package(r["package_key"])
        db.execute("update legal_export set status = 'expired', expired_at = now() where id = %s and status = 'ready' returning id",
                   (r["id"],))
        db.execute("update legal_export_link set revoked_at = now() where export_id = %s and revoked_at is null returning id",
                   (r["id"],))
        _event(r["tenant_id"], "the platform", "export_expired", str(r["hold_id"]), {"export_id": str(r["id"])})
        expired.append(str(r["id"]))
    return {"produced": produced, "expired": expired, "stalled": [str(r["id"]) for r in stalled],
            "erasures_honoured": honour_deferred_erasures()}


def honour_deferred_erasures() -> list[str]:
    """Carry out an erasure that a legal hold held back, once no hold stands over the organisation."""
    import psycopg
    done = []
    for r in db.all_rows("select record_id, tenant_id, reason, requested_by from deferred_erasure "
                         "where honoured_at is null and tenant_hold_state(tenant_id) = 'none'"):
        try:
            db.execute("update record_key set wrapped_key = null, destroyed_at = now() "
                       "where record_id = %s and destroyed_at is null returning record_id", (r["record_id"],))
            db.execute("insert into tombstone (record_id, tenant_id, reason, requested_by) values (%s, %s, %s, %s) "
                       "on conflict (record_id) do nothing returning record_id",
                       (r["record_id"], r["tenant_id"], r["reason"], r["requested_by"]))
            db.execute("update deferred_erasure set honoured_at = now() where record_id = %s returning record_id", (r["record_id"],))
            done.append(r["record_id"])
        except psycopg.errors.ReadOnlySqlTransaction:
            # The organisation is closed to writes. It is being deleted, which erases the record anyway.
            continue
    return done
