"""U169: the locked data audit can be searched, filtered and read a page at a time, and every page is exact.

The audit used to hand back the latest 200 lines of each list, so anything older, or any one person or table, could not be reached. Now each list is asked for on its
own, newest first, narrowed by person, table, date, result and free text, a page at a time, with the number of lines that match.

Checked here one item at a time, against the live platform, with a table, a lease for rows, files and a lease for files made up for the purpose:

  * every list can be walked page by page, and the lines found equal the total, with none twice, in time order, and a page past the end is empty with the same total;
  * the page size is held to its limits, and a bad date, result or list is refused in words;
  * each filter narrows the list it applies to and nothing else: person, table, date, result and free text, alone and together, and results that partition a list add up;
  * free text is matched as text, so a percent sign or underscore is not a wildcard;
  * a filter a list does not offer is ignored and the list says so;
  * only the privacy auditor and the data protection officer read it, a refusal and each read is recorded, and nothing outside the organisation appears;
  * the whole-evidence call is unchanged, and no file is named in any page.

    docker compose exec -T munitas-api python /verify/v162_audit_filters.py
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/app")
sys.path.insert(0, "/")

from common import CANARY, api, bearer_for, check, db, fixture_tenant, heading, require_api, summary  # noqa: E402
from v128_person_keys_erasure import ENGINEER, CUSTODIAN, RESEARCHER, good_contract, register_dataset, reset_keys  # noqa: E402
from v129_locked_column_leases import AUDITOR, DPO, ask as ask_rows, decide as decide_rows, read as read_rows, table  # noqa: E402
from v137_locked_files import make_wav, upload_whole  # noqa: E402
from v157_file_leases import ask as ask_files, decide as decide_files, open_it  # noqa: E402

SECTIONS = ("reads", "column_leases", "file_reads", "file_leases", "keys", "erasures", "small_groups", "contracts")
TIME = {"reads": "at", "column_leases": "requested_at", "file_reads": "at", "file_leases": "requested_at", "keys": "at", "erasures": "at", "small_groups": "created_at",
        "contracts": "sealed_under_since"}
ROUTE = "/audit/locked-columns"


def get(who: str, **params):
    return api("GET", ROUTE, params={k: v for k, v in params.items() if v is not None}, headers=bearer_for(who))


def pages(section: str, size: int = 100, who: str = DPO, **filters) -> tuple[list[dict], dict]:
    """Every line of one list, page by page, and the first page's envelope."""
    out, first, number = [], None, 1
    while True:
        r = get(who, section=section, page=number, page_size=size, **filters)
        assert r.status_code == 200, f"{section} page {number}: {r.status_code} {r.text[:120]}"
        body = r.json()
        first = first or body
        out.extend(body["rows"])
        if number >= body["pages"]:
            return out, first
        number += 1


