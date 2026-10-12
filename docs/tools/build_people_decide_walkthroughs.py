r"""Build the last two pages of "The people" in the Health story:

  docs/public/walkthroughs/de-identify-and-release-walkthrough.html   names are removed from the three recordings, and a reviewer decides
  docs/public/walkthroughs/evidence-without-names-walkthrough.html    the privacy auditor and the data protection officer read how it all was used

The screenshots come from web/walkthroughs/story-b.spec.ts (parts remove-names, after-release and evidence), which drives the real console against the
live Health organisation. The pipeline run is real work on the graphics card over the three locked recordings. Run the capture first
(scripts/walkthroughs/capture-health-story.py), then this builder:

    ..\.venv\Scripts\python.exe ..\docs\tools\build_people_decide_walkthroughs.py
"""
from __future__ import annotations

from walkthrough_kit import SHOTS_ROOT, Page, series_entry, write

DEID_STEPS = [
    {
        "file": "01-devi-chooses-to-remove-names.png", "actor": "devi",
        "act": ("PART ONE", "A run that removes names",
                "The three recordings that Devi brought in are locked, and every word spoken in them is personal data. The research programme "
                "needs a version with the names, dates and addresses taken out. A pipeline is a series of steps that Munitas runs on a dataset "
                "version, each step after the one before it, and the built-in one removes identifying details from recordings."),
        "title": "Devi starts the built-in pipeline on the locked recordings",
        "screen": "A dataset version &middot; /versions/&lt;id&gt;",
        "text": "On the version page, Devi chooses the pipeline that removes identifying details and presses Start. The pipeline opens each locked "
                "file through Munitas, which unlocks it with the key of the patient it is about and writes down every opening. Nobody is handed "
                "a key or a plain copy of a recording.",
        "note": "The panel <strong>Run a pipeline</strong> offers <strong>De-identify these files</strong> and a button <strong>Start</strong>, with the "
                "line <strong>Nothing is released until a reviewer decides that separately.</strong>",
    },
    {
        "file": "02-the-run-has-started.png", "actor": "devi",
        "title": "The run starts, and nothing has been decided",
        "screen": "A pipeline run &middot; /pipeline-runs/&lt;id&gt;",
        "text": "The page of the run lists its steps in order, each one waiting for the step before it. The first step turns the speech into text. "
                "The next finds the words that identify somebody. The third passes what was found to the last, which replaces those words in the text and covers them in the sound. "
                "Scoring happens afterwards, against the answer keys that Devi brought in.",
        "note": "The first step, <strong>Transcribe the files</strong>, and the steps after it read <strong>Not started</strong>. At the foot the page "
                "says <strong>Scoring happens after the last step. Nothing has been decided, and nothing has been released.</strong>",
    },
    {
        "file": "03-the-run-has-finished.png", "actor": "devi",
        "title": "The run finishes in a couple of minutes, and still nothing is released",
        "screen": "A pipeline run &middot; /pipeline-runs/&lt;id&gt;",
        "text": "The speech models run on a computer with a graphics card, which is why this takes minutes and not seconds. When the steps are done, "
                "the run has also been scored. The result still has to be decided by a person, and the page says who cannot decide it.",
        "note": "The page says <strong>Finished. Nothing has been released yet: a reviewer decides that.</strong> and, at the foot, <strong>Scoring is "
                "done. A reviewer has to decide whether this may be released, and it cannot be whoever started the run.</strong>",
    },
    {
        "file": "04-devi-cannot-decide-their-own-run.png", "actor": "devi",
        "title": "Devi cannot decide the result of the run Devi started",
        "screen": "De-identification decision &middot; /gates/&lt;id&gt;",
        "text": "The decision page shows what the run measured and what each step did. The person who started the run may read all of it, but the two "
                "buttons are greyed out for that person, and the page says why.",
        "note": "The result is called <strong>consultation-recordings-redacted</strong>, <strong>version 1</strong>, with the state <strong>Waiting for a "
                "decision</strong>. Under <strong>Your decision</strong> the page says <strong>You started this run, so somebody else has to decide it. "
                "It is waiting on Imani.</strong>",
    },
    {
        "file": "05-hartley-cannot-decide-either.png", "actor": "hartley",
        "title": "Hartley, who owns the recordings, cannot decide either",
        "screen": "De-identification decision &middot; /gates/&lt;id&gt;",
        "text": "Hartley is the custodian of the Cardiology department, which owns the recordings. Owning the data does not give the right to "
                "decide that its de-identified form may be released. That belongs to a role of its own, and Hartley does not hold it.",
        "note": "Under <strong>Your decision</strong> the page says <strong>Deciding a de-identification result is not part of your role. It is "
                "waiting on Imani.</strong>",
    },
    {
        "file": "06-imani-opens-the-decision.png", "actor": "imani",
        "act": ("PART TWO", "The reviewer chooses the standard, and decides",
                "A de-identification reviewer is a person who holds the role that decides whether a result with the names removed may be released. "
                "The role gives no right to read the recordings themselves. A de-identification standard is a named set of limits that the organisation's data "
                "protection officer wrote for one audience; the reviewer chooses the one for the audience the result is going to."),
        "title": "Imani sees what the run measured, kind of identifier by kind",
        "screen": "De-identification decision &middot; /gates/&lt;id&gt;",
        "text": "Imani is the de-identification reviewer. The page shows how much identifying detail the run removed and how much it left in, measured "
                "against the answer keys, and counts each kind of identifier separately, so a good overall figure cannot hide one kind that is "
                "being missed. Until Imani chooses a standard, the result is judged by the Platform default. Imani is told plainly what releasing does: "
                "it moves the result one step up, and it cannot be undone.",
        "note": "<strong>Identifying detail removed</strong> says <strong>100.0%</strong>, and <strong>Left in the clear</strong> and <strong>Naming a "
                "person directly</strong> both say <strong>0</strong>. Under <strong>De-identification standard</strong> the choice reads <strong>Platform "
                "default</strong>, and a green box says <strong>The measurement clears the Platform default standard.</strong> The table lists "
                "<strong>A person's name</strong>, <strong>A phone number</strong> and the other kinds with how many were left in the clear. The text "
                "above says <strong>Releasing moves it from under review to open for annotation, which cannot be undone.</strong>",
    },
    {
        "file": "07-imani-chooses-the-annotation-team-standard.png", "actor": "imani",
        "title": "Imani chooses the standard for the audience, and the platform checks the result against it",
        "screen": "De-identification decision &middot; /gates/&lt;id&gt;",
        "text": "The result is going to the annotation team, so Imani chooses the Annotation team standard that Nakamura wrote. The platform works the "
                "recommendation out from the counts the run kept, and works it out again when Imani decides, so nothing Imani types can change what "
                "the measurement says. The last column of the table now shows what this standard asks of each kind.",
        "note": "The choice reads <strong>Annotation team (version 1)</strong> and the box says <strong>The measurement clears the Annotation team "
                "standard.</strong> A line reads <strong>The Annotation team standard asks for at least 95% of identifying detail removed, over all kinds "
                "together.</strong> In the table, <strong>An address or a place</strong> asks for <strong>at least 90% removed</strong>.",
    },
    {
        "file": "08-the-public-release-standard-asks-more.png", "actor": "imani",
        "title": "Imani can see what a stricter standard would ask of the same result",
        "screen": "De-identification decision &middot; /gates/&lt;id&gt;",
        "text": "Choosing another standard changes nothing until Imani decides. Under Public release the same result is checked against stricter limits, "
                "and the table shows them. This result clears both, because the run removed everything the answer keys mark.",
        "note": "The choice reads <strong>Public release (version 1)</strong> and the box says <strong>The measurement clears the Public release "
                "standard.</strong> In the table, <strong>A date of birth</strong> and <strong>An address or a place</strong> both ask for <strong>at "
                "least 99% removed</strong>.",
    },
    {
        "file": "09-imani-writes-a-reason.png", "actor": "imani",
        "title": "Imani writes why, and both buttons become available",
        "screen": "De-identification decision &middot; /gates/&lt;id&gt;",
        "text": "Imani goes back to the Annotation team standard, because that is the audience this result is going to, and writes the reason. A decision "
                "needs a reason before the buttons work, because whoever reads the decision later will not have the result in front of them. Imani "
                "can release the result or hold it back, and the standard chosen is the one kept with the decision.",
        "note": "The box holds Imani's reason, <strong>The names, dates and addresses that the answer keys mark were all removed from the "
                "transcripts.</strong> Both <strong>Release to open for annotation</strong> and <strong>Hold it back</strong> are now ready.",
    },
    {
        "file": "10-released-and-judged-by-the-standard.png", "actor": "imani",
        "title": "The result is released, and it keeps the standard it was judged by",
        "screen": "De-identification decision &middot; /gates/&lt;id&gt;",
        "text": "Once the result is decided, the page no longer offers a choice. It says which standard the result was judged by, which version of it, "
                "and what that standard asked at the time. If the data protection officer changes the standard later, this decision still says what it said.",
        "note": "A green box says <strong>Judged by the Annotation team standard, version 1: it cleared the standard.</strong> A line reads <strong>The "
                "Annotation team standard asked for at least 95% of identifying detail removed, over all kinds together.</strong> A line that opens "
                "to show more reads <strong>All of the limits of the standard, as they were then</strong>.",
    },
    {
        "file": "11-the-decision-is-recorded.png", "actor": "imani",
        "title": "Imani's decision is recorded with who made it, when, and the reason",
        "screen": "De-identification decision &middot; /gates/&lt;id&gt;",
        "text": "The decision is recorded with who made it, when, and the reason. It also records what it rested on. The result is now open for "
                "annotation, and no data moved: the same files stay where they are and what may read them changed.",
        "note": "Under <strong>The decision</strong> the page says <strong>Released by Imani</strong> with the date and time, and <strong>Reason:</strong> "
                "followed by Imani's reason, which begins <strong>The names, dates and addresses that the answer keys mark were all removed from the transcripts.</strong>",
    },
    {
        "file": "12-sam-finds-the-released-result.png", "actor": "sam",
        "title": "A release widens access by one step, not to everybody",
        "screen": "Datasets &middot; /datasets",
        "text": "Sam, a researcher, looks for the result in the list of datasets. It is listed, and its widest access level is now open for annotation. "
                "Sam's role reaches only the Published level, so Sam still cannot read it. A release is one step at a time, and each step has its "
                "own decision.",
        "note": "The line for <strong>consultation-recordings-redacted</strong> says <strong>Open for annotation</strong> under <strong>Widest "
                "access</strong>, and under <strong>Versions</strong> it says <strong>0 of 1 you can read</strong>.",
    },
]

