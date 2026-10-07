r"""Build docs/public/walkthroughs/custom-step-walkthrough.html from captured screenshots.

The screenshots come from web/walkthroughs/custom-step.spec.ts, which drives the real console against the live
stack, with the pipeline's own script running for real in the sandbox worker. Run that first, then this builder:

    cd web
    npx playwright test --config=walkthroughs/playwright.config.ts custom-step
    ..\.venv\Scripts\python.exe ..\docs\tools\build_custom_step_walkthrough.py
"""
from __future__ import annotations

from walkthrough_kit import SHOTS_ROOT, Page, series_entry, write

PIPELINE = "Pipeline detail &middot; /pipelines/&lt;id&gt;"
VERSION = "Version detail &middot; /versions/&lt;id&gt;"

STEPS = [
    {
        "file": "01-devi-uploads-the-pipeline-and-its-script.png", "actor": "devi",
        "act": ("PART ONE", "Devi uploads a pipeline with a step of their own, and is stopped",
                "A pipeline is a series of steps that Munitas runs on a version of a dataset. Most steps are provided by Munitas. "
                "A custom step is one whose script was written by the person who registered the pipeline. A step like that is "
                "code that the platform did not write, so when it asks to read recordings or to write files, a second person has "
                "to agree first. This part shows Devi uploading such a pipeline and the platform refusing to run it."),
        "title": "Devi uploads a pipeline whose step reads the recordings and writes files",
        "screen": PIPELINE,
        "text": "Devi is a data engineer in the Cardiology department of the Health organisation. A department is the team inside "
                "an organisation that owns some of its data. Devi has registered a pipeline called silence-the-start, and now "
                "uploads its first version, which is one sealed state of the pipeline that can never be edited. The version is made of "
                "a YAML file, which is a plain text file that names each step, and a zip file that holds the script of each step. The first step is a custom step. The YAML file says that the step is given the "
                "recordings and may write files, and its script makes the first half second of each recording silent and writes "
                "the result back.",
        "note": "Two files are chosen, <strong>pipeline.yaml</strong> and <strong>scripts.zip</strong>, and the button "
                "<strong>Upload version</strong> is blue. Above the form the page says <strong>Owned by Cardiology. Registered by "
                "Devi.</strong> and <strong>No versions.</strong>",
    },
    {
        "file": "02-the-version-waits-for-an-approver.png", "actor": "devi",
        "title": "The version is sealed in development, and it waits for an approver",
        "screen": PIPELINE,
        "text": "Uploading seals the version. Sealed means finished and closed for good, so that it can never be edited, only "
                "replaced by a next version. Every version of a pipeline has a stage, which is where it is in its life: in "
                "development, which means it cannot be run, in production, which means it may be run, or retired, which means it "
                "has ended for good. A version with a step like this one starts in development. The line under the pipeline&rsquo;s "
                "name explains the three stages. The page lists the two steps and then shows a yellow box. The box says that the "
                "first step reads the patient recordings and writes files, which become a new version that a reviewer checks before "
                "anyone can use it. The version leaves development only when an approver of the owning department moves it to "
                "production, and the approver cannot be the person who uploaded it. An approver is a data custodian, a person who may decide "
                "who reads a department&rsquo;s data, who is listed for that department.",
        "note": "Under the pipeline&rsquo;s name the page says <strong>A version in development cannot be run, a version in "
                "production can, and a retired version has ended for good.</strong> Beside <strong>v1</strong> a yellow label reads "
                "<strong>In development</strong>. Under it the line <strong>silence (script: steps/silence.py, reads the recordings, "
                "writes files)</strong> and the line <strong>decide (gate) &larr; silence</strong> are listed. A gate is the last "
                "step of a pipeline, where a person decides whether the result may be released. The yellow box ends "
                "<strong>This version is in development. An approver of Cardiology other than Devi checks it and moves it to "
                "production first.</strong> The only button on Devi&rsquo;s screen is <strong>Retire this version</strong>; there "
                "is no button to move it to production.",
    },
    {
        "file": "03-the-run-is-refused-in-development.png", "actor": "devi",
        "title": "Devi tries to run it, and is refused",
        "screen": VERSION,
        "text": "Devi opens the version of the recordings that was sealed, which means finished and closed for good, and chooses the new pipeline in the list of pipelines that can be "
                "run on it. The platform checks the stage of the pipeline version before it starts anything, and refuses because "
                "the version is in development. It does not wait until the step reads a recording to find out, and nothing is left "
                "behind: no run, no file and no record of an attempt to read.",
        "note": "A red box reads <strong>Nothing was started. Fix these first:</strong> and says that <strong>this version is in "
                "development, so it cannot be run</strong>, that its custom step <strong>silence</strong> reads the recordings or "
                "writes files, and that an approver of the department that owns the pipeline, other than the person who uploaded "
                "it, has to check it and move it to production first.",
    },
    {
        "file": "04-hartley-is-offered-the-move-to-production.png", "actor": "hartley",
        "act": ("PART TWO", "Hartley moves it to production",
                "Moving a version to production is a decision by the department that owns the pipeline, made by a person other "
                "than the one who uploaded the version. It is recorded with who made it and when, and it can be reversed."),
        "title": "Hartley, an approver of Cardiology, is offered the move to production",
        "screen": PIPELINE,
        "text": "Hartley is the data custodian for Cardiology. Hartley opens the same page and sees the same yellow box, and this "
                "time it has a button to move the version out of development, because Hartley is an approver of the department "
                "that owns the pipeline and did not upload the version. Moving it to production is the department&rsquo;s "
                "statement that the script has been looked at and may be given the recordings and may write files.",
        "note": "The screen names <strong>Hartley</strong> and <strong>Data custodian for Cardiology</strong>. The label beside "
                "<strong>v1</strong> still reads <strong>In development</strong>. The yellow box is the same as on Devi&rsquo;s "
                "screen, with one addition, a dark button <strong>Move to production</strong> beside <strong>Retire this "
                "version</strong>.",
    },
    {
        "file": "05-the-version-is-in-production.png", "actor": "hartley",
        "title": "Hartley moves it to production, and can return it to development at any time",
        "screen": PIPELINE,
        "text": "Hartley presses the button, and the move is recorded with Hartley&rsquo;s name and the time. Returning the version "
                "to development stops a step that is already running at its next request, because the platform checks the stage "
                "again on every request that the step makes and not only when the run starts.",
        "note": "The label beside <strong>v1</strong> now reads <strong>In production</strong> and the box is green. It begins "
                "<strong>Moved to production by Hartley on</strong> followed by the date and time. The buttons are "
                "<strong>Return to development</strong> and <strong>Retire this version</strong>.",
    },
    {
        "file": "06-the-run-finishes-and-names-what-it-made.png", "actor": "devi",
        "act": ("PART THREE", "Devi runs it, and finds what it made",
                "With the version in production the same run starts. This part shows what the run records and where the files "
                "that the step wrote end up."),
        "title": "Devi runs the pipeline, and the run names the version it made",
        "screen": "Pipeline run &middot; /pipeline-runs/&lt;id&gt;",
        "text": "Devi starts the same run again, and this time it works. The script runs in a container, which is an isolated "
                "environment. This one has no internet, runs as a user without special rights, and holds no key and no storage "
                "address. It can only ask the platform for the recordings it is allowed to read, and send back the files it "
                "makes, and the platform checks on every request that the version is still in production. When the script ends, the platform seals the "
                "files it finished, which closes them for good, as one new version, and the page of the run links to it.",
        "note": "Under <strong>Steps</strong> the step <strong>silence</strong> reads <strong>Done</strong> and has a link "
                "<strong>Open the version it made</strong>. The step <strong>decide</strong> is also <strong>Done</strong>.",
    },
    {
        "file": "07-the-new-version-waits-for-a-review.png", "actor": "devi",
        "title": "The new version is under review, so nobody can use it yet",
        "screen": VERSION,
        "text": "Devi follows the link. The files are in a new dataset, a named collection of data, whose name is the name of the "
                "first dataset followed by the name of the step. The version is marked <strong>Under review</strong>, the access "
                "level of something that no person has reviewed. Nobody may use it until a reviewer decides. The platform "
                "made this version itself from what the script sent, so the script was never given any way to write into "
                "storage. The page also says that the version is stored as a table of 2 rows, one for each file it holds.",
        "note": "The title ends with <strong>-silence v1</strong>. Both <strong>Access level when sealed</strong> and "
                "<strong>Access level now</strong> read <strong>Under review</strong>, and <strong>Records</strong> is "
                "<strong>2</strong>.",
    },
    {
        "file": "08-where-the-new-version-came-from.png", "actor": "devi",
        "title": "The version records exactly what made it, and what it was made from",
        "screen": VERSION,
        "text": "Further down the same page, <strong>Where this came from</strong> names what produced the version: the step, the "
                "identity that the platform runs pipelines as, the code version and the environment. The code version is a "
                "fingerprint of the exact pipeline version that was moved to production. A fingerprint is a value worked out from the exact "
                "contents of a file, so it changes if the contents change. <strong>Made from</strong> links to the version of "
                "recordings that the step read, so erasing a patient later lists this version among what was made from their "
                "recordings.",
        "note": "<strong>Step</strong> reads <strong>custom_step</strong>, <strong>Run by</strong> reads <strong>health-pipeline</strong>, "
                "and <strong>Code version</strong> starts <strong>78e344ac5074f4</strong>, the same start as the fingerprint of the "
                "version shown in the second step. <strong>Made from</strong> is a link to the version of recordings.",
    },
    {
        "file": "09-hartley-retires-the-version.png", "actor": "hartley",
        "act": ("PART FOUR", "Hartley retires the version",
                "A version is retired when something replaces it or when it should no longer run. Retiring is final: the version "
                "is never brought back, and a later version takes its place."),
        "title": "Hartley retires the version",
        "screen": PIPELINE,
        "text": "Hartley retires the version of the pipeline. Retiring ends the life of a version for good. Nothing can run it "
                "again, and a step that is running at the moment it is retired is refused at its next request, so the files that "
                "step was writing are discarded. An approver of the owning department may retire a version, and so may the person "
                "who uploaded it, because retiring only makes things stricter.",
        "note": "The label beside <strong>v1</strong> now reads <strong>Retired</strong>, in grey. The box under the steps reads "
                "<strong>Retired by Hartley on</strong> followed by the date and time, then <strong>This version can no longer be "
                "run. Run a later version.</strong> The page offers no buttons for this version.",
    },
    {
        "file": "10-a-retired-version-cannot-be-run.png", "actor": "devi",
        "title": "A run of the retired version is refused",
        "screen": VERSION,
        "text": "Devi opens the version of the recordings again and chooses the retired pipeline version in the list of pipelines "
                "that can be run on it. The platform refuses before it starts anything and says that the version was retired.",
        "note": "A red box reads <strong>Nothing was started. Fix these first:</strong> and says that <strong>this version of the "
                "pipeline was retired, so it cannot be run</strong>, and to run a later version.",
    },
]

