r"""Build docs/public/walkthroughs/locked-recordings-walkthrough.html from captured screenshots.

The screenshots come from web/walkthroughs/locked-recordings.spec.ts, which drives the real console against the
live stack. Run that first, then this builder:

    cd web
    npx playwright test --config=walkthroughs/playwright.config.ts locked-recordings
    ..\.venv\Scripts\python.exe ..\docs\tools\build_locked_recordings_walkthrough.py
"""
from __future__ import annotations

from walkthrough_kit import SHOTS_ROOT, Page, series_entry, write

SCREEN = "Bring data into a dataset &middot; /datasets/&lt;id&gt;/ingest"

STEPS = [
    {
        "file": "01-devi-is-asked-whether-anyone-may-need-erasing.png", "actor": "devi",
        "act": ("PART ONE", "Devi brings in recordings so that one person can be erased later",
                "A dataset is a named collection of data that belongs to one department, the team inside an organisation that owns "
                "some of its data. A recording of a consultation is about one person, and a person may later ask to be erased. "
                "This part shows how recordings are brought in so that erasing one person is possible: each recording is locked "
                "under a key made for the person it is about, and destroying that key makes everything locked under it unreadable for good."),
        "title": "Devi is asked whether anyone will ever need to be erased from this data",
        "screen": SCREEN,
        "text": "Devi is a data engineer in the Cardiology department of the Health organisation, a role that may bring data in. "
                "A dataset for consultation recordings is already registered and still empty. Before any file can be added, the page "
                "asks one question: will anyone ever need to erase one person from this data? The answer has to be given now, "
                "because locking cannot be added after the upload. The first choice, which is selected when the page opens, stores "
                "the files exactly as they are, and nothing in them is locked.",
        "note": "A box titled <strong>Will you ever need to erase one person from this data?</strong> has two choices. "
                "<strong>No. Store the files as they are.</strong> is selected, and the usual <strong>Upload files</strong> area is "
                "below it, with the button <strong>Seal this as version 1</strong> greyed out. Sealing finishes a version, which is one "
                "state of a dataset, so that nothing can be added to it afterwards.",
    },
    {
        "file": "02-devi-chooses-recordings.png", "actor": "devi",
        "title": "Devi answers yes, and says the data is recordings",
        "screen": SCREEN,
        "text": "Devi answers yes. The page explains what that means: the data of each person is locked under a key made for that "
                "person. Locking scrambles the data, so that nothing can read it without the key. Erasing a person destroys their "
                "key, so what was locked for them can never be read again, and the rest of the data stays. The page also says, in "
                "the same place, that locked data is hidden and not anonymised: it is still personal data until the person is "
                "erased. Devi then says what is being brought in. The other choice is a table of records. Here it is recordings, "
                "which are audio files with one person in each, so the upload area is replaced by a way to name the person of "
                "each recording.",
        "note": "<strong>Yes. Lock what identifies a person, with one key for each person.</strong> is selected, and the sentence "
                "<strong>Locked data is hidden, not anonymised</strong> is on the screen. Under <strong>What are you bringing "
                "in?</strong>, <strong>Recordings, one person in each</strong> is selected. Three boxes follow: the kind of "
                "identifier that names a person, the manifest, which is the list that says which person each recording is "
                "about, and the recordings themselves.",
    },
    {
        "file": "03-every-mistake-is-listed-before-anything-is-sent.png", "actor": "devi",
        "title": "A manifest and files with mistakes are refused, with every mistake listed",
        "screen": SCREEN,
        "text": "Devi types the kind of identifier that names a person, a hospital chart number. Devi then chooses a manifest, "
                "which is a small table saved as a CSV file with two columns, the recording and the person it is about, and three "
                "files with several mistakes in them. The page checks the manifest against the files straight away, before a single "
                "byte is sent, and lists every problem at once, so one look shows all of them. One of the files is an answer key. "
                "An answer key is a file that holds the correct written text of a recording, used later to check how well names were "
                "removed from it. It is named after its recording and belongs to the same person.",
        "note": "A yellow box titled <strong>Fix these before uploading:</strong> lists four problems, one on each line. They name "
                "<strong>visit-2.wav</strong>, which names no person, <strong>visit-4.wav</strong>, which is not in the manifest, "
                "<strong>visit-9.truth.json</strong>, an answer key whose recording is missing, and <strong>visit-3.wav</strong>, "
                "which is in the manifest although the file was not chosen. The button <strong>Upload and lock</strong> is greyed out.",
    },
    {
        "file": "04-the-files-are-ready.png", "actor": "devi",
        "title": "With the right manifest and files, the upload is allowed",
        "screen": SCREEN,
        "text": "Devi corrects the manifest and chooses the right files: two recordings, each with its own person named in the manifest, "
                "which is the table that says which person each recording is about, and the answer key of the first recording. "
                "Nothing is left to list, and the button that starts the upload can be pressed. The chart numbers in the manifest "
                "are invented, and the recordings are noise, so that nothing of a real person is in them.",
        "note": "The yellow box has gone. The button <strong>Upload and lock</strong> is now blue. The boxes read "
                "<strong>manifest.csv</strong> and <strong>3 files</strong>.",
    },
    {
        "file": "05-every-file-is-locked-under-its-persons-key.png", "actor": "devi",
        "title": "Each file is sent in parts and locked under the key of the person it is about as it arrives",
        "screen": SCREEN,
        "text": "Pressing <strong>Upload and lock</strong> sends each file to the platform in parts of at most 8 megabytes. A key is a "
                "secret value that locks and unlocks data, and locking scrambles the data so that it cannot be read without the key. "
                "Each part is locked under the key of the person it is about as it arrives, so a plain copy of a recording is never stored. The platform "
                "finds the person from the identifier, makes a key for any person it has not met before, and does not keep the "
                "identifier itself. A part that fails because the connection did is sent again by itself, and a connection that "
                "stays down pauses the upload and keeps what was already sent, so carrying on sends only the rest.",
        "note": "Three rows, <strong>visit-1.wav</strong>, <strong>visit-2.wav</strong> and <strong>visit-1.truth.json</strong>, each "
                "have a full green bar and end with <strong>Locked</strong>. A new button, <strong>Seal these recordings</strong>, "
                "appears below them.",
    },
    {
        "file": "06-the-recordings-are-sealed.png", "actor": "devi",
        "title": "Devi seals the recordings",
        "screen": SCREEN,
        "text": "Devi seals the recordings. Sealed means finished and closed for good, so that nothing can be added to it or changed "
                "in it afterwards, and later recordings become a new version, which is one sealed state of a dataset. Before it "
                "seals, the platform checks that every file was locked, that no ordinary file is mixed in with the locked ones, "
                "and that every answer key belongs to the same person as its recording. If any check fails, nothing is sealed, "
                "and every reason is listed.",
        "note": "A green box reads <strong>Sealed as version 1: 2 recordings about 2 people.</strong> It says that each "
                "recording is locked under the key of the person it is about, and offers a link, <strong>Open this version</strong>.",
    },
    {
        "file": "07-the-version-says-what-it-holds.png", "actor": "devi",
        "title": "The version says how many recordings and people it holds, and names nobody",
        "screen": "Version detail &middot; /versions/&lt;id&gt;",
        "text": "Devi opens the new version, which is the sealed state of the dataset that the upload just made. Under the heading <strong>Locked recordings</strong> the page counts what it holds and "
                "says nothing about who the people are: no identifier and no name from the manifest appears anywhere on it. A "
                "pipeline run is a program that runs on a version, and it opens a recording only while it is running, with every "
                "opening written to a record that names the run. When a person is erased later, their recordings are counted here "
                "as unreadable, and every later run leaves them out.",
        "note": "<strong>Locked recordings</strong> shows three counts: <strong>Recordings 2</strong>, <strong>People 2</strong> "
                "and <strong>Unreadable because the person was erased 0</strong>. The line below says that every opening is "
                "recorded with the run named.",
    },
]

