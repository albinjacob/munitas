r"""Build docs/public/walkthroughs/people-administration-walkthrough.html from captured screenshots.

The screenshots come from web/walkthroughs/people-administration.spec.ts,
which drives the real console against the live stack. Run that first:

    cd web
    npx playwright test --config=walkthroughs/playwright.config.ts
    ..\.venv\Scripts\python.exe ..\docs\tools\build_people_walkthrough.py

Needs Pillow, listed in docs/tools/requirements.txt.

Two scripts rather than one because they answer to different tools: the
capture needs a browser and a running platform, and this needs neither. It
also means re-wording a step does not mean re-photographing the console.

PNGs are re-encoded as JPEG here. A walkthrough carries its images inline so
the page opens from the filesystem with nothing beside it, and ten full-width
PNGs base64-encoded is several megabytes of a file somebody is expected to
open in a browser.
"""
from __future__ import annotations

import base64
import io
from datetime import date
from pathlib import Path

from PIL import Image

DOCS = Path(__file__).resolve().parent.parent
SHOTS = DOCS.parent / "web" / "walkthroughs" / "shots"
OUT = DOCS / "public" / "walkthroughs" / "people-administration-walkthrough.html"

QUALITY = 72

ACTORS = {
    "sam": ("Sam", "Researcher, asks for the role"),
    "priya": ("Priya", "Platform administrator, deliberately cannot grant it"),
    "hartley": ("Hartley", "Data custodian for Cardiology, decides it"),
}

