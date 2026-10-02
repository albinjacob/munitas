r"""Build the two derivation walkthroughs, one page per organisation:

  docs/public/walkthroughs/derive-health-walkthrough.html
  docs/public/walkthroughs/derive-finance-walkthrough.html

from recorded runs.

Two real recordings feed this page, and nothing on it is mocked:

  * The console screens come from web/walkthroughs/derive.spec.ts, which drives
    the real console against the live stack as one organisation's own people
    (DERIVE_TENANT picks which, health by default).
  * The steps taken in a Python session have no screen, so they show the real code
    and the real output that scripts/demo/derive-demo.py printed, saved by its
    --transcript option, with who typed each command. The code on the page is the
    exact text that ran.

Run them in this order, then this builder:

    .venv\Scripts\python.exe scripts\seed\seed-derive-demo-data.py
    .venv\Scripts\python.exe scripts\demo\derive-demo.py --tenant health --name chronic-heart-patients-over-65 ^
        --transcript web\walkthroughs\shots\derive-health\transcript.json
    .venv\Scripts\python.exe scripts\demo\derive-demo.py --tenant finance --name large-foreign-payments ^
        --transcript web\walkthroughs\shots\derive-finance\transcript.json
    cd web
    npx playwright test --config=walkthroughs/playwright.config.ts derive
    $env:DERIVE_TENANT = "finance"; npx playwright test --config=walkthroughs/playwright.config.ts derive
    ..\.venv\Scripts\python.exe ..\docs\tools\build_derive_walkthrough.py

Needs Pillow, listed in docs/tools/requirements.txt.

Each step reads in the same order: what is happening, then the evidence (a real
screenshot, or the code and what came back), then what to look for in it.
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
SHOTS_ROOT = DOCS.parent / "web" / "walkthroughs" / "shots"
OUT_DIR = DOCS / "public" / "walkthroughs"
QUALITY = 72

# Set by use(): the organisation being built. The functions below read these.
ORG = "health"
SHOTS = SHOTS_ROOT / "derive-health"
TRANSCRIPTS: dict = {}
STEPS: list = []
ACTORS: dict = {}
CROPS: dict = {}


def use(org: str) -> None:
    """Point the builder at one organisation's recording, steps and people."""
    global ORG, SHOTS, TRANSCRIPTS, STEPS, ACTORS, CROPS
    ORG = org
    SHOTS = SHOTS_ROOT / f"derive-{org}"
    TRANSCRIPTS = {org: json.loads((SHOTS / "transcript.json").read_text(encoding="utf-8"))}
    STEPS = ORGS[org]["steps"]
    ACTORS = ORGS[org]["actors"]
    CROPS = ORGS[org]["crops"]


SESSION = None  # code steps are labelled from the code they show (see code_screen)
CONSOLE = "The Munitas console, the platform&rsquo;s web app"

WORDS = [
    ("Organisation", "A company or hospital group that uses Munitas. Each organisation sees only its own data."),
    ("Department", "A team inside an organisation that owns some of its data, such as Cardiology."),
    ("Dataset", "A named collection of records, such as a table of patient admissions."),
    ("Custodian", "The person a department trusts to decide who may read its data."),
    ("Access level", "How restricted a dataset is. Raw is the most restricted, and nobody may read it without "
                     "permission. Published is the most open."),
    ("Lease", "Permission, given by the custodian, to read one dataset for one stated purpose and for a "
              "limited time."),
    ("Query", "A question about a dataset, written in a language called SQL."),
    ("Sensitivity label", "How identifying a column is. <em>none</em> does not identify anybody, <em>quasi</em> is "
                          "a detail that could help identify a person when combined with others, and "
                          "<em>phi</em> is protected health information."),
    ("Console", "The platform's web page for people."),
    ("Catalog and token", "A catalog is the list of tables the platform offers to analysis tools. A token is a "
                          "short-lived password that also states why the person wants to read."),
    ("Sandbox", "An isolated container, with no network connection and no passwords, where the platform runs a "
                "query."),
    ("Sealed", "Finished and closed for good, so that it can never be edited."),
    ("Iceberg table", "An open way of storing a table that tools such as DuckDB can read directly."),
]

HOW_TO_READ = [
    "Every step names the tool or the part of Munitas where it happens.",
    "<strong>Console steps</strong> happen in the Munitas console, the web app that people open in a browser. "
    "They show real screenshots.",
    "<strong>Python steps</strong> happen in Python, on the person&rsquo;s own computer, in a notebook or in a "
    "terminal. Python is a window where a person types a line and sees the answer straight away. From there the "
    "person uses the Munitas client library, which sends requests to Munitas, and DuckDB, a free tool for "
    "analysing tables. Each dark box on the page is one of these steps.",
    "In a dark box, the top part is exactly what was typed and the bottom part is exactly what came back. "
    "<code>me</code> is the client library signed in as the person named on the label, and "
    "<code>custodian</code> is the client library signed in as the custodian.",
    "Every step was run for real by a script that types each one, so the answers are genuine and nothing has "
    "been edited.",
]

PARTS = [
    ("Console", "The Munitas web app that people open in a browser. It calls the control plane for everything it shows."),
    ("Control plane", "Munitas&rsquo;s API service. It decides who may do what, keeps the permanent record of every "
                      "decision, and starts the work for new datasets."),
    ("Catalog", "The part of the control plane that lists tables for tools such as DuckDB and hands them a "
                "short-lived key to read the files."),
    ("Storage", "SeaweedFS, the object storage where the files of every dataset are kept."),
    ("Sandbox worker", "A background worker that runs the query for a new dataset in an isolated container named "
                       "<code>munitas-derive-runner</code>, which has no network connection and no passwords."),
    ("Munitas client library", "A small Python library, <code>munitas_client.py</code>, that a person uses in Python "
                               "to send requests to the control plane."),
    ("DuckDB", "A free tool for analysing tables. It runs on the person&rsquo;s computer and reads tables from "
               "Munitas through the catalog."),
]

