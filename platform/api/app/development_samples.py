"""Development samples: the small set of recordings a pipeline version in development is allowed to run on.

A pipeline version with a custom step is in development until a second person moves it to production, and in development it may run only on
development data: recordings declared synthetic, or a sample of real ones that a department approver approved for the person who asked.

A person asks for a sample (a kind, a purpose, how many recordings, for how many days). An approver of the department that owns the recordings, who
is not that person, decides. The platform then draws the recordings and seals a new version that names the same stored files, so no audio is
copied and nothing is stored in the clear. Erasing a person makes their recording unreadable in the sample too, because it is the same file under
the same key. A debug sample names the recordings that went wrong instead of drawing them.

The draw is the platform's. It is reproducible from the salt kept with the sample, so anyone can see that it was not hand-picked:
  1. leave out recordings of erased people;
  2. one recording for each person, the one whose hash ranks first;
  3. three length bands, each a third of what is left;
  4. the number asked for divided across the bands in proportion, the remainder going to the shorter bands first;
  5. in each band, the people whose hash ranks first.
The hash is SHA-256 of the salt, the person's code and the recording's identifier, so the same person asking for the same purpose of the same version
gets the same sample, and asking again does not collect a different one.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timezone

import psycopg

from . import db, opa, recordings, storage, versions
from .locked_leases import approvers_of
from .person_errors import LeaseConflict, NoSuchSample, NoSuchVersion, NotPermitted, Refused

METHOD = "sample:v1"
BANDS = 3
BAND_NAMES = {1: ["all"], 2: ["short", "long"], 3: ["short", "medium", "long"]}
COLUMNS = ("id", "source_version_id", "sample_version_id", "principal", "kind", "purpose", "requested_count", "granted_count", "days", "granted_days",
           "named_records", "status", "decided_by", "decided_at", "decision_note", "expires_at", "ended_by", "ended_at", "method", "strata", "requested_at")


# The draw ----------------------------------------------------------------------------------------


def rank(salt: str, person: str, record: str) -> str:
    return hashlib.sha256(f"{salt}|{person}|{record}".encode("utf-8")).hexdigest()


def salt_for(version_id: str, principal: str, kind: str) -> str:
    return f"{version_id}|{principal}|{kind}|{METHOD}"


def draw(candidates: list[dict], count: int, salt: str, *, bands: int = BANDS, per_person: int = 1) -> dict:
    """Choose `count` recordings from `candidates` (each `record_id`, `person_code`, `bytes`), by the method in the module note.

    A pure function of its arguments, so that it can be tested alone and reproduced from the salt. The caller has already left out erased people.
    Returns the chosen identifiers, how many came from each band, and how many recordings and people there were to choose from.
    """
    by_person: dict[str, list[dict]] = {}
    for c in candidates:
        by_person.setdefault(c["person_code"], []).append(c)
    pool: list[dict] = []
    for person, items in by_person.items():
        items.sort(key=lambda c: rank(salt, person, c["record_id"]))
        pool.extend(items[:per_person])
    n = len(pool)
    if n == 0:
        return {"chosen": [], "strata": [], "pool": 0, "people": len(by_person)}
    bands = max(1, min(bands, n))
    pool.sort(key=lambda c: (int(c["bytes"]), c["record_id"]))
    sizes = [n // bands + (1 if i < n % bands else 0) for i in range(bands)]
    groups, start = [], 0
    for size in sizes:
        groups.append(pool[start:start + size])
        start += size
    take = min(count, n)
    exact = [take * len(g) / n for g in groups]
    alloc = [int(x) for x in exact]
    for i in sorted(range(bands), key=lambda i: (-(exact[i] - alloc[i]), i))[:take - sum(alloc)]:
        alloc[i] += 1
    names = BAND_NAMES.get(bands, [f"band {i + 1}" for i in range(bands)])
    chosen: list[str] = []
    strata: list[dict] = []
    for group, k, name in zip(groups, alloc, names):
        group.sort(key=lambda c: rank(salt, c["person_code"], c["record_id"]))
        picked = group[:k]
        chosen += [c["record_id"] for c in picked]
        strata.append({"band": name, "pool": len(group), "chosen": len(picked)})
    return {"chosen": sorted(chosen), "strata": strata, "pool": n, "people": len(by_person)}


# Reading the rows ----------------------------------------------------------------------------------


def _stamp(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def _shape(row: dict) -> dict:
    out = {k: row[k] for k in COLUMNS}
    for k in ("id", "source_version_id", "sample_version_id"):
        out[k] = str(out[k]) if out[k] else None
    for k in ("decided_at", "expires_at", "ended_at", "requested_at"):
        out[k] = _stamp(out[k])
    return out


def _labels(rows: list[dict]) -> list[dict]:
    ids = {v for r in rows for v in (r["principal"], r["decided_by"], r["ended_by"]) if v}
    names = {r["id"]: r["label"] for r in db.all_rows("select id, label from directory where id = any(%s)", (list(ids),))} if ids else {}
    return [{**r, "principal_label": names.get(r["principal"], r["principal"]),
             "decided_by_label": names.get(r["decided_by"]) if r["decided_by"] else None,
             "ended_by_label": names.get(r["ended_by"]) if r["ended_by"] else None} for r in rows]


def sweep() -> int:
    """Mark the samples that have run out as ended, so that the person they were drawn for can ask for another."""
    rows = db.all_rows("update development_sample set status = 'ended', ended_at = expires_at where status = 'approved' and expires_at <= now() returning id")
    return len(rows)


def _version(version_id: str, tenant_id: str) -> dict:
    row = db.one("select v.id, v.tenant_id, v.dataset_id, v.storage_backend, v.object_manifest, d.department_id, d.name as dataset_name "
                 "from dataset_version v join dataset d on d.id = v.dataset_id where v.id = %s", (version_id,))
    if not row or row["tenant_id"] != tenant_id:
        raise NoSuchVersion(version_id)
    return row


def _source(version_id: str, tenant_id: str) -> dict:
    """A sealed version of locked recordings that is not itself a sample, since a sample drawn from a sample would collect a set by stages."""
    row = _version(version_id, tenant_id)
    if not recordings.is_locked_version(version_id):
        raise NoSuchVersion(f"{version_id} holds no locked recordings")
    if db.one("select 1 as x from development_sample where sample_version_id = %s", (version_id,)):
        raise Refused(["this version is a development sample already, and a sample is not drawn from a sample"])
    return row


def _candidates(version_id: str) -> list[dict]:
    return [{"record_id": r["record_id"], "person_code": r["person_code"], "bytes": int(r["plain_bytes"]), "erased": bool(r["erased"])} for r in db.all_rows(
        "select r.record_id, r.person_code, r.plain_bytes, p.destroyed_at is not null as erased from recording_person r "
        "left join subject_key p on p.tenant_id = r.tenant_id and p.person_code = r.person_code "
        "where r.dataset_version_id = %s order by r.record_id", (version_id,))]


# Asking, deciding, ending -------------------------------------------------------------------------


def current_sample(tenant_id: str, principal: str, version_id: str) -> dict | None:
    """This person's approved sample whose version is `version_id` and which has not ended and has not run out, or None."""
    return db.one(
        """select * from development_sample
            where tenant_id = %s and principal = %s and sample_version_id = %s and status = 'approved' and expires_at > now()""",
        (tenant_id, principal, version_id))


