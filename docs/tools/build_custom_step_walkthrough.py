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
        "title": "The version is sealed, and it waits for an approver",
        "screen": PIPELINE,
        "text": "Uploading seals the version. Sealed means finished and closed for good, so that it can never be edited, only "
                "replaced by a next version. The page lists the two steps and then shows a yellow box. The box says that the first "
                "step is given the recordings, so code that the platform did not write reads the sound, and that it writes files, "
                "which become a version that waits for a person&rsquo;s review. A version like this cannot run until an approver "
                "of the owning department approves it, and the approver cannot be the person who uploaded it. An approver is a "
                "data custodian, a person who may decide who reads a department&rsquo;s data, who is listed for that department.",
        "note": "Under <strong>v1</strong> the line <strong>silence (script: steps/silence.py, given the recordings, writes "
                "files)</strong> and the line <strong>decide (gate) &larr; silence</strong> are listed. A gate is the last step of a "
                "pipeline, where a person decides whether the result may be released. A yellow box says the "
                "version <strong>cannot run until an approver of Cardiology, who is not Devi, approves it</strong>. There is no "
                "approve button on Devi&rsquo;s screen.",
    },
    {
        "file": "03-the-run-is-refused-until-it-is-approved.png", "actor": "devi",
        "title": "Devi tries to run it, and is refused",
        "screen": VERSION,
        "text": "Devi opens the version of the recordings that was sealed, which means finished and closed for good, and chooses the new pipeline in the list of pipelines that can be "
                "run on it. The platform checks before it starts anything, and refuses. It does not wait until the step reads a "
                "recording to find out, and nothing is left behind: no run, no file and no record of an attempt to read.",
        "note": "A red box reads <strong>Nothing was started. Fix these first:</strong> and says that the version lets the custom "
                "step <strong>silence</strong> read the recordings or write files and <strong>has not been approved</strong>, and "
                "that an approver of the department that owns the pipeline, who is not the person who uploaded it, approves it "
                "first.",
    },
    {
        "file": "04-hartley-is-offered-the-approval.png", "actor": "hartley",
        "act": ("PART TWO", "Hartley approves it",
                "The approval is a decision by the department that owns the pipeline, made by a person other than the one who "
                "uploaded the version. It is recorded with who gave it and when, and it can be taken back."),
        "title": "Hartley, an approver of Cardiology, is offered the approval",
        "screen": PIPELINE,
        "text": "Hartley is the data custodian for Cardiology. Hartley opens the same page and sees the same yellow box, and this "
                "time it has a button, because Hartley is an approver of the department that owns the pipeline and did not upload "
                "the version. Approving is the department&rsquo;s statement that the script has been looked at and may be given "
                "the recordings and may write files.",
        "note": "The screen names <strong>Hartley</strong> and <strong>Data custodian for Cardiology</strong>. The yellow box is "
                "the same as on Devi&rsquo;s screen, with one addition, a button <strong>Approve this version</strong>.",
    },
    {
        "file": "05-the-version-is-approved.png", "actor": "hartley",
        "title": "Hartley approves it, and can take the approval back at any time",
        "screen": PIPELINE,
        "text": "Hartley presses the button, and the approval is recorded. Taking it back stops a step that is already running at "
                "its next request, because the platform checks the approval again on every request that the step makes and not "
                "only when the run starts.",
        "note": "The yellow box now begins <strong>Approved by Hartley on</strong> followed by the date and time, and the button "
                "has become <strong>Take the approval back</strong>.",
    },
    {
        "file": "06-the-run-finishes-and-names-what-it-made.png", "actor": "devi",
        "act": ("PART THREE", "Devi runs it, and finds what it made",
                "With the approval in place the same run starts. This part shows what the run records and where the files "
                "that the step wrote end up."),
        "title": "Devi runs the pipeline, and the run names the version it made",
        "screen": "Pipeline run &middot; /pipeline-runs/&lt;id&gt;",
        "text": "Devi starts the same run again, and this time it works. The script runs in a container, which is an isolated "
                "environment. This one has no internet, runs as a user without special rights, and holds no key and no storage "
                "address. It can only ask the platform for the recordings it is allowed to read, and send back the files it "
                "makes, and the platform checks the approval on every request. When the script ends, the platform seals the "
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
                "fingerprint of the exact pipeline version that was approved. A fingerprint is a value worked out from the exact "
                "contents of a file, so it changes if the contents change. <strong>Made from</strong> links to the version of "
                "recordings that the step read, so erasing a patient later lists this version among what was made from their "
                "recordings.",
        "note": "<strong>Step</strong> reads <strong>custom_step</strong>, <strong>Run by</strong> reads <strong>health-pipeline</strong>, "
                "and <strong>Code version</strong> starts <strong>78e344ac5074f4</strong>, the same start as the fingerprint of the "
                "approved version in the second step. <strong>Made from</strong> is a link to the version of recordings.",
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
         "code the platform did not write, the platform refuses to run the pipeline until an approver of the owning department, "
         "who did not upload it, approves it. The run then reads the recordings only through the platform, and the files it "
         "writes become a new version that waits for a review and records exactly what made it.",
    actors=ACTORS, steps=STEPS,
    words=["organisation", "department", "dataset", "custodian", "approver", "sealed", "version", "pipeline", "run",
           "custom_step", "under_review", "fingerprint"],
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