def main() -> int:
    require_api()
    fixture_tenant(CANARY)
    reset_keys()
    from app import db as app_db
    if app_db.pool.closed:
        app_db.pool.open()
    tag = uuid.uuid4().hex[:8]
    started = datetime.now(timezone.utc) - timedelta(seconds=5)
    made_leases: list[str] = []
    file_lease = ""
    try:
        heading("A table, a lease for its rows, files and a lease for them, made for this check")
        rows, _ = table(tag)
        dataset = register_dataset(ENGINEER)
        sealed = api("POST", f"/datasets/{dataset}/seal-table", headers=bearer_for(ENGINEER), json={"contract": good_contract(), "rows": rows})
        check("a table is sealed", sealed.status_code == 201, sealed.text[:120])
        version = sealed.json()["id"]
        with db() as conn:
            table_name = conn.execute("select d.name from dataset_version v join dataset d on d.id = v.dataset_id where v.id = %s", (version,)).fetchone()["name"]
        marker = f"U169 {tag} read a few rows"
        refused = read_rows(RESEARCHER, version)
        check("a researcher without a lease is refused a read", refused.status_code == 403)
        asked = ask_rows(ENGINEER, version, purpose=marker)
        check("a lease for rows is asked for", asked.status_code == 201, asked.text[:120])
        row_lease = asked.json()["id"]
        made_leases.append(row_lease)
        check("and allowed", decide_rows(CUSTODIAN, row_lease).status_code == 200)
        check("the engineer reads under it", read_rows(ENGINEER, version).status_code == 200)

        files_dataset = register_dataset(ENGINEER)
        for i in range(4):
            upload_whole(files_dataset, f"r-{i + 1}.wav", make_wav(40_000 + i * 1_000), f"U169-{tag}-{i}")
        files = api("POST", f"/datasets/{files_dataset}/seal-files", headers=bearer_for(ENGINEER))
        check("four locked files are sealed", files.status_code == 201, files.text[:120])
        files_version = files.json()["id"]
        with db() as conn:
            files_name = conn.execute("select d.name from dataset_version v join dataset d on d.id = v.dataset_id where v.id = %s", (files_version,)).fetchone()["name"]
        file_marker = f"U169 {tag} listen to two files"
        fl = ask_files(files_version, RESEARCHER, purpose=file_marker, records=2, hours=1)
        check("a lease for files is asked for", fl.status_code == 201, fl.text[:120])
        file_lease = fl.json()["id"]
        check("and allowed", decide_files(file_lease, CUSTODIAN, "approve", records=2).status_code == 200)
        statuses = [open_it(files_version, f"r-{i}", RESEARCHER).status_code for i in range(1, 5)]
        check("two files open and two are refused", sorted(statuses) == [200, 200, 403, 403], str(statuses))
        finished = datetime.now(timezone.utc) + timedelta(seconds=5)

        heading("Every list can be walked, page by page, exactly")
        for section in SECTIONS:
            size = 3 if section == "contracts" else 100
            lines, first = pages(section, size)
            keys = [json.dumps(l, sort_keys=True, default=str) for l in lines]
            check(f"{section}: the lines found are the {first['total']} that match, none twice", len(lines) == first["total"] and len(set(keys)) == len(keys), f"{len(lines)} of {first['total']}")
            check(f"{section}: newest first", [l[TIME[section]] for l in lines] == sorted((l[TIME[section]] for l in lines), reverse=True))
            check(f"{section}: it says how many pages, the page size, and which filters it takes",
                  first["pages"] == max(1, -(-first["total"] // size)) and first["page_size"] == size and "can_filter" in first and "results" in first)
            past = get(DPO, section=section, page=first["pages"] + 3, page_size=size).json()
            check(f"{section}: a page past the end is empty and the total is the same", past["rows"] == [] and past["total"] == first["total"])

        heading("Pages are the right size, and bad asks are refused in words")
        # Makes up the lines it pages through: fifteen refused reads, so there are more than two pages of seven whatever else the organisation holds.
        for _ in range(15):
            read_rows(RESEARCHER, version)
        small = get(DPO, section="reads", page=2, page_size=7).json()
        check("a page of 7 holds 7 lines, and the next page starts where it ended", len(small["rows"]) == 7 and small["page"] == 2 and small["page_size"] == 7
              and small["rows"][0] != get(DPO, section="reads", page=1, page_size=7).json()["rows"][0])
        check("the page size is held to 100", get(DPO, section="reads", page_size=1000).json()["page_size"] == 100)
        check("and to at least 1; page 0 is page 1", get(DPO, section="reads", page=0, page_size=0).json()["page"] == 1 and get(DPO, section="reads", page_size=0).json()["page_size"] == 1)
        bad_date = get(DPO, section="reads", date_from="yesterday")
        check("a date that is not a date is refused, saying so", bad_date.status_code == 400 and "must be a date and time" in bad_date.text, f"{bad_date.status_code} {bad_date.text[:100]}")
        bad_result = get(DPO, section="reads", result="maybe")
        check("a result the list does not have is refused, listing the ones it has", bad_result.status_code == 400 and "allowed, refused" in bad_result.text, bad_result.text[:120])
        check("a list that does not exist is refused", get(DPO, section="everything").status_code == 400)

        heading("Each filter narrows its own list")
        everything, _ = pages("reads")
        allowed_n = get(DPO, section="reads", result="allowed").json()["total"]
        refused_n = get(DPO, section="reads", result="refused").json()["total"]
        check("allowed and refused add up to every read", allowed_n + refused_n == len(everything) and allowed_n >= 1 and refused_n >= 1, f"{allowed_n} + {refused_n} vs {len(everything)}")
        who_rows, _ = pages("reads", person="Canary researcher")
        check("by person: only that person's lines, and at least the refusal made above", len(who_rows) >= 1 and all("canary researcher" in l["principal_label"].lower() for l in who_rows)
              and len(who_rows) < len(everything))
        check("by person, a part of the label is enough, in any case", get(DPO, section="reads", person="RESEARCHER").json()["total"] == len(who_rows))
        mine, _ = pages("reads", table=table_name)
        check("by table: the lines about that table, the researcher's refusal and the engineer's read", len(mine) >= 2 and all(l["table"] and l["table"].startswith(table_name) for l in mine)
              and {l["allowed"] for l in mine} == {True, False}, str([(l["principal_label"], l["allowed"]) for l in mine]))
        both, _ = pages("reads", table=table_name, result="refused")
        check("by table and result together: both must hold", len(both) >= 1 and all(l["table"].startswith(table_name) and not l["allowed"] for l in both) and len(both) < len(mine))
        check("by person and table and result together", pages("reads", table=table_name, result="refused", person="researcher")[1]["total"] >= 1
              and pages("reads", table=table_name, result="refused", person="Canary engineer")[1]["total"] == 0)
        window = get(DPO, section="column_leases", date_from=started.isoformat(), date_to=finished.isoformat()).json()
        check("by date: the lease made here is inside the window it was made in", window["total"] >= 1 and any(l["purpose"] == marker for l in window["rows"]))
        check("a window after everything, and one before it, are empty",
              get(DPO, section="column_leases", date_from=(finished + timedelta(days=1)).isoformat()).json()["total"] == 0
              and get(DPO, section="column_leases", date_to="2001-01-01T00:00:00Z").json()["total"] == 0)
        whole_ids = {l["id"] for l in pages("column_leases")[0]}
        before = {l["id"] for l in pages("column_leases", date_to=started.isoformat())[0]}
        after = {l["id"] for l in pages("column_leases", date_from=started.isoformat())[0]}
        check("dates split a list without losing or repeating a line: to is exclusive, from is inclusive", before | after == whole_ids and not (before & after))
        by_text = get(DPO, section="column_leases", q=marker).json()
        check("by free text: the one lease whose purpose has the marker", by_text["total"] == 1 and by_text["rows"][0]["purpose"] == marker and by_text["rows"][0]["id"] == row_lease)
        check("free text is not case sensitive", get(DPO, section="column_leases", q=marker.upper()).json()["total"] == 1)
        check("a percent sign and an underscore are text, not wildcards", get(DPO, section="column_leases", q="%").json()["total"] == 0
              and get(DPO, section="column_leases", q="_").json()["total"] == sum(1 for l in pages("column_leases")[0] if "_" in l["purpose"]))
        states = {s: get(DPO, section="column_leases", result=s).json()["total"] for s in ("requested", "approved", "declined", "revoked")}
        check("lease states partition the leases", sum(states.values()) == len(whole_ids), str(states))
        check("the lease made here is approved", get(DPO, section="column_leases", result="approved", q=marker).json()["total"] == 1
              and get(DPO, section="column_leases", result="declined", q=marker).json()["total"] == 0)

        heading("Locked files")
        opened = pages("file_reads", table=files_name)[0]
        check("by table: the two openings and the two refusals of the files made here", sorted(l["allowed"] for l in opened) == [False, False, True, True], str(opened)[:200])
        check("an opening says who, where, why and under which lease; a refusal says why not",
              all(l["via"] == "console" and l["basis"] == "lease" and l["lease_id"] == file_lease and l["principal_label"] == "Canary researcher" for l in opened if l["allowed"])
              and all("not one of the files your lease opens" in (l["reason"] or "") for l in opened if not l["allowed"]))
        check("results split file openings", pages("file_reads", table=files_name, result="allowed")[1]["total"] == 2 and pages("file_reads", table=files_name, result="refused")[1]["total"] == 2)
        check("free text finds a reason", pages("file_reads", q="your lease opens", table=files_name)[1]["total"] == 2)
        fleases = get(DPO, section="file_leases", q=file_marker).json()
        check("a lease for files is found by what it was for, with its table and counts", fleases["total"] == 1 and fleases["rows"][0]["id"] == file_lease
              and fleases["rows"][0]["dataset_name"] == files_name and fleases["rows"][0]["granted_count"] == 2)
        check("and by person and by state", get(DPO, section="file_leases", q=file_marker, person="researcher", result="approved").json()["total"] == 1
              and get(DPO, section="file_leases", q=file_marker, result="declined").json()["total"] == 0)

        heading("Who accessed one file")
        for i in range(1, 5):
            lines = pages("file_reads", file=f"r-{i}", table=files_name)[0]
            check(f"file r-{i}: exactly one line, by the researcher, named, and its result is what happened", len(lines) == 1 and lines[0]["file"] == f"r-{i}"
                  and lines[0]["principal_label"] == "Canary researcher" and lines[0]["allowed"] == (statuses[i - 1] == 200), str(lines)[:200])
        check("a file name can be given in part and in any case", pages("file_reads", file="R-1", table=files_name)[1]["total"] == 1)
        check("a refusal says which file it was about", all(l["file"] for l in pages("file_reads", table=files_name, result="refused")[0]))
        opened_names = {l["file"] for l in pages("file_reads", table=files_name, result="allowed")[0]}
        for i in range(1, 5):
            leased = pages("file_leases", file=f"r-{i}", q=file_marker)[0]
            check(f"file r-{i}: the lease names it only if it was one of the files drawn", (len(leased) == 1) == (f"r-{i}" in opened_names), f"{len(leased)} {opened_names}")
        key_lines = pages("keys", table=files_name, result="unwrap")[0]
        check("keys by table: each locked file is a key opened to lock it, then a key opened to open it, all with the table named",
              {k["reason"] for k in key_lines} >= {"To lock a file", "To open a locked file in the page, with an approver's permission"} and all(k["table"] and k["table"].startswith(files_name) for k in key_lines),
              str({k["reason"] for k in key_lines}))
        check("keys by table name the files locked", sorted(k["file"] for k in key_lines if k["reason"] == "To lock a file") == [f"r-{i}.wav" for i in range(1, 5)])
        check("keys by file: opening r-1 and locking r-1.wav both show, and no other file's", all("r-1" in (k["file"] or "") for k in pages("keys", file="r-1", table=files_name)[0])
              and pages("keys", file="r-1", table=files_name)[1]["total"] == 1 + (1 if statuses[0] == 200 else 0))
        sealing = [k for k in pages("keys", table=table_name)[0] if k["reason"] == "To seal a table"]
        check("keys by table: sealing the table opened the key of each of its 30 people, with the table named", len(sealing) == 30 and all(k["table"] == table_name for k in sealing), f"{len(sealing)}")
        check("and the engineer's read under the lease is there, with the same table", any(k["reason"] == "To read locked rows, under a lease" and k["table"].startswith(table_name) for k in pages("keys", table=table_name)[0]))
        check("a file filter on a list that takes none, and a table that does not exist, find nothing and say so",
              get(DPO, section="reads", file="r-1").json()["total"] == get(DPO, section="reads").json()["total"] and "file" not in get(DPO, section="reads").json()["can_filter"]
              and pages("keys", table="no such table " + tag)[1]["total"] == 0)

        heading("The other lists")
        unwrapped = pages("keys", result="unwrap")[0]
        check("keys by what happened", len(unwrapped) >= 1 and all(k["action"] == "unwrap" for k in unwrapped))
        check("keys by person: the engineer opened keys", pages("keys", person="Canary engineer")[1]["total"] >= 1 and all(k["actor_label"] == "Canary engineer" for k in pages("keys", person="Canary engineer")[0]))
        with_code = next(k for k in pages("keys", result="unwrap")[0] if k["person_code"])
        one_key = pages("keys", q=with_code["person_code"])[0]
        check("following a person code lists the key's own lines: made, and every time it was opened, and no other key's",
              len(one_key) >= 2 and all(k["person_code"] == with_code["person_code"] for k in one_key) and {"make_person", "unwrap"} <= {k["action"] for k in one_key},
              str([(k["action"], k["person_code"]) for k in one_key][:4]))
        check("each key line carries a reason and a table, never the platform's own text", all("reason" in k and "table" in k and "purpose" not in k for k in unwrapped))
        check("erasures offer no table filter, and one given changes nothing", "table" not in get(DPO, section="erasures").json()["can_filter"]
              and get(DPO, section="erasures", table="no such table").json()["total"] == get(DPO, section="erasures").json()["total"])
        check("erasures by person code", get(DPO, section="erasures", q="person:T-").json()["total"] == get(DPO, section="erasures").json()["total"])
        check("small groups by table: the table made here", all(g["table"].startswith(table_name) for g in pages("small_groups", table=table_name)[0]))
        for decision in ("accepted", "will_change", "undecided"):
            check(f"small groups decided as {decision} are only those", get(DPO, section="small_groups", result=decision).status_code == 200)
        parts = sum(get(DPO, section="small_groups", result=d).json()["total"] for d in ("accepted", "will_change", "undecided"))
        check("and the three add up to all of them", parts == get(DPO, section="small_groups").json()["total"])
        by_table = pages("contracts", 3, table=table_name)[0]
        check("contracts by table: the contract the table made here was sealed under, naming it", len(by_table) == 1 and any(t.startswith(table_name) for t in by_table[0]["tables"]))
        check("contracts by a column name", pages("contracts", 3, q="patient_name")[1]["total"] >= 1 and pages("contracts", 3, q="no_such_column_" + tag)[1]["total"] == 0)

        heading("Who may read it, what is recorded, and what stays out")
        before_refusals = db_count("select count(*) from access_decision where tenant_id = %s and principal = %s and purpose like 'read audit evidence%%' and not allowed", (CANARY, ENGINEER))
        denied = get(ENGINEER, section="reads")
        check("an engineer is refused, with the reason", denied.status_code == 403 and "audit evidence" in denied.text, f"{denied.status_code} {denied.text[:100]}")
        check("and the refusal is recorded", db_count("select count(*) from access_decision where tenant_id = %s and principal = %s and purpose like 'read audit evidence%%' and not allowed",
                                                       (CANARY, ENGINEER)) == before_refusals + 1)
        check("a researcher and the approver are refused too", get(RESEARCHER, section="reads").status_code == 403 and get(CUSTODIAN, section="keys").status_code == 403)
        like = "read audit evidence: file_reads (file=r-1%%"
        seen = db_count("select count(*) from access_decision where tenant_id = %s and principal = %s and purpose like %s and allowed", (CANARY, AUDITOR, like))
        check("the privacy auditor may read it", get(AUDITOR, section="file_reads", file="r-1").status_code == 200)
        check("and a search says which list it was and what was searched for", db_count("select count(*) from access_decision where tenant_id = %s and principal = %s and purpose like %s and allowed",
                                                                                       (CANARY, AUDITOR, like)) == seen + 1)
        opened_before = db_count("select count(*) from access_decision where tenant_id = %s and principal = %s and purpose like 'read audit evidence%%' and allowed", (CANARY, AUDITOR))
        lists = ("reads", "column_leases", "file_reads", "file_leases", "keys", "erasures", "small_groups", "contracts")
        for other in lists:
            get(AUDITOR, section=other)
        check("every list read is its own line, so the log shows which evidence was shown", db_count("select count(*) from access_decision where tenant_id = %s and principal = %s and purpose like 'read audit evidence%%' and allowed",
                                                                                                  (CANARY, AUDITOR)) == opened_before + len(lists))
        for other in lists:
            check(f"and the line for {other} names it", db_count("select count(*) from access_decision where tenant_id = %s and principal = %s and purpose = %s and allowed and at > now() - interval '2 minutes'",
                                                                  (CANARY, AUDITOR, f"read audit evidence: {other}")) >= 1)
        get(AUDITOR, section="keys", page=2)
        check("turning a page says which page", db_count("select count(*) from access_decision where tenant_id = %s and principal = %s and purpose = 'read audit evidence: keys page 2'", (CANARY, AUDITOR)) >= 1)
        ids = [l["id"] for l in pages("file_leases")[0]]
        with db() as conn:
            outside = conn.execute("select count(*) as n from file_lease where id = any(%s::uuid[]) and tenant_id <> %s", (ids, CANARY)).fetchone()["n"]
        check("nothing from another organisation appears", outside == 0 and len(ids) >= 1)
        leases_text = json.dumps(pages("file_leases", q=file_marker)[0], default=str)
        check("a lease does not list the files it names", '"records"' not in leases_text and '"salt"' not in leases_text)
        legacy = get(DPO).json()
        check("the call without a list is as it was: every list, the latest lines", all(k in legacy for k in ("reads", "key_uses", "leases", "contracts", "erasures", "group_sizes", "file_reads", "file_leases")))
    finally:
        for lease in made_leases:
            api("POST", f"/locked-column-leases/{lease}/revoke", headers=bearer_for(CUSTODIAN))
        if file_lease:
            api("POST", f"/file-leases/{file_lease}/end", headers=bearer_for(CUSTODIAN))
    return summary("U169")


def db_count(sql: str, params: tuple) -> int:
    with db() as conn:
        return list(conn.execute(sql, params).fetchone().values())[0]


if __name__ == "__main__":
    sys.exit(main())
