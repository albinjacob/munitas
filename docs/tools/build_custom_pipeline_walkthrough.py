r"""Build docs/public/walkthroughs/custom-pipeline-walkthrough.html from captured screenshots.

The screenshots come from web/walkthroughs/custom-pipeline.spec.ts, which
drives the real console against the live stack. Run that first:

    cd web
    npx playwright test --config=walkthroughs/playwright.config.ts custom-pipeline
    ..\.venv\Scripts\python.exe ..\docs\tools\build_custom_pipeline_walkthrough.py

Needs Pillow, listed in docs/tools/requirements.txt.

This replaces the previous, hand-authored version of this page, which had
no spec behind it and carried a stale placeholder access-level name (`AL`)
nobody had a live run to catch. Every step below is captured from a real
run, following the same two-script pattern as the other walkthroughs:
build_people_walkthrough.py generates a sibling page the same way.
"""
from __future__ import annotations

import base64
import io
from datetime import date
from pathlib import Path

from PIL import Image

DOCS = Path(__file__).resolve().parent.parent
SHOTS = DOCS.parent / "web" / "walkthroughs" / "shots" / "custom-pipeline"
OUT = DOCS / "public" / "walkthroughs" / "custom-pipeline-walkthrough.html"

QUALITY = 72

ACTORS = {
    "devi": ("Devi", "Data engineer in Cardiology, builds and runs the pipeline"),
    "hartley": ("Hartley", "Data custodian for Cardiology, decides who may read its data"),
    "imani": ("Imani", "De-identification reviewer, the only role that may clear a gate"),
}