ACTORS = {
    "devi": ("Devi", "Data engineer in the Health organisation"),
}

PAGE = Page(
    slug="locked-recordings", org="health",
    title=series_entry("locked-recordings")["title"],
    eyebrow="Health organisation &middot; Cardiology",
    lede="A data engineer brings in recordings of consultations. The page first asks whether anyone will ever need to be erased from "
         "this data, and the answer has to be given before the upload. With the answer yes, every recording is locked under a key "
         "made for the person it is about as it arrives, a mistake in the list of people is caught before anything is sent, and the sealed "
         "version says how many recordings and people it holds without naming a single one.",
    actors=ACTORS, steps=STEPS,
    words=["organisation", "department", "dataset", "sealed", "version", "locked_recording", "recordings_manifest", "answer_key"],
    shots_dir=SHOTS_ROOT / "locked-recordings",
    capture_note="Captured live against the <code>health</code> organisation by "
                 "<code>web/walkthroughs/locked-recordings.spec.ts</code> and built by "
                 "<code>docs/tools/build_locked_recordings_walkthrough.py</code>. The recordings are noise and the chart numbers are "
                 "invented. What the platform refuses here is proved separately by <code>verify/v137_locked_recordings.py</code>, and "
                 "what a pipeline can and cannot do with a locked recording by <code>verify/v138_worker_opens_locked_recordings.py</code>.",
)

if __name__ == "__main__":
    missing = [s["file"] for s in STEPS if not (PAGE.shots_dir / s["file"]).exists()]
    if missing:
        raise SystemExit("No screenshot for: " + ", ".join(missing) + "\nRun the capture first: cd web && npx "
                         "playwright test --config=walkthroughs/playwright.config.ts locked-recordings")
    write(PAGE)
