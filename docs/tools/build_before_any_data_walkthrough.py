r"""Build docs/public/walkthroughs/before-any-data-walkthrough.html from captured screenshots.

The screenshots come from web/walkthroughs/before-any-data.spec.ts, which drives the real console against the live stack
as Priya, the platform administrator of the Health organisation. Run that first, then this builder:

    cd web
    npx playwright test --config=walkthroughs/playwright.config.ts before-any-data
    ..\.venv\Scripts\python.exe ..\docs\tools\build_before_any_data_walkthrough.py

This is the first page of the Health story: what the organisation looks like before a research programme brings any new data in.
The People page lists the people of the signed-in person's own organisation, which is the cast of the rest of the story.
"""
from __future__ import annotations

import re

from walkthrough_kit import SHOTS_ROOT, Page, series_entry, write

STEPS = [
    {
        "file": "01-priya-signs-in.png", "actor": "priya",
        "act": ("PART ONE", "Who owns what, and who may do what",
                "Priya is the platform administrator at a hospital group that uses Munitas. The platform administrator keeps "
                "Munitas itself running and looks after the organisation's setup, but has no say over who may read a "
                "department's data. That belongs to the departments that own the data and to their approvers."),
        "title": "Priya signs in",
        "screen": "Sign in &middot; /auth/login",
        "text": "Priya types an email address and a password. Munitas never takes a typed name as proof of who somebody is. "
                "Everyone signs in for real, and what the console shows afterwards depends on who signed in and which "
                "organisation they belong to.",
        "note": "The page still says <strong>Nobody is signed in</strong>, and the email box holds "
                "<strong>priya@health.example</strong>. The picture was taken just before Priya pressed Sign in.",
    },
    {
        "file": "02-the-departments.png", "actor": "priya",
        "title": "The Departments page shows who answers for each department",
        "screen": "Departments &middot; /departments",
        "text": "A department is the part of an organisation that owns a set of datasets, and a dataset is a named collection "
                "of data that Munitas keeps. Each card is one department of the Health organisation, with the number of "
                "datasets it owns and its approvers. The approvers are data custodians, people who hold the role that lets "
                "them decide who may read the department's data. Permanent means the entry has no end date. Only an "
                "approver of a department can change that department's list, so even Priya, who runs the platform, cannot "
                "add anybody to it.",
        "note": "There are four cards: <strong>Cardiology</strong>, <strong>Oncology</strong>, <strong>Orthopaedics</strong> "
                "and <strong>Radiology</strong>. Each has one approver, <strong>Hartley</strong>, <strong>Mensah</strong>, "
                "<strong>Lindqvist</strong> and <strong>Okonjo</strong> in that order, and every card ends with "
                "<strong>Only an approver of this department can change this list.</strong>",
    },
    {
        "file": "03-the-people.png", "actor": "priya",
        "title": "The same list is on the People and roles page in the console",
        "screen": "People and roles &middot; /directory",
        "text": "In the console, the menu entry People and roles opens the screen that the table at the top of this page comes from. "
                "The last column of the screen says which department a data custodian may approve for.",
        "note": "<strong>Hartley</strong> is a <strong>Data custodian</strong> for <strong>Cardiology</strong>, and the last "
                "column says <strong>Cardiology only</strong>: Hartley can approve access to Cardiology's data and nobody "
                "else's. <strong>Devi</strong> is a <strong>Data engineer</strong>, and the last column says <strong>no</strong>, "
                "as it does for everyone who is not a data custodian.",
    },
    {
        "file": "04-what-each-role-can-do.png", "actor": "priya",
        "title": "No role reads sensitive data without permission, and that includes the platform administrator",
        "screen": "What each role can do &middot; /roles",
        "text": "This page is the same in every organisation. It lists each role a person can hold, the most sensitive data "
                "that role may read without asking, and whether the role may approve access beyond that. An access level "
                "says how restricted data is, and Published is the most open. Anything more sensitive needs a lease, which "
                "is permission to read one version of one dataset for one stated purpose and for a limited time. Somebody "
                "else has to approve it.",
        "note": "Look at the <strong>Can read without a lease</strong> column. For the platform administrator, and for every "
                "other role a person can hold, it says <strong>Published data only</strong>. Only the "
                "<strong>Data custodian</strong> may approve access, and the page says <strong>yes, for their own "
                "department</strong>. The last section puts it plainly: <strong>Nobody else can, including administrators, "
                "and you cannot approve your own.</strong>",
    },
    {
        "file": "05-the-limits-the-organisation-has-set.png", "actor": "priya",
        "title": "The organisation sets limits on what people may ask for",
        "screen": "Access rules &middot; /access-rules",
        "text": "When somebody asks for access, they say what they want, why, and for how long. This page shows the limits the "
                "organisation has set on how much a person may ask for. A locked column holds values that identify a person, "
                "and a locked file is a file that stays unreadable until somebody is allowed to open it. For each limit the "
                "page shows the value in force now, the default, and the highest value the platform allows. An approver "
                "still decides every request.",
        "note": "Take <strong>Most rows in one request</strong>: the limit now is <strong>20 rows</strong>, which is also the "
                "default, and the platform allows at most <strong>100 rows</strong>. Every line ends with "
                "<strong>Not changed</strong>, because the organisation has kept the defaults. At the top the page says "
                "<strong>To change a rule, ask your platform administrator.</strong>",
    },
]

