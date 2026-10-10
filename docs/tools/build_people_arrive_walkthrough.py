r"""Build docs/public/walkthroughs/people-arrive-walkthrough.html from captured screenshots.

The screenshots come from web/walkthroughs/story-b.spec.ts (parts recordings and table), which drives the real console against the live Health organisation
as Devi, a data engineer, with the files that scripts/seed/make-story-b-files.py writes. Run the capture first (scripts/walkthroughs/capture-health-story.py),
then this builder:

    ..\.venv\Scripts\python.exe ..\docs\tools\build_people_arrive_walkthrough.py

This is the first page of "The people", the part of the Health story about data that identifies patients: three recordings and a table of appointments, about the
same patients, arrive locked so that one patient can be erased later.
"""
from __future__ import annotations

from walkthrough_kit import SHOTS_ROOT, Page, series_entry, write

STEPS = [
    {
        "file": "01-devi-registers-the-dataset.png", "actor": "devi",
        "act": ("PART ONE", "Three recordings arrive locked",
                "The research programme now needs data about people: recordings of consultations, and a table of clinic appointments about the "
                "same patients. Everything in it that identifies a person must be erasable later, so that one patient can be removed without "
                "losing anybody else's data. Devi, a data engineer in the Cardiology department, brings it in. Every name and number in this story is "
                "invented, and the recordings were made with a computer voice."),
        "title": "Devi registers a dataset for the recordings",
        "screen": "Bring a dataset in &middot; /datasets/register",
        "text": "A dataset is a named collection of data that Munitas keeps, and it has an owning department from the first moment, so that "
                "somebody is answerable for deciding who may read it. Devi names this one <em>consultation-recordings</em> and chooses the "
                "Cardiology department. Nothing has been uploaded yet.",
        "note": "The <strong>Name</strong> box holds <strong>consultation-recordings</strong> and <strong>Owned by</strong> says "
                "<strong>Cardiology</strong>. Beside the Register button the page says <strong>Nothing is readable yet. This only creates the "
                "record and its owner.</strong>",
    },
    {
        "file": "02-devi-is-asked-whether-anyone-may-need-erasing.png", "actor": "devi",
        "title": "The first question: will anybody ever need to be erased from this data?",
        "screen": "Bring a dataset in &middot; /datasets/&lt;id&gt;/ingest",
        "text": "The page asks a question that cannot be asked later. To erase one person from locked data, Munitas destroys the key that the "
                "person's data was locked under. What was locked for that person can then never be read again, and the rest of the data stays. "
                "The key has to be made as the data arrives, which is why the question comes first. The safe default is no.",
        "note": "The heading is <strong>Will you ever need to erase one person from this data?</strong> and under it the page says "
                "<strong>Decide now: locking cannot be added after the upload.</strong> The answer <strong>No. Store what I bring as it is.</strong> "
                "is selected until somebody changes it.",
    },
    {
        "file": "03-devi-chooses-recordings.png", "actor": "devi",
        "title": "Devi answers yes and chooses files, one person in each",
        "screen": "Bring a dataset in &middot; /datasets/&lt;id&gt;/ingest",
        "text": "Devi answers yes, and chooses to bring in files, each of which is about one person. A manifest says which person each file is "
                "about. A manifest is a plain text table with two columns, <em>file</em> and <em>person</em>. The recordings also come with an "
                "answer key each, which is the correct written text of a recording, used later to check how well names were removed from it.",
        "note": "The answer <strong>Yes. Lock what identifies a person, with one key for each person.</strong> is selected, and so is "
                "<strong>Files, one person in each</strong>. The text below says that <strong>Each file is locked under the key of the person it "
                "is about as it uploads.</strong>",
    },
    {
        "file": "04-every-mistake-is-listed-before-anything-is-sent.png", "actor": "devi",
        "title": "Every mistake in the files is listed before anything is sent",
        "screen": "Bring a dataset in &middot; /datasets/&lt;id&gt;/ingest",
        "text": "Devi first tries a manifest and a set of files that contain mistakes. Munitas checks the whole set and lists every problem at "
                "once, so that nothing half correct is uploaded and locked, and so that Devi does not have to find the mistakes one at a time.",
        "note": "<strong>Fix these before uploading:</strong> lists four problems, one on each line. The first says <strong>visit-2.wav names no "
                "person in the manifest, and a file is locked to one person.</strong> The second says <strong>visit-4.wav is not in the manifest, "
                "so nobody is named as the person it is about.</strong> The button <strong>Upload and lock</strong> is greyed out.",
    },
    {
        "file": "05-the-files-are-ready.png", "actor": "devi",
        "title": "The corrected manifest and the six files are ready",
        "screen": "Bring a dataset in &middot; /datasets/&lt;id&gt;/ingest",
        "text": "Devi corrects the manifest and chooses the three recordings with the answer key for each. The manifest names each patient by "
                "patient number, and Devi has told Munitas that this is the kind of identifier it holds. The same kind of number will name the "
                "same patients in the table of appointments later, which is how Munitas will know that they are one person.",
        "note": "The box labelled <strong>What kind of identifier names a person</strong> holds <strong>patient number</strong>, <strong>The manifest</strong> "
                "holds <strong>manifest.csv</strong>, and <strong>The files</strong> lists <strong>visit-1.wav, visit-2.wav, visit-3.wav, "
                "visit-1.truth.json, visit-2.truth.json, visit-3.truth.json</strong>. The list of problems has gone.",
    },
    {
        "file": "06-every-file-is-locked-under-its-persons-key.png", "actor": "devi",
        "title": "Every file is locked under the key of the patient it is about",
        "screen": "Bring a dataset in &middot; /datasets/&lt;id&gt;/ingest",
        "text": "Devi presses Upload and lock. Each file is locked as it arrives, under a key that Munitas makes for the patient the file is about. "
                "Devi is never given the keys and cannot see them.",
        "note": "Each of the six files ends with <strong>Locked</strong>. The recordings are <strong>8.2 MB</strong>, <strong>4.1 MB</strong> and "
                "<strong>7.3 MB</strong>. The button <strong>Seal these files</strong> has appeared, with the line <strong>Closes the upload. "
                "Nothing can be added to this version afterwards.</strong>",
    },
    {
        "file": "07-the-recordings-are-sealed.png", "actor": "devi",
        "title": "Devi seals the files, and Munitas counts them without naming anybody",
        "screen": "Bring a dataset in &middot; /datasets/&lt;id&gt;/ingest",
        "text": "Devi seals the upload. A sealed version is one state of a dataset that is finished and closed for good: nothing can be added to "
                "it, and files that arrive later become a new version. The message counts the files and the people, and names nobody.",
        "note": "The green box says <strong>Sealed as version 1: 3 files about 3 people.</strong> and <strong>Each file is locked under the key of the "
                "person it is about.</strong> The three answer keys do not count as files of their own, because each belongs to its recording.",
    },
    {
        "file": "08-the-version-says-what-it-holds.png", "actor": "devi",
        "title": "Even Devi cannot open the files that Devi brought in",
        "screen": "A dataset version &middot; /versions/&lt;id&gt;",
        "text": "The page of the new version lists its files. A locked file is opened only through Munitas, which unlocks it with the key of the "
                "person it is about and writes down every opening. Devi is not an approver of the Cardiology department, so Devi has to ask. Asking is shown in the walkthroughs Read five locked rows, with an approver's say, and Open two of the recordings, "
                "with an approver's say.",
        "note": "The panel <strong>Files</strong> lists <strong>visit-1</strong>, <strong>visit-2</strong> and <strong>visit-3</strong>, each marked "
                "<strong>Locked</strong>, with the sentence <strong>You cannot open these files.</strong> No patient number and no name appears "
                "on the page.",
    },
    {
        "file": "11-a-bad-file-is-refused.png", "actor": "devi",
        "act": ("PART TWO", "The same patients arrive in a table",
                "Three of the patients in the recordings also appear in a table of clinic appointments, with other patients. Devi brings the "
                "table in the same way, with a table instead of files."),
        "title": "A table with mistakes is refused, with every mistake named",
        "screen": "Bring a dataset in &middot; /datasets/&lt;id&gt;/ingest",
        "text": "Devi registered a second dataset, <em>clinic-appointments</em>, owned by Cardiology, answered yes to the erasure question, and "
                "chose a table. A table needs two files. The first is a contract, which says, column by column, how identifying each column "
                "is and what is done to it, and which column names the person each row is about. The second holds the rows. Devi first sends "
                "rows that contain two mistakes.",
        "note": "The page says <strong>Nothing was sealed. Fix these first:</strong> and lists <strong>A-1001: seen_on is 'not a date', which "
                "is not a date written like 2026-03-31.</strong> and <strong>A-1012: names nobody, because patient_number (the patient number) is "
                "empty.</strong>",
    },
    {
        "file": "12-the-table-is-sealed.png", "actor": "devi",
        "title": "The corrected table is sealed, and Munitas reports what is left identifiable",
        "screen": "Bring a dataset in &middot; /datasets/&lt;id&gt;/ingest",
        "text": "With the mistakes fixed, the table is sealed. Munitas also reports on small groups. A column left readable, such as an age band "
                "or a postcode, can point at one person when few people share the same combination. The report does not stop the seal. It "
                "gives the approver of the department something to decide about later.",
        "note": "The green box says <strong>Sealed as version 1: 23 rows about 20 people.</strong> and <strong>Their identifying values are "
                "locked, each under a key made for that person.</strong> A second line adds <strong>12 people sit in a group smaller than 5. It "
                "did not stop the seal.</strong>",
    },
    {
        "file": "13-the-locked-columns.png", "actor": "devi",
        "title": "The version page says what was done to every column",
        "screen": "A dataset version &middot; /versions/&lt;id&gt;",
        "text": "The page of the new version lists each column of the table, what it is, and what was done to it. A column that names a person is "
                "replaced by a person code, which is a label that stands for one person and means nothing on its own. Columns that describe "
                "a person are locked under that person's key. Columns that are not identifying stay readable, so that analysis keeps working.",
        "note": "<strong>patient_name</strong> and <strong>reason_for_visit</strong> say <strong>Locked under the person's key</strong>, "
                "<strong>patient_number</strong> says <strong>Replaced by a person code</strong>, and <strong>postcode</strong> says "
                "<strong>Readable</strong> with <strong>Could identify someone in combination with others</strong>. Under the table: "
                "<strong>23 rows.</strong>",
    },
]

