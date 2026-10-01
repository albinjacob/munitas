"""U91: the Iceberg catalog shows a person only the tables they may read, and
opens them only on the same terms as any other read.

The claim is not "the catalog works". It is that the catalog adds a way in
without adding a way round:

  * a token names a person and a purpose, is stored only as a hash, and is
    accepted by the catalog and by nothing else
  * a table is listed to a person only while they may read it, and a table they
    may not read is neither listed nor opened
  * opening one is a recorded decision, made by the same code as a credential
    request, and the key it returns reaches that version and nothing else
  * when the lease ends the table goes, and so does the key
  * another organisation, an expired or revoked token, and every write are refused

Per assertion, never in aggregate.

    docker compose exec -T munitas-api python /verify/v91_iceberg_catalog.py
"""

from __future__ import annotations

import hashlib
import sys
import time
import uuid

from common import (ADMIN, CANARY, RESEARCHER, api, bearer_for, check, db,
                    fixture_department, fixture_tabular_version, fixture_tenant,
                    heading, require_api, s3_client, summary)

OTHER_PERSON = "sam-researcher"  # an organisation that is not canary
ACTIVATION_WAIT_SECONDS = 90


def mint(person: str, purpose: str, hours: float = 1) -> dict:
    r = api("POST", "/iceberg/tokens", json={"purpose": purpose, "hours": hours},
            headers=bearer_for(person))
    r.raise_for_status()
    return r.json()


