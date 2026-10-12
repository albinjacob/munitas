r"""U200: the example note drafter only writes what a transcript says, and says where each line came from.

Runs on the host, no platform needed (the settings of U193 are enough to import the shared helpers):

    .venv\Scripts\python.exe verify\v186_note_drafter_example.py

The point of a drafter that uses plain rules is that a reader can check it. This proves that is true of examples/note-drafter and not only claimed: it runs the agent the way the sandbox does (a data
folder with a manifest, and one JSON object on standard output) over the sample transcripts and over transcripts made up here, and checks every line of every draft against the transcript:

  * every bullet of a draft is a line of its transcript, word for word, with the right line number, and nothing in a draft is anything else;
  * every spoken line is either used or counted as not placed, never both, never neither, and no line is used twice;
  * the headings are the ones the rules define, and a heading with nothing under it says so and does not make something up;
  * the same transcript gives the same draft;
  * a file that is not a transcript is skipped and named, an empty folder and a missing manifest are said in words, and the agent prints exactly one JSON object last.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "verify"))

from common import check, heading, summary  # noqa: E402

AGENT = ROOT / "examples" / "note-drafter"
SAMPLES = AGENT / "sample-transcripts"
BULLET = re.compile(r"^  - (.+) \(line (\d+)\)$")


def run(files: dict[str, str], with_manifest: bool = True) -> tuple[dict | None, str]:
    """Run the agent over a data folder holding these files, as the sandbox would, and return the JSON it printed and everything it printed."""
    with tempfile.TemporaryDirectory() as folder:
        data = Path(folder)
        entries = []
        for name, text in files.items():
            (data / name).write_text(text, encoding="utf-8")
            entries.append({"key": f"t/d/v1/{name}", "relative_path": name, "bytes": len(text), "sha256": "x"})
        if with_manifest:
            (data / "manifest.json").write_text(json.dumps({"objects": entries}))
        done = subprocess.run([sys.executable, str(AGENT / "main.py")], env={**os.environ, "NOTE_DRAFTER_DATA": str(data), "MUNITAS_DATASET_VERSION_ID": "version-under-test"},
                              capture_output=True, text=True, timeout=60)
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    try:
        return json.loads(lines[-1]), done.stdout
    except (IndexError, json.JSONDecodeError):
        return None, done.stdout


def spoken(text: str) -> dict[int, str]:
    out = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        found = re.match(r"^\s*(?:Doctor|Patient)\s*:\s*(.+?)\s*$", raw, re.IGNORECASE)
        if found:
            out[number] = found.group(1)
    return out


def main() -> int:
    samples = {p.name: p.read_text(encoding="utf-8") for p in sorted(SAMPLES.glob("*.txt"))}
    heading("U200: the sample transcripts")
    check("there are sample transcripts to draft from", len(samples) >= 3, str(list(samples)))
    result, printed = run(samples)
    check("the agent prints one JSON object, last", result is not None and printed.strip().splitlines()[-1].startswith("{"), printed[-200:])
    assert result is not None
    check("it says which agent it is, which version of the data it read, and how many documents", result["agent"] == "note-drafter" and result["dataset_version_id"] == "version-under-test" and result["documents_read"] == len(samples))
    again, _ = run(samples)
    check("the same transcripts give the same drafts", again == result)

    for name, text in samples.items():
        drafted = next(d for d in result["drafts"] if d["document"] == name)
        lines = spoken(text)
        bullets = [BULLET.match(line) for line in drafted["note"].splitlines() if line.startswith("  - ")]
        check(f"{name}: every bullet matches the pattern of a quoted line", all(bullets), str([line for line in drafted["note"].splitlines() if line.startswith("  - ")][:3]))
        quoted = [(int(m.group(2)), m.group(1)) for m in bullets if m]
        check(f"{name}: every bullet is a spoken line of the transcript, word for word, at the line number it gives", all(lines.get(number) == said for number, said in quoted), str(quoted[:3]))
        check(f"{name}: no line is used twice", len({n for n, _ in quoted}) == len(quoted))
        used, left = set(drafted["lines_used"]), set(drafted["lines_not_placed"])
        check(f"{name}: every spoken line is used or counted as not placed, never both and never neither", used | left == set(lines) and not (used & left), f"{sorted(used | left)} vs {sorted(lines)}")
        check(f"{name}: the lines the note quotes are exactly the lines it says it used", {n for n, _ in quoted} == used)
        check(f"{name}: it says how many lines it left out", f"Not placed: {len(left)} of {len(lines)} spoken lines" in drafted["note"])
        check(f"{name}: it says a clinician has to read it, correct it and sign it", "A clinician has to read it, correct it and sign it." in drafted["note"])
        check(f"{name}: it opens with the first thing the patient said", drafted["note"].split("Reason for the visit:\n  - ")[1].split(" (line ")[0] == next(s for n, s in lines.items() if re.search(r"^Patient", text.splitlines()[n - 1], re.I)))
        nothing = [line for line in drafted["note"].splitlines() if "nothing in the transcript fit this heading" in line]
        empty_headings = {line.split(":")[0] for line in nothing}
        check(f"{name}: a heading with nothing under it says so, and is not in the headings found", empty_headings.isdisjoint(drafted["headings_found"]))

    heading("U200: what each heading takes")
    one = samples["visit-1.txt"]
    d1 = next(d for d in result["drafts"] if d["document"] == "visit-1.txt")
    text1 = d1["note"]
    check("measurements go under Examination and measurements", "Examination and measurements:\n  - Let me examine you. Your blood pressure is 150 over 92 and your pulse is 88. (line 7)" in text1)
    check("an order and a prescription go under Plan", "I will order an ECG and an exercise stress test, and start a low dose of aspirin. (line 10)" in text1.split("Plan:")[1])
    check("a suspected cause goes under Assessment", "This sounds like angina brought on by effort, and I suspect narrowed arteries. (line 9)" in text1.split("Assessment:")[1].split("Plan:")[0])
    check("a medicine the patient takes goes under History", "I take a tablet for blood pressure every morning, and I stopped smoking last year. (line 6)" in text1.split("History:")[1].split("Examination")[0])
    check("a question the doctor asked fits no heading, and is left out", "Does it spread anywhere" not in text1 and 4 in d1["lines_not_placed"])
    check("a line of thanks is left out", "Thank you, doctor." not in text1 and 12 in d1["lines_not_placed"])
    check("the header line of the file is not treated as speech", 1 not in d1["lines_used"] and 1 not in d1["lines_not_placed"] and one.splitlines()[0].startswith("Visit 1"))
    # Two lines of the third visit were once filed under Plan: "Welcome back" holds the letters of "come back", and "as prescribed?" is a question about a prescription, not a plan.
    d3 = next(d for d in result["drafts"] if d["document"] == "visit-3.txt")
    plan3 = d3["note"].split("Plan:")[1]
    check("a greeting that holds the letters of 'come back' is not filed under Plan", "Welcome back" not in plan3 and 2 in d3["lines_not_placed"])
    check("a question about a prescription is not filed under Plan", "as prescribed" not in plan3 and 4 in d3["lines_not_placed"])
    check("the real plan of the third visit is still under Plan", "I will reduce the dose, and schedule a blood test, and see you again in one month. (line 9)" in plan3)

    heading("U200: input the agent was not made for")
    odd, out = run({"notes.txt": "Doctor: Hello.\nPatient: Hello.\n", "scan.png": "not text", "readme.md": "# not a transcript"})
    check("a file that is not a transcript is skipped and named", odd is not None and sorted(odd["files_skipped"]) == ["readme.md", "scan.png"] and odd["documents_read"] == 1, str(odd)[:200])
    quiet = next(d for d in odd["drafts"])
    check("a transcript with almost nothing in it gives a draft that says nothing fit, not an invented one", "Plan: nothing in the transcript fit this heading." in quiet["note"] and quiet["headings_found"] == ["Reason for the visit"], quiet["note"])
    blank, _ = run({"empty.txt": ""})
    check("an empty transcript gives a draft that places nothing", blank is not None and blank["drafts"][0]["lines_used"] == [] and "Not placed: 0 of 0" in blank["drafts"][0]["note"])
    nodata, _ = run({}, with_manifest=False)
    check("a run given no data says so in words", nodata is not None and "no data was given to this run" in nodata["error"])
    nofiles, _ = run({})
    check("a data folder with no files reads no documents", nofiles is not None and nofiles["documents_read"] == 0 and nofiles["drafts"] == [])
    check("the project names its entry point", json.loads((AGENT / "munitas.json").read_text())["entrypoint"] == "main.py" and (AGENT / "main.py").exists())
    check("it asks for nothing outside the standard library, so the sandbox needs to install nothing", not (AGENT / "requirements.txt").exists() and not re.search(r"^\s*(?:import|from)\s+(?!json|os|re|sys|__future__|pathlib)\w+", (AGENT / "main.py").read_text(), re.M))
    return summary("U200")


if __name__ == "__main__":
    sys.exit(main())
