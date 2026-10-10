r"""Build the two pages of "Devi checks what arrived" in the Health story:

  docs/public/walkthroughs/read-a-few-rows-walkthrough.html    five locked rows of the appointments table, opened with an approver's say
  docs/public/walkthroughs/open-a-few-files-walkthrough.html   two of the three recordings, opened in the page, with an approver's say

The screenshots come from web/walkthroughs/story-b.spec.ts (parts rows and files), which drives the real console against the live Health organisation as
Devi, Sam and Hartley, on the table and the recordings that the previous page brought in. Run the capture first (scripts/walkthroughs/capture-health-story.py),
then this builder:

    ..\.venv\Scripts\python.exe ..\docs\tools\build_check_what_arrived_walkthroughs.py
"""
from __future__ import annotations

from walkthrough_kit import SHOTS_ROOT, Page, series_entry, write

ROWS_STEPS = [{'file': '01-devi-is-refused-the-rows.png',
  'actor': 'devi',
  'act': ('PART ONE',
          'Devi cannot read what is locked, so Devi asks',
          'A department approver is a data custodian who is listed for one department and may decide who reads its data. A <strong>locked-column '
          'lease</strong> is permission, decided by an approver of the owning department, to read a few rows of a table&rsquo;s locked columns for a '
          'few days, for a stated purpose. Holding a role does not open locked rows. Only an approver of the department that owns the data, or a person who holds a lease, '
          'may read them.'),
  'title': 'Devi tries to read the locked rows and is refused',
  'screen': 'Locked columns, opened &middot; /versions/&lt;id&gt;/locked-rows',
  'text': 'Devi brought the table in, but is not an approver of the Cardiology department, so the platform refuses and says why in its own words.',
  'note': 'A red box reads <strong>Could not read these rows.</strong> and gives one reason: <strong>this person is not an approver of the '
          'department that owns this data and holds no current lease for its locked columns</strong>.'},
 {'file': '02-devi-asks-for-five-rows.png',
  'actor': 'devi',
  'title': 'Devi writes why, and asks for five rows for three days',
  'screen': 'Version detail &middot; Your access',
  'text': 'The form asks for a purpose, a number of days and a number of rows. The platform states the limits itself, at most 7 days and at most 20 '
          'rows, and the purpose is recorded with the request. The rows are chosen by the platform, not by the person asking, and they are the same '
          'every time, so asking repeatedly cannot be used to read the whole table.',
  'note': '<strong>What you need them for</strong> holds Devi&rsquo;s reason. <strong>For how many days (up to 7)</strong> reads <strong>3</strong>, '
          'and <strong>How many rows (up to 20)</strong> reads <strong>5</strong>. The small print reads <strong>The rows you can read are chosen '
          'for you and are the same every time you look</strong>, and <strong>Ask for access</strong> is ready.'},
 {'file': '03-the-request-waits.png',
  'actor': 'devi',
  'title': 'The request waits for an approver',
  'screen': 'Version detail &middot; Your access',
  'text': 'Asking opens nothing. Devi still cannot read any locked value until an approver of the Cardiology department decides.',
  'note': 'A yellow box reads <strong>Your request is waiting for an approver.</strong> and repeats the request: <strong>for 5 rows for 3 '
          'days</strong>, with Devi&rsquo;s reason. A link reads <strong>See your requests</strong>.'},
 {'file': '04-sam-has-no-way-to-ask.png',
  'actor': 'sam',
  'title': 'Sam, a researcher, has no way to ask at all',
  'screen': 'Version detail &middot; Your access',
  'text': 'Sam holds the researcher role, which is for working with data that has already been de-identified, meaning the names, dates and other '
          'identifying detail have been taken out. Sam opens the same table and sees how it is built, but no value, and is not offered a form, '
          'because the locked values are not needed to work with the readable columns.',
  'note': 'The box under <strong>Your access</strong> reads <strong>Your role cannot read the locked values and cannot ask for them</strong>. The '
          'table above it lists the same columns Devi saw, and there is no form.'},
 {'file': '05-hartley-sees-the-request.png',
  'actor': 'hartley',
  'act': ('PART TWO',
          'Hartley, who answers for Cardiology, decides',
          'The person who asks never decides. Only an approver of the department that owns the data can, and an approver of another department '
          'cannot.'),
  'title': 'Hartley sees the request waiting',
  'screen': 'Locked-column access &middot; /locked-column-access',
  'text': 'Hartley is a data custodian and an approver of the Cardiology department, so the request about Cardiology data appears on this page. It '
          'shows who asked, why, how many rows and days, and offers <strong>Approve</strong> and <strong>Decline</strong>. A custodian of another '
          'department would see nothing here.',
  'note': 'Under <strong>Waiting for you to decide</strong> there is one row: <strong>Devi</strong>, the reason, <strong>5 rows, 3 days</strong> and '
          'the state <strong>Waiting for an approver</strong>, with a box reading <strong>A note, recorded with your decision</strong>. <strong>In '
          'force in your departments</strong> reads <strong>No leases in force</strong>.'},
 {'file': '06-hartley-writes-a-note.png',
  'actor': 'hartley',
  'title': 'Hartley writes a note, and both buttons are ready',
  'screen': 'Locked-column access &middot; Waiting for you to decide',
  'text': 'The note is stored with the decision. A refusal needs a note, so that the person who asked is told why. Approving needs none, but a note '
          'makes the record easier to read later.',
  'note': 'The note box holds <strong>Five rows is enough for that.</strong> and both <strong>Approve</strong> and <strong>Decline</strong> are '
          'ready to press.'},
 {'file': '07-the-lease-is-in-force.png',
  'actor': 'hartley',
  'title': 'Hartley approves, and the lease is in force',
  'screen': 'Locked-column access &middot; In force in your departments',
  'text': 'The lease now lasts three days from the approval and opens five rows. Hartley can end it early at any time, and so can Devi.',
  'note': '<strong>Waiting for you to decide</strong> now reads <strong>No requests waiting for you</strong>. <strong>In force in your '
          'departments</strong> lists <strong>Devi</strong>, <strong>5 rows, 3 days</strong>, the label <strong>In force</strong>, an end date three '
          'days after the approval, and <strong>Approved by Hartley</strong> with the note <strong>Five rows is enough for that.</strong> A button '
          'reads <strong>End now</strong>.'},
 {'file': '08-hartley-sees-the-small-groups.png',
  'actor': 'hartley',
  'title': 'Hartley reads the report on small groups',
  'screen': 'Version detail &middot; Small groups',
  'text': 'A <strong>small group</strong> is a combination of readable columns, such as postcode and age band, shared by only a few people, so that '
          'one of them could be singled out even though no column names anybody. The platform counts different people, not rows, because a person '
          'with several visits is still one person to find. The report is advice. It never stopped the table being sealed and shows no values. The '
          'approvers, the de-identification reviewer, the privacy auditor and the data protection officer can read it. The data protection officer is '
          'the person who watches how an organisation handles personal data. Only an approver can decide what to do about it.',
  'note': 'A yellow box reads <strong>12 of 20 people sit in a group smaller than 5. The smallest group holds 1 person.</strong> Two lines say what '
          'locking each column would leave: <strong>Locking postcode would leave 4 people in a small group</strong> and <strong>Locking age_band '
          'would leave 6 people in a small group</strong>. Below, <strong>What will you do about it?</strong> offers two choices, and <strong>Record '
          'this decision</strong> is greyed out until a reason is written.'},
 {'file': '09-hartley-records-a-decision.png',
  'actor': 'hartley',
  'title': 'Hartley records what will be done, and why',
  'screen': 'Version detail &middot; Small groups',
  'text': 'Hartley chooses to change the contract before the next table is sealed, and writes why. The decision is made once, and the platform '
          'refuses to change it afterwards, even to somebody who writes to the database directly.',
  'note': 'A line reads <strong>Decided by Hartley</strong>, then the date, <strong>the contract will change before the next table</strong>, and '
          'Hartley&rsquo;s reason, <strong>The next contract version will lock the postcode.</strong> The decision form has gone.'},
 {'file': '10-devi-holds-a-lease.png',
  'actor': 'devi',
  'act': ('PART THREE', 'Devi reads the five rows the lease opens', 'A lease opens a fixed few rows for a short time. It does not open the table.'),
  'title': 'Devi now holds a lease',
  'screen': 'Version detail &middot; Your access',
  'text': 'The same section that showed a form now says what Devi holds: five of the 23 rows, until the lease ends, approved by Hartley for the '
          'stated reason. Devi can read the rows or end the lease personally.',
  'note': 'A green box reads <strong>You hold a lease: 5 rows of 23</strong>, with the end date, and <strong>Approved by Hartley</strong> and the '
          'date, then <strong>for:</strong> followed by Devi&rsquo;s reason. Two actions are offered: <strong>Read your 5 rows</strong> and '
          '<strong>End this lease now</strong>.'},
 {'file': '11-devi-reads-five-rows.png',
  'actor': 'devi',
  'title': 'Devi reads five rows, with the locked values opened',
  'screen': 'Locked columns, opened &middot; /versions/&lt;id&gt;/locked-rows',
  'text': 'These are the only five rows Devi can read, and they are the same five every time. The locked values are opened for them: the actual names and '
          'reasons for visits. Every key that is opened for this is recorded, naming the person it belongs to by their person code. The other 18 '
          'rows stay locked.',
  'note': 'A yellow banner reads <strong>Your lease opens 5 of 23 rows. They are the same rows every time, and a later lease opens the same '
          'ones.</strong> The table shows five rows with names under <strong>patient_name</strong> and reasons under '
          '<strong>reason_for_visit</strong>. The column <strong>patient_number (now a person code)</strong> holds labels that begin with '
          '<strong>T-</strong>.'}]