STEPS = [
    {
        "file": "01-pipelines-empty-for-this-department.png",
        "actor": "devi",
        "act": (
            "ACT ONE",
            "Devi registers a pipeline the platform never shipped",
            "The platform ships one built-in pipeline: de-identifying audio recordings. "
            "It has no way to know about every check an organisation might need. Devi's "
            "cardiology department needs cardiology notes checked for the structured "
            "fields it needs for its own quality registry reporting (ejection fraction, "
            "procedure type, complications) before a note is used more widely, which is "
            "not something a general-purpose platform would build in. So Devi registers "
            "her own pipeline instead of waiting for one to be shipped.",
        ),
        "title": "Devi opens Pipelines, empty in this organisation",
        "screen": "Pipelines &middot; /pipelines",
        "text": "Nobody has registered a pipeline in this organisation yet. Registering "
                "one only names it and its owning department; nothing about it can run "
                "until a version is uploaded.",
        "note": "The page is empty. That is the state before any operator has registered "
                "a pipeline of their own, not an error.",
    },
    {
        "file": "02-names-it-and-its-department.png",
        "actor": "devi",
        "title": "Names the pipeline and its owning department",
        "screen": "Pipelines &middot; register a pipeline",
        "text": "A pipeline is named cardiac-note-qc and owned by Cardiology, the same "
                "department Hartley is the data custodian for. Ownership decides who is "
                "answerable for what this pipeline does, exactly the way it does for a "
                "dataset.",
        "note": "The department field: Cardiology. Whoever owns a pipeline is answerable "
                "for it the same way a department is answerable for the datasets it owns.",
    },
    {
        "file": "03-registered-no-version-yet.png",
        "actor": "devi",
        "title": "Registered, but nothing can run yet",
        "screen": "Pipelines &middot; /pipelines/&lt;id&gt;",
        "text": "The pipeline exists as a name and an owner only. Nothing about it is "
                "readable or runnable until a version is sealed, the same rule that "
                "applies to a dataset or an agent.",
        "note": "No version is listed yet. Registering a pipeline is a separate act from "
                "giving it something to run.",
    },
    {
        "file": "04-uploads-the-dag-config-and-scripts.png",
        "actor": "devi",
        "title": "Uploads a pipeline.yaml and a scripts.zip",
        "screen": "Pipelines &middot; upload a version",
        "text": "Two files: a pipeline.yaml naming each step in order and which steps "
                "each one depends on, and a scripts.zip holding every script those steps "
                "reference by relative path. This pipeline has two steps: check_notes, "
                "Devi's own script, feeding a built-in decide gate. The DAG's step list "
                "is not a fixed list the platform already knew; it is read from these two "
                "files.",
        "note": "Two file inputs: the pipeline config and the scripts archive. Every "
                "step's dependencies and every script it names are checked before this "
                "version can seal.",
    },
    {
        "file": "05-version-1-is-sealed.png",
        "actor": "devi",
        "title": "Version 1 is sealed",
        "screen": "Pipelines &middot; /pipelines/&lt;id&gt;",
        "text": "Every step's dependencies and every script it names were checked before "
                "this version could seal. There is no editing a sealed version, only "
                "registering the next one, the same immutability rule a sealed dataset "
                "version or agent version already follows.",
        "note": "Version 1 now appears on the pipeline's own page, sealed and ready to "
                "be picked from the run picker on any dataset version.",
    },
    {
        "file": "06-registers-the-dataset.png",
        "actor": "devi",
        "act": (
            "ACT TWO",
            "A dataset comes in, and Devi has to ask",
            "Registering a pipeline does not grant Devi any right to read data with it. "
            "A cardiology note still has to be registered, sealed, and requested for, "
            "exactly as if no custom pipeline existed at all.",
        ),
        "title": "Registers cardiology-intake-notes",
        "screen": "Datasets &middot; /datasets/register",
        "text": "Naming a dataset also names who is answerable for it. Devi leaves "
                "sensitivity at Raw, the safe default: claiming anything less sensitive "
                "is a claim recorded under her own name, which Cardiology's custodian, "
                "Hartley, would then have to agree with.",
        "note": "The department is Cardiology, and the sensitivity field reads Raw. "
                "Nothing about this form assumes the note is safer than it might be.",
    },
    {
        "file": "07-uploads-a-file-and-seals-version-1.png",
        "actor": "devi",
        "title": "Uploads a note and seals version 1",
        "screen": "Datasets &middot; upload and seal",
        "text": "Nothing in the dataset is readable until it is sealed. Sealing closes "
                "the upload: nothing can be added to this version afterwards.",
        "note": "The sealed-version confirmation. Nothing further can be added to this "
                "version; a later change would be a new version, not an edit.",
    },
    {
        "file": "08-tries-to-run-and-has-to-ask-first.png",
        "actor": "devi",
        "title": "Tries to run against it, and has to ask first",
        "screen": "Version detail &middot; /versions/&lt;id&gt;",
        "text": "Raw is more sensitive than Devi's own role reads by default, so the "
                "version page asks her what she needs it for, why nothing less sensitive "
                "would do, and for how long. She writes in the reason her pipeline "
                "exists for: checking required registry fields before this note is used "
                "more widely.",
        "note": "A request form in place of a run button. Devi cannot decide this "
                "for herself, no matter what she writes in it.",
    },
    {
        "file": "09-the-request-goes-to-cardiologys-custodian.png",
        "actor": "devi",
        "title": "The request goes to Cardiology's custodian",
        "screen": "Version detail &middot; request sent",
        "text": "Nothing is readable until Hartley grants it. Devi cannot approve her "
                "own request no matter what she wrote in it.",
        "note": "The request is recorded as sent, and no read access exists yet: those "
                "are two separate facts, not one.",
    },
    {
        "file": "10-sees-devis-request-waiting.png",
        "actor": "hartley",
        "act": (
            "ACT THREE",
            "Cardiology's own custodian decides",
            "Hartley is the data custodian for Cardiology: the person accountable for "
            "who may read what this department owns, including a note Devi registered "
            "herself.",
        ),
        "title": "Hartley signs in and sees Devi's request waiting",
        "screen": "Home &middot; pending requests",
        "text": "Hartley's queue is scoped to what Cardiology owns: one request "
                "waiting, with what Devi wrote laid out plainly.",
        "note": "The request names its purpose in Devi's own words: checking required "
                "registry fields before this note is used more widely.",
    },
    {
        "file": "11-grants-access.png",
        "actor": "hartley",
        "title": "Grants access",
        "screen": "Home &middot; after granting",
        "text": "Granting gives Devi read access for this purpose only, and it ends by "
                "itself; nobody has to remember to take it away. The queue is now empty.",
        "note": "The request has left the pending queue. That is what a granted request "
                "looks like: gone from here, live on the version page Devi will return to.",
    },
    {
        "file": "12-access-shows-up-on-the-version-page.png",
        "actor": "devi",
        "act": (
            "ACT FOUR",
            "Devi runs her own pipeline",
            "With access granted, the version page now offers a run picker instead of a "
            "request form, and Devi's own pipeline appears in it next to the platform's "
            "built-in one.",
        ),
        "title": "Access shows up on the version page",
        "screen": "Version detail &middot; /versions/&lt;id&gt;",
        "text": "In place of the request form is now a run picker: the version may be "
                "read for the purpose Devi asked with, until the grant expires on its own.",
        "note": "The request form is gone, replaced by a pipeline picker. That is what "
                "a live grant looks like on this page.",
    },
    {
        "file": "13-picks-her-own-pipeline-from-the-run-picker.png",
        "actor": "devi",
        "title": "Picks her own pipeline from the run picker",
        "screen": "Version detail &middot; run picker",
        "text": "The dropdown that always offered the built-in de-identification pipeline "
                "now also offers cardiac-note-qc (v1), the pipeline she registered in Act "
                "One, listed next to the platform's own starter template.",
        "note": "cardiac-note-qc (v1) in the pipeline dropdown, alongside the built-in "
                "options. Registering a pipeline is what put it there.",
    },
    {
        "file": "14-starts-the-run.png",
        "actor": "devi",
        "title": "Starts the run",
        "screen": "Pipeline run &middot; /pipeline-runs/&lt;id&gt;",
        "text": "The run page updates itself. Nothing is released until a reviewer "
                "decides that separately, which holds for an operator's own pipeline "
                "exactly as it does for the built-in one.",
        "note": "The run status reads running now. Nothing on this page has been "
                "released to anybody yet.",
    },
    {
        "file": "15-both-steps-finish.png",
        "actor": "devi",
        "title": "Both steps finish",
        "screen": "Pipeline run &middot; /pipeline-runs/&lt;id&gt;",
        "text": "check_notes, Devi's own script, ran first; decide, the built-in gate "
                "step, ran after it. This step list is not a fixed list the console "
                "already knew: it is read straight from what this run actually reported.",
        "note": "Two step rows, check_notes and decide, both marked done. Neither name "
                "came from a list the console had built in; both came from this run's "
                "own report.",
    },
    {
        "file": "16-opens-the-gate-decision-and-is-blocked.png",
        "actor": "devi",
        "act": (
            "ACT FIVE",
            "The same gate decision every pipeline ends in",
            "The terminal step of any DAG pipeline is always a real gate decision, so "
            "review and promotion need no code specific to this pipeline. Only one role "
            "on the platform, the de-identification reviewer, may clear any gate, "
            "regardless of what the pipeline actually checks.",
        ),
        "title": "Opens the gate decision, and is blocked",
        "screen": "Gate decision &middot; /gates/&lt;id&gt;",
        "text": "Devi started this run, so the console will not let her also be the one "
                "who clears it. Releasing it would move the note from Raw to Open for "
                "annotation, and cannot be undone, which is exactly why it is not her "
                "call.",
        "note": "You started this run, so somebody else has to clear it: the console's "
                "own words, not a disabled button with no explanation.",
    },
    {
        "file": "17-the-reviewer-writes-why.png",
        "actor": "imani",
        "title": "Imani, the reviewer, writes down why",
        "screen": "Gate decision &middot; /gates/&lt;id&gt;",
        "text": "Imani holds the one role that may clear any gate decision on the "
                "platform, not just the built-in pipeline's. She writes that the "
                "required registry fields are present and well-formed before the "
                "buttons can be used.",
        "note": "A written reason, required before either button can be clicked. A "
                "decision with no reason recorded would be a fact with the useful half "
                "missing.",
    },
    {
        "file": "18-cleared-and-promoted.png",
        "actor": "imani",
        "title": "Clears it, and the note is released",
        "screen": "Gate decision &middot; after releasing",
        "text": "The note moves from Raw to Open for annotation, the access level where "
                "annotators can start working with it. That promotion, and the reason "
                "behind it, are both on the record.",
        "note": "The state reads Released. Nothing about which pipeline produced this "
                "gate decision changed how it had to be cleared.",
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
    """The walkthrough styling, taken from the page that established it, plus
    the screen-label/observe-callout rules introduced for the newer
    walkthroughs (people-administration's own build script carries the
    same addition, copied here rather than shared as a file because a
    walkthrough has to open standalone from the filesystem).
    """
    source = (DOCS / "public" / "walkthroughs" / "healthcare-walkthrough.html").read_text(encoding="utf-8")
    style = source[source.index("<style>"): source.index("</style>") + len("</style>")]
    return style


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
  <h1>Registering and running your own pipeline</h1>
  <p class="lede">
    The platform ships one built-in pipeline: de-identifying audio recordings. It has no way
    to know about every check an organisation might need, so an operator can register their
    own instead. This walkthrough follows Devi, a data engineer in Cardiology, building a
    pipeline the platform never shipped: checking that a cardiology note has the structured
    fields Cardiology needs for its own quality reporting (things like ejection fraction,
    procedure type, and complications) before the note goes anywhere else. Once she registers
    it, this pipeline is held to the exact same rules as the built-in one: another person's
    data still has to be requested before it can be read, a custodian still decides, and the
    run still ends in a gate decision nobody can skip. Every screenshot below was captured
    from one real, unedited run of this exact example.
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
  <code>web/walkthroughs/custom-pipeline.spec.ts</code> and rendered by
  <code>docs/tools/build_custom_pipeline_walkthrough.py</code>. Re-run both when the flow changes.
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
        "<title>Registering and running your own pipeline: a walkthrough</title>",
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
              "--config=walkthroughs/playwright.config.ts custom-pipeline"
        )
    OUT.write_text(build(), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {len(STEPS)} steps)")
