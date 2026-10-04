r"""Build docs/public/walkthroughs/department-approvers-walkthrough.html from captured screenshots.

The screenshots come from web/walkthroughs/department-approvers.spec.ts, which drives the
real console against the live stack. Run that first, then this builder:

    cd web
    npx playwright test --config=walkthroughs/playwright.config.ts department-approvers
    ..\.venv\Scripts\python.exe ..\docs\tools\build_department_approvers_walkthrough.py
"""
from __future__ import annotations

from walkthrough_kit import SHOTS_ROOT, Page, series_entry, write

STEPS = [
    {
        "file": "01-hartley-opens-the-departments.png", "actor": "hartley",
        "act": ("PART ONE", "Hartley names cover for Cardiology",
                "A department is the part of an organisation that owns a set of datasets, such as Cardiology in the "
                "Health organisation. A dataset is a registered, owned collection of data. Each department has "
                "approvers: data custodians who decide who may read its data and who confirm claims about how "
                "sensitive it is. Any one approver can act, so a department is never stuck because one person is "
                "away."),
        "title": "Hartley opens the Departments page",
        "screen": "Departments &middot; /departments",
        "text": "Each card is one department of the Health organisation, with the number of datasets it owns and "
                "its approvers. Hartley is a data custodian, which is the role that allows a person to approve "
                "access to data. Hartley is also an approver of the Cardiology department, so the Cardiology card "
                "carries a form for adding another approver.",
        "note": "Under <strong>Approvers</strong> the Cardiology card lists <strong>Hartley</strong> as "
                "<strong>permanent</strong>. The Oncology, Orthopaedics and Radiology cards each read "
                "<strong>Only an approver of this department can change this list</strong>, because Hartley is "
                "not an approver of those departments.",
    },
    {
        "file": "02-hartley-names-cover.png", "actor": "hartley",
        "title": "Hartley chooses Mensah as cover and writes why",
        "screen": "Departments &middot; the Cardiology card, Add an approver",
        "text": "Mensah is the data custodian of the Oncology department. Only people who already hold the data "
                "custodian role can be chosen, because adding an approver never gives anybody the role. Hartley "
                "writes a reason, which is stored with the change, and an end date, so the cover stops by "
                "itself.",
        "note": "<strong>Person</strong> reads <strong>Mensah</strong>, <strong>Why</strong> reads "
                "<strong>covers while Hartley is on leave</strong> and <strong>Cover ends on</strong> holds a "
                "date two weeks away. Leaving the date empty would make a permanent approver.",
    },
    {
        "file": "03-the-cover-is-listed.png", "actor": "hartley",
        "title": "Mensah is now an approver of Cardiology",
        "screen": "Departments &middot; the Cardiology card",
        "text": "The Cardiology department now has two approvers. Hartley is permanent. Mensah is listed with the "
                "last day of the cover, after which the platform stops counting Mensah as an approver of "
                "Cardiology without anybody having to remove the entry.",
        "note": "<strong>Mensah</strong> reads <strong>covering until Oct 19, 2026</strong> and "
                "<strong>Hartley</strong> still reads <strong>permanent</strong>. A notice at the bottom right "
                "reads <strong>Approver added</strong>.",
    },
    {
        "file": "04-the-cover-sees-the-claim.png", "actor": "mensah",
        "act": ("PART TWO", "The cover confirms a claim on their own",
                "A claim is a statement, made by the person who brings a dataset in, about how sensitive it is. "
                "The person who made a claim may never confirm it, so a different approver of the owning "
                "department has to."),
        "title": "Mensah sees a claim waiting for confirmation",
        "screen": "Home &middot; /",
        "text": "Devi, a data engineer, has registered a dataset for the Cardiology department and claimed its "
                "sensitivity. Mensah is a data custodian of Oncology and now also an approver of Cardiology, so "
                "the Cardiology claim appears on Mensah's home screen.",
        "note": "The header reads <strong>Cardiology and Oncology</strong>, the two departments Mensah answers "
                "for. The list shows a Cardiology dataset marked <strong>Published</strong>, with "
                "<strong>Claimed by Devi</strong> and the date.",
    },
    {
        "file": "05-the-cover-confirms-it.png", "actor": "mensah",
        "title": "Mensah confirms the claim",
        "screen": "Home &middot; /",
        "text": "Pressing <strong>Agree with this</strong> records that Mensah, an approver of the owning "
                "department, confirmed the claim that Devi made. Hartley did not have to be present.",
        "note": "<strong>Needs your confirmation</strong> now reads <strong>0</strong>, and the claim has left "
                "the list.",
    },
    {
        "file": "06-hartley-ends-the-cover.png", "actor": "hartley",
        "act": ("PART THREE", "Hartley ends the cover and the platform keeps the record",
                "Cover can end before its end date. Every change to who answers for a department is recorded "
                "with who made it and why, and the record is never edited."),
        "title": "Hartley removes Mensah and writes why",
        "screen": "Departments &middot; the Cardiology card, Remove",
        "text": "Hartley is back, so Hartley presses <strong>Remove</strong> next to Mensah. The platform asks for "
                "a reason before it accepts the removal.",
        "note": "The reason box next to <strong>Mensah</strong> reads <strong>Hartley is back</strong>, and "
                "<strong>Confirm removal</strong> is ready to press.",
    },
    {
        "file": "07-the-cover-has-ended.png", "actor": "hartley",
        "title": "Mensah is no longer an approver of Cardiology",
        "screen": "Departments &middot; the Cardiology card",
        "text": "Only Hartley is listed again. From this moment Mensah can no longer confirm claims about "
                "Cardiology data or approve access to it, but Mensah is still the data custodian of Oncology.",
        "note": "The Cardiology card lists only <strong>Hartley</strong>, and the notice reads "
                "<strong>Mensah is no longer an approver of Cardiology</strong>. The Oncology card still lists "
                "<strong>Mensah</strong> as <strong>permanent</strong>.",
    },
    {
        "file": "08-the-last-approver-stays.png", "actor": "hartley",
        "title": "The last permanent approver cannot be removed",
        "screen": "Departments &middot; the Cardiology card, Remove",
        "text": "Hartley tries to remove Hartley, the only approver left. The platform refuses, because a "
                "department with nobody who answers for it could neither approve access nor confirm a claim. "
                "Another approver has to be added first.",
        "note": "The red sentence reads <strong>A department always keeps at least one permanent approver, so "
                "this one cannot be removed until another is added</strong>. <strong>Hartley</strong> is still "
                "listed.",
    },
    {
        "file": "09-the-history.png", "actor": "hartley",
        "title": "The history shows who answered for Cardiology, and when",
        "screen": "Departments &middot; the Cardiology card, Show history",
        "text": "Pressing <strong>Show history</strong> lists every approver the department has had, who added "
                "each one and why, and who ended it and why. Entries are never edited or deleted. Each time the "
                "capture for this page ran, Mensah was named as cover and removed again, so Mensah appears once "
                "for each run.",
        "note": "Each Mensah row reads <strong>covers while Hartley is on leave</strong> under "
                "<strong>Added</strong> and <strong>Hartley is back</strong> under <strong>Ended</strong>. "
                "Hartley's own row has an empty <strong>Ended</strong> cell, because Hartley is still an "
                "approver.",
    },
]

