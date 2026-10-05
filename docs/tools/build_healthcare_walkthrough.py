r"""Build docs/public/walkthroughs/healthcare-walkthrough.html from captured screenshots.

The screenshots come from web/walkthroughs/healthcare.spec.ts, which drives the real console
against the live stack, seeded by scripts/seed/seed-health-example.py. Run both first:

    .venv\Scripts\python.exe scripts/seed/seed-health-example.py
    cd web
    npx playwright test --config=walkthroughs/playwright.config.ts healthcare
    ..\.venv\Scripts\python.exe ..\docs\tools\build_healthcare_walkthrough.py

The pipeline run in this capture is real: it transcribes a synthetic four-minute recording,
finds the identifying details in it and removes them, so the capture takes a few minutes.

The capture also records a custodian choosing what a grant covers (screenshots 32 to 36). That
story is told on the Finance page, where it began, so it is not repeated here. The page, its
look and the wording check come from walkthrough_kit.py.
"""
from __future__ import annotations

from walkthrough_kit import SHOTS_ROOT, Page, series_entry, write

STEPS = [
    {
        "file": "01-devi-signs-in.png", "actor": "devi",
        "act": ("PART ONE", "Devi signs in",
                "Devi is a data engineer at a hospital group that uses Munitas. Everybody in the organisation "
                "signs in on the same page. Who signs in decides what the console shows afterwards: a person sees "
                "only the data that the person's own organisation owns."),
        "title": "Devi signs in",
        "screen": "Sign in &middot; /auth/login",
        "text": "Devi types an email address and a password. Devi's job title, data engineer, is fixed by the "
                "organisation and is not chosen at sign-in. It decides what the platform lets Devi do, including "
                "one thing it will not allow in Part Three.",
        "note": "The email field reads <strong>devi@health.example</strong>, and the yellow bar reads "
                "<strong>Nobody is signed in</strong>, because the picture is taken before Devi presses "
                "<strong>Sign in</strong>.",
    },
    {
        "file": "02-devi-home.png", "actor": "devi",
        "title": "Devi's home page counts the organisation's data",
        "screen": "Home &middot; /",
        "text": "The home page counts four things for the Health organisation only: datasets, sealed versions, "
                "versions released for wider use, and versions held back. A dataset is a named collection of "
                "records. A version is one state of a dataset, and sealed means finished and closed for good.",
        "note": "The panel at the top left reads <strong>Devi, Data engineer, Organisation: health</strong>. The "
                "four tiles are <strong>Datasets</strong>, <strong>Sealed versions</strong>, <strong>Released "
                "for wider use</strong> and <strong>Held back</strong>.",
    },
    {
        "file": "03-register-blank.png", "actor": "devi",
        "act": ("PART TWO", "Devi brings a recording in",
                "A recording of a cardiology consultation says the patient's name aloud and carries the "
                "patient's voice, so it identifies a person from the moment it exists. Bringing it into Munitas "
                "takes three steps, always in the same order: register a name and an owner, upload the file, and "
                "seal it so that it can never be changed."),
        "title": "Devi opens the form for a new dataset",
        "screen": "Bring a dataset in &middot; /datasets/register",
        "text": "Registering a dataset names who owns it and where it came from. Nothing is readable yet. A "
                "department is a team inside an organisation that owns some of its data, and the form requires "
                "one.",
        "note": "<strong>Owned by</strong> reads <strong>Choose a department</strong>, and <strong>What access "
                "level?</strong> already reads <strong>Raw (the safe default)</strong>. The access level says "
                "how restricted a dataset is, and Raw is the most restricted. <strong>Register</strong> is pale "
                "until the form is complete.",
    },
    {
        "file": "04-register-filled.png", "actor": "devi",
        "title": "Devi names the dataset and chooses Cardiology as its owner",
        "screen": "Bring a dataset in &middot; /datasets/register",
        "text": "Devi types a name, chooses the Cardiology department, and ticks <strong>audio</strong>. "
                "Hartley is a custodian, a person who decides who may read a department's data, and an approver of "
                "Cardiology, the department that owns this recording. Any approver of a department can decide, and "
                "Hartley is the one here, so Hartley will decide who may read this recording.",
        "note": "<strong>Owned by</strong> reads <strong>Cardiology</strong>, and the access level still reads "
                "<strong>Raw (the safe default)</strong>. Choosing a lower level would be a claim recorded under "
                "Devi's name, which the custodian would have to agree with.",
    },
    {
        "file": "05-fake-wav-rejected.png", "actor": "devi",
        "title": "A file that is not a real recording is rejected",
        "screen": "Bring a dataset in &middot; upload step",
        "text": "Devi first tries a file named <code>probe.wav</code> that is not built as a real recording. "
                "Munitas reads what a file contains and does not trust what it is called.",
        "note": "A red line reads <strong>'probe.wav' is named as audio but could not be read as a wav file: "
                "file does not start with RIFF id</strong>. <strong>Seal this as version 1</strong> stays pale.",
    },
    {
        "file": "06-sealed-plain-version.png", "actor": "devi",
        "title": "A genuine recording uploads, and version 1 is sealed",
        "screen": "Bring a dataset in &middot; upload and seal",
        "text": "Devi uploads a real recording and seals it with the ordinary seal button. Sealing closes the "
                "version: nothing can be added to it, and nothing in it can change. A later fix becomes a new "
                "version. This dataset only shows the three steps. The next part registers a second one "
                "sealed in the way the pipeline needs.",
        "note": "<strong>Uploaded</strong> sits next to <strong>cardiology-consult-synthetic.wav</strong> with "
                "its size and length, and a green panel reads <strong>Sealed as version 1, 1 file. Nothing "
                "about it can change from here; a later upload becomes a new version</strong>.",
    },
    {
        "file": "07-recording-and-answer-key-uploaded.png", "actor": "devi",
        "act": ("PART THREE", "A pipeline removes the names, and a reviewer decides",
                "De-identification means finding and removing identifying details, such as names and dates of "
                "birth, from a recording. Munitas does it with a pipeline: a series of steps that it runs on a "
                "dataset version. Nothing the pipeline produces is released automatically. It ends at a "
                "decision that only one role, the de-identification reviewer, may make."),
        "title": "Devi uploads a second recording with its answer key",
        "screen": "Bring a dataset in &middot; upload step",
        "text": "A second dataset, also owned by Cardiology. Devi uploads the recording and an answer key, a "
                "file that lists every identifying detail in it. The answer key lets the pipeline's own work "
                "be checked afterwards and not taken on trust.",
        "note": "Two files are listed, <strong>cardiology-consult-synthetic.wav</strong> and "
                "<strong>cardiology-consult-synthetic.truth.json</strong>. Two seal buttons appear: "
                "<strong>Seal this as version 1</strong> and <strong>Seal as recordings</strong>.",
    },
    {
        "file": "08-sealed-as-recordings.png", "actor": "devi",
        "title": "Devi seals it as recordings",
        "screen": "Bring a dataset in &middot; upload and seal",
        "text": "<strong>Seal as recordings</strong> reads each audio file as a recording and matches it with its "
                "answer key. The de-identification pipeline can only run on a version sealed this way.",
        "note": "The green panel reads <strong>Sealed as version 1, 1 recording the de-identification pipeline "
                "can read</strong>.",
    },
    {
        "file": "09-pipeline-started.png", "actor": "devi",
        "title": "Devi starts the de-identification pipeline",
        "screen": "Pipeline run &middot; /pipeline-runs/&lt;id&gt;",
        "text": "A run is one execution of a pipeline. A worker starts reading the sealed version. The page "
                "updates itself as the steps progress.",
        "note": "The line under the heading reads <strong>Running now. This page updates itself</strong>. "
                "<strong>Steps</strong> lists <strong>Transcribe the recordings</strong>, <strong>Find the "
                "identifiers</strong>, <strong>Hand off for annotation</strong> and <strong>Remove the "
                "identifiers</strong>, each marked <strong>Not started</strong>. <strong>What happens "
                "next</strong> says that nothing has been decided or released.",
    },
    {
        "file": "10-pipeline-finished.png", "actor": "devi",
        "title": "All four steps finish",
        "screen": "Pipeline run &middot; /pipeline-runs/&lt;id&gt;",
        "text": "The pipeline turned the speech into text, found the identifying details, and removed them. "
                "Each step is real work with a real duration. The page then says that a reviewer must decide, "
                "and that the reviewer cannot be whoever started the run.",
        "note": "Each step is marked <strong>Done</strong> with its duration. The measurement reads "
                "<strong>pass: recall 1.000, 0 direct leaks, 0 quasi-identifier leaks</strong>, and a link reads "
                "<strong>Open the decision</strong>.",
    },
    {
        "file": "11-devi-blocked-from-own-decision.png", "actor": "devi",
        "title": "Devi cannot approve the result of Devi's own run",
        "screen": "De-identification results &middot; /gates/&lt;id&gt;",
        "text": "The last step of a pipeline is a gate decision: a person decides whether the result may be "
                "released to a more open access level. Here it would move the recording from Under review to "
                "Open for annotation, which cannot be undone. Devi started the run, so Devi may not decide it.",
        "note": "The state reads <strong>Waiting for a decision</strong>. The text above the buttons reads "
                "<strong>You started this run, so somebody else has to clear it</strong>, and both buttons are "
                "pale.",
    },
    {
        "file": "12-hartley-blocked-too.png", "actor": "hartley",
        "title": "Hartley is refused for a different reason",
        "screen": "De-identification results &middot; /gates/&lt;id&gt;",
        "text": "Hartley owns the recording's department, yet opens the same decision and is refused. Owning a "
                "department's data and reviewing what a pipeline did to it are two separate kinds of authority.",
        "note": "The panel reads <strong>Hartley, Data custodian for Cardiology</strong>, and the text above "
                "the buttons reads <strong>Clearing a de-identification result is not part of your "
                "role</strong>.",
    },
    {
        "file": "13-imani-opens-the-decision.png", "actor": "imani",
        "title": "Imani, the de-identification reviewer, opens the decision",
        "screen": "De-identification results &middot; /gates/&lt;id&gt;",
        "text": "Imani holds the one role this screen is built for, the de-identification reviewer. The buttons "
                "are still pale, not because of Imani's role but because a decision needs a written reason.",
        "note": "The panel reads <strong>Imani, De-identification reviewer</strong>. The <strong>Why you "
                "decided this</strong> box is empty.",
    },
    {
        "file": "14-reason-written-buttons-enabled.png", "actor": "imani",
        "title": "Imani writes a reason, and the buttons become active",
        "screen": "De-identification results &middot; /gates/&lt;id&gt;",
        "text": "The reason is kept with the decision. Whoever reads it later will not have the recording or the "
                "results in front of them, only Imani's own words.",
        "note": "The reason is filled in, and <strong>Release to open for annotation</strong> and <strong>Hold "
                "it back</strong> are both active.",
    },
    {
        "file": "15-held-back.png", "actor": "imani",
        "title": "Imani holds the recording back",
        "screen": "De-identification results &middot; /gates/&lt;id&gt;",
        "text": "The decision is recorded with its reason. The recording stays at its original, most restricted "
                "level, and nobody has to ask Imani later why it was not released.",
        "note": "The state reads <strong>Held back</strong>, <strong>Your decision</strong> reads <strong>Held "
                "back by Imani</strong> followed by the reason, and a notice at the bottom right reads "
                "<strong>Held back</strong>.",
    },
    {
        "file": "16-sam-finds-a-request-form.png", "actor": "sam",
        "act": ("PART FOUR", "A researcher asks, and a custodian decides",
                "Sam is a researcher who wants to read the original recording that Devi sealed. Sam's role does "
                "not read data at the Raw level, so the recording's page asks what the data would be used for "
                "before it shows anything more."),
        "title": "Sam opens the recording and finds a request form",
        "screen": "Version detail &middot; /versions/&lt;id&gt;",
        "text": "A request is a written ask to read one dataset for one stated purpose. The page offers a "
                "request form where a file would be, and says who decides.",
        "note": "<strong>Your access</strong> reads <strong>This is more sensitive than your role reads, so "
                "Hartley decides</strong>. The form asks for a purpose, a reason and a length of time.",
    },
    {
        "file": "17-sam-fills-in-the-ask.png", "actor": "sam",
        "title": "Sam writes the purpose and the reason",
        "screen": "Version detail &middot; the request form",
        "text": "Sam states the purpose and why a less sensitive substitute would not do. Both are shown to "
                "whoever decides, and neither is kept private.",
        "note": "The purpose reads <strong>measure recall of the de-identification model before wider "
                "release</strong>, and the reason reads <strong>for arrhythmia detection study, comparing "
                "detected spans against the original audio</strong>.",
    },
    {
        "file": "18-request-sent.png", "actor": "sam",
        "title": "The request goes to the custodian",
        "screen": "Version detail &middot; /versions/&lt;id&gt;",
        "text": "Sam cannot approve the request, and nothing is readable yet. The page says who it is waiting "
                "on.",
        "note": "<strong>Your access</strong> reads <strong>Your request has gone to the person answerable for "
                "this data. Nothing is readable until they grant it</strong>.",
    },
    {
        "file": "19-hartley-sees-sams-request.png", "actor": "hartley",
        "title": "Hartley finds Sam's request waiting",
        "screen": "Home &middot; the custodian's home page",
        "text": "Hartley's home page lists the requests to read Cardiology's data. A request for a recording that "
                "Radiology owns would never appear here. The decisions below the request come from earlier "
                "recordings of this walkthrough.",
        "note": "<strong>Waiting for your decision</strong> counts <strong>1</strong>. The card reads "
                "<strong>Sam (Researcher) wants to read</strong> the recording, with <strong>Raw</strong>, "
                "Sam's reason, and <strong>Granting gives Sam 24 hours, for this purpose only</strong>.",
    },
    {
        "file": "20-queue-after-granting.png", "actor": "hartley",
        "title": "Hartley grants the request",
        "screen": "Home &middot; after granting",
        "text": "Granting creates a lease, which is permission to read one dataset for one purpose and for a "
                "limited time. The request leaves the waiting list and moves into the history of decisions.",
        "note": "A notice at the bottom right reads <strong>Access granted to Sam (Researcher)</strong>, and "
                "<strong>Waiting for your decision</strong> counts <strong>0</strong>.",
    },
    {
        "file": "21-agent-before-upload.png", "actor": "devi",
        "act": ("PART FIVE", "A program asks for access, the way a person does",
                "An agent is a program registered on Munitas, with a named owner, that Munitas runs on "
                "somebody's behalf. Registering an agent gives it no right to read data. When it needs a Raw "
                "dataset, its run waits until the custodian of the department that owns the dataset decides."),
        "title": "Devi opens the radiology triage agent",
        "screen": "Agents &middot; /agents",
        "text": "<code>radiology-intake-triage</code> ranks radiology documents so that the most urgent ones "
                "reach a human reviewer first. It belongs to Radiology, so the Radiology custodian decides "
                "what it may read, not Hartley.",
        "note": "The row reads <strong>radiology-intake-triage</strong>, the purpose <strong>Ranks radiology "
                "intake documents for human review</strong>, the department <strong>Radiology</strong>, and "
                "the newest version followed by <strong>sealed</strong>.",
    },
    {
        "file": "22-agent-empty-upload-form.png", "actor": "devi",
        "title": "The agent's page shows its owner, its identity and its versions",
        "screen": "Agent detail &middot; /agents/&lt;id&gt;",
        "text": "An agent's code is kept as sealed versions, the same way a dataset is. When it runs, the agent "
                "acts under an identity of its own, shown here as its runtime identity.",
        "note": "<strong>Owned by</strong> reads <strong>Radiology</strong>, <strong>Runtime identity "
                "(principal)</strong> shows a name ending in <strong>-runtime</strong>, and the newest version "
                "carries the label <strong>active</strong>.",
    },
    {
        "file": "23-agent-upload-filled.png", "actor": "devi",
        "title": "Devi prepares a new version of the agent's code",
        "screen": "Agent detail &middot; the upload form",
        "text": "Devi chooses a zip of a small project. The model field says <code>none</code>, because the "
                "code calls no AI model, and the only tool it may use is <code>read_dataset_version</code>. The "
                "list of tools is a declaration. What limits the agent is that every read it makes is still "
                "checked against the same rules as a person's.",
        "note": "<strong>Project (.zip)</strong> shows <strong>triage-agent.zip</strong>, <strong>Model</strong> "
                "shows <strong>none</strong>, and <strong>Tools it may call</strong> shows "
                "<code>read_dataset_version</code>.",
    },
    {
        "file": "24-agent-version-sealed.png", "actor": "devi",
        "title": "The new version is sealed",
        "screen": "Agent detail &middot; Versions",
        "text": "The version is identified by a hash of its own code and is sealed on arrival. The platform "
                "runs it in a container, an isolated environment with no network access beyond its own "
                "requests for access. Older versions below come from earlier recordings.",
        "note": "The newest version is at the top with a <strong>Deploy</strong> button, and the version "
                "below it carries the label <strong>active</strong>.",
    },
    {
        "file": "25-run-warned.png", "actor": "devi",
        "title": "Devi starts a run and is told that it must wait",
        "screen": "Agent detail &middot; Runs",
        "text": "After deploying the new version, Devi names a purpose for a run and chooses "
                "<code>radiology-reports</code>, a Raw dataset that Radiology owns. Munitas warns before the "
                "click, not after.",
        "note": "A notice reads <strong>deployed</strong>. The orange line reads <strong>This agent cannot read "
                "that dataset on its own (RAW). Starting will ask Okonjo, who looks after Radiology, to grant "
                "it access, in your name. The run waits until they decide</strong>, and the button reads "
                "<strong>Request access and start</strong>.",
    },
    {
        "file": "26-run-parked-waiting.png", "actor": "devi",
        "title": "The run waits for permission",
        "screen": "Agent detail &middot; Runs",
        "text": "The run is recorded and goes nowhere until the custodian decides. That is not a failure. It is "
                "the same pause that a person's request would meet.",
        "note": "The top row carries the label <strong>waiting for data access</strong> and the line "
                "<strong>Waiting for a custodian to grant this agent access to the dataset. It starts on its "
                "own once they do</strong>.",
    },
    {
        "file": "27-okonjo-sees-the-request.png", "actor": "okonjo",
        "title": "Okonjo sees who asked and who will do the reading",
        "screen": "Home &middot; the custodian's home page",
        "text": "Okonjo is the custodian of the Radiology department. The request names both people involved: "
                "Devi asked, on behalf of the agent's own runtime identity, to read the Radiology dataset. "
                "Nothing from Cardiology appears in this list.",
        "note": "The card reads <strong>Devi asked for health-radiology-intake-triage-runtime to read "
                "radiology-reports v1</strong> with <strong>Raw</strong>, says that the request was made "
                "automatically so the agent can run, and says <strong>Granting gives</strong> the runtime "
                "identity <strong>4 hours, for this purpose only</strong>.",
    },
    {
        "file": "28-second-run-needs-second-grant.png", "actor": "devi",
        "title": "A second run has to ask again",
        "screen": "Agent detail &middot; Runs",
        "text": "Okonjo's grant covered one purpose and was not a standing credential. Devi starts a second run "
                "on the same dataset for another purpose, and it waits again. This is what Raw data always gets. "
                "Reviewed data, which is no longer Raw, can be granted more widely, as the Finance walkthrough "
                "shows.",
        "note": "A new row at the top reads <strong>waiting for data access</strong> and ends with "
                "<strong>second batch</strong>, separate from the first run below it.",
    },
    {
        "file": "29-run-finished.png", "actor": "devi",
        "title": "Once Okonjo agrees, the runs finish",
        "screen": "Agent detail &middot; Runs",
        "text": "Okonjo grants the second request, which this page does not show as its own screen, and the "
                "waiting run resumes by itself. Under each green label is the text that the agent's code "
                "printed. The sample agent prints one fixed result.",
        "note": "The top rows read <strong>succeeded</strong>, with <strong>0 tool calls</strong>, and show the "
                "agent's printed result, <code>contains an unusually urgent phrase, flagged for a human "
                "reviewer first</code>.",
    },
    {
        "file": "30-pipeline-registry-empty.png", "actor": "devi",
        "act": ("PART SIX", "Other pipelines, and the platform's own health",
                "The de-identification pipeline in Part Three is the one that Munitas offers. Anybody may "
                "register a different pipeline that ends in the same kind of decision, as the custom pipeline "
                "walkthrough shows. And behind every screen is Priya, who can see whether the parts of "
                "Munitas are healthy."),
        "title": "Devi opens the Pipelines page",
        "screen": "Pipelines &middot; /pipelines",
        "text": "The page lists pipelines that people in the organisation have registered, each with its owning "
                "department and version. A pipeline is registered the way an agent is: as sealed versions that "
                "are identified by their contents.",
        "note": "The columns read <strong>Pipeline</strong>, <strong>Department</strong> and "
                "<strong>Version</strong>. The built-in de-identification pipeline is not in this list, because "
                "it ships with Munitas.",
    },
    {
        "file": "31-priya-sees-platform-health.png", "actor": "priya",
        "title": "Priya sees the parts of the system",
        "screen": "Home &middot; the platform administrator's home page",
        "text": "Priya is the platform administrator, who runs Munitas itself and holds no standing access to "
                "any patient data. Priya's home page lists every part of the system that this story relied on, "
                "such as the control plane, the policy engine and the workflow engine, each with what stops "
                "working if it goes down.",
        "note": "Each card names a part, such as <strong>Control plane</strong> or <strong>Policy "
                "engine</strong>, says what it does, and ends with <strong>If it is down:</strong> and the "
                "effect.",
    },
]