WORDS = [
    ("Organisation", "A company or hospital group that uses Munitas. Each organisation sees only its own data."),
    ("Department", "A team inside an organisation that owns some of its data, such as Cardiology."),
    ("Dataset", "A named collection of records, such as a table of patient admissions."),
    ("Custodian", "The person a department trusts to decide who may read its data."),
    ("Access level", "How restricted a dataset is. Raw is the most restricted, and nobody may read it without "
                     "permission. Published is the most open."),
    ("Lease", "Permission, given by the custodian, to read one dataset for one stated purpose and for a "
              "limited time."),
    ("Query", "A question about a dataset, written in a language called SQL."),
    ("Sensitivity label", "How identifying a column is. <em>none</em> does not identify anybody, <em>quasi</em> is "
                          "a detail that could help identify a person when combined with others, and "
                          "<em>phi</em> is protected health information."),
    ("Token", "A short-lived password that also states why the person wants to read."),
    ("Sealed", "Finished and closed for good, so that it can never be edited."),
    ("Iceberg table", "An open way of storing a table that tools such as DuckDB can read directly."),
]

HEALTH_STEPS = [
    # ------------------------------------------------------------------ one --
    {
        "act": ("PART ONE", "A closed table, and a request for access",
                "The Cardiology department of a hospital group owns a dataset of patient admissions. Every "
                "record names a patient, so the dataset is closed to most people. A researcher wants to study "
                "it. This part shows the researcher being stopped, asking for access, and being answered by a "
                "different person."),
        "kind": "code", "source": "health", "scenes": [1], "actor": "sam",
        "title": "Sam connects an analysis tool and sees only the open table",
        "screen": SESSION,
        "text": "Sam is a researcher in the Health organisation. To start, Sam asks the platform for a token, "
                "which is a short-lived password that also says why Sam wants to read, here a readmission "
                "study. Sam then connects DuckDB, a free tool for analysing tables, to the platform's catalog, "
                "which is the list of tables the platform offers, and asks what is in it. DuckDB runs inside "
                "Sam's own Python session and reads the tables from the platform.",
        "note": "Only one dataset is listed, <code>diagnosis_codes</code>, a public lookup of diagnosis "
                "names. The admissions dataset does not appear at all, because Sam may not read it.",
    },
    {
        "kind": "shot", "file": "01-sam-finds-the-open-lookup.png", "actor": "sam",
        "title": "The console shows why that table is open",
        "screen": CONSOLE + " &middot; Datasets",
        "text": "The console is the platform's web page for people. Signed in as Sam, the datasets list shows "
                "<code>diagnosis_codes</code>, the public lookup of diagnosis names, which the Cardiology "
                "department owns.",
        "note": "The <strong>You can read</strong> column says <strong>1 of 1</strong>, and the access level is "
                "<strong>Published</strong>, the most open level. Sam may read this dataset without asking "
                "anybody.",
    },
    {
        "kind": "code", "source": "health", "scenes": [2], "actor": "sam",
        "title": "The restricted table is closed, and the platform says why",
        "screen": SESSION,
        "text": "The admissions dataset is at the Raw access level, the most restricted one. Nobody may read it "
                "without a lease, which is permission from the owning department's custodian to read it for "
                "one stated purpose and for a limited time. Sam tries to read it anyway.",
        "note": "The refusal ends with <code>no role reaches class RAW</code>. Sam's role, researcher, reaches "
                "the Published level and nothing above it. The platform gives the reason instead of a bare "
                "error. The line also names one lease that was revoked earlier, because this example was "
                "recorded more than once on the same data and the platform remembers Sam's earlier lease. "
                "Only the most recent revoked lease is named, however many there have been.",
    },
    {
        "kind": "shot", "file": "02-sam-finds-admissions-closed.png", "actor": "sam",
        "title": "The console shows the same closed table",
        "screen": CONSOLE + " &middot; Datasets",
        "text": "The same datasets list in the console now shows the admissions dataset, which the Cardiology "
                "department owns and which holds the patient records.",
        "note": "The <strong>You can read</strong> column says <strong>0 of 1</strong>, and the access level "
                "is <strong>Raw</strong>. The dataset has one version, and Sam may read none of it.",
    },
    {
        "kind": "shot", "file": "03-sam-fills-in-the-request.png", "actor": "sam",
        "title": "Sam asks the Cardiology custodian for access",
        "screen": CONSOLE + " &middot; A dataset's page",
        "text": "On the dataset's page, Sam writes what the data will be used for, why something less "
                "sensitive would not do, and for how long access is needed. The request goes to Hartley, the "
                "custodian of the Cardiology department, because Cardiology owns the data.",
        "note": "The two grey lines above the form are history from earlier recordings of this page: an "
                "earlier lease that has ended, and an earlier request that was refused. The platform keeps "
                "that in view whenever somebody asks again.",
    },
    {
        "kind": "shot", "file": "05-hartley-sees-the-request.png", "actor": "hartley",
        "title": "Hartley sees the request in the Cardiology queue",
        "screen": CONSOLE + " &middot; The custodian's home page",
        "text": "Hartley's home page lists the requests to read Cardiology's data. Nobody else can decide this "
                "one, and Sam cannot approve a request made under Sam's own name.",
        "note": "The card reads <strong>Sam (Researcher) wants to read admissions v1</strong>, followed by "
                "Sam's reason and two buttons, <strong>Grant access</strong> and <strong>Refuse</strong>. The "
                "small text under them says what granting means: a fixed time, this purpose only, and it ends "
                "by itself.",
    },
    {
        "kind": "shot", "file": "06-after-granting.png", "actor": "hartley",
        "title": "Hartley grants the request",
        "screen": CONSOLE + " &middot; The custodian's home page",
        "text": "Hartley chooses Grant access. The request leaves the queue, and a lease now exists that lets "
                "Sam read this one dataset for the stated purpose.",
        "note": "A notice at the bottom right reads <strong>Access granted to Sam (Researcher)</strong>, and "
                "the box <strong>Currently granted</strong> now counts one. The newest entry under <strong>What "
                "you have decided</strong> says <strong>Granted</strong>, shows when the lease runs out, and "
                "offers a <strong>Revoke</strong> link, which Hartley uses at the end of this walkthrough.",
    },
    {
        "kind": "code", "source": "health", "scenes": [4], "actor": "sam",
        "title": "With the lease, the restricted table opens",
        "screen": SESSION,
        "text": "Back in Sam's Python session, the kind of question that was refused a moment ago now works. "
                "Every name in the table is invented for this example.",
        "note": "Real rows come back, including <code>patient_name</code>, which names a person directly. That "
                "is why the dataset is restricted, and why anything made from it has to stay just as careful.",
    },
    # ------------------------------------------------------------------ two --
    {
        "act": ("PART TWO", "Making a new dataset from a query",
                "Sam only needs the older patients with a long-term heart condition. Munitas lets a person make "
                "a new dataset by writing a query over datasets the person may already read. The platform runs "
                "the query itself, not the person's computer, and holds the new dataset to the same rules as "
                "the data it came from."),
        "kind": "code", "source": "health", "scenes": [5], "actor": "sam",
        "title": "The platform shows its plan before anything runs",
        "screen": SESSION,
        "text": "Sam writes a query that joins the restricted admissions with the public diagnosis lookup and "
                "keeps patients over 65 with a chronic condition, then asks the platform to plan it. Nothing "
                "runs yet. The platform replies with the columns the new dataset would have, and the "
                "sensitivity label each one must carry: <code>none</code> for a column that does not identify "
                "anybody, <code>quasi</code> for a detail that could help identify a person when combined with "
                "others, and <code>phi</code> for protected health information.",
        "note": "<code>description</code>, which comes from the public lookup, is <strong>none</strong>. "
                "<code>diagnosis_code</code> and <code>readmitted_30d</code>, which come from the admissions, "
                "are <strong>phi</strong>. The last line says the new dataset would be <strong>RAW</strong>, "
                "because it takes the strictest level among its inputs.",
    },
    {
        "kind": "code", "source": "health", "scenes": [6], "actor": "sam",
        "title": "A label cannot be lowered by the person who wrote the query",
        "screen": SESSION,
        "text": "A label can be raised but not lowered. Lowering one would claim that the data is safer than "
                "the data it came from, and that claim needs somebody other than the person who wrote the "
                "query. Sam tries to mark the age column as <code>none</code> anyway.",
        "note": "The platform refuses and explains: age is computed from a field marked <code>quasi</code>, "
                "so it cannot be marked <code>none</code>.",
    },
    {
        "kind": "code", "source": "health", "scenes": [7], "actor": "sam",
        "title": "Sam confirms, and the platform runs the query in an isolated container",
        "screen": SESSION,
        "text": "After Sam confirms, the platform copies the input files into a sandbox, an isolated container "
                "with no network connection and no passwords. The query runs there, and the platform checks "
                "every row of the result against the plan. The query never runs on Sam's computer. Sam's "
                "session only waits for the answer.",
        "note": "The status is <strong>succeeded</strong>, and the new dataset is <strong>RAW</strong> again. "
                "Sam did not choose that level: it follows from the datasets the query read.",
    },
    {
        "kind": "shot", "file": "07-sam-finds-the-new-dataset.png", "actor": "sam",
        "title": "The new dataset appears in the console, owned by Cardiology",
        "screen": CONSOLE + " &middot; Datasets",
        "text": "The new dataset belongs to the same department as the data it came from, so Hartley is still "
                "the person who decides who may read it. Sam made it, so Sam can read it straight away without "
                "asking anybody.",
        "note": "The <strong>You can read</strong> column says <strong>1 of 1</strong>, and the access level "
                "is still <strong>Raw</strong>. The name ends in a number only so that this example can be "
                "recorded again.",
    },
    {
        "kind": "code", "source": "health", "scenes": [8], "actor": "sam",
        "title": "The new dataset opens like any other table",
        "screen": SESSION,
        "text": "The platform sealed the result, which means it is finished and closed for good, so it can "
                "never be edited. It is also an Iceberg table, an open way of storing a table that tools such "
                "as DuckDB read directly. Sam queries it.",
        "note": "The second answer counts the patients in the new dataset and gives their average age, which "
                "is above 65 as the query required.",
    },
    {
        "kind": "shot", "file": "08-where-it-came-from.png", "actor": "sam",
        "title": "The console records where the new dataset came from",
        "screen": CONSOLE + " &middot; A dataset's page",
        "text": "Every version of a dataset records what produced it: the step, who ran it, and the exact "
                "versions of the datasets it was made from.",
        "note": "<strong>Your access</strong> reads <em>You can read this until</em> a date, for the stated "
                "purpose. <strong>Where this came from</strong> shows the step <strong>derive</strong> and two "
                "<strong>Made from</strong> versions, one for the admissions and one for the diagnosis lookup.",
    },
    {
        "kind": "code", "source": "health", "scenes": [9], "actor": "sam",
        "title": "The query is kept exactly as written",
        "screen": SESSION,
        "text": "Anybody auditing the new dataset can follow it back. The platform keeps the purpose, the "
                "datasets and versions it was made from, and the text of the query.",
        "note": "The inputs line names <strong>admissions version 1 (RAW)</strong> and "
                "<strong>diagnosis_codes version 1 (PUBLISHED)</strong>, which are the most restricted and the "
                "most open access levels. The query appears word for word.",
    },
    # ---------------------------------------------------------------- three --
    {
        "act": ("PART THREE", "The rules hold",
                "A new dataset is only safe if nobody can use it to get around the rules. This part tests that "
                "with a colleague, with a query that reaches too far, and with the custodian taking the access "
                "back."),
        "kind": "code", "source": "health", "scenes": [10], "actor": "devi",
        "title": "A colleague without access is refused",
        "screen": SESSION,
        "text": "Devi is a pipeline engineer in the same organisation, with no lease on the new dataset. The "
                "new dataset is as restricted as the data it came from, so Devi cannot open it.",
        "note": "The reason is the same as before: <code>no role reaches class RAW</code>. Working in the same "
                "organisation is not enough.",
    },
    {
        "kind": "code", "source": "health", "scenes": [11], "actor": "sam",
        "title": "A query that reaches outside its inputs is stopped before it runs",
        "screen": SESSION,
        "text": "A query may read the datasets it names and nothing else. Sam tries one that reads a file from "
                "the machine instead.",
        "note": "The platform refuses with <strong>that query reaches outside the datasets it "
                "declared</strong>. Nothing ran.",
    },
    {
        "kind": "shot", "file": "09-the-decision-log.png", "actor": "hartley",
        "title": "Every refusal is on record, with the reason",
        "screen": CONSOLE + " &middot; Who accessed what",
        "text": "The decision log lists every request to read data. Each request is recorded twice: once for "
                "whether it was allowed, and once for whether access was actually given. Here the log is "
                "filtered to the requests that were refused.",
        "note": "Every row says <strong>no</strong>, and the last column gives the reason, such as <code>no "
                "role reaches class RAW</code> and the names of the revoked leases. One row shows a researcher "
                "from a different organisation being refused even a Published dataset.",
    },
    {
        "kind": "code", "source": "health", "scenes": [12], "actor": "hartley",
        "title": "Hartley withdraws the lease, and everything built on it closes",
        "screen": SESSION,
        "text": "Hartley ends the lease. Sam immediately loses the admissions table, and the new dataset too, "
                "because Sam's access to the new dataset rested on the same lease.",
        "note": "Both tables report <strong>refused</strong>, and the reasons name the revoked leases.",
    },
    {
        "kind": "shot", "file": "10-sam-after-the-lease-is-withdrawn.png", "actor": "sam",
        "title": "The console agrees",
        "screen": CONSOLE + " &middot; Datasets",
        "text": "The new dataset is still listed, so Sam can still know that it exists, but Sam can no longer "
                "read it.",
        "note": "The <strong>You can read</strong> column now says <strong>0 of 1</strong>, where it said "
                "<strong>1 of 1</strong> earlier.",
    },
]

