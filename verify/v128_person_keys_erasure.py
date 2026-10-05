"""U128: one person's data is locked under a key of their own, and erasing the person destroys that key.

A sealed table never changes, and a person can still be forgotten: the columns that identify them are locked under a key made for that
person, and erasure destroys the key and nothing else. This checks, one item at a time, on a real database and real object storage, with a
table the check makes up for itself (invented patients, invented numbers):

  * a contract is refused when it is wrong: a misspelt treatment, an identifying column left readable, no rule for finding the person, a
    treatment this version does not offer, a rule that names a column which is not there, a different standard version;
  * two rows of one person share one person code and one key, two people never do, and the same text under two kinds of identifier is two
    people;
  * a table with rows that name nobody is refused whole, every such row is listed, and nothing is made or sealed;
  * the sealed file holds no readable identifying value, its fingerprint covers the stored bytes, and it does not change after erasure;
  * a permitted reader gets the locked columns back and the decision is recorded; a reader who is not permitted is refused and that is
    recorded as well;
  * erasing person A makes A's locked columns report an erased marker, leaves A's readable columns, leaves person B whole, makes the lookup
    of A's identifier find nothing, and leaves a tombstone;
  * every one of those checks runs twice: once with the built-in key service and once with a second, fake implementation, which proves the
    seam between the platform and whatever holds the keys.

    docker compose exec -T munitas-api python /verify/v128_person_keys_erasure.py
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/app")
sys.path.insert(0, "/")

from common import ADMIN, CANARY, api, bearer_for, check, db, fixture_tenant, heading, require_api, s3_client, summary  # noqa: E402

ENGINEER = "canary-engineer"
CUSTODIAN = "canary-custodian"
RESEARCHER = "canary-researcher"

LOCKED_COLUMNS = ["patient_name", "diagnosis"]
READABLE_COLUMNS = ["visit_id", "clinic", "age"]


def column(name, logical="string", classification="none", treatment="readable", **extra):
    props = [{"property": "treatment", "vendor": "munitas", "value": treatment}]
    props += [{"property": k, "vendor": "munitas", "value": v} for k, v in extra.items()]
    out = {"id": f"{name}_prop", "name": name, "logicalType": logical, "classification": classification, "customProperties": props}
    return out


def good_contract(kind: str = "clinic number", name: str = "visits") -> dict:
    return {
        "apiVersion": "v3.2.0", "kind": "DataContract", "id": name, "name": name, "version": "1.0.0", "status": "active",
        "schema": [{"id": f"{name}_obj", "name": name, "logicalType": "object", "properties": [
            {**column("visit_id", treatment="readable"), "primaryKey": True, "required": True},
            column("clinic_number", classification="direct", treatment="replaced by the person code", personIdentifier=True),
            column("patient_name", classification="direct", treatment="lock"),
            column("diagnosis", classification="phi", treatment="lock"),
            column("clinic", treatment="readable"),
            column("age", "integer", classification="quasi", treatment="readable"),
        ]}],
        "customProperties": [
            {"property": "subject", "vendor": "munitas", "value": {"kind": kind, "foundBy": "column", "column": "clinic_number"}},
            {"property": "erasable", "vendor": "munitas", "value": True},
        ],
    }


def with_treatment(contract: dict, name: str, value: str) -> dict:
    out = copy.deepcopy(contract)
    for prop in out["schema"][0]["properties"]:
        if prop["name"] == name:
            for cp in prop["customProperties"]:
                if cp["property"] == "treatment":
                    cp["value"] = value
    return out


def without_subject(contract: dict) -> dict:
    out = copy.deepcopy(contract)
    out["customProperties"] = [c for c in out["customProperties"] if c["property"] != "subject"]
    return out


def rows_for(a: str, b: str) -> list[dict]:
    return [
        {"visit_id": "v1", "clinic_number": a, "patient_name": "Ada Example", "diagnosis": "asthma", "clinic": "north", "age": 41},
        {"visit_id": "v2", "clinic_number": a, "patient_name": "Ada Example", "diagnosis": "review", "clinic": "south", "age": 41},
        {"visit_id": "v3", "clinic_number": b, "patient_name": "Ben Example", "diagnosis": "fracture", "clinic": "north", "age": 57},
    ]


def register_dataset(person: str) -> str:
    with db() as conn:
        dept = str(conn.execute("select id from department where tenant_id = %s and name = 'Verification'", (CANARY,)).fetchone()["id"])
    body = {"tenant_id": CANARY, "name": f"u128-{uuid.uuid4().hex[:8]}", "department_id": dept, "registered_by": person,
            "provenance": "internal_regulated", "declared_class": "RAW", "source_kind": "upload"}
    r = api("POST", "/datasets/register", json=body, headers=bearer_for(person))
    r.raise_for_status()
    return r.json()["id"]


def count(sql: str, params: tuple = ()) -> int:
    with db() as conn:
        return conn.execute(sql, params).fetchone()["n"]


def identity(person: str, roles: list[str]) -> dict:
    return {"id": person, "kind": "human", "roles": roles, "tenant_id": CANARY}


def stored_bytes(version_id: str) -> tuple[bytes, dict]:
    with db() as conn:
        v = conn.execute("select storage_prefix, object_manifest from dataset_version where id = %s", (version_id,)).fetchone()
    entry = next(m for m in v["object_manifest"] if m["key"].endswith("table.jsonl"))
    from common import bucket_for
    body = s3_client(*ADMIN).get_object(Bucket=bucket_for(CANARY), Key=entry["key"])["Body"].read()
    return body, entry


def build_fake(KeyService, DestroyedKeyError, UnknownRecordError):
    """A second key service that shares nothing with the built-in one: keys in a dict, no database, no master key file."""

    class Fake(KeyService):
        def __init__(self):
            self.keys, self.gone, self.log, self.secrets = {}, {}, [], {}

        def lookup_secret(self, tenant_id):
            return self.secrets.setdefault(tenant_id, os.urandom(32))

        def make_person_key(self, tenant_id, person_code, actor):
            key = os.urandom(32)
            self.keys[(tenant_id, person_code)] = key
            self.log.append(("make", person_code, actor))
            return key

        def unwrap(self, tenant_id, person_code, actor, purpose):
            if (tenant_id, person_code) in self.gone:
                raise DestroyedKeyError(person_code)
            if (tenant_id, person_code) not in self.keys:
                raise UnknownRecordError(person_code)
            self.log.append(("unwrap", person_code, actor))
            return self.keys[(tenant_id, person_code)]

        def unwrap_many(self, tenant_id, person_codes, actor, purpose):
            out = {}
            for code in person_codes:
                try:
                    out[code] = self.unwrap(tenant_id, code, actor, purpose)
                except DestroyedKeyError:
                    out[code] = None
            return out

        def destroy(self, tenant_id, person_code, actor):
            existed = (tenant_id, person_code) in self.keys and (tenant_id, person_code) not in self.gone
            self.keys.pop((tenant_id, person_code), None)
            import datetime
            self.gone[(tenant_id, person_code)] = datetime.datetime.now(datetime.timezone.utc)
            self.log.append(("destroy", person_code, actor))
            return existed

        def status(self, tenant_id, person_code):
            if (tenant_id, person_code) in self.gone:
                return "destroyed", self.gone[(tenant_id, person_code)]
            return ("live", None) if (tenant_id, person_code) in self.keys else ("unknown", None)

        def use_log(self, tenant_id, person_code):
            return [e for e in self.log if e[1] == person_code]

    return Fake()


def run_suite(label: str, service, person_keys, privacy_contract) -> None:
    heading(f"{label}: contracts")
    good = good_contract()
    check("a correct contract is accepted", privacy_contract.check(good) == [], str(privacy_contract.check(good)))
    problems = privacy_contract.check(with_treatment(good, "patient_name", "lokc"))
    check("a misspelt treatment is refused, and the refusal names the column and the word", any("patient_name" in p and "lokc" in p for p in problems), str(problems))
    problems = privacy_contract.check(with_treatment(good, "diagnosis", "readable"))
    check("an identifying column left readable, with no approved reason, is refused", any("diagnosis" in p for p in problems), str(problems))
    problems = privacy_contract.check(without_subject(good))
    check("a contract with identifying columns and no rule for finding the person is refused", any("subject" in p for p in problems), str(problems))
    problems = privacy_contract.check(with_treatment(good, "age", "generalise"))
    check("a treatment this version does not offer is refused, and the refusal says so", any("age" in p and "generalise" in p for p in problems), str(problems))
    broken = copy.deepcopy(good)
    broken["customProperties"][0]["value"]["column"] = "nowhere"
    problems = privacy_contract.check(broken)
    check("a rule that names a column which is not there is refused", any("nowhere" in p for p in problems), str(problems))
    old = copy.deepcopy(good)
    old["apiVersion"] = "v3.1.0"
    check("a different version of the standard is refused", any("v3.2.0" in p for p in privacy_contract.check(old)), str(privacy_contract.check(old)))
    reasoned = with_treatment(good, "diagnosis", "readable")
    for prop in reasoned["schema"][0]["properties"]:
        if prop["name"] == "diagnosis":
            prop["customProperties"] += [{"property": "treatmentReason", "vendor": "munitas", "value": "Needed for audit."},
                                         {"property": "treatmentApprovedBy", "vendor": "munitas", "value": "a data protection officer"}]
    check("the same column left readable with a recorded and approved reason is accepted", privacy_contract.check(reasoned) == [], str(privacy_contract.check(reasoned)))

    heading(f"{label}: one person, one code, one key")
    tag = uuid.uuid4().hex[:8]
    a, b = f"A-{tag}", f"B-{tag}"
    code_a = person_keys.resolve_person(service, CANARY, "clinic number", a, ENGINEER)
    check("the same identifier of the same kind finds the same person code", person_keys.resolve_person(service, CANARY, "clinic number", a, ENGINEER) == code_a)
    code_b = person_keys.resolve_person(service, CANARY, "clinic number", b, ENGINEER)
    check("a different person gets a different person code", code_b != code_a)
    other_kind = person_keys.resolve_person(service, CANARY, "passport number", a, ENGINEER)
    check("the same text under another kind of identifier is another person", other_kind not in (code_a, code_b))
    check("the person code means nothing on its own: it does not contain the identifier", a not in code_a and code_a.startswith("T-"), code_a)
    with db() as conn:
        stored = conn.execute("select blind_index from subject_lookup where tenant_id = %s and person_code = %s", (CANARY, code_a)).fetchone()
    check("the lookup stores a blind index, never the identifier", stored is not None and a not in stored["blind_index"] and len(stored["blind_index"]) == 64, str(stored)[:90])

    heading(f"{label}: a table with rows that name nobody")
    dataset = register_dataset(ENGINEER)
    bad_rows = rows_for(f"C-{tag}", f"D-{tag}") + [
        {"visit_id": "v4", "clinic_number": "", "patient_name": "No Body", "diagnosis": "x", "clinic": "north", "age": 30},
        {"visit_id": "v5", "clinic_number": None, "patient_name": "No Body", "diagnosis": "y", "clinic": "north", "age": 31}]
    before = (count("select count(*) as n from subject_lookup where tenant_id = %s", (CANARY,)),
              count("select count(*) as n from dataset_version where dataset_id = %s", (dataset,)))
    try:
        person_keys.seal_table(service, dataset_id=dataset, identity=identity(ENGINEER, ["pipeline_operator"]), contract=good, rows=bad_rows)
        refused, reasons = False, []
    except person_keys.Refused as exc:
        refused, reasons = True, exc.reasons
    check("the whole table is refused", refused)
    check("every row without a person is listed, by its own id", any("v4" in r for r in reasons) and any("v5" in r for r in reasons), str(reasons))
    after = (count("select count(*) as n from subject_lookup where tenant_id = %s", (CANARY,)),
             count("select count(*) as n from dataset_version where dataset_id = %s", (dataset,)))
    check("nothing was made or sealed for it: no new people, no version", before == after, f"{before} -> {after}")

    heading(f"{label}: sealing a table")
    rows = rows_for(f"E-{tag}", f"F-{tag}")
    sealed = person_keys.seal_table(service, dataset_id=dataset, identity=identity(ENGINEER, ["pipeline_operator"]), contract=good, rows=rows)
    version = sealed["id"]
    body, entry = stored_bytes(version)
    text = body.decode()
    check("the file holds no readable name or diagnosis", all(w not in text for w in ["Ada Example", "Ben Example", "asthma", "fracture"]))
    check("the identifier is not in the file either", f"E-{tag}" not in text and f"F-{tag}" not in text)
    check("the readable columns are readable", '"clinic": "north"' in text and '"age": 41' in text)
    check("the fingerprint in the version covers the stored bytes", hashlib.sha256(body).hexdigest() == entry["sha256"] and len(body) == entry["bytes"])
    file_rows = [json.loads(line) for line in text.splitlines()]
    check("both rows of the first person carry one person code, and the second person another",
          file_rows[0]["person_code"] == file_rows[1]["person_code"] != file_rows[2]["person_code"], str([r["person_code"] for r in file_rows]))
    check("the version records how many rows it holds", count("select record_count as n from dataset_version where id = %s", (version,)) == 3)
    code_e, code_f = file_rows[0]["person_code"], file_rows[2]["person_code"]
    check("the version records the contract it was sealed under",
          count("select count(*) as n from privacy_contract p join dataset_version v on v.schema_id = p.schema_id where v.id = %s", (version,)) == 1)

    heading(f"{label}: reading through the platform")
    decisions_before = count("select count(*) as n from access_decision where principal = %s and purpose = 'read locked columns'", (CUSTODIAN,))
    read = person_keys.read_rows(service, version_id=version, identity=identity(CUSTODIAN, ["data_custodian"]))
    by_id = {r["visit_id"]: r for r in read}
    check("a permitted reader gets the locked columns back", by_id["v1"]["patient_name"] == "Ada Example" and by_id["v3"]["diagnosis"] == "fracture", str(by_id["v1"]))
    check("and the identifier column holds the person code", by_id["v1"]["clinic_number"] == code_e)
    check("the decision is recorded", count("select count(*) as n from access_decision where principal = %s and purpose = 'read locked columns' and allowed", (CUSTODIAN,)) == decisions_before + 1)
    try:
        person_keys.read_rows(service, version_id=version, identity=identity(RESEARCHER, ["notebook_explore"]))
        refused_reader = False
    except person_keys.NotPermitted as exc:
        refused_reader = bool(exc.reasons)
    check("a reader who is not permitted is refused, with a reason", refused_reader)
    check("and that refusal is recorded", count("select count(*) as n from access_decision where principal = %s and purpose = 'read locked columns' and not allowed", (RESEARCHER,)) >= 1)

    heading(f"{label}: erasing one person")
    digest_before = hashlib.sha256(stored_bytes(version)[0]).hexdigest()
    out = person_keys.erase_person(service, CANARY, "clinic number", f"E-{tag}", identity(CUSTODIAN, ["data_custodian"]), "asked to be forgotten")
    check("the erasure reports the key destroyed", out["key_destroyed"] is True, str(out))
    again = stored_bytes(version)
    check("the sealed file is byte for byte what it was", hashlib.sha256(again[0]).hexdigest() == digest_before)
    check("re-hashing it still matches the fingerprint the version recorded", hashlib.sha256(again[0]).hexdigest() == again[1]["sha256"])
    read = {r["visit_id"]: r for r in person_keys.read_rows(service, version_id=version, identity=identity(CUSTODIAN, ["data_custodian"]))}
    erased = read["v1"]["patient_name"]
    check("the erased person's locked columns report that they were erased, with a date",
          isinstance(erased, dict) and "erased_on" in erased and read["v1"]["diagnosis"] == erased, str(erased))
    check("the erased person's readable columns still read", read["v1"]["clinic"] == "north" and read["v1"]["age"] == 41 and read["v2"]["clinic"] == "south")
    check("the other person is untouched", read["v3"]["patient_name"] == "Ben Example" and read["v3"]["diagnosis"] == "fracture")
    check("the table still has every row: erasure removed a key, not a row", len(read) == 3)
    check("looking the erased identifier up finds nobody", person_keys.find_person(service, CANARY, "clinic number", f"E-{tag}") is None)
    check("the other person is still found", person_keys.find_person(service, CANARY, "clinic number", f"F-{tag}") == code_f)
    check("a tombstone records that the person existed and was erased",
          count("select count(*) as n from tombstone where tenant_id = %s and record_id = %s", (CANARY, f"person:{code_e}")) == 1)
    status, when = service.status(CANARY, code_e)
    check("the key service reports the key destroyed, with the time", status == "destroyed" and when is not None, str(status))
    check("the other person's key is live", service.status(CANARY, code_f)[0] == "live")
    check("erasing the same person a second time changes nothing and says so",
          person_keys.erase_person(service, CANARY, "clinic number", f"E-{tag}", identity(CUSTODIAN, ["data_custodian"]), "again")["key_destroyed"] is False)


def reset_keys(tenant: str = CANARY) -> None:
    """No keys, no people and no organisation key for a test organisation. Keys made under another master key cannot be opened by this one,
    which is exactly right, so each section of this check begins from none. The sealed versions an earlier section left stay: only the
    keys that opened them go."""
    with db() as conn:
        for table in ("subject_lookup", "subject_key", "org_key"):
            conn.execute(f"delete from {table} where tenant_id = %s", (tenant,))


def reasons_of(response) -> str:
    try:
        detail = response.json()["detail"]
        return " ".join(detail["reasons"]) if isinstance(detail, dict) else str(detail)
    except Exception:  # noqa: BLE001
        return response.text[:160]


def run_api_section(person_keys) -> None:
    heading("Through the API, with the master key file the platform really uses")
    reset_keys()
    check("the platform's master key file is mounted", Path(person_keys.config.KEY_SERVICE_MASTER_FILE).is_file(), person_keys.config.KEY_SERVICE_MASTER_FILE)
    tag = uuid.uuid4().hex[:8]
    dataset = register_dataset(ENGINEER)
    engineer, custodian, researcher = bearer_for(ENGINEER), bearer_for(CUSTODIAN), bearer_for(RESEARCHER)
    good = good_contract()
    rows = rows_for(f"G-{tag}", f"H-{tag}")

    r = api("POST", f"/datasets/{dataset}/seal-table", headers=engineer, json={"contract": with_treatment(good, "patient_name", "lokc"), "rows": rows})
    check("a wrong contract is refused with 400, and the reasons name the column", r.status_code == 400 and "patient_name" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:90]}")
    r = api("POST", f"/datasets/{dataset}/seal-table", headers=engineer, json={"contract": good, "rows": rows + [{**rows[0], "visit_id": "v9", "clinic_number": ""}]})
    check("a row that names nobody is refused with 400, and the reasons name the row", r.status_code == 400 and "v9" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:90]}")
    r = api("POST", f"/datasets/{dataset}/seal-table", headers=bearer_for(RESEARCHER), json={"contract": good, "rows": rows})
    check("a researcher may not bring data in", r.status_code == 403, f"{r.status_code} {reasons_of(r)[:90]}")
    check("nothing was sealed by the refusals", count("select count(*) as n from dataset_version where dataset_id = %s", (dataset,)) == 0)

    r = api("POST", f"/datasets/{dataset}/seal-table", headers=engineer, json={"contract": good, "rows": rows})
    check("a data engineer seals the table", r.status_code == 201 and r.json()["records"] == 3 and r.json()["people"] == 2, f"{r.status_code} {reasons_of(r)[:90]}")
    version = r.json()["id"]

    r = api("GET", f"/dataset-versions/{version}/person-rows", headers=custodian)
    got = {x["visit_id"]: x for x in r.json()["rows"]} if r.status_code == 200 else {}
    check("a data custodian reads the rows with the locked columns opened", r.status_code == 200 and got["v1"]["patient_name"] == "Ada Example", f"{r.status_code} {reasons_of(r)[:90]}")
    r = api("GET", f"/dataset-versions/{version}/person-rows", headers=researcher)
    check("a researcher is refused with 403 and a reason", r.status_code == 403 and "data custodian" in reasons_of(r), f"{r.status_code} {reasons_of(r)[:90]}")
    r = api("GET", f"/dataset-versions/{version}/person-rows", headers=engineer)
    check("so is a data engineer, who brings data in but does not read what was locked", r.status_code == 403, f"{r.status_code}")
    r = api("GET", f"/dataset-versions/{uuid.uuid4()}/person-rows", headers=custodian)
    check("a table that does not exist is not found", r.status_code == 404, str(r.status_code))

    out = person_keys.erase_person(person_keys.key_service(), CANARY, "clinic number", f"G-{tag}", identity(CUSTODIAN, ["data_custodian"]), "asked to be forgotten")
    r = api("GET", f"/dataset-versions/{version}/person-rows", headers=custodian)
    got = {x["visit_id"]: x for x in r.json()["rows"]}
    check("after erasure the API answers 200, with the erased person's locked columns marked erased",
          out["key_destroyed"] and r.status_code == 200 and "erased_on" in got["v1"]["patient_name"] and got["v3"]["patient_name"] == "Ben Example")
    reset_keys()


def run_hold_section(person_keys, service) -> None:
    heading("A legal hold stops an erasure")
    from lifecycle_fixture import ADMIN_A, ADMIN_B, drop_org, finish_org, hold_in_force, make_org
    org = make_org()
    try:
        tag = uuid.uuid4().hex[:8]
        code = person_keys.resolve_person(service, org.id, "clinic number", f"K-{tag}", ENGINEER)
        actor = {"id": org.people["custodian"], "kind": "human", "roles": ["data_custodian"], "tenant_id": org.id}
        hold = hold_in_force(org, bearer_for(ADMIN_A), bearer_for(ADMIN_B))
        try:
            person_keys.erase_person(service, org.id, "clinic number", f"K-{tag}", actor, "asked to be forgotten")
            held = False
        except person_keys.ErasureHeld:
            held = True
        check("the erasure is refused while a legal hold stands over the organisation", held)
        check("and nothing of it happened: the key is live, the person is still found, no tombstone",
              service.status(org.id, code)[0] == "live" and person_keys.find_person(service, org.id, "clinic number", f"K-{tag}") == code
              and count("select count(*) as n from tombstone where tenant_id = %s", (org.id,)) == 0)
        released = api("POST", f"/lifecycle/holds/{hold}/release", headers=bearer_for(ADMIN_B), json={"reason": "the matter is closed"})
        check("the hold is released", released.status_code == 200, f"{released.status_code} {reasons_of(released)[:90]}")
        out = person_keys.erase_person(service, org.id, "clinic number", f"K-{tag}", actor, "asked to be forgotten")
        check("with no hold standing, the same erasure goes ahead", out["key_destroyed"] is True and service.status(org.id, code)[0] == "destroyed", str(out))
        person_keys.resolve_person(service, org.id, "clinic number", f"L-{tag}", ENGINEER)
        finish_org(org, bearer_for(ADMIN_A), bearer_for(ADMIN_B))
        left = sum(count(f"select count(*) as n from {t} where tenant_id = %s", (org.id,)) for t in ("subject_key", "subject_lookup", "org_key", "key_use_log", "tombstone"))
        check("closing the organisation down deletes its keys, its people and its key log with everything else", left == 0, f"{left} rows left")
    finally:
        drop_org(org)


def main() -> int:
    require_api()
    from app import db as app_db
    if app_db.pool.closed:
        app_db.pool.open()
    fixture_tenant(CANARY)
    from app import key_store, person_keys, privacy_contract
    from crypto.keyservice import BuiltInKeyService, DestroyedKeyError, KeyService, UnknownRecordError

    run_api_section(person_keys)
    reset_keys()
    with tempfile.TemporaryDirectory() as tmp:
        master = Path(tmp) / "master_key"
        master.write_bytes(os.urandom(32))
        builtin = BuiltInKeyService.from_file(master, key_store.PostgresKeyStore())
        run_suite("Built-in key service", builtin, person_keys, privacy_contract)

        heading("Built-in key service: where the keys are kept")
        with db() as conn:
            row = conn.execute("select wrapped_key from subject_key where tenant_id = %s and destroyed_at is not null limit 1", (CANARY,)).fetchone()
            live = conn.execute("select wrapped_key from subject_key where tenant_id = %s and destroyed_at is null limit 1", (CANARY,)).fetchone()
        check("a destroyed person key holds no key material", row is not None and row["wrapped_key"] is None)
        check("a live person key is stored only wrapped", live is not None and live["wrapped_key"] is not None and len(live["wrapped_key"]) > 32)
        check("every use of a key is logged", count("select count(*) as n from key_use_log where tenant_id = %s and action = 'unwrap'", (CANARY,)) > 0
              and count("select count(*) as n from key_use_log where tenant_id = %s and action = 'destroy'", (CANARY,)) > 0)
        try:
            BuiltInKeyService.from_file(Path(tmp) / "missing", key_store.PostgresKeyStore())
            starts = True
        except RuntimeError:
            starts = False
        check("with no master key file the service refuses to start, rather than inventing a key", not starts)
        other = Path(tmp) / "other_master"
        other.write_bytes(os.urandom(32))
        wrong = BuiltInKeyService.from_file(other, key_store.PostgresKeyStore())
        with db() as conn:
            code = conn.execute("select person_code from subject_key where tenant_id = %s and destroyed_at is null limit 1", (CANARY,)).fetchone()["person_code"]
        try:
            wrong.unwrap(CANARY, code, ENGINEER, "check")
            opened = True
        except Exception:  # noqa: BLE001
            opened = False
        check("a different master key cannot open the keys", not opened)

        fake = build_fake(KeyService, DestroyedKeyError, UnknownRecordError)
        run_suite("Fake key service", fake, person_keys, privacy_contract)
        check("the fake service logged its own key uses, so the platform really went through it", len(fake.log) > 5, str(len(fake.log)))
        reset_keys()
        run_hold_section(person_keys, builtin)
        reset_keys()

    return summary("v128")


if __name__ == "__main__":
    sys.exit(main())
