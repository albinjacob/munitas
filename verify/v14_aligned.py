"""V14: recall through audio, measured by alignment.

Reads the pipeline's own artefacts and scores them with the same module the
pipeline uses, so this reports the number the promotion gate acted on rather
than a second opinion that might quietly disagree with it.

Three populations, which string comparison cannot separate and which need
different responses:

  * **Destroyed by ASR.** Too little of the identifier survived transcription
    for any detector to have found it. A speech problem, not a detector problem.
  * **Removed.** The detector covered the identifier, allowing for residue that
    identifies nobody, such as a title or an email domain.
  * **Leaked.** Identifying text left in the clear. For redaction this is a
    failure whether the detector found most of the span or none of it.

    .venv\\Scripts\\python.exe verify\\v14_aligned.py \\
        --detected-key <detected.json key> --truth-prefix <ingest version prefix>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3  # noqa: E402
from botocore.config import Config  # noqa: E402

from worker.align import Alignment  # noqa: E402
from worker.scoring import (GATE_DIRECT_LEAKS, GATE_RECALL, ScoreCard,  # noqa: E402
                            evaluate_gate, score_record)
from ports_config import PORTS  # noqa: E402


def get_json(key: str):
    client = boto3.client(
        "s3", endpoint_url=f"http://localhost:{PORTS['seaweedfs_s3']}",
        aws_access_key_id="pipeline-action",
        aws_secret_access_key="pipeline-action-secret",
        config=Config(signature_version="s3v4"), region_name="us-east-1",
    )
    # The bucket comes from the record that owns the key, not from a name:
    # the version whose prefix the key sits under, and its tenant's bucket.
    import psycopg
    with psycopg.connect(f"postgresql://munitas:munitas@localhost:{PORTS['postgres']}/platform") as conn:
        row = conn.execute(
            """select p.bucket from dataset_version v
                 join tenant_storage_provision p
                   on p.tenant_id = v.tenant_id and p.backend = 'seaweedfs'
                where %s like v.storage_prefix || '/%%' limit 1""", (key,)).fetchone()
    if not row:
        raise SystemExit(f"no sealed version owns {key}")
    return json.loads(client.get_object(Bucket=row[0], Key=key)["Body"].read())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detected-key", required=True)
    parser.add_argument("--truth-prefix", required=True)
    args = parser.parse_args()

    detected = get_json(args.detected_key)
    card = ScoreCard()
    similarities = []
    hazard = {True: {"total": 0, "destroyed": 0}, False: {"total": 0, "destroyed": 0}}

    for row in detected:
        truth = get_json(f"{args.truth_prefix}/{row['record_id']}.truth.json")
        reference = truth["reference_transcript"]
        before_total, before_destroyed = card.total, card.destroyed

        similarities.append(Alignment(reference, row["transcript"]).similarity())
        score_record(card, reference, row["transcript"], truth["spans"],
                     row["candidates"], record_id=row["record_id"])

        bucket = hazard[bool(truth["hazard"])]
        bucket["total"] += card.total - before_total
        bucket["destroyed"] += card.destroyed - before_destroyed

    card.similarity = sum(similarities) / len(similarities)

    print("\nV14: recall through audio, measured by alignment")
    print("=" * 64)
    print(f"  records                                {len(detected)}")
    print(f"  mean reference-to-ASR similarity       {card.similarity:.1%}")
    print(f"  ground-truth identifiers               {card.total}")
    print()
    print(f"  destroyed by ASR, undetectable         {card.destroyed:>4}  ({card.destruction_rate:.1%})")
    print(f"  survived transcription                 {card.survived:>4}")
    print(f"  removed by the detector                {card.effective:>4}  ({card.recall_effective:.1%} of survivors)")
    print(f"  leaked, identifying text in the clear  {card.leaked:>4}")
    print(f"    of which direct identifiers          {card.direct_leaks:>4}")
    print(f"    of which quasi-identifiers           {card.quasi_leaks:>4}")

    print("\n  Recall")
    print("  " + "-" * 58)
    print("    on clean reference text (V13 union)  99.1%")
    print(f"    through audio, of survivors          {card.recall_effective:.1%}")
    print(f"    through audio, of everything spoken  {card.recall_spoken:.1%}")

    print("\nPer entity")
    print("-" * 64)
    print(f"  {'entity':<24} {'n':>4} {'destroyed':>10} {'removed':>9} {'leaks':>6}")
    for name, st in sorted(card.per_entity.items()):
        surv = st["total"] - st["destroyed"]
        removed = st["effective"] / surv if surv else 0.0
        print(f"  {name:<24} {st['total']:>4} {st['destroyed']:>10} "
              f"{removed:>8.1%} {st['leaked']:>6}")

    print("\nHazardous versus ordinary records")
    print("-" * 64)
    for flag, label in ((True, "hazardous"), (False, "ordinary")):
        st = hazard[flag]
        if st["total"]:
            print(f"  {label:<11} n={st['total']:<4} destroyed by ASR {st['destroyed']}")

    if card.leak_detail:
        print("\nEvery leak, so the number can be argued with")
        print("-" * 64)
        for leak in card.leak_detail:
            kind = "DIRECT" if leak["direct"] else "quasi "
            print(f"  [{kind}] {leak['entity']:<22} {leak['identifier']!r}")
            print(f"           left in the clear: {leak['left_in_the_clear']!r}"
                  f"  ({leak['coverage']:.0%} covered)")

    passed, reason = evaluate_gate(card)
    print("\nGate")
    print("-" * 64)
    print(f"  thresholds: recall >= {GATE_RECALL:.2f}, direct leaks <= {GATE_DIRECT_LEAKS}")
    print(f"  {'PASS' if passed else 'REFUSE'}: {reason}")

    print("\nWhat this does not show")
    print("-" * 64)
    print("  That any of it transfers to real recordings. This is synthesised")
    print("  speech, cleaner and more uniform than a consulting room, so the")
    print("  destruction rate here says little about real audio.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