EVIDENCE_STEPS = [
    {
        "file": "01-marlow-reads-the-audit.png", "actor": "marlow",
        "act": ("PART ONE", "The auditor checks how locked data was used, and sees no name",
                "A privacy auditor is a person who checks how locked data was used. The role reads records of use and no personal data. Everything "
                "that happens to locked data leaves a record, and this page is where those records can be read together."),
        "title": "Marlow reads who read locked data, and who was refused",
        "screen": "Locked data audit &middot; /locking-audit",
        "text": "Marlow holds the privacy auditor role. The page lists every read of locked columns and every opening of locked files, every "
                "refusal, every permission with who asked and who decided, and what was done to each column. People appear by name. No name "
                "of a patient and no value from any table appears at all.",
        "note": "The page says <strong>No person's name and no value from any table appears here.</strong> Under <strong>Who read locked columns, and "
                "who was refused</strong>, <strong>Devi</strong> appears with <strong>Read</strong> and with <strong>Refused</strong> for "
                "<strong>clinic-appointments, version 1</strong>, and the reason for the read is Devi's own, <strong>to see what the postcode column "
                "really holds before I write the rule that locks it</strong>.",
    },
    {
        "file": "02-marlow-sees-how-columns-were-treated.png", "actor": "marlow",
        "title": "Marlow sees what was done to every column",
        "screen": "Locked data audit &middot; How each table's columns were treated",
        "text": "For each table sealed under a privacy contract, the page lists its columns, how identifying each is, and what was done to it, so an "
                "auditor can check that what the contract promised is what happened.",
        "note": "<strong>patient_name</strong> and <strong>reason_for_visit</strong> say <strong>Locked under the person's key</strong>, and "
                "<strong>patient_number</strong> says <strong>Replaced by a person code</strong>.",
    },
    {
        "file": "03-marlow-sees-the-keys.png", "actor": "marlow",
        "title": "Marlow sees every key that was opened, and why, and finds one person in two places",
        "screen": "Locked data audit &middot; Keys",
        "text": "Each patient has a key of their own. It was made when the patient first appeared, and it is opened when their locked values or "
                "files are read. The list shows each opening with the time, who did it, why, and a person code, which stands for one patient and "
                "means nothing on its own. The pipeline's openings of the recordings appear beside Devi's reading of the table. The same person "
                "code appears on lines about the table and on lines about a recording, which is how Munitas shows that the patient in the "
                "recording and the patient in the table are one person with one key. A script checked this.",
        "note": "Each line reads <strong>A key was opened</strong>, and under it gives the reason, such as <strong>To read locked rows, under a lease</strong> "
                "or <strong>To open a locked file in the page, with an approver's permission</strong>. Each line also shows who opened the key and a person "
                "code that begins with <strong>T-</strong>. The lines for the pipeline's run name the run, with a code that starts <strong>pipeline_run</strong> "
                "or <strong>action_run</strong>, and give the reason <strong>For a pipeline run</strong>.",
    },
    {
        "file": "04-nakamura-reads-the-same-audit.png", "actor": "nakamura",
        "title": "The data protection officer reads the same record",
        "screen": "Locked data audit &middot; /locking-audit",
        "text": "Nakamura is the data protection officer, a person who watches how the organisation handles personal data. The same page is open to "
                "that role, with the same limit: it shows how locked data was used and never what the locked data says. A script checked that "
                "none of the three patients' names appears anywhere on it.",
        "note": "The signed-in person at the foot of the menu is <strong>Nakamura</strong>, <strong>Data protection officer</strong>. The page is the "
                "same one, and its first lines are under <strong>Who read locked columns, and who was refused</strong>.",
    },
]

