r"""Build docs/public/walkthroughs/development-sample-walkthrough.html from captured screenshots.

The screenshots come from web/walkthroughs/development-sample.spec.ts, which drives the real console against the live stack, with the pipeline's own
script running for real in the sandbox worker. Run that first, then this builder:

    cd web
    npx playwright test --config=walkthroughs/playwright.config.ts development-sample
    ..\.venv\Scripts\python.exe ..\docs\tools\build_development_sample_walkthrough.py
"""
from __future__ import annotations

from walkthrough_kit import SHOTS_ROOT, Page, series_entry, write

VERSION = "Version detail &middot; /versions/&lt;id&gt;"
RUN = "Pipeline run &middot; /pipeline-runs/&lt;id&gt;"

STEPS = [
    {
        "file": "01-devi-asks-for-a-sample.png", "actor": "devi",
        "act": ("PART ONE", "Devi asks for a sample of the recordings",
                "A pipeline is a series of steps that Munitas runs on a version of a dataset. A pipeline version that has a custom step, a step whose "
                "script was written by the person who registered the pipeline, starts in development, which means it may run only on development data. "
                "For recordings of patients, development data is a small sample of them, or recordings that hold no real person. This part shows where "
                "a sample comes from."),
        "title": "Devi asks for a development sample of the recordings",
        "screen": VERSION,
        "text": "Devi is a data engineer in the Cardiology department of the Health organisation. A department is the team inside an organisation "
                "that owns some of its data. Devi has written a pipeline version whose step makes the first half second of each recording silent, and "
                "it is in development. Devi opens the sealed version of the consultation recordings, which means the version that is finished and "
                "closed for good, so that it can never be edited. Under the heading Development samples, Devi says how many recordings are wanted, "
                "for how many days, and what the sample is for. Devi cannot choose which recordings: the platform draws them.",
        "note": "The heading <strong>Development samples</strong> explains that a sample is a few of these files, drawn by the platform, that "
                "names the same locked files and copies none. Under <strong>Ask for a sample</strong>, <strong>Draw a sample for me.</strong> "
                "is selected, the boxes read <strong>4</strong> files and <strong>5</strong> days, and the purpose reads "
                "<strong>Building a step that makes the first half second of each recording silent</strong>. The limits are written beside the "
                "boxes: at most 50 files and at most 14 days.",
    },
    {
        "file": "02-the-request-waits-for-an-approver.png", "actor": "devi",
        "title": "The request waits for an approver",
        "screen": VERSION,
        "text": "Pressing the button records the request and does nothing else. A sample needs the agreement of an approver of the department that "
                "owns the recordings, and the approver cannot be the person asking. An approver is a data custodian, a person who may decide who "
                "reads a department&rsquo;s data, who is listed for that department. Devi may withdraw the request while it waits.",
        "note": "Under <strong>Your samples</strong> a row reads <strong>Waiting for an approver</strong> and <strong>Asked for 4 files for 5 "
                "days, to build and test with</strong>, and offers the button <strong>Withdraw this request</strong>. The form above now offers "
                "only <strong>Name the files that went wrong.</strong>, because a request to draw a sample is already waiting.",
    },
    {
        "file": "03-hartley-sees-the-request.png", "actor": "hartley",
        "title": "Hartley sees the request, and cannot choose the recordings",
        "screen": VERSION,
        "text": "Hartley is the data custodian for Cardiology, so the same page shows the request to Hartley, under Waiting for your decision. It "
                "says who asked, how many recordings, for how many days and why. Hartley may lower the number or the days, and may not raise "
                "them. Hartley may not pick recordings either: the platform draws them, so nobody can steer a sample towards particular patients.",
        "note": "Under <strong>Waiting for your decision</strong> a yellow box reads <strong>Devi asks for 4 files for 5 days, to build and "
                "test with.</strong>, then the purpose. Two boxes, <strong>Files</strong> and <strong>Days</strong>, each say that you may "
                "lower this, and a third reads <strong>A note, recorded with your decision</strong>. The text says <strong>You cannot choose the "
                "files: the platform draws them.</strong> The buttons are <strong>Approve</strong> and <strong>Decline</strong>. A white "
                "box above them lets an approver declare the files synthetic, which Part Four uses.",
    },
    {
        "file": "04-hartley-approves-fewer-and-the-platform-draws.png", "actor": "hartley",
        "title": "Hartley approves fewer recordings, and the platform draws them",
        "screen": VERSION,
        "text": "Hartley lowers the request to 3 recordings for 3 days, adds a note, and approves. The platform then draws the recordings by one "
                "fixed method: at most one recording from each patient, spread across short, medium and long recordings, and picked by a value "
                "that comes out the same every time for the same person asking for the same purpose. Nobody chooses, including Hartley. The "
                "platform seals a new version, the sample, that names the same locked recordings as the original. No audio is copied.",
        "note": "Under <strong>In force for these files</strong> one line reads <strong>Devi: 3 files (1 short, 1 medium and 1 long), "
                "ends</strong> followed by a date three days away, and offers <strong>End this sample</strong>. The line shows how many "
                "recordings came from each length and names nobody but Devi: no recording and no patient.",
    },
    {
        "file": "05-devi-opens-the-sample.png", "actor": "devi",
        "act": ("PART TWO", "Devi uses the sample",
                "Devi now runs the pipeline version on the sample. Running it on the real recordings is still refused, which shows what development "
                "means."),
        "title": "Devi opens the sample, which says what it is",
        "screen": VERSION,
        "text": "The platform made the sample as a version of its own, in a dataset named after the recordings with dev-sample and a short code "
                "added. A dataset is a registered, owned collection of data. Devi opens the sample from the list of Devi&rsquo;s own samples. The "
                "page counts 3 recordings about 3 people and says that the sample is for the person it was drawn for and for nobody else. Lower "
                "down, the section Where this came from names the platform&rsquo;s own step that made the sample, and links to the recordings it "
                "was made from.",
        "note": "Under <strong>Locked files</strong> the counts read <strong>Files 3</strong> and <strong>People 3</strong>. Under "
                "<strong>Development sample</strong> a green box reads <strong>This is a development sample of 3 files, drawn for you.</strong>, "
                "then the date it ends, and says that it names the same locked files as <strong>the files it was drawn from</strong> and "
                "copies none of them. Under <strong>Where this came from</strong>, <strong>Step</strong> reads <strong>development_sample</strong>, "
                "<strong>Code version</strong> reads <strong>sample:v1</strong> and <strong>Environment</strong> reads <strong>platform</strong>.",
    },
    {
        "file": "06-on-the-real-recordings-it-is-refused.png", "actor": "devi",
        "title": "On the real recordings the same pipeline version is refused",
        "screen": VERSION,
        "text": "Devi opens the real recordings again, chooses the pipeline version and presses Start. The platform refuses before it starts "
                "anything. A pipeline version in development may run only on development data, and the real recordings are not development data "
                "for anybody. Nothing is left behind: no run, no file and no record of an attempt to read.",
        "note": "A red box reads <strong>Nothing was started. Fix these first:</strong> and says that <strong>this version is in development, so it "
                "can be run only on development data</strong>: files declared synthetic, or a development sample drawn for you. It names the "
                "custom step <strong>silence</strong>, says that on any other data an approver of the department that owns the pipeline has to "
                "check the version and move it to production first, and ends <strong>Ask for a sample on the version&rsquo;s page, under Development samples.</strong>",
    },
    {
        "file": "07-on-the-sample-it-runs.png", "actor": "devi",
        "title": "On the sample the pipeline version runs for real",
        "screen": RUN,
        "text": "Devi opens the sample, chooses the same pipeline version and presses Start, and this time it runs. The script runs in a container, "
                "which is an isolated environment. This one has no internet and holds no key and no storage address. It asks the platform for its "
                "recordings and is told of exactly the 3 in the sample. The platform finds the stored files that the sample names, unlocks each "
                "with its own patient&rsquo;s key inside the platform, and hands the sound over. What the step writes back becomes a new version "
                "that waits for a person&rsquo;s review.",
        "note": "The heading reads <strong>Running</strong>, the name of the pipeline, <strong>against</strong>, and the name of the recordings "
                "followed by <strong>dev-sample</strong> and a short code. The page says <strong>Finished</strong>. Under <strong>Steps</strong>, "
                "<strong>silence</strong> reads <strong>Done</strong> and has a link <strong>Open the version it made</strong>, and "
                "<strong>decide</strong> also reads <strong>Done</strong>.",
    },
    {
        "file": "08-devi-sees-the-files-the-step-made.png", "actor": "devi",
        "title": "Devi sees the files the step made",
        "screen": VERSION,
        "text": "Devi follows the link Open the version it made. A version is one sealed copy of a dataset, here the files the step wrote. The page "
                "has a section called Files. It lists the three recordings the step made silent, each marked Locked, which means the platform "
                "keeps the file scrambled with the key of the one patient it came from, and unscrambles it only inside the platform. Devi may "
                "open them because Devi started the run, and the run read a sample drawn for Devi. A line under the heading says when the files "
                "are deleted: after seven days at the latest, or sooner if the sample they came from ends.",
        "note": "Under <strong>Files</strong> the page reads <strong>A run you started made these from development data, so you may open them "
                "while that data is in force for you.</strong> and <strong>These files are deleted on</strong> a date three days ahead. Three rows "
                "end in <strong>silenced.wav</strong>, each with its size, the word <strong>Locked</strong> and a <strong>Play</strong> button. "
                "No patient name and no key appear anywhere.",
    },
    {
        "file": "09-devi-plays-one.png", "actor": "devi",
        "title": "Devi plays one of them",
        "screen": VERSION,
        "text": "Devi presses Play on the first file. A sound player appears in the page and loads the recording. The platform unscrambles the file "
                "as it sends it, so the browser receives ordinary sound and never the key or the place where the file is stored. Each opening is "
                "written down: who opened which file, why the platform allowed it, and how many bytes were sent.",
        "note": "Under the first row a sound player shows a pause button, the time 0:00 / 0:01 and a volume control. "
                "There is no download button.",
    },
    {
        "file": "10-hartley-cannot-open-them.png", "actor": "hartley",
        "title": "Hartley, who approved the sample, cannot open what Devi made",
        "screen": VERSION,
        "text": "Hartley is the data custodian, the approver for the Cardiology department, and approved the sample. Hartley opens the same page. "
                "The files are listed, so Hartley can see that they exist and how large they are, but there is no Play button. A sample is "
                "for the person it was drawn for, and what a run makes from it is that person&rsquo;s to open. Hartley could open real "
                "recordings only under a lease, which is a time-limited permission asked for on the page of the recordings and decided by a second "
                "person.",
        "note": "A yellow box reads <strong>You cannot open these files.</strong> followed by the reason. The three rows are listed with "
                "<strong>Locked</strong> and no button.",
    },
    {
        "file": "11-devi-ends-the-sample.png", "actor": "devi",
        "act": ("PART THREE", "The sample ends",
                "A sample ends when its days are over, or sooner when the person it was drawn for, or an approver, ends it. Nothing is left running "
                "on it afterwards."),
        "title": "Devi ends the sample when the work is done",
        "screen": VERSION,
        "text": "Devi presses End this sample on the page of the real recordings. The row changes to Ended, and the form for asking appears again "
                "with Draw a sample for me selected, because the earlier request is over and a new one may be made.",
        "note": "Under <strong>Your samples</strong> the row reads <strong>Ended</strong> and <strong>3 files, to build and test with</strong>, "
                "and has no buttons. The form above offers both kinds of sample again, with <strong>Draw a sample for me.</strong> selected.",
    },
    {
        "file": "12-an-ended-sample-cannot-be-used.png", "actor": "devi",
        "title": "A run on an ended sample is refused",
        "screen": VERSION,
        "text": "Devi opens the ended sample and tries to run the pipeline version on it. The platform refuses at the start, for the same reason as "
                "on the real recordings: the sample is no longer development data for Devi. A step that is already running when a sample ends is "
                "refused at its next request.",
        "note": "A red box reads <strong>Nothing was started. Fix these first:</strong> and gives the same reason as before, that the version is "
                "<strong>in development</strong> and can be run only on development data.",
    },
    {
        "file": "13-when-the-sample-ends-the-files-close.png", "actor": "devi",
        "title": "When the sample ends, what was made from it ends too",
        "screen": VERSION,
        "text": "Devi opens the version the step made once more. The page now says that the version was made for development and has ended. "
                "The platform deletes the files within a minute of the sample ending, so nothing made from a sample outlives it. The version "
                "itself stays on record, because a sealed version is never removed, and so does a note of how many files and bytes were freed "
                "and why. The real recordings the sample named are not touched: a sample holds only a short list.",
        "note": "Under <strong>Files</strong> a single line reads <strong>This version was made for development and has ended</strong>, with the "
                "time, <strong>Its files are deleted. The version itself stays on record.</strong> No rows and no Play button remain.",
    },
    {
        "file": "14-hartley-declares-recordings-synthetic.png", "actor": "hartley",
        "act": ("PART FOUR", "Recordings that hold no real person",
                "Some recordings hold no real person, for example sentences read aloud by volunteers from invented scripts. They need no sample."),
        "title": "Hartley declares a version of recordings synthetic",
        "screen": VERSION,
        "text": "Synthetic means made up, with no real person in it. An approver of the owning department may declare a version of recordings "
                "synthetic, and must give a reason. Hartley opens a version of recordings that volunteers read aloud and types why.",
        "note": "Under <strong>Development samples</strong> a white box reads <strong>If these files hold no real person, declare them "
                "synthetic. Then any pipeline version in development may run on them, and nobody needs a sample.</strong> The reason box reads "
                "<strong>Read aloud by volunteers from invented scripts, so no patient is in them</strong>, beside the button "
                "<strong>Declare these files synthetic</strong>.",
    },
    {
        "file": "15-the-declaration-is-recorded.png", "actor": "hartley",
        "title": "The declaration is recorded, with who made it and why",
        "screen": VERSION,
        "text": "After Hartley presses the button, the page says who made the declaration, when, and why. From then on a pipeline version in "
                "development may run on these recordings for anybody in the organisation, because nobody real is in them. A development sample "
                "holds real recordings, so it can never be declared synthetic.",
        "note": "A green box has three lines. <strong>Declared synthetic by Hartley on</strong> a date and time. <strong>Reason given: Read aloud by "
                "volunteers from invented scripts, so no patient is in them</strong>. <strong>No real person is in these files, so a pipeline "
                "version in development may run on them.</strong>",
    },
]

