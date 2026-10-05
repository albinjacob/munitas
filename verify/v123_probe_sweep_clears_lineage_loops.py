"""U123: the sweep that clears disposable test tenants removes one whose pipeline run and sealed versions refer to each other, and one that cannot be removed does not stop the others.

A pipeline run starts from a sealed version, an action run belongs to the run, and a second version names the action run that produced it.
No row loops, but the tables do, so no one-table delete can clear them. The fallback that clears such a loop counted what it deleted with
`delete ... returning`, which PostgreSQL refuses on `dataset_version` for a disposable tenant (its immutability rule is conditional), so the
sweep failed on exactly the test tenants a pipeline run leaves, and one failure stopped every tenant after it. This checks, one item at a time:

  * a disposable tenant with that loop, a sealed version, and a bucket holding a file is removed by the sweep's own delete, rows and bucket;
  * the counts it reports include the three tables that formed the loop;
  * a sweep over two tenants, the first made to fail, removes the second, reports the first with its reason, and leaves the first in place;
  * a tenant that is not disposable is refused by the same delete, not removed.

It uses only tenants of its own (the sweep's selection is narrowed to them), so it can run beside anything else. Host only, because it loads
`scripts/admin/tidy-probes.py` and needs storage reachable on localhost:

    .venv\\Scripts\\python.exe verify\\v123_probe_sweep_clears_lineage_loops.py
"""

from __future__ import annotations

import importlib.util
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "verify"))
sys.path.insert(0, str(ROOT))

from ports_config import PORTS  # noqa: E402

os.environ.setdefault("PG_DSN", f"postgresql://munitas:munitas@localhost:{PORTS['postgres']}/platform")
os.environ.setdefault("S3_ENDPOINT", f"http://localhost:{PORTS['seaweedfs_s3']}")

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from common import api, check, db, fixture_contract, fixture_tenant, fixture_version, heading, require_api, summary  # noqa: E402

spec = importlib.util.spec_from_file_location("tidy_probes", ROOT / "scripts" / "admin" / "tidy-probes.py")
tp = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = tp
spec.loader.exec_module(tp)


def make_probe(tag: str, sink: list, *, loop: bool, bucket: bool = True) -> dict:
    """A disposable tenant (the `scratch-probe-` prefix declares it so, while it is empty), with a sealed version, and optionally the
    lineage loop and a bucket holding a file. It is added to `sink` the moment it exists, so cleanup knows it even if a later step fails."""
    tenant = fixture_tenant(f"scratch-probe-{tag}-{uuid.uuid4().hex[:8]}")
    made = {"tenant": tenant, "bucket": None}
    sink.append(made)
    contract = fixture_contract(tenant)
    sealed = fixture_version(tenant, contract, "RAW")
    if loop:
        run_id, action_run_id, action_id = (str(uuid.uuid4()) for _ in range(3))
        person = f"{tenant}-person"
        with db() as conn:
            conn.execute("insert into directory (id, tenant_id, label, kind, roles) values (%s, %s, 'U123 person', 'human', '{}')", (person, tenant))
            conn.execute("insert into dataset_action (id, tenant_id, name) values (%s, %s, 'u123-loop')", (action_id, tenant))
            conn.execute(
                "insert into pipeline_run (id, tenant_id, dataset, workflow_id, triggered_by, source_version_id, started_from) "
                "values (%s, %s, %s, %s, %s, %s, 'console')", (run_id, tenant, sealed["dataset_name"], f"u123-{run_id}", person, sealed["id"]))
            conn.execute(
                "insert into action_run (id, tenant_id, action_id, code_hash, image_digest, status, operator, idempotency_key, pipeline_run_id, "
                "triggered_by) values (%s, %s, %s, 'verify', 'verify', 'succeeded', 'verify', %s, %s, %s)",
                (action_run_id, tenant, action_id, f"u123-{action_run_id}", run_id, person))
        produced = api("POST", "/dataset-versions", json={
            "tenant_id": tenant, "dataset_id": sealed["dataset_id"], "schema_id": contract, "visibility_class": "RAW",
            "object_manifest": [{"key": "part-0.json", "bytes": 8}], "record_count": 1, "produced_by_run": action_run_id})
        made["produced_ok"] = produced.status_code in (200, 201)
        made["detail"] = f"HTTP {produced.status_code} {produced.text[:120]}"
    if bucket:
        client = tp.s3()
        name = f"munitas-{tenant}"
        client.create_bucket(Bucket=name)
        client.put_object(Bucket=name, Key=f"{tenant}/probe.txt", Body=b"left behind?")
        with db() as conn:
            conn.execute("insert into tenant_storage_provision (tenant_id, backend, bucket) values (%s, 'seaweedfs', %s)", (tenant, name))
        made["bucket"] = name
    return made


def rows_left(tenant: str) -> dict:
    with db() as conn:
        return {t: conn.execute(f'select count(*) as n from "{t}" where tenant_id = %s', (tenant,)).fetchone()["n"]
                for t in ("dataset_version", "pipeline_run", "action_run", "dataset", "schema_contract")}


