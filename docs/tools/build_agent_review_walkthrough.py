r"""Build docs/public/walkthroughs/agent-review-walkthrough.html from captured screenshots.

The screenshots come from web/walkthroughs/agent-review.spec.ts, which drives the real console against the live Health organisation as Devi (a data engineer),
Hartley (the custodian of Cardiology), Imani (the de-identification reviewer) and Nakamura (the data protection officer). Run that first, then this builder:

    cd web
    npx playwright test --config=walkthroughs/playwright.config.ts agent-review
    ..\.venv\Scripts\python.exe ..\docs\tools\build_agent_review_walkthrough.py

This is the fourth page of "AI agents" in the Health story. The agent is consultation-note-drafter, which really runs in the platform's sandbox on three made-up
consultation transcripts. Picture 17 of the capture, the table that maps the pack's records to two public frameworks, is not used: it prints the product's own
statements about what it does not provide, and those belong on the internal page.
"""
from __future__ import annotations

from walkthrough_kit import SHOTS_ROOT, Page, series_entry, write

STEPS = [
    {
        "file": "01-devi-starts-a-run.png", "actor": "devi",
        "act": ("PART ONE", "An agent does some work, and the work waits for somebody else to check it",
                "An AI agent is a program that works on data on somebody's behalf. The agent on this page, consultation-note-drafter, belongs to the Cardiology "
                "department. It reads transcripts of consultations and drafts a visit note from each, for a clinician to read, correct and sign. The "
                "transcripts here are made up, and the agent's code runs in a sandbox, which means an isolated container with no network access of its own."),
        "title": "Devi asks the agent to draft notes from three transcripts, and the page says who will be asked first",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the Runs section",
        "text": "A run is one execution of an agent, started for a stated purpose. Devi, a data engineer in the Health organisation, writes the purpose "
                "and chooses the dataset the agent will read: a dataset is a named collection of records, here the three transcripts. The dataset is at the "
                "access level called Raw, the most restricted level, where nobody may read without permission. So the page warns Devi before the "
                "button is pressed: the agent has to be granted access, and the request goes to an approver of the department that owns the data.",
        "note": "The box holds the purpose, which ends <strong>first batch</strong>, and the list holds <strong>synthetic-consultation-transcripts "
                "v1</strong>. The button reads <strong>Request access and start</strong>. The orange notice says <strong>This agent cannot read that dataset on "
                "its own (RAW). Starting will ask Hartley, who looks after Cardiology, to grant it access, in your name.</strong>",
    },
    {
        "file": "02-the-run-waits-for-permission.png", "actor": "devi",
        "title": "The run is listed, and it waits for a custodian to decide",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the Runs section",
        "text": "The run exists at once, but it has not started. A custodian is a person who may decide who reads a department's data. Until the custodian "
                "of Cardiology decides, the run does nothing, and it starts by itself afterwards. The tag sandboxed on each row says that the agent's code "
                "runs in the isolated container described above.",
        "note": "The new row carries the orange label <strong>waiting for data access</strong> and the purpose ending <strong>first batch</strong>. Under it the page says "
                "<strong>Waiting for a custodian to grant this agent access to the dataset. It starts on its own once they do.</strong>",
    },
    {
        "file": "03-hartley-sees-the-request.png", "actor": "hartley",
        "title": "Hartley, the custodian of Cardiology, sees the request and grants it for one purpose and four hours",
        "screen": "Home &middot; /",
        "text": "Hartley is a data custodian for the Cardiology department. The request names the agent's own runtime account, not Devi, because the "
                "agent, not Devi, is what reads the data. It also gives the purpose and says what granting does: it covers that purpose only and ends by itself. "
                "Hartley presses Grant access. A grant covers one run, so the next run will ask again.",
        "note": "Under <strong>Waiting for your decision</strong> the card says <strong>Devi asked for "
                "health-consultation-note-drafter-runtime to read</strong> <strong>synthetic-consultation-transcripts v1</strong> with the tag "
                "<strong>Raw</strong>. Below are the buttons <strong>Grant access</strong> and <strong>Refuse</strong>, and the sentence <strong>Granting gives "
                "health-consultation-note-drafter-runtime 4 hours, for this purpose only, and it ends by itself.</strong>",
    },
    {
        "file": "04-the-run-finishes-and-needs-somebody-else.png", "actor": "devi",
        "title": "The run finishes with three drafts, and Devi is told that somebody else has to review them",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the Runs section",
        "text": "The agent read the three transcripts and wrote a draft note for each. Every line of a draft is a line of the transcript, with its number, so "
                "that a reader can check it. The drafts are for a clinician, and the platform adds a rule of its own: what a run produced cannot be reviewed by the "
                "person who started it. Devi sees the drafts and no way to review them.",
        "note": "The third draft, <strong>visit-3.txt</strong>, ends with <strong>Not placed: 3 of 10 spoken lines fit no heading and are not in this "
                "draft.</strong> Below the drafts the page says <strong>Someone else has to review this.</strong>",
    },
    {
        "file": "05-imani-finds-the-agent-in-the-registry.png", "actor": "imani",
        "act": ("PART TWO", "A reviewer reads the drafts and corrects one",
                "The platform asks only that the reviewer is not the person who started the run. In the Health organisation the "
                "de-identification reviewer, Imani, does the checking."),
        "title": "Imani opens the agent from the Agent registry",
        "screen": "Agent registry &middot; /agent-registry",
        "text": "Imani has no Agents menu item, because that page is for the people who build and run agents. The Agent registry lists every agent in "
                "the organisation for anybody signed in, and each name in it is a link to the agent's own page. The row says which department owns the "
                "agent, who registered it, the risk level it is held at, and that nothing needs attention.",
        "note": "The search box holds <strong>consultation-note-drafter</strong>, and <strong>Showing 1 of 8</strong> follows it. The row shows "
                "<strong>Cardiology</strong>, <strong>Registered by Devi</strong>, <strong>Sponsor: Hartley</strong> and <strong>High</strong>. "
                "<strong>Active version</strong> reads <strong>Version 2</strong> and <strong>Tools</strong> reads <strong>Uses no tools</strong>.",
    },
    {
        "file": "06-imani-reads-the-third-draft.png", "actor": "imani",
        "title": "Imani reads the drafts, and the third one has two greetings under Plan",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the Runs section",
        "text": "The agent sorts each line of a transcript under a heading by the words in it, so it can put a line in the wrong place. In the third draft it placed "
                "two lines from the start of the visit, the clinician's greeting and a question about the water tablet, under Plan. Imani can see them in "
                "the draft above and in the box below it, which holds the same text for editing. The page says that only a fingerprint of the final "
                "text is kept, never the text: a fingerprint is a value worked out from the exact contents, which changes if even one character does.",
        "note": "Under <strong>Plan:</strong> in the third draft are <strong>Welcome back. How have the last two weeks been? (line 2)</strong> and "
                "<strong>Are you taking the water tablet as prescribed? (line 4)</strong>. The box is headed <strong>Review what it produced</strong>, "
                "and below it are <strong>Accept it as it is</strong>, <strong>Save my changes</strong> and <strong>Refuse it</strong>.",
    },
    {
        "file": "07-imani-removes-the-two-greetings.png", "actor": "imani",
        "title": "Imani removes the two lines, and the button for saving changes becomes available",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the review box",
        "text": "Imani deletes the two lines in the box. The box is scrolled to the end of the third draft, where the heading Plan now holds only the line that "
                "belongs there. Once the text differs from the draft, the button for saving changes works and the button for accepting the draft is switched off, because the "
                "text is no longer the draft as it was.",
        "note": "In the box, under the last <strong>Plan:</strong>, only <strong>I will reduce the dose, and schedule a blood test, and see you again in one "
                "month. (line 9)</strong> remains. The dark button reads <strong>Save my changes</strong> and the pale one reads <strong>Accept it as it is</strong>.",
    },
    {
        "file": "08-the-review-is-recorded.png", "actor": "imani",
        "title": "The review is recorded with who made it, when, and how soon after the run ended",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the Runs section",
        "text": "The platform keeps the outcome, the person, the date and time, the fingerprint of the draft and the fingerprint of the corrected text. It does not keep "
                "the corrected text. It also keeps how many seconds passed between the end of the run and the review, so that an auditor can later tell a "
                "reviewer who read from one who clicked through. A draft can be reviewed once, and the form has gone.",
        "note": "A line under the drafts reads <strong>Changed by the reviewer</strong>, then <strong>by Imani on</strong>, a date and time, and a number "
                "of seconds <strong>after the run ended</strong>. The notice <strong>Review recorded.</strong> appears at the foot of the screen.",
    },
    {
        "file": "09-the-corrected-version-is-listed.png", "actor": "devi",
        "act": ("PART THREE", "The drafter is corrected, and a clean run is accepted",
                "A reviewer has three possible outcomes: accept a draft as it was, change it, or refuse it with a reason. A correction is also a signal to the "
                "people who own the agent. Here they fix the cause, so that the next run does not repeat the mistake."),
        "title": "Devi uploads a corrected version of the drafter, and it needs no approval",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the list of versions",
        "text": "The two greetings were filed under Plan because the drafter's rules look for words, and two of the words matched inside other lines: the letters "
                "of come back are inside Welcome back, and a question about a prescription matched the word prescribed. The corrected code looks for whole words "
                "and never files a question under Plan. The platform seals it as version 3, which is finished and closed for good, and compares it with version 2. "
                "Nothing that the platform watches has changed: not the tools, the model, the data it is given or the hosts it calls. So no approval is "
                "asked for, and the Deploy button works at once.",
        "note": "The list has three rows. <strong>v3</strong> is at the top, with <strong>by Devi</strong> under its date and <strong>Tools:</strong> "
                "<strong>uses no tools</strong>. <strong>v2</strong> carries the green label <strong>active</strong>. The <strong>Deploy</strong> button on v3 is "
                "blue, and there is no explanation under it.",
    },
    {
        "file": "10-the-corrected-version-is-deployed.png", "actor": "devi",
        "title": "Devi deploys version 3, and it becomes the version that runs",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the list of versions",
        "text": "Only one version of an agent is active at a time, so the green label moves from version 2 to version 3. Version 2 stays in the list, with "
                "its fingerprints, and every earlier run still names the version that actually produced it.",
        "note": "<strong>v3</strong> now carries the green label <strong>active</strong>, and the notice <strong>v3 deployed.</strong> appears at the foot of the "
                "screen. <strong>v2</strong> has a <strong>Deploy</strong> button again.",
    },
    {
        "file": "11-devi-starts-a-run-on-the-corrected-version.png", "actor": "devi",
        "title": "Devi starts another run, and it asks for access again",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the Runs section",
        "text": "A grant covers one run, so the page warns Devi again that Hartley, who looks after Cardiology, will be asked. Hartley grants it, as before, "
                "and the run starts by itself.",
        "note": "The purpose ends <strong>after the correction</strong>, the list holds <strong>synthetic-consultation-transcripts v1</strong>, and the button "
                "reads <strong>Request access and start</strong>. The earlier run is listed above with the label <strong>succeeded</strong>.",
    },
    {
        "file": "12-imani-reads-the-clean-third-draft.png", "actor": "imani",
        "title": "Imani reads the new drafts, and the third one no longer has the greetings under Plan",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the Runs section",
        "text": "The same three transcripts give three new drafts. In the third one, Plan now holds only the line that belongs there, and the two greetings are "
                "among the lines that fit no heading, so the count of lines not placed has gone up from three to five. Imani reads the drafts and finds nothing "
                "to correct, so the button that accepts the drafts is the one to press.",
        "note": "Under the last <strong>Plan:</strong> in the third draft there is one line, <strong>I will reduce the dose, and schedule a blood test, and "
                "see you again in one month. (line 9)</strong> It ends <strong>Not placed: 5 of 10 spoken lines fit no heading and are not in this "
                "draft.</strong> The dark button reads <strong>Accept it as it is</strong>.",
    },
    {
        "file": "13-the-acceptance-is-recorded.png", "actor": "imani",
        "title": "The acceptance is recorded with the same facts as the correction",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the Runs section",
        "text": "The record holds the decision, who made it, when, and how soon after the run ended. Judging whether a decision was right stays with the "
                "organisation, and the record is what lets an auditor ask whether the drafts were really being read.",
        "note": "The line reads <strong>Accepted as it was</strong>, then <strong>by Imani on</strong>, a date and time, and a number of seconds "
                "<strong>after the run ended</strong>. <strong>Review recorded.</strong> appears at the foot of the screen.",
    },
    {
        "file": "14-nakamura-reads-the-figures.png", "actor": "nakamura",
        "act": ("PART FOUR", "The data protection officer shows an auditor what happened",
                "An evidence pack is a record of everything the platform can back about one agent over a period. It holds identifiers, counts, "
                "fingerprints and times, and no personal data."),
        "title": "Nakamura reads the figures for the agent, counted for the agent and never for a person",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the section on how its output is reviewed",
        "text": "Nakamura is the data protection officer, a person who watches how the organisation handles personal data. Nakamura reaches the same page from the "
                "registry. The section adds up every review of this agent's output: how many were accepted as they were, how many changed and how many refused, "
                "how many different people decided, and the middle length of time they took. It is counted for the agent. It never names a person.",
        "note": "<strong>2 reviewed: 1 accepted as they were, 1 changed, 0 refused.</strong> is followed by <strong>1 different person decided</strong>. The "
                "section is headed <strong>How its output is reviewed</strong> and says <strong>Counted for the agent, never for a person.</strong>",
    },
    {
        "file": "15-nakamura-makes-the-evidence-pack.png", "actor": "nakamura",
        "title": "Nakamura makes the evidence pack for the agent",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the Evidence pack section",
        "text": "The pack covers the risk level history, each version that ran and who approved it, the runs, how the output was reviewed, and any suspensions or "
                "incidents. It records who decided what and when, and how many runs there were. It holds no personal data, so the text of the drafts is not in it. "
                "With no dates chosen it covers the last 90 days. Making a pack is itself recorded. The page shows the fingerprint of the pack, so that "
                "a copy given to an auditor can later be matched to it. It also says how many records the agent's own chain holds. The chain is the agent's "
                "oversight records, each stored with a fingerprint of the one before it, so that a change to an old record shows.",
        "note": "<strong>Evidence pack</strong> says <strong>Everything the platform can back about this agent over a period</strong>. After "
                "<strong>Make the evidence pack</strong> the page says <strong>Made for</strong> two dates, then <strong>Hash:</strong> followed by a value that "
                "starts <strong>sha256:</strong>, and <strong>Nothing was changed.</strong> The buttons <strong>Download it as a "
                "file</strong> and <strong>Check that the records have not been changed</strong> are offered.",
    },
    {
        "file": "16-nakamura-checks-the-records.png", "actor": "nakamura",
        "title": "Nakamura checks that no record was changed, removed or added outside the platform",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the Evidence pack section",
        "text": "The check reads every record again, works out its fingerprint, and compares it with the one stored when the record was written and with the link "
                "to the record before it. A record altered afterwards, a record removed from the middle, or one added without the platform would show. Here "
                "nothing does, so the page says so. The same check can be run again at any time, by anybody who may make the pack.",
        "note": "<strong>Check that the records have not been changed</strong> is followed by <strong>records checked. None was changed, removed or "
                "added outside the platform.</strong>",
    },
]

