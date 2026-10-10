r"""Check every published walkthrough page for the problems that reading alone keeps finding one pass at a time.

    .venv\Scripts\python.exe docs\tools\check_walkthrough_pages.py            # every page listed on an organisation page
    .venv\Scripts\python.exe docs\tools\check_walkthrough_pages.py agent-egress people-arrive

It reads the built pages in docs/public/walkthroughs and needs no browser. The kit already refuses a bold phrase that is not on its screen and the wording
rules in CLAUDE.md (walkthrough_kit.check_notes and check_wording). This covers what those do not:

  FAIL (the exit code is 1 if any page has one)
    - a verification script named in the text, or a path to a script or builder (the footer, which names the capture on purpose, is left out)
    - a reference to "the previous page", "an earlier walkthrough" and the like, because every page has to stand alone
    - spacing or punctuation that is broken: " ,", " .", "..", "( "
    - screen text that ends a sentence and is followed by more of the note without quotation marks
    - a different number of pictures than steps, a picture that is empty, or a step count that disagrees with the summary strip or the contents list
    - Previous and Next that do not follow the order of walkthrough_kit.SERIES inside the organisation

  REVIEW (listed for a person to read; some are fine)
    - talk about how the page was made ("the capture", "recorded again")
    - a status code or an ALL-CAPS value in the text, which may need its plain-word meaning beside it
    - a date written into the text, which will be stale after the next capture
    - a word doubled by mistake
    - a platform word used on the page that its own list of words does not define
    - one person described in different words on different pages
"""
from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from walkthrough_kit import ARCHIVED, ORG_TITLES, PUBLIC, SERIES, WORDS  # noqa: E402

KNOWN_CAPS = {"CSV", "SQL", "URL", "API", "PDF", "HTTP", "DNS", "UTC", "DPO", "IP", "AWS", "SDK", "JSON", "YAML", "WAV", "GPU", "AI", "HTML", "ID", "CUDA",
              "TLS", "OK", "S3", "LLP", "SHA"}
MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"