FINANCE_STEPS = [
    # ------------------------------------------------------------------ one --
    {
        "act": ("PART ONE", "A closed table, and a request for access",
                "The Fraud Operations department of a finance organisation owns a dataset of card "
                "transactions. Every record names the account holder and the last four digits of the card, so "
                "the dataset is closed to most people. An analyst wants to review it. This part shows the "
                "analyst being stopped, asking for access, and being answered by a different person."),
        "kind": "code", "source": "finance", "scenes": [1], "actor": "omar",
        "title": "Omar connects an analysis tool and sees only the open table",
        "screen": SESSION,
        "text": "Omar is an analyst in the Finance organisation. To start, Omar asks the platform for a token, "
                "which is a short-lived password that also says why Omar wants to read, here a cross-border "
                "fraud review. Omar then connects DuckDB, a free tool for analysing tables, to the platform's "
                "catalog, which is the list of tables the platform offers, and asks what is in it. DuckDB runs "
                "inside Omar's own Python session and reads the tables from the platform.",
        "note": "Only one dataset is listed, <code>merchants</code>, a public list of merchants and their "
                "categories. The transactions dataset does not appear at all, because Omar may not read it.",
    },
    {
        "kind": "shot", "file": "01-sam-finds-the-open-lookup.png", "actor": "omar",
        "title": "The console shows why that table is open",
        "screen": CONSOLE + " &middot; Datasets",
        "text": "The console is the platform's web page for people. Signed in as Omar, the datasets list shows "
                "<code>merchants</code>, the public list of merchants, which the Risk and Compliance "
                "department owns.",
        "note": "The <strong>You can read</strong> column says <strong>1 of 1</strong>, and the access level is "
                "<strong>Published</strong>, the most open level. Omar may read this dataset without asking "
                "anybody.",
    },
    {
        "kind": "code", "source": "finance", "scenes": [2], "actor": "omar",
        "title": "The restricted table is closed, and the platform says why",
        "screen": SESSION,
        "text": "The transactions dataset is at the Raw access level, the most restricted one. Nobody may read "
                "it without a lease, which is permission from the owning department's custodian to read it for "
                "one stated purpose and for a limited time. Omar tries to read it anyway.",
        "note": "The refusal ends with <code>no role reaches class RAW</code>. Omar's role, analyst, reaches "
                "the Published level and nothing above it. The platform gives the reason instead of a bare "
                "error. The line also names one lease that was revoked earlier, because this example was "
                "recorded more than once on the same data and the platform remembers Omar's earlier lease. "
                "Only the most recent revoked lease is named, however many there have been.",
    },
    {
        "kind": "shot", "file": "02-sam-finds-admissions-closed.png", "actor": "omar",
        "title": "The console shows the same closed table",
        "screen": CONSOLE + " &middot; Datasets",
        "text": "The same datasets list in the console now shows the transactions dataset, which the Fraud "
                "Operations department owns and which holds the card transactions. The list also holds "
                "<code>high-value-foreign-transactions</code>, a dataset made from these transactions in an "
                "earlier recording of this walkthrough.",
        "note": "The <strong>You can read</strong> column says <strong>0 of 1</strong> on both rows, and the "
                "access level is <strong>Raw</strong>. Each dataset has one version, and Omar may read none "
                "of either.",
    },
    {
        "kind": "shot", "file": "03-sam-fills-in-the-request.png", "actor": "omar",
        "title": "Omar asks the Fraud Operations custodian for access",
        "screen": CONSOLE + " &middot; A dataset's page",
        "text": "On the dataset's page, Omar writes what the data will be used for, why something less "
                "sensitive would not do, and for how long access is needed. The request goes to Marcus, the "
                "custodian of the Fraud Operations department, because Fraud Operations owns the data.",
        "note": "The line above the form reads <strong>Your access ended</strong>, with the date it was "
                "withdrawn. It is history from an earlier recording of this page, and the platform keeps it "
                "in view whenever somebody asks again.",
    },
    {
        "kind": "shot", "file": "05-hartley-sees-the-request.png", "actor": "marcus",
        "title": "Marcus sees the request in the Fraud Operations queue",
        "screen": CONSOLE + " &middot; The custodian's home page",
        "text": "Marcus's home page lists the requests to read Fraud Operations' data. Nobody else can decide "
                "this one, and Omar cannot approve a request made under Omar's own name.",
        "note": "The first card reads <strong>Omar (Analyst) wants to read transactions v1</strong>, followed by "
                "Omar's reason and two buttons, <strong>Grant access</strong> and <strong>Refuse</strong>. The "
                "small text under them says what granting means: a fixed time, this purpose only, and it ends "
                "by itself. The counter says 2 because the queue also holds a request from Omar for a "
                "different dataset, <code>card-transaction-log</code>, which this walkthrough does not touch.",
    },
    {
        "kind": "shot", "file": "06-after-granting.png", "actor": "marcus",
        "title": "Marcus grants the request",
        "screen": CONSOLE + " &middot; The custodian's home page",
        "text": "Marcus chooses Grant access. The request leaves the queue, and a lease now exists that lets "
                "Omar read this one dataset for the stated purpose.",
        "note": "A notice at the bottom right reads <strong>Access granted to Omar (Analyst)</strong>, and the "
                "box <strong>Currently granted</strong> now counts one. The newest entry under <strong>What "
                "you have decided</strong> says <strong>Granted</strong>, shows when the lease runs out, and "
                "offers a <strong>Revoke</strong> link, which Marcus uses at the end of this walkthrough.",
    },
    {
        "kind": "code", "source": "finance", "scenes": [4], "actor": "omar",
        "title": "With the lease, the restricted table opens",
        "screen": SESSION,
        "text": "Back in Omar's Python session, the kind of question that was refused a moment ago now works. "
                "Every name in the table is invented for this example.",
        "note": "Real rows come back, including <code>account_holder</code> and <code>card_last4</code>, which "
                "identify a person and a card. That is why the dataset is restricted, and why anything made "
                "from it has to stay just as careful.",
    },
    # ------------------------------------------------------------------ two --
    {
        "act": ("PART TWO", "Making a new dataset from a query",
                "Omar only needs the large payments made outside the home country. Munitas lets a person make "
                "a new dataset by writing a query over datasets the person may already read. The platform runs "
                "the query itself, not the person's computer, and holds the new dataset to the same rules as "
                "the data it came from."),
        "kind": "code", "source": "finance", "scenes": [5], "actor": "omar",
        "title": "The platform shows its plan before anything runs",
        "screen": SESSION,
        "text": "Omar writes a query that joins the restricted transactions with the public list of "
                "merchants and keeps payments over 300 made outside the United States, then asks the platform "
                "to plan it. Nothing runs yet. The platform replies with the columns the new dataset would "
                "have, and the sensitivity label each one must carry: <code>none</code> for a column that does "
                "not identify anybody, and <code>quasi</code> for a detail that could help identify a person "
                "when combined with others.",
        "note": "<code>category</code> and <code>high_risk</code>, which come from the public list of "
                "merchants, are <strong>none</strong>. <code>country</code>, <code>occurred_at</code> and "
                "<code>flagged</code>, which come from the transactions, are <strong>quasi</strong>. The last "
                "line says the new dataset would be <strong>RAW</strong>, because it takes the strictest level "
                "among its inputs.",
    },
    {
        "kind": "code", "source": "finance", "scenes": [6], "actor": "omar",
        "title": "A label cannot be lowered by the person who wrote the query",
        "screen": SESSION,
        "text": "A label can be raised but not lowered. Lowering one would claim that the data is safer than "
                "the data it came from, and that claim needs somebody other than the person who wrote the "
                "query. Omar tries to mark the country column as <code>none</code> anyway.",
        "note": "The platform refuses and explains: country is computed from a field marked <code>quasi</code>, "
                "so it cannot be marked <code>none</code>.",
    },
    {
        "kind": "code", "source": "finance", "scenes": [7], "actor": "omar",
        "title": "Omar confirms, and the platform runs the query in an isolated container",
        "screen": SESSION,
        "text": "After Omar confirms, the platform copies the input files into a sandbox, an isolated container "
                "with no network connection and no passwords. The query runs there, and the platform checks "
                "every row of the result against the plan. The query never runs on Omar's computer. Omar's "
                "session only waits for the answer.",
        "note": "The status is <strong>succeeded</strong>, and the new dataset is <strong>RAW</strong> again. "
                "Omar did not choose that level: it follows from the datasets the query read.",
    },
    {
        "kind": "shot", "file": "07-sam-finds-the-new-dataset.png", "actor": "omar",
        "title": "The new dataset appears in the console, owned by Fraud Operations",
        "screen": CONSOLE + " &middot; Datasets",
        "text": "The new dataset belongs to the same department as the data it came from, so Marcus is still "
                "the person who decides who may read it. Omar made it, so Omar can read it straight away "
                "without asking anybody.",
        "note": "The <strong>You can read</strong> column says <strong>1 of 1</strong>, and the access level "
                "is still <strong>Raw</strong>. The name ends in a number only so that this example can be "
                "recorded again.",
    },
    {
        "kind": "code", "source": "finance", "scenes": [8], "actor": "omar",
        "title": "The new dataset opens like any other table",
        "screen": SESSION,
        "text": "The platform sealed the result, which means it is finished and closed for good, so it can "
                "never be edited. It is also an Iceberg table, an open way of storing a table that tools such "
                "as DuckDB read directly. Omar queries it.",
        "note": "The first answer lists the five largest payments, all above 300 and none in the United "
                "States. The second answer counts the matching transactions and adds up their amounts, from a "
                "dataset that holds no account holder names.",
    },
    {
        "kind": "shot", "file": "08-where-it-came-from.png", "actor": "omar",
        "title": "The console records where the new dataset came from",
        "screen": CONSOLE + " &middot; A dataset's page",
        "text": "Every version of a dataset records what produced it: the step, who ran it, and the exact "
                "versions of the datasets it was made from.",
        "note": "<strong>Your access</strong> reads <em>You can read this until</em> a date, for the stated "
                "purpose. <strong>Where this came from</strong> shows the step <strong>derive</strong> and two "
                "<strong>Made from</strong> versions, one for the transactions and one for the merchants.",
    },
    {
        "kind": "code", "source": "finance", "scenes": [9], "actor": "omar",
        "title": "The query is kept exactly as written",
        "screen": SESSION,
        "text": "Anybody auditing the new dataset can follow it back. The platform keeps the purpose, the "
                "datasets and versions it was made from, and the text of the query.",
        "note": "The inputs line names <strong>transactions version 1 (RAW)</strong> and "
                "<strong>merchants version 1 (PUBLISHED)</strong>, which are the most restricted and the "
                "most open access levels. The query appears word for word.",
    },
    # ---------------------------------------------------------------- three --
    {
        "act": ("PART THREE", "The rules hold",
                "A new dataset is only safe if nobody can use it to get around the rules. This part tests that "
                "with a colleague, with a query that reaches too far, and with the custodian taking the access "
                "back."),
        "kind": "code", "source": "finance", "scenes": [10], "actor": "lena",
        "title": "A colleague without access is refused",
        "screen": SESSION,
        "text": "Lena is a pipeline engineer in the same organisation, with no lease on the new dataset. The "
                "new dataset is as restricted as the data it came from, so Lena cannot open it.",
        "note": "The reason is the same as before: <code>no role reaches class RAW</code>. Working in the same "
                "organisation is not enough.",
    },
    {
        "kind": "code", "source": "finance", "scenes": [11], "actor": "omar",
        "title": "A query that reaches outside its inputs is stopped before it runs",
        "screen": SESSION,
        "text": "A query may read the datasets it names and nothing else. Omar tries one that reads a file from "
                "the machine instead.",
        "note": "The platform refuses with <strong>that query reaches outside the datasets it "
                "declared</strong>. Nothing ran.",
    },
    {
        "kind": "shot", "file": "09-the-decision-log.png", "actor": "marcus",
        "title": "Every refusal is on record, with the reason",
        "screen": CONSOLE + " &middot; Who accessed what",
        "text": "The decision log lists every request to read data. Each request is recorded twice: once for "
                "whether it was allowed, and once for whether access was actually given. Here the log is "
                "filtered to the requests that were refused.",
        "note": "Every row says <strong>no</strong>, and the last column gives the reason, such as <code>no "
                "role reaches class RAW</code> and the names of the revoked leases. The rows for Lena, whose "
                "stated purpose is <strong>curiosity</strong>, show only the role reason, because Lena never "
                "held a lease.",
    },
    {
        "kind": "code", "source": "finance", "scenes": [12], "actor": "marcus",
        "title": "Marcus withdraws the lease, and everything built on it closes",
        "screen": SESSION,
        "text": "Marcus ends the lease. Omar immediately loses the transactions dataset, and the new dataset "
                "too, because Omar's access to the new dataset rested on the same lease.",
        "note": "Both tables report <strong>refused</strong>, and the reasons name the revoked leases.",
    },
    {
        "kind": "shot", "file": "10-sam-after-the-lease-is-withdrawn.png", "actor": "omar",
        "title": "The console agrees",
        "screen": CONSOLE + " &middot; Datasets",
        "text": "The new dataset is still listed, so Omar can still know that it exists, but Omar can no longer "
                "read it.",
        "note": "The <strong>You can read</strong> column now says <strong>0 of 1</strong>, where it said "
                "<strong>1 of 1</strong> earlier.",
    },
]

