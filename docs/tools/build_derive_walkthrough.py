r"""Build docs/public/walkthroughs/derive-walkthrough.html from a recorded run.

Two real recordings feed this page, and nothing on it is mocked:

  * The console screens come from web/walkthroughs/derive.spec.ts, which drives
    the real console against the live stack as the Health organisation's own
    people.
  * The notebook steps have no screen, so they show the real code and the real
    output that scripts/demo/derive-demo.py printed, saved by its --transcript
    option. The code on the page is the exact text that ran.

Run them in this order, then this builder:

    .venv\Scripts\python.exe scripts\seed\seed-derive-demo-data.py
    .venv\Scripts\python.exe scripts\demo\derive-demo.py --tenant health --name older-chronic-heart-patients ^
        --transcript web\walkthroughs\shots\derive\transcript-health.json
    .venv\Scripts\python.exe scripts\demo\derive-demo.py --tenant finance --name high-value-foreign-transfers ^
        --transcript web\walkthroughs\shots\derive\transcript-finance.json
    cd web
    npx playwright test --config=walkthroughs/playwright.config.ts derive
    ..\.venv\Scripts\python.exe ..\docs\tools\build_derive_walkthrough.py

Needs Pillow, listed in docs/tools/requirements.txt.
"""
from __future__ import annotations

import base64
import html
import io
import json
from datetime import date
from pathlib import Path

from PIL import Image

DOCS = Path(__file__).resolve().parent.parent
SHOTS = DOCS.parent / "web" / "walkthroughs" / "shots" / "derive"
OUT = DOCS / "public" / "walkthroughs" / "derive-walkthrough.html"
QUALITY = 72

TRANSCRIPTS = {
    "health": json.loads((SHOTS / "transcript-health.json").read_text(encoding="utf-8")),
    "finance": json.loads((SHOTS / "transcript-finance.json").read_text(encoding="utf-8")),
}

ACTORS = {
    "sam": ("Sam", "Researcher, Health organisation"),
    "hartley": ("Hartley", "Data custodian for the Cardiology department"),
    "devi": ("Devi", "Pipeline engineer, Health organisation"),
    "omar": ("Omar", "Analyst, Finance organisation"),
    "marcus": ("Marcus", "Data custodian for the Fraud Operations department"),
}

NOTEBOOK_DUCKDB = "Notebook &middot; DuckDB, a free tool for analysing tables"
NOTEBOOK_CLIENT = "Notebook &middot; the Munitas client library"