def tenant_exists(tenant: str) -> bool:
    with db() as conn:
        return bool(conn.execute("select 1 from tenant where id = %s", (tenant,)).fetchone())


def bucket_exists(name: str) -> bool:
    return tp.nuke.bucket_keys(tp.s3(), name) is not None


def remove_probe(m: dict) -> None:
    """Remove a disposable probe through the sweep's own functions."""
    with psycopg.connect(os.environ["PG_DSN"], row_factory=dict_row) as conn:
        if m["bucket"]:
            tp.empty_bucket(tp.s3(), m["bucket"])
        tp.delete_tenant(conn, m["tenant"])
        conn.commit()


def main() -> int:
    require_api()
    mine: list[dict] = []
    kept: list[str] = []
    real_delete, real_probe_tenants = tp.delete_tenant, tp.probe_tenants
    try:
        heading("A disposable tenant whose lineage loops back on itself is removed")
        a = make_probe("loop", mine, loop=True)
        check("the loop was built: a version was produced by the run", a.get("produced_ok") is True, a.get("detail", ""))
        before = rows_left(a["tenant"])
        check("it holds a sealed version, a pipeline run and an action run",
              before["dataset_version"] >= 2 and before["pipeline_run"] == 1 and before["action_run"] == 1, str(before))
        check("and a bucket with a file in it", bucket_exists(a["bucket"]))

        with psycopg.connect(os.environ["PG_DSN"], row_factory=dict_row) as conn:
            tp.empty_bucket(tp.s3(), a["bucket"])
            deleted = tp.delete_tenant(conn, a["tenant"])
            conn.commit()
        check("the sweep's own delete removed the tenant", not tenant_exists(a["tenant"]))
        check("and every row of it, including the sealed versions", all(v == 0 for v in rows_left(a["tenant"]).values()), str(rows_left(a["tenant"])))
        check("and its bucket", not bucket_exists(a["bucket"]))
        check("the counts it reports include the three tables that formed the loop",
              all(deleted.get(t, 0) >= 1 for t in ("dataset_version", "pipeline_run", "action_run")), str(deleted))

        heading("One tenant that cannot be removed does not stop the next")
        bad = make_probe("bad", mine, loop=False, bucket=False)
        good = make_probe("good", mine, loop=True)
        chosen = [bad["tenant"], good["tenant"]]

        def only_mine(conn, min_age_hours=0):
            rows = conn.execute(
                "select t.id, t.purpose, t.created_at, (select count(*) from dataset_version dv where dv.tenant_id = t.id) as versions "
                "from tenant t where t.id = any(%s)", (chosen,)).fetchall()
            return sorted(rows, key=lambda r: chosen.index(r["id"]))  # the failing one first

        def fails_for_bad(conn, tenant):
            if tenant == bad["tenant"]:
                raise RuntimeError("made-up failure for this check")
            return real_delete(conn, tenant)

        tp.probe_tenants, tp.delete_tenant = only_mine, fails_for_bad
        try:
            result = tp.run_sweep(min_age_hours=0, apply=True)
        finally:
            tp.probe_tenants, tp.delete_tenant = real_probe_tenants, real_delete
        check("the sweep finished instead of stopping at the failure", isinstance(result, dict), str(type(result)))
        check("the tenant after the failing one was removed", good["tenant"] in result["removed"] and not tenant_exists(good["tenant"]),
              str(result["removed"]))
        check("the failing tenant is reported, with its reason",
              [f["id"] for f in result["failed"]] == [bad["tenant"]] and "made-up failure" in result["failed"][0]["reason"], str(result["failed"]))
        check("and is left in place for the next run", tenant_exists(bad["tenant"]))

        heading("A tenant that is not disposable is refused")
        # Empty, and named so the helper does not declare it disposable (it is `canary`), so it can be removed by hand afterwards.
        keep = fixture_tenant(f"u123-keep-{uuid.uuid4().hex[:8]}")
        kept.append(keep)
        refused = None
        try:
            with psycopg.connect(os.environ["PG_DSN"], row_factory=dict_row) as conn:
                tp.delete_tenant(conn, keep)
        except RuntimeError as exc:
            refused = str(exc)
        check("the same delete refuses a tenant whose purpose is not scratch", bool(refused) and "refusing to delete" in refused, str(refused))
        check("and it is still there", tenant_exists(keep))
    finally:
        tp.probe_tenants, tp.delete_tenant = real_probe_tenants, real_delete
        # Whatever this run made and left, by the names it generated: the disposable ones through the sweep's own delete, the empty
        # non-disposable one by removing its row (nothing refers to it).
        for m in mine:
            if tenant_exists(m["tenant"]):
                remove_probe(m)
        for tenant in kept:
            with db() as conn:
                conn.execute("delete from tenant where id = %s", (tenant,))
    return summary("U123")


if __name__ == "__main__":
    sys.exit(main())
