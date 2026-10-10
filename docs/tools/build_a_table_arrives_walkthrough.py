r"""Build docs/public/walkthroughs/a-table-arrives-walkthrough.html from captured screenshots.

The screenshots come from web/walkthroughs/story-a-1.spec.ts, which drives the real console against the live Health organisation as Devi, a data
engineer, and Hartley, the custodian of Cardiology. Run that first, then this builder:

    .venv\Scripts\python.exe scripts\seed\make-story-a-files.py
    cd web
    npx playwright test --config=walkthroughs/playwright.config.ts story-a-1
    ..\.venv\Scripts\python.exe ..\docs\tools\build_a_table_arrives_walkthrough.py

This is the first page of "The numbers", the part of the Health story about regular data: a table of hospital admissions that nobody can read yet.
The next page is Sam, a researcher, finding it and asking to read it.
"""
from __future__ import annotations

from walkthrough_kit import SHOTS_ROOT, Page, series_entry, write

STEPS = [
    {
        "file": "01-admissions-the-registration.png", "actor": "devi",
        "act": ("PART ONE", "A table of admissions is brought in",
                "A hospital group has a research programme that needs the quarterly figures for hospital admissions. Devi is a data "
                "engineer in the Health organisation, and bringing data in is part of the job. The table has one row for each hospital "
                "stay and nothing in it names a person, so Munitas treats it as an ordinary table."),
        "title": "Devi registers a dataset and names the department that owns it",
        "screen": "Bring a dataset in &middot; /datasets/register",
        "text": "A dataset is a named collection of data that Munitas keeps. Before any data is uploaded, the dataset is registered "
                "with a name and an owning department, so that from the first moment somebody is answerable for deciding who may "
                "read it. Devi names this dataset <em>admissions</em> and chooses the Cardiology department. The access level, which "
                "says how restricted the data is, is left at its safe default. Raw is the most restricted level, and nobody may read "
                "data at that level without a lease, which is permission from the owning department to read one version of one "
                "dataset for one stated purpose and for a limited time.",
        "note": "The <strong>Name</strong> box holds <strong>admissions</strong>, <strong>Owned by</strong> says "
                "<strong>Cardiology</strong>, and <strong>What access level?</strong> says <strong>Raw (the safe default)</strong>. "
                "Beside the Register button the page says <strong>Nothing is readable yet. This only creates the record and its "
                "owner.</strong>",
    },
    {
        "file": "02-admissions-the-table-and-its-contract.png", "actor": "devi",
        "title": "Devi chooses a table and gives Munitas two files: a contract and the rows",
        "screen": "Bring a dataset in &middot; /datasets/register, after Register",
        "text": "Once the dataset is registered, the page asks whether anybody will ever need to be erased from this data. Devi "
                "answers no, because a row of this table is a hospital stay and not a person. Devi then chooses a table, which "
                "needs two files. The first is the contract, which names every column and its type, marks the one column whose "
                "value is different in every row, and states that the table cannot be erased and why. The second holds the rows. "
                "Here it is a CSV file, which is a plain text file with one row per line and the values separated by commas. The "
                "admissions file has 400 rows, one for each stay, with the columns admission id, date admitted, age, diagnosis "
                "code, days in hospital and whether the patient was admitted again within 30 days.",
        "note": "The answer <strong>No. Store what I bring as it is.</strong> is chosen, and under it "
                "<strong>Nothing in it is locked.</strong> The choice <strong>A table</strong> is selected. The two file boxes "
                "hold <strong>admissions.contract.yaml</strong> and <strong>admissions.csv</strong>, and the reading options say "
                "<strong>Commas</strong> and <strong>The first line holds the column names</strong>.",
    },
    {
        "file": "03-admissions-sealed.png", "actor": "devi",
        "title": "Munitas checks every row against the contract and seals the table",
        "screen": "Bring a dataset in &middot; /datasets/register, after Seal as a table",
        "text": "When Devi presses the button, Munitas reads every row and checks it against the contract. A row with a value of the "
                "wrong type is refused with its row and column named, and nothing is sealed. Here every row passes. A sealed "
                "version is one state of a dataset that is finished and closed for good: it can never be edited, and a later "
                "table becomes a new version instead. The platform also writes the rows as a table that a standard tool such as "
                "DuckDB, a free tool for analysing tables, can read once somebody is allowed to.",
        "note": "The green box says <strong>Sealed as version 1: 400 rows.</strong> It goes on: <strong>Nothing in it is locked. "
                "It starts at the most restricted level, so nobody can read it until the person who answers for it allows "
                "it.</strong>",
    },
    {
        "file": "04-admissions-the-version.png", "actor": "devi",
        "title": "Even Devi cannot read the table: it starts closed",
        "screen": "Version &middot; /versions/&lt;id&gt;",
        "text": "The version page shows the sealed table as the platform recorded it. Two things are worth reading. The platform "
                "recorded who brought the data in and the exact file that arrived, with a fingerprint, which is a value worked out "
                "from the contents of a file and changes if even one byte changes. And the access level is Raw, so the person who "
                "brought the table in is in the same position as everybody else: to read it, Devi would have to ask the "
                "department's custodian, a person who holds the role that decides who may read a department's data.",
        "note": "<strong>Access level when sealed</strong> and <strong>Access level now</strong> both say <strong>Raw</strong>. "
                "<strong>Brought in</strong> says <strong>admissions.csv (15 KB) by eng-devi</strong>, where eng-devi is the name "
                "Devi signs in with. <strong>Stored as a table</strong> says <strong>Yes, as a table</strong> and "
                "<strong>400 rows</strong>. Under <strong>Your access</strong> the page tells Devi <strong>This is more sensitive "
                "than your role reads, so Hartley decides.</strong>",
    },
    {
        "file": "05-diagnosis_codes-the-registration.png", "actor": "devi",
        "act": ("PART TWO", "A public lookup, and a claim that its custodian agrees with",
                "Analysts who work with admissions need to know what each diagnosis code means. That list is public and about no "
                "person, so it should be open to everybody in the organisation. Only the owning department can agree that data is "
                "that open."),
        "title": "Devi registers a second table and claims that it is Published",
        "screen": "Bring a dataset in &middot; /datasets/register",
        "text": "The second table is a small public list of ten diagnosis codes and what each one means, with a column that says "
                "whether the condition is long lasting. Devi registers it in Cardiology and, this time, chooses the access level "
                "Published, the most open level, which every signed-in person of the organisation may read without asking. "
                "Choosing anything other than the safe default is a claim, a statement about how sensitive the data is, and it is "
                "recorded under the name of the person who makes it. The owning department's custodian has to agree with a claim "
                "before it takes effect.",
        "note": "The name is <strong>diagnosis_codes</strong>, the department is <strong>Cardiology</strong> and the access level "
                "is <strong>Published</strong>. A yellow box says <strong>You are claiming this is Published. The claim is "
                "recorded under your name.</strong> It adds <strong>Until the owning custodian agrees, the data stays at the "
                "most restrictive setting.</strong>",
    },
    {
        "file": "07-diagnosis_codes-sealed.png", "actor": "devi",
        "title": "The lookup is sealed in the same way, and the claim waits",
        "screen": "Bring a dataset in &middot; /datasets/register, after Seal as a table",
        "text": "The lookup goes through the same two files and the same check, and is sealed as version 1. Its claim has not been "
                "agreed yet, so for now it is no more open than the admissions table. A script checked this, and there is no "
                "picture of it: signed in as Sam, a researcher in the same organisation, the "
                "version page of the lookup still offered the form for asking to read it.",
        "note": "The green box says <strong>Sealed as version 1: 10 rows.</strong> Under the box, the dataset is listed as "
                "<strong>diagnosis_codes</strong>, owned by <strong>Cardiology</strong>, with the level <strong>Published</strong>. "
                "The page does not say that the claim has been agreed, because it has not.",
    },
    {
        "file": "08-hartley-sees-the-claim.png", "actor": "hartley",
        "title": "Hartley sees the claim waiting",
        "screen": "Home &middot; /",
        "text": "Hartley is the data custodian for Cardiology and signs in. The home page of a custodian lists what is waiting for "
                "a decision. A claim about how open some data is has its own list, shown first, because it is more basic than a "
                "request to read: until the level is agreed, nothing else about the dataset can be decided.",
        "note": "The panel <strong>Arrivals waiting for your confirmation</strong> lists <strong>diagnosis_codes</strong> with the "
                "label <strong>Published</strong> and the line <strong>Claimed by Devi</strong>, with the date and time. The "
                "button says <strong>Agree with this</strong>, and the tile <strong>Need your confirmation</strong> shows "
                "<strong>1</strong>. The admissions table is not listed, because Devi made no claim about it.",
    },
    {
        "file": "09-hartley-has-agreed.png", "actor": "hartley",
        "title": "Hartley agrees, and the lookup opens to everybody in the organisation",
        "screen": "Home &middot; /",
        "text": "Hartley presses Agree with this. The decision is recorded under Hartley's name, and the lookup is now Published. "
                "The same check as before, made by script as Sam, found that the form for asking to read the lookup is gone: it "
                "is open. The admissions table is untouched and stays Raw. Hartley is still the person who decides who may read "
                "it. Asking to read it is the subject of the next walkthrough, Find a table, ask to read it, and the custodian decides.",
        "note": "A message at the bottom says <strong>Agreed with diagnosis_codes's claim.</strong> The panel of arrivals has gone, "
                "and the tile <strong>Need your confirmation</strong> now shows <strong>0</strong>. Under <strong>What you are "
                "answerable for</strong>, the page says <strong>5 datasets belong to Cardiology</strong>, which now includes "
                "both tables Devi brought in.",
    },
]

