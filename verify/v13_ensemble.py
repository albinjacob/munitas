"""V13: the ensemble beats any single detector.

Runs the three detectors over the synthetic corpus and reports, per detector and
for the union, how many ground-truth spans each one found. The claim under test
is that the detectors fail differently, so the union catches spans that no single
member catches.

If the union does not beat the best single detector by a useful margin, the
ensemble is three times the compute for nothing, and the design should say so.
That outcome is a real possibility and the script is written so it would be
visible rather than absorbed.

Scored against the transcript text directly, not against ASR output, so this
isolates detection from transcription. V14 is where the two are combined and the
gap between them measured.

    .venv\\Scripts\\python.exe verify\\v13_ensemble.py --limit 20
"""

from __future__ import annotations

import os
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from worker.detect import (ENTITY_MAP_GLINER, ENTITY_MAP_PRESIDIO,  # noqa: E402
                           ENTITY_MAP_SPACY, GLINER_LABELS, reconcile)


def overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    data = os.environ.get("MUNITAS_DATA", "").rstrip("/\\")
    parser.add_argument("--corpus", type=Path, required=not data,
                        default=Path(f"{data}/synthetic") if data else None)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    records = sorted(args.corpus.glob("synth-*.json"))[:args.limit]
    if not records:
        print(f"no corpus at {args.corpus}")
        return 2

    print("loading detectors (CPU is fine here, the texts are short)")
    import spacy
    from gliner import GLiNER
    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.nlp_engine import NlpEngineProvider

    nlp = spacy.load("en_core_web_lg")
    provider = NlpEngineProvider(nlp_configuration={
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "en", "model_name": "en_core_web_lg"}],
    })
    presidio = AnalyzerEngine(nlp_engine=provider.create_engine())
    gliner = GLiNER.from_pretrained("urchade/gliner_medium-v2.1")

    found_by = defaultdict(int)
    union_found = 0
    total = 0
    only_union: list[dict] = []
    per_entity_union = defaultdict(lambda: [0, 0])   # entity -> [found, total]
    disagreements = 0
    candidates_total = 0

    for path in records:
        record = json.loads(path.read_text(encoding="utf-8"))
        text = record["transcript"]

        raw = []
        for r in presidio.analyze(text=text, language="en"):
            entity = ENTITY_MAP_PRESIDIO.get(r.entity_type)
            if entity:
                raw.append(("presidio", r.start, r.end, entity, float(r.score)))
        for ent in nlp(text).ents:
            entity = ENTITY_MAP_SPACY.get(ent.label_)
            if entity:
                raw.append(("spacy", ent.start_char, ent.end_char, entity, 0.70))
        for ent in gliner.predict_entities(text, GLINER_LABELS, threshold=0.35):
            entity = ENTITY_MAP_GLINER.get(ent["label"])
            if entity:
                raw.append(("gliner", ent["start"], ent["end"], entity, float(ent["score"])))

        merged = reconcile(raw, text)
        candidates_total += len(merged)
        disagreements += sum(1 for c in merged if c.votes == 1)

        by_detector = defaultdict(list)
        for name, start, end, _, _ in raw:
            by_detector[name].append((start, end))

        for span in record["spans"]:
            total += 1
            entity_stats = per_entity_union[span["entity"]]
            entity_stats[1] += 1

            hits = set()
            for detector in ("presidio", "spacy", "gliner"):
                if any(overlaps(span["start"], span["end"], s, e)
                       for s, e in by_detector.get(detector, [])):
                    found_by[detector] += 1
                    hits.add(detector)

            if hits:
                union_found += 1
                entity_stats[0] += 1
                if len(hits) == 1:
                    only_union.append({
                        "record": record["record_id"],
                        "entity": span["entity"],
                        "text": span["text"],
                        "only": next(iter(hits)),
                    })

    print(f"\nGround-truth spans: {total} across {len(records)} records")
    print("-" * 52)
    best_single = 0
    for detector in ("presidio", "spacy", "gliner"):
        n = found_by[detector]
        best_single = max(best_single, n)
        print(f"  {detector:<10} {n:>4} / {total}   recall {n / total:6.1%}")
    print(f"  {'UNION':<10} {union_found:>4} / {total}   recall {union_found / total:6.1%}")

    print(f"\nSpans only one detector caught: {len(only_union)}")
    by_saviour = defaultdict(int)
    for item in only_union:
        by_saviour[item["only"]] += 1
    for detector, n in sorted(by_saviour.items(), key=lambda kv: -kv[1]):
        print(f"  {detector:<10} was the only one to find {n}")

    print("\nPer entity, union recall")
    print("-" * 52)
    for entity, (found, seen) in sorted(per_entity_union.items()):
        flag = "  <-- weak" if seen and found / seen < 0.8 else ""
        print(f"  {entity:<24} {found:>3} / {seen:<3}  {found / seen:6.1%}{flag}")

    print(f"\nCandidates proposed: {candidates_total}")
    print(f"  proposed by exactly one detector: {disagreements} "
          f"({disagreements / candidates_total:.1%})" if candidates_total else "")

    print("\nVerdict")
    print("-" * 52)
    gain = union_found - best_single
    if gain > 0:
        print(f"  [PASS] the union found {gain} spans the best single detector missed "
              f"({gain / total:.1%} of the answer key)")
    else:
        print("  [FAIL] the union found nothing the best single detector missed. "
              "The ensemble is not earning its compute on this corpus.")
    return 0 if gain > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