ACTORS = {
    "devi": ("Devi", "Data engineer in the Health organisation"),
    "hartley": ("Hartley", "Data custodian for the Cardiology department"),
    "imani": ("Imani", "De-identification reviewer in the Health organisation"),
    "nakamura": ("Nakamura", "Data protection officer in the Health organisation"),
}

PAGE = Page(
    slug="agent-review", org="health",
    title=series_entry("agent-review")["title"],
    eyebrow="Health organisation &middot; AI agents",
    lede="An AI agent that drafts visit notes from consultation transcripts runs for real in a sandbox. The data engineer who starts a run cannot review "
         "what it produced, so a reviewer does: one run is corrected, the drafter is then fixed, and the next run is accepted as it was. The platform records each outcome with who "
         "decided, when and how soon after the run ended, and the data protection officer reads the counts for the agent, makes an evidence pack for "
         "an auditor and checks that no record was changed.",
    actors=ACTORS, steps=STEPS,
    words=["organisation", "department", "custodian", "agent", "run", "dataset", "access_level", "container", "fingerprint", "dpo", "deid_reviewer",
           "agent_registry", "evidence_pack", "record_chain"],
    shots_dir=SHOTS_ROOT / "agent-review",
    capture_note="Captured live against the <code>health</code> organisation by <code>web/walkthroughs/agent-review.spec.ts</code> and built by "
                 "<code>docs/tools/build_agent_review_walkthrough.py</code>.",
)

if __name__ == "__main__":
    missing = [s["file"] for s in STEPS if not (PAGE.shots_dir / s["file"]).exists()]
    if missing:
        raise SystemExit("No screenshot for: " + ", ".join(missing) + "\nRun the capture first: cd web && npx "
                         "playwright test --config=walkthroughs/playwright.config.ts agent-review")
    write(PAGE)
