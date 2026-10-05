"""U64: storage grants are a projection of the register, not an edit of a file.

The claim this exists for is the first one below: a lease that expires stops
opening the door. Before this slice the permissions document was edited in
place and nothing ever removed a grant, so an expired lease's access stayed
live indefinitely. Now the document is reprinted from Postgres, so a grant that
is no longer justified is simply not printed the next time.

Runs inside the munitas-api container, which is why it can both call the API
over HTTP and import `app.grants` to reconcile in process and to exercise the
guard directly.
"""

from __future__ import annotations

import concurrent.futures
import sys
import uuid

sys.path.insert(0, "/app")

from app import db as app_db  # noqa: E402
from app import grants as app_grants  # noqa: E402
from app import seaweed as app_seaweed  # noqa: E402

from common import (ADMIN, ENGINEER, api, bearer_for, bucket_for, check, db,  # noqa: E402
                    fixture_contract, fixture_department, fixture_tenant,
                    fixture_version, heading, require_api, s3_client, summary)

KEY = "part-0.json"
BODY = b'{"phi":"present"}'


def version_with_object(tenant: str, contract: str, klass: str) -> dict:
    """A sealed version with a real object under its prefix and an owning
    department, so a credential can be minted and a read actually attempted."""
    version = fixture_version(tenant, contract, klass)
    # This tenant's own bucket, created by the platform if this is its first
    # write. Writing the fixture anywhere else would put it where the
    # platform will never look.
    bucket = bucket_for(tenant)
    admin = s3_client(*ADMIN)
    try:
        admin.create_bucket(Bucket=bucket)
    except Exception:
        pass
    admin.put_object(Bucket=bucket, Key=f"{version['storage_prefix']}/{KEY}", Body=BODY)
    fixture_department(tenant, version["dataset_id"])
    return version


def ensure_principal(principal: str, tenant: str, roles: list[str], kind: str) -> None:
    role_literal = "{" + ",".join(roles) + "}"
    with db() as conn:
        conn.execute(
            """insert into directory (id, tenant_id, label, kind, roles)
                 values (%s, %s, %s, %s, %s)
               on conflict (id) do update set roles = excluded.roles""",
            (principal, tenant, principal, kind, role_literal),
        )


def approve_lease(tenant: str, principal: str, version_id: str, purpose: str,
                  custodian: str) -> str | None:
    """Request and approve a lease, returning its lease id, or None if either
    step failed (which the caller turns into a failed check)."""
    req = api("POST", "/leases/requests", json={
        "tenant_id": tenant,
        "principal": principal,
        "dataset_version_id": version_id,
        "purpose": purpose,
        "justification": "needed for a projection test",
        "ttl_hours": 4,
    }, headers=bearer_for(ENGINEER))
    if req.status_code != 201:
        return None
    approved = api("POST", f"/leases/requests/{req.json()['id']}/approve",
                   headers=bearer_for(custodian))
    if approved.status_code != 201:
        return None
    return approved.json()["lease_id"]


def mint(principal: str, roles: list[str], kind: str, tenant: str,
         version_id: str, purpose: str):
    return api("POST", "/credentials", json={
        "principal": principal,
        "principal_kind": kind,
        "roles": roles,
        "tenant_id": tenant,
        "dataset_version_id": version_id,
        "purpose": purpose,
    })


def can_read(creds: dict, prefix: str, bucket: str) -> tuple[bool, object]:
    client = s3_client(creds["access_key"], creds["secret_key"])
    try:
        obj = client.get_object(Bucket=bucket, Key=f"{prefix}/{KEY}")
        return obj["Body"].read() == BODY, 200
    except Exception as exc:
        code = getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
        return False, code