# One entry per captured screenshot, in capture order. `actor` picks the
# colour of the chip; an `act` entry starts a new act above that step.
STEPS = [
    {
        "file": "01-sam-opens-the-directory.png",
        "actor": "sam",
        "act": (
            "ACT ONE",
            "Sam asks for a role",
            "A role is Munitas's name for a specific permission a person "
            "can hold, such as being allowed to review data that has had "
            "identifying details removed from it. Anybody in an "
            "organisation may ask to hold a role. Nobody may grant a role "
            "to themselves, which is exactly why asking is a screen with "
            "its own request and its own decision, rather than something "
            "a person could simply switch on.",
        ),
        "title": "Sam opens the directory, a page listing everyone in his organisation",
        "screen": "Directory &middot; /directory",
        "text": "The directory lists every person the organisation has "
                "registered, and which roles each of them already holds. "
                "What the directory could never show before this feature "
                "existed is how that list of roles changes over time: who "
                "asked for what, who decided it, and when.",
        "note": "Sam is signed in as a researcher, a role that does not "
                "make him accountable for any department's data. The "
                "directory lists every person in the organisation "
                "regardless of what they hold, because being allowed to "
                "see who holds a role is a different permission from being "
                "allowed to decide who holds one.",
    },
    {
        "file": "02-sam-fills-in-the-ask.png",
        "actor": "sam",
        "title": 'Asks to hold the role named deid_reviewer',
        "screen": "Directory &middot; ask form",
        "text": "Sam fills in two fields: which role he wants to hold, "
                "named here as deid_reviewer (short for de-identification "
                "reviewer, the role that allows someone to review data "
                "that has already had identifying details removed from "
                "it), and a written reason for wanting it. The reason is "
                "not a formality. It is stored together with the request "
                "and read by whoever decides it.",
        "note": "Both fields are free text that Sam controls, but filling "
                "them in and submitting this form does not grant Sam "
                "anything by itself. It only creates a request, waiting "
                "for somebody else to decide, which the next step shows "
                "directly.",
    },
    {
        "file": "03-the-ask-is-waiting.png",
        "actor": "sam",
        "title": "The request is waiting, and Sam cannot decide it himself",
        "screen": "Directory &middot; /directory",
        "text": "Sam's own request appears on this page with no buttons "
                "next to it, and the page states in words why there are "
                "none, rather than leaving an unexplained gap where "
                "buttons might otherwise be. The list labelled Roles held, "
                "showing which roles Sam actually holds right now, is "
                "still empty.",
        "note": "Two facts on this page matter together. The request now "
                "appears in the list of pending requests, and Roles held "
                "is still empty. Asking changed what is on record. It did "
                "not change what Sam is actually allowed to do yet.",
    },
    {
        "file": "04-the-administrator-sees-the-ask.png",
        "actor": "priya",
        "act": (
            "ACT TWO",
            "The administrator is refused",
            "Priya is the platform administrator: the person responsible "
            "for running Munitas itself, not for any one department's "
            "data. This act shows the refusal the whole feature exists to "
            "demonstrate. Priya runs the software, and that is exactly "
            "why she is not allowed to decide who holds a role on it: "
            "somebody who could both run the platform and decide access "
            "on it would be able to grant themselves anything.",
        ),
        "title": "Priya, the platform administrator, opens the same directory page",
        "screen": "Directory &middot; /directory",
        "text": "Priya sees the same pending request Sam made, and the "
                "buttons to approve or refuse it are shown to her: Munitas "
                "does not decide in advance who is allowed to click a "
                "button, so it never hides one and leaves a person "
                "guessing what might have been possible.",
        "note": "Priya's own role, shown beside her name on this page, is "
                "platform administrator, not data custodian for any "
                "department. The buttons being shown to her anyway is "
                "what makes the refusal in the next step a real, earned "
                "one, decided by the rules that check every access "
                "request, rather than the page simply hiding the option "
                "to protect her from a mistake.",
    },
    {
        "file": "05-the-administrator-is-refused.png",
        "actor": "priya",
        "title": "Priya clicks Approve, and the platform refuses her in its own words",
        "screen": "Directory &middot; after clicking Approve",
        "text": "The page did not simply decline to draw a button here. "
                "Munitas checked the request against its access rules and "
                "answered with a specific sentence, shown exactly as "
                "written: <em>this principal holds no role that may "
                "decide who holds a role</em>. \"Principal\" is Munitas's "
                "word for whichever person or piece of automated code is "
                "being checked, Priya in this case. If an administrator "
                "could grant the role named data_custodian, the "
                "platform's own claim about what an administrator is "
                "allowed to do would stop being true.",
        "note": "Read the refusal sentence itself, not only its red "
                "colour: it names the specific reason (no role that may "
                "decide who holds a role), not a generic \"not allowed\". "
                "That precise wording comes from the same access rules "
                "that check every request for access made anywhere on "
                "this platform, not from a message written specifically "
                "for this screen.",
    },
    {
        "file": "06-the-custodian-records-a-reason.png",
        "actor": "hartley",
        "act": (
            "ACT THREE",
            "A custodian decides, and the grant starts out unreviewed",
            "Granting a role is one department's own job, done by the "
            "same person who already decides who may read that "
            "department's data: its data custodian. That is Hartley here, "
            "the data custodian for the Cardiology department.",
        ),
        "title": "Hartley, the data custodian for Cardiology, writes down why he is granting it",
        "screen": "Directory &middot; /directory",
        "text": "A decision made with no reason recorded would be a fact "
                "with the useful half missing: what happened, but not "
                "why. Munitas stores the reason together with the "
                "outcome, on the same request Sam originally made.",
        "note": "Hartley's role, data custodian for Cardiology, is scoped "
                "to one department, unlike Priya's platform-wide "
                "administrator role from Act Two. That department scoping "
                "is exactly what lets Hartley decide this particular "
                "request, when Priya, a moment ago, could not.",
    },
    {
        "file": "07-granted-but-never-checked.png",
        "actor": "hartley",
        "title": "The role is granted, and immediately marked as never yet reviewed",
        "screen": "Directory &middot; Roles held",
        "text": "The row for this grant states who approved it and when "
                "it automatically expires, plus one more fact in amber "
                "text: never checked. Being granted a role and having "
                "that grant reviewed afterward are two different events, "
                "and this page refuses to let them look the same.",
        "note": "The amber \"never checked\" label sits directly beside "
                "who approved the grant and when. Granting a role and "
                "later reviewing whether it is still needed are recorded "
                "as two separate facts on the same row, never combined "
                "into a single status.",
    },
    {
        "file": "08-somebody-confirms-it-is-still-needed.png",
        "actor": "hartley",
        "title": "Somebody confirms the grant is still needed",
        "screen": "Directory &middot; Roles held, after confirming",
        "text": "Confirming a grant does not extend or renew it in any "
                "way. It only records that a specific person looked at it "
                "on a specific date, which is the exact question somebody "
                "auditing the organisation later would ask, and the "
                "question most systems have no record to answer.",
        "note": "Compare this row with the previous step: the amber "
                "\"never checked\" label is gone, replaced with the date "
                "somebody checked it. Nothing about when the grant "
                "expires, or what it covers, changed. Only the fact that "
                "somebody looked was added.",
    },
    {
        "file": "09-withdrawn-and-gone.png",
        "actor": "hartley",
        "title": "Withdrawing the role ends it immediately",
        "screen": "Directory &middot; Roles held, after withdrawing",
        "text": "Withdrawing a role revokes it rather than deleting the "
                "record of it ever having existed, so anything Sam did "
                "while he held the role can still be traced back to him. "
                "The Roles held list shows what is true right now, not "
                "everything that has ever been granted.",
        "note": "The row disappears from Roles held, but nothing on this "
                "page claims the grant never happened. A separate audit "
                "record, not shown in this walkthrough, still keeps who "
                "approved the grant, when, and when it was withdrawn.",
    },
]


