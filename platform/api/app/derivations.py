"""A new dataset made from a query over existing ones.

A person names the datasets a query reads, the query, and what the result is to
be called. The platform answers with a draft: the versions it would read, the
shape of the result, and the sensitivity each field must carry. Nothing is
registered and nothing runs until the person confirms. After that a worker runs
the query in a sandbox that has no network and no credentials, the platform
checks the rows against the declared shape, and seals the result as an ordinary
dataset version, with the query and its input versions on record.

Three rules are enforced here in code, not left to a label:

  * Every input must be readable by the person, right now, by the same decision
    a table open makes. The SQL cannot name anything else, because the sandbox
    only has the inputs that were declared.
  * A field may not carry less sensitivity than the fields it was computed from.
    Raising it is free. Lowering it is refused, since lowering is a claim that
    needs somebody other than the person who made the query.
  * The result starts at the strictest class of any input, so a derived dataset
    is never easier to reach than what it came from.

Parsing and describing the query is done by DuckDB itself, over empty tables
with the inputs' column types, with external access switched off and the
configuration locked. No row of any input is read here.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException

from . import access_preview, auth, config, db, logs, models, versions

log = logs.get_logger("derivations")
router = APIRouter(tags=["derivations"])

# Most to least restrictive. The order is the policy's own (access.rego).
CLASS_ORDER = ["RAW", "UNDER_REVIEW", "OPEN_FOR_ANNOTATION", "OPEN_FOR_TRAINING", "PUBLISHED"]
SENSITIVITY_ORDER = ["none", "quasi", "direct", "phi"]
DRAFT_LIFETIME = timedelta(hours=1)
DESCRIBE_SECONDS = 10
COLUMN_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
# The staging limit the sandbox already applies to copied-in data.
MAX_INPUT_BYTES = 1024 * 1024 * 1024
RUNNER_VERSION = "derive-1"

_DUCKDB_TYPE = {"string": "VARCHAR", "float": "DOUBLE", "int": "BIGINT", "bool": "BOOLEAN"}
_INTEGER_TYPES = {"BIGINT", "INTEGER", "SMALLINT", "TINYINT", "HUGEINT", "UBIGINT",
                  "UINTEGER", "USMALLINT", "UTINYINT", "UHUGEINT"}


def contract_type(duckdb_type: str) -> str:
    """The contract's name for what DuckDB calls this. Anything with no plain
    equivalent (dates, times, lists, structs) is carried as text, which is how
    the projection already treats it."""
    base = duckdb_type.split("(")[0].upper()
    if base in _INTEGER_TYPES:
        return "int"
    if base in ("DOUBLE", "FLOAT", "DECIMAL"):
        return "float"
    if base == "BOOLEAN":
        return "bool"
    return "string"


def _worst(values: list[str], order: list[str]) -> str:
    return max(values, key=order.index) if values else order[0]


class _Complex(Exception):
    """The query is shaped in a way its columns cannot be traced one by one."""


# ------------------------------------------------------------- the query --


def _duck(inputs: list[dict]):
    """A DuckDB connection that holds the inputs as empty tables and can reach
    nothing else."""
    import duckdb

    con = duckdb.connect(":memory:")
    con.execute("SET threads = 1")
    con.execute("SET memory_limit = '256MB'")
    for item in inputs:
        columns = ", ".join(
            f'"{f["name"]}" {_DUCKDB_TYPE.get(f.get("type", "string"), "VARCHAR")}'
            for f in item["fields"])
        con.execute(f'CREATE TABLE "{item["alias"]}" ({columns})')
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    return con


def _parse(con, sql: str) -> dict:
    """The parse tree of exactly one SELECT, or a refusal that says why."""
    raw = con.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()[0]
    tree = json.loads(raw)
    if tree.get("error"):
        raise HTTPException(422, {"reasons": [
            "only a single SELECT can be run: " + str(tree.get("error_message", ""))[:200]]})
    if len(tree.get("statements", [])) != 1:
        raise HTTPException(422, {"reasons": ["send exactly one statement"]})
    return tree["statements"][0]["node"]


def _describe(con, sql: str) -> list[tuple[str, str]]:
    """The names and types the query would produce, without running it."""
    timer = threading.Timer(DESCRIBE_SECONDS, con.interrupt)
    timer.start()
    try:
        rows = con.execute(f"DESCRIBE SELECT * FROM ({sql}) AS derived").fetchall()
    except Exception as exc:  # noqa: BLE001 - reported to the person, not raised
        kind = type(exc).__name__
        if kind in ("ParserException", "BinderException", "CatalogException"):
            raise HTTPException(422, {"reasons": [str(exc).splitlines()[0][:300]]}) from exc
        if kind == "PermissionException":
            # External access is switched off for exactly this: a query that
            # reads a file or an address is reaching outside its inputs.
            raise HTTPException(422, {"reasons": [
                "that query reaches outside the datasets it declared (a file or an address). "
                "A query may read only its declared inputs"]}) from exc
        raise HTTPException(422, {"reasons": [
            f"the query could not be described ({kind}). Give every computed column an "
            "alias, and avoid queries whose columns depend on the data, such as PIVOT"]}) from exc
    finally:
        timer.cancel()
    return [(r[0], r[1]) for r in rows]


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk(value)


def _from_tables(node: dict) -> list[dict]:
    """The base tables of a FROM clause in order, or _Complex for anything else."""
    kind = node.get("type")
    if kind == "BASE_TABLE":
        return [node]
    if kind == "JOIN":
        return _from_tables(node["left"]) + _from_tables(node["right"])
    raise _Complex()


def _traced_floor(tree: dict, inputs: list[dict], described: list[tuple[str, str]]) -> list[str]:
    """For each output column, the sensitivity of the input columns it draws on.

    Raises _Complex for a query whose columns cannot be traced one by one
    (subqueries, set operations, CTEs, columns defined in terms of each other).
    The caller then takes the most cautious answer.
    """
    if tree.get("type") != "SELECT_NODE" or tree.get("cte_map", {}).get("map"):
        raise _Complex()
    tables = _from_tables(tree["from_table"])
    by_alias = {i["alias"]: {f["name"]: f.get("sensitivity", "none") for f in i["fields"]}
                for i in inputs}
    # A table may be given a short name in the query, so map what the query
    # calls each table back to the input it is.
    names: dict[str, str] = {}
    for t in tables:
        alias = t.get("alias") or t.get("table_name")
        names[alias] = t["table_name"]
        names.setdefault(t["table_name"], t["table_name"])
    order = [t["table_name"] for t in tables]
    if any(n not in by_alias for n in order):
        raise _Complex()

    def sensitivity_of(column_names: list[str]) -> str:
        if len(column_names) == 2:
            owner = names.get(column_names[0])
            if owner is None or column_names[1] not in by_alias[owner]:
                raise _Complex()
            return by_alias[owner][column_names[1]]
        if len(column_names) == 1:
            found = [by_alias[n][column_names[0]] for n in order if column_names[0] in by_alias[n]]
            if not found:
                raise _Complex()
            return _worst(found, SENSITIVITY_ORDER)
        raise _Complex()

    floors: list[str] = []
    for item in tree["select_list"]:
        if item.get("class") == "STAR":
            if item.get("columns"):
                raise _Complex()
            relation = item.get("relation_name") or ""
            owners = [names[relation]] if relation else order
            for owner in owners:
                floors.extend(by_alias[owner].values())
            continue
        found = []
        for sub in _walk(item):
            if sub.get("class") == "SUBQUERY":
                raise _Complex()
            if sub.get("class") == "STAR":
                raise _Complex()
            if sub.get("class") == "COLUMN_REF":
                found.append(sensitivity_of(sub["column_names"]))
        floors.append(_worst(found, SENSITIVITY_ORDER))
    if len(floors) != len(described):
        raise _Complex()
    return floors


def sensitivity_floors(tree: dict, inputs: list[dict], described: list[tuple[str, str]]) -> list[str]:
    try:
        return _traced_floor(tree, inputs, described)
    except _Complex:
        every = [f.get("sensitivity", "none") for i in inputs for f in i["fields"]]
        return [_worst(every, SENSITIVITY_ORDER)] * len(described)


# ---------------------------------------------------------------- inputs --


def _resolve_inputs(identity: dict, requested: list[models.DerivationInput]) -> list[dict]:
    """The version each input names, and a refusal for any the person may not read."""
    resolved, seen = [], set()
    for item in requested:
        alias = item.alias or re.sub(r"[^A-Za-z0-9]", "_", item.dataset)
        if not COLUMN_NAME.match(alias):
            raise HTTPException(422, {"reasons": [f"give {item.dataset!r} an alias made of letters, digits and underscores"]})
        if alias.lower() in seen:
            raise HTTPException(422, {"reasons": [f"two inputs are both called {alias!r}"]})
        seen.add(alias.lower())
        dataset = db.one(
            "select id::text as id, name, department_id::text as department_id, provenance "
            "from dataset where tenant_id = %s and name = %s",
            (identity["tenant_id"], item.dataset))
        if not dataset:
            raise HTTPException(404, {"reasons": [f"no dataset called {item.dataset!r}"]})
        row = db.one(
            """select dv.id::text as version_id, dv.version, dv.schema_id::text as schema_id,
                      dv.object_manifest, dv.record_count, vc.current_class,
                      (select count(*) from iceberg_table_ref r where r.dataset_version_id = dv.id) as is_table
                 from dataset_version dv join version_class vc on vc.dataset_version_id = dv.id
                where dv.dataset_id = %s and dv.sealed
                  and (%s::int is null or dv.version = %s)
                order by dv.version desc limit 1""",
            (dataset["id"], item.version, item.version))
        if not row:
            raise HTTPException(404, {"reasons": [
                f"{item.dataset!r} has no sealed version" + (f" {item.version}" if item.version else "")]})
        if not row["is_table"]:
            raise HTTPException(422, {"reasons": [
                f"{item.dataset!r} version {row['version']} is not a table, so a query cannot read it"]})
        contract = db.one("select fields from schema_contract where id = %s", (row["schema_id"],))
        data_bytes = sum(int(e.get("bytes") or 0) for e in (row["object_manifest"] or []))
        if data_bytes > MAX_INPUT_BYTES:
            raise HTTPException(413, {"reasons": [
                f"{item.dataset!r} version {row['version']} is {data_bytes} bytes, over the "
                f"{MAX_INPUT_BYTES} byte limit for a query that copies its inputs in"]})
        resolved.append({
            "alias": alias, "dataset": dataset["name"], "dataset_id": dataset["id"],
            "department_id": dataset["department_id"], "provenance": dataset["provenance"],
            "version": row["version"], "version_id": row["version_id"],
            "class": row["current_class"], "fields": contract["fields"],
        })
    answer = access_preview.access_preview(
        models.AccessPreviewIn(version_ids=[i["version_id"] for i in resolved]), identity=identity)
    for item in resolved:
        mark = answer["versions"][item["version_id"]]["mark"]
        if mark not in access_preview._READABLE:
            raise HTTPException(403, {"reasons": [
                f"you cannot read {item['dataset']!r} version {item['version']} right now "
                f"(it is {item['class']}). Ask for access to it first"]})
    return resolved


def _output_class(inputs: list[dict]) -> str:
    return min((i["class"] for i in inputs), key=CLASS_ORDER.index)


def _strictest_input(inputs: list[dict]) -> dict:
    return min(inputs, key=lambda i: CLASS_ORDER.index(i["class"]))


def _provenance(inputs: list[dict]) -> str:
    order = ["internal_regulated", "external_licensed", "external_public"]
    return min((i["provenance"] for i in inputs), key=order.index)


def _derivation_key(tenant_id: str, person: str, target: str, sql: str, inputs: list[dict],
                    fields: list[dict], primary_key: list[str]) -> str:
    """Identifies one request: this person asking for this named result of this
    query over these input versions. The person and the name are part of it so a
    repeat finds the person's own earlier request, never somebody else's and
    never a dataset under a different name than the one asked for."""
    body = {
        "tenant": tenant_id, "person": person, "target": target, "sql": " ".join(sql.split()),
        "inputs": sorted((i["alias"], i["version_id"]) for i in inputs),
        "fields": [(f["name"], f["type"], f["sensitivity"]) for f in fields],
        "primary_key": primary_key,
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


# --------------------------------------------------------------- the API --


def _shown(row: dict) -> dict:
    return {
        "id": str(row["id"]), "status": row["status"], "target_name": row["target_name"],
        "purpose": row["purpose"], "output_class": row["output_class"], "sql": row["sql"],
        "inputs": [{k: i[k] for k in ("alias", "dataset", "version", "class")} for i in row["inputs"]],
        "fields": row["proposed_fields"], "primary_key": list(row["primary_key"]),
        "output_version_id": str(row["output_version_id"]) if row["output_version_id"] else None,
        "error": row["error"], "created_at": row["created_at"], "ended_at": row["ended_at"],
    }


@router.post("/derivations", status_code=201)
def draft(body: models.DerivationDraftIn, identity: dict = Depends(auth.current_session)) -> dict:
    """Answer a query with what it would read and what it would produce. Runs
    nothing and registers nothing."""
    if db.one("select 1 as x from dataset where tenant_id = %s and name = %s",
              (identity["tenant_id"], body.target_name)):
        raise HTTPException(409, {"reasons": [f"a dataset called {body.target_name!r} already exists"]})

    inputs = _resolve_inputs(identity, body.inputs)
    con = _duck(inputs)
    try:
        tree = _parse(con, body.sql)
        described = _describe(con, body.sql)
    finally:
        con.close()

    names = [n for n, _ in described]
    bad = [n for n in names if not COLUMN_NAME.match(n)]
    if bad:
        raise HTTPException(422, {"reasons": [
            f"give these columns an alias made of letters, digits and underscores: {bad}"]})
    if len({n.lower() for n in names}) != len(names):
        raise HTTPException(422, {"reasons": ["two output columns have the same name"]})
    missing = [k for k in body.primary_key if k not in names]
    if missing:
        raise HTTPException(422, {"reasons": [f"the primary key names columns the query does not produce: {missing}"]})

    floors = sensitivity_floors(tree, inputs, described)
    fields = [{"name": n, "type": contract_type(t), "duckdb_type": t, "sensitivity": floor, "floor": floor}
              for (n, t), floor in zip(described, floors)]
    out_class = _output_class(inputs)
    draft_id = str(uuid.uuid4())
    db.execute(
        """insert into derivation
             (id, tenant_id, submitted_by, status, sql, target_name, purpose, inputs,
              primary_key, proposed_fields, output_class)
           values (%s, %s, %s, 'draft', %s, %s, %s, %s, %s, %s, %s)""",
        (draft_id, identity["tenant_id"], identity["id"], body.sql, body.target_name,
         body.purpose, json.dumps(inputs), body.primary_key, json.dumps(fields), out_class))
    log.info("derivation drafted", extra={"tenant_id": identity["tenant_id"], "derivation_id": draft_id})
    return {**_shown(db.one("select * from derivation where id = %s", (draft_id,))),
            "expires_at": datetime.now(timezone.utc) + DRAFT_LIFETIME}


def _own(derivation_id: str, identity: dict) -> dict:
    row = db.one("select * from derivation where id = %s and tenant_id = %s",
                 (derivation_id, identity["tenant_id"]))
    if not row or row["submitted_by"] != identity["id"]:
        raise HTTPException(404, {"reasons": ["no such derivation"]})
    return row


@router.get("/derivations/{derivation_id}")
def show(derivation_id: str, identity: dict = Depends(auth.current_session)) -> dict:
    return _shown(_own(derivation_id, identity))


@router.get("/derivations")
def mine(identity: dict = Depends(auth.current_session)) -> dict:
    rows = db.all_rows(
        "select * from derivation where submitted_by = %s and tenant_id = %s "
        "order by created_at desc limit 50", (identity["id"], identity["tenant_id"]))
    return {"derivations": [_shown(r) for r in rows]}


@router.post("/derivations/{derivation_id}/confirm", status_code=202)
async def confirm(derivation_id: str, body: models.DerivationConfirmIn,
                  identity: dict = Depends(auth.current_session)) -> dict:
    """Register the result's shape and dataset, and start the run."""
    row = _own(derivation_id, identity)
    if row["status"] != "draft":
        raise HTTPException(409, {"reasons": [f"this derivation is {row['status']}, not a draft"]})
    if row["created_at"] + DRAFT_LIFETIME < datetime.now(timezone.utc):
        db.execute("update derivation set status = 'expired', ended_at = now() where id = %s", (derivation_id,))
        raise HTTPException(410, {"reasons": ["this draft has expired. Send the query again"]})

    fields = [dict(f) for f in row["proposed_fields"]]
    known = {f["name"] for f in fields}
    for name, level in body.sensitivities.items():
        if name not in known:
            raise HTTPException(422, {"reasons": [f"no output column called {name!r}"]})
    for f in fields:
        wanted = body.sensitivities.get(f["name"], f["sensitivity"])
        if SENSITIVITY_ORDER.index(wanted) < SENSITIVITY_ORDER.index(f["floor"]):
            raise HTTPException(422, {"reasons": [
                f"{f['name']!r} is computed from fields marked {f['floor']!r}, so it cannot be "
                f"marked {wanted!r}. A sensitivity can only be raised here: lowering one needs "
                "the agreement of somebody other than the person who wrote the query"]})
        f["sensitivity"] = wanted

    # The inputs are checked again: the draft may be an hour old, and a lease
    # that has ended since must stop the run, not just the preview.
    inputs = row["inputs"]
    for item in inputs:
        current = db.one("select current_class from version_class where dataset_version_id = %s",
                         (item["version_id"],))
        item["class"] = current["current_class"]
    answer = access_preview.access_preview(
        models.AccessPreviewIn(version_ids=[i["version_id"] for i in inputs]), identity=identity)
    for item in inputs:
        if answer["versions"][item["version_id"]]["mark"] not in access_preview._READABLE:
            raise HTTPException(403, {"reasons": [
                f"you can no longer read {item['dataset']!r} version {item['version']}"]})
    out_class = _output_class(inputs)

    key = _derivation_key(identity["tenant_id"], identity["id"], row["target_name"], row["sql"], inputs,
                          fields, list(row["primary_key"]))
    earlier = db.one(
        """select * from derivation where derivation_key = %s and tenant_id = %s
              and status in ('queued', 'running', 'succeeded') order by created_at limit 1""",
        (key, identity["tenant_id"]))
    if earlier:
        return {**_shown(earlier), "reused": True}

    if db.one("select 1 as x from dataset where tenant_id = %s and name = %s",
              (identity["tenant_id"], row["target_name"])):
        raise HTTPException(409, {"reasons": [f"a dataset called {row['target_name']!r} already exists"]})

    # Every run is recorded under one action per organisation, which has no
    # fixed shape because each derivation brings its own.
    db.execute(
        """insert into dataset_action (id, tenant_id, name) values (%s, %s, 'derive')
           on conflict (tenant_id, name) do nothing""",
        (str(uuid.uuid4()), identity["tenant_id"]))
    contract = versions.register_contract(
        identity["tenant_id"], row["target_name"],
        [{"name": f["name"], "type": f["type"], "format": None,
          "sensitivity": f["sensitivity"], "added_by": identity["id"]} for f in fields],
        list(row["primary_key"]))
    strictest = _strictest_input(inputs)
    dataset_id = str(uuid.uuid4())
    db.execute(
        """insert into dataset
             (id, tenant_id, name, department_id, provenance, registered_by, registered_at,
              declared_class, declared_by, declared_at, declaration_basis, modality)
           values (%s, %s, %s, %s, %s, %s, now(), %s, %s, now(), 'verified_source', %s)""",
        (dataset_id, identity["tenant_id"], row["target_name"], strictest["department_id"],
         _provenance(inputs), identity["id"], out_class, identity["id"], ["tabular"]))
    db.execute(
        """update derivation
              set status = 'queued', proposed_fields = %s, inputs = %s, output_class = %s,
                  derivation_key = %s, dataset_id = %s, schema_id = %s, confirmed_at = now()
            where id = %s""",
        (json.dumps(fields), json.dumps(inputs), out_class, key, dataset_id,
         contract["id"], derivation_id))
    await _start_run(derivation_id, identity["tenant_id"])
    log.info("derivation confirmed", extra={"tenant_id": identity["tenant_id"], "derivation_id": derivation_id})
    return {**_shown(db.one("select * from derivation where id = %s", (derivation_id,))), "reused": False}


