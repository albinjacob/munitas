r"""Build docs/public/walkthroughs/locked-columns-walkthrough.html from captured screenshots.

The screenshots come from web/walkthroughs/locked-columns.spec.ts, which drives the real console against the
live stack. Run that first, then this builder:

    cd web
    npx playwright test --config=walkthroughs/playwright.config.ts locked-columns
    ..\.venv\Scripts\python.exe ..\docs\tools\build_locked_columns_walkthrough.py
"""
from __future__ import annotations

from walkthrough_kit import SHOTS_ROOT, Page, series_entry, write

STEPS = [
    {
        "file": "01-devi-is-asked-whether-anyone-may-need-erasing.png", "actor": "devi",
        "act": ("PART ONE", "Devi seals a table of appointments from two files",
                "A dataset is a registered, owned collection of data. Sealed means finished and closed for good, so "
                "that it can never be edited. This part shows a table of people&rsquo;s records being sealed in a way "
                "that locks what identifies each person, while the table itself stays exactly as sealed."),
        "title": "Devi is asked whether anyone will ever need to be erased from this data",
        "screen": "Bring data into a dataset &middot; /datasets/&lt;id&gt;/ingest",
        "text": "The dataset on this page is owned by the Cardiology department of the Health organisation. A "
                "department is the team inside an organisation that owns some of its data. Nothing in the dataset is "
                "readable yet. Devi is a data engineer, a role that may bring data in. Before any file can be added, the "
                "page asks one question: will anyone ever need to erase one person from this data? The answer has to be "
                "given now, because locking cannot be added after the upload. The first choice, which is selected when the "
                "page opens, stores the files exactly as they are, and nothing in them is locked.",
        "note": "A box titled <strong>Will you ever need to erase one person from this data?</strong> has two choices. "
                "<strong>No. Store the files as they are.</strong> is selected, and the usual <strong>Upload files</strong> "
                "area is below it.",
    },
    {
        "file": "02-devi-opens-the-table-option.png", "actor": "devi",
        "title": "Devi answers yes, and chooses to bring in a table of people&rsquo;s records",
        "screen": "Bring data into a dataset &middot; /datasets/&lt;id&gt;/ingest",
        "text": "Devi answers yes. The page explains what that means: the data of each person is locked under a key made for "
                "that person, and erasing a person destroys their key, so what was locked for them can never be read again. "
                "It also says that locked data is hidden and not anonymised, because it is still personal data until the "
                "person is erased. Devi then says what is being brought in, a table of people&rsquo;s records. The upload "
                "area is replaced by two files: a <strong>privacy contract</strong>, which says column by column how "
                "identifying each column is and what is done to it when the table is sealed, and the <strong>rows</strong> "
                "as a CSV file, which is a plain text table with one row on each line and the column names on the first line.",
        "note": "<strong>Yes. Lock what identifies a person, with one key for each person.</strong> is selected, and under "
                "<strong>What are you bringing in?</strong> the choice <strong>A table of people&rsquo;s records</strong> is "
                "selected. Two file choosers, <strong>The contract</strong> and <strong>The rows</strong>, both read "
                "<strong>No file chosen</strong>. The button <strong>Seal as a table</strong> is greyed out, and the text "
                "names the limits: <strong>20,000 rows and 5 MB</strong>.",
    },
    {
        "file": "03-a-bad-file-is-refused.png", "actor": "devi",
        "title": "A rows file with two mistakes is refused, with both listed",
        "screen": "Bring data into a dataset &middot; the table option",
        "text": "Devi chooses the contract and a rows file in which one date has been replaced by the words &lsquo;not "
                "a date&rsquo; and one patient number has been removed. Pressing <strong>Seal as a table</strong> is "
                "the check. Nothing is sealed, and every problem is listed with the row it belongs to, so that one "
                "attempt shows all of them instead of one at a time.",
        "note": "A red box reads <strong>Nothing was sealed. Fix these first:</strong> and lists two problems. The "
                "first is <strong>A-1001: seen_on is &lsquo;not a date&rsquo;, which is not a date written like "
                "2026-03-31</strong>. The second is <strong>A-1002: names nobody, because patient_number (the "
                "patient number) is empty</strong>. The file names under the choosers end in "
                "<strong>contract.yaml</strong> and <strong>mistakes.csv</strong>.",
    },
    {
        "file": "04-the-table-is-sealed.png", "actor": "devi",
        "title": "With the right rows file, the table is sealed",
        "screen": "Bring data into a dataset &middot; the table option",
        "text": "Devi replaces the rows file with the correct one. For each of the 20 people in it, the platform makes "
                "a key of their own and uses it to scramble that person&rsquo;s name and reason for visit. These "
                "are the <strong>locked columns</strong>. The number that identified each patient is replaced by a "
                "<strong>person code</strong>, a label that stands for one person and means nothing on its own. The "
                "platform also counts how many different people share each combination of the columns that stay "
                "readable, and reports it.",
        "note": "A green box reads <strong>Sealed as version 1: 23 rows about 20 people.</strong> It says the "
                "identifying values are locked, each under a key made for that person. A line below reads "
                "<strong>12 people sit in a group smaller than 5. It did not stop the seal.</strong> The link "
                "<strong>Open this version</strong> leads to the sealed table.",
    },
    {
        "file": "05-the-locked-columns.png", "actor": "devi",
        "title": "The version page lists every column and what was done to it",
        "screen": "Version detail &middot; /versions/&lt;id&gt;",
        "text": "The <strong>Locked columns</strong> section lists each column, how identifying it is, and what was "
                "done to it. It never shows a value. <strong>patient_name</strong> and "
                "<strong>reason_for_visit</strong> are locked. <strong>patient_number</strong> was replaced by a "
                "person code. <strong>postcode</strong> and <strong>age_band</strong> stay readable, because "
                "researchers need them, but together they could identify someone, which is what the small groups "
                "report is about.",
        "note": "<strong>patient_name</strong> and <strong>reason_for_visit</strong> carry the label <strong>Locked "
                "under the person&rsquo;s key</strong>, and <strong>patient_number</strong> reads <strong>Replaced "
                "by a person code</strong>. The footnote reads <strong>23 rows</strong>. Under <strong>Your access to "
                "the locked values</strong> there is a form, and no name, number or reason for a visit appears "
                "anywhere on the page.",
    },
    {
        "file": "06-devi-is-refused-the-rows.png", "actor": "devi",
        "act": ("PART TWO", "Devi cannot read what is locked, so Devi asks",
                "A department approver is a data custodian who is listed for one department and may decide who reads "
                "its data. A <strong>locked-column lease</strong> is permission, decided by an approver of the "
                "owning department, to read a few rows of a table&rsquo;s locked columns for a few days, for a "
                "stated purpose. Holding a role is never enough on its own, apart from being an approver of the "
                "department that owns the data."),
        "title": "Devi tries to read the locked rows and is refused",
        "screen": "Locked columns, opened &middot; /versions/&lt;id&gt;/locked-rows",
        "text": "Devi brought the table in, but is not an approver of the Cardiology department, so the platform "
                "refuses and says why in its own words.",
        "note": "A red box reads <strong>Could not read these rows.</strong> and gives one reason: <strong>this "
                "person is not an approver of the department that owns this data and holds no current lease for its "
                "locked columns</strong>.",
    },
    {
        "file": "07-devi-asks-for-five-rows.png", "actor": "devi",
        "title": "Devi writes why, and asks for five rows for three days",
        "screen": "Version detail &middot; Your access to the locked values",
        "text": "The form asks for a purpose, a number of days and a number of rows. The platform states the limits "
                "itself, at most 7 days and at most 20 rows, and the purpose is recorded with the request. The rows "
                "are chosen by the platform, not by the person asking, and they are the same every time, so asking "
                "repeatedly cannot be used to read the whole table.",
        "note": "<strong>What you need them for</strong> holds Devi&rsquo;s reason. <strong>For how many days (up to "
                "7)</strong> reads <strong>3</strong>, and <strong>How many rows (up to 20)</strong> reads "
                "<strong>5</strong>. The small print reads <strong>The rows you can read are chosen for you and are "
                "the same every time you look</strong>, and <strong>Ask for access</strong> is ready.",
    },
    {
        "file": "08-the-request-waits.png", "actor": "devi",
        "title": "The request waits for an approver",
        "screen": "Version detail &middot; Your access to the locked values",
        "text": "Asking opens nothing. Devi still cannot read any locked value until an approver of the Cardiology "
                "department decides.",
        "note": "A yellow box reads <strong>Your request is waiting for an approver.</strong> and repeats the request: "
                "<strong>for 5 rows for 3 days</strong>, with Devi&rsquo;s reason. A link reads <strong>See your "
                "requests</strong>.",
    },
    {
        "file": "09-sam-has-no-way-to-ask.png", "actor": "sam",
        "title": "Sam, a researcher, has no way to ask at all",
        "screen": "Version detail &middot; Your access to the locked values",
        "text": "Sam holds the researcher role, which is for working with data that has already been de-identified, "
                "meaning the names, dates and other identifying detail have been taken out. "
                "Sam opens the same table and sees how it is built, but no value, and is not offered a form, because "
                "the locked values are not needed to work with the readable columns.",
        "note": "The box under <strong>Your access to the locked values</strong> reads <strong>Your role cannot read "
                "the locked values and cannot ask for them</strong>. The table above it lists the same columns Devi "
                "saw, and there is no form.",
    },
    {
        "file": "10-hartley-sees-the-request.png", "actor": "hartley",
        "act": ("PART THREE", "Hartley, who answers for Cardiology, decides",
                "The person who asks never decides. Only an approver of the department that owns the data can, and "
                "an approver of another department cannot."),
        "title": "Hartley sees the request waiting",
        "screen": "Locked-column access &middot; /locked-column-access",
        "text": "Hartley is a data custodian and an approver of the Cardiology department, so the request about "
                "Cardiology data appears on this page. It shows who asked, why, how many rows and days, and offers "
                "<strong>Approve</strong> and <strong>Decline</strong>. A custodian of another department would "
                "see nothing here.",
        "note": "Under <strong>Waiting for you to decide</strong> there is one row: <strong>Devi</strong>, the "
                "reason, <strong>5 rows, 3 days</strong> and the state <strong>Waiting for an approver</strong>, "
                "with a box reading <strong>A note, recorded with your decision</strong>. <strong>In force in your "
                "departments</strong> reads <strong>No leases in force</strong>.",
    },
    {
        "file": "11-hartley-writes-a-note.png", "actor": "hartley",
        "title": "Hartley writes a note, and both buttons are ready",
        "screen": "Locked-column access &middot; Waiting for you to decide",
        "text": "The note is stored with the decision. A refusal needs a note, so that the person who asked is told "
                "why. Approving needs none, but a note makes the record easier to read later.",
        "note": "The note box holds <strong>Five rows is enough for that.</strong> and both <strong>Approve</strong> "
                "and <strong>Decline</strong> are ready to press.",
    },
    {
        "file": "12-the-lease-is-in-force.png", "actor": "hartley",
        "title": "Hartley approves, and the lease is in force",
        "screen": "Locked-column access &middot; In force in your departments",
        "text": "The lease now lasts three days from the approval and opens five rows. Hartley can end it early at "
                "any time, and so can Devi.",
        "note": "<strong>Waiting for you to decide</strong> now reads <strong>No requests waiting for you</strong>. "
                "<strong>In force in your departments</strong> lists <strong>Devi</strong>, <strong>5 rows, 3 "
                "days</strong>, the label <strong>In force</strong>, an end date three days after the approval, "
                "and <strong>Approved by Hartley: Five rows is enough for that.</strong> A button reads <strong>End "
                "now</strong>.",
    },
    {
        "file": "13-hartley-sees-the-small-groups.png", "actor": "hartley",
        "title": "Hartley reads the report on small groups",
        "screen": "Version detail &middot; Small groups",
        "text": "A <strong>small group</strong> is a combination of readable columns, such as postcode and age band, "
                "shared by only a few people, so that one of them could be singled out even though no column names "
                "anybody. The platform counts different people, not rows, because a person with several visits is "
                "still one person to find. The report is advice. It never stopped the table being sealed and shows "
                "no values. The approvers, the de-identification reviewer, the privacy auditor and the data "
                "protection officer, who watches how an organisation handles personal data, can read it, and only an "
                "approver can decide what to do about it.",
        "note": "A yellow box reads <strong>12 of 20 people sit in a group smaller than 5. The smallest group holds "
                "1 person.</strong> Two lines say what locking each column would leave: <strong>Locking "
                "postcode would leave 4 people in a small group</strong> and <strong>Locking age_band would leave 6 "
                "people in a small group</strong>. Below, <strong>What will you do about it?</strong> offers two "
                "choices, and <strong>Record this decision</strong> is greyed out until a reason is written.",
    },
    {
        "file": "14-hartley-records-a-decision.png", "actor": "hartley",
        "title": "Hartley records what will be done, and why",
        "screen": "Version detail &middot; Small groups",
        "text": "Hartley chooses to change the contract before the next table is sealed, and writes why. The "
                "decision is made once, and the platform refuses to change it afterwards, even to somebody who "
                "writes to the database directly.",
        "note": "A line reads <strong>Decided by Hartley</strong>, then the date, <strong>the contract will change "
                "before the next table</strong>, and Hartley&rsquo;s reason, <strong>The next contract version will "
                "lock the postcode.</strong> The decision form has gone.",
    },
    {
        "file": "15-devi-holds-a-lease.png", "actor": "devi",
        "act": ("PART FOUR", "Devi reads the five rows the lease opens",
                "A lease opens a fixed few rows for a short time. It does not open the table."),
        "title": "Devi now holds a lease",
        "screen": "Version detail &middot; Your access to the locked values",
        "text": "The same section that showed a form now says what Devi holds: five of the 23 rows, until the "
                "lease ends, approved by Hartley for the stated reason. Devi can read the rows or end the lease "
                "personally.",
        "note": "A green box reads <strong>You hold a lease: 5 rows of 23</strong>, with the end date, and "
                "<strong>Approved by Hartley for:</strong> followed by Devi&rsquo;s reason. Two actions are offered: "
                "<strong>Read your 5 rows</strong> and <strong>End this lease now</strong>.",
    },
    {
        "file": "16-devi-reads-five-rows.png", "actor": "devi",
        "title": "Devi reads five rows, with the locked values opened",
        "screen": "Locked columns, opened &middot; /versions/&lt;id&gt;/locked-rows",
        "text": "These are the only five rows Devi can read, and they are the same five every time. The locked values "
                "are opened for them: real names and reasons for visits. Every key that is opened for this is "
                "recorded, naming the person it belongs to by their person code. The other 18 rows stay locked.",
        "note": "A yellow banner reads <strong>Your lease opens 5 of 23 rows. They are the same rows every time, and "
                "a later lease opens the same ones.</strong> The table shows five rows with names under "
                "<strong>patient_name</strong> and reasons under <strong>reason_for_visit</strong>. The column "
                "<strong>patient_number (now a person code)</strong> holds labels that begin with <strong>T-</strong>.",
    },
    {
        "file": "17-marlow-reads-the-audit.png", "actor": "marlow",
        "act": ("PART FIVE", "Marlow checks how locking was used, and sees no name",
                "A privacy auditor is a person who checks how locked data was used. The role reads records of use "
                "and no personal data, and can ask for a lease like anybody else if it ever needs a real value."),
        "title": "Marlow reads who read, who was refused, and under which lease",
        "screen": "Locking audit &middot; /locking-audit",
        "text": "Marlow holds the privacy auditor role. The page lists every read of locked columns and every "
                "refusal, every lease with who asked and who decided, and the treatment of every column. People "
                "appear by name, and no value from any table appears at all. Reading this page is recorded too. "
                "Each time the capture for this page ran, Devi asked and read again, so the same story appears "
                "once for each run.",
        "note": "<strong>Who read locked columns, and who was refused</strong> lists <strong>Devi</strong> with "
                "<strong>Refused</strong>, the reason and <strong>asked without a lease</strong>, and with "
                "<strong>Read</strong> beside Devi&rsquo;s purpose. Under <strong>Leases</strong>, Devi&rsquo;s lease "
                "reads <strong>5 / 3</strong>, meaning five rows for three days, with <strong>Approved by "
                "Hartley</strong>. No patient name appears.",
    },
    {
        "file": "18-marlow-sees-how-columns-were-treated.png", "actor": "marlow",
        "title": "Marlow sees how each table&rsquo;s columns were treated, and the small groups",
        "screen": "Locking audit &middot; How each table&rsquo;s columns were treated, Small groups",
        "text": "For each table sealed under a privacy contract, the page lists its columns, how identifying each is "
                "and what was done to it, so an auditor can check that what the contract promised is what happened. "
                "The small groups table shows each finding and whether the owner decided anything about it.",
        "note": "<strong>Clinic appointments, version 1.0.0</strong> lists <strong>patient_name</strong> and "
                "<strong>reason_for_visit</strong> as <strong>Locked under the person&rsquo;s key</strong>. Under "
                "<strong>Small groups</strong>, each row reads <strong>postcode, age_band</strong>, "
                "<strong>20</strong> people and <strong>12 (smaller than 5)</strong>, and the last column reads "
                "<strong>Contract will change by Hartley</strong> or <strong>Not decided</strong>.",
    },
    {
        "file": "19-marlow-sees-the-keys.png", "actor": "marlow",
        "title": "Marlow sees every key that was opened, and by whom",
        "screen": "Locking audit &middot; Keys",
        "text": "Each person has a key of their own. It is made when the person first appears in a table, opened "
                "when their locked values are read, and destroyed if they are ever erased. The list shows each "
                "opening with the time, who did it, and the person code, which means nothing on its own. The page "
                "shows the latest 25 and offers the rest.",
        "note": "Each line reads <strong>A key was opened</strong> with the time, <strong>Devi</strong> and a person "
                "code beginning with <strong>T-</strong>. A link at the bottom reads <strong>Show all</strong> and "
                "the number of entries.",
    },
]

