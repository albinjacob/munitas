r"""U175: the test data generator makes tables that are reproducible, realistic, safe, correctly described and checkable.

Runs on the host with no database and no stack, in about a minute, at 20,000 rows or fewer (the large tier is never part of this):

    .venv\Scripts\python.exe verify\v167_testdata_generator.py

Everything later in the large tables work is judged against what this generator makes, so this check is about the generator itself:

  * the same seed gives the same rows whatever the size of a batch, and the same bytes in every file on a second run;
  * every file, in every format, reads back with a different reader to what the truth file says, and a damaged file is noticed;
  * a second engine (DuckDB) counts a Parquet file the same way the truth file does;
  * nothing can belong to a real person: emails, phone and national numbers are in ranges reserved for fiction or never issued;
  * the rows make sense: nobody is admitted before they were born, a delivery is never a man's, a first name matches the person's sex;
  * every contract is valid under the standard's own schema, and the platform's own contract check says what is expected of it today;
  * the faulty copies are damaged where the answers file says, in the rows and on the lines it names, and nowhere else;
  * the large tier cannot be started by accident, and a table that would not fit on the disk is refused before anything is written.
"""

from __future__ import annotations

import csv
from datetime import date
import hashlib
import importlib.util
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pyarrow as pa  # noqa: E402
import pyarrow.compute as pc  # noqa: E402

from scripts.testdata import contracts, faults, formats, generate, pools, tables, truth  # noqa: E402