EXTRA_CSS = """
<style>
  :root {
    --sam: #6a4c9c; --sam-bg: #efe9f7; --sam-line: #d8c9ee;
    --omar: #2a7d6a; --omar-bg: #e3f3ee; --omar-line: #bfe3d8;
    --lena: #3949e0; --lena-bg: #edeefc; --lena-line: #c7caf5;
    --marcus: #a4562a; --marcus-bg: #fbeee4; --marcus-line: #edcdb4;
    --note: #9a6b00; --note-bg: #fdf3d9; --note-line: #f0dea3;
    --term-bg: #0f172a; --term-ink: #e2e8f0; --term-out: #94a3b8; --term-line: #1e293b;
  }
  :root:not([data-theme="light"]) {
    @media (prefers-color-scheme: dark) {
      --sam: #c3a8ec; --sam-bg: #2c2340; --sam-line: #473465;
      --omar: #7fd1ba; --omar-bg: #14302a; --omar-line: #24574a;
      --lena: #8b93f7; --lena-bg: #23264a; --lena-line: #3a3e78;
      --marcus: #f0a771; --marcus-bg: #3a2415; --marcus-line: #5e3c22;
      --note: #e0b64c; --note-bg: #3a2f0c; --note-line: #5c4b16;
    }
  }
  :root[data-theme="dark"] {
    --sam: #c3a8ec; --sam-bg: #2c2340; --sam-line: #473465;
    --omar: #7fd1ba; --omar-bg: #14302a; --omar-line: #24574a;
    --lena: #8b93f7; --lena-bg: #23264a; --lena-line: #3a3e78;
    --marcus: #f0a771; --marcus-bg: #3a2415; --marcus-line: #5e3c22;
    --note: #e0b64c; --note-bg: #3a2f0c; --note-line: #5c4b16;
  }
  .cast .dot.sam { background: var(--sam); }
  .cast .dot.omar { background: var(--omar); }
  .cast .dot.marcus { background: var(--marcus); }
  .cast .dot.lena { background: var(--lena); }
  .actor-chip.sam { background: var(--sam-bg); border-color: var(--sam-line); color: var(--sam); }
  .actor-chip.sam .dot { background: var(--sam); }
  .actor-chip.omar { background: var(--omar-bg); border-color: var(--omar-line); color: var(--omar); }
  .actor-chip.omar .dot { background: var(--omar); }
  .actor-chip.marcus { background: var(--marcus-bg); border-color: var(--marcus-line); color: var(--marcus); }
  .actor-chip.marcus .dot { background: var(--marcus); }
  .actor-chip.lena { background: var(--lena-bg); border-color: var(--lena-line); color: var(--lena); }
  .actor-chip.lena .dot { background: var(--lena); }
  .cast-title { margin-top: 28px; font-weight: 600; color: var(--ink-soft); }

  .before {
    margin: 0 0 28px; padding: 18px 22px; border: 1px solid var(--line); border-radius: 12px;
    background: var(--bg-raised);
  }
  .before h2 { margin: 0 0 8px; font-size: 17px; }
  .before p { margin: 0 0 10px; color: var(--ink-soft); font-size: 14.5px; }
  .before ul { margin: 0; padding-left: 20px; color: var(--ink-soft); font-size: 14.5px; }
  .before li { margin-bottom: 6px; }
  .before dl { margin: 0; display: grid; grid-template-columns: max-content 1fr; gap: 6px 18px; font-size: 14px; }
  .before dt { font-weight: 600; }
  .before dd { margin: 0; color: var(--ink-soft); }
  @media (max-width: 640px) { .before dl { grid-template-columns: 1fr; gap: 0; } .before dd { margin-bottom: 8px; } }

  .step, .step-body { min-width: 0; }
  .step-screen { display: block; margin: 2px 0 8px; font-size: 12px; color: var(--ink-faint); }
  .step-note {
    display: flex; gap: 8px; margin: 12px 0 14px; padding: 10px 12px;
    border-radius: 8px; border: 1px solid var(--note-line);
    background: var(--note-bg); font-size: 13.5px; color: var(--ink);
  }
  .step-note::before {
    content: "Look for"; flex: none; font-weight: 700; font-size: 11px;
    text-transform: uppercase; letter-spacing: 0.06em; color: var(--note);
    padding-top: 1.5px;
  }
  .step-note .note-body { flex: 1; min-width: 0; }
  .term { border-radius: 10px; overflow: hidden; background: var(--term-bg); margin: 8px 0 4px; max-width: 100%; }
  .term pre {
    margin: 0; padding: 12px 14px; overflow-x: auto; font-size: 12.5px; line-height: 1.5;
    font-family: ui-monospace, "Cascadia Mono", "Segoe UI Mono", Consolas, "DejaVu Sans Mono", Menlo, monospace;
    white-space: pre; max-width: 100%;
  }
  .term .code { color: var(--term-ink); }
  .term .out { color: var(--term-out); border-top: 1px solid var(--term-line); line-height: 1.25; }
  .term .tag {
    display: block; padding: 8px 14px 0; font-size: 10.5px; letter-spacing: 0.08em;
    text-transform: uppercase; color: var(--term-out);
  }
  .term .who { padding-top: 10px; color: var(--term-ink); letter-spacing: 0.04em; text-transform: none; font-size: 12px; }
  .term .does {
    padding-top: 3px; padding-bottom: 4px; letter-spacing: 0.01em; text-transform: none;
    font-size: 12px; line-height: 1.45;
  }
  .term .does code { color: var(--term-ink); }
</style>"""

