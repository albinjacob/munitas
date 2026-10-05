"""Hard-delete datasets from the canary tenant, and from nothing else. The library behind tidy-canary.py.

Why this exists
---------------
The canary tenant is where the verification suite writes its fixtures (infra/postgres/seed-canary.sql). A sealed version cannot be
deleted through the platform, which is the guarantee check V1 proves, so those fixtures accumulate: the rows are small, but they show
up in screens, and a custodian's queue that holds the oldest 100 arrivals stops showing new ones once the suite has left 100 behind.
Freeing the files (reclaim-storage.py) does not remove the rows.

This removes the rows and the files of canary datasets, as test-harness housekeeping. It is not platform behaviour: nothing in the API
calls it, and the immutability it steps around is proven elsewhere, on fixtures of its own.

It is hardened to work on the canary tenant and nothing else, and it checks that at every point rather than once. Each guard below is a
separate function so that a check (verify/v119_canary_tidy_is_canary_only.py) can prove it refuses:

  G1  the tenant is a constant in this file. No function takes a tenant from a caller, an argument or the environment.
  G2  the tenant row in the database has the id `canary` and the purpose `canary`.
  G3  the storage binding for it is SeaweedFS and the bucket is exactly `munitas-canary`.
  G4  every dataset handed in is read back from the database, and every one belongs to canary, or nothing happens.
  G5  inside the transaction, before anything is deleted, the same check is made again on rows locked for update.
  G6  every delete statement is scoped to canary in its own SQL wherever the table has a tenant column, as well as by id.
  G7  before the commit, the number of rows each OTHER tenant has in the tables this touches is compared with what it was
      before. Any difference rolls the whole transaction back.
  G8  every stored object is checked against canary's prefix and bucket before it is deleted.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

# G1. The one tenant this can ever touch. Not read from an argument, a file or the environment.
TENANT = "canary"
PURPOSE = "canary"
BUCKET = "munitas-canary"
PREFIX_ROOT = f"{TENANT}/"

PG_DSN = os.environ.get("PG_DSN", "postgresql://munitas:munitas@localhost:5432/platform")
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "http://localhost:8333")
ADMIN_KEY = os.environ.get("S3_ADMIN_KEY", "munitas-admin")
ADMIN_SECRET = os.environ.get("S3_ADMIN_SECRET", "munitas-admin-secret")

PROTECTED_RULES = [("dataset_version", "dataset_version_no_update"), ("dataset_version", "dataset_version_no_delete")]

# The tables a purge touches, for the comparison in G7. Every one of them is counted per tenant before and after.
WATCHED = ("dataset", "dataset_version", "action_run", "access_lease", "lease_request", "agent_run", "pipeline_run",
           "derivation", "gate_decision", "access_decision", "storage_identity", "task_read_grant", "class_transition",
           "storage_reclamation", "catalog_key", "table_job", "huggingface_fetch_job", "dataset_source", "iceberg_table_ref",
           "write_grant", "agent", "agent_version", "agent_deployment", "agent_egress_approval", "directory")


class NotCanary(Exception):
    """A guard refused. Nothing was changed."""


# ---------------------------------------------------------------------------------------------------------------- the guards


def check_tenant_row(row: dict | None) -> None:
    """G2."""
    if not row:
        raise NotCanary(f"G2: there is no tenant {TENANT!r}")
    if row["id"] != TENANT or row["purpose"] != PURPOSE:
        raise NotCanary(f"G2: the tenant is {row['id']!r} with purpose {row['purpose']!r}; only {TENANT!r} with purpose {PURPOSE!r} is allowed")


def check_binding(row: dict | None) -> None:
    """G3."""
    if not row:
        raise NotCanary("G3: the canary tenant has no SeaweedFS storage binding")
    if row["tenant_id"] != TENANT or row["backend"] != "seaweedfs" or row["bucket"] != BUCKET:
        raise NotCanary(f"G3: the storage binding is {row['tenant_id']!r} / {row['backend']!r} / {row['bucket']!r}; "
                        f"only {TENANT!r} / 'seaweedfs' / {BUCKET!r} is allowed")


def check_datasets_are_canary(asked: list[str], found: list[dict]) -> None:
    """G4 and G5. Every id asked for was found, and every one found belongs to canary."""
    foreign = [str(d["id"]) for d in found if d["tenant_id"] != TENANT]
    if foreign:
        raise NotCanary(f"G4/G5: {len(foreign)} dataset(s) are not canary's, first {foreign[0]}. Nothing was changed")
    if {str(d["id"]) for d in found} != set(asked):
        raise NotCanary("G4/G5: some datasets asked for do not exist, or are not in the canary tenant. Nothing was changed")


def check_snapshot(before: dict, after: dict) -> None:
    """G7. No tenant but canary has a different number of rows in any watched table."""
    for key in sorted(set(before) | set(after)):
        tenant, table = key
        if tenant != TENANT and before.get(key, 0) != after.get(key, 0):
            raise NotCanary(f"G7: tenant {tenant!r} has {after.get(key, 0)} row(s) in {table!r} where it had {before.get(key, 0)}. "
                            "Rolling back; nothing was changed")


def check_keys(keys: list[str], dataset_ids: list[str], bucket: str) -> None:
    """G8. Every object about to be deleted is in canary's bucket, under canary/<one of the verified datasets>/."""
    if bucket != BUCKET:
        raise NotCanary(f"G8: the bucket is {bucket!r}, not {BUCKET!r}")
    allowed = tuple(f"{PREFIX_ROOT}{d}/" for d in dataset_ids)
    stray = [k for k in keys if not k.startswith(allowed)]
    if stray:
        raise NotCanary(f"G8: {len(stray)} object(s) are not under a verified canary dataset, first {stray[0]!r}. No object was deleted")