def synthetic_of(version_id: str) -> dict | None:
    return db.one("select * from development_data_declaration where version_id = %s", (version_id,))


def is_development_data(tenant_id: str, principal: str | None, version_id: str | None) -> bool:
    """May a pipeline version in development run on this version for this person? Only on a version declared synthetic, or on a sample drawn for them."""
    if not version_id:
        return False
    declared = synthetic_of(version_id)
    if declared and str(declared["tenant_id"]) == tenant_id:
        return True
    return bool(principal) and current_sample(tenant_id, principal, version_id) is not None


def request_sample(identity: dict, version_id: str, kind: str, purpose: str, days: int, records: int, named: list[str] | None) -> dict:
    sweep()
    _source(version_id, identity["tenant_id"])
    named = list(dict.fromkeys(n.strip() for n in (named or []) if n.strip())) if kind == "debug" else []
    allowed, reasons = opa.may_request_development_sample({
        "requester": {"id": identity["id"], "roles": identity["roles"]},
        "request": {"principal": identity["id"], "kind": kind, "purpose": purpose, "days": days, "records": records, "named": len(named)}})
    if not allowed:
        raise NotPermitted(reasons)
    candidates = _candidates(version_id)
    by_id = {c["record_id"]: c for c in candidates}
    if kind == "debug":
        problems = [f"{n} is not a recording of this version" for n in named if n not in by_id]
        problems += [f"{n} is a recording about a person who was erased" for n in named if n in by_id and by_id[n]["erased"]]
        if problems:
            raise Refused(problems)
    elif not any(not c["erased"] for c in candidates):
        raise Refused(["every recording in this version is about a person who was erased, so there is nothing to draw"])
    try:
        row = db.execute(
            """insert into development_sample (id, tenant_id, source_version_id, principal, kind, purpose, requested_count, days, named_records)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning *""",
            (str(uuid.uuid4()), identity["tenant_id"], version_id, identity["id"], kind, purpose.strip(), records, days,
             json.dumps(named) if kind == "debug" else None))
    except psycopg.errors.UniqueViolation as exc:
        raise LeaseConflict([f"you already have a sample for {'debugging' if kind == 'debug' else 'developing'} on these recordings, waiting or in force"]) from exc
    return _labels([_shape(row)])[0]