ACTORS = {
    "devi": ("Devi", "Data engineer in the Health organisation"),
    "hartley": ("Hartley", "Data custodian for the Cardiology department"),
}

PAGE = Page(
    slug="development-sample", org="health",
    title=series_entry("development-sample")["title"],
    eyebrow="Health organisation &middot; Cardiology",
    lede="A data engineer has written a pipeline version that is still in development, so it may run only on development data. The engineer asks for "
         "a sample of the locked recordings, a custodian who did not ask approves fewer, and the platform draws them without anybody choosing. The "
         "pipeline version then runs for real on the sample and is refused on the real recordings. The sample ends, and recordings that hold no real "
         "person are declared synthetic.",
    actors=ACTORS, steps=STEPS,
    words=["organisation", "department", "dataset", "custodian", "approver", "sealed", "version", "pipeline", "run", "custom_step", "stage",
           "development_sample", "locked_recording", "synthetic"],
    shots_dir=SHOTS_ROOT / "development-sample",
    capture_note="Captured live against the <code>health</code> organisation by <code>web/walkthroughs/development-sample.spec.ts</code> and built by "
                 "<code>docs/tools/build_development_sample_walkthrough.py</code>. The script really ran in the sandbox worker. The recordings are "
                 "noise and the chart numbers are invented. What the platform refuses and draws is proved separately by "
                 "<code>verify/v142_development_samples.py</code>, and the whole loop on a real step by <code>verify/v143_development_loop.py</code>.",
)

if __name__ == "__main__":
    missing = [s["file"] for s in STEPS if not (PAGE.shots_dir / s["file"]).exists()]
    if missing:
        raise SystemExit("No screenshot for: " + ", ".join(missing) + "\nRun the capture first: cd web && npx "
                         "playwright test --config=walkthroughs/playwright.config.ts development-sample")
    write(PAGE)