# ------------------------------------------------------------------------------------------------------------------ the work


@dataclass
class Plan:
    datasets: list[dict]
    versions: list[dict]
    guards: list[str] = field(default_factory=list)

    @property
    def dataset_ids(self) -> list[str]:
        return [str(d["id"]) for d in self.datasets]

    @property
    def version_ids(self) -> list[str]:
        return [str(v["id"]) for v in self.versions]


def connect() -> psycopg.Connection:
    return psycopg.connect(PG_DSN, row_factory=dict_row)


def s3():
    import boto3
    from botocore.config import Config

    return boto3.client("s3", endpoint_url=S3_ENDPOINT, aws_access_key_id=ADMIN_KEY, aws_secret_access_key=ADMIN_SECRET,
                        region_name="us-east-1", config=Config(s3={"addressing_style": "path"}))


def verify_canary(conn) -> list[str]:
    """G2 and G3 against the live database. Returns the guards that passed, for the log."""
    check_tenant_row(conn.execute("select id, purpose from tenant where id = %s", (TENANT,)).fetchone())
    check_binding(conn.execute("select tenant_id, backend, bucket from tenant_storage_provision where tenant_id = %s and backend = 'seaweedfs'",
                               (TENANT,)).fetchone())
    return [f"G1 the tenant is the constant {TENANT!r}", f"G2 the tenant row is {TENANT!r} with purpose {PURPOSE!r}",
            f"G3 storage is SeaweedFS bucket {BUCKET!r}"]


# Datasets that cannot be deleted without each other. A pipeline run started from a dataset has its outputs in other datasets, the
# action runs of that run point back at it, and the versions point at those action runs; deleting only some of them is refused by the
# database. Each query returns pairs of canary datasets that go together.
LINKS = (
    """select src.dataset_id::text as a, out.dataset_id::text as b
         from pipeline_run p join dataset_version src on src.id = p.source_version_id
         join action_run ar on ar.pipeline_run_id = p.id join dataset_version out on out.id = ar.output_version""",
    """select src.dataset_id::text as a, gv.dataset_id::text as b
         from pipeline_run p join dataset_version src on src.id = p.source_version_id
         join gate_decision g on g.pipeline_run_id = p.id join dataset_version gv on gv.id = g.dataset_version_id""",
    """select pv.dataset_id::text as a, ov.dataset_id::text as b
         from dataset_version pv join action_run ar on ar.id = pv.produced_by_run join dataset_version ov on ov.id = ar.output_version""",
    """select lv.dataset_id::text as a, av.dataset_id::text as b
         from agent_run a join lease_request lr on lr.id = a.lease_request_id
         join dataset_version lv on lv.id = lr.dataset_version_id join dataset_version av on av.id = a.dataset_version_id""",
)