FILES_STEPS = [
    {
        "file": "12-devi-asks-to-open-the-recordings.png", "actor": "devi",
        "act": ("PART ONE", "Devi asks to open some of the recordings",
                "The recordings that Devi, a data engineer, brought in are locked, each under the key of the patient it is about, so the version "
                "cannot be handed over whole. A person who answers for none of the data can ask to open a few of the files, for a stated purpose and a "
                "short time. Munitas chooses which files, and an approver of the department that owns them decides."),
        "title": "Devi asks to open three recordings, in the page, for an hour",
        "screen": "A dataset version &middot; /versions/&lt;id&gt;",
        "text": "The version page has a form for asking to open files. Devi chooses where to open them, here in the page, where a file plays and "
                "cannot be downloaded. Devi says what the files are for, how many are needed and for how long. Munitas states its own limits, at "
                "most 10 files and at most 1 hour here. The person asking cannot pick which files, and the same files stay the same until the "
                "approval ends.",
        "note": "The heading is <strong>Ask to open some of these files</strong>. The choice <strong>In this page: you listen, and a file plays here "
                "and cannot be downloaded</strong> is selected, <strong>What the files are for</strong> holds Devi's reason, <strong>How many files "
                "(up to 10)</strong> says <strong>3</strong> and <strong>For how long (up to 1 hour)</strong> says <strong>1</strong>.",
    },
    {
        "file": "14-hartley-sees-the-request-to-open-files.png", "actor": "hartley",
        "act": ("PART TWO", "Hartley decides, and allows fewer",
                "The approver may allow fewer files or fewer hours than were asked for, never more, and says why when declining."),
        "title": "Hartley sees the request under Files",
        "screen": "Locked data requests &middot; /locked-column-access",
        "text": "Hartley is the custodian of Cardiology, which owns the recordings. The page of locked data requests has a section for files, with the "
                "requests that wait for Hartley's decision. Each shows who asked, what for, where they will open the files, how many files and for "
                "how long.",
        "note": "Under <strong>Files</strong> and <strong>Waiting for you to decide</strong> there is one row: <strong>consultation-recordings</strong> "
                "<strong>v1</strong>. It names <strong>Devi</strong> as the person who asked and gives Devi's reason, <strong>listen to the recordings to "
                "judge how long a consultation runs</strong>. The same row says <strong>In the page</strong>, <strong>3 files, 1 hour</strong>, and the "
                "state <strong>Waiting for an approver</strong>.",
    },
    {
        "file": "15-hartley-allows-two-of-the-three.png", "actor": "hartley",
        "title": "Hartley allows two of the three, and writes why",
        "screen": "Locked data requests &middot; /locked-column-access",
        "text": "Hartley lowers the number of files from three to two and writes a note, which is stored with the decision. Munitas, not Hartley, "
                "chooses which two files: the approver is never asked which, so nobody can steer a person to a particular patient.",
        "note": "<strong>Files to allow</strong> says <strong>2</strong>, <strong>Hours to allow</strong> says <strong>1</strong>, the note box holds "
                "Hartley's reason, and the buttons are <strong>Allow</strong> and <strong>Decline</strong>.",
    },
    {
        "file": "16-the-access-is-in-force.png", "actor": "hartley",
        "title": "The permission is in force, for two files and an hour",
        "screen": "Locked data requests &middot; /locked-column-access",
        "text": "The request has moved from waiting to allowed. The row shows what Hartley allowed, until when, and the note Hartley wrote. Hartley can "
                "end the permission early at any time.",
        "note": "Under <strong>Allowed now in your departments</strong> the row says <strong>2 files, 1 hour</strong> with the state "
                "<strong>Allowed</strong>, and <strong>Allowed by Hartley</strong> followed by <strong>Two of the three are enough to judge their "
                "length.</strong> A button reads <strong>End now</strong>.",
    },
    {
        "file": "18-devi-plays-one.png", "actor": "devi",
        "act": ("PART THREE", "Devi listens, and the access ends",
                "A permission to open files is for a stated purpose and a short time. It opens a few files, not the version."),
        "title": "Devi sees two of the recordings and plays one",
        "screen": "A dataset version &middot; /versions/&lt;id&gt;",
        "text": "Back on the version page, the list of files now offers a Play button on two of the three recordings, the two Munitas chose. A "
                "recording plays in the page through Munitas, which unlocks it with the key of the patient it is about, and it cannot be "
                "downloaded. Every opening is written down.",
        "note": "<strong>visit-2</strong> and <strong>visit-3</strong> each show a <strong>Play</strong> button, and <strong>visit-2</strong> is "
                "playing, with an audio player that shows the length of the recording. Each file is still marked <strong>Locked</strong>.",
    },
    {
        "file": "19-when-the-access-ends-the-files-close.png", "actor": "devi",
        "title": "When Hartley ends the permission, the files close again",
        "screen": "A dataset version &middot; /versions/&lt;id&gt;",
        "text": "Hartley ends the permission, and Devi's page goes back to how it was: the files are listed and locked, and none can be played. "
                "To listen again, Devi would have to ask again.",
        "note": "A yellow box reads <strong>You cannot open these files.</strong> The three files <strong>visit-1</strong>, <strong>visit-2</strong> and "
                "<strong>visit-3</strong> are listed with <strong>Locked</strong> and no <strong>Play</strong> button.",
    },
]