STEPS = [
    # ------------------------------------------------------------------ one --
    {
        "act": ("ACT ONE", "A closed table, and a request for access",
                "Munitas is a governance platform: it decides who may read which piece of data, and it keeps a "
                "permanent record of every decision. The Cardiology department of a hospital group owns a "
                "dataset of admissions, which is a named collection of records. Every record names a patient. "
                "A researcher wants to study it. The first act shows the researcher being stopped, asking, and "
                "being answered by a different person."),
        "kind": "code", "source": "health", "scenes": [1], "actor": "sam",
        "title": "Sam connects an analysis tool and sees only the table that is open to everyone",
        "screen": NOTEBOOK_DUCKDB,
        "text": "Sam is a researcher in the Health organisation. A catalog is a list of tables that analysis "
                "tools can open. To connect, Sam first asks the platform for a token, which is a short-lived "
                "password that also states why Sam wants to read, here a readmission study. Sam then attaches "
                "the catalog in DuckDB and lists what the platform shows.",
        "note": "The list holds one dataset, <code>diagnosis_codes</code>, a public lookup of diagnosis names. "
                "The admissions dataset is not listed at all, because Sam may not read it.",
    },
    {
        "kind": "code", "source": "health", "scenes": [2], "actor": "sam",
        "title": "The restricted table is closed, and the platform says why",
        "screen": NOTEBOOK_DUCKDB,
        "text": "The admissions dataset has the access level Raw, which is the most restricted level: nobody "
                "may read it without a lease. A lease is permission to read one dataset, for one stated "
                "purpose, for a limited time, that the owning department's custodian has approved. A custodian "
                "is the person accountable for who may read that department's data. Sam tries to read the "
                "table anyway.",
        "note": "The refusal ends with <code>no role reaches class RAW</code>. Sam's role, researcher, "
                "reaches the Published level and nothing above it. The platform gave the reason instead of "
                "a bare error.",
    },
    {
        "kind": "shot", "file": "01-sam-finds-admissions-closed.png", "actor": "sam",
        "title": "The console shows the same closed table",
        "screen": "Datasets &middot; /datasets",
        "text": "The console is the platform's own web screen for people. Signed in as Sam in the Health "
                "organisation, the datasets list shows the admissions dataset, owned by the Cardiology "
                "department, with its access level.",
        "note": "The <strong>You can read</strong> column says <strong>0 of 1</strong>, and the access level "
                "is <strong>Raw</strong>, the most restricted level. The dataset has one version and Sam may "
                "read none of it.",
    },
    {
        "kind": "shot", "file": "02-sam-fills-in-the-request.png", "actor": "sam",
        "title": "Sam asks the Cardiology custodian for access",
        "screen": "A dataset version &middot; /versions/&lt;version id&gt;",
        "text": "On the version page, Sam writes what the data will be used for, why something less "
                "sensitive would not do, and for how long. The request goes to Hartley, the custodian of "
                "the Cardiology department, because Cardiology owns the data. A custodian is the person accountable "
                "for who may read a department's data.",
        "note": "The lines above the form record earlier attempts by the same person (an earlier lease, "
                "which is time-limited permission to read, that ended, and an earlier request that was refused). The page keeps that history in view when "
                "somebody asks again. Below them, the stated purpose is <strong>readmission study</strong>.",
    },
    {
        "kind": "shot", "file": "04-hartley-sees-the-request.png", "actor": "hartley",
        "title": "Hartley sees the request, in the queue for Cardiology",
        "screen": "Custodian home &middot; /",
        "text": "Hartley is the custodian of the Cardiology department, the person accountable for who may read "
                "its data, so the home screen lists requests to "
                "read data that Cardiology owns. Nobody else in the Health organisation can decide this "
                "request, and Sam cannot approve a request made under Sam's own name.",
        "note": "The first card reads <strong>Sam (Researcher) wants to read admissions v1</strong>, with the "
                "reason Sam wrote and two buttons, <strong>Grant access</strong> and <strong>Refuse</strong>. "
                "The text under them says what granting means: a fixed time, this purpose only, and it ends "
                "by itself.",
    },
    {
        "kind": "shot", "file": "05-after-granting.png", "actor": "hartley",
        "title": "Hartley grants it, and the decision is recorded",
        "screen": "Custodian home &middot; /",
        "text": "Hartley chooses Grant access. The request leaves the queue and a lease now exists for Sam. A lease "
                "is time-limited permission to read one dataset for the stated purpose.",
        "note": "The notice at the bottom right reads <strong>Access granted to Sam (Researcher)</strong>, and "
                "the card <strong>Currently granted</strong> now counts one.",
    },
    {
        "kind": "code", "source": "health", "scenes": [4], "actor": "sam",
        "title": "With the lease, the restricted table opens",
        "screen": NOTEBOOK_DUCKDB,
        "text": "The lease Hartley granted is the time-limited permission to read this dataset for the stated "
                "purpose. The same DuckDB connection can now read the admissions table. Every name in it is "
                "invented for this example. The platform treats a column like <code>patient_name</code> as "
                "a direct identifier, which is why the dataset is restricted and why anything made from it "
                "has to stay as careful.",
        "note": "Real rows come back, with patient names. A moment earlier the same query was refused.",
    },
    # ------------------------------------------------------------------ two --
    {
        "act": ("ACT TWO", "Making a new dataset from a query",
                "Sam now wants a smaller dataset, only the older patients with a long-term heart condition, "
                "to keep for the study. Munitas lets a person make a new dataset by writing a query, which "
                "is a question written in SQL, over datasets they may already read. The platform, not the "
                "person's own computer, runs the query, and the new dataset is held to the same rules as "
                "the data it came from."),
        "kind": "code", "source": "health", "scenes": [5], "actor": "sam",
        "title": "The platform shows its plan before anything runs",
        "screen": NOTEBOOK_CLIENT,
        "text": "Sam writes a query, which is a question written in the SQL language, that joins the restricted "
                "admissions with the public diagnosis lookup, "
                "keeps patients over 65 with a chronic condition, and names the result. The platform does "
                "not run it. It replies with the columns the result would have, and the sensitivity each "
                "column must carry. A sensitivity is a label for how identifying a column is: <code>none</code> "
                "means not identifying, <code>quasi</code> means a detail that could help identify a person "
                "when combined with others, and <code>phi</code> means protected health information.",
        "note": "<code>description</code>, which comes from the public lookup, is <strong>none</strong>. "
                "<code>diagnosis_code</code> and <code>readmitted_30d</code>, which come from the "
                "admissions, are <strong>phi</strong>. The last line says the new dataset would be "
                "<strong>RAW</strong>, the most restricted access level, because that is the strictest level among "
                "its inputs.",
    },
    {
        "kind": "code", "source": "health", "scenes": [6], "actor": "sam",
        "title": "A label cannot be lowered by the person who wrote the query",
        "screen": NOTEBOOK_CLIENT,
        "text": "Raising a sensitivity label is allowed. Lowering one would be a claim that data is safer "
                "than where it came from, and that claim needs somebody other than the person who wrote the "
                "query. Sam tries to mark the age column as not identifying anyway. The label <code>quasi</code> means "
                "a detail that could help identify a person when combined with others, and <code>none</code> "
                "means not identifying.",
        "note": "The platform refuses with the reason: the column is computed from a field marked "
                "<code>quasi</code>, so it cannot be marked <code>none</code>.",
    },
    {
        "kind": "code", "source": "health", "scenes": [7], "actor": "sam",
        "title": "Sam confirms, and the platform runs the query in an isolated container",
        "screen": NOTEBOOK_CLIENT,
        "text": "After Sam confirms, the platform copies the input files into a sandbox, which is a "
                "container that has no network connection and holds no credentials. The query runs there, "
                "and every row of the result is checked against the plan. The query never runs on Sam's "
                "own computer.",
        "note": "The status reads <strong>succeeded</strong> and the new dataset is again "
                "<strong>RAW</strong>, the most restricted access level. Sam does not choose that level: it "
                "follows from the inputs.",
    },
    {
        "kind": "shot", "file": "06-sam-finds-the-new-dataset.png", "actor": "sam",
        "title": "The new dataset appears in the console, owned by Cardiology",
        "screen": "Datasets &middot; /datasets",
        "text": "In the console, the platform's web screen for people, the new dataset is registered like any "
                "other. It is owned by the same department as the "
                "restricted data it came from, so Hartley stays the person accountable for it. Sam made "
                "it, so Sam can read it at once, without asking anybody.",
        "note": "The <strong>You can read</strong> column says <strong>1 of 1</strong> for the new dataset, "
                "and its access level is still <strong>Raw</strong>, the most restricted level. The name ends in a "
                "number only so "
                "that this example can be recorded again.",
    },
    {
        "kind": "code", "source": "health", "scenes": [8], "actor": "sam",
        "title": "The new dataset opens in DuckDB like any other table",
        "screen": NOTEBOOK_DUCKDB,
        "text": "The result is sealed, which means closed for good so that it can never be edited, and it "
                "is also an Iceberg table, an open table format that tools such as DuckDB read directly. "
                "Sam queries it.",
        "note": "The second result counts the patients in the new dataset and their average age, which is "
                "above 65 as the query required.",
    },
    {
        "kind": "shot", "file": "07-where-it-came-from.png", "actor": "sam",
        "title": "The console records where the new dataset came from",
        "screen": "A dataset version &middot; /versions/&lt;version id&gt;",
        "text": "The console is the platform's web screen for people. Every version of a dataset records what "
                "produced it. For a dataset made from a query, the record names "
                "the step, who ran it, and the exact versions of the datasets it was made from.",
        "note": "<strong>Your access</strong> reads <em>You can read this until</em> a date, for the stated "
                "purpose. <strong>Where this came from</strong> shows the step <strong>derive</strong> and "
                "two <strong>Made from</strong> versions, the admissions and the diagnosis lookup.",
    },
    {
        "kind": "code", "source": "health", "scenes": [9], "actor": "sam",
        "title": "The query itself is kept, exactly as written",
        "screen": NOTEBOOK_CLIENT,
        "text": "Anybody auditing the new dataset can follow it back. The platform keeps the purpose, the "
                "input versions with their access levels, and the text of the query, which is the question written in "
                "SQL.",
        "note": "The inputs line names <strong>admissions version 1 (RAW)</strong> and "
                "<strong>diagnosis_codes version 1 (PUBLISHED)</strong>, where RAW is the most restricted access level "
                "and PUBLISHED the most open. The query is shown word for word.",
    },
    # ---------------------------------------------------------------- three --
    {
        "act": ("ACT THREE", "The rules hold",
                "The new dataset is useful only if nobody can use it to get around the rules. The last act "
                "tests that: a colleague without access, a query that tries to reach outside its inputs, "
                "the record of every decision, and finally the custodian withdrawing the access."),
        "kind": "code", "source": "health", "scenes": [10], "actor": "devi",
        "title": "A colleague without access is refused",
        "screen": NOTEBOOK_DUCKDB,
        "text": "Devi is a pipeline engineer in the same organisation. The new dataset is as restricted as "
                "the data it came from, and Devi holds no lease on it, meaning no time-limited permission to read it, "
                "so Devi cannot open it.",
        "note": "The same reason appears as before: <code>no role reaches class RAW</code>, where RAW is the "
                "most restricted access level. Being in the same "
                "organisation as the data is not enough.",
    },
    {
        "kind": "code", "source": "health", "scenes": [11], "actor": "sam",
        "title": "A query that reaches outside its inputs is stopped before it runs",
        "screen": NOTEBOOK_CLIENT,
        "text": "A query, a question written in SQL, may read the datasets it declared and nothing else. Sam "
                "tries a query that reads a "
                "file from the machine that runs it.",
        "note": "The platform refuses with <strong>that query reaches outside the datasets it declared</strong>. "
                "Nothing ran.",
    },
    {
        "kind": "shot", "file": "08-the-decision-log.png", "actor": "hartley",
        "title": "Every refusal is on record, with the reason",
        "screen": "Who accessed what &middot; /audit",
        "text": "The decision log lists every request to read data and what happened. Each request is "
                "recorded twice: whether it was allowed, and whether access was actually given. Hartley "
                "can see all of it for the Health organisation, and here the log is filtered to the "
                "requests that were refused.",
        "note": "Every row in the <strong>Result</strong> column says <strong>no</strong>, and the "
                "<strong>Why</strong> column gives the reason, such as <code>no role reaches class "
                "RAW</code>, and the revoked leases (time-limited permissions to read) are named. One row shows a researcher from a "
                "different organisation refused even for a Published dataset, because the dataset "
                "belongs to another organisation. The <strong>What for</strong> column shows the "
                "purpose each person stated.",
    },
    {
        "kind": "code", "source": "health", "scenes": [12], "actor": "hartley",
        "title": "Hartley withdraws the lease, and everything built on it closes",
        "screen": NOTEBOOK_DUCKDB,
        "text": "Hartley, the custodian of the Cardiology department and so the person accountable for who may "
                "read its data, ends the lease, the time-limited permission to read. Sam loses the "
                "admissions table at once. Sam also loses the "
                "new dataset made from it, because the access to the new dataset was given on the strength "
                "of the lease that has now ended.",
        "note": "Both tables report <strong>refused</strong>, and the reasons name the revoked leases.",
    },
    {
        "kind": "shot", "file": "09-sam-after-the-lease-is-withdrawn.png", "actor": "sam",
        "title": "The console agrees",
        "screen": "Datasets &middot; /datasets",
        "text": "Back in the console, the platform's web screen for people, the new dataset is still listed, "
                "because Sam can still know it "
                "exists, but Sam can no longer read it.",
        "note": "The <strong>You can read</strong> column now says <strong>0 of 1</strong>, where it said "
                "<strong>1 of 1</strong> earlier.",
    },
    # ----------------------------------------------------------------- four --
    {
        "act": ("ACT FOUR", "The same story in a second organisation",
                "Nothing here is specific to hospitals. A card payments company in the Finance "
                "organisation holds transactions that name account holders. Its Fraud Operations "
                "department owns them, and an analyst makes a new dataset from a query in exactly the same way."),
        "kind": "code", "source": "finance", "scenes": [5], "actor": "omar",
        "title": "Omar, an analyst, asks for a plan",
        "screen": NOTEBOOK_CLIENT,
        "text": "Omar is an analyst in the Finance organisation, and Marcus is the custodian of the Fraud "
                "Operations department, the person accountable for who may read its data. After Marcus "
                "approved a lease on the transactions, which is time-limited permission to read them, Omar "
                "writes a query, a question written in SQL, that joins them with a public list of merchants "
                "and keeps large foreign payments.",
        "note": "<code>category</code> and <code>amount</code>, which are not identifying, are "
                "<strong>none</strong>. <code>country</code>, <code>occurred_at</code> and "
                "<code>flagged</code> are <strong>quasi</strong>. Here <strong>none</strong> means not identifying and "
                "<strong>quasi</strong> means a detail that could help identify a person when combined with "
                "others. The result would be <strong>RAW</strong>, the most restricted access level.",
    },
    {
        "kind": "code", "source": "finance", "scenes": [7, 8], "actor": "omar",
        "title": "The query runs, and the new dataset opens",
        "screen": NOTEBOOK_CLIENT + " and DuckDB",
        "text": "Omar confirms. The platform runs the query in its isolated container, which has no network and "
                "holds no credentials, seals the result so that it can never be edited, and Omar opens the "
                "new table in DuckDB.",
        "note": "The second result counts the matching transactions and adds up their amounts, from a "
                "dataset that holds no account holder names.",
    },
    {
        "kind": "code", "source": "finance", "scenes": [12], "actor": "marcus",
        "title": "Marcus withdraws the lease, and both tables close",
        "screen": NOTEBOOK_DUCKDB,
        "text": "Marcus ends the lease, the time-limited permission to read. Omar loses the transactions and the "
                "new dataset made from them.",
        "note": "Both tables report <strong>refused</strong>, exactly as in the Health organisation.",
    },
]

