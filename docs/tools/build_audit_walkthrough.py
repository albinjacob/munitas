r"""Build docs/public/walkthroughs/audit-decisions-walkthrough.html from captured screenshots.

The screenshots come from web/walkthroughs/audit-decisions.spec.ts, which drives the real console against the live Health organisation as Nakamura, the data
protection officer. Run that first, then this builder:

    cd web
    npx playwright test --config=walkthroughs/playwright.config.ts audit-decisions
    ..\.venv\Scripts\python.exe ..\docs\tools\build_audit_walkthrough.py

This is the Audit page of the Health story. It shows the record the platform keeps of every request to read data, how a person who watches the organisation reads
it, and that a refusal is recorded with its reason. It reads what the other Health pages left behind, so it is captured after them.
"""
from __future__ import annotations

from walkthrough_kit import SHOTS_ROOT, Page, series_entry, write

STEPS = [
    {
        "file": "01-nakamura-sees-the-counts-and-the-latest-refusals.png", "actor": "nakamura",
        "act": ("PART ONE", "The record of every access decision",
                "Every time somebody asks to read data, whether a person or a program, the platform decides and writes the decision down: who asked, what "
                "for, how restricted the data is, and the reason. The record keeps the refusals as well as the grants. A data protection officer is a person who watches how an organisation handles personal data. The role reads the record and "
                "decides nothing."),
        "title": "Nakamura's home page counts every decision and lists the latest refusals",
        "screen": "Home &middot; /",
        "text": "Nakamura is the data protection officer of the Health organisation. The home page of this role is about oversight and nothing else: it counts "
                "what has been asked, how much of it was refused, and shows the most recent refusals with the reason given for each. The access level of a "
                "dataset says how restricted it is, and Raw is the most restricted level. A lease is permission from the owning department to read one dataset "
                "for one stated purpose and for a limited time, and it ends by itself.",
        "note": "The heading is <strong>Welcome, Nakamura</strong>, and the line under it says <strong>You see every decision, including every refusal, and "
                "you make none.</strong> The first tile reads <strong>Requests recorded</strong> with <strong>Granted or not, every one is on record</strong>. The "
                "second reads <strong>Refused</strong>, with the share of everything asked beneath it. The other two tiles read <strong>Access currently "
                "granted</strong> and <strong>Records erased on request</strong>. Under <strong>Latest refusals</strong> a table lists who asked, what for, "
                "why it was refused and when, for example <strong>no role reaches class RAW</strong>, which means that no role may read data at the Raw level "
                "without a lease.",
    },
    {
        "file": "02-every-request-and-what-happened-to-it.png", "actor": "nakamura",
        "title": "The Access decisions page lists every request, newest first",
        "screen": "Access decisions &middot; /audit",
        "text": "Nakamura follows the link to see every decision. Each request is recorded twice: once for whether the rules allowed it, and once for whether the "
                "access was actually given, and the two can differ. The columns say when, who asked and in what role, what was checked, the access level of the "
                "data, what it was for, the result and the reason. Only a data custodian, the data protection officer or a platform administrator may open "
                "this page: any other role is told that it is not for them, and the platform refuses it the data as well. The newest rows are not requests to read a dataset. They are Nakamura's own reads of the Locked data "
                "audit, and the privacy auditor Marlow's, because looking at the record is itself recorded, so the people who watch are watched in the same way.",
        "note": "The heading is <strong>Access decisions</strong>. The sentence under it begins <strong>Every request, and what happened to it.</strong> Two sets "
                "of buttons filter the list: <strong>All</strong>, <strong>Dataset access</strong> and <strong>Agent activity</strong>, and <strong>All</strong>, "
                "<strong>Refused</strong> and <strong>Allowed</strong>. The columns read <strong>When</strong>, <strong>Who</strong>, <strong>What was "
                "checked</strong>, <strong>Access level</strong>, <strong>What for</strong>, <strong>Result</strong> and <strong>Why</strong>. The first "
                "rows name <strong>Nakamura</strong> and <strong>Data protection officer</strong>, <strong>Whether it was allowed</strong>, <strong>Read the "
                "locked data audit: keys</strong> and the result <strong>yes</strong>. A count and page numbers at the foot say how many decisions there are.",
    },
    {
        "file": "03-only-the-refusals-with-the-reason-for-each.png", "actor": "nakamura",
        "title": "One click shows only the refusals, with the reason for each",
        "screen": "Access decisions &middot; /audit, with Refused chosen",
        "text": "Nakamura chooses Refused, and every row that remains is a request that was turned down. Each one keeps its date and time, the person who asked "
                "and the role they held, the access level they reached for, what they said it was for, and the reasons in plain sentences. A reason can be that "
                "no role reaches the Raw level, or that a lease which once covered the request has since been revoked, which means that it was ended "
                "early. A program is held to the same record: the oldest row on this page is a pipeline action that was refused, with the reason that it did not "
                "present the credential of the task it was acting for. A link on each row opens the dataset version that was asked for.",
        "note": "The button <strong>Refused</strong> is chosen, and every row in the list has the result <strong>no</strong> in red. The reasons include "
                "<strong>No role reaches class RAW.</strong>, and a link, <strong>Open the dataset version</strong>, sits under each. The first row names <strong>Devi</strong>, "
                "<strong>Data engineer</strong>, the access level <strong>Raw</strong>, <strong>read locked columns</strong> and the reason <strong>This person is "
                "not an approver of the department that owns this data and holds no current lease for its locked columns.</strong> Rows for Sam name a revoked "
                "lease by its identifier.",
    },
]

ACTORS = {
    "nakamura": ("Nakamura", "Data protection officer in the Health organisation"),
}

PAGE = Page(
    slug="audit-decisions", org="health",
    title=series_entry("audit-decisions")["title"],
    eyebrow="Health organisation &middot; Audit",
    lede="Every request to read data, from a person or a program, is recorded with who asked, what for, and why it was allowed or refused. A data protection "
         "officer reads that record and decides nothing. This walkthrough follows one officer from a home page of counts, to a list of every request, to the "
         "refusals alone with the reason for each.",
    actors=ACTORS, steps=STEPS,
    words=["organisation", "dpo", "access_level", "lease", "dataset", "custodian"],
    shots_dir=SHOTS_ROOT / "audit-decisions",
    capture_note="Captured live against the <code>health</code> organisation by <code>web/walkthroughs/audit-decisions.spec.ts</code> and built by "
                 "<code>docs/tools/build_audit_walkthrough.py</code>.",
)

if __name__ == "__main__":
    missing = [s["file"] for s in STEPS if not (PAGE.shots_dir / s["file"]).exists()]
    if missing:
        raise SystemExit("No screenshot for: " + ", ".join(missing) + "\nRun the capture first: cd web && npx "
                         "playwright test --config=walkthroughs/playwright.config.ts audit-decisions")
    write(PAGE)
