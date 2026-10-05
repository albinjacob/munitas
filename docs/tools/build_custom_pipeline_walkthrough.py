r"""Build docs/public/walkthroughs/custom-pipeline-walkthrough.html from captured screenshots.

The screenshots come from web/walkthroughs/custom-pipeline.spec.ts, which drives the real
console against the live stack. Run that first, then this builder:

    cd web
    npx playwright test --config=walkthroughs/playwright.config.ts custom-pipeline
    ..\.venv\Scripts\python.exe ..\docs\tools\build_custom_pipeline_walkthrough.py

Covers a data engineer registering a pipeline of their own, running it on a dataset after the
owning department's custodian grants access, and the gate decision that every pipeline ends
in. The page, its look and the wording check come from walkthrough_kit.py.
"""
from __future__ import annotations

from walkthrough_kit import SHOTS_ROOT, Page, series_entry, write

STEPS = [
    {
        "file": "01-pipelines-list.png", "actor": "devi",
        "act": ("PART ONE", "Devi registers a pipeline of their own",
                "A pipeline is a series of steps that Munitas runs on a dataset version, each step after the "
                "ones it depends on. Munitas offers a starter pipeline, <em>De-identify these recordings</em>, "
                "but an organisation may need checks that no starter can know about. Devi, a data engineer in "
                "the Cardiology department, needs cardiology notes checked for the fields that Cardiology's "
                "quality reporting requires, so Devi registers a pipeline for that."),
        "title": "Devi opens the Pipelines page",
        "screen": "Pipelines &middot; /pipelines",
        "text": "The page lists the pipelines registered in the Health organisation. Each one has an owning "
                "department and a version number. Several rows come from earlier recordings of this "
                "walkthrough, because a sealed version can never be deleted.",
        "note": "The columns read <strong>Pipeline</strong>, <strong>Department</strong> and "
                "<strong>Version</strong>. Most rows read <strong>v1 sealed</strong>, and one reads <strong>no "
                "version registered yet</strong>. The button <strong>Register a pipeline</strong> is at the top "
                "right.",
    },
    {
        "file": "02-names-it-and-its-department.png", "actor": "devi",
        "title": "Devi names the pipeline and its department",
        "screen": "Pipelines &middot; Register a pipeline",
        "text": "Devi types a name and chooses <strong>Cardiology</strong> as the owner. A department is a team "
                "inside an organisation that owns some of its data. Ownership says which department is "
                "answerable for what the pipeline does. The name ends in a date and a number only so that this "
                "example can be recorded again.",
        "note": "<strong>Owned by</strong> reads <strong>Cardiology</strong>. The line under the heading reads "
                "<strong>Names an owner. No version exists yet, so nothing can run until a DAG config and its "
                "scripts are uploaded</strong>. A DAG is a set of steps in which each step may depend on "
                "earlier ones.",
    },
    {
        "file": "03-registered-no-version-yet.png", "actor": "devi",
        "title": "The pipeline exists, but nothing can run yet",
        "screen": "Pipelines &middot; the pipeline's own page",
        "text": "Registering created a name and an owner and nothing else. The page says so, and offers the "
                "upload of the first version below.",
        "note": "<strong>Owned by Cardiology. Registered by Devi</strong> sits under the name. The panel reads "
                "<strong>No versions</strong>, and the text under it asks for a config and its scripts.",
    },
    {
        "file": "04-uploads-the-dag-config-and-scripts.png", "actor": "devi",
        "title": "Devi chooses the two files of a version",
        "screen": "Pipelines &middot; Upload the next version",
        "text": "A version is two files. The first, <code>cardiac-note-qc.yaml</code>, names each step in order "
                "and says which steps each one depends on. The second is a zip of the scripts that those steps "
                "name. This pipeline has two steps: <code>check_notes</code>, Devi's own script, and "
                "<code>decide</code>, a gate that comes last.",
        "note": "<strong>Pipeline config (.yaml)</strong> shows <strong>cardiac-note-qc.yaml</strong>, "
                "<strong>Scripts (.zip)</strong> shows a file ending in <strong>-scripts.zip</strong>, and "
                "<strong>Upload version</strong> is now active.",
    },
    {
        "file": "05-version-1-is-sealed.png", "actor": "devi",
        "title": "Version 1 is finished",
        "screen": "Pipelines &middot; the pipeline's own page",
        "text": "Munitas checked that every step's dependencies and every script that a step names were "
                "present, and then sealed version 1, which means finished and closed for good. A sealed version "
                "is never edited: a change is registered as the next version.",
        "note": "<strong>v1</strong> and a short code identify the version. Under it, <strong>check_notes "
                "(script: steps/check_notes.py)</strong> and <strong>decide (gate) &larr; check_notes</strong> "
                "show the two steps and that <code>decide</code> depends on <code>check_notes</code>. The "
                "heading below reads <strong>Upload the next version</strong>, and says there is no editing in "
                "place.",
    },
    {
        "file": "06-registers-the-dataset.png", "actor": "devi",
        "act": ("PART TWO", "A dataset comes in, and Devi has to ask",
                "Registering a pipeline gives Devi no right to read any data with it. A cardiology note still "
                "has to be registered and sealed as a dataset, and a request to read it goes to the custodian "
                "of the department that owns it. A custodian is the person a department trusts to decide who "
                "may read its data."),
        "title": "Devi registers a dataset for a cardiology note",
        "screen": "Bring a dataset in &middot; /datasets/register",
        "text": "A dataset is a named collection of records, and Munitas decides who may read it. Devi names "
                "it, gives <strong>Cardiology</strong> as the owner, says it is text, and leaves the access "
                "level at its default. The access level says how restricted a dataset is, and "
                "<strong>Raw</strong> is the most restricted level.",
        "note": "<strong>Owned by</strong> reads <strong>Cardiology</strong>, the origin reads "
                "<strong>Collected here, regulated</strong>, the access level reads <strong>Raw (the safe "
                "default)</strong>, and <strong>text</strong> is ticked. The small text beside "
                "<strong>Register</strong> says that nothing is readable yet.",
    },
    {
        "file": "07-uploads-a-file-and-seals-version-1.png", "actor": "devi",
        "title": "Devi uploads a note and seals version 1",
        "screen": "Bring a dataset in &middot; upload and seal",
        "text": "Devi uploads <code>consult-note.txt</code>, a one-line cardiology note, and seals the "
                "version. After sealing, nothing can be added to it. A later upload becomes a new version.",
        "note": "<strong>Uploaded</strong> sits next to <strong>consult-note.txt</strong>, and a green "
                "panel reads <strong>Sealed as version 1, 1 file. Nothing about it can change from here; a "
                "later upload becomes a new version</strong>. Below it is a link, <strong>Run a pipeline "
                "against this version</strong>.",
    },
    {
        "file": "08-tries-to-run-and-has-to-ask-first.png", "actor": "devi",
        "title": "Devi opens the dataset and has to ask",
        "screen": "Version detail &middot; /versions/&lt;id&gt;",
        "text": "The version page offers a place to run a pipeline, and under <strong>Your access</strong> it "
                "explains that the data is more sensitive than Devi's own role may read. A request is a "
                "written ask to read one dataset for one stated purpose. Devi fills in the purpose, the "
                "reason that something less sensitive would not do, and how long access is needed.",
        "note": "<strong>Your access</strong> reads <strong>This is more sensitive than your role reads, so "
                "Hartley decides</strong>. The purpose reads <strong>run cardiac-note-qc against this version "
                "to check note quality before wider release</strong>, and the button reads <strong>Ask for "
                "access</strong>.",
    },
    {
        "file": "09-the-request-goes-to-cardiologys-custodian.png", "actor": "devi",
        "title": "The request goes to the custodian of Cardiology",
        "screen": "Version detail &middot; after sending the request",
        "text": "Devi cannot approve the request, whatever the request says. The page reports that the request "
                "has gone to the person answerable for this data, and that nothing is readable until that "
                "person grants it.",
        "note": "<strong>Your access</strong> now reads <strong>Your request has gone to the person "
                "answerable for this data. Nothing is readable until they grant it</strong>, and the form has "
                "gone.",
    },
    {
        "file": "10-sees-devis-request-waiting.png", "actor": "hartley",
        "act": ("PART THREE", "The custodian of Cardiology decides",
                "Hartley is the data custodian of the Cardiology department, the person answerable for who may "
                "read what that department owns, including a note that a Cardiology engineer registered."),
        "title": "Hartley signs in and finds Devi's request waiting",
        "screen": "Home &middot; the custodian's home page",
        "text": "Hartley's home page lists the requests to read Cardiology's data. The first card carries "
                "everything Devi wrote.",
        "note": "<strong>Waiting for your decision</strong> counts <strong>1</strong>. The card reads "
                "<strong>Devi (Data engineer) wants to read cardiology-intake-notes v1</strong> with the label "
                "<strong>Raw</strong>, Devi's reason, the purpose, and <strong>for 24 hours</strong>. Beside the "
                "two buttons, the text reads <strong>Granting gives Devi 24 hours, for this purpose only, and "
                "it ends by itself</strong>.",
    },
    {
        "file": "11-grants-access.png", "actor": "hartley",
        "title": "Hartley grants access",
        "screen": "Home &middot; after granting",
        "text": "Granting creates a lease, which is permission to read one dataset for one stated purpose and "
                "for a limited time. The request leaves the waiting list and moves into the history of "
                "decisions.",
        "note": "A notice at the bottom right reads <strong>Access granted to Devi (Data engineer)</strong>. "
                "<strong>Waiting for your decision</strong> counts <strong>0</strong> and <strong>Currently "
                "granted</strong> counts <strong>1</strong>. The top decision reads <strong>Granted</strong>, "
                "with a time until which it stays open and a <strong>Revoke</strong> link.",
    },
    {
        "file": "12-access-shows-up-on-the-version-page.png", "actor": "devi",
        "act": ("PART FOUR", "Devi runs the pipeline",
                "With the lease in place, the version page tells Devi what the access covers, and Devi can "
                "choose Devi's own pipeline to run."),
        "title": "The page now shows the access that Devi has",
        "screen": "Version detail &middot; /versions/&lt;id&gt;",
        "text": "In place of the request form, <strong>Your access</strong> states what the lease covers and "
                "until when.",
        "note": "<strong>Your access</strong> reads <strong>You can read this until</strong> a date "
                "<strong>for run cardiac-note-qc against this version to check note quality before wider "
                "release</strong>. The request form has gone.",
    },
    {
        "file": "13-picks-her-own-pipeline-from-the-run-picker.png", "actor": "devi",
        "title": "Devi chooses the new pipeline from the list",
        "screen": "Version detail &middot; Run a pipeline",
        "text": "The <strong>Pipeline</strong> list used to offer only the starter pipeline. It now also "
                "offers the pipeline that Devi registered in Part One.",
        "note": "The list reads <strong>cardiac-note-qc</strong> followed by the date and number, and "
                "<strong>(v1)</strong>, which is the version sealed in step 5. The <strong>Start</strong> "
                "button is next to it.",
    },
    {
        "file": "14-starts-the-run.png", "actor": "devi",
        "title": "Devi starts the run",
        "screen": "Pipeline run &middot; /pipeline-runs/&lt;id&gt;",
        "text": "A run is one execution of a pipeline. The run page says what is running, who started it, and "
                "what has and has not happened. Nothing is decided or released at this point.",
        "note": "The heading reads <strong>Running cardiac-note-qc</strong> against the dataset, and the line "
                "under it reads <strong>Running now. This page updates itself</strong>. <strong>Started "
                "by</strong> reads <strong>Devi</strong>. <strong>What happens next</strong> reads "
                "<strong>Nothing has been decided, and nothing has been released</strong>.",
    },
    {
        "file": "15-both-steps-finish.png", "actor": "devi",
        "title": "Both steps finish, and the run points to a decision",
        "screen": "Pipeline run &middot; /pipeline-runs/&lt;id&gt;",
        "text": "The list of steps is read from what the run reported, so the console shows the two steps "
                "of Devi's pipeline and not a fixed list. Under it, the page says that a reviewer has to "
                "decide whether the note may be released, and that the reviewer cannot be whoever started the "
                "run.",
        "note": "<strong>Steps</strong> lists <strong>check_notes</strong> and <strong>decide</strong>, each "
                "marked <strong>Done</strong>. The line under the heading reads <strong>Finished. Nothing has "
                "been released yet: a reviewer decides that</strong>. The measurement reads <strong>pass: "
                "cardiac note fields present and well-formed</strong>, and a link reads <strong>Open the "
                "decision</strong>.",
    },
    {
        "file": "16-opens-the-gate-decision-and-is-blocked.png", "actor": "devi",
        "act": ("PART FIVE", "Every pipeline ends with a person's decision",
                "The last step of a pipeline is a gate decision: a person decides whether a dataset version "
                "may be released to a more open access level. It works the same way for a pipeline that Devi "
                "wrote as for the starter pipeline, and only a de-identification reviewer may clear it. A "
                "de-identification reviewer is a role that allows a person to review the result of a "
                "pipeline and release it or hold it back."),
        "title": "Devi cannot approve the result of Devi's own run",
        "screen": "Gate decision &middot; /gates/&lt;id&gt;",
        "text": "The decision would move the note from <strong>Raw</strong> to <strong>Open for "
                "annotation</strong>, an access level above Under review where people who label data may work "
                "with it, and the page says this cannot be undone. Devi started the run, so Devi may not "
                "clear it.",
        "note": "The state reads <strong>Waiting for a decision</strong>. The text above the buttons reads "
                "<strong>You started this run, so somebody else has to clear it</strong>, and "
                "<strong>Release to open for annotation</strong> and <strong>Hold it back</strong> are both "
                "pale and cannot be pressed.",
    },
    {
        "file": "17-the-reviewer-writes-why.png", "actor": "imani",
        "title": "Imani, the reviewer, writes down why",
        "screen": "Gate decision &middot; /gates/&lt;id&gt;",
        "text": "Imani holds the de-identification reviewer role. A reason has to be written before either "
                "button can be pressed, so that whoever reads the record later has the reasoning and not only "
                "the outcome.",
        "note": "The panel at the top left reads <strong>Imani, De-identification reviewer</strong>. The "
                "reason reads <strong>Required registry fields are present and well-formed; safe to open for "
                "annotation</strong>, and both buttons are now active.",
    },
    {
        "file": "18-cleared-and-promoted.png", "actor": "imani",
        "title": "Imani releases the note",
        "screen": "Gate decision &middot; after releasing",
        "text": "The note moves from Raw to Open for annotation. The decision and its reason are now part of "
                "the record on this page.",
        "note": "The state reads <strong>Released</strong>. <strong>Your decision</strong> reads "
                "<strong>Released by Imani</strong> followed by the reason, and a notice at the bottom right "
                "reads <strong>Released to open for annotation</strong>.",
    },
]

