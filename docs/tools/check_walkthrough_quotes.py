r"""Find bold quotations in the walkthroughs that no longer appear anywhere in the console or the platform.

    .venv\Scripts\python.exe docs\tools\check_walkthrough_quotes.py                 # every page listed on an organisation page
    .venv\Scripts\python.exe docs\tools\check_walkthrough_quotes.py agent-egress    # chosen pages

The kit checks every bold phrase in a note against the words that were on the screen when its picture was taken. That proves a note matches its own picture. It
cannot tell that the console has changed since, which is how a page comes to say "Needs your confirmation" after the tile was renamed "Need your confirmation".
This searches today's console source (web/src), the platform (platform/api/app) and the policy for each bold phrase of three words or more.

A phrase that is not found is listed for a person to look at. Many are fine, because the screen builds them from values: a date, a count, a name, a reason that
somebody typed, or a sentence put together from parts ("Could not " followed by what failed). Those are left out when they carry an obvious capture-time value.
What is left over is the short list worth reading: if a phrase there is a fixed label or sentence on the screen, the page is out of date and the picture needs
retaking. The exit code is always 0, because only a person can tell which is which.
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from walkthrough_kit import ORG_TITLES, PUBLIC, SERIES  # noqa: E402

ROOT = PUBLIC.parents[2]
SOURCES = ("web/src/**/*.ts", "web/src/**/*.tsx", "platform/api/app/*.py", "platform/policy/*.rego")
# Values that the capture made up or typed, so that they cannot be in the source.
MADE_UP = re.compile(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]* \d{1,2}, 20\d\d|\d+ (?:file|files|row|rows|day|days|hours?|sign-in|audit)|\bon$|\d{4}-\d+", re.I)


def norm(text: str, strip_tags: bool = True) -> str:
    if strip_tags:
        text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).lower()
    for a, b in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"')):
        text = text.replace(a, b)
    return re.sub(r"\s+", " ", text).strip(" .,:;?!\"'")


def corpus() -> str:
    out = []
    for pattern in SOURCES:
        for path in ROOT.glob(pattern):
            try:
                out.append(norm(path.read_text(encoding="utf-8"), strip_tags=False))   # keep tags: text in attributes such as label= and title= counts
            except OSError:
                pass
    return re.sub(r"\s+", " ", " ".join(out))


def main() -> int:
    wanted = sys.argv[1:]
    pages = [e["slug"] for org in ORG_TITLES for e in SERIES if e["org"] == org and (not wanted or e["slug"] in wanted)]
    source = corpus()
    # Names of people and capture-time values are not in the source either; the page's own cast is a good list of the names.
    for slug in pages:
        page = (PUBLIC / f"{slug}-walkthrough.html").read_text(encoding="utf-8")
        names = {n.lower() for n in re.findall(r'<div class="name">([^<]+)</div>', page)}
        notes = re.findall(r'<span class="note-body">(.*?)</span></div>', page, re.S)
        missing: list[tuple[int, str]] = []
        total = 0
        for number, note in enumerate(notes, 1):
            for phrase in re.findall(r"<strong>(.*?)</strong>", note, re.S):
                text = norm(phrase)
                if len(text.split()) < 3:
                    continue
                total += 1
                if MADE_UP.search(text) or any(re.search(rf"\b{re.escape(n)}\b", text) for n in names):
                    continue
                words = text.split()
                found = text in source or any(" ".join(words[a:b]) in source for a in range(2) for b in (len(words) - 1, len(words)) if b - a >= 3)
                if not found:
                    missing.append((number, html.unescape(re.sub(r"<[^>]+>", "", phrase))[:110]))
        print(f"{slug}: {total} quoted phrases of three words or more, {len(missing)} to look at")
        for number, phrase in missing:
            print(f"    step {number}: {phrase}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