def _sample(sample_id: str, tenant_id: str) -> dict:
    row = db.one("select s.*, d.department_id from development_sample s join dataset_version v on v.id = s.source_version_id "
                 "join dataset d on d.id = v.dataset_id where s.id = %s", (sample_id,))
    if not row or row["tenant_id"] != tenant_id:
        raise NoSuchSample(sample_id)
    return row


def decide(identity: dict, sample_id: str, approve: bool, note: str | None, records: int | None = None, days: int | None = None) -> dict:
    sweep()
    sample = _sample(sample_id, identity["tenant_id"])
    allowed, reasons = opa.may_decide_development_sample({
        "approver": {"id": identity["id"], "roles": identity["roles"]}, "request": {"principal": sample["principal"]},
        "dataset": {"approvers": approvers_of(sample["department_id"])}})
    if not allowed:
        raise NotPermitted(reasons)
    if sample["status"] != "requested":
        raise LeaseConflict([f"this request was already decided ({sample['status']})"])
    if not approve:
        row = db.execute("update development_sample set status = 'declined', decided_by = %s, decided_at = now(), decision_note = %s "
                         "where id = %s and status = 'requested' returning *", (identity["id"], note, sample_id))
        if row is None:
            raise LeaseConflict(["this request was decided a moment ago by somebody else"])
        return _labels([_shape(row)])[0]

    count = records if records is not None else sample["requested_count"]
    length = days if days is not None else sample["days"]
    if not 1 <= count <= sample["requested_count"] or not 1 <= length <= sample["days"]:
        raise Refused([f"an approver may lower what was asked for, never raise it: {sample['requested_count']} recordings for {sample['days']} days was asked for"])
    version = _source(str(sample["source_version_id"]), identity["tenant_id"])
    candidates = [c for c in _candidates(str(sample["source_version_id"])) if not c["erased"]]
    salt = salt_for(str(sample["source_version_id"]), sample["principal"], sample["kind"])
    if sample["kind"] == "debug":
        present = {c["record_id"] for c in candidates}
        named = [n for n in (sample["named_records"] or []) if n in present]
        chosen = named[:count]
        strata = [{"band": "named", "pool": len(named), "chosen": len(chosen)}]
    else:
        drawn = draw(candidates, count, salt)
        chosen, strata = drawn["chosen"], drawn["strata"]
    if not chosen:
        raise Refused(["nothing is left to give: every recording asked for is about a person who was erased"])
    sample_version = _make_version(sample, version, chosen, identity)
    row = db.execute(
        """update development_sample
              set status = 'approved', decided_by = %s, decided_at = now(), decision_note = %s, granted_count = %s, granted_days = %s,
                  sample_version_id = %s, expires_at = now() + make_interval(days => %s), method = %s, salt = %s, strata = %s, chosen_records = %s
            where id = %s and status = 'requested' returning *""",
        (identity["id"], note, len(chosen), length, sample_version, length, METHOD, salt, json.dumps(strata), json.dumps(chosen), sample_id))
    if row is None:
        raise LeaseConflict(["this request was decided a moment ago by somebody else"])
    from . import development_outputs
    development_outputs.note(identity["tenant_id"], sample_version, "sample", sample_id, row["expires_at"])
    return _labels([_shape(row)])[0]