def group_old(created: dict[str, object], edges: list[tuple[str, str]], cutoff) -> list[list[str]]:
    """Connected groups of canary datasets in which EVERY member is older than the cutoff, oldest first.

    `created` maps each canary dataset id to when it was made; only ids in it can be grouped, so a link to anything that is not a canary
    dataset is ignored here and can never pull one in. A group with even one member newer than the cutoff is left whole, because
    deleting its old members alone would be refused (or would take young data with it).
    """
    parent = {d: d for d in created}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        if a in parent and b in parent:
            parent[find(a)] = find(b)
    groups: dict[str, list[str]] = {}
    for d in created:
        groups.setdefault(find(d), []).append(d)
    whole = [g for g in groups.values() if all(created[d] < cutoff for d in g)]
    whole.sort(key=lambda g: min(created[d] for d in g))
    return whole


KEEP_FREED = 3


def mark_kept(created: dict[str, object], kept: set[str], cutoff) -> dict[str, object]:
    """The same map with the kept datasets dated as newer than the cutoff, so neither they nor anything that goes with them is selected."""
    from datetime import timedelta

    return {d: (cutoff + timedelta(hours=1) if d in kept else when) for d, when in created.items()}


def keep_for_the_freed_files_screen(conn) -> set[str]:
    """The canary datasets to leave alone so the console's "the files were freed" screen always has a version to look at (check U32).

    Freeing a version's files happens a day after it is made (reclaim-storage.py), but this tidy deletes after hours, so without this no
    version would ever live long enough to be freed and that test would skip for good. Kept: the three datasets whose files were freed
    most recently, and the oldest dataset that has a sealed version and has not been freed yet, which is left to age until it is.
    """
    freed = conn.execute(
        """select v.dataset_id from storage_reclamation r join dataset_version v on v.id = r.dataset_version_id
            where r.tenant_id = %s group by v.dataset_id order by max(r.at) desc limit %s""", (TENANT, KEEP_FREED)).fetchall()
    waiting = conn.execute(
        """select d.id as dataset_id from dataset d
            where d.tenant_id = %s and exists (select 1 from dataset_version v where v.dataset_id = d.id)
              and not exists (select 1 from dataset_version v join storage_reclamation r on r.dataset_version_id = v.id where v.dataset_id = d.id)
            order by d.created_at limit 1""", (TENANT,)).fetchall()
    return {str(r["dataset_id"]) for r in freed + waiting}


def select_old(conn, older_than_hours: float, limit: int | None) -> list[list[str]]:
    """Groups of canary datasets, each wholly older than that many hours, oldest first, up to `limit` datasets (a group is never split).

    Selection is itself scoped to canary; G4 re-checks every id again before anything is deleted.
    """
    cutoff = conn.execute("select now() - make_interval(secs => %s) as c", (int(older_than_hours * 3600),)).fetchone()["c"]
    created = {str(r["id"]): r["created_at"] for r in conn.execute("select id, created_at from dataset where tenant_id = %s", (TENANT,)).fetchall()}
    created = mark_kept(created, keep_for_the_freed_files_screen(conn), cutoff)
    edges = [(r["a"], r["b"]) for q in LINKS for r in conn.execute(q).fetchall()]
    groups = group_old(created, edges, cutoff)
    if not limit:
        return groups
    chosen, total = [], 0
    for g in groups:
        if total + len(g) > limit and chosen:
            break
        chosen.append(g)
        total += len(g)
    return chosen