FOOTER_TEMPLATE = """
<footer>
  The Python session steps are the real code and output from one recorded run of
  <code>scripts/demo/derive-demo.py</code>. The console screens were captured live against
  the <code>__TENANT__</code> organisation by <code>web/walkthroughs/derive.spec.ts</code>. Both
  are rendered by <code>docs/tools/build_derive_walkthrough.py</code>. Run all three again
  when the flow changes. Every person, patient and card holder in the example data is invented.
  <a href="feature-walkthrough.html">All walkthroughs</a>.
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


# A screenshot of a short list is mostly empty page. These keep only the part of the
# screen that holds the list, cut from the same pixels with nothing redrawn. The custodian's
# home page continues below the request with other waiting requests and the history of past
# decisions, and the decision log is newest first with older rows holding the long lists of
# leases they were recorded with, so those two keep only the top.
def _crops(home_height: int) -> dict:
    crops = {name: (330, 0, 1600, 330) for name in (
        "01-sam-finds-the-open-lookup.png",
        "02-sam-finds-admissions-closed.png",
        "07-sam-finds-the-new-dataset.png",
        "10-sam-after-the-lease-is-withdrawn.png",
    )}
    crops["05-hartley-sees-the-request.png"] = (330, 0, 1600, home_height)
    crops["09-the-decision-log.png"] = (330, 0, 1600, 545)
    return crops


def encoded(path: Path) -> str:
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        if path.name in CROPS:
            rgb = rgb.crop(CROPS[path.name])
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


import ast
import builtins

# Names the recorded session starts with, explained once in "How to read this page".
SESSION_NAMES = {"me", "custodian", "colleague"}

# Names that no visible box sets. A short, plain reason is given instead of leaving the reader to wonder.
HIDDEN_NAMES = {
    "lease": "the lease the custodian approved earlier in the same session",
    "request": "the access request filed earlier in the same session",
    "token": "the token requested at the start of the same session",
    "time": "Python&rsquo;s standard time module",
    "MunitasError": "the error the client library raises when Munitas refuses a request",
}


def _stores_and_loads(code: str) -> tuple[set[str], list[str]]:
    tree = ast.parse(code)
    stored, loaded = set(), []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            (stored.add(node.id) if isinstance(node.ctx, ast.Store) else loaded.append(node.id))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            stored.update((a.asname or a.name).split(".")[0] for a in node.names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            stored.add(node.name)
    return stored, list(dict.fromkeys(loaded))


def _page_step_of(source: str, scene: int) -> int | None:
    for i, step in enumerate(STEPS, start=1):
        if step["kind"] == "code" and step["source"] == source and scene in step["scenes"]:
            return i
    return None


def earlier_names(source: str) -> dict[tuple[int, int], list[tuple[str, int | None]]]:
    """For every recorded box, the names it uses that an earlier box of the session set,
    each with the page step that set it (None when that box is not shown on the page)."""
    defined: dict[str, int] = {}
    found: dict[tuple[int, int], list[tuple[str, int | None]]] = {}
    for scene in TRANSCRIPTS[source]["scenes"]:
        for index, block in enumerate(scene["blocks"]):
            stored, loaded = _stores_and_loads(block["code"])
            uses = []
            for name in loaded:
                if name in stored or name in SESSION_NAMES or hasattr(builtins, name):
                    continue
                if name in defined:
                    uses.append((name, _page_step_of(source, defined[name])))
                elif name in HIDDEN_NAMES:
                    uses.append((name, None))
            found[(scene["step"], index)] = uses
            for name in stored:
                defined[name] = scene["step"]
    return found


def names_line(uses: list[tuple[str, int | None]]) -> str:
    """One plain sentence naming where each carried-over name came from."""
    if not uses:
        return ""
    parts = []
    for name, step in uses:
        if step is not None:
            parts.append(f"<code>{name}</code> was set in step {step}")
        else:
            parts.append(f"<code>{name}</code> is {HIDDEN_NAMES.get(name, 'set earlier in the same session')}")
    return "Names used here that are not defined in this box: " + "; ".join(parts) + "."


def what_it_does(code: str, typist: str) -> str:
    """Plain sentences naming what this code talks to, read from the code itself."""
    objects = [o for o in ("me", "custodian", "colleague") if f"{o}." in code]
    parts = []
    if objects:
        parts.append(f"<code>{objects[0]}</code> is the Munitas client library, signed in as {typist}. Its commands "
                     "call the control plane, which is Munitas&rsquo;s API and the part that decides who may do what.")
    if "confirm(draft)" in code:
        parts.append("To run the query, the control plane starts a background job. A sandbox worker runs it in an "
                     "isolated container named <code>munitas-derive-runner</code> and writes the result back to storage.")
    if ".sql(" in code or ".execute(" in code:
        parts.append("DuckDB runs in this Python session. It asks the Munitas catalog for the table. If the catalog "
                     "allows it, DuckDB receives a short-lived storage key and reads the table files from storage.")
    return " ".join(parts)


def code_screen(step: dict) -> str:
    """The tool and computer a code step happens on, named from the blocks it shows."""
    transcript = TRANSCRIPTS[step["source"]]
    typists, platform, duck = [], False, False
    for number in step["scenes"]:
        scene = next(sc for sc in transcript["scenes"] if sc["step"] == number)
        for block in scene["blocks"]:
            name = transcript["people"][block["who"]].removeprefix("Dr ")
            if name not in typists:
                typists.append(name)
            platform |= any(f"{o}." in block["code"] for o in ("me", "custodian", "colleague"))
            duck |= ".sql(" in block["code"] or ".execute(" in block["code"]
    where = (f"{typists[0]}&rsquo;s own computer" if len(typists) == 1
             else " and ".join(f"{n}&rsquo;s" for n in typists) + " own computers")
    tools = " and ".join(t for t, used in (("the Munitas client library", platform), ("DuckDB", duck)) if used)
    return f"Python on {where}, using {tools}"


def terminal(step: dict) -> str:
    """The recorded code boxes of a step, each labelled with who typed it and where."""
    transcript = TRANSCRIPTS[step["source"]]
    people = transcript["people"]
    carried = earlier_names(step["source"])
    boxes = []
    for number in step["scenes"]:
        scene = next(s for s in transcript["scenes"] if s["step"] == number)
        for index, block in enumerate(scene["blocks"]):
            name = people[block["who"]].removeprefix("Dr ")
            boxes.append(
                '<div class="term">'
                f'<span class="tag who">Typed by {name}, in Python on {name}&rsquo;s own computer</span>'
                f'<span class="tag does">{what_it_does(block["code"], name)}</span>'
                f'<span class="tag does">{names_line(carried[(number, index)])}</span>'
                f'<pre class="code">{html.escape(block["code"])}</pre>'
                f'<span class="tag">What came back</span>'
                f'<pre class="out">{html.escape(block["output"] or "(nothing is printed)")}</pre>'
                "</div>")
    return "\n".join(boxes)


def hero() -> str:
    config = ORGS[ORG]
    cards = "\n".join(
        f'    <div class="card">\n      <span class="dot {key}"></span>\n      <div>\n'
        f'        <div class="name">{name}</div>\n        <div class="role">{role}</div>\n      </div>\n    </div>'
        for key, (name, role) in ACTORS.items())
    return f"""
