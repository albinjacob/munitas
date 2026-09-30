r"""Build docs/public/walkthroughs/healthcare-walkthrough.html from captured screenshots.

The screenshots come from web/walkthroughs/healthcare.spec.ts, which drives
the real console against the live stack, seeded by
scripts/seed/seed-health-example.py. Run both first:

    .venv\Scripts\python.exe scripts/seed/seed-health-example.py
    cd web
    npx playwright test --config=walkthroughs/playwright.config.ts healthcare
    ..\.venv\Scripts\python.exe ..\docs\tools\build_healthcare_walkthrough.py

Needs Pillow, listed in docs/tools/requirements.txt.

Covers the whole worked example: a recording coming in, a real
de-identification pipeline run (transcription, speaker separation, PHI
detection, redaction), a gate decision only the de-identification reviewer
may make, a researcher's access request, an AI agent asking for access the
same way a person does, and the platform administrator's own view of the
system's health.
"""
from __future__ import annotations

import base64
import io
from datetime import date
from pathlib import Path

from PIL import Image

DOCS = Path(__file__).resolve().parent.parent
SHOTS = DOCS.parent / "web" / "walkthroughs" / "shots" / "healthcare"
OUT = DOCS / "public" / "walkthroughs" / "healthcare-walkthrough.html"

QUALITY = 72

ACTORS = {
    "devi": ("Devi", "Data engineer, brings data in and runs the pipeline"),
    "hartley": ("Hartley", "Data custodian for Cardiology"),
    "sam": ("Sam", "Researcher, asks for access"),
    "imani": ("Imani", "De-identification reviewer"),
    "okonjo": ("Okonjo", "Data custodian for Radiology"),
    "priya": ("Priya", "Platform administrator"),
}

