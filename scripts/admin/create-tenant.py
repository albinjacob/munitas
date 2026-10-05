"""Create a tenant from an onboarding file: departments, people, workloads.

Nothing else in this platform can bring up a tenant's first person: there is
no POST /directory or POST /departments endpoint, by design: creating the
people who may then decide things is an operator's act, not something a
request to the platform can do for itself. This script is the one place that gap is closed, and only from the host.

The onboarding file never names a tenant. `key` is a local reference; the
real directory.id for each person or workload is derived as
"{tenant}-{slug(key)}" at creation time, because directory.id is one global
primary key across every tenant (see infra/postgres/seed-canary.sql's own
comment on this) and a key that is only locally unique must not become a
globally colliding id.

Idempotent, row by row: rerunning against a tenant that already exists is
safe and reports what already existed. Editing the file to add one more
person and rerunning creates only that person.

    python scripts/admin/create-tenant.py --tenant acme --config scripts/admin/onboarding-template.json
    python scripts/admin/create-tenant.py --tenant acme --config scripts/admin/onboarding-template.json --purpose canary
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from ports_config import PORTS  # noqa: E402

PG_DSN = os.environ.get(
    "PG_DSN", f"postgresql://munitas:munitas@localhost:{PORTS['postgres']}/platform"
)
API = os.environ.get("MUNITAS_VERIFY_API", f"http://localhost:{PORTS['munitas_api_http']}")


def slugify(key: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", key.strip().lower()).strip("-")
    return slug or "x"


def known_roles() -> set[str]:
    # The role list is closed to anonymous callers. This script is the platform's operator acting, so it sends the worker token.
    r = httpx.get(f"{API}/policy/roles", timeout=15.0,
                  headers={"x-worker-token": os.environ.get("MUNITAS_WORKER_TOKEN", "dev-worker-token-not-for-production")})
    r.raise_for_status()
    return set(r.json()["role_floor"].keys())


def validate_roles(config: dict, known: set[str]) -> list[str]:
    problems = []
    for section in ("people", "workloads"):
        for entry in config.get(section, []):
            for role in entry.get("roles", []):
                if role not in known:
                    problems.append(
                        f"{entry['key']!r} in {section!r} has unknown role {role!r}"
                    )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--purpose", default="production",
                        choices=["production", "canary"])
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as f:
        config = json.load(f)

    try:
        problems = validate_roles(config, known_roles())
    except httpx.HTTPError as exc:
        print(f"could not reach the policy engine at {API}: {exc}")
        return 1

    if problems:
        print("Refusing to create anything: unknown roles found.")
        for p in problems:
            print(f"  {p}")
        return 1

    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn:
        existing = conn.execute(
            "select purpose from tenant where id = %s", (args.tenant,)
        ).fetchone()
        if existing:
            print(f"tenant {args.tenant!r} already exists (purpose={existing['purpose']})")
        else:
            # Optional, and absent for almost every tenant. A number here
            # says this tenant is expected to be large enough to want more
            # than the install's default opening storage, decided once, by
            # name, at the moment somebody actually knows it. See
            # `initial_storage_volumes` in scripts/admin/onboarding-template.json.
            volumes = (config.get("storage") or {}).get("initial_storage_volumes")
            if volumes is not None and (not isinstance(volumes, int) or volumes < 1):
                print(f"storage.initial_storage_volumes must be a positive "
                      f"whole number, not {volumes!r}")
                return 1
            conn.execute(
                "insert into tenant (id, isolation_level, key_ref, purpose, "
                "                    initial_storage_volumes) "
                "values (%s, 'shared', %s, %s, %s)",
                (args.tenant, f"key/{args.tenant}", args.purpose, volumes),
            )
            opening = (f", opening storage {volumes} volume(s)"
                       if volumes else "")
            print(f"tenant {args.tenant!r}: created "
                  f"(purpose={args.purpose}{opening})")

        derived_ids: dict[str, str] = {}
        for section, kind in (("people", "human"), ("workloads", "workload")):
            for entry in config.get(section, []):
                directory_id = f"{args.tenant}-{slugify(entry['key'])}"
                derived_ids[entry["key"]] = directory_id
                found = conn.execute(
                    "select id from directory where id = %s", (directory_id,)
                ).fetchone()
                if found:
                    print(f"  {entry['key']}: already exists ({directory_id})")
                    continue
                conn.execute(
                    "insert into directory (id, tenant_id, label, kind, roles) "
                    "values (%s, %s, %s, %s, %s)",
                    (directory_id, args.tenant, entry["label"], kind, entry["roles"]),
                )
                print(f"  {entry['key']}: created ({directory_id})")

        for entry in config.get("departments", []):
            custodian_id = derived_ids.get(entry["custodian"])
            if not custodian_id:
                print(
                    f"  {entry['key']}: refused, custodian {entry['custodian']!r} "
                    "is not one of this file's people"
                )
                conn.rollback()
                return 1
            found = conn.execute(
                "select id from department where tenant_id = %s and name = %s",
                (args.tenant, entry["name"]),
            ).fetchone()
            if found:
                print(f"  {entry['key']}: already exists ({entry['name']})")
                continue
            conn.execute(
                "insert into department (id, tenant_id, name, custodian) "
                "values (%s, %s, %s, %s)",
                (str(uuid.uuid4()), args.tenant, entry["name"], custodian_id),
            )
            print(f"  {entry['key']}: created ({entry['name']})")

        conn.commit()

    return 0


if __name__ == "__main__":
    sys.exit(main())