def _read_index(version: dict) -> list[dict]:
    entry = next((m for m in version["object_manifest"] if m["key"].endswith("/records.json")), None)
    if entry is None:
        raise Refused(["the recordings have no list to draw from"])
    client = storage.admin_client_for(version["storage_backend"], version["tenant_id"])
    return json.loads(client.get_object(Bucket=storage.bucket_for(version["storage_backend"], version["tenant_id"]), Key=entry["key"])["Body"].read())


def _make_version(sample: dict, source: dict, chosen: list[str], identity: dict) -> str:
    """Seal a version that names the chosen recordings and the stored files they already are. Nothing is copied and nothing is written but a small list."""
    from . import ingest
    tenant, backend = source["tenant_id"], source["storage_backend"] or "seaweedfs"
    name = f"{source['dataset_name']}-dev-sample-{str(sample['id'])[:8]}"
    dataset_id, _created = versions.ensure_dataset(tenant, name, derived_from_version_id=str(source["id"]))
    chosen_set = set(chosen)
    index = [r for r in _read_index(source) if r["record_id"] in chosen_set]
    rows = {r["record_id"]: r for r in db.all_rows(
        "select record_id, person_code, plain_bytes, format, answer_key_bytes from recording_person where dataset_version_id = %s and record_id = any(%s)",
        (str(source["id"]), list(chosen_set)))}
    by_name = {m["key"].rsplit("/", 1)[-1]: m for m in source["object_manifest"]}
    manifest = []
    for rid in sorted(chosen_set):
        for suffix in (recordings.WAV, recordings.ANSWER_KEY):
            entry = by_name.get(f"{rid}{suffix}")
            if entry:
                manifest.append(dict(entry))
    prefix = versions.next_version(tenant, dataset_id)["storage_prefix"]
    payload = json.dumps(index).encode("utf-8")
    storage.admin_client_for(backend, tenant).put_object(Bucket=storage.bucket_for(backend, tenant), Key=f"{prefix}/records.json", Body=payload)
    manifest.append({"key": f"{prefix}/records.json", "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    contract = versions.register_contract(tenant, "encounter_raw", ingest.ENCOUNTER_RAW_FIELDS, ["record_id"])

    action = db.one("select id from dataset_action where tenant_id = %s and name = 'development_sample'", (tenant,))
    if not action:
        db.execute("insert into dataset_action (id, tenant_id, name, output_class) values (%s, %s, 'development_sample', 'RAW') on conflict (tenant_id, name) do nothing returning id",
                   (str(uuid.uuid4()), tenant))
        action = db.one("select id from dataset_action where tenant_id = %s and name = 'development_sample'", (tenant,))
    run_id = str(uuid.uuid4())
    db.execute("""insert into action_run (id, tenant_id, action_id, code_hash, image_digest, params, input_versions, status, operator, idempotency_key,
                                          trigger_kind, triggered_by)
                  values (%s, %s, %s, %s, %s, %s, %s, 'running', %s, %s, 'manual', %s) returning id""",
               (run_id, tenant, str(action["id"]), METHOD, "platform", json.dumps({"kind": sample["kind"], "purpose": sample["purpose"], "records": len(chosen)}),
                [str(source["id"])], identity["id"], f"development-sample-{sample['id']}", sample["principal"]))
    version = versions.seal(tenant_id=tenant, dataset_id=dataset_id, schema_id=contract["id"], visibility_class="RAW", storage_backend=backend,
                            object_manifest=manifest, record_count=len(index), produced_by_run=run_id, records_key=f"{prefix}/records.json")
    for rid in sorted(chosen_set):
        r = rows[rid]
        db.execute("insert into recording_person (dataset_version_id, record_id, tenant_id, person_code, plain_bytes, format, answer_key_bytes) "
                   "values (%s, %s, %s, %s, %s, %s, %s) returning record_id",
                   (version["id"], rid, tenant, r["person_code"], r["plain_bytes"], r["format"], r["answer_key_bytes"]))
    return version["id"]


def end(identity: dict, sample_id: str) -> dict:
    sample = _sample(sample_id, identity["tenant_id"])
    allowed, reasons = opa.may_end_development_sample({
        "actor": {"id": identity["id"], "roles": identity["roles"]}, "request": {"principal": sample["principal"]},
        "dataset": {"approvers": approvers_of(sample["department_id"])}})
    if not allowed:
        raise NotPermitted(reasons)
    row = db.execute("update development_sample set status = 'ended', ended_by = %s, ended_at = now() "
                     "where id = %s and status in ('requested', 'approved') returning *", (identity["id"], sample_id))
    if row is None:
        raise LeaseConflict(["this sample has ended already"])
    return _labels([_shape(row)])[0]


def declare_synthetic(identity: dict, version_id: str, note: str) -> dict:
    version = _version(version_id, identity["tenant_id"])
    allowed, reasons = opa.may_declare_synthetic({
        "actor": {"id": identity["id"], "roles": identity["roles"]}, "request": {"note": note},
        "dataset": {"approvers": approvers_of(version["department_id"])}})
    if not allowed:
        raise NotPermitted(reasons)
    if db.one("select 1 as x from development_sample where sample_version_id = %s", (version_id,)):
        raise Refused(["this version is a development sample, so it holds real recordings and cannot be declared synthetic"])
    try:
        row = db.execute("insert into development_data_declaration (version_id, tenant_id, declared_by, note) values (%s, %s, %s, %s) returning *",
                         (version_id, identity["tenant_id"], identity["id"], note.strip()))
    except psycopg.errors.UniqueViolation as exc:
        raise LeaseConflict(["these recordings were declared synthetic already"]) from exc
    return _declaration(row)


def _declaration(row: dict | None) -> dict | None:
    if not row:
        return None
    label = db.one("select label from directory where id = %s", (row["declared_by"],))
    return {"version_id": str(row["version_id"]), "declared_by": row["declared_by"], "declared_by_label": label["label"] if label else row["declared_by"],
            "note": row["note"], "declared_at": _stamp(row["declared_at"])}


def for_version(identity: dict, version_id: str) -> dict:
    """What a screen needs on a version of recordings: this person's samples, the requests waiting for them as an approver, what they may do, the limits.

    Never a recording or a person. A person who is an approver sees the counts of what was drawn, band by band, and nothing more.
    """
    sweep()
    version = _version(version_id, identity["tenant_id"])
    approvers = approvers_of(version["department_id"])
    am_approver = identity["id"] in approvers
    own = db.one("select * from development_sample where sample_version_id = %s", (version_id,))
    source_info = None
    if own:
        source_info = {"source_version_id": str(own["source_version_id"]), "kind": own["kind"], "status": own["status"], "expires_at": _stamp(own["expires_at"]),
                       "records": own["granted_count"], "mine": own["principal"] == identity["id"]}
    mine = [_shape(r) for r in db.all_rows("select * from development_sample where tenant_id = %s and principal = %s and source_version_id = %s order by requested_at desc limit 50",
                                           (identity["tenant_id"], identity["id"], version_id))]
    waiting, granted = [], []
    if am_approver:
        waiting = [_shape(r) for r in db.all_rows("select * from development_sample where tenant_id = %s and source_version_id = %s and status = 'requested' and principal <> %s order by requested_at",
                                                  (identity["tenant_id"], version_id, identity["id"]))]
        granted = [_shape(r) for r in db.all_rows("select * from development_sample where tenant_id = %s and source_version_id = %s and status = 'approved' and expires_at > now() order by expires_at",
                                                  (identity["tenant_id"], version_id))]
    may_ask, _ = opa.may_request_development_sample({
        "requester": {"id": identity["id"], "roles": identity["roles"]},
        "request": {"principal": identity["id"], "kind": "develop", "purpose": "x", "days": 1, "records": 1, "named": 0}})
    may_declare, _ = opa.may_declare_synthetic({"actor": {"id": identity["id"], "roles": identity["roles"]}, "request": {"note": "x"}, "dataset": {"approvers": approvers}})
    locked = recordings.is_locked_version(version_id)
    live = {r["kind"] for r in mine if r["status"] in ("requested", "approved")}
    return {
        "version_id": version_id,
        "is_sample": source_info,
        "synthetic": _declaration(synthetic_of(version_id)),
        "may_ask": bool(may_ask) and locked and own is None,
        "may_ask_kinds": [k for k in ("develop", "debug") if k not in live] if bool(may_ask) and locked and own is None else [],
        "may_decide": am_approver,
        "may_declare_synthetic": bool(may_declare) and own is None and synthetic_of(version_id) is None,
        "mine": _labels(mine),
        "to_decide": _labels(waiting),
        "granted": _labels(granted),
        "limits": {"days": opa.read_document("munitas/access/development_sample_max_days"), "records": opa.read_document("munitas/access/development_sample_max_records")},
    }