EXTRA_CSS = """
<style>
  :root {
    --sam: #6a4c9c; --sam-bg: #efe9f7; --sam-line: #d8c9ee;
    --omar: #2a7d6a; --omar-bg: #e3f3ee; --omar-line: #bfe3d8;
    --marcus: #a4562a; --marcus-bg: #fbeee4; --marcus-line: #edcdb4;
    --note: #9a6b00; --note-bg: #fdf3d9; --note-line: #f0dea3;
    --term-bg: #0f172a; --term-ink: #e2e8f0; --term-out: #94a3b8; --term-line: #1e293b;
  }
  :root:not([data-theme="light"]) {
    @media (prefers-color-scheme: dark) {
      --sam: #c3a8ec; --sam-bg: #2c2340; --sam-line: #473465;
      --omar: #7fd1ba; --omar-bg: #14302a; --omar-line: #24574a;
      --marcus: #f0a771; --marcus-bg: #3a2415; --marcus-line: #5e3c22;
      --note: #e0b64c; --note-bg: #3a2f0c; --note-line: #5c4b16;
    }
  }
  :root[data-theme="dark"] {
    --sam: #c3a8ec; --sam-bg: #2c2340; --sam-line: #473465;
    --omar: #7fd1ba; --omar-bg: #14302a; --omar-line: #24574a;
    --marcus: #f0a771; --marcus-bg: #3a2415; --marcus-line: #5e3c22;
    --note: #e0b64c; --note-bg: #3a2f0c; --note-line: #5c4b16;
  }
  .cast .dot.sam { background: var(--sam); }
  .cast .dot.omar { background: var(--omar); }
  .cast .dot.marcus { background: var(--marcus); }
  .actor-chip.sam { background: var(--sam-bg); border-color: var(--sam-line); color: var(--sam); }
  .actor-chip.sam .dot { background: var(--sam); }
  .actor-chip.omar { background: var(--omar-bg); border-color: var(--omar-line); color: var(--omar); }
  .actor-chip.omar .dot { background: var(--omar); }
  .actor-chip.marcus { background: var(--marcus-bg); border-color: var(--marcus-line); color: var(--marcus); }
  .actor-chip.marcus .dot { background: var(--marcus); }

  .step-screen {
    display: block; margin: 2px 0 8px; font-size: 12px; color: var(--ink-faint);
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
  .step, .step-body { min-width: 0; }
  .step-note .note-body { flex: 1; min-width: 0; }
  .term { border-radius: 10px; overflow: hidden; background: var(--term-bg); margin: 8px 0 4px; max-width: 100%; }
  .term pre {
    margin: 0; padding: 12px 14px; overflow-x: auto; font-size: 12.5px; line-height: 1.5;
    font-family: ui-monospace, "Cascadia Mono", "Segoe UI Mono", Consolas, "DejaVu Sans Mono", Menlo, monospace;
    white-space: pre; max-width: 100%;
  }
  .term .out { line-height: 1.25; }
  .term .code { color: var(--term-ink); }
  .term .out { color: var(--term-out); border-top: 1px solid var(--term-line); }
  .term .tag {
    display: block; padding: 6px 14px 0; font-size: 10.5px; letter-spacing: 0.08em;
    text-transform: uppercase; color: var(--term-out);
  }
</style>"""

