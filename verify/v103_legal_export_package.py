"""U103: a legal export is produced, delivered and opened, and cannot be opened wrongly or quietly altered.

This takes an export through the whole chain: asked for, approved, confirmed, built by the platform, and then
opened by the tool a recipient would use (`scripts/client/open_legal_package.py`), which shares no code with the
platform. It checks the things a court would later ask about: that the files are the files, that the manifest is
signed by the platform, that the passphrase is given once and only to the custodian, that a link works a limited
number of times, that a package whose source bytes were altered after sealing is not produced, that damage or a
wrong passphrase is noticed, and that nothing outlives its retention. The days are brought forward by changing
the dates the platform works from.

    docker compose exec -T munitas-api python /verify/v103_legal_export_package.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import httpx

sys.path.insert(0, "/client")
sys.path.insert(0, "/app")

from common import ADMIN, api, bearer_for, check, db, heading, require_api, s3_client, summary  # noqa: E402
from lifecycle_fixture import (ADMIN_A, ADMIN_B, drop_org, export_body, finish_org, hold_in_force, make_org,  # noqa: E402
                               seal_altered_version, seal_data)
import open_legal_package  # noqa: E402

BUCKET = "munitas-legal-exports"


def status(export_id: str) -> dict:
    with db() as conn:
        return conn.execute("select status, failure, package_key, passphrase_ciphertext, expires_at from legal_export where id = %s",
                            (export_id,)).fetchone()


def wait_for(export_id: str, wanted: str, seconds: int = 120) -> dict:
    end = time.monotonic() + seconds
    row = status(export_id)
    while time.monotonic() < end and row["status"] not in (wanted, "failed"):
        time.sleep(2)
        row = status(export_id)
    return row


def object_exists(key: str) -> bool:
    from botocore.exceptions import ClientError
    try:
        s3_client(*ADMIN).head_object(Bucket=BUCKET, Key=key)
        return True
    except ClientError:
        return False


def through_to_confirmed(priya, ravi, records, hold_id: str, datasets: list[str], **changes) -> str:
    made = api("POST", "/legal-exports", headers=priya, json=export_body(hold_id, datasets, **changes))
    made.raise_for_status()
    export_id = made.json()["id"]
    api("POST", f"/legal-exports/{export_id}/approve", headers=ravi, json={"approve": True, "note": "ok"}).raise_for_status()
    api("POST", f"/legal-exports/{export_id}/confirm", headers=records, json={"approve": True, "note": "scope is right"}).raise_for_status()
    return export_id


def main() -> int:
    require_api()
    org = make_org()
    priya, ravi = bearer_for(ADMIN_A), bearer_for(ADMIN_B)
    try:
        data = seal_data(org)
        records, custodian = org.bearer("dpo"), org.bearer("custodian")
        with db() as conn:
            conn.execute("insert into tombstone (record_id, tenant_id, reason, requested_by) values (%s, %s, 'patient request', 'x')",
                         (f"erased-{org.id[-6:]}", org.id))
        hold_id = hold_in_force(org, priya, ravi)

        heading("Produced by the platform, from the stored files")
        export_id = through_to_confirmed(priya, ravi, records, hold_id, [data["dataset_id"]])
        row = wait_for(export_id, "ready")
        check("the package is built without anybody opening its contents", row["status"] == "ready", f"{row['status']} {row['failure'] or ''}")
        listing = api("GET", "/legal-exports", params={"hold_id": hold_id}, headers=priya).json()["exports"]
        mine = next(e for e in listing if e["id"] == export_id)
        check("it is signed, hashed and counted", bool(mine["signature"]) and bool(mine["package_sha256"]) and mine["file_count"] == 2,
              f"{mine['file_count']} files")
        check("and what a platform administrator sees holds no part of the passphrase",
              "passphrase_ciphertext" not in mine and "passphrase_wrapped_key" not in mine)
        files = api("GET", f"/legal-exports/{export_id}/manifest", headers=priya).json()["files"]
        expected = {f"data/records/v1/{k}": hashlib.sha256(v).hexdigest() for k, v in data["bodies"].items()}
        check("the manifest lists each stored file with the hash of its bytes", {f["path"]: f["sha256"] for f in files} == expected)

        heading("The passphrase goes to the custodian once")
        r = api("POST", f"/legal-exports/{export_id}/passphrase", headers=priya)
        check("a platform administrator is not given it", r.status_code == 403, f"{r.status_code}")
        r = api("POST", f"/legal-exports/{export_id}/passphrase", headers=custodian)
        check("nor is the organisation's data custodian", r.status_code == 403, f"{r.status_code}")
        r = api("POST", f"/legal-exports/{export_id}/passphrase", headers=records)
        passphrase = r.json().get("passphrase", "")
        check("the custodian the hold names is given it", r.status_code == 200 and len(passphrase) >= 25, f"{r.status_code}")
        r = api("POST", f"/legal-exports/{export_id}/passphrase", headers=records)
        check("and cannot read it again", r.status_code == 403, f"{r.status_code}")
        check("it is no longer stored", status(export_id)["passphrase_ciphertext"] is None)

        heading("A link works a limited number of times")
        r = api("POST", f"/legal-exports/{export_id}/links", headers=custodian)
        check("only a platform administrator makes a link", r.status_code == 403, f"{r.status_code}")
        made = api("POST", f"/legal-exports/{export_id}/links", headers=priya)
        link = made.json()
        check("a link is made, shown once, with an expiry and a number of uses",
              made.status_code == 201 and link["token"].startswith("mlx_") and link["uses"] == 3, f"{made.status_code}")
        downloads = [api("GET", link["download_path"]) for _ in range(3)]
        check("it works three times", all(d.status_code == 200 for d in downloads), str([d.status_code for d in downloads]))
        package_bytes = downloads[0].content
        check("each time it hands over the same bytes the platform recorded",
              all(hashlib.sha256(d.content).hexdigest() == mine["package_sha256"] for d in downloads))
        check("and the package is encrypted, so the bytes say nothing", b"record_id" not in package_bytes and package_bytes[:5] == b"MLEP1")
        check("a fourth use is refused", api("GET", link["download_path"]).status_code == 404)
        check("so is a link nobody made", api("GET", "/legal-exports/download/mlx_nothing").status_code == 404)
        with db() as conn:
            grants = conn.execute("select count(*) as n from access_decision where tenant_id = %s and purpose like %s and allowed",
                                  (org.id, "legal export KB-2026-004411: package downloaded")).fetchone()["n"]
            refused = conn.execute("select count(*) as n from access_decision where tenant_id = %s and purpose like %s and not allowed",
                                   (org.id, "legal export KB-2026-004411: download refused")).fetchone()["n"]
        check("every download and the refused one are in the audit trail", grants == 3 and refused == 1, f"{grants} and {refused}")
        again = api("POST", f"/legal-exports/{export_id}/links", headers=priya).json()
        with db() as conn:
            conn.execute("update legal_export_link set expires_at = now() - interval '1 minute' where id = %s", (again["link_id"],))
        check("an expired link is refused", api("GET", again["download_path"]).status_code == 404)

        heading("Opened with the recipient's own tool")
        public = api("GET", "/legal-exports/signing-key").json()["public_key"]
        package = Path(tempfile.mkdtemp()) / "package.mlep"
        package.write_bytes(package_bytes)
        opened = Path(tempfile.mkdtemp())
        report = open_legal_package.open_package(package, passphrase, opened, public)
        check("the package opens, the manifest's signature verifies against the published key, and every file matches",
              report["ok"] and report["signed"] and report["files"] == 2, "; ".join(report["problems"]))
        check("the files are the stored files, byte for byte",
              all((opened / p).read_bytes() == data["bodies"][Path(p).name] for p in expected))
        audit_csv = (opened / "audit-trail.csv").read_text()
        check("the audit trail of the named versions is included", org.people["member"] in audit_csv)
        erased = json.loads((opened / "erased.json").read_text())
        check("records already erased are listed as erased", any(e["record_id"].startswith("erased-") for e in erased["records_erased_by_key_destruction"]))
        custody = json.loads((opened / "chain-of-custody.json").read_text())
        check("the chain of custody names who asked, approved and confirmed, and the demand",
              [s["by"] for s in custody["steps"][:3]] == ["Priya", "Ravi", "Records officer"]
              and custody["demand"]["reference"] == "KB-2026-004411", str([s["by"] for s in custody["steps"]]))
        wrong = open_legal_package.open_package(package, "not-the-passphrase", Path(tempfile.mkdtemp()), public)
        check("a wrong passphrase opens nothing", not wrong["ok"])
        damaged = bytearray(package_bytes)
        damaged[len(damaged) // 2] ^= 0xFF
        bad = Path(tempfile.mkdtemp()) / "bad.mlep"
        bad.write_bytes(bytes(damaged))
        check("a package with one changed byte is refused", not open_legal_package.open_package(bad, passphrase, Path(tempfile.mkdtemp()), public)["ok"])
        short = Path(tempfile.mkdtemp()) / "short.mlep"
        short.write_bytes(package_bytes[:-40])
        check("a package cut short is refused", not open_legal_package.open_package(short, passphrase, Path(tempfile.mkdtemp()), public)["ok"])
        other_key = open_legal_package.open_package(package, passphrase, Path(tempfile.mkdtemp()), "00" * 32)
        check("a signature checked against somebody else's key fails", not other_key["ok"] and not other_key["signed"])

        # Altered after the platform made it: unpack, change a file, repack and encrypt with the same passphrase. The
        # encryption is valid, so only the manifest can notice.
        from app import package_crypto
        work = Path(tempfile.mkdtemp())
        plain = work / "p.zip"
        package_crypto.decrypt_file(package, plain, passphrase)
        altered = work / "a.zip"
        with zipfile.ZipFile(plain) as zin, zipfile.ZipFile(altered, "w") as zout:
            for item in zin.infolist():
                content = zin.read(item.filename)
                zout.writestr(item.filename, b'{"record_id": "CHANGED"}' if item.filename.endswith("part-0.json") else content)
        forged = work / "forged.mlep"
        package_crypto.encrypt_file(altered, forged, passphrase)
        forged_report = open_legal_package.open_package(forged, passphrase, Path(tempfile.mkdtemp()), public)
        check("a file changed inside a validly encrypted package is caught by the manifest",
              not forged_report["ok"] and any("part-0.json" in p for p in forged_report["problems"]), "; ".join(forged_report["problems"])[:120])

        heading("Bytes altered after sealing are never handed over")
        altered_set = seal_altered_version(org, data["schema_id"])
        failing = through_to_confirmed(priya, ravi, records, hold_id, [altered_set["dataset_id"]], demand_reference="KB-2026-004499")
        row = wait_for(failing, "ready")
        check("the production stops and says which file differs", row["status"] == "failed" and "part-0.json" in (row["failure"] or "")
              and "differ" in (row["failure"] or ""), (row["failure"] or "")[:140])
        with db() as conn:
            noted = conn.execute("select count(*) as n from access_decision where tenant_id = %s and purpose like %s and not allowed",
                                 (org.id, "legal export KB-2026-004499: production failed")).fetchone()["n"]
            left = conn.execute("select count(*) as n from legal_export where id = %s and package_key is not null", (failing,)).fetchone()["n"]
        check("the failure is in the audit trail and no package exists", noted == 1 and left == 0)

        heading("Nothing outlives its retention")
        third = through_to_confirmed(priya, ravi, records, hold_id, [data["dataset_id"]], demand_reference="KB-2026-004500")
        wait_for(third, "ready")
        third_key = status(third)["package_key"]
        check("a second package exists", object_exists(third_key))
        with db() as conn:
            conn.execute("update legal_export set expires_at = now() - interval '1 hour' where id = %s", (export_id,))
        swept = api("POST", "/lifecycle/sweep", headers=priya).json()
        check("the sweep deletes a package whose retention has ended", export_id in swept["exports"]["expired"], str(swept["exports"]["expired"]))
        check("the file is gone from storage", not object_exists(f"{export_id}.mlep"))
        check("its links stop working", api("GET", link["download_path"]).status_code == 404)
        still = api("GET", f"/legal-exports/{export_id}/manifest", headers=priya).json()
        check("but the manifest and its hash are kept", len(still["files"]) == 2 and still["manifest_sha256"])
        check("a package still inside its retention is untouched", object_exists(third_key))

        heading("Deleting the organisation takes what is left, and keeps a summary")
        finish_org(org, priya, ravi)
        with db() as conn:
            record = conn.execute("select exports from tenant_deletion_record where original_tenant_id = %s", (org.id,)).fetchone()
        check("the deletion record summarises each export by demand, recipient and manifest hash",
              record is not None and len(record["exports"]) == 3
              and {e["demand_reference"] for e in record["exports"]} == {"KB-2026-004411", "KB-2026-004499", "KB-2026-004500"}
              and "ruth.aldous" not in json.dumps(record["exports"]), str(record["exports"])[:150] if record else "no record")
        check("the unexpired package was deleted with the organisation", not object_exists(third_key))
    finally:
        drop_org(org)
    return summary("U103")


if __name__ == "__main__":
    sys.exit(main())