ACTORS = {
    "devi": ("Devi", "Data engineer in the Cardiology department, builds and runs the pipeline"),
    "hartley": ("Hartley", "Data custodian for the Cardiology department, decides who may read its data"),
    "imani": ("Imani", "De-identification reviewer, the role that may clear a gate decision"),
}

CROPS = {
    "01-pipelines-list.png": (320, 0, 1600, 340),
    "02-names-it-and-its-department.png": (320, 0, 1600, 330),
    "03-registered-no-version-yet.png": (320, 0, 1600, 620),
    "04-uploads-the-dag-config-and-scripts.png": (320, 0, 1600, 620),
    "05-version-1-is-sealed.png": (320, 0, 1600, 560),
    "10-sees-devis-request-waiting.png": (320, 0, 1600, 720),
    "11-grants-access.png": (320, 0, 1600, 660),
    "14-starts-the-run.png": (320, 0, 1600, 380),
    "15-both-steps-finish.png": (320, 0, 1600, 570),
    "16-opens-the-gate-decision-and-is-blocked.png": (320, 0, 1600, 910),
    "17-the-reviewer-writes-why.png": (320, 0, 1600, 880),
    "18-cleared-and-promoted.png": (320, 0, 1600, 750),
}

PAGE = Page(
    slug="custom-pipeline", org="health",
    title=series_entry("custom-pipeline")["title"],
    eyebrow="Health organisation &middot; Pipelines",
    lede="A data engineer in the Cardiology department registers a pipeline of their own that checks cardiology "
         "notes, asks the department's custodian for access to a note, and runs the pipeline. It ends in the "
         "same gate decision as every other pipeline, which only a reviewer may clear.",
    actors=ACTORS, steps=STEPS, crops=CROPS,
    words=["organisation", "department", "dataset", "version", "sealed", "access_level", "custodian", "request",
           "purpose", "lease", "pipeline", "run", "gate", "deid_reviewer", "open_for_annotation"],
    shots_dir=SHOTS_ROOT / "custom-pipeline",
    capture_note="Captured live against the <code>health</code> organisation by "
                 "<code>web/walkthroughs/custom-pipeline.spec.ts</code> and built by "
                 "<code>docs/tools/build_custom_pipeline_walkthrough.py</code>.",
)

if __name__ == "__main__":
    missing = [s["file"] for s in STEPS if not (PAGE.shots_dir / s["file"]).exists()]
    if missing:
        raise SystemExit("No screenshot for: " + ", ".join(missing) + "\nRun the capture first: cd web && npx "
                         "playwright test --config=walkthroughs/playwright.config.ts custom-pipeline")
    write(PAGE)