def make_plan(conn, dataset_ids: list[str]) -> Plan:
    guards = verify_canary(conn)
    found = conn.execute("select id, tenant_id, name from dataset where id = any(%s)", (dataset_ids,)).fetchall()
    check_datasets_are_canary(dataset_ids, found)
    guards.append(f"G4 all {len(found)} dataset(s) read back from the database belong to {TENANT!r}")
    versions = conn.execute("select id, dataset_id, tenant_id from dataset_version where dataset_id = any(%s)", (dataset_ids,)).fetchall()
    if any(v["tenant_id"] != TENANT for v in versions):
        raise NotCanary("G4: a version of these datasets is not canary's. Nothing was changed")
    return Plan(sorted(found, key=lambda d: d["name"]), versions, guards)


def snapshot(conn) -> dict:
    """Rows per (tenant, table) in every watched table that has a tenant column, for G7."""
    out: dict = {}
    for table in WATCHED:
        has = conn.execute("select 1 from information_schema.columns where table_name = %s and column_name = 'tenant_id'", (table,)).fetchone()
        if not has:
            continue
        for r in conn.execute(f'select tenant_id, count(*) as n from "{table}" group by 1').fetchall():
            out[(r["tenant_id"], table)] = r["n"]
    return out


def has_tenant_column(conn, table: str) -> bool:
    return bool(conn.execute("select 1 from information_schema.columns where table_name = %s and column_name = 'tenant_id'", (table,)).fetchone())


def delete_where(conn, table: str, column: str, values: list[str]):
    """G6. One delete, scoped by id and, wherever the table has a tenant column, by tenant as well."""
    if not values:
        return None
    scope = f' and "tenant_id" = %s' if has_tenant_column(conn, table) else ""
    params = (values, TENANT) if scope else (values,)
    return conn.execute(f'delete from "{table}" where "{column}" = any(%s){scope}', params)


def run_passes(conn, steps: list[tuple[str, str, list[str]]]) -> dict[str, int]:
    """Try every delete; one still blocked by a foreign key from a table not yet cleared is deferred and retried. Stuck ones fail loudly."""
    remaining, deleted = list(steps), {}
    while remaining:
        progressed, still, reasons = False, [], {}
        for table, column, values in remaining:
            try:
                with conn.transaction():
                    cur = delete_where(conn, table, column, values)
                if cur is not None and cur.rowcount:
                    deleted[table] = deleted.get(table, 0) + cur.rowcount
                progressed = True
            except psycopg.errors.ForeignKeyViolation as exc:
                still.append((table, column, values))
                reasons[f"{table}.{column}"] = f"{exc.diag.constraint_name}: {exc.diag.message_detail}"
        if not progressed and still:
            # Rows that only reference each other (a pipeline run, the action runs of that run, and the versions those runs produced
            # point at one another in a ring) cannot be removed one table at a time. A foreign key is checked at the end of a
            # statement, so deleting every stuck table in ONE statement lets the ring go at once. Each part is still scoped to
            # canary in its own SQL (G6), and anything referenced from outside the ring still refuses.
            try:
                with conn.transaction():
                    parts, params = [], []
                    for i, (table, column, values) in enumerate(still):
                        scope = ' and "tenant_id" = %s' if has_tenant_column(conn, table) else ""
                        parts.append(f'd{i} as (delete from "{table}" where "{column}" = any(%s){scope} returning 1)')
                        params += [values] + ([TENANT] if scope else [])
                    counts = ", ".join(f"(select count(*) from d{i}) as n{i}" for i in range(len(still)))
                    row = conn.execute("with " + ", ".join(parts) + " select " + counts, params).fetchone()
                    for i, (table, _column, _values) in enumerate(still):
                        if row[f"n{i}"]:
                            deleted[table] = deleted.get(table, 0) + row[f"n{i}"]
                return deleted
            except psycopg.errors.ForeignKeyViolation as exc:
                raise RuntimeError("stuck, still referenced from outside the set being deleted, even together: " + f"{exc.diag.constraint_name}: {exc.diag.message_detail}. "
                                   "One table at a time, it said: " + "; ".join(f"{k}: {v}" for k, v in reasons.items())) from exc
        remaining = still
    return deleted