STEPS = [
    # ---- ACT ONE: Signing in -------------------------------------------
    {
        "file": "01-devi-signs-in.png",
        "actor": "devi",
        "act": (
            "ACT ONE",
            "Signing in",
            "Munitas shows nothing until somebody proves who they are. "
            "Every person at the health organisation shares one sign-in "
            "screen. Which person signs in decides which home page they "
            "land on and which patient data they are allowed to see.",
        ),
        "title": "Signs in as a data engineer",
        "screen": "Sign in &middot; /auth/login",
        "text": "Devi works at a hospital that uses Munitas. Her job title "
                "there is data engineer, and her job includes bringing "
                "recordings in and starting the pipeline that strips "
                "identifying detail out of them.",
        "note": "Devi's job title, data engineer, is fixed the moment she "
                "signs in. It is not something she chooses afterwards, and "
                "it decides which actions the platform will let her take "
                "for the rest of this story, including the one act it "
                "specifically will not let her take: deciding her own "
                "pipeline run in Act Three.",
    },
    {
        "file": "02-devi-home.png",
        "actor": "devi",
        "title": "Lands on her own home page",
        "screen": "Home &middot; /",
        "text": "This home page counts recent pipeline runs, what they "
                "produced, and what a reviewer held back, counted only for "
                "the health organisation.",
        "note": "Every figure on this page belongs to the health "
                "organisation alone. A different organisation's data "
                "engineer, signed in at the same moment, would see "
                "entirely different numbers on the same page.",
    },
    # ---- ACT TWO: Devi brings a recording in ---------------------------
    {
        "file": "03-register-blank.png",
        "actor": "devi",
        "act": (
            "ACT TWO",
            "Devi brings a recording in",
            "A cardiology consultation gets recorded, and that recording "
            "says the patient's name out loud, states their date of "
            "birth, and carries their voice: personally identifying "
            "information from the moment it exists. Bringing a recording "
            "like this into Munitas means creating a dataset, the "
            "platform's own record for a file, which controls who is "
            "allowed to read it. Creating a dataset takes three steps: "
            "register a name and an owning department for it, upload the "
            "file into it, and seal the finished version so nobody can "
            "change it afterward. Nothing about a file is trusted just "
            "because of its name: the platform reads what it is given.",
        ),
        "title": "Opens the registration form",
        "screen": "Bring a dataset in &middot; /datasets/register",
        "text": "A blank form, reached from the Datasets page. Nothing "
                "created here is readable by anyone yet. This step only "
                "creates the dataset's record and names who owns it.",
        "note": "No department is selected yet, and the sensitivity "
                "field, not visible until filled in, already defaults to "
                "the most restrictive setting the platform has. Nothing "
                "about this form assumes the data is safer than it might "
                "be.",
    },
    {
        "file": "04-register-filled.png",
        "actor": "devi",
        "title": "Names it and leaves sensitivity at the safe default",
        "screen": "Bring a dataset in &middot; /datasets/register",
        "text": "The dataset is named, and Cardiology is chosen as the "
                "department that will own it. Cardiology is the "
                "department Hartley is the data custodian for, which is "
                "why Hartley, not anybody else, will later be the person "
                "who decides who else may read this recording.",
        "note": "The sensitivity field reads Raw, the safe default. "
                "Choosing anything less restrictive here would be a "
                "claim, recorded under Devi's own name, that somebody "
                "else would then have to agree with before it took "
                "effect. Leaving it at Raw makes no claim at all.",
    },
    {
        "file": "05-fake-wav-rejected.png",
        "actor": "devi",
        "title": "Tries to upload a fake recording, and the platform catches it",
        "screen": "Bring a dataset in &middot; upload step",
        "text": "A file named probe.wav, but not actually built as a real "
                "audio file, is rejected outright: the platform states "
                "plainly that it could not be read as a real recording, "
                "because it does not start with the bytes every genuine "
                "WAV file starts with.",
        "note": "The rejection message names the exact technical reason "
                "(the file does not start with the bytes a real WAV file "
                "always starts with), not a vague \"upload failed\". The "
                "platform reads the actual contents of a file it is "
                "given, not just the name somebody typed for it.",
    },
    {
        "file": "06-sealed-plain-version.png",
        "actor": "devi",
        "title": "Uploads a genuine recording and seals version 1",
        "screen": "Version detail &middot; /versions/&lt;id&gt;",
        "text": "A real recording this time. Sealing closes the upload "
                "permanently: nothing more can be added to this version, "
                "and nothing already in it can be changed. A later fix "
                "becomes a new version, never an edit to this one.",
        "note": "This dataset was sealed with the ordinary Seal button, "
                "not the recordings-specific one. It exists only to show "
                "registration, upload and sealing working end to end. The "
                "next act registers a second, separate dataset the proper "
                "way, because the de-identification pipeline needs more "
                "than an ordinary sealed file to run against.",
    },
    # ---- ACT THREE: the pipeline runs, and a reviewer decides ----------
    {
        "file": "07-recording-and-answer-key-uploaded.png",
        "actor": "devi",
        "act": (
            "ACT THREE",
            "The de-identification pipeline runs, and a reviewer decides",
            "De-identification is the process of finding and removing "
            "personally identifying detail, such as names and dates of "
            "birth, from a recording. Munitas runs a real four-step "
            "pipeline to do it: transcribe the recording to text, work "
            "out who is speaking when, find the identifying detail, and "
            "remove it. The pipeline needs a dataset sealed as recordings, "
            "not an ordinary file, because that is the only way it knows "
            "each file's real duration and how to check its work. What "
            "the pipeline produces is never released automatically: it "
            "ends at a gate decision, a screen only one specific role, "
            "the de-identification reviewer, is allowed to decide.",
        ),
        "title": "Registers a second recording, this time for the pipeline",
        "screen": "Bring a dataset in &middot; upload step",
        "text": "A second, separate dataset, also owned by Cardiology. "
                "Alongside the recording itself, Devi uploads an answer "
                "key: a file naming every piece of identifying detail "
                "actually in the recording, which is what lets the "
                "pipeline's own work be checked afterward rather than "
                "taken on trust.",
        "note": "Two files are listed: the recording itself, and its "
                "answer key. The answer key's name has to match the "
                "recording's own name, which is how the platform knows "
                "which answer key belongs to which recording when more "
                "than one pair is uploaded.",
    },
    {
        "file": "08-sealed-as-recordings.png",
        "actor": "devi",
        "title": "Seals it as recordings, not as a plain version",
        "screen": "Version detail &middot; /versions/&lt;id&gt;",
        "text": "A different Seal button from Act Two's: this one reads "
                "each file as an actual recording rather than an opaque "
                "upload, recording its real duration and how many "
                "recordings this version actually holds.",
        "note": "This is the shape the de-identification pipeline can "
                "actually run against. A version sealed the ordinary way, "
                "like the one in Act Two, has no recognised recordings "
                "for the pipeline to read at all.",
    },
    {
        "file": "09-pipeline-started.png",
        "actor": "devi",
        "title": "Starts the de-identification pipeline",
        "screen": "Pipeline run &middot; /pipeline-runs/&lt;id&gt;",
        "text": "A real worker starts reading this version. Nothing is "
                "released to anybody until a reviewer decides that, "
                "separately, once the pipeline has finished.",
        "note": "None of the four steps (transcribe, find identifiers, "
                "hand off for a person to check, remove them) have "
                "started yet. This screen updates itself as the run "
                "actually progresses, rather than requiring a reload to "
                "find out what happened.",
    },
    {
        "file": "10-pipeline-finished.png",
        "actor": "devi",
        "title": "All four steps finish",
        "screen": "Pipeline run &middot; /pipeline-runs/&lt;id&gt;",
        "text": "The pipeline transcribed the recording, worked out who "
                "was speaking when, found the identifying detail in it "
                "(names, dates of birth, anything else that could name "
                "the patient), and removed it. Each step is a real, "
                "timed piece of work, not a simulated delay.",
        "note": "Every step's badge reads Done, and a summary states "
                "what the pipeline measured against the answer key Devi "
                "uploaded in step 7. A reviewer has not looked at this "
                "yet, and nothing has been released to anybody.",
    },
    {
        "file": "11-devi-blocked-from-own-decision.png",
        "actor": "devi",
        "title": "Opens the decision, and is refused before she can touch it",
        "screen": "De-identification results &middot; /gates/&lt;id&gt;",
        "text": "Devi started this pipeline run, and the platform states "
                "plainly that somebody else has to clear it. Releasing "
                "this result would let more people read it, and that "
                "cannot be undone, which is exactly why it is not the "
                "decision of whoever happened to start the run.",
        "note": "Both decision buttons are present but disabled, and the "
                "reason is stated in words next to them rather than left "
                "for Devi to guess: you started this run, so somebody "
                "else has to clear it.",
    },
    {
        "file": "12-hartley-blocked-too.png",
        "actor": "hartley",
        "title": "Is refused too, for a different reason",
        "screen": "De-identification results &middot; /gates/&lt;id&gt;",
        "text": "Hartley, the data custodian for Cardiology, the "
                "department that owns this recording, opens the same "
                "decision and is refused as well, but for a different "
                "reason: clearing a de-identification result is not part "
                "of what a data custodian's role covers.",
        "note": "Owning a department's data and reviewing what a pipeline "
                "did to that data are two different kinds of authority. "
                "Hartley holds the first. Deciding this screen needs the "
                "second, which belongs to one specific role: the "
                "de-identification reviewer.",
    },
    {
        "file": "13-imani-opens-the-decision.png",
        "actor": "imani",
        "title": "Signs in as the de-identification reviewer",
        "screen": "De-identification results &middot; /gates/&lt;id&gt;",
        "text": "Imani holds the one role this screen is actually built "
                "for: de-identification reviewer. Neither Devi's "
                "department nor Hartley's ownership of the data matters "
                "here. What matters is this specific role.",
        "note": "The reason field is empty and both decision buttons are "
                "still disabled, not because Imani lacks the authority "
                "this time, but because a decision needs a written reason "
                "before either button will accept a click.",
    },
    {
        "file": "14-reason-written-buttons-enabled.png",
        "actor": "imani",
        "title": "Writes why, and the buttons come alive",
        "screen": "De-identification results &middot; /gates/&lt;id&gt;",
        "text": "The reason is kept permanently with the decision. "
                "Whoever reads this later, an auditor or a colleague, "
                "will not have the recording or the pipeline's output in "
                "front of them, only Imani's own written reasoning.",
        "note": "Both decision buttons, Release and Hold it back, are now "
                "enabled. Nothing else changed on this screen except the "
                "reason field no longer being empty.",
    },
    {
        "file": "15-held-back.png",
        "actor": "imani",
        "title": "Holds it back",
        "screen": "De-identification results &middot; /gates/&lt;id&gt;",
        "text": "Recorded permanently, reason and all. The recording "
                "stays at its original, most restricted sensitivity, and "
                "nobody has to ask Imani again later why it was not "
                "released.",
        "note": "The state badge now reads Held back, naming Imani as the "
                "person who decided it. This is a real, permanent outcome "
                "for this run, not a placeholder waiting for a different "
                "decision later.",
    },
    # ---- ACT FOUR: a researcher asks, a custodian grants ---------------
    {
        "file": "16-sam-finds-a-request-form.png",
        "actor": "sam",
        "act": (
            "ACT FOUR",
            "A researcher asks, a custodian grants",
            "Sam wants to read the original recording Devi sealed in Act "
            "Three, still at its most restricted sensitivity. Sam's role, "
            "researcher, does not read that far by default, so the "
            "version's own page asks what the data would be used for "
            "before showing anything at all.",
        ),
        "title": "Opens the raw recording and finds a form, not a file",
        "screen": "Version detail &middot; /versions/&lt;id&gt;",
        "text": "The recording is more sensitive than Sam's role reads by "
                "default, so the platform asks what she needs it for "
                "before showing her anything about it.",
        "note": "There is no file, no player, no transcript on this "
                "screen. Only a form asking Sam to state her purpose. "
                "Nothing about the recording itself is visible until "
                "somebody with the authority to grant it decides she may "
                "read it.",
    },
    {
        "file": "17-sam-fills-in-the-ask.png",
        "actor": "sam",
        "title": "Fills it in and asks",
        "screen": "Version detail &middot; access request form",
        "text": "A stated purpose and a written justification: why this "
                "particular recording, not a less sensitive substitute. "
                "Both are shown to whoever decides the request, not kept "
                "private to Sam.",
        "note": "The justification names a specific reason this "
                "particular raw recording is needed rather than the "
                "already-available alternative: comparing detected "
                "identifiers against the original audio is not possible "
                "without the original audio.",
    },
    {
        "file": "18-request-sent.png",
        "actor": "sam",
        "title": "Request sent, waiting on the person answerable for this data",
        "screen": "Version detail &middot; /versions/&lt;id&gt;",
        "text": "Sam's request now exists as a permanent record. Nothing "
                "is readable yet: the request is waiting on Hartley, the "
                "data custodian for the department that owns this "
                "recording.",
        "note": "The confirmation names who the request is now waiting "
                "on: the person answerable for this data. Sam has no way "
                "to grant her own request, and no way to skip the wait.",
    },
    {
        "file": "19-hartley-sees-sams-request.png",
        "actor": "hartley",
        "title": "Sees Sam's request, alongside real history",
        "screen": "Home &middot; Hartley, data custodian for Cardiology",
        "text": "Hartley's queue is not only Sam's request: other, "
                "already-existing traffic sits beside it, because a real "
                "queue is never only the one request being looked at in a "
                "walkthrough.",
        "note": "Hartley is the data custodian for Cardiology, the "
                "department that owns the recording Sam is asking for. "
                "That department match is the only reason this request "
                "reaches Hartley at all. A request for a recording owned "
                "by Radiology would never appear on this screen.",
    },
    {
        "file": "20-queue-after-granting.png",
        "actor": "hartley",
        "title": "Grants it",
        "screen": "Home &middot; Hartley, data custodian for Cardiology",
        "text": "Sam's row disappears from the waiting queue the moment "
                "it is decided. The grant itself, and the reasoning "
                "behind it, are kept permanently even though the row is "
                "no longer waiting.",
        "note": "Compare this screen with the previous step: the row "
                "naming Sam's request for the recording is gone from what "
                "is waiting. Granting a request removes it from the "
                "queue; it does not merely mark it read.",
    },
    # ---- ACT FIVE: an AI agent asks for access the same way a person does
    {
        "file": "21-agent-before-upload.png",
        "actor": "devi",
        "act": (
            "ACT FIVE",
            "An AI agent asks for access the same way a person does",
            "radiology-intake-triage is an agent: a piece of registered, "
            "versioned code Munitas runs on somebody's behalf, rather "
            "than a person reading data directly. This particular agent "
            "ranks incoming radiology documents so the riskiest ones "
            "reach a human reviewer first. Registering an agent and "
            "deploying its code is not the same thing as being allowed "
            "to read any particular dataset. By default this agent may "
            "only read data that has already been made widely available, "
            "so reaching for anything more sensitive parks its run "
            "exactly the way Sam's own request paused in Act Four: "
            "behind a real request, to a real data custodian, in the "
            "agent's own name.",
        ),
        "title": "Opens the agent Munitas already knows about",
        "screen": "Agents &middot; /agents",
        "text": "radiology-intake-triage already exists on the platform: "
                "a name, an owning department, and a stated purpose, the "
                "same three facts recorded when Devi registered a "
                "dataset earlier, just for a different kind of resource.",
        "note": "The row names the department this agent belongs to: "
                "Radiology, not Cardiology. That is why Okonjo, the data "
                "custodian for Radiology, is the person who will decide "
                "this agent's access later in this act, not Hartley.",
    },
    {
        "file": "22-agent-empty-upload-form.png",
        "actor": "devi",
        "title": "Opens it and finds an empty upload form",
        "screen": "Agent detail &middot; /agents/&lt;id&gt;",
        "text": "Registering an agent's identity and giving it code to "
                "run are two separate steps, the same way registering a "
                "dataset and sealing it are two separate steps.",
        "note": "No versions are listed yet. This agent exists as a name "
                "and an owner only, with nothing runnable behind it until "
                "code is uploaded.",
    },
    {
        "file": "23-agent-upload-filled.png",
        "actor": "devi",
        "title": "Uploads a small real project",
        "screen": "Agent detail &middot; upload form",
        "text": "A small, real code project, packaged as a .zip file "
                "naming its own entry point. No AI model is declared for "
                "this particular agent, and one tool is declared: reading "
                "a dataset version.",
        "note": "The tools field states in advance what this agent's "
                "code is expected to call. It is a declaration, not by "
                "itself a security boundary: what actually limits the "
                "agent is that every read it attempts is still checked "
                "against the same access rules a person's would be.",
    },
    {
        "file": "24-agent-version-sealed.png",
        "actor": "devi",
        "title": "Version 1 seals, sandboxed",
        "screen": "Agent detail &middot; /agents/&lt;id&gt;",
        "text": "Identified by a hash of its own code, not by a "
                "description somebody typed. Sandboxed means the "
                "platform runs this code inside an isolated container "
                "with no network access beyond its own credential "
                "requests.",
        "note": "The newest version, listed at the top, is the one just "
                "uploaded. Clicking its Deploy button, an action this "
                "walkthrough does not capture as its own screenshot, "
                "makes that version the one that will actually run next.",
    },
    {
        "file": "25-run-warned.png",
        "actor": "devi",
        "title": "Starts a run against a dataset the agent cannot read",
        "screen": "Agent detail &middot; Runs section",
        "text": '<code>This agent cannot read that dataset on its own '
                "(Raw). Starting will ask Okonjo, who looks after "
                "Radiology, to grant it access, in your name. The run "
                "waits until they decide.</code> Stated before the click "
                "that would start the run, not discovered afterward.",
        "note": "The dataset chosen for this run, radiology-reports, "
                "belongs to Radiology and is still at its most "
                "restricted sensitivity. That is exactly why this "
                "warning appears now, naming Okonjo specifically rather "
                "than describing the rule in the abstract.",
    },
    {
        "file": "26-run-parked-waiting.png",
        "actor": "devi",
        "title": "The run parks, waiting",
        "screen": "Agent detail &middot; Runs section",
        "text": "A real run, already recorded, going nowhere until "
                "somebody with authority over Radiology's data decides. "
                "This is not a failure: it is the same pause a person's "
                "own request would hit.",
        "note": "The status badge reads waiting for data access, not "
                "running and not failed. Nothing this agent's code does "
                "can move this run forward on its own.",
    },
    {
        "file": "27-okonjo-sees-the-request.png",
        "actor": "okonjo",
        "title": "Sees the request, named honestly",
        "screen": "Home &middot; Okonjo, data custodian for Radiology",
        "text": "The request names both who asked and what will "
                "actually do the reading: Devi asked, on the agent's "
                "behalf, for the agent's own runtime identity to read "
                "radiology-reports. Neither name is hidden behind the "
                "other.",
        "note": "Okonjo is the data custodian for Radiology, the "
                "department that owns the dataset the agent is asking to "
                "read. Neither Sam's earlier request nor anything from "
                "Cardiology appears on this screen: this queue only ever "
                "shows requests for data Radiology owns.",
    },
    {
        "file": "28-second-run-needs-second-grant.png",
        "actor": "devi",
        "title": "A second run needs a second grant, because this data is raw",
        "screen": "Agent detail &middot; Runs section",
        "text": "The grant Okonjo gave covered one purpose, not a standing "
                "credential the agent keeps forever. Starting a second run "
                "against the same dataset, for a different reason, asks "
                "again and parks again, exactly like the first one did. "
                "That is not a rule this platform applies everywhere: it is "
                "what raw, unreviewed patient data always gets, because the "
                "database itself refuses anything looser against it, no "
                "matter how long this agent has been running or how much "
                "Okonjo has come to trust it. The finance organisation's own "
                "walkthrough shows the other half of this: a custodian "
                "choosing to trust a workload with any purpose, for data "
                "that has already been reviewed and is no longer raw.",
        "note": "A fresh row appears in the Runs list, also waiting for "
                "data access, entirely separate from the first run that "
                "already has its own grant. Access here is scoped to the "
                "purpose that needed it, not handed to the agent "
                "permanently -- and radiology-reports never offers the "
                "custodian any other option, because raw data does not "
                "get one.",
    },
    {
        "file": "29-run-finished.png",
        "actor": "devi",
        "title": "The run finishes, for real",
        "screen": "Agent detail &middot; Runs section",
        "text": "Granted, resumed on its own, and completed: real output "
                "from a real sandboxed container, not text written for "
                "this walkthrough.",
        "note": "The status badge now reads succeeded, and the block "
                "beneath it is the exact result the agent's own code "
                "printed while it ran. Compare this with the warning in "
                "step 25: this is the same kind of run that was told it "
                "would have to wait, now finished.",
    },
    # ---- ACT SIX: beyond the built-in pipeline, and who is watching it all
    {
        "file": "30-pipeline-registry-empty.png",
        "actor": "devi",
        "act": (
            "ACT SIX",
            "Beyond the built-in pipeline, and who is watching it all",
            "The de-identification pipeline shown in Act Three is the "
            "platform's own built-in one. Anyone may register a "
            "different pipeline, a custom sequence of steps that still "
            "ends at a real gate decision the same way this one did. "
            "And behind every screen in this whole story is Priya, who "
            "can see whether the parts of the platform making all of it "
            "possible are actually healthy.",
        ),
        "title": "Opens the pipeline registry",
        "screen": "Pipelines &middot; /pipelines",
        "text": "Empty in this organisation so far. A custom pipeline is "
                "registered the same way an agent is: a sequence of "
                "steps, versioned and identified by its own content, not "
                "merely described.",
        "note": "No rows appear in this list. Nothing about that is a "
                "problem: the built-in de-identification pipeline used "
                "throughout this walkthrough is not a registered pipeline "
                "at all, it ships with the platform itself.",
    },
    {
        "file": "31-priya-sees-platform-health.png",
        "actor": "priya",
        "title": "Sees the platform's own health, not just the data in it",
        "screen": "Home &middot; Priya, platform administrator",
        "text": "Every component this whole story depended on: the "
                "workflow engine that ran the pipeline, the access rules "
                "engine every decision above went through, the sign-in "
                "service that proved who each person was, named plainly, "
                "with what breaks if each one goes down.",
        "note": "Priya is the platform administrator, the one role in "
                "this entire walkthrough that holds no standing access "
                "to any patient data at all. What she can see instead is "
                "the health of the system everybody else's work in this "
                "story depended on.",
    },
    {
        "file": "32-sam-finds-a-second-request-form.png",
        "actor": "sam",
        "act": (
            "ACT SEVEN",
            "The same decision, made the other way",
            "Everything above asked again per purpose, because everything "
            "above was raw, identifiable patient data: recordings, "
            "transcripts, radiology reports. Healthcare is a regulated "
            "industry, but so is finance, and neither fact by itself "
            "decides how a grant behaves -- what decides it is the data. "
            "Below the raw floor, the shape of a grant is the custodian's "
            "own call, the same choice finance's own walkthrough shows "
            "Marcus making for Omar's monthly digest. This dataset has "
            "already been reviewed and is no longer raw, so Hartley gets "
            "the same choice here.",
        ),
        "title": "Asks to read a quarterly digest that is no longer raw",
        "screen": "Version detail &middot; cardiology-outcomes-digest, under review",
        "text": "The same request form used throughout this walkthrough, "
                "on a dataset that has already been through review. Under "
                "review is not raw: it sits below the published floor, so "
                "reading it still needs a custodian's grant, but it is the "
                "one class where that custodian gets to decide how far to "
                "extend their trust.",
        "note": "Sam's own request looks identical to any other: a "
                "purpose, a justification, nothing about which shape of "
                "grant he is hoping for. That choice belongs to whoever "
                "approves it, not to whoever asks.",
    },
    {
        "file": "33-hartley-sees-sams-second-request.png",
        "actor": "hartley",
        "title": "Sees the request, and a choice raw data never offered",
        "screen": "Home &middot; Hartley, data custodian for Cardiology",
        "text": "Underneath the stated purpose sits a control that never "
                "appeared anywhere earlier in this walkthrough: "
                "<em>This purpose only</em> or <em>Any purpose, while this "
                "lasts</em>. It is absent entirely on a request against "
                "raw data -- the database refuses to create that "
                "combination -- so the choice only ever appears where it "
                "is actually safe to make.",
        "note": "Nothing forces Hartley to notice this control or to "
                "change it. Left alone, granting behaves exactly as every "
                "other grant in this walkthrough already has: scoped to "
                "the one purpose named today.",
    },
    {
        "file": "34-hartley-chooses-any-purpose.png",
        "actor": "hartley",
        "title": "Chooses to trust Sam with this dataset, not just with today's reason",
        "screen": "Home &middot; Hartley, data custodian for Cardiology",
        "text": "Hartley picks <em>Any purpose, while this lasts</em>, and "
                "the line beneath the buttons updates to say so before she "
                "commits to anything.",
        "note": "This is a judgment about Sam and this particular "
                "dataset, made once, by the person accountable for the "
                "data. It never becomes available for raw data no matter "
                "how much Hartley trusts Sam -- the choice this screen "
                "offers is bounded by the data, not by her opinion of him.",
    },
    {
        "file": "35-any-purpose-granted.png",
        "actor": "hartley",
        "title": "Grants it, and the request leaves the queue exactly as any other would",
        "screen": "Home &middot; Hartley, data custodian for Cardiology",
        "text": "The grant itself looks identical to every other approval "
                "in this walkthrough: the request disappears from the "
                "queue. What differs is invisible here, and shows up only "
                "in what Sam can do next.",
        "note": "A screenshot of a queue emptying cannot show a "
                "difference that has not happened yet. The next step is "
                "the proof.",
    },
    {
        "file": "36-any-purpose-lease-marked-in-history.png",
        "actor": "hartley",
        "title": "Sam reads for a reason nobody approved, and it works anyway",
        "screen": "Home &middot; Hartley, data custodian for Cardiology",
        "text": "Sam reads the digest again, this time for an unplanned "
                "spot-check he never mentioned when he first asked. Under "
                "a purpose-locked grant -- every grant earlier in this "
                "walkthrough -- this would be refused outright. Here it is "
                "allowed, because Hartley's grant was never scoped to the "
                "one reason Sam happened to type. Her own record of what "
                "she decided now carries a small label, <code>covers any "
                "purpose</code>, so the choice she made stays visible "
                "every time she looks back at it, not only at the moment "
                "she made it.",
        "note": "Compare this directly with Act Four: the same platform, "
                "the same kind of request, and two different outcomes for "
                "a purpose stated after the fact -- refused there, allowed "
                "here -- because the data and the custodian's own choice "
                "differed, not because healthcare and finance are held to "
                "different rules.",
    },
]