async def _start_run(derivation_id: str, tenant_id: str) -> None:
    from . import temporal_client
    try:
        client = temporal_client.get()
        await client.start_workflow(
            "DerivationWorkflow", {"derivation_id": derivation_id, "tenant_id": tenant_id},
            id=f"derivation-{derivation_id}", task_queue=config.DERIVATION_TASK_QUEUE)
    except Exception as exc:  # noqa: BLE001 - recorded on the row and reported
        db.execute("update derivation set status = 'failed', error = %s, ended_at = now() where id = %s",
                   (f"the job runner could not start it: {type(exc).__name__}", derivation_id))
        raise HTTPException(503, {"reasons": ["the background job runner is unavailable. Nothing was run"]}) from exc


# ------------------------------------------------------ the worker's calls --


def _worker(x_worker_token: str | None = Header(default=None)) -> None:
    if not config.WORKER_TOKEN or x_worker_token != config.WORKER_TOKEN:
        raise HTTPException(403, {"reasons": ["only the platform's own workers may call this"]})


@router.get("/derivations/{derivation_id}/job", dependencies=[Depends(_worker)])
def job(derivation_id: str) -> dict:
    """What a worker needs to run one confirmed derivation."""
    row = db.one("select * from derivation where id = %s", (derivation_id,))
    if not row or row["status"] not in ("queued", "running"):
        raise HTTPException(404, {"reasons": ["no such derivation waiting to run"]})
    versions_info = []
    for item in row["inputs"]:
        v = db.one("select storage_prefix, storage_backend, object_manifest from dataset_version where id = %s",
                   (item["version_id"],))
        versions_info.append({**item, "storage_prefix": v["storage_prefix"],
                              "storage_backend": v["storage_backend"],
                              "object_manifest": v["object_manifest"]})
    workload = db.one(
        "select id from directory where tenant_id = %s and kind = 'workload' "
        "and 'pipeline_action' = any(roles) order by id limit 1", (row["tenant_id"],))
    action = db.one("select id::text as id from dataset_action where tenant_id = %s and name = 'derive'",
                    (row["tenant_id"],))
    return {
        "id": str(row["id"]), "tenant_id": row["tenant_id"], "submitted_by": row["submitted_by"],
        "action_id": action["id"] if action else None,
        "sql": row["sql"], "purpose": row["purpose"], "inputs": versions_info,
        "primary_key": list(row["primary_key"]), "fields": row["proposed_fields"],
        "output_class": row["output_class"], "dataset_id": str(row["dataset_id"]),
        "schema_id": str(row["schema_id"]), "workload": workload["id"] if workload else None,
        "runner_version": RUNNER_VERSION,
    }


