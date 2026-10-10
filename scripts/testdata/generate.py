"""Make test tables, in every format, with their contracts and truth files.

    python -m scripts.testdata.generate --table patients --rows 100000 --variants csv,parquet --out C:/Work/testdata
    python -m scripts.testdata.generate --preset ordinary --out C:/Work/testdata
    python -m scripts.testdata.generate --preset large --out C:/Work/testdata --large --dry-run

Output, for each table and row count:
    <out>/<table>/contract.yaml                    the contract (the same for every size)
    <out>/<table>/<rows>/<variant>/<table>.<ext>   the file
    <out>/<table>/<rows>/truth.json                what is true of the data, written as it was made
    <out>/<table>/<rows>/manifest.json             every file with its size, SHA-256 and the options that read it

Reproducible: the same seed gives the same files. More than 100,000 rows is the large tier: it needs --large, checks the disk first and
says what it will do, so it is never started by accident.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

from . import contracts, faults, formats, readback, truth
from .tables import SPECS, TableSpec

SEED = 20261009
BATCH_ROWS = 250_000
ORDINARY_LIMIT = 100_000
DEFAULT_OUT = Path("C:/Work/testdata")

# Roughly how big each variant is next to the text of the same rows, measured on the first thousand rows.
SIZE_FACTOR = {"csv": 1.0, "csv-quoted": 1.1, "csv-bom": 1.0, "csv-cp1252": 1.0, "csv-semicolon": 1.0, "csv-pipe": 1.0, "tsv": 1.0, "csv-gzip": 0.3, "csv-zstd": 0.3,
               "jsonl": 1.8, "jsonl-gzip": 0.3, "jsonl-zstd": 0.3, "json": 1.9, "parquet": 0.3, "parquet-small-groups": 0.35, "arrow": 1.0,
               "feather": 0.35, "orc": 0.3, "avro": 0.6, "xlsx": 0.5}
TEXT_ONLY = ("csv", "csv-quoted", "csv-bom", "csv-cp1252", "csv-semicolon", "csv-pipe", "tsv", "csv-gzip", "csv-zstd", "jsonl", "jsonl-gzip", "jsonl-zstd", "json")
BINARY = ("parquet", "parquet-small-groups", "arrow", "feather", "orc", "avro", "xlsx")

PRESETS = {
    "ordinary": [(t, 1_000, "all") for t in SPECS] + [(t, 100_000, ("csv", "jsonl", "parquet")) for t in SPECS if SPECS[t].fixed_rows is None],
    "large": [(t, 5_000_000, "all") for t in ("admissions", "transactions", "ward_occupancy", "fx_ticks", "vehicle_readings")]
             + [(t, 50_000_000, ("csv", "csv-gzip", "jsonl-zstd", "parquet")) for t in ("admissions", "transactions", "ward_occupancy", "fx_ticks", "vehicle_readings")],
}


def pick_variants(choice) -> list[str]:
    if choice in ("all", None):
        return list(formats.VARIANTS)
    names = list(choice) if not isinstance(choice, str) else {"text": list(TEXT_ONLY), "binary": list(BINARY)}.get(choice, choice.split(","))
    unknown = [n for n in names if n not in formats.VARIANTS]
    if unknown:
        raise SystemExit(f"no such variant: {', '.join(unknown)}. The variants are {', '.join(formats.VARIANTS)}")
    return names


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 22), b""):
            digest.update(block)
    return digest.hexdigest()


def estimate(spec: TableSpec, rows: int, names: list[str]) -> int:
    """About how many bytes these files will take, from the text of the first thousand rows."""
    ctx = spec.context(rows, SEED)
    sample = spec.build(0, min(1000, ctx.rows), ctx)
    per_row = sum(len(str(x)) + 1 for column in formats.stringify(sample).columns for x in column.to_pylist()) / max(1, sample.num_rows)
    return int(per_row * ctx.rows * sum(SIZE_FACTOR[n] for n in names if formats.VARIANTS[n].applies(ctx.rows) is None))


def check_disk(out: Path, needed: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(out).free
    if needed * 1.3 > free:
        raise SystemExit(f"this needs about {needed / 1e9:.1f} GB and {out} has {free / 1e9:.1f} GB free. Nothing was written")


def make(table: str, rows: int, variants, out: Path, seed: int = SEED, batch_rows: int = BATCH_ROWS, quiet: bool = False,
         faulty: bool = False) -> dict:
    spec = SPECS[table]
    ctx = spec.context(rows, seed)
    names = pick_variants(variants)
    folder = out / table / str(ctx.rows)
    folder.mkdir(parents=True, exist_ok=True)
    (out / table / "contract.yaml").write_text(contracts.to_yaml(spec), encoding="utf-8")

    writers, skipped = {}, []
    for name in names:
        variant = formats.VARIANTS[name]
        why = variant.applies(ctx.rows)
        if why:
            skipped.append({"variant": name, "reason": why})
            continue
        directory = folder / name
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True)
        writers[name] = variant.open(directory / f"{table}.{variant.extension}", spec)

    wants_text = any(formats.VARIANTS[n].kind == "csv" for n in writers)
    wants_json = any(formats.VARIANTS[n].kind in ("jsonl", "json") for n in writers)
    summary = truth.Summary(spec)
    started, done = time.perf_counter(), 0
    while done < ctx.rows:
        count = min(batch_rows, ctx.rows - done)
        batch = spec.build(done, count, ctx)
        text = formats.stringify(batch) if (wants_text or wants_json) else None
        lines = formats.json_rows(batch, text) if wants_json else None
        summary.add(batch)
        for writer in writers.values():
            writer.write(batch, text, lines)
        done += count
        if not quiet and ctx.rows >= 1_000_000:
            print(f"  {table}: {done:,} of {ctx.rows:,} rows, {time.perf_counter() - started:.0f}s", flush=True)
    for writer in writers.values():
        writer.close()

    files = []
    for name, writer in writers.items():
        variant = formats.VARIANTS[name]
        files.append({"variant": name, "path": str(writer.path.relative_to(folder)).replace("\\", "/"), "bytes": writer.path.stat().st_size,
                      "sha256": sha256_of(writer.path), "rows": writer.written, "options": variant.options, "extension": variant.extension})
    (folder / "truth.json").write_text(json.dumps(truth.build(spec, seed, summary.result()), indent=1) + "\n", encoding="utf-8")
    manifest = {"table": table, "rows": ctx.rows, "seed": seed, "kind": spec.kind, "key": spec.key_columns(), "subject": spec.subject,
                "files": files, "skipped": skipped}
    if faulty:
        manifest["faults"] = len(faults.make(spec, folder, seed))
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest


def verify(folder: Path, only: list[str] | None = None) -> list[str]:
    """Read every file in a made folder back with another reader and compare it with its truth file. Returns the differences, in words."""
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    expected = json.loads((folder / "truth.json").read_text(encoding="utf-8"))
    spec = SPECS[manifest["table"]]
    problems = []
    for entry in manifest["files"]:
        if only and entry["variant"] not in only:
            continue
        measured = truth.summarize(spec, readback.read_back(folder / entry["path"], formats.VARIANTS[entry["variant"]], spec))
        problems += [f"{entry['variant']}: {p}" for p in truth.compare(expected, measured)]
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--table", help="a table name, a comma separated list, or all")
    ap.add_argument("--rows", type=int, default=1000)
    ap.add_argument("--variants", default="all", help="all, text, binary, or a comma separated list")
    ap.add_argument("--preset", choices=sorted(PRESETS))
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--large", action="store_true", help=f"allow more than {ORDINARY_LIMIT:,} rows (the large tier)")
    ap.add_argument("--dry-run", action="store_true", help="say what would be made and how much disk it needs, and stop")
    ap.add_argument("--verify", action="store_true", help="read every file back and compare it with its truth file")
    ap.add_argument("--faults", action="store_true", help="also make faulty copies of the text files, with an answers file, and check the answers")
    args = ap.parse_args(argv)

    if args.preset:
        plan = PRESETS[args.preset]
    else:
        tables = list(SPECS) if args.table in (None, "all") else args.table.split(",")
        bad = [t for t in tables if t not in SPECS]
        if bad:
            raise SystemExit(f"no such table: {', '.join(bad)}. The tables are {', '.join(SPECS)}")
        plan = [(t, args.rows, args.variants) for t in tables]

    big = [p for p in plan if SPECS[p[0]].fixed_rows is None and p[1] > ORDINARY_LIMIT]
    if big and not args.large:
        raise SystemExit(f"{len(big)} of these would make more than {ORDINARY_LIMIT:,} rows, which is the large tier. Add --large to say you mean it")

    needed = 0
    print("plan:")
    for table, rows, variants in plan:
        names = pick_variants(variants)
        size = estimate(SPECS[table], rows, names)
        needed += size
        print(f"  {table:20s} {SPECS[table].fixed_rows or rows:>12,} rows  {len(names):>2} variants  about {size / 1e9:6.2f} GB")
    print(f"  total about {needed / 1e9:.2f} GB; {shutil.disk_usage(args.out if args.out.exists() else args.out.anchor).free / 1e9:.0f} GB free")
    if args.dry_run:
        return 0
    check_disk(args.out, needed)

    started = time.perf_counter()
    for table, rows, variants in plan:
        t0 = time.perf_counter()
        manifest = make(table, rows, variants, args.out, args.seed, faulty=args.faults)
        print(f"{table} {manifest['rows']:,} rows: {len(manifest['files'])} files in {time.perf_counter() - t0:.1f}s"
              + (f" ({len(manifest['skipped'])} not made)" if manifest["skipped"] else ""), flush=True)
        if args.verify:
            problems = verify(args.out / table / str(manifest["rows"]))
            if args.faults:
                problems += faults.check(SPECS[table], args.out / table / str(manifest["rows"]))
            print("  read back: " + ("every file agrees with its truth" if not problems else f"{len(problems)} differences"))
            for p in problems[:10]:
                print("   ", p)
            if problems:
                return 1
    print(f"done in {time.perf_counter() - started:.0f}s, files under {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