ACTORS = {
    "devi": ("Devi", "Data engineer in the Health organisation"),
    "hartley": ("Hartley", "Data custodian for the Cardiology department"),
    "imani": ("Imani", "De-identification reviewer"),
    "sam": ("Sam", "Researcher"),
    "okonjo": ("Okonjo", "Data custodian for the Radiology department"),
    "priya": ("Priya", "Platform administrator, who runs Munitas itself"),
}

CROPS = {
    "01-devi-signs-in.png": (0, 0, 1920, 440),
    "02-devi-home.png": (320, 0, 1600, 380),
    "05-fake-wav-rejected.png": (320, 0, 1600, 1040),
    "09-pipeline-started.png": (320, 0, 1600, 600),
    "10-pipeline-finished.png": (320, 0, 1600, 640),
    "19-hartley-sees-sams-request.png": (320, 0, 1600, 720),
    "20-queue-after-granting.png": (320, 0, 1600, 660),
    "21-agent-before-upload.png": (320, 0, 1600, 320),
    "27-okonjo-sees-the-request.png": (320, 0, 1600, 720),
    "30-pipeline-registry-empty.png": (320, 0, 1600, 420),
}

PAGE = Page(
    slug="healthcare", org="health",
    title=series_entry("healthcare")["title"],
    eyebrow="Health organisation &middot; Patient recordings",
    lede="A recording of a cardiology consultation is brought in, and a pipeline removes the names and dates from "
         "it. A reviewer decides whether it may be released. A researcher then asks for access, and a program "
         "has to ask the same way a person does.",
    actors=ACTORS, steps=STEPS, crops=CROPS,
    words=["organisation", "department", "dataset", "version", "sealed", "access_level", "custodian", "pipeline",
           "run", "gate", "deid_reviewer", "request", "purpose", "lease", "agent", "container",
           "open_for_annotation"],
    shots_dir=SHOTS_ROOT / "healthcare",
    capture_note="Captured live against the <code>health</code> organisation by "
                 "<code>web/walkthroughs/healthcare.spec.ts</code> and built by "
                 "<code>docs/tools/build_healthcare_walkthrough.py</code>. The pipeline run is real: "
                 "transcription and identifier detection ran on a synthetic recording made for this purpose.",
)

if __name__ == "__main__":
    missing = [s["file"] for s in STEPS if not (PAGE.shots_dir / s["file"]).exists()]
    if missing:
        raise SystemExit("No screenshot for: " + ", ".join(missing) + "\nRun the capture first: cd web && npx "
                         "playwright test --config=walkthroughs/playwright.config.ts healthcare")
    write(PAGE)