FOOTER_TEMPLATE = """
<footer>
  Captured live against the <code>health</code> organisation by
  <code>web/walkthroughs/healthcare.spec.ts</code> and rendered by
  <code>docs/tools/build_healthcare_walkthrough.py</code>. Re-run both when
  the flow changes. The pipeline run shown is real: transcription, speaker
  separation and identifier detection all ran against a real, synthetic
  recording built for this purpose, not a real patient.
  <p class="updated">Last updated __UPDATED__.</p>
</footer>

<div id="lightbox">
  <span class="close-hint">Click anywhere to close</span>
  <img id="lightbox-img" src="" alt="">
</div>

<script>
  (function () {
    var box = document.getElementById("lightbox");
    var boxImg = document.getElementById("lightbox-img");

    document.querySelectorAll(".shot img").forEach(function (img) {
      img.addEventListener("click", function () {
        boxImg.src = img.src;
        boxImg.alt = img.alt;
        box.classList.add("open");
      });
    });

    box.addEventListener("click", function () {
      box.classList.remove("open");
      boxImg.src = "";
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        box.classList.remove("open");
        boxImg.src = "";
      }
    });
  })();
</script>
"""


def footer() -> str:
    return FOOTER_TEMPLATE.replace("__UPDATED__", date.today().isoformat())


