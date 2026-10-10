# Test data generator

Realistic, reproducible tables of any size, in every format the platform reads, with a contract and a truth file for each, and faulty copies
whose damaged rows are known. Every name and number is invented, and none can belong to a real person.

## Make some

```
.venv\Scripts\python.exe -m scripts.testdata.generate --table patients --rows 100000 --variants csv,parquet --out C:/Work/testdata
.venv\Scripts\python.exe -m scripts.testdata.generate --preset ordinary --out C:/Work/testdata --verify --faults
.venv\Scripts\python.exe -m scripts.testdata.generate --preset large --out C:/Work/testdata --large --dry-run
```

`--dry-run` says what would be made and how much disk it needs, and stops. More than 100,000 rows is the **large tier**: it needs `--large`,
and it is never part of the ordinary checks. The default folder is `C:/Work/testdata`, outside the repository.

`python -m scripts.testdata.generate --help` lists every option.

## The tables

| Table | About people | Key | Note |
|---|---|---|---|
| `patients` | yes | `registration_id` | Names, birth dates and contact details are locked. |
| `admissions` | yes | `admission_id` | Tied to a person by `patient_number`. A decimal cost, a missing discharge for people still in hospital. |
| `diagnosis_codes` | no | `code` | A lookup of 102 diagnoses. Always 102 rows. |
| `ward_occupancy` | no | `ward_code` and `hour_start` | 800 wards, every hour. A composite key. |
| `customers` | yes | `customer_record` | Names, birth dates, national numbers and contact details are locked. |
| `transactions` | yes | `transaction_id` | Tied to a customer by `customer_number`. Decimal amounts. |
| `merchant_categories` | no | `mcc` | A lookup of 81 merchant category codes. Always 81 rows. |
| `fx_ticks` | no | `pair` and `ticked_at` | Four prices a second for twelve pairs. A composite key, millisecond timestamps, decimals. |
| `vehicle_readings` | no | none | 20,000 vehicles. A table with no key. |

## The variants

`csv` (a value is quoted only when it must be), `csv-quoted` (every text value quoted), `csv-bom`, `csv-cp1252`, `csv-semicolon`, `csv-pipe`,
`tsv`, `csv-gzip`, `csv-zstd`, `jsonl`, `jsonl-gzip`, `jsonl-zstd`, `json` (a list, up to 100,000 rows), `parquet`, `parquet-small-groups`,
`arrow`, `feather`, `orc`, `avro` and `xlsx` (up to 1,048,575 rows). `manifest.json` records the options that read each one (delimiter,
encoding, compression) with its size and SHA-256.

## What is written

```
<out>/<table>/contract.yaml                    the contract (Open Data Contract Standard 3.2.0 with the Munitas privacy profile)
<out>/<table>/<rows>/<variant>/<table>.<ext>   the file
<out>/<table>/<rows>/truth.json                what is true of the data, written as it was made
<out>/<table>/<rows>/manifest.json             every file with its size, hash and the options that read it
<out>/<table>/<rows>/faults/answers.json       with --faults: every faulty file, the rows and lines damaged, the column, the value
```

The truth file holds the row count and, for each column, the missing values, smallest, largest and total (or total length), plus the number of
different keys and an order-independent fingerprint of them, and for a table about people the number of different people. A test compares
what the platform says about a file with this, which did not come from the platform.

## Rules it keeps

* **Reproducible.** The same seed gives the same bytes in every file. Each value is a function of the seed, its name and the row number
  (`hashing.py`), so a batch size, a thread count or a library version cannot change a row.
* **Safe.** Emails are at `example.com`, `.org` or `.net`. Phone numbers are in ranges reserved for fiction (the UK drama range, the US 555-01
  block) or never allocated. National numbers are of a kind never issued (a UK prefix `QQ`, a US area number of 900 or more, an Indian number that
  starts with 1). Record numbers are a local scheme.
* **Realistic.** A first name matches the person's sex, nobody is admitted before they were born, a delivery is a woman of child-bearing age,
  ages, admissions by weekday and season, stays by diagnosis, costs and prices follow believable shapes.
* **Checkable.** `verify/v167_testdata_generator.py` (U175) proves all of the above and runs on the host in under a minute, with no stack.

## Files

`pools/` the name, street, town, diagnosis and merchant lists (made once from Faker with a fixed seed by `build_pools.py`, then kept; the
generator never needs Faker). `hashing.py` the numbers. `tables.py` the nine tables. `contracts.py` the contracts. `formats.py` the writers.
`truth.py` the truth and the comparison. `readback.py` reading every format back with a different reader. `faults.py` the faulty text copies and the
scan that checks their answers. `faults_binary.py` the same for Parquet, Arrow, ORC, Avro and Excel (a repeated or empty key, a value with too many places,
a column of the wrong type, a column missing or added, text where a workbook should hold a number), and the hostile files in `hostile/` (cut short, garbage,
text under a binary ending, a workbook that expands hundreds of times, a workbook with a macro, an Avro block that claims gigabytes), listed in answers.json. `generate.py` the command line.

## Not here

Sizes of 5 million rows and more are made and checked only in the large tier (`verify/run_large.py`), on a go-ahead.