COUNTS_MARKER = "##MUNITAS-CHECK-COUNTS##"
_results: list[tuple[str, bool | None, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    _results.append((label, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail and (not ok or len(detail) <= 90) else ""))
    return ok


def heading(text: str) -> None:
    print(f"\n{text}\n" + "-" * len(text))


def whole(spec: tables.TableSpec, rows: int, seed: int = 7, batches: int = 1) -> pa.Table:
    ctx = spec.context(rows, seed)
    size = -(-ctx.rows // batches)
    parts = [spec.build(first, min(size, ctx.rows - first), ctx) for first in range(0, ctx.rows, size)]
    return pa.concat_tables(parts)


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="munitas-testdata-"))
    try:
        run(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    passed = sum(1 for _, ok, _ in _results if ok is True)
    failed = sum(1 for _, ok, _ in _results if ok is False)
    print(f"\nU175: {passed} passed, {failed} failed, 0 skipped")
    print(f"{COUNTS_MARKER} " + json.dumps({"passed": passed, "failed": failed, "skipped": 0}, separators=(",", ":")))
    return 1 if failed else 0


def run(work: Path) -> None:
    heading("The same seed gives the same rows, whatever the size of a batch")
    for name, spec in tables.SPECS.items():
        one, four = whole(spec, 4000, batches=1), whole(spec, 4000, batches=4)
        check(f"{name}: one batch and four batches are the same table", one.equals(four))
    a, b, c = whole(tables.SPECS["admissions"], 800, seed=7), whole(tables.SPECS["admissions"], 800, seed=7), whole(tables.SPECS["admissions"], 800, seed=8)
    check("the same seed gives the same table and another seed does not", a.equals(b) and not a.equals(c))

    heading("Every format, every table: reproducible bytes, and each file reads back to its truth")
    first, second = work / "first", work / "second"
    for name in tables.SPECS:
        generate.make(name, 1000, "all", first, quiet=True)
        generate.make(name, 1000, "all", second, quiet=True)
    differing = []
    for manifest in sorted(first.glob("*/*/manifest.json")):
        mine = {f["variant"]: f["sha256"] for f in json.loads(manifest.read_text())["files"]}
        other = {f["variant"]: f["sha256"] for f in json.loads((second / manifest.relative_to(first)).read_text())["files"]}
        differing += [f"{manifest.parent.parent.name}/{v}" for v in mine if mine[v] != other.get(v)]
        if (manifest.parent / "truth.json").read_text() != (second / manifest.relative_to(first).parent / "truth.json").read_text():
            differing.append(f"{manifest.parent.parent.name}/truth")
    files = sum(len(json.loads(m.read_text())["files"]) for m in first.glob("*/*/manifest.json"))
    check(f"a second run gives the same bytes in all {files} files and the same truth files", not differing, ", ".join(differing[:6]))
    for name in tables.SPECS:
        problems = generate.verify(first / name / str(tables.SPECS[name].fixed_rows or 1000))
        check(f"{name}: every file agrees with its truth when read back by another reader", not problems, "; ".join(problems[:2]))
    folder = first / "patients" / "1000"
    damaged = work / "damaged"
    shutil.copytree(folder, damaged)
    csv_path = damaged / "csv" / "patients.csv"
    csv_path.write_bytes(csv_path.read_bytes().replace(b"REG00000007", b"REG00000008", 1))
    check("a damaged file is noticed", bool(generate.verify(damaged, ["csv"])))

    heading("A second engine counts the same")
    import duckdb
    for name, column in (("admissions", "total_cost"), ("transactions", "amount"), ("fx_ticks", "bid"), ("vehicle_readings", "speed_kph")):
        generate.make(name, 20000, ("parquet",), first, quiet=True)
        expected = json.loads((first / name / "20000" / "truth.json").read_text())
        path = (first / name / "20000" / "parquet" / f"{name}.parquet").as_posix()
        spec = tables.SPECS[name]
        keys = ", ".join(spec.key_columns())
        distinct = f"(SELECT count(*) FROM (SELECT DISTINCT {keys} FROM '{path}'))" if keys else "NULL"
        rows, total, different = duckdb.sql(f"SELECT count(*), sum({column}), {distinct} FROM '{path}'").fetchone()
        check(f"{name}: DuckDB counts {rows:,} rows, the same total of {column} and the same number of keys as the truth file",
              rows == expected["rows"] and abs(float(total) - float(expected["columns"][column]["sum"])) <= 1e-6 * max(1.0, abs(float(total)))
              and (different is None or different == expected["key"]["distinct"]), f"{rows} / {total} / {different}")

    heading("Nothing can belong to a real person")
    people = whole(tables.SPECS["patients"], 20000)
    customers = whole(tables.SPECS["customers"], 20000)
    emails = people["email"].to_pylist() + customers["email"].to_pylist()
    check("every email is at a domain reserved for documentation", all(re.fullmatch(r"[a-z0-9.]+@example\.(com|org|net)", e) for e in emails))
    phone = re.compile(r"\+44 7700 900\d{3}|\+1 202-555-01\d{2}|\+353 1 000 \d{4}|\+91 00000 \d{5}")
    check("every phone number is in a range reserved for fiction or never allocated", all(phone.fullmatch(p) for p in people["phone"].to_pylist()))
    national = re.compile(r"QQ\d{6}[ABCD]|9\d{2}-\d{2}-\d{4}|1\d{3} \d{4} \d{4}")
    check("every national number is of a kind that is never issued", all(national.fullmatch(n) for n in customers["national_id"].to_pylist()))
    check("every record number is of the local scheme", all(re.fullmatch(r"MRN\d{9}", n) for n in people["patient_number"].to_pylist())
          and all(re.fullmatch(r"CUS\d{9}", n) for n in customers["customer_number"].to_pylist()))
    check("a patient and a customer with the same number are different people",
          people["full_name"].to_pylist()[:200] != customers["full_name"].to_pylist()[:200])

    heading("The rows make sense")
    sex_of = {}
    for row in csv.DictReader(open(pools.DIR / "first_names.csv", encoding="utf-8")):
        sex_of.setdefault(row["name"], set()).add(row["sex"])
    mismatched = [1 for n, s in zip(people["full_name"].to_pylist(), people["sex"].to_pylist())
                  if s in ("F", "M") and s not in sex_of.get(n.split(" ")[0].strip('"'), {s})]
    check("a first name belongs to the person's sex", not mismatched, f"{len(mismatched)} do not")
    women = people["sex"].to_pylist().count("F") / people.num_rows
    check("about half the people are women (50.4 percent expected)", 0.48 <= women <= 0.53, f"{women:.3f}")
    ages = np.array([(tables.TODAY - d) / 365.25 for d in pc.cast(people["date_of_birth"], pa.int32()).to_numpy()])
    check("about a fifth of the patients are children and none is older than 95", 0.17 <= (ages < 18).mean() <= 0.23 and ages.max() < 95, f"{(ages < 18).mean():.3f}")
    prefixes = [p.split(" ")[0] if p.startswith("+44") or p.startswith("+353") or p.startswith("+91") else "+1" for p in people["phone"].to_pylist()]
    share = {k: prefixes.count(k) / len(prefixes) for k in ("+44", "+1", "+353", "+91")}
    check("the mix of countries follows the weights (50, 30, 8 and 12 percent)",
          all(abs(share[k] - w) < 0.03 for k, w in zip(("+44", "+1", "+353", "+91"), (0.50, 0.30, 0.08, 0.12))), str({k: round(v, 3) for k, v in share.items()}))
    check("about two percent of addresses run over two lines and some names carry a quote",
          0.01 <= sum("\n" in (a or "") for a in people["address_line"].to_pylist()) / people.num_rows <= 0.03
          and sum('"' in n for n in people["full_name"].to_pylist()) >= 1)

    spec = tables.SPECS["admissions"]
    admissions = whole(spec, 20000)
    ctx = spec.context(20000, 7)
    held = whole(tables.SPECS["patients"], ctx.people)
    born = dict(zip(held["patient_number"].to_pylist(), zip(pc.cast(held["date_of_birth"], pa.int32()).to_pylist(), held["sex"].to_pylist())))
    codes = {r["code"]: r for r in pools.diagnoses()}
    early = old = wrong_sex = wrong_age = bad_stay = 0
    for patient, admitted, left, code, age, stay, cost, outcome in zip(*[admissions[c].to_pylist() for c in (
            "patient_number", "admitted_at", "discharged_at", "primary_diagnosis", "age_at_admission", "length_of_stay_days", "total_cost", "outcome")]):
        dob, sex = born[patient]
        days_old = (admitted.date() - date(1970, 1, 1)).days - dob
        early += days_old < 0
        old += age != int(days_old / 365.25)
        if code.startswith("O") and not (sex == "F" and 16 <= age <= 48):
            wrong_sex += 1
        if code in ("Z38.0", "P07.3", "P59.9", "Q21.0") and days_old > 28:
            wrong_age += 1
        if code in ("C61", "N40") and sex != "M":
            wrong_sex += 1
        bad_stay += (left is None) != (outcome == "in hospital") or (left is not None and (left < admitted or stay < 0 or cost <= 0))
    check("nobody is admitted before they were born", early == 0, f"{early}")
    check("the age at admission is the age on the day", old == 0, f"{old}")
    check("a delivery is a woman of child-bearing age, and prostate conditions are a man's", wrong_sex == 0, f"{wrong_sex}")
    check("a newborn's conditions are only given to a baby", wrong_age == 0, f"{wrong_age}")
    check("a stay ends after it starts, costs something, and is open only when the person is still in hospital", bad_stay == 0, f"{bad_stay}")
    check("every diagnosis is in the lookup, and a good spread of them is used",
          set(admissions["primary_diagnosis"].to_pylist()) <= set(codes) and len(set(admissions["primary_diagnosis"].to_pylist())) >= 60)
    check("the discharge is missing for a small share still in hospital", 0.0005 < admissions["discharged_at"].null_count / admissions.num_rows < 0.02,
          f"{admissions['discharged_at'].null_count / admissions.num_rows:.4f}")

    occupancy = whole(tables.SPECS["ward_occupancy"], 20000)
    check("a ward's occupied beds never exceed its open beds",
          pc.all(pc.less_equal(occupancy["beds_occupied"], occupancy["beds_open"])).as_py())
    check("the two columns of the key are never repeated together",
          len({(w, h) for w, h in zip(occupancy["ward_code"].to_pylist(), occupancy["hour_start"].to_pylist())}) == occupancy.num_rows)
    ticks = whole(tables.SPECS["fx_ticks"], 20000)
    check("a price's ask is always above its bid", pc.all(pc.greater(ticks["ask"], ticks["bid"])).as_py())
    readings = whole(tables.SPECS["vehicle_readings"], 20000)
    check("a vehicle reading has a believable speed and fuel level", pc.max(readings["speed_kph"]).as_py() <= 140 and 0 <= pc.min(readings["fuel_pct"]).as_py()
          and pc.max(readings["fuel_pct"]).as_py() <= 100)
    transactions = whole(tables.SPECS["transactions"], 20000)
    categories = {r["mcc"] for r in pools.merchant_categories()}
    check("every transaction is in a known merchant category", set(transactions["merchant_category"].to_pylist()) <= categories)

    heading("Every contract is valid under the standard, and the platform accepts it")
    import yaml
    try:
        from jsonschema import Draft202012Validator
        schema = json.loads((ROOT / "docs" / "internal" / "design" / "contract-examples" / "odcs-json-schema-latest.json").read_text(encoding="utf-8"))
    except (ImportError, FileNotFoundError):
        Draft202012Validator = None
    module = importlib.util.spec_from_file_location("privacy_contract", ROOT / "platform" / "api" / "app" / "privacy_contract.py")
    privacy = importlib.util.module_from_spec(module)
    module.loader.exec_module(privacy)
    for name, spec in tables.SPECS.items():
        document = yaml.safe_load(contracts.to_yaml(spec))
        if Draft202012Validator is not None:
            check(f"{name}: the contract is valid under the standard's own schema", not list(Draft202012Validator(schema).iter_errors(document)))
        problems = privacy.check(document)
        check(f"{name}: the platform accepts the contract ({len(spec.key_columns())} key column{'' if len(spec.key_columns()) == 1 else 's'})", not problems, "; ".join(problems))

    heading("Faulty copies are damaged where the answers say, and nowhere else")
    for name, rows in (("patients", 20000), ("admissions", 20000), ("ward_occupancy", 20000), ("vehicle_readings", 20000), ("diagnosis_codes", 102)):
        generate.make(name, rows, ("csv", "tsv", "jsonl", "json"), work / "faulty", quiet=True, faulty=True)
        folder = work / "faulty" / name / str(tables.SPECS[name].fixed_rows or rows)
        answers = json.loads((folder / "faults" / "answers.json").read_text())["faults"]
        problems = faults.check(tables.SPECS[name], folder)
        check(f"{name}: {len(answers)} faulty files, and a plain scan of each finds the rows and lines the answers name", not problems and len(answers) >= 15,
              "; ".join(problems[:2]) or f"{len(answers)} files")
    folder = work / "faulty" / "patients" / "20000"
    answers = json.loads((folder / "faults" / "answers.json").read_text())
    answers["faults"][0]["planted"][0]["row"] += 1
    (folder / "faults" / "answers.json").write_text(json.dumps(answers))
    check("a wrong answer is noticed", bool(faults.check(tables.SPECS["patients"], folder)))

    heading("The large tier cannot start by accident")
    try:
        generate.main(["--table", "patients", "--rows", "250000", "--out", str(work / "no")])
        refused = False
    except SystemExit as stop:
        refused = "large tier" in str(stop)
    check("more than 100,000 rows is refused without --large, and nothing is written", refused and not (work / "no").exists())
    real = generate.shutil.disk_usage
    generate.shutil.disk_usage = lambda path: type("U", (), {"free": 10 ** 6})()
    try:
        try:
            generate.check_disk(work / "tight", 10 ** 9)
            stopped = False
        except SystemExit as stop:
            stopped = "Nothing was written" in str(stop)
    finally:
        generate.shutil.disk_usage = real
    check("a table that would not fit on the disk is refused before anything is written", stopped)
    check("a workbook is not made past the sheet limit and a JSON list is not made past its own limit",
          formats.VARIANTS["xlsx"].applies(1_048_576) is not None and formats.VARIANTS["json"].applies(100_001) is not None
          and formats.VARIANTS["xlsx"].applies(1_048_575) is None)


if __name__ == "__main__":
    sys.exit(main())
