r"""Build docs/public/walkthroughs/people-administration-walkthrough.html from captured screenshots.

The screenshots come from web/walkthroughs/people-administration.spec.ts, which drives the
real console against the live stack. Run that first, then this builder:

    cd web
    npx playwright test --config=walkthroughs/playwright.config.ts people-administration
    ..\.venv\Scripts\python.exe ..\docs\tools\build_people_walkthrough.py

Two scripts rather than one because they need different things: the capture needs a browser
and a running platform, and this needs neither. Re-wording a step does not mean
re-photographing the console. The page, its look and the wording check come from
walkthrough_kit.py.
"""
from __future__ import annotations

from walkthrough_kit import SHOTS_ROOT, Page, series_entry, write

STEPS = [
    {
        "file": "01-sam-opens-the-directory.png", "actor": "sam",
        "act": ("PART ONE", "Sam asks for a role",
                "A role is a named set of permissions that a person can hold, such as the permission to review "
                "data from which identifying details have been removed. Anybody in an organisation may ask to "
                "hold a role. Nobody may decide their own request, so asking and deciding are two separate "
                "steps done by two different people."),
        "title": "Sam opens the directory of people",
        "screen": "People and departments",
        "text": "The directory lists every person the Health organisation has registered, the role each one "
                "holds, the department each one belongs to, and what each one may approve. Sam is a researcher "
                "and belongs to no department.",
        "note": "Sam's own row reads <strong>Researcher</strong>, with <strong>none</strong> under Department "
                "and <strong>no</strong> under May approve. The custodians, such as Hartley for Cardiology, "
                "show a department and an approval scope, for example <strong>Cardiology only</strong>.",
    },
    {
        "file": "02-sam-fills-in-the-ask.png", "actor": "sam",
        "title": "Sam asks to hold the role of de-identification reviewer",
        "screen": "People and departments &middot; the Ask for a role form at the bottom of the page",
        "text": "Sam fills in two fields. The first names the role, here <code>deid_reviewer</code>, short for "
                "de-identification reviewer, the role that allows a person to review data from which "
                "identifying details have been removed. The second is a written reason, which is stored with "
                "the request and read by whoever decides it.",
        "note": "The role field reads <strong>deid_reviewer</strong> and the reason reads <strong>covering "
                "de-identification reviews while Imani is away</strong>. Imani is the person who holds the "
                "role today, as the directory above the form shows.",
    },
    {
        "file": "03-the-ask-is-waiting.png", "actor": "sam",
        "title": "The request is waiting, and Sam cannot decide it",
        "screen": "People and departments &middot; Asks waiting for a decision",
        "text": "Sam's request now appears under <strong>Asks waiting for a decision</strong>. It has no "
                "buttons next to it, and the page says in words why there are none. Sam does not hold the role "
                "yet, because a request records a wish and changes no permission.",
        "note": "The request reads <strong>Sam asks to hold De-identification reviewer for 90 days</strong>, "
                "and under it, <strong>This is your own ask, so somebody else decides it</strong>. The "
                "<strong>Roles held</strong> list below still reads <strong>No roles</strong>.",
    },
    {
        "file": "04-the-administrator-sees-the-ask.png", "actor": "priya",
        "act": ("PART TWO", "The platform administrator is refused",
                "Priya is the platform administrator, the person responsible for running Munitas itself and not "
                "for any one department's data. This part shows the refusal the whole feature exists to "
                "demonstrate. Somebody who could both run the platform and decide who holds a role on it could "
                "grant themselves anything, so the platform does not allow it."),
        "title": "Priya, the platform administrator, opens the same page",
        "screen": "People and departments",
        "text": "Priya sees the same waiting request that Sam made. The page shows Priya the reason box and the "
                "two buttons, <strong>Grant it</strong> and <strong>Refuse</strong>, because the page does not "
                "decide in advance who may click. The platform decides when a button is pressed.",
        "note": "The panel at the top left reads <strong>Priya, Platform administrator, Support and "
                "reliability</strong>. <strong>Grant it</strong> is pale because no reason has been written "
                "yet, and it becomes usable once one is.",
    },
    {
        "file": "05-the-administrator-is-refused.png", "actor": "priya",
        "title": "Priya tries to grant the role and is refused",
        "screen": "People and departments &middot; after pressing Grant it",
        "text": "Priya writes a reason, <code>seems reasonable</code>, and presses <strong>Grant it</strong>. "
                "The platform checks the request against its access rules and answers with a specific sentence. "
                "A principal is the Munitas word for whichever person or program is being checked, here Priya.",
        "note": "The red panel reads <strong>Could not record this decision</strong>, then <strong>this "
                "principal holds no role that may decide who holds a role</strong>. The reason is named, not a "
                "bare <em>not allowed</em>.",
    },
    {
        "file": "06-the-custodian-records-a-reason.png", "actor": "hartley",
        "act": ("PART THREE", "A custodian grants the role, and the grant starts as never checked",
                "Granting a role is the job of the person who already decides who may read a department's data, "
                "the data custodian of that department. Here that is Hartley, the data custodian of the "
                "Cardiology department."),
        "title": "Hartley, the data custodian for Cardiology, writes down why",
        "screen": "People and departments",
        "text": "Hartley opens the same page and writes a reason before deciding. The platform stores the "
                "reason together with the decision, on the same request that Sam made.",
        "note": "The panel at the top left reads <strong>Hartley, Data custodian for Cardiology</strong>. The "
                "reason reads <strong>Imani is away and reviews cannot wait</strong>, and <strong>Grant "
                "it</strong> is now fully coloured.",
    },
    {
        "file": "07-granted-but-never-checked.png", "actor": "hartley",
        "title": "The role is granted, and marked as never checked",
        "screen": "People and departments &middot; Roles held",
        "text": "Sam now holds the role. The row in <strong>Roles held</strong> shows who granted it, the date "
                "on which it lapses by itself, and when somebody last checked that it is still needed. Being "
                "granted a role and having that grant checked are two separate facts, and the page keeps them "
                "apart.",
        "note": "The row reads <strong>Sam, De-identification reviewer, Granted by Hartley</strong>, with a "
                "lapse date in <strong>Lapses</strong> and <strong>never checked</strong> under "
                "<strong>Last checked</strong>. A notice at the bottom right reads <strong>De-identification "
                "reviewer granted</strong>.",
    },
    {
        "file": "08-somebody-confirms-it-is-still-needed.png", "actor": "hartley",
        "title": "Hartley confirms the grant is still needed",
        "screen": "People and departments &middot; Roles held",
        "text": "Pressing <strong>Still needed</strong> records that a named person looked at the grant on a "
                "named day. It does not extend the grant or change what it covers.",
        "note": "<strong>never checked</strong> has been replaced by <strong>checked</strong> followed by "
                "the date. <strong>Lapses</strong> still shows the same date as in the previous step.",
    },
    {
        "file": "09-withdrawn-and-gone.png", "actor": "hartley",
        "title": "Withdrawing the role ends it at once",
        "screen": "People and departments &middot; Roles held",
        "text": "Pressing <strong>Withdraw</strong> ends the grant immediately. Sam no longer holds the role, "
                "and the <strong>Roles held</strong> list shows what is true now.",
        "note": "<strong>Roles held</strong> reads <strong>No roles</strong> again, and the notices at the "
                "bottom right list the three things Hartley did: <strong>granted</strong>, <strong>Marked "
                "still needed</strong> and <strong>Withdrawn</strong>.",
    },
]