def purge(conn, plan: Plan, client=None) -> tuple[dict[str, int], list[str]]:
    """Delete the plan's rows in one transaction, then its objects. Every guard is checked again here."""
    verify_canary(conn)  # G2, G3 again, on this connection, at the moment of acting
    dataset_ids, version_ids = plan.dataset_ids, plan.version_ids

    # G5. The datasets, locked, re-read and re-checked inside the transaction that deletes them.
    locked = conn.execute("select id, tenant_id from dataset where id = any(%s) for update", (dataset_ids,)).fetchall()
    check_datasets_are_canary(dataset_ids, locked)
    locked_versions = conn.execute("select id, tenant_id from dataset_version where dataset_id = any(%s) for update", (dataset_ids,)).fetchall()
    if any(v["tenant_id"] != TENANT for v in locked_versions) or {str(v["id"]) for v in locked_versions} != set(version_ids):
        raise NotCanary("G5: the versions changed, or are not all canary's, since the plan was made. Nothing was changed")

    before = snapshot(conn)  # for G7

    def ids(sql: str) -> list[str]:
        return [str(r["id"]) for r in conn.execute(sql, (version_ids,)).fetchall()]

    leases = ids("select id from access_lease where dataset_version_id = any(%s)")
    action_runs = ids("select id from action_run where output_version = any(%s)")
    pipeline_runs = ids("select id from pipeline_run where source_version_id = any(%s)")
    agent_runs = ids("select id from agent_run where dataset_version_id = any(%s)")
    fetch_jobs = [str(r["id"]) for r in conn.execute("select id from huggingface_fetch_job where dataset_id = any(%s)", (dataset_ids,)).fetchall()]

    try:
        for table, rule in PROTECTED_RULES:
            conn.execute(f'alter table "{table}" disable rule "{rule}"')
        deleted: dict[str, int] = {}

        # What hangs off the runs and leases first.
        first = [("task_read_grant", "action_run_id", action_runs), ("task_read_grant", "pipeline_run_id", pipeline_runs),
                 ("task_read_grant", "agent_run_id", agent_runs), ("task_read_grant", "dataset_version_id", version_ids),
                 ("storage_identity", "action_run_id", action_runs), ("storage_identity", "pipeline_run_id", pipeline_runs),
                 ("storage_identity", "agent_run_id", agent_runs), ("storage_identity", "lease_id", leases),
                 ("storage_identity", "huggingface_fetch_job_id", fetch_jobs),
                 ("catalog_key", "lease_id", leases), ("agent_run_attempt", "run_id", agent_runs),
                 ("pipeline_step_run", "pipeline_run_id", pipeline_runs)]
        for k, v in run_passes(conn, first).items():
            deleted[k] = deleted.get(k, 0) + v

        by_version = [("access_lease", "dataset_version_id", version_ids), ("action_run", "output_version", version_ids),
                      ("action_run", "pipeline_run_id", pipeline_runs), ("gate_decision", "pipeline_run_id", pipeline_runs),
                      ("agent_run", "dataset_version_id", version_ids), ("class_transition", "dataset_version_id", version_ids),
                      ("gate_decision", "dataset_version_id", version_ids), ("lease_request", "dataset_version_id", version_ids),
                      ("pipeline_run", "source_version_id", version_ids), ("storage_reclamation", "dataset_version_id", version_ids),
                      ("access_decision", "dataset_version_id", version_ids), ("derivation", "output_version_id", version_ids),
                      ("derivation", "dataset_id", dataset_ids), ("table_job", "dataset_id", dataset_ids),
                      ("dataset_source", "dataset_id", dataset_ids), ("huggingface_fetch_job", "dataset_id", dataset_ids),
                      ("dataset_version", "id", version_ids)]
        for k, v in run_passes(conn, by_version).items():
            deleted[k] = deleted.get(k, 0) + v

        with conn.transaction():
            cur = delete_where(conn, "dataset", "id", dataset_ids)
            if cur is not None and cur.rowcount:
                deleted["dataset"] = cur.rowcount

        # A deferred foreign key (action_run.output_version) must be checked before the rules go back on.
        conn.execute("set constraints all immediate")
        for table, rule in PROTECTED_RULES:
            conn.execute(f'alter table "{table}" enable rule "{rule}"')

        after = snapshot(conn)
        check_snapshot(before, after)  # G7
        gone_datasets = conn.execute("select count(*) as n from dataset where id = any(%s)", (dataset_ids,)).fetchone()["n"]
        if gone_datasets:
            raise RuntimeError(f"{gone_datasets} dataset(s) are still there after the delete. Rolling back")
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    plan.guards.append("G5 re-read and locked inside the transaction, still all canary")
    plan.guards.append("G6 every delete scoped to canary in its own SQL")
    plan.guards.append("G7 no other tenant's row counts changed")

    # The objects, only after the rows are gone, and only what G8 allows.
    removed: list[str] = []
    if client is not None:
        keys: list[str] = []
        for dataset_id in dataset_ids:
            prefix = f"{PREFIX_ROOT}{dataset_id}/"
            token = None
            while True:
                kwargs = {"Bucket": BUCKET, "Prefix": prefix}
                if token:
                    kwargs["ContinuationToken"] = token
                page = client.list_objects_v2(**kwargs)
                keys.extend(o["Key"] for o in page.get("Contents", []))
                if not page.get("IsTruncated"):
                    break
                token = page.get("NextContinuationToken")
        check_keys(keys, dataset_ids, BUCKET)  # G8
        for start in range(0, len(keys), 1000):
            client.delete_objects(Bucket=BUCKET, Delete={"Objects": [{"Key": k} for k in keys[start:start + 1000]]})
        removed = keys
        plan.guards.append(f"G8 all {len(keys)} object(s) were under canary/<dataset>/ in {BUCKET!r}")
    return deleted, removed