ACTORS = {
    "devi": ("Devi", "Data engineer in the Health organisation"),
    "hartley": ("Hartley", "Data custodian and approver for the Cardiology department"),
}

PAGE = Page(
    slug="custom-step", org="health",
    title=series_entry("custom-step")["title"],
    eyebrow="Health organisation &middot; Cardiology",
    lede="A data engineer uploads a pipeline with a step of their own that reads recordings and writes files. Because that step is "
         "code the platform did not write, the version starts in development and the platform refuses to run it until an approver "
         "of the owning department, who did not upload it, moves it to production. The run then reads the recordings only through "
         "the platform, and the files it writes become a new version that waits for a review and records exactly what made it. "
         "Last, the version is retired and can no longer be run.",
    actors=ACTORS, steps=STEPS,
    words=["organisation", "department", "dataset", "custodian", "approver", "sealed", "version", "pipeline", "run",
           "custom_step", "stage", "under_review", "fingerprint"],
    shots_dir=SHOTS_ROOT / "custom-step",
    capture_note="Captured live against the <code>health</code> organisation by "
                 "<code>web/walkthroughs/custom-step.spec.ts</code> and built by "
                 "<code>docs/tools/build_custom_step_walkthrough.py</code>. The script really ran in the sandbox worker. The "
                 "recordings are noise and the chart numbers are invented. What the platform refuses is proved separately by "
                 "<code>verify/v140_custom_steps_and_recordings.py</code>, and what the step can and cannot do from inside its "
                 "container by <code>verify/v141_custom_step_given_locked_recordings.py</code>.",
)

if __name__ == "__main__":
    missing = [s["file"] for s in STEPS if not (PAGE.shots_dir / s["file"]).exists()]
    if missing:
        raise SystemExit("No screenshot for: " + ", ".join(missing) + "\nRun the capture first: cd web && npx "
                         "playwright test --config=walkthroughs/playwright.config.ts custom-step")
    write(PAGE)