ACTORS = {
    "devi": ("Devi", "Data engineer in the Health organisation"),
    "hartley": ("Hartley", "Data custodian for the Cardiology department"),
}

PAGE = Page(
    slug="a-table-arrives", org="health",
    title=series_entry("a-table-arrives")["title"],
    eyebrow="Health organisation &middot; The numbers",
    lede="A research programme at a hospital group needs the quarterly figures for hospital admissions. Devi, a data engineer, brings in "
         "the table, and nobody can read it yet, not even Devi. Devi also brings in a small public list of diagnosis codes and claims "
         "that it is open to everybody. Hartley, who answers for the Cardiology department, agrees with the claim.",
    actors=ACTORS, steps=STEPS,
    words=["organisation", "department", "dataset", "access_level", "lease", "custodian", "contract", "sealed", "version",
           "fingerprint", "claim"],
    shots_dir=SHOTS_ROOT / "a-table-arrives",
    capture_note="Captured live against the <code>health</code> organisation by "
                 "<code>web/walkthroughs/story-a-1.spec.ts</code> and built by "
                 "<code>docs/tools/build_a_table_arrives_walkthrough.py</code>.",
)

if __name__ == "__main__":
    missing = [s["file"] for s in STEPS if not (PAGE.shots_dir / s["file"]).exists()]
    if missing:
        raise SystemExit("No screenshot for: " + ", ".join(missing) + "\nRun the capture first: cd web && npx "
                         "playwright test --config=walkthroughs/playwright.config.ts story-a-1")
    write(PAGE)