ACTORS = {
    "devi": ("Devi", "Data engineer in the Health organisation"),
    "sam": ("Sam", "Researcher in the Health organisation"),
    "hartley": ("Hartley", "Data custodian and approver for the Cardiology department"),
    "marlow": ("Marlow", "Privacy auditor in the Health organisation"),
}

PAGE = Page(
    slug="locked-columns", org="health",
    title=series_entry("locked-columns")["title"],
    eyebrow="Health organisation &middot; Cardiology",
    lede="A data engineer seals a table of appointments from two files, so that each patient&rsquo;s name and reason "
         "for visit are locked under a key of their own. The engineer is refused the locked values, asks for five "
         "rows, and the custodian of the owning department decides. The platform reports which readable columns "
         "could single someone out, and a privacy auditor then checks how everything was used without seeing a "
         "single name.",
    actors=ACTORS, steps=STEPS,
    words=["organisation", "department", "dataset", "custodian", "approver", "sealed", "contract", "locked",
           "person_code", "locked_lease", "small_group", "auditor", "dpo", "version", "role"],
    shots_dir=SHOTS_ROOT / "locked-columns",
    capture_note="Captured live against the <code>health</code> organisation by "
                 "<code>web/walkthroughs/locked-columns.spec.ts</code> and built by "
                 "<code>docs/tools/build_locked_columns_walkthrough.py</code>. Every person and number in the two "
                 "files is invented, and the files themselves are in "
                 "<code>web/walkthroughs/fixtures/locked-appointments/</code>. What the platform refuses here is "
                 "proved separately by <code>verify/v129_locked_column_leases.py</code>, "
                 "<code>verify/v130_group_size_reports.py</code> and "
                 "<code>verify/v133_seal_table_from_files.py</code>.",
)

if __name__ == "__main__":
    missing = [s["file"] for s in STEPS if not (PAGE.shots_dir / s["file"]).exists()]
    if missing:
        raise SystemExit("No screenshot for: " + ", ".join(missing) + "\nRun the capture first: cd web && npx "
                         "playwright test --config=walkthroughs/playwright.config.ts locked-columns")
    write(PAGE)