<header class="hero">
  <div class="eyebrow">Munitas &middot; {config["eyebrow"]} &middot; Governed datasets</div>
  <h1>Making a new dataset from a query, without loosening any rule</h1>
  <p class="lede">
    Munitas is a governance platform: it decides who may read which piece of data, and it keeps a
    permanent record of every decision. {config["lede"]} That person then makes a new dataset from a
    query. The platform runs the query itself, checks the result, and keeps the new dataset exactly
    as restricted as the data it came from. When the custodian withdraws the access, the new dataset
    closes too. Every name in the example data is invented.
  </p>
  <div class="cast-title">Who is involved</div>
  <div class="cast">
{cards}
  </div>
</header>
"""


def before_you_start() -> str:
    how_html = "\n".join(f"    <li>{point}</li>" for point in HOW_TO_READ)
    parts_html = "\n".join(f"      <dt>{term}</dt><dd>{meaning}</dd>" for term, meaning in PARTS)
    words = "\n".join(f"      <dt>{term}</dt><dd>{meaning}</dd>" for term, meaning in WORDS)
    return f"""
<section class="before">
  <h2>How to read this page</h2>
  <ul>
{how_html}
  </ul>
</section>
<section class="before">
  <h2>The parts of Munitas, and the tools around it</h2>
  <dl>
{parts_html}
  </dl>