ACTORS = {
    "devi": ("Devi", "Data engineer in the Health organisation"),
}

PAGE = Page(
    slug="people-arrive", org="health",
    title=series_entry("people-arrive")["title"],
    eyebrow="Health organisation &middot; The people",
    lede="The research programme now needs data about patients: three recordings of consultations and a table of clinic appointments about "
         "the same patients. Devi, a data engineer, brings both in so that each patient's identifying data is locked under a key made for that "
         "patient. Destroying one patient's key later erases that patient and nobody else.",
    actors=ACTORS, steps=STEPS,
    words=["organisation", "department", "dataset", "locked", "locked_recording", "recordings_manifest", "answer_key", "contract", "person_code",
           "sealed", "version", "sensitivity"],
    shots_dir=SHOTS_ROOT / "people-arrive",
    capture_note="Captured live against the <code>health</code> organisation by <code>web/walkthroughs/story-b.spec.ts</code>, with the files made by "
                 "<code>scripts/seed/make-story-b-files.py</code>, and built by <code>docs/tools/build_people_arrive_walkthrough.py</code>.",
)

if __name__ == "__main__":
    missing = [s["file"] for s in STEPS if not (PAGE.shots_dir / s["file"]).exists()]
    if missing:
        raise SystemExit("No screenshot for: " + ", ".join(missing) + "\nRun the capture first: scripts/walkthroughs/capture-health-story.py --only b1")
    write(PAGE)