# ----------------------------------------------------------------------------------------- the second stage: agents, then people
#
# The checks register agents in the canary tenant, and registering an agent makes a directory identity for its runtime. Neither is ever
# removed, so canary collected hundreds of invented identities. Agents go first (they hold the identities), then the identities that
# nothing refers to any more. Every guard above applies again, and these add three of their own:
#
#  G9   an agent is only taken when it, its versions, runs and deployments are all older than the cutoff, and the agent's stored code is only
#       taken from canary/agents/<a verified agent>/;
#  G10  a person is only taken when they have no login, are not one of the identities the seed file creates (read from the file, and the
#       tool refuses to go on if it finds fewer than expected), are canary's, and nothing refers to them;
#  G11  the immutability rules on agent versions are switched off only inside the transaction that deletes them and are switched on again
#       before it commits; a rollback restores them.

_HERE = Path(__file__).resolve()
# The repository root when run from a checkout, and the filesystem root in the API container, where the seed file is mounted at /infra.
SEED_FILE = (_HERE.parents[2] if len(_HERE.parents) > 2 else Path("/")) / "infra" / "postgres" / "seed-canary.sql"
MIN_SEEDED = 11  # seven people and four workloads in the seed file
AGENT_PREFIX_ROOT = f"{TENANT}/agents/"
AGENT_RULES = [("agent_version", "agent_version_no_update"), ("agent_version", "agent_version_no_delete")]


def seeded_ids() -> set[str]:
    """The identities the canary seed file creates, read from the file so a newly seeded one is protected without editing this."""
    text = SEED_FILE.read_text(encoding="utf-8")
    return set(re.findall(r"\(\s*'(canary-[a-z0-9-]+)'\s*,\s*'canary'", text))