ROWS_ACTORS = {
    "devi": ("Devi", "Data engineer in the Health organisation"),
    "sam": ("Sam", "Researcher in the Health organisation"),
    "hartley": ("Hartley", "Data custodian for the Cardiology department"),
}
FILES_ACTORS = {
    "devi": ("Devi", "Data engineer in the Health organisation"),
    "hartley": ("Hartley", "Data custodian for the Cardiology department"),
}

ROWS_PAGE = Page(
    slug="read-a-few-rows", org="health",
    title=series_entry("read-a-few-rows")["title"],
    eyebrow="Health organisation &middot; The people",
    lede="Devi brought in the table of appointments, and is now refused the locked names and reasons. Devi asks to read five rows for three days, "
         "and the custodian of the owning department decides. A researcher has no way to ask at all. Munitas reports which readable columns could "
         "single someone out, and the lease opens exactly five rows.",
    actors=ROWS_ACTORS, steps=ROWS_STEPS,
    words=["organisation", "department", "dataset", "custodian", "approver", "sealed", "contract", "locked", "person_code", "locked_lease",
           "small_group", "auditor", "dpo", "version", "role"],
    shots_dir=SHOTS_ROOT / "check-what-arrived",
    capture_note="Captured live against the <code>health</code> organisation by <code>web/walkthroughs/story-b.spec.ts</code> and built by "
                 "<code>docs/tools/build_check_what_arrived_walkthroughs.py</code>.",
)

FILES_PAGE = Page(
    slug="open-a-few-files", org="health",
    title=series_entry("open-a-few-files")["title"],
    eyebrow="Health organisation &middot; The people",
    lede="Devi asks to open three of the locked recordings in the page, for an hour. The custodian of the owning department allows two, and "
         "Munitas chooses which. Devi plays one, and when the custodian ends the permission the files close again.",
    actors=FILES_ACTORS, steps=FILES_STEPS,
    words=["organisation", "department", "dataset", "custodian", "approver", "locked", "locked_recording", "version", "lease"],
    shots_dir=SHOTS_ROOT / "check-what-arrived",
    capture_note="Captured live against the <code>health</code> organisation by <code>web/walkthroughs/story-b.spec.ts</code> and built by "
                 "<code>docs/tools/build_check_what_arrived_walkthroughs.py</code>.",
)

if __name__ == "__main__":
    for page in (ROWS_PAGE, FILES_PAGE):
        missing = [s["file"] for s in page.steps if not (page.shots_dir / s["file"]).exists()]
        if missing:
            raise SystemExit("No screenshot for: " + ", ".join(missing) + "\\nRun the capture first: scripts/walkthroughs/capture-health-story.py --only b2")
        write(page)