NUMBER_WORDS = {12: "Twelve"}
PEOPLE_FILE = "03-the-people.txt"

# What each role is for, in plain words. A role that the People screen shows and this list lacks stops the build, so that
# nobody appears in the table without an explanation.
ROLE_MEANING = {
    "Data custodian": "Decides who may read the data of one department, and can approve access to that department's data only.",
    "Data engineer": "Brings data in, and registers pipelines and agents.",
    "Researcher": "Asks to read data, and works with data that has already been de-identified.",
    "De-identification reviewer": "Decides whether a result with the names removed may be released.",
    "Privacy auditor": "Checks how locked data was used, and reads records of use but no personal data.",
    "Data protection officer": "Watches how the organisation handles personal data, and decides nothing about who may read it.",
    "Network architect": "Decides which external hosts an AI agent may call.",
    "Platform administrator, Support and reliability": "Runs Munitas itself and looks after the organisation's setup, "
                                                       "with no say over who may read a department's data.",
}

# The order people appear in the table: the platform's staff, the approvers, then the people who work with data.
PEOPLE_ORDER = ["Priya", "Ravi", "Hartley", "Mensah", "Lindqvist", "Okonjo", "Devi", "Sam", "Imani", "Marlow", "Nakamura", "Turner"]


def read_people(text: str) -> dict:
    """The rows of the People screen, as captured. A name and its sign-in name run together in the captured text."""
    rows = {}
    for line in text.splitlines():
        cells = line.split("\t")
        if len(cells) != 4 or cells[0] == "PERSON":
            continue
        joined = cells[0]
        for i in range(2, len(joined)):
            name, login = joined[:i], joined[i:]
            if re.fullmatch(r"[A-Z][a-z]+", name) and re.fullmatch(r"[a-z]+-[a-z]+", login) and name.lower() in login.split("-"):
                rows[name] = {"role": cells[1], "department": cells[3].replace(" only", "") if cells[3] != "no" else ""}
                break
        else:
            raise SystemExit(f"Cannot tell the name from the sign-in name in: {joined}")
    return rows


def people_table(shots_dir) -> str:
    rows = read_people((shots_dir / PEOPLE_FILE).read_text(encoding="utf-8"))
    if set(rows) != set(PEOPLE_ORDER):
        raise SystemExit(f"The People screen shows {sorted(rows)}, and the table lists {sorted(PEOPLE_ORDER)}. Update PEOPLE_ORDER.")
    unknown = {r["role"] for r in rows.values()} - set(ROLE_MEANING)
    if unknown:
        raise SystemExit(f"No plain-words meaning for the role: {sorted(unknown)}")
    body = "\n".join(
        f'      <tr><td class="who">{name}</td><td>{rows[name]["role"]}</td><td class="for">{ROLE_MEANING[rows[name]["role"]]}</td>'
        f'<td>{rows[name]["department"] or "No department"}</td></tr>' for name in PEOPLE_ORDER)
    return (f'<section class="people">\n  <h2>Who is who in the Health organisation</h2>\n'
            f'  <p>{NUMBER_WORDS.get(len(PEOPLE_ORDER), len(PEOPLE_ORDER))} fictitious people are registered in the Health organisation. '
            f'Each holds a role, which is a named set of permissions, and a data custodian is an approver for one department. '
            f'Step 3 shows the screen that this table comes from.</p>\n'
            f'  <table>\n    <thead><tr><th>Person</th><th>Role</th><th>What the role is for</th><th>Approves access for</th></tr></thead>\n'
            f'    <tbody>\n{body}\n    </tbody>\n  </table>\n</section>')


ACTORS = {
    "priya": ("Priya", "Platform administrator, who runs Munitas itself"),
}

PAGE = Page(
    slug="before-any-data", org="health",
    title=series_entry("before-any-data")["title"],
    eyebrow="Health organisation &middot; Before the programme starts",
    lede="Before a research programme brings in any new data, the Health organisation already has its structure in place. "
         "Priya, who runs the platform, walks through it: the departments and who answers for each, the people and "
         "their roles, what each role may read without asking, and the limits on what people may ask for.",
    actors=ACTORS, steps=STEPS,
    words=["organisation", "department", "dataset", "custodian", "approver", "role", "platform_admin", "lease", "access_level"],
    shots_dir=SHOTS_ROOT / "before-any-data",
    before_steps=people_table(SHOTS_ROOT / "before-any-data"),
    capture_note="Captured live against the <code>health</code> organisation by "
                 "<code>web/walkthroughs/before-any-data.spec.ts</code> and built by "
                 "<code>docs/tools/build_before_any_data_walkthrough.py</code>.",
)

if __name__ == "__main__":
    missing = [s["file"] for s in STEPS if not (PAGE.shots_dir / s["file"]).exists()]
    if missing:
        raise SystemExit("No screenshot for: " + ", ".join(missing) + "\nRun the capture first: cd web && npx "
                         "playwright test --config=walkthroughs/playwright.config.ts before-any-data")
    write(PAGE)