def encoded(path: Path) -> str:
    """The screenshot as an inline JPEG data URI."""
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        buffer = io.BytesIO()
        rgb.save(buffer, format="JPEG", quality=QUALITY, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def strip_tags(text: str) -> str:
    """The plain words of a step title, for the contents list."""
    out, depth = [], 0
    for char in text:
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
        elif depth == 0:
            out.append(char)
    return "".join(out)


def stylesheet() -> str:
    """The walkthrough styling, taken from the page that established it.

    Copied rather than shared as a file because a walkthrough has to open
    standalone from the filesystem. The base page already names its two
    actors devi and hartley, matching two of this flow's own people; the
    other four reuse the colour ramp the original hand-authored healthcare
    page established.
    """
    source = (DOCS / "public" / "walkthroughs" / "custom-pipeline-walkthrough.html").read_text(encoding="utf-8")
    style = source[source.index("<style>"): source.index("</style>") + len("</style>")]
    extra = """
<style>
  :root {
    --sam: #b5680c; --sam-bg: #fbeee0; --sam-line: #f0cfa4;
    --imani: #7b3fa0; --imani-bg: #f1e7f7; --imani-line: #ddc2ec;
    --okonjo: #b3315c; --okonjo-bg: #fbe7ee; --okonjo-line: #f0bfd1;
    --priya: #4a4640; --priya-bg: #ece9e3; --priya-line: #d3ccc0;
    --note: #9a6b00; --note-bg: #fdf3d9; --note-line: #f0dea3;
  }
  :root:not([data-theme="light"]) {
    @media (prefers-color-scheme: dark) {
      --sam: #e8ab5f; --sam-bg: #3a2a13; --sam-line: #5f4620;
      --imani: #cd9be6; --imani-bg: #33223f; --imani-line: #503762;
      --okonjo: #ea87a8; --okonjo-bg: #3a1f2a; --okonjo-line: #5c3346;
      --priya: #cfc8bb; --priya-bg: #2c2820; --priya-line: #453f34;
      --note: #e0b64c; --note-bg: #3a2f0c; --note-line: #5c4b16;
    }
  }
  :root[data-theme="dark"] {
    --sam: #e8ab5f; --sam-bg: #3a2a13; --sam-line: #5f4620;
    --imani: #cd9be6; --imani-bg: #33223f; --imani-line: #503762;
    --okonjo: #ea87a8; --okonjo-bg: #3a1f2a; --okonjo-line: #5c3346;
    --priya: #cfc8bb; --priya-bg: #2c2820; --priya-line: #453f34;
    --note: #e0b64c; --note-bg: #3a2f0c; --note-line: #5c4b16;
  }
  .cast .dot.sam { background: var(--sam); }
  .cast .dot.imani { background: var(--imani); }
  .cast .dot.okonjo { background: var(--okonjo); }
  .cast .dot.priya { background: var(--priya); }
  .actor-chip.sam { background: var(--sam-bg); border-color: var(--sam-line); color: var(--sam); }
  .actor-chip.sam .dot { background: var(--sam); }
  .actor-chip.imani { background: var(--imani-bg); border-color: var(--imani-line); color: var(--imani); }
  .actor-chip.imani .dot { background: var(--imani); }
  .actor-chip.okonjo { background: var(--okonjo-bg); border-color: var(--okonjo-line); color: var(--okonjo); }
  .actor-chip.okonjo .dot { background: var(--okonjo); }
  .actor-chip.priya { background: var(--priya-bg); border-color: var(--priya-line); color: var(--priya); }
  .actor-chip.priya .dot { background: var(--priya); }

  /*
    Which screen, and what to notice on it: standing requirement in
    CLAUDE.md's "UI flow walkthroughs" section, so a step never reduces to a
    picture with a one-line caption.
  */
  .step-screen {
    display: block; margin: 2px 0 8px; font-family: "SF Mono", ui-monospace,
    Consolas, monospace; font-size: 12px; color: var(--ink-faint);
  }
  .step-note {
    display: flex; gap: 8px; margin: 10px 0 14px; padding: 10px 12px;
    border-radius: 8px; border: 1px solid var(--note-line);
    background: var(--note-bg); font-size: 13.5px; color: var(--ink);
  }
  .step-note::before {
    content: "Look for"; flex: none; font-weight: 700; font-size: 11px;
    text-transform: uppercase; letter-spacing: 0.06em; color: var(--note);
    padding-top: 1.5px;
  }
</style>"""
    return style + extra


def hero() -> str:
    cards = "\n".join(
        f'    <div class="card">\n      <span class="dot {key}"></span>\n'
        f"      <div>\n"
        f'        <div class="name">{name}</div>\n'
        f'        <div class="role">{role}</div>\n'
        f"      </div>\n    </div>"
        for key, (name, role) in ACTORS.items()
    )
    return f"""
<header class="hero">
  <div class="eyebrow">Munitas &middot; Health organisation &middot; Patient recordings</div>
  <h1>A patient consultation, recorded, stripped of identifying detail, and put to work without ever exposing it</h1>
  <p class="lede">
    Munitas is a governance platform: it decides who may read which piece of
    data, and it keeps a permanent record of every time that access was
    granted or refused. This walkthrough follows one real example inside a
    hospital that uses Munitas. A cardiologist's consultation is recorded for
    research, and that recording says the patient's name out loud, states
    their date of birth, and carries their voice, personally identifying
    information from the moment it exists. A real pipeline transcribes the
    recording, finds that information, and removes it. A human reviewer then
    decides, in writing, whether the result is safe to release, and both a
    researcher and an AI agent have to ask the department's own data
    custodian before either of them can read anything more sensitive. Every
    screenshot below was captured from one real, unedited run of this exact
    example.
  </p>
  <div class="cast-title">Who is involved</div>
  <div class="cast">
{cards}
  </div>
</header>
"""


def build() -> str:
    parts: list[str] = [
        "<title>A patient consultation, de-identified: a walkthrough</title>",
        stylesheet(),
        hero(),
        '<div class="wrap">',
    ]

    contents = "\n".join(
        f'    <li><a href="#s{i}">{strip_tags(step["title"])}</a></li>'
        for i, step in enumerate(STEPS, start=1)
    )
    parts.append(
        '<nav class="toc">\n  <div class="toc-title">On this page</div>\n'
        f"  <ol>\n{contents}\n  </ol>\n</nav>"
    )

    open_act = False
    for i, step in enumerate(STEPS, start=1):
        if "act" in step:
            if open_act:
                parts.append("</section>")
            num, heading, sub = step["act"]
            parts.append(
                '<section class="act">\n  <div class="act-head">\n'
                f'    <span class="act-num">{num}</span>\n    <h2>{heading}</h2>\n'
                f'  </div>\n  <p class="act-sub">{sub}</p>'
            )
            open_act = True

        actor = step["actor"]
        name = ACTORS[actor][0]
        image = encoded(SHOTS / step["file"])
        parts.append(
            f'  <div class="step" id="s{i}">\n'
            f'    <div class="step-num">{i}</div>\n'
            f'    <div class="step-body">\n'
            f'      <span class="actor-chip {actor}"><span class="dot"></span>{name}</span>\n'
            f'      <div class="step-title">{step["title"]}</div>\n'
            f'      <span class="step-screen">{step["screen"]}</span>\n'
            f'      <p class="step-text">{step["text"]}</p>\n'
            f'      <div class="step-note">{step["note"]}</div>\n'
            f'      <div class="shot"><img src="{image}" '
            f'alt="Screenshot: {strip_tags(step["title"])}" loading="lazy"></div>\n'
            f"    </div>\n  </div>"
        )
    if open_act:
        parts.append("</section>")

    parts.append("</div>")
    parts.append(footer())
    return "\n".join(parts) + "\n"


if __name__ == "__main__":
    missing = [s["file"] for s in STEPS if not (SHOTS / s["file"]).exists()]
    if missing:
        raise SystemExit(
            "No screenshot for: "
            + ", ".join(missing)
            + "\nRun the capture first: cd web && npx playwright test "
              "--config=walkthroughs/playwright.config.ts healthcare"
        )
    OUT.write_text(build(), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {len(STEPS)} steps)")