</section>
<section class="before">
  <h2>Words used on this page</h2>
  <dl>
{words}
  </dl>
</section>"""


def build() -> str:
    parts = [f"<title>Making a new dataset from a query, {ORGS[ORG]['label']}: a walkthrough</title>", stylesheet(), hero(),
             '<div class="wrap">',
             before_you_start()]
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
        evidence = (terminal(step) if step["kind"] == "code" else
                    f'<div class="shot"><img src="{encoded(SHOTS / step["file"])}" '
                    f'alt="Screenshot: {html.escape(strip_tags(step["title"]))}" loading="lazy"></div>')
        parts.append(
            f'  <div class="step" id="s{i}">\n    <div class="step-num">{i}</div>\n    <div class="step-body">\n'
            f'      <span class="actor-chip {step["actor"]}"><span class="dot"></span>{name}</span>\n'
            f'      <div class="step-title">{step["title"]}</div>\n'
            f'      <span class="step-screen">{step["screen"] if step["kind"] == "shot" else code_screen(step)}</span>\n'
            f'      <p class="step-text">{step["text"]}</p>\n'
            f"      {evidence}\n"
            f'      <div class="step-note"><span class="note-body">{step["note"]}</span></div>\n'
            f"    </div>\n  </div>")
    if open_act:
        parts.append("</section>")
    parts.append("</div>")
    parts.append(FOOTER_TEMPLATE.replace("__UPDATED__", date.today().isoformat()).replace("__TENANT__", ORG))
    return "\n".join(parts) + "\n"


HEALTH_ACTORS = {
    "sam": ("Sam", "Researcher, Health organisation"),
    "hartley": ("Hartley", "Data custodian for the Cardiology department"),
    "devi": ("Devi", "Pipeline engineer, Health organisation"),
}
FINANCE_ACTORS = {
    "omar": ("Omar", "Analyst, Finance organisation"),
    "marcus": ("Marcus", "Data custodian for the Fraud Operations department"),
    "lena": ("Lena", "Pipeline engineer, Finance organisation"),
}

ORGS = {
    "health": {
        "label": "Health organisation", "eyebrow": "Health organisation",
        "steps": HEALTH_STEPS, "actors": HEALTH_ACTORS, "crops": _crops(540),
        "out": OUT_DIR / "derive-health-walkthrough.html",
        "lede": "This walkthrough follows a researcher in a hospital group who is stopped at a restricted "
                "table of patient admissions, asks for access, and is answered by the custodian of the "
                "department that owns it.",
    },
    "finance": {
        "label": "Finance organisation", "eyebrow": "Finance organisation",
        "steps": FINANCE_STEPS, "actors": FINANCE_ACTORS, "crops": _crops(700),
        "out": OUT_DIR / "derive-finance-walkthrough.html",
        "lede": "This walkthrough follows an analyst in a finance organisation who is stopped at a restricted "
                "table of card transactions, asks for access, and is answered by the custodian of the "
                "department that owns it.",
    },
}
# The finance page keeps the two waiting requests visible, so its custodian crop is taller.
ORGS["finance"]["crops"]["02-sam-finds-admissions-closed.png"] = (330, 0, 1600, 380)


if __name__ == "__main__":
    for org, config in ORGS.items():
        use(org)
        needed = [s["file"] for s in STEPS if s["kind"] == "shot"]
        missing = [f for f in needed if not (SHOTS / f).exists()]
        if missing:
            raise SystemExit(f"No screenshot for {org}: " + ", ".join(missing) +
                             "\nRun the capture first: cd web && npx playwright test "
                             "--config=walkthroughs/playwright.config.ts derive")
        config["out"].write_text(build(), encoding="utf-8")
        print(f"wrote {config['out']} ({config['out'].stat().st_size // 1024} KB, {len(STEPS)} steps)")