ACTORS = {
    "sam": ("Sam", "Researcher in the Health organisation, asks for the role"),
    "priya": ("Priya", "Platform administrator, who runs Munitas itself"),
    "hartley": ("Hartley", "Data custodian for the Cardiology department, decides it"),
}

PAGE = Page(
    slug="people-administration", org="health",
    title=series_entry("people-administration")["title"],
    eyebrow="Health organisation &middot; People and roles",
    lede="A researcher asks to hold a role and cannot decide the request alone. The platform administrator, who "
         "runs Munitas itself, is refused when trying to grant it. The data custodian of a department grants it "
         "instead, and the grant starts out marked as never checked.",
    actors=ACTORS, steps=STEPS,
    words=["organisation", "department", "custodian", "role", "deid_reviewer", "platform_admin", "principal"],
    shots_dir=SHOTS_ROOT / "people-administration",
    capture_note="Captured live against the <code>health</code> organisation by "
                 "<code>web/walkthroughs/people-administration.spec.ts</code> and built by "
                 "<code>docs/tools/build_people_walkthrough.py</code>. What the platform refuses here is proved "
                 "separately by <code>verify/v72_role_administration.py</code>.",
)

if __name__ == "__main__":
    missing = [s["file"] for s in STEPS if not (PAGE.shots_dir / s["file"]).exists()]
    if missing:
        raise SystemExit("No screenshot for: " + ", ".join(missing) + "\nRun the capture first: cd web && npx "
                         "playwright test --config=walkthroughs/playwright.config.ts people-administration")
    write(PAGE)