FOOTER_TEMPLATE = """
<footer>
  The notebook steps are the real code and output from one recorded run of
  <code>scripts/demo/derive-demo.py</code>. The console screens were captured live against
  the <code>health</code> organisation by <code>web/walkthroughs/derive.spec.ts</code>. Both
  are rendered by <code>docs/tools/build_derive_walkthrough.py</code>. Run all three again
  when the flow changes. Every person, patient and card holder in the example data is invented.
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
        boxImg.src = img.src; boxImg.alt = img.alt; box.classList.add("open");
      });
    });
    box.addEventListener("click", function () { box.classList.remove("open"); boxImg.src = ""; });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { box.classList.remove("open"); boxImg.src = ""; }
    });
  })();
</script>
"""


def encoded(path: Path) -> str:
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        buffer = io.BytesIO()
        rgb.save(buffer, format="JPEG", quality=QUALITY, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def strip_tags(text: str) -> str:
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
    """The shared walkthrough styling, taken from the page that established it
    (copied, because a walkthrough has to open standalone from the filesystem)."""
    source = (DOCS / "public" / "walkthroughs" / "custom-pipeline-walkthrough.html").read_text(encoding="utf-8")
    style = source[source.index("<style>"): source.index("</style>") + len("</style>")]
    return style + EXTRA_CSS


def terminal(step: dict) -> str:
    transcript = TRANSCRIPTS[step["source"]]
    boxes = []
    for number in step["scenes"]:
        scene = next(s for s in transcript["scenes"] if s["step"] == number)
        for block in scene["blocks"]:
            boxes.append(
                '<div class="term">'
                f'<span class="tag">What was typed</span><pre class="code">{html.escape(block["code"])}</pre>'
                f'<span class="tag">What came back</span><pre class="out">{html.escape(block["output"] or "(nothing is printed)")}</pre>'
                "</div>")
    return "\n".join(boxes)


def hero() -> str:
    cards = "\n".join(
        f'    <div class="card">\n      <span class="dot {key}"></span>\n      <div>\n'
        f'        <div class="name">{name}</div>\n        <div class="role">{role}</div>\n      </div>\n    </div>'
        for key, (name, role) in ACTORS.items())
    return f"""
<header class="hero">
  <div class="eyebrow">Munitas &middot; Health and Finance organisations &middot; Governed datasets</div>
  <h1>Making a new dataset from a query, without loosening any rule</h1>
  <p class="lede">
    Munitas is a governance platform: it decides who may read which piece of data, and it keeps a
    permanent record of every decision. This walkthrough follows a researcher in a hospital group
    who is stopped at a restricted table of patient admissions, asks for access, and is answered
    by the custodian of the department that owns it. The researcher then makes a new dataset from
    a query. The platform runs the query itself, in an isolated container, checks the result, and
    keeps the new dataset exactly as restricted as the data it came from. When the custodian
    withdraws the access, the new dataset closes too. Some steps are screens of the console, the
    platform's web page for people, and some happen in a notebook, where no screen exists, so
    those show the real code that was typed and what it printed. All of it comes from real,
    unedited runs, and every name in the data is invented.
  </p>
  <div class="cast-title">Who is involved</div>
  <div class="cast">
{cards}
  </div>
</header>
"""


def build() -> str:
    parts = ["<title>Making a new dataset from a query: a walkthrough</title>", stylesheet(), hero(), '<div class="wrap">']
    contents = "\n".join(
        f'    <li><a href="#s{i}">{strip_tags(step["title"])}</a></li>' for i, step in enumerate(STEPS, start=1))
    parts.append(f'<nav class="toc">\n  <div class="toc-title">On this page</div>\n  <ol>\n{contents}\n  </ol>\n</nav>')

    open_act = False
    for i, step in enumerate(STEPS, start=1):
        if "act" in step:
            if open_act:
                parts.append("</section>")
            num, heading, sub = step["act"]
            parts.append('<section class="act">\n  <div class="act-head">\n'
                         f'    <span class="act-num">{num}</span>\n    <h2>{heading}</h2>\n'
                         f'  </div>\n  <p class="act-sub">{sub}</p>')
            open_act = True
        name = ACTORS[step["actor"]][0]
        body = (terminal(step) if step["kind"] == "code" else
                f'<div class="shot"><img src="{encoded(SHOTS / step["file"])}" '
                f'alt="Screenshot: {html.escape(strip_tags(step["title"]))}" loading="lazy"></div>')
        parts.append(
            f'  <div class="step" id="s{i}">\n    <div class="step-num">{i}</div>\n    <div class="step-body">\n'
            f'      <span class="actor-chip {step["actor"]}"><span class="dot"></span>{name}</span>\n'
            f'      <div class="step-title">{step["title"]}</div>\n'
            f'      <span class="step-screen">{step["screen"]}</span>\n'
            f'      <p class="step-text">{step["text"]}</p>\n'
            f'      <div class="step-note"><span class="note-body">{step["note"]}</span></div>\n'
            f"      {body}\n    </div>\n  </div>")
    if open_act:
        parts.append("</section>")
    parts.append("</div>")
    parts.append(FOOTER_TEMPLATE.replace("__UPDATED__", date.today().isoformat()))
    return "\n".join(parts) + "\n"


if __name__ == "__main__":
    needed = [s["file"] for s in STEPS if s["kind"] == "shot"]
    missing = [f for f in needed if not (SHOTS / f).exists()]
    if missing:
        raise SystemExit("No screenshot for: " + ", ".join(missing) +
                         "\nRun the capture first: cd web && npx playwright test "
                         "--config=walkthroughs/playwright.config.ts derive")
    OUT.write_text(build(), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {len(STEPS)} steps)")