def check_protected(protected: set[str]) -> None:
    """G10. Refuse to go on without the protected set: an empty or short one would mean the seed file was not read."""
    if len(protected) < MIN_SEEDED:
        raise NotCanary(f"G10: only {len(protected)} seeded identities were found in {SEED_FILE.name}, expected at least {MIN_SEEDED}. "
                        "Refusing to delete any person without knowing who must stay")


def check_agents_are_canary(asked: list[str], found: list[dict]) -> None:
    """G4 and G5 for agents."""
    if any(a["tenant_id"] != TENANT for a in found):
        raise NotCanary("G4/G5: an agent is not canary's. Nothing was changed")
    if {str(a["id"]) for a in found} != set(asked):
        raise NotCanary("G4/G5: some agents asked for do not exist, or are not in the canary tenant. Nothing was changed")


def check_agent_keys(keys: list[str], agent_ids: list[str], bucket: str) -> None:
    """G8 for agents' stored code."""
    if bucket != BUCKET:
        raise NotCanary(f"G8: the bucket is {bucket!r}, not {BUCKET!r}")
    allowed = tuple(f"{AGENT_PREFIX_ROOT}{a}/" for a in agent_ids)
    stray = [k for k in keys if not k.startswith(allowed)]
    if stray:
        raise NotCanary(f"G8: {len(stray)} object(s) are not under a verified canary agent, first {stray[0]!r}. No object was deleted")


def check_people(asked: list[str], found: list[dict], protected: set[str]) -> None:
    """G10."""
    check_protected(protected)
    if {str(p["id"]) for p in found} != set(asked):
        raise NotCanary("G10: some people asked for do not exist. Nothing was changed")
    for p in found:
        if p["tenant_id"] != TENANT:
            raise NotCanary(f"G10: {p['id']!r} is not canary's. Nothing was changed")
        if p["kratos_identity_id"]:
            raise NotCanary(f"G10: {p['id']!r} has a login. Nothing was changed")
        if p["id"] in protected:
            raise NotCanary(f"G10: {p['id']!r} is created by the seed file and must stay. Nothing was changed")


def select_old_agents(conn, older_than_hours: float) -> list[str]:
    """Canary agents that are old, and whose versions, runs and deployments are all old too (G9)."""
    cutoff = conn.execute("select now() - make_interval(secs => %s) as c", (int(older_than_hours * 3600),)).fetchone()["c"]
    rows = conn.execute(
        """select a.id from agent a
            where a.tenant_id = %s and a.created_at < %s
              and not exists (select 1 from agent_version v where v.agent_id = a.id and v.created_at >= %s)
              and not exists (select 1 from agent_run r where r.agent_id = a.id and r.started_at >= %s)
              and not exists (select 1 from agent_deployment d where d.agent_id = a.id and d.deployed_at >= %s)
            order by a.created_at""", (TENANT, cutoff, cutoff, cutoff, cutoff)).fetchall()
    return [str(r["id"]) for r in rows]