@router.post("/derivations/{derivation_id}/running", dependencies=[Depends(_worker)])
def running(derivation_id: str, body: dict) -> dict:
    db.execute("update derivation set status = 'running', action_run_id = %s "
               "where id = %s and status in ('queued', 'running')",
               (body["action_run_id"], derivation_id))
    return {"status": "running"}


@router.post("/derivations/{derivation_id}/complete", dependencies=[Depends(_worker)])
def complete(derivation_id: str, body: dict) -> dict:
    """The run sealed a version. Checked here, not taken on trust: the version
    must be this derivation's dataset and must have come from this run. Then the
    person who asked is given access to the result for as long as their access
    to the inputs lasts."""
    row = db.one("select * from derivation where id = %s", (derivation_id,))
    version = db.one(
        "select id::text as id, dataset_id::text as dataset_id, produced_by_run::text as run "
        "from dataset_version where id = %s", (body["output_version_id"],))
    if (not row or not version or version["dataset_id"] != str(row["dataset_id"])
            or version["run"] != str(row["action_run_id"])):
        raise HTTPException(422, {"reasons": ["that version is not this derivation's result"]})
    _grant_submitter(row, version["id"])
    db.execute("update derivation set status = 'succeeded', output_version_id = %s, ended_at = now() "
               "where id = %s", (version["id"], derivation_id))
    return {"status": "succeeded", "output_version_id": version["id"]}