def read_settles(creds: dict, prefix: str, bucket: str, *, allowed: bool,
                 within: float = 10.0) -> tuple[bool, object, float]:
    """Read until the result is `allowed`, or until `within` seconds pass.

    SeaweedFS picks up a rewritten identity document asynchronously, so a
    read straight after a grant or a reconcile can still be answered from the
    document before it. Read once, that race decided the check: U64 failed
    once under a full suite and passed alone three times. The claim being
    tested is that the change takes effect, not that it takes effect within
    the same millisecond, so the read is repeated until the gateway agrees or
    the bound runs out. A change that never takes effect still fails, just
    `within` seconds later, and the time it took is reported either way.

    Returns (read succeeded, status, seconds until the result settled).
    """
    import time

    start = time.monotonic()
    while True:
        ok, code = can_read(creds, prefix, bucket)
        waited = round(time.monotonic() - start, 2)
        if ok is allowed or waited >= within:
            return ok, code, waited
        time.sleep(0.25)


def expire(lease_id: str) -> None:
    with db() as conn:
        conn.execute(
            "update access_lease set expires_at = now() - interval '1 hour' where id = %s",
            (lease_id,),
        )


def main() -> int:
    require_api()
    app_db.pool.open()
    tenant = fixture_tenant()
    contract = fixture_contract(tenant)

    # --- U64a: a lease that expires stops access ---------------------------
    heading("U64a: an expired lease stops opening the door")
    va = version_with_object(tenant, contract, "UNDER_REVIEW")
    bucket = bucket_for(tenant)
    custodian = fixture_department(tenant, va["dataset_id"])
    # A workload reader: a human may only request a lease for themselves, but may
    # request one on a workload's behalf, so this is what lets the script drive
    # the whole request/approve flow. The role is lease-only for an UNDER_REVIEW
    # version, which is the property the expiry test needs.
    reader_a = f"canary-u64a-{uuid.uuid4().hex[:6]}"
    ensure_principal(reader_a, tenant, ["training_job"], "workload")

    # Without a lease the role's floor does not cover an UNDER_REVIEW version,
    # so the whole test would be vacuous if this were granted.
    pre = mint(reader_a, ["training_job"], "workload", tenant, va["id"], "u64a")
    check("without a lease the version is above the role's floor",
          pre.status_code == 403, f"HTTP {pre.status_code}")

    lease_a = approve_lease(tenant, reader_a, va["id"], "u64a", custodian)
    check("a lease can be approved for the reader", lease_a is not None)

    granted = mint(reader_a, ["training_job"], "workload", tenant, va["id"], "u64a")
    check("the lease lets a credential be minted", granted.status_code == 200,
          f"HTTP {granted.status_code} {granted.text[:100]}")
    creds_a = granted.json() if granted.status_code == 200 else {}

    ok, code, waited = read_settles(creds_a, va["storage_prefix"], bucket, allowed=True) if creds_a else (False, "no creds", 0)
    check("with the live lease the object reads", ok, f"read result {code} after {waited}s")

    expire(lease_a)
    app_grants.reconcile()
    ok, code, waited = read_settles(creds_a, va["storage_prefix"], bucket, allowed=False) if creds_a else (False, "no creds", 0)
    check("after the lease expires and the document is reprinted, the read is refused",
          ok is False and code in (401, 403), f"read result {code} after {waited}s")

    # --- U64b: a live lease survives the print that kills an expired one ----
    heading("U64b: reprinting for one expiry does not touch a live lease")
    vb = version_with_object(tenant, contract, "UNDER_REVIEW")
    custodian_b = fixture_department(tenant, vb["dataset_id"])
    # Two workload readers holding different roles, so their credentials are
    # different keys. The expired one uses training_job, which is lease-only for
    # this class, so its access can only come from the lease and must vanish when
    # the lease does; the live one uses annotation_tool and keeps reading.
    # annotation_tool rather than agent_runtime because the latter mints only
    # inside an agent run's own scope, which is the U54 boundary, not a lease.
    reader_exp = f"canary-u64b-exp-{uuid.uuid4().hex[:6]}"
    reader_live = f"canary-u64b-live-{uuid.uuid4().hex[:6]}"
    ensure_principal(reader_exp, tenant, ["training_job"], "workload")
    ensure_principal(reader_live, tenant, ["annotation_tool"], "workload")

    lease_exp = approve_lease(tenant, reader_exp, vb["id"], "u64b-exp", custodian_b)
    lease_live = approve_lease(tenant, reader_live, vb["id"], "u64b-live", custodian_b)
    check("two leases on one version, different roles", bool(lease_exp) and bool(lease_live))

    cred_exp = mint(reader_exp, ["training_job"], "workload", tenant, vb["id"], "u64b-exp")
    cred_live = mint(reader_live, ["annotation_tool"], "workload", tenant, vb["id"], "u64b-live")
    creds_exp = cred_exp.json() if cred_exp.status_code == 200 else {}
    creds_live = cred_live.json() if cred_live.status_code == 200 else {}
    check("both leases mint a credential",
          cred_exp.status_code == 200 and cred_live.status_code == 200,
          f"exp {cred_exp.status_code}, live {cred_live.status_code}")

    expire(lease_exp)
    app_grants.reconcile()
    ok_exp, code_exp, waited_exp = read_settles(creds_exp, vb["storage_prefix"], bucket, allowed=False) if creds_exp else (False, "no creds", 0)
    ok_live, code_live, _ = read_settles(creds_live, vb["storage_prefix"], bucket, allowed=True) if creds_live else (False, "no creds", 0)
    check("the expired holder is refused", ok_exp is False and code_exp in (401, 403),
          f"read result {code_exp} after {waited_exp}s")
    check("the live holder still reads", ok_live is True, f"read result {code_live}")

    # --- U64c: two grants at once both survive -----------------------------
    heading("U64c: two grants issued at once do not lose each other")
    readers_c = []
    versions_c = []
    for _ in range(2):
        v = version_with_object(tenant, contract, "UNDER_REVIEW")
        api("POST", f"/dataset-versions/{v['id']}/promote", json={
            "to_class": "PUBLISHED", "decided_by": "verify-suite", "decided_by_kind": "workload",
            "gate_evidence": {"note": "fixture for the concurrency check"},
            "grant_roles": ["notebook_explore"],
        })
        reader = f"canary-u64c-{uuid.uuid4().hex[:6]}"
        ensure_principal(reader, tenant, ["notebook_explore"], "human")
        readers_c.append(reader)
        versions_c.append(v)

    def fire(i):
        return mint(readers_c[i], ["notebook_explore"], "human", tenant,
                    versions_c[i]["id"], "u64c").status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(fire, range(2)))
    check("both concurrent grants return 200", codes == [200, 200], str(codes))

    doc = app_seaweed.load_identities()
    nb = next((i for i in doc["identities"] if i["name"] == app_grants.tenant_identity_name("notebook_explore", tenant)), {})
    nb_actions = set(nb.get("actions", []))
    present = sum(
        1 for v in versions_c if f"Read:{bucket}/{v['storage_prefix']}/*" in nb_actions
    )
    check("both prefixes survive in the document, so neither write lost the other",
          present == 2, f"{present} of 2 prefixes present")

    # --- U64d: the guard refuses an empty document -------------------------
    heading("U64d: the guard refuses a document that would grant nothing")
    before = app_seaweed.load_identities()
    original = app_grants.desired_document
    app_grants.desired_document = lambda: {"identities": []}
    raised = False
    try:
        app_grants.reconcile()
    except app_grants.UnsafeProjection:
        raised = True
    finally:
        app_grants.desired_document = original
    after = app_seaweed.load_identities()
    check("an empty projection raises UnsafeProjection", raised)
    check("and nothing was written", before == after)

    # --- U64e: structural grants survive -----------------------------------
    heading("U64e: bucket-wide and admin grants are never dropped")
    app_grants.reconcile()
    doc = app_seaweed.load_identities()
    by_name = {i["name"]: set(i.get("actions", [])) for i in doc["identities"]}
    buckets = app_grants._buckets()
    owned = app_grants._tenant_buckets()
    # The pipeline no longer holds one key that opens every bucket. Each organisation has a key of its own, and it opens that
    # organisation's bucket and no other (the policy's `scope: own_tenant`).
    check("there is no key shared by every organisation: no identity is named for the bare pipeline role",
          "pipeline_action" not in by_name, sorted(n for n in by_name if n.startswith("pipeline_action")))
    pipelines = {n: a for n, a in by_name.items() if n.startswith("pipeline_action~")}
    check("every organisation with a bucket has a pipeline key of its own",
          set(pipelines) == {f"pipeline_action~{t}" for t in owned}, f"{len(pipelines)} keys, {len(owned)} organisations")
    pipeline = set().union(*pipelines.values()) if pipelines else set()
    # No bucket-wide verb at all, and no folder either: what a pipeline task may read or write is in the key of that task, which is made for
    # it and ends with it (U114, U116, U117). The role's own key opens nothing by itself.
    wide = sorted(a for a in pipeline if "/" not in a)
    check("the pipeline's keys hold no bucket-wide verb: they open nothing by themselves", not wide, str(wide[:3]))
    folders = sorted(a for a in pipeline if "/" in a)
    check("and no folder either: what a task may read or write is in the task's own key", not folders, str(folders[:2]))
    crossing = [(n, b) for n, a in pipelines.items() for t, bs in owned.items() if n != f"pipeline_action~{t}"
                for b in bs if any(x.endswith(f":{b}") or f":{b}/" in x for x in a)]
    check("and no organisation's pipeline key reaches another organisation's bucket, whole or in part",
          not crossing, f"{len(crossing)} crossings, first {crossing[:1]}")
    no_standing_write = not any(a.startswith("Write:") and "/" not in a for a in pipeline)
    check("and holds no standing, bucket-wide Write anywhere",
          no_standing_write, sorted(a for a in pipeline if a.startswith("Write:") and "/" not in a))
    admin = by_name.get("munitas-admin", set())
    check("munitas-admin keeps its five unscoped actions",
          admin == {"Admin", "List", "Read", "Tagging", "Write"}, str(sorted(admin)))

    # --- U64f: printing twice changes nothing ------------------------------
    heading("U64f: a second print of an unchanged register removes nothing")
    app_grants.reconcile()
    second = app_grants.reconcile()
    check("the second reconcile removes 0", second["removed"] == 0, str(second))

    # --- U64g: one holder is cut without cutting the other -----------------
    heading("U64g: cutting one holder leaves the other reading")
    vg = version_with_object(tenant, contract, "UNDER_REVIEW")
    custodian_g = fixture_department(tenant, vg["dataset_id"])
    reader_1 = f"canary-u64g-1-{uuid.uuid4().hex[:6]}"
    reader_2 = f"canary-u64g-2-{uuid.uuid4().hex[:6]}"
    ensure_principal(reader_1, tenant, ["training_job"], "workload")
    ensure_principal(reader_2, tenant, ["training_job"], "workload")

    lease_1 = approve_lease(tenant, reader_1, vg["id"], "u64g-1", custodian_g)
    lease_2 = approve_lease(tenant, reader_2, vg["id"], "u64g-2", custodian_g)
    check("two holders of one role, each with their own lease",
          bool(lease_1) and bool(lease_2))

    c1 = mint(reader_1, ["training_job"], "workload", tenant, vg["id"], "u64g-1")
    c2 = mint(reader_2, ["training_job"], "workload", tenant, vg["id"], "u64g-2")
    creds_1 = c1.json() if c1.status_code == 200 else {}
    creds_2 = c2.json() if c2.status_code == 200 else {}
    key_1, key_2 = creds_1.get("access_key"), creds_2.get("access_key")
    check("each holder got its own distinct key, not the shared role card",
          bool(key_1) and bool(key_2) and key_1 != key_2, f"{key_1} vs {key_2}")

    r1, _, _ = read_settles(creds_1, vg["storage_prefix"], bucket, allowed=True) if creds_1 else (False, None, 0)
    r2, _, _ = read_settles(creds_2, vg["storage_prefix"], bucket, allowed=True) if creds_2 else (False, None, 0)
    check("both holders read at first", r1 is True and r2 is True)

    expire(lease_1)
    app_grants.reconcile()
    r1, code1, waited_1 = read_settles(creds_1, vg["storage_prefix"], bucket, allowed=False) if creds_1 else (False, None, 0)
    r2, code2, _ = read_settles(creds_2, vg["storage_prefix"], bucket, allowed=True) if creds_2 else (False, None, 0)
    check("the holder whose lease expired is refused", r1 is False and code1 in (401, 403),
          f"key {key_1} read result {code1} after {waited_1}s")
    check("the other holder still reads", r2 is True, f"key {key_2} read result {code2}")

    # --- U64h: the document is compiled, never inherited -------------------
    heading("U64h: the document is compiled from the policy and the register, never read back")

    # A role's standing access used to be copied from its access to the
    # buckets it already had, so the rule lived only in the previous
    # document, and a fresh install depended on a template grant naming a
    # bucket that no longer exists. Now nothing is read back: if reading the
    # live document were an input, this compile would fail.
    compiled = app_grants.desired_document()
    real_load = app_seaweed.load_identities

    def refuse_to_read():
        raise AssertionError("the compiler read the live document")

    app_seaweed.load_identities = refuse_to_read
    try:
        blind = app_grants.desired_document()
        read_back = False
    except AssertionError:
        read_back = True
    finally:
        app_seaweed.load_identities = real_load
    check("compiling never reads the live document", not read_back)

    def shape(doc):
        return {i["name"]: (i["credentials"], sorted(i["actions"])) for i in doc["identities"]}

    check("and gives the same document without it, which is the fresh-install path",
          not read_back and shape(blind) == shape(compiled))

    # A bucket provisioned now is closed to the organisation's pipeline key from the first moment: the key is in the document at once, from
    # the policy rather than from a copy, and it opens nothing, because no role holds standing access to a bucket. Probed with a list,
    # which is what the pipeline used to be able to do on every bucket.
    fresh = fixture_tenant(f"storage-probe-{uuid.uuid4().hex[:8]}")
    fresh_bucket = app_seaweed.bucket(fresh)
    pipe_key, pipe_secret = app_grants.tenant_role_key("pipeline_action", fresh)
    pipe = s3_client(pipe_key, pipe_secret)
    import time
    present = False
    for _ in range(40):
        live_names = {i["name"] for i in app_seaweed.load_identities().get("identities", [])}
        if f"pipeline_action~{fresh}" in live_names:
            present = True
            break
        time.sleep(0.25)
    time.sleep(1)
    try:
        pipe.list_objects_v2(Bucket=fresh_bucket, MaxKeys=1)
        listed = "allowed"
    except Exception as exc:  # noqa: BLE001
        listed = getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)
    check("a bucket provisioned mid-run has its organisation's pipeline key in the document at once, and the key opens nothing",
          present and listed == "AccessDenied", f"present={present} list={listed}")

    # And the write side of the same claim: the organisation's pipeline key
    # alone still cannot write to a bucket it was never issued a write_grant
    # prefix in, freshly provisioned or not.
    denied = None
    try:
        pipe.put_object(Bucket=fresh_bucket, Key="u64h/probe.txt", Body=b"should be refused")
        denied = False
    except Exception as exc:  # noqa: BLE001 - the refusal itself is the assertion
        denied = "AccessDenied" in str(exc) or "403" in str(exc)
    check("but the same key still cannot write with no write_grant",
          denied is True, f"denied={denied!r}")

    # Writers that overlap cannot leave the document wrong: several reconciles
    # and grants at once, and afterwards the live document is exactly what
    # the register compiles to.
    def one_reconcile(_):
        app_grants.reconcile()
        return True

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(one_reconcile, range(6)))
    check("six reconciles at once all finish", results == [True] * 6)
    check("and the live document equals the compiled one exactly",
          shape(app_seaweed.load_identities()) == shape(app_grants.desired_document()))

    return summary("U64")


if __name__ == "__main__":
    sys.exit(main())