def purge_agents(conn, agent_ids: list[str], client=None) -> tuple[dict[str, int], list[str]]:
    """Delete canary agents, their versions, deployments, runs and approvals, and their stored code."""
    verify_canary(conn)
    locked = conn.execute("select id, tenant_id from agent where id = any(%s) for update", (agent_ids,)).fetchall()
    check_agents_are_canary(agent_ids, locked)
    for table in ("agent_version", "agent_run"):
        if conn.execute(f'select 1 from "{table}" where agent_id = any(%s) and tenant_id <> %s limit 1', (agent_ids, TENANT)).fetchone():
            raise NotCanary(f"G5: a row of {table} for these agents is not canary's. Nothing was changed")
    before = snapshot(conn)
    runs = [str(r["id"]) for r in conn.execute("select id from agent_run where agent_id = any(%s)", (agent_ids,)).fetchall()]
    deleted: dict[str, int] = {}
    try:
        for k, v in run_passes(conn, [("task_read_grant", "agent_run_id", runs), ("storage_identity", "agent_run_id", runs),
                                      ("agent_run_attempt", "run_id", runs)]).items():
            deleted[k] = deleted.get(k, 0) + v
        for table, rule in AGENT_RULES:  # G11
            conn.execute(f'alter table "{table}" disable rule "{rule}"')
        for k, v in run_passes(conn, [("agent_run", "agent_id", agent_ids), ("agent_deployment", "agent_id", agent_ids),
                                      ("agent_egress_approval", "agent_id", agent_ids), ("agent_version", "agent_id", agent_ids),
                                      ("agent", "id", agent_ids)]).items():
            deleted[k] = deleted.get(k, 0) + v
        conn.execute("set constraints all immediate")
        for table, rule in AGENT_RULES:
            conn.execute(f'alter table "{table}" enable rule "{rule}"')
        check_snapshot(before, snapshot(conn))  # G7
        if conn.execute("select count(*) as n from agent where id = any(%s)", (agent_ids,)).fetchone()["n"]:
            raise RuntimeError("agents are still there after the delete. Rolling back")
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    removed: list[str] = []
    if client is not None:
        keys: list[str] = []
        for agent_id in agent_ids:
            prefix = f"{AGENT_PREFIX_ROOT}{agent_id}/"
            token = None
            while True:
                kwargs = {"Bucket": BUCKET, "Prefix": prefix}
                if token:
                    kwargs["ContinuationToken"] = token
                page = client.list_objects_v2(**kwargs)
                keys.extend(o["Key"] for o in page.get("Contents", []))
                if not page.get("IsTruncated"):
                    break
                token = page.get("NextContinuationToken")
        check_agent_keys(keys, agent_ids, BUCKET)  # G8
        for start in range(0, len(keys), 1000):
            client.delete_objects(Bucket=BUCKET, Delete={"Objects": [{"Key": k} for k in keys[start:start + 1000]]})
        removed = keys
    return deleted, removed


def referenced_people(conn, ids: list[str]) -> set[str]:
    """Which of these directory ids some row still points at, through any foreign key to the directory."""
    held: set[str] = set()
    refs = conn.execute(
        """select c.conrelid::regclass::text as tbl, a.attname as col from pg_constraint c
             join pg_attribute a on a.attrelid = c.conrelid and a.attnum = any(c.conkey)
            where c.contype = 'f' and c.confrelid = 'public.directory'::regclass""").fetchall()
    for r in refs:
        for row in conn.execute(f'select distinct "{r["col"]}" as v from {r["tbl"]} where "{r["col"]}" = any(%s)', (ids,)).fetchall():
            held.add(row["v"])
    return held


def select_old_people(conn, older_than_hours: float) -> list[str]:
    """Canary identities that are old, have no login, are not seeded, and that nothing refers to (G10)."""
    protected = seeded_ids()
    check_protected(protected)
    cutoff = conn.execute("select now() - make_interval(secs => %s) as c", (int(older_than_hours * 3600),)).fetchone()["c"]
    rows = conn.execute(
        """select id from directory where tenant_id = %s and kratos_identity_id is null and created_at < %s and not (id = any(%s))""",
        (TENANT, cutoff, list(protected))).fetchall()
    ids = [r["id"] for r in rows]
    if not ids:
        return []
    held = referenced_people(conn, ids)
    return [i for i in ids if i not in held]


def purge_people(conn, ids: list[str]) -> int:
    """Delete canary identities that were selected by select_old_people, with every check made again inside the transaction."""
    protected = seeded_ids()
    check_protected(protected)
    verify_canary(conn)
    locked = conn.execute("select id, tenant_id, kratos_identity_id from directory where id = any(%s) for update", (ids,)).fetchall()
    check_people(ids, locked, protected)
    before = snapshot(conn)
    try:
        cur = conn.execute(
            """delete from directory where id = any(%s) and tenant_id = %s and kratos_identity_id is null and not (id = any(%s))""",
            (ids, TENANT, list(protected)))
        if cur.rowcount != len(ids):
            raise RuntimeError(f"{cur.rowcount} identities were deleted where {len(ids)} were expected. Rolling back")
        check_snapshot(before, snapshot(conn))  # G7
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return len(ids)