def _grant_submitter(row: dict, output_version_id: str) -> None:
    """Let the person read what they made, without waiting for anybody, but only
    while they could still read what it was made from."""
    identity = auth.identity_for(row["submitted_by"])
    answer = access_preview.access_preview(
        models.AccessPreviewIn(version_ids=[output_version_id]), identity=identity)
    if answer["versions"][output_version_id]["mark"] in access_preview._READABLE:
        return
    ends = db.one(
        """select min(expires_at) as ends from access_lease
            where principal = %s and revoked = false and expires_at > now()
              and dataset_version_id = any(%s::uuid[])""",
        (row["submitted_by"], [i["version_id"] for i in row["inputs"]]))["ends"]
    ends = ends or datetime.now(timezone.utc) + timedelta(hours=24)
    approver = db.one(
        "select id from directory where tenant_id = %s and kind = 'workload' "
        "and 'pipeline_action' = any(roles) order by id limit 1", (row["tenant_id"],))
    db.execute(
        """insert into access_lease
             (id, tenant_id, principal, requested_by, dataset_version_id, purpose,
              approved_by, expires_at, pattern, derivation_id)
           values (%s, %s, %s, %s, %s, %s, %s, %s, 'strict', %s)""",
        (str(uuid.uuid4()), row["tenant_id"], row["submitted_by"], row["submitted_by"],
         output_version_id, row["purpose"], approver["id"], ends, str(row["id"])))


@router.post("/derivations/{derivation_id}/fail", dependencies=[Depends(_worker)])
def fail(derivation_id: str, body: dict) -> dict:
    db.execute("update derivation set status = 'failed', error = %s, ended_at = now() "
               "where id = %s and status in ('queued', 'running')",
               (str(body.get("reason", ""))[:500], derivation_id))
    return {"status": "failed"}
