"""Connect one real login to every registered human.

Real authentication. Kratos has no identities of its own until something
creates one, and nothing here lets a person self-register
(`selfservice.flows.registration.enabled: false` in infra/kratos/kratos.yml)
-- a login only exists because a directory row already exists and an
administrator is connecting the two, the same "identity comes from the
registry, never from the thing being decided" rule this platform already
applies everywhere else.

Who gets a login lives in infra/kratos/identities.json, not in this file.
Adding a tenant means adding a group to that JSON file with the same
directory_ids its infra/postgres/seed-*.sql inserts, then re-running this
script -- no Python edit needed. Workloads are not included and never will
be: `health-pipeline`, `agent-triage-1` and the rest have no login, because
their identity is the runtime that holds them (agent/identity.py), not a
session -- see schema.sql's comment on `directory.kratos_identity_id`.

Idempotent per identity: a row that already has a `kratos_identity_id` is
left alone. If a Kratos identity with a seeded email exists but the
directory row was never linked (a partial prior run), it links the existing
identity rather than creating a second one. Safe to re-run any time,
including after a Postgres volume reset -- that is the whole point of it
living here as a committed script instead of a one-off admin-API call.

    .venv\\Scripts\\python.exe infra/kratos/seed-identities.py

If Kratos itself was recreated against an emptied database (not just
`platform`, but Kratos's own schema), its own migrations need to run first:
`docker compose up -d --force-recreate kratos` re-runs `kratos-migrate`,
the same fix Temporal needs after that kind of reset. A 500 with
"identities_nid_fk_idx" from the admin API is that condition.

One shared password, the same posture as `MUNITAS_MASTER_KEY`'s own
documented "not for production" default: these identities exist to prove
the mechanism works, not to guard anything real, and one password is one
fewer thing to keep straight across every login that all mean the same
thing (a name typed into psql during a demo, not a real access boundary).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from ports_config import PORTS  # noqa: E402

KRATOS_ADMIN = os.environ.get("MUNITAS_KRATOS_ADMIN_URL", f"http://localhost:{PORTS['kratos_admin']}")
PG_DSN = os.environ.get(
    "PG_DSN", f"postgresql://munitas:munitas@localhost:{PORTS['postgres']}/platform"
)
PASSWORD = "dev-password-not-for-production"
IDENTITIES_FILE = Path(__file__).resolve().parent / "identities.json"


@dataclass(frozen=True)
class Seed:
    directory_id: str
    email: str
    name: str
    tenant: str


def load_seeds(path: Path) -> list[Seed]:
    data = json.loads(path.read_text(encoding="utf-8"))
    seeds: list[Seed] = []
    for group in data["tenants"]:
        for identity in group["identities"]:
            seeds.append(Seed(
                directory_id=identity["directory_id"],
                email=identity["email"],
                name=identity["name"],
                tenant=group["tenant"],
            ))
    return seeds


def find_existing_identity(email: str) -> str | None:
    r = httpx.get(
        f"{KRATOS_ADMIN}/admin/identities",
        params={"credentials_identifier": email},
        timeout=10.0,
    )
    r.raise_for_status()
    matches = r.json()
    return matches[0]["id"] if matches else None


def create_identity(seed: Seed) -> str:
    r = httpx.post(
        f"{KRATOS_ADMIN}/admin/identities",
        json={
            "schema_id": "default",
            "traits": {"email": seed.email, "name": seed.name},
            "credentials": {"password": {"config": {"password": PASSWORD}}},
        },
        timeout=10.0,
    )
    if r.status_code == 409:
        existing = find_existing_identity(seed.email)
        if existing:
            return existing
    r.raise_for_status()
    return r.json()["id"]


def link(conn, seed: Seed) -> str:
    """Link one seed, or report why it could not be. One line either way."""
    row = conn.execute(
        "select kratos_identity_id from directory where id = %s",
        (seed.directory_id,),
    ).fetchone()
    if row is None:
        return f"skip   [{seed.tenant}] {seed.directory_id:<20} not in directory yet"
    if row["kratos_identity_id"]:
        return f"linked [{seed.tenant}] {seed.directory_id:<20} already ({row['kratos_identity_id']})"

    identity_id = create_identity(seed)
    conn.execute(
        "update directory set kratos_identity_id = %s where id = %s",
        (identity_id, seed.directory_id),
    )
    return f"linked [{seed.tenant}] {seed.directory_id:<20} {seed.email}"


def main() -> int:
    seeds = load_seeds(IDENTITIES_FILE)
    results: list[str] = []
    with psycopg.connect(PG_DSN, row_factory=dict_row, autocommit=True) as conn:
        for seed in seeds:
            results.append(link(conn, seed))

    for line in results:
        print(line)

    skipped = [r for r in results if r.startswith("skip")]
    if skipped:
        print(
            f"\n{len(skipped)} directory row(s) missing. Apply that tenant's "
            "infra/postgres/seed-*.sql first, then re-run this script."
        )

    print(f"\nOne shared password for every identity above: {PASSWORD!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