def encoded(path: Path) -> str:
    """The screenshot as an inline JPEG data URI."""
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        buffer = io.BytesIO()
        rgb.save(buffer, format="JPEG", quality=QUALITY, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def strip_tags(text: str) -> str:
    """The plain words of a step title, for the contents list."""
    out, depth = [], 0
    for char in text:
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
        elif depth == 0:
            out.append(char)
    return "".join(out)


def stylesheet() -> str:
    """The walkthrough styling, taken from the page that established it.

    Copied rather than shared as a file because a walkthrough has to open
    standalone from the filesystem. The one rename is the actor colour: the
    original names its two people, and this flow has three.
    """
    source = (DOCS / "public" / "walkthroughs" / "custom-pipeline-walkthrough.html").read_text(encoding="utf-8")
    style = source[source.index("<style>"): source.index("</style>") + len("</style>")]
    style = style.replace("devi", "sam").replace("hartley", "hartley")
    # A third actor, coloured from the same ramp the other two came from.
    extra = """
<style>
  :root {
    --priya: #a4562a; --priya-bg: #fbeee4; --priya-line: #edcdb4;
    --note: #9a6b00; --note-bg: #fdf3d9; --note-line: #f0dea3;
  }
  :root:not([data-theme="light"]) {
    @media (prefers-color-scheme: dark) {
      --priya: #f0a771; --priya-bg: #3a2415; --priya-line: #5e3c22;
      --note: #e0b64c; --note-bg: #3a2f0c; --note-line: #5c4b16;
    }
  }
  :root[data-theme="dark"] {
    --priya: #f0a771; --priya-bg: #3a2415; --priya-line: #5e3c22;
    --note: #e0b64c; --note-bg: #3a2f0c; --note-line: #5c4b16;
  }
  .cast .dot.priya { background: var(--priya); }
  .actor-chip.priya {
    background: var(--priya-bg); border-color: var(--priya-line); color: var(--priya);
  }
  .actor-chip.priya .dot { background: var(--priya); }

  /*
    Which screen, and what to notice on it: standing requirement in
    CLAUDE.md's "UI flow walkthroughs" section, so a step never reduces to a
    picture with a one-line caption.
  */
  .step-screen {
    display: block; margin: 2px 0 8px; font-family: "SF Mono", ui-monospace,
    Consolas, monospace; font-size: 12px; color: var(--ink-faint);
  }
  .step-note {
    display: flex; gap: 8px; margin: 10px 0 14px; padding: 10px 12px;
    border-radius: 8px; border: 1px solid var(--note-line);
    background: var(--note-bg); font-size: 13.5px; color: var(--ink);
  }
  .step-note::before {
    content: "Look for"; flex: none; font-weight: 700; font-size: 11px;
    text-transform: uppercase; letter-spacing: 0.06em; color: var(--note);
    padding-top: 1.5px;
  }
</style>"""
    return style + extra


def hero() -> str:
    cards = "\n".join(
        f'    <div class="card">\n      <span class="dot {key}"></span>\n'
        f"      <div>\n"
        f'        <div class="name">{name}</div>\n'
        f'        <div class="role">{role}</div>\n'
        f"      </div>\n    </div>"
        for key, (name, role) in ACTORS.items()
    )
    return f"""
<header class="hero">
  <div class="eyebrow">Munitas &middot; Demo organisation</div>
  <h1>Who is allowed to grant a role, and who is not</h1>
  <p class="lede">
    Munitas is a governance platform: it decides who may read which piece of
    data, and it keeps a permanent record of who granted, refused, or
    withdrew that permission. A role is Munitas's name for a specific
    permission a person can hold, such as being allowed to review data that
    has had identifying details removed from it. This walkthrough follows
    one real example: a researcher asks to hold a role, the platform
    administrator who runs the software is refused when she tries to grant
    it to him, a department's own data custodian grants it instead, and the
    grant arrives marked as never yet reviewed. Every screenshot below was
    captured from one real, unedited run of this exact example.
  </p>
  <div class="cast-title">Who is involved</div>
  <div class="cast">
{cards}
  </div>
</header>
"""


FOOTER_TEMPLATE = """
<footer>
  Captured live against the <code>health</code> organisation by
  <code>web/walkthroughs/people-administration.spec.ts</code> and rendered by
  <code>docs/tools/build_people_walkthrough.py</code>. Re-run both when the flow changes.
  What the platform refuses here is proved separately by
  <code>verify/v72_role_administration.py</code>.
  <p class="updated">Last updated __UPDATED__.</p>
</footer>

<div id="lightbox">
  <span class="close-hint">Click anywhere to close</span>
  <img id="lightbox-img" src="" alt="">
</div>

<script>
  (function () {
    var box = document.getElementById("lightbox");
    var boxImg = document.getElementById("lightbox-img");

    document.querySelectorAll(".shot img").forEach(function (img) {
      img.addEventListener("click", function () {
        boxImg.src = img.src;
        boxImg.alt = img.alt;
        box.classList.add("open");
      });
    });

    box.addEventListener("click", function () {
      box.classList.remove("open");
      boxImg.src = "";
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        box.classList.remove("open");
        boxImg.src = "";
      }
    });
  })();
</script>
"""


def footer() -> str:
    return FOOTER_TEMPLATE.replace("__UPDATED__", date.today().isoformat())


def build() -> str:
    parts: list[str] = [
        "<title>Who holds which role: a walkthrough</title>",
        stylesheet(),
        hero(),
        '<div class="wrap">',
    ]

    contents = "\n".join(
        f'    <li><a href="#s{i}">{strip_tags(step["title"])}</a></li>'
        for i, step in enumerate(STEPS, start=1)
    )
    parts.append(
        '<nav class="toc">\n  <div class="toc-title">On this page</div>\n'
        f"  <ol>\n{contents}\n  </ol>\n</nav>"
    )

    open_act = False
    for i, step in enumerate(STEPS, start=1):
        if "act" in step:
            if open_act:
                parts.append("</section>")
            num, heading, sub = step["act"]
            parts.append(
                '<section class="act">\n  <div class="act-head">\n'
                f'    <span class="act-num">{num}</span>\n    <h2>{heading}</h2>\n'
                f'  </div>\n  <p class="act-sub">{sub}</p>'
            )
            open_act = True

        actor = step["actor"]
        name = ACTORS[actor][0]
        image = encoded(SHOTS / step["file"])
        parts.append(
            f'  <div class="step" id="s{i}">\n'
            f'    <div class="step-num">{i}</div>\n'
            f'    <div class="step-body">\n'
            f'      <span class="actor-chip {actor}"><span class="dot"></span>{name}</span>\n'
            f'      <div class="step-title">{step["title"]}</div>\n'
            f'      <span class="step-screen">{step["screen"]}</span>\n'
            f'      <p class="step-text">{step["text"]}</p>\n'
            f'      <div class="step-note">{step["note"]}</div>\n'
            f'      <div class="shot"><img src="{image}" '
            f'alt="Screenshot: {strip_tags(step["title"])}" loading="lazy"></div>\n'
            f"    </div>\n  </div>"
        )
    if open_act:
        parts.append("</section>")

    parts.append("</div>")
    parts.append(footer())
    return "\n".join(parts) + "\n"


if __name__ == "__main__":
    missing = [s["file"] for s in STEPS if not (SHOTS / s["file"]).exists()]
    if missing:
        raise SystemExit(
            "No screenshot for: "
            + ", ".join(missing)
            + "\nRun the capture first: cd web && npx playwright test "
              "--config=walkthroughs/playwright.config.ts"
        )
    OUT.write_text(build(), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {len(STEPS)} steps)")
