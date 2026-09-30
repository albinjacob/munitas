"""Align a reference transcript to an ASR transcript, at character level.

This exists to turn V14 from a bracket into a measurement.

The problem it solves: ground-truth spans carry offsets into the reference
transcript, and detector spans carry offsets into the ASR transcript. Those are
different strings, so the two cannot be compared directly. Every earlier attempt
compared the *text* of the spans instead, and text comparison cannot tell the
difference between three situations that need telling apart:

  * the detector found the identifier
  * the detector found part of it, leaving the rest in the clear
  * ASR destroyed it, so no detector could have found it

Aligning the two strings and mapping the offsets separates all three. Overlap
between mapped spans is then an ordinary interval calculation, with no string
matching and no threshold chosen to make a number look better.

`difflib.SequenceMatcher` over characters is enough here. The two strings are
the same utterance, so they share long identical runs, which is the case it
handles well. `autojunk` is switched off because it treats frequent characters
as noise, and in English prose that means spaces.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass(frozen=True)
class MappedSpan:
    """A reference span located in the ASR transcript."""

    ref_start: int
    ref_end: int
    asr_start: int
    asr_end: int
    preserved: float   # fraction of the reference span ASR reproduced verbatim

    @property
    def survived(self) -> bool:
        """Did enough of the identifier survive for a detector to have a chance?

        Half is the threshold. Below it, so little of the identifier is in the
        transcript that finding it would be luck rather than detection, and
        counting the miss against the detector would be scoring it for a
        transcription failure.
        """
        return self.preserved >= 0.5


class Alignment:
    def __init__(self, reference: str, asr: str) -> None:
        self.reference = reference
        self.asr = asr
        self._matcher = SequenceMatcher(None, reference.casefold(), asr.casefold(),
                                        autojunk=False)
        # ref offset -> asr offset, for characters ASR reproduced exactly.
        self._exact: dict[int, int] = {}
        for i1, j1, size in self._matcher.get_matching_blocks():
            for k in range(size):
                self._exact[i1 + k] = j1 + k

    def similarity(self) -> float:
        return self._matcher.ratio()

    def map_span(self, ref_start: int, ref_end: int) -> MappedSpan:
        """Locate a reference span in the ASR transcript."""
        hits = [self._exact[i] for i in range(ref_start, ref_end) if i in self._exact]
        length = max(ref_end - ref_start, 1)
        preserved = len(hits) / length

        if hits:
            asr_start, asr_end = min(hits), max(hits) + 1
        else:
            # Nothing survived. Anchor to the nearest surviving character so the
            # span still has a position, and let `preserved` carry the truth.
            anchor = self._nearest(ref_start)
            asr_start = asr_end = anchor

        return MappedSpan(ref_start, ref_end, asr_start, asr_end, preserved)

    def _nearest(self, ref_index: int) -> int:
        if not self._exact:
            return 0
        best = min(self._exact, key=lambda i: abs(i - ref_index))
        return self._exact[best]


def coverage(target: tuple[int, int], spans: list[tuple[int, int]]) -> float:
    """What fraction of `target` is covered by the union of `spans`.

    The union matters rather than the best single span: two detectors each
    catching half an address between them have covered it, and scoring only the
    best one would call that a miss.
    """
    start, end = target
    if end <= start:
        return 0.0

    covered = [False] * (end - start)
    for s, e in spans:
        for i in range(max(s, start), min(e, end)):
            covered[i - start] = True
    return sum(covered) / len(covered)


def _self_test() -> None:
    """Run with `python verify/align.py`.

    A measurement tool that has never been checked against a case with a known
    answer is not evidence of anything, so the answers here are worked out by
    hand rather than recorded from a previous run.
    """
    cases = []

    # Verbatim: the span must map exactly and be fully preserved.
    ref = "patient: My name is Aoife Brennan."
    asr = "My name is Aoife Brennan."
    a = Alignment(ref, asr)
    m = a.map_span(ref.index("Aoife"), ref.index("Aoife") + len("Aoife Brennan"))
    cases.append(("verbatim span maps exactly",
                  asr[m.asr_start:m.asr_end] == "Aoife Brennan" and m.preserved == 1.0,
                  f"mapped to {asr[m.asr_start:m.asr_end]!r}, preserved {m.preserved:.2f}"))

    # Destroyed: ASR heard something else entirely.
    ref = "patient: My name is Aoife Brennan."
    asr = "My name is Eva Brendan."
    a = Alignment(ref, asr)
    m = a.map_span(ref.index("Aoife"), ref.index("Aoife") + len("Aoife"))
    cases.append(("mangled name is reported as not surviving",
                  not m.survived, f"preserved {m.preserved:.2f}"))

    # Reformatted digits: same characters, different spacing, so most survive.
    ref = "receptionist: it's 85145542 yes"
    asr = "it's 8 5 1 4 5 5 4 2 yes"
    a = Alignment(ref, asr)
    m = a.map_span(ref.index("85145542"), ref.index("85145542") + 8)
    cases.append(("respaced digits are reported as surviving",
                  m.survived, f"preserved {m.preserved:.2f}"))

    # Coverage: partial detection must not read as full.
    cases.append(("partial coverage is measured, not rounded up",
                  abs(coverage((0, 10), [(0, 3)]) - 0.3) < 1e-9,
                  f"{coverage((0, 10), [(0, 3)]):.2f}"))
    cases.append(("union of two partial spans covers the whole",
                  coverage((0, 10), [(0, 5), (5, 10)]) == 1.0))
    cases.append(("no overlap is zero coverage",
                  coverage((0, 10), [(20, 30)]) == 0.0))

    failed = 0
    print("align.py self-test")
    print("-" * 44)
    for case in cases:
        label, ok = case[0], case[1]
        detail = case[2] if len(case) > 2 else ""
        failed += not ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    print(f"\n{len(cases) - failed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    _self_test()