def with_token(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def catalog(path: str, token: str, method: str = "GET", **kwargs):
    return api(method, f"/iceberg/v1{path}", headers=with_token(token), **kwargs)


def open_table(tenant: str, namespace: str, table: str, token: str, wait: bool = False):
    """Open a table. Approval can be given before storage permissions have been
    printed, which the catalog answers with 503 and Retry-After; `wait` retries."""
    deadline = time.monotonic() + 45
    while True:
        r = catalog(f"/{tenant}/namespaces/{namespace}/tables/{table}", token)
        if not (wait and r.status_code == 503 and time.monotonic() < deadline):
            return r
        time.sleep(2)


def decisions(person: str, version_id: str) -> list[dict]:
    with db() as conn:
        return conn.execute(
            """select phase, allowed from access_decision
                where principal = %s and dataset_version_id = %s order by id""",
            (person, version_id)).fetchall()


def main() -> int:
    require_api()
    tenant = fixture_tenant(CANARY)

    # Two tabular versions in the researcher's own organisation: one anyone in
    # the organisation may read (published), one that needs a lease (raw).
    open_v = fixture_tabular_version(tenant, klass="PUBLISHED")
    raw_v = fixture_tabular_version(tenant, klass="RAW")
    custodian = fixture_department(tenant, raw_v["dataset_id"])
    purpose = f"u91 check {uuid.uuid4().hex[:6]}"

    heading("U91: a token names a person and a purpose, and nothing else")
    issued = mint(RESEARCHER, purpose, hours=72)
    token = issued["token"]
    check("a token is issued with the mct_ prefix", token.startswith("mct_"), token[:4])
    check("it names the person's organisation and the purpose asked",
          issued["tenant_id"] == tenant and issued["purpose"] == purpose,
          f"{issued['tenant_id']} / {issued['purpose']}")
    check("its life is capped, whatever was asked for", issued["hours"] <= 12, str(issued["hours"]))
    check("it says which address and warehouse to connect to",
          issued["catalog_url"].endswith("/iceberg") and issued["warehouse"] == tenant,
          f"{issued['catalog_url']} {issued['warehouse']}")
    with db() as conn:
        row = conn.execute("select * from catalog_token where id = %s", (issued["id"],)).fetchone()
    hashed = row["token_hash"] == hashlib.sha256(token.encode()).hexdigest() and token not in str(dict(row))
    check("only a hash of it is stored", hashed, "a hash" if hashed else "the token itself is in the table")

    heading("U91: it is accepted by the catalog and by nothing else")
    none = api("GET", "/iceberg/v1/config")
    wrong = api("GET", "/iceberg/v1/config", headers=with_token("mct_not-a-real-token"))
    check("no token is refused", none.status_code == 401, f"HTTP {none.status_code}")
    check("a wrong token is refused", wrong.status_code == 401, f"HTTP {wrong.status_code}")
    same = none.json() == wrong.json()
    check("both refusals say the same thing, so the answer reveals nothing",
          same, "identical" if same else "the two bodies differ")
    check("the refusal is in the shape Iceberg clients read",
          set(none.json().get("error", {})) == {"message", "type", "code"}, str(none.json()))
    elsewhere = api("GET", "/auth/whoami", headers=with_token(token))
    check("the token is not accepted by the rest of the API", elsewhere.status_code == 401,
          f"HTTP {elsewhere.status_code}")
    cfg = catalog("/config", token)
    check("with the token the catalog answers", cfg.status_code == 200, f"HTTP {cfg.status_code}")
    check("and the warehouse is the person's own organisation",
          cfg.json().get("overrides", {}).get("prefix") == tenant, str(cfg.json().get("overrides")))
    check("it lists reading, and nothing that writes",
          all(e.startswith(("GET", "HEAD")) for e in cfg.json().get("endpoints", [])),
          str(cfg.json().get("endpoints")))

    heading("U91: a table is listed only while the person may read it")
    spaces = catalog(f"/{tenant}/namespaces", token).json()["namespaces"]
    check("the published dataset is listed", [open_v["dataset_name"]] in spaces,
          f"{len(spaces)} namespaces")
    check("the raw dataset, with no lease, is not", [raw_v["dataset_name"]] not in spaces,
          f"{len(spaces)} namespaces")
    tables = catalog(f"/{tenant}/namespaces/{open_v['dataset_name']}/tables", token)
    check("the published dataset's version is listed as a table",
          tables.status_code == 200 and {"namespace": [open_v["dataset_name"]], "name": "v1"}
          in tables.json()["identifiers"], str(tables.json()))
    hidden = catalog(f"/{tenant}/namespaces/{raw_v['dataset_name']}/tables", token)
    check("asking for the raw dataset's tables answers as if there were none",
          hidden.status_code == 404, f"HTTP {hidden.status_code}")

    heading("U91: opening a table is a decision, made and recorded as any read is")
    refused = open_table(tenant, raw_v["dataset_name"], "v1", token)
    check("opening the raw table with no lease is refused", refused.status_code == 403,
          f"HTTP {refused.status_code}")
    check("the refusal says why, in the catalog's own shape",
          refused.json().get("error", {}).get("type") == "ForbiddenException"
          and bool(refused.json()["error"]["message"]), str(refused.json())[:160])
    check("and it is in the audit log as a refusal",
          any(d["allowed"] is False for d in decisions(RESEARCHER, raw_v["id"])),
          str(decisions(RESEARCHER, raw_v["id"])))

    opened = open_table(tenant, open_v["dataset_name"], "v1", token, wait=True)
    check("opening the published table works", opened.status_code == 200, f"HTTP {opened.status_code}")
    body = opened.json() if opened.status_code == 200 else {}
    meta = body.get("metadata", {})
    with db() as conn:
        ref = conn.execute("select * from iceberg_table_ref where dataset_version_id = %s",
                           (open_v["id"],)).fetchone()
    check("it returns the metadata file the register recorded",
          body.get("metadata-location") == ref["metadata_location"], str(body.get("metadata-location")))
    check("and the table's metadata, which names the same snapshot",
          meta.get("current-snapshot-id") == ref["snapshot_id"] and meta.get("format-version") == 2,
          f"{meta.get('current-snapshot-id')} {meta.get('format-version')}")
    cfg_out = body.get("config", {})
    check("it returns what a tool needs to read the files",
          {"s3.endpoint", "s3.access-key-id", "s3.secret-access-key"} <= set(cfg_out), str(sorted(cfg_out)))
    check("at the address a person's own tool can reach, not the one inside the network",
          "seaweedfs" not in cfg_out.get("s3.endpoint", "seaweedfs"), cfg_out.get("s3.endpoint", ""))
    d = decisions(RESEARCHER, open_v["id"])
    check("the open is recorded: the policy's answer, then the grant",
          [x["phase"] for x in d][:2] == ["policy", "grant"] and all(x["allowed"] for x in d[:2]),
          str(d))

    heading("U91: with a lease the raw table appears, and the key reaches that version only")
    asked = api("POST", "/leases/requests", json={
        "tenant_id": tenant, "principal": RESEARCHER, "dataset_version_id": raw_v["id"],
        "purpose": purpose, "justification": "u91 verifies the catalog", "ttl_hours": 4,
    }, headers=bearer_for(RESEARCHER))
    check("the researcher asks for a lease", asked.status_code == 201, f"HTTP {asked.status_code}")
    approved = api("POST", f"/leases/requests/{asked.json()['id']}/approve",
                   headers=bearer_for(custodian))
    check("the custodian approves it", approved.status_code == 201, f"HTTP {approved.status_code}")
    lease_id = approved.json().get("lease_id") or approved.json().get("id")

    other_purpose = mint(RESEARCHER, "a different purpose entirely")
    wrong_purpose = open_table(tenant, raw_v["dataset_name"], "v1", other_purpose["token"])
    check("a token for a different purpose does not open it", wrong_purpose.status_code == 403,
          f"HTTP {wrong_purpose.status_code}")

    spaces = catalog(f"/{tenant}/namespaces", token).json()["namespaces"]
    check("the raw dataset is listed now", [raw_v["dataset_name"]] in spaces, f"{len(spaces)} namespaces")
    got = open_table(tenant, raw_v["dataset_name"], "v1", token, wait=True)
    check("and its table opens", got.status_code == 200, f"HTTP {got.status_code}: {got.text[:120]}")
    leased = got.json().get("config", {}) if got.status_code == 200 else {}
    if leased:
        mine = s3_client(leased["s3.access-key-id"], leased["s3.secret-access-key"])
        with db() as conn:
            raw_ref = conn.execute("select metadata_location from iceberg_table_ref "
                                   "where dataset_version_id = %s", (raw_v["id"],)).fetchone()
        bucket, key = raw_ref["metadata_location"].removeprefix("s3://").split("/", 1)
        try:
            mine.get_object(Bucket=bucket, Key=key)["Body"].read()
            reads_own = True
        except Exception as exc:
            reads_own = False
        check("the key reads the version's table file", reads_own,
              "read" if reads_own else "the key could not read it")
        other_key = open_v["records_key"]
        try:
            mine.get_object(Bucket=open_v["bucket"], Key=other_key)["Body"].read()
            reads_other = True
        except Exception:
            reads_other = False
        check("the same key cannot read another version's files", not reads_other,
              "refused" if not reads_other else "it read a different version's records")

    heading("U91: when the lease ends, the table goes, and so does the key")
    revoked = api("POST", f"/leases/{lease_id}/revoke", headers=bearer_for(custodian))
    check("the custodian revokes the lease", revoked.status_code == 200, f"HTTP {revoked.status_code}")
    after = open_table(tenant, raw_v["dataset_name"], "v1", token)
    check("the table no longer opens", after.status_code == 403, f"HTTP {after.status_code}")
    spaces = catalog(f"/{tenant}/namespaces", token).json()["namespaces"]
    check("and is no longer listed", [raw_v["dataset_name"]] not in spaces, f"{len(spaces)} namespaces")
    if leased:
        deadline, stopped = time.monotonic() + ACTIVATION_WAIT_SECONDS, False
        while time.monotonic() < deadline:
            try:
                mine.get_object(Bucket=bucket, Key=key)["Body"].read()
                time.sleep(3)
            except Exception:
                stopped = True
                break
        check(f"the key stops working once the lease is revoked (waited up to "
              f"{ACTIVATION_WAIT_SECONDS} s)", stopped,
              "refused" if stopped else "the key still reads after revocation")

    heading("U91: a lease that simply runs out is cut off too, with nobody asking")
    again = api("POST", "/leases/requests", json={
        "tenant_id": tenant, "principal": RESEARCHER, "dataset_version_id": raw_v["id"],
        "purpose": purpose, "justification": "u91 verifies expiry", "ttl_hours": 1,
    }, headers=bearer_for(RESEARCHER))
    second = api("POST", f"/leases/requests/{again.json()['id']}/approve", headers=bearer_for(custodian))
    check("a second lease is approved", again.status_code == 201 and second.status_code == 201,
          f"HTTP {again.status_code}/{second.status_code}")
    reopened = open_table(tenant, raw_v["dataset_name"], "v1", token, wait=True)         if catalog("/config", token).status_code == 200 else None
    if reopened is None:
        token = mint(RESEARCHER, purpose)["token"]
        reopened = open_table(tenant, raw_v["dataset_name"], "v1", token, wait=True)
    check("its table opens", reopened.status_code == 200, f"HTTP {reopened.status_code}")
    if reopened.status_code == 200:
        c2 = reopened.json()["config"]
        k2 = s3_client(c2["s3.access-key-id"], c2["s3.secret-access-key"])
        lease2 = second.json().get("lease_id") or second.json().get("id")
        with db() as conn:
            conn.execute("update access_lease set expires_at = now() where id = %s",
                         (lease2,))
        # Expired now, so it ends after the print that held it live, as a real lease
        # does. (Setting it earlier than that print would be a sequence that cannot
        # happen.) Nobody makes a request from here on: only the activator can print.
        deadline, cut = time.monotonic() + ACTIVATION_WAIT_SECONDS, False
        while time.monotonic() < deadline:
            try:
                k2.get_object(Bucket=bucket, Key=key)["Body"].read()
                time.sleep(3)
            except Exception:
                cut = True
                break
        check(f"the key stops working on its own, within the activator's next ticks (waited up to "
              f"{ACTIVATION_WAIT_SECONDS} s)", cut, "refused" if cut else "the key still reads after expiry")
        gone = open_table(tenant, raw_v["dataset_name"], "v1", token)
        check("and the table no longer opens", gone.status_code == 403, f"HTTP {gone.status_code}")

    heading("U91: another organisation sees none of it")
    stranger = mint(OTHER_PERSON, "u91 another organisation")["token"]
    cfg2 = catalog("/config", stranger).json()
    their_prefix = cfg2["overrides"]["prefix"]
    check("their warehouse is their own organisation, not ours", their_prefix != tenant, their_prefix)
    names = catalog(f"/{their_prefix}/namespaces", stranger).json()["namespaces"]
    check("none of our datasets is listed to them",
          [open_v["dataset_name"]] not in names and [raw_v["dataset_name"]] not in names,
          f"{len(names)} namespaces")
    naming_ours = catalog(f"/{tenant}/namespaces", stranger)
    check("naming our warehouse is answered as if it did not exist", naming_ours.status_code == 404,
          f"HTTP {naming_ours.status_code}")
    forged = open_table(their_prefix, open_v["dataset_name"], "v1", stranger)
    check("and so is opening our table by name under theirs", forged.status_code == 404,
          f"HTTP {forged.status_code}")

    heading("U91: the catalog writes nothing")
    victim = f"/{tenant}/namespaces/{open_v['dataset_name']}/tables"
    for label, method, path in [
        ("creating a table", "POST", victim),
        ("committing to a table", "POST", f"{victim}/v1"),
        ("dropping a table", "DELETE", f"{victim}/v1"),
        ("creating a namespace", "POST", f"/{tenant}/namespaces"),
        ("renaming a table", "POST", f"/{tenant}/tables/rename"),
    ]:
        r = catalog(path, token, method=method, json={})
        check(f"{label} is refused", r.status_code == 403
              and "read-only" in r.json().get("error", {}).get("message", ""),
              f"HTTP {r.status_code}")
    unauth = api("POST", f"/iceberg/v1{victim}", json={})
    check("and an unauthenticated write is refused as unauthenticated", unauth.status_code == 401,
          f"HTTP {unauth.status_code}")
    with db() as conn:
        still = conn.execute("select count(*) as n from iceberg_table_ref where dataset_version_id = %s",
                             (open_v["id"],)).fetchone()["n"]
    check("the table is still there", still == 1, str(still))

    heading("U91: a token can be listed without being shown, revoked, and expires")
    mine_list = api("GET", "/iceberg/tokens", headers=bearer_for(RESEARCHER)).json()["tokens"]
    check("the person's tokens are listed", any(t["id"] == issued["id"] and t["active"] for t in mine_list),
          f"{len(mine_list)} tokens")
    clean = all("token" not in t and "token_hash" not in t for t in mine_list)
    check("without the token itself", clean, "no token" if clean else "a token or hash is in the listing")
    gone = api("POST", f"/iceberg/tokens/{issued['id']}/revoke", headers=bearer_for(RESEARCHER))
    check("the person revokes one", gone.status_code == 200, f"HTTP {gone.status_code}")
    check("and it stops working", catalog("/config", token).status_code == 401,
          f"HTTP {catalog('/config', token).status_code}")
    foreign = api("POST", f"/iceberg/tokens/{issued['id']}/revoke", headers=bearer_for(OTHER_PERSON))
    check("somebody else cannot revoke it, and is told it does not exist", foreign.status_code == 404,
          f"HTTP {foreign.status_code}")
    stale = "mct_" + uuid.uuid4().hex
    with db() as conn:
        conn.execute(
            """insert into catalog_token (id, token_hash, principal, tenant_id, purpose, expires_at)
               values (gen_random_uuid(), %s, %s, %s, 'expired', now() - interval '1 minute')""",
            (hashlib.sha256(stale.encode()).hexdigest(), RESEARCHER, tenant))
    check("an expired token is refused", catalog("/config", stale).status_code == 401,
          f"HTTP {catalog('/config', stale).status_code}")

    return summary("U91")


if __name__ == "__main__":
    sys.exit(main())