class Page(HTMLParser):
    """The text of a built page, by the part it belongs to. Pictures, code boxes and the footer are left out."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, set[str]]] = []
        self.blocks: list[dict] = []        # {"kind", "step", "text", "html"}
        self.glossary: list[str] = []
        self.cast: list[tuple[str, str]] = []
        self.images: list[int] = []
        self.media: set[int] = set()        # steps that carry a picture or a recorded code box
        self.step = 0
        self.steps = 0
        self.toc = 0
        self.pager: dict[str, str] = {}
        self._cur: dict | None = None
        self._buf: list[str] = []
        self._raw: list[str] = []
        self._dt = False
        self._cast_part: str | None = None
        self._cast_name = ""
        self._in_toc = False

    def _classes(self) -> set[str]:
        return {c for _, cl in self.stack for c in cl}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cl = set((a.get("class") or "").split())
        self.stack.append((tag, cl))
        if tag == "img" and "src" in a and "step" in self._classes():
            self.images.append(len(a["src"]))
            self.media.add(self.step)
        if "term" in cl and self.step:
            self.media.add(self.step)
        if tag == "div" and a.get("class", "").startswith("step") and re.fullmatch(r"s\d+", a.get("id", "") or "") and "step" in cl:
            self.step = int(a["id"][1:])
            self.steps += 1
        if tag == "nav" and "toc" in cl:
            self._in_toc = True
        if tag == "li" and self._in_toc:
            self.toc += 1
        if tag == "a" and ("prev" in cl or "next" in cl):
            self.pager["prev" if "prev" in cl else "next"] = a.get("href", "")
        if tag == "dt":
            self._dt = True
        if "name" in cl and "card" in {c for _, k in self.stack for c in k}:
            self._cast_part = "name"
        elif "role" in cl and "card" in {c for _, k in self.stack for c in k}:
            self._cast_part = "role"
        kinds = {"lede": "lede", "act-sub": "act", "step-title": "title", "step-screen": "screen", "step-text": "text", "note-body": "note"}
        for c in cl:
            if c in kinds and self._cur is None:
                self._cur = {"kind": kinds[c], "step": self.step}
                self._buf, self._raw = [], []
        if self._cur is not None:
            self._raw.append(self.get_starttag_text() or "")

    def handle_endtag(self, tag):
        if self._cur is not None:
            self._raw.append(f"</{tag}>")
        while self.stack:
            t, cl = self.stack.pop()
            if t == tag:
                kinds = {"lede", "act-sub", "step-title", "step-screen", "step-text", "note-body"}
                if self._cur is not None and cl & kinds:
                    text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
                    self._cur.update(text=text, html="".join(self._raw))
                    self.blocks.append(self._cur)
                    self._cur = None
                if tag == "nav":
                    self._in_toc = False
                if tag == "dt":
                    self._dt = False
                if "name" in cl or "role" in cl:
                    self._cast_part = None
                break

    def handle_data(self, data):
        classes = self._classes()
        if {"term"} & classes or any(t == "pre" for t, _ in self.stack):
            return
        if self._cur is not None:
            self._buf.append(data)
            self._raw.append(data)
        if self._dt and data.strip():
            self.glossary.append(data.strip().lower())
        if self._cast_part == "name":
            self._cast_name = data.strip()
        elif self._cast_part == "role" and data.strip():
            self.cast.append((self._cast_name, data.strip()))



def listed(org: str) -> list[str]:
    return [e["slug"] for e in SERIES if e["org"] == org]


def check_page(slug: str, org: str) -> tuple[list[str], list[str], list[tuple[str, str]]]:
    path = PUBLIC / f"{slug}-walkthrough.html"
    p = Page()
    p.feed(path.read_text(encoding="utf-8"))
    fail: list[str] = []
    review: list[str] = []
    blocks = p.blocks
    prose = [b for b in blocks if b["kind"] in {"lede", "act", "title", "text", "note"}]
    narrative = [b for b in blocks if b["kind"] in {"lede", "act", "text"}]
    everything = " \n ".join(b["text"] for b in prose)

    def hits(rx: str, src: str = everything, flags: int = 0) -> list[str]:
        return sorted({m.group(0) for m in re.finditer(rx, src, flags)})[:4]

    # FAIL
    if h := hits(r"verify/[\w./-]+"):
        fail.append(f"a verification script is named: {h}")
    if h := hits(r"(?:docs/tools|web/walkthroughs|scripts)/[\w./-]+"):
        fail.append(f"a path to a script or builder is in the text: {h}")
    if h := hits(r"\b(?:earlier|previous|next|last|other) (?:page|pages|walkthrough|walkthroughs)\b(?!,? [A-Z])|on an earlier|in the page before", flags=re.I):
        fail.append(f"refers to another page without naming it: {h}")
    if h := hits(r" ,| \.(?=\s|$)|\.\.(?!\.)|\( "):
        fail.append(f"broken spacing or punctuation: {h}")
    bad = [re.sub(r"<[^>]+>", "", b["html"])[:70] for b in blocks if b["kind"] == "note" and re.search(r"<strong>[^<]*[.?!]</strong>,? [a-z]", b["html"])]
    if bad:
        fail.append(f"screen text ends a sentence and the note goes on without quotation marks: {bad[:2]}")
    numbers = sorted({b["step"] for b in blocks if b["kind"] == "title"})
    steps = len(numbers)
    bare = [n for n in numbers if n not in p.media]
    if bare or (p.images and min(p.images) < 1000):
        fail.append(f"steps with neither a picture nor a code box: {bare}, or a picture that is empty")
    strip = re.search(r"<b>(\d+)</b>Steps", path.read_text(encoding="utf-8"))
    if strip and int(strip.group(1)) != steps:
        fail.append(f"the summary strip says {strip.group(1)} steps and the page has {steps}")
    if p.toc and p.toc != steps:
        fail.append(f"the contents list has {p.toc} entries and the page has {steps} steps")
    order = listed(org)
    if slug in order:
        i = order.index(slug)
        want_prev = f"{order[i - 1]}-walkthrough.html" if i else None
        want_next = f"{order[i + 1]}-walkthrough.html" if i < len(order) - 1 else None
        if p.pager.get("prev") != want_prev or p.pager.get("next") != want_next:
            fail.append(f"Previous and Next are {p.pager.get('prev')} and {p.pager.get('next')}, the series says {want_prev} and {want_next}")

    # REVIEW
    if h := hits(r"while this page was|the capture\b|recorded again|each time the capture|capture script", flags=re.I):
        review.append(f"talks about how the page was made: {h}")
    if h := hits(r"\bHTTP \d{3}\b|\bcode \d{3}\b"):
        review.append(f"a status code (is it explained?): {h}")
    caps = re.sub(r"\b[A-Z]{3,}\b", lambda m: "" if m.group(0) in KNOWN_CAPS else m.group(0), " ".join(b["text"] for b in narrative))
    if h := hits(r"\b[A-Z]{3,}(?:_[A-Z]+)*\b", caps):
        review.append(f"an ALL-CAPS value in the narrative (is it explained?): {h}")
    if h := hits(rf"{MONTH} \d{{1,2}}, 20\d\d", " ".join(b["text"] for b in narrative)):
        review.append(f"a date written into the text: {h}")
    doubled = sorted({m.group(0) for b in prose for m in re.finditer(r"(?<![-\w])(\w{2,})\s+\1\b", b["text"], flags=re.I)})
    if doubled:
        review.append(f"a doubled word: {doubled[:4]}")
    body = " ".join(b["text"] for b in narrative).lower()
    missing = []
    common = {"request", "purpose", "run", "role", "stage"}
    for key, (term, _meaning) in WORDS.items():
        t = term.lower()
        if t in common:
            continue
        if re.search(rf"\b{re.escape(t)}s?\b", body) and not any(g.startswith(t[:5]) for g in p.glossary):
            missing.append(term)
    if missing:
        review.append(f"uses words that this page's own list does not define: {missing}")
    return fail, review, p.cast


def main() -> int:
    wanted = sys.argv[1:]
    pages = [(s, org) for org in ORG_TITLES for s in listed(org)]
    if wanted:
        pages = [(s, o) for s, o in pages if s in wanted]
    failed = 0
    people: dict[str, dict[str, list[str]]] = {}
    for slug, org in pages:
        fail, review, cast = check_page(slug, org)
        for name, role in cast:
            people.setdefault(name, {}).setdefault(role, []).append(slug)
        status = "FAIL" if fail else ("review" if review else "ok")
        print(f"{status:6} {slug}")
        for line in fail:
            print(f"         FAIL    {line}")
        for line in review:
            print(f"         review  {line}")
        failed += bool(fail)
    shown = {n: r for n, r in people.items() if len(r) > 1}
    if shown and not wanted:
        print("\nOne person described in different words:")
        for name, roles in shown.items():
            print(f"  {name}")
            for role, where in roles.items():
                print(f"      {role}  ({len(where)} page{'s' if len(where) != 1 else ''})")
    print(f"\n{len(pages)} pages checked, {failed} with a failure.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