DEID_ACTORS = {
    "devi": ("Devi", "Data engineer in the Health organisation"),
    "hartley": ("Hartley", "Data custodian for the Cardiology department"),
    "imani": ("Imani", "De-identification reviewer in the Health organisation"),
    "sam": ("Sam", "Researcher in the Health organisation"),
}
EVIDENCE_ACTORS = {
    "marlow": ("Marlow", "Privacy auditor in the Health organisation"),
    "nakamura": ("Nakamura", "Data protection officer in the Health organisation"),
}

DEID_PAGE = Page(
    slug="de-identify-and-release", org="health",
    title=series_entry("de-identify-and-release")["title"],
    eyebrow="Health organisation &middot; The people",
    lede="The built-in pipeline removes names, dates and addresses from the three locked recordings, scored against the answer keys that came "
         "with them. The person who started the run cannot decide the result, and neither can the custodian of the department that owns the "
         "recordings. The de-identification reviewer chooses the standard for the audience, decides with a written reason, and a release "
         "widens access by one step.",
    actors=DEID_ACTORS, steps=DEID_STEPS,
    words=["organisation", "department", "dataset", "custodian", "pipeline", "run", "gate", "deid_reviewer", "under_review", "open_for_annotation",
           "answer_key", "locked", "release", "version", "access_level", "deidentification_standard"],
    shots_dir=SHOTS_ROOT / "remove-names-story",
    capture_note="Captured live against the <code>health</code> organisation by <code>web/walkthroughs/story-b.spec.ts</code>, with a real run on a "
                 "graphics card, and built by <code>docs/tools/build_people_decide_walkthroughs.py</code>.",
)
EVIDENCE_PAGE = Page(
    slug="evidence-without-names", org="health",
    title=series_entry("evidence-without-names")["title"],
    eyebrow="Health organisation &middot; The people",
    lede="The privacy auditor and the data protection officer read the record of everything that happened to locked data: "
         "who read, who was refused, what was done to each column, and which key was opened and why. They see people by name where a person "
         "acted, and no patient's name and no value from any table.",
    actors=EVIDENCE_ACTORS, steps=EVIDENCE_STEPS,
    words=["organisation", "dataset", "locked", "person_code", "auditor", "dpo", "contract", "version", "role"],
    shots_dir=SHOTS_ROOT / "evidence-without-names",
    capture_note="Captured live against the <code>health</code> organisation by <code>web/walkthroughs/story-b.spec.ts</code> and built by "
                 "<code>docs/tools/build_people_decide_walkthroughs.py</code>.",
)

if __name__ == "__main__":
    for page in (DEID_PAGE, EVIDENCE_PAGE):
        missing = [s["file"] for s in page.steps if not (page.shots_dir / s["file"]).exists()]
        if missing:
            raise SystemExit("No screenshot for: " + ", ".join(missing) + "\nRun the capture first: scripts/walkthroughs/capture-health-story.py --only b3")
        write(page)