ACTORS = {
    "hartley": ("Hartley", "Data custodian and approver for the Cardiology department"),
    "mensah": ("Mensah", "Data custodian for the Oncology department, named as cover for Cardiology"),
}

PAGE = Page(
    slug="department-approvers", org="health",
    title=series_entry("department-approvers")["title"],
    eyebrow="Health organisation &middot; Departments",
    lede="The custodian of the Cardiology department names a colleague from another department as cover while "
         "away. The cover confirms a claim about Cardiology data without waiting, and the custodian then ends "
         "the cover. The platform refuses to remove the last permanent approver and keeps a record of every "
         "change.",
    actors=ACTORS, steps=STEPS,
    words=["organisation", "department", "dataset", "custodian", "approver", "claim", "role"],
    shots_dir=SHOTS_ROOT / "department-approvers",
    capture_note="Captured live against the <code>health</code> organisation by "
                 "<code>web/walkthroughs/department-approvers.spec.ts</code> and built by "
                 "<code>docs/tools/build_department_approvers_walkthrough.py</code>. What the platform refuses "
                 "here is proved separately by <code>verify/v127_department_approvers.py</code>.",
)

if __name__ == "__main__":
    missing = [s["file"] for s in STEPS if not (PAGE.shots_dir / s["file"]).exists()]
    if missing:
        raise SystemExit("No screenshot for: " + ", ".join(missing) + "\nRun the capture first: cd web && npx "
                         "playwright test --config=walkthroughs/playwright.config.ts department-approvers")
    write(PAGE)
