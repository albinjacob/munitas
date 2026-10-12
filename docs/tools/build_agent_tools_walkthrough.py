r"""Build docs/public/walkthroughs/agent-tools-walkthrough.html from captured screenshots.

The screenshots come from web/walkthroughs/agent-tools.spec.ts, which drives the real console against the live Health organisation as Okafor (the AI risk owner),
Devi (a data engineer), Hartley (an approver of the Cardiology department) and Okonjo (an approver of the Radiology department). Run that first, then this builder:

    cd web
    npx playwright test --config=walkthroughs/playwright.config.ts agent-tools
    ..\.venv\Scripts\python.exe ..\docs\tools\build_agent_tools_walkthrough.py

This is the fourth page of "AI agents" in the Health story. It shows the list of tools the organisation approves in advance, and what it saves: a version of the planning agent
(made by the approval walkthrough) that adds a listed tool is approved in advance, one that adds a tool needing the department only waits for the department alone, and a tool
approved only up to a lower level, a tool that is not on the list and a tool that pages a person are decided by named people with written reasons.
"""
from __future__ import annotations

from walkthrough_kit import SHOTS_ROOT, Page, series_entry, write

STEPS = [
    {
        "file": "01-okafor-reads-the-approved-tools.png", "actor": "okafor",
        "act": ("PART ONE", "The organisation decides which tools need no request",
                "Every time a data engineer uploads a new version of an AI agent, Munitas compares it with the version before it. If the new version lets the agent "
                "do more, for example by adding a tool, the change has to be approved before the version can be put to work. In a large organisation that means "
                "a stream of requests for tools that are routine and safe, and people who approve a stream stop reading it. So the organisation writes a list of "
                "tools it has approved in advance. A new version that adds only those tools does not wait for the people who would otherwise decide it."),
        "title": "Okafor, the AI risk owner, opens the list of tools the Health organisation has approved in advance",
        "screen": "Approved tools &middot; /approved-tools",
        "text": "Okafor is the AI risk owner of the Health organisation: the one person who answers for how the organisation holds its AI agents. A tool is something an "
                "agent may do, such as reading a dataset or adding an item to a queue. The name of a tool is declared by whoever uploads the agent, so the list records "
                "the organisation's decision about a name. It does not check what the code behind the name does. Each row says what the tool does, how far it is "
                "approved, the highest risk level it is approved up to, the conditions and the reason, and the date by which somebody must look at it again. Four tools "
                "are pre-approved, which means nobody is asked when a version adds one. Five need the approval of the department that owns the agent, and not the officer.",
        "note": "The heading above the table is <strong>The tools in force</strong>, followed by <strong>Version 1 by Okafor on</strong> a date and time. The first "
                "row is <strong>read_dataset_version</strong>, with <strong>Pre-approved: nobody is asked</strong> under <strong>How far it is approved</strong> and "
                "<strong>High</strong> under <strong>Up to level</strong>. The row <strong>draft_appointment_letter</strong> says <strong>Department approval "
                "only: the officer is not asked</strong>, and <strong>request_followup_appointment</strong> is approved up to <strong>Moderate</strong>. The button at the "
                "foot reads <strong>Change the approved tools</strong>.",
    },
    {
        "file": "02-okafor-adds-a-tool.png", "actor": "okafor",
        "title": "Okafor adds a tool that reads the clinic's free appointment times",
        "screen": "Approved tools &middot; /approved-tools, the editor",
        "text": "Only the AI risk owner can change the list, and the platform refuses everybody else. Okafor adds a tenth tool. Reading the free times in the clinic's "
                "schedule changes nothing and shows nothing about a person, so it is pre-approved up to the highest level. Okafor writes why the tool is approved, a date "
                "within a year when somebody must look at it again, and a reason for the change to the list as a whole, because every change is kept with who made it, when "
                "and why.",
        "note": "The box <strong>Tool 10</strong> holds <strong>read_clinic_schedule</strong> under <strong>Name, as the agent declares it</strong>, with <strong>Reads data and "
                "changes nothing</strong>, <strong>Pre-approved: nobody is asked</strong> and <strong>High</strong>. <strong>Why it is approved in advance</strong> reads "
                "<strong>It reads dates and times only and changes nothing, so a version that adds it needs no request.</strong> The button is "
                "<strong>Save as a new version</strong>.",
    },
    {
        "file": "03-the-new-tool-is-on-the-list.png", "actor": "okafor",
        "title": "The list is now version 2, with the new tool in its last row",
        "screen": "Approved tools &middot; /approved-tools",
        "text": "Saving did not change version 1. It made version 2, and the line above the table says who wrote it, when, and why. A version of an agent that was "
                "registered earlier keeps the decision it was given. The new list applies to versions that are uploaded from now on.",
        "note": "The line above the table reads <strong>Version 2 by Okafor on</strong> a date and time, followed by <strong>Reason:</strong> and <strong>Agents that plan "
                "follow-up visits need the free times in the clinic's schedule, and reading them is as safe as the other reading tools.</strong> The last row of the table is "
                "<strong>read_clinic_schedule</strong>, and the link below reads <strong>Show the 1 earlier version</strong>.",
    },
    {
        "file": "04-both-versions-are-kept.png", "actor": "okafor",
        "title": "Version 1 of the list is kept, with who wrote it and why",
        "screen": "Approved tools &middot; /approved-tools, Earlier versions",
        "text": "Every earlier version of the list stays readable by everybody who is signed in. Anybody who asks later what was approved on a given day can be answered "
                "from the list as it was then, with the name of the person who wrote it.",
        "note": "The heading is <strong>Earlier versions</strong>, with the link <strong>Hide the earlier versions</strong>. Under it the page says <strong>Version 1</strong> "
                "<strong>by Okafor on</strong> a date and time and <strong>Reason:</strong> <strong>The routine tools that need nobody, and the tools that need only the "
                "department, agreed for the first list.</strong> The table has nine rows and no row for <strong>read_clinic_schedule</strong>.",
    },
    {
        "file": "05-devi-uploads-a-version-with-a-listed-tool.png", "actor": "devi",
        "act": ("PART TWO", "A version that adds a listed tool is approved in advance",
                "The followup-appointment-planner agent belongs to the Cardiology department. It plans follow-up appointments, and the organisation holds it at the level called "
                "High, which means that a change that lets the agent do more needs the approval of the department that owns it and, as a second approver, the AI risk owner. "
                "A new version that adds only a listed tool skips both."),
        "title": "Devi uploads a new version of the planning agent and lists the tool it may call",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the upload form",
        "text": "Devi is a data engineer in the Health organisation. A version of an agent is one uploaded copy of its code, which Munitas seals, meaning finished and closed "
                "for good, so it can never be edited. The new version keeps the two tools of the version before it and adds the one Okafor has just put on the list. Devi types "
                "the names in the box for the tools the version may call.",
        "note": "<strong>Project (.zip)</strong> holds <strong>triage-agent.zip</strong> and <strong>Model</strong> holds <strong>none</strong>. <strong>Tools it may call</strong> "
                "holds <strong>read_dataset_version, send_confirmation, read_clinic_schedule</strong>, and the button is <strong>Upload version</strong>.",
    },
    {
        "file": "06-the-version-is-approved-in-advance.png", "actor": "devi",
        "title": "Munitas approves the version in advance and says which entry of the list covered it",
        "screen": "Agent changes &middot; /agent-changes",
        "text": "The page lists what each new version changes compared with the version before it. The first sentence on the line for the tool is the platform's own note: it "
                "cannot know what a tool does, because the name is declared by whoever uploads the agent, so it treats an unknown tool as one that may act outside the platform. "
                "The second sentence is the organisation's decision: the tool is on the approved tools list, approved up to High, which is the level this agent is held at, so "
                "nobody is asked.",
        "note": "The card is headed <strong>followup-appointment-planner</strong>, followed by <strong>version 3</strong>, with the green label <strong>Approved in advance</strong>. "
                "The line <strong>On the approved tools list.</strong> says <strong>read_clinic_schedule is on the list (version 2, by Okafor on</strong> a date and time<strong>), "
                "approved up to High, so nobody is asked.</strong>",
    },
    {
        "file": "07-the-version-is-deployed.png", "actor": "devi",
        "title": "Devi deploys the version at once, because nobody has to approve it",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the list of versions",
        "text": "To deploy a version is to make it the one that runs when somebody starts a run. Devi pressed Deploy on the new version straight after uploading it. The older "
                "versions each still have a Deploy button, because an earlier approved version can be put back.",
        "note": "The row for <strong>v3</strong> carries the green label <strong>active</strong> and says <strong>Tools:</strong> <strong>read_clinic_schedule, read_dataset_version, "
                "send_confirmation</strong>. The rows for <strong>v2</strong> and <strong>v1</strong> each have a blue <strong>Deploy</strong> button.",
    },
    {
        "file": "08-devi-uploads-a-version-with-a-draft-tool.png", "actor": "devi",
        "act": ("PART THREE", "A listed tool that needs the department only asks one person",
                "Some tools are routine but not harmless: they create something that a person then acts on. The organisation approves those in advance only for the department "
                "that owns the agent to decide, and does not ask the officer each time."),
        "title": "Devi uploads the next version, which adds a tool that drafts an appointment letter",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the upload form",
        "text": "The new tool prepares a letter that names a follow-up appointment, and leaves it as a draft for a member of staff to check and send. Okafor's list approves it up to High, "
                "on the condition that a member of staff reads and sends every letter and the agent never sends one.",
        "note": "<strong>Tools it may call</strong> holds <strong>read_dataset_version, send_confirmation, read_clinic_schedule, draft_appointment_letter</strong>. The button is "
                "<strong>Upload version</strong>.",
    },
    {
        "file": "09-hartley-is-the-only-person-asked.png", "actor": "hartley",
        "title": "Only an approver of the Cardiology department is asked, and Hartley writes a reason",
        "screen": "Agent changes &middot; /agent-changes",
        "text": "Hartley is a data custodian for the Cardiology department, which means a person who may decide who reads its data and approve changes to agents it owns. "
                "Because the tool is on the list for the department alone, this agent's level asks for nobody else. The card names the one approval it waits for. Every decision "
                "is kept with its reason, who made it and when.",
        "note": "The card for <strong>followup-appointment-planner</strong>, <strong>version 4</strong>, has the label <strong>Waiting for approval</strong>. The line "
                "<strong>On the approved tools list.</strong> says <strong>draft_appointment_letter is on the list (version 2, by Okafor on</strong> a date and time<strong>), "
                "approved up to High with the approval of the department alone, so the officer is not asked.</strong> Below it: <strong>Waiting for an approver of the department "
                "that owns the agent.</strong> The box <strong>Your reason, for the approval of the department that owns the agent</strong> holds <strong>Cardiology owns this agent. "
                "A member of staff checks and sends every letter, so the department approves it.</strong>",
    },
    {
        "file": "10-approved-by-the-department-alone.png", "actor": "hartley",
        "title": "The version is approved with one decision",
        "screen": "Agent changes &middot; /agent-changes",
        "text": "One decision was enough. The card lists the single approval, with who gave it, when and why. No decision by the AI risk owner was needed, and none is recorded.",
        "note": "The card for <strong>version 4</strong> now has the green label <strong>Approved</strong>, and one decision line: <strong>Approved</strong> <strong>for the department "
                "that owns the agent by Hartley on</strong> a date and time, followed by the reason Hartley wrote.",
    },
    {
        "file": "11-devi-uploads-a-version-with-a-lower-level-tool.png", "actor": "devi",
        "act": ("PART FOUR", "A tool approved only up to a lower level is decided by two people",
                "An entry on the list is approved up to a risk level. The higher a level, the more an agent can affect people, so a tool approved only up to a lower level does not "
                "cover an agent held at a higher one."),
        "title": "Devi uploads a version that adds a tool the list approves only up to Moderate",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the upload form",
        "text": "The new tool leaves a reminder, built from a fixed and neutral wording, for the clinic's own notification service to pick up. Okafor's list approves it up to Moderate, "
                "one level below High. The planning agent is held at High, so the list does not cover it.",
        "note": "<strong>Tools it may call</strong> holds <strong>read_dataset_version, send_confirmation, read_clinic_schedule, draft_appointment_letter, queue_reminder_from_template</strong>.",
    },
    {
        "file": "12-the-list-does-not-cover-this-agents-level.png", "actor": "okafor",
        "title": "Hartley has approved for the department, and Okafor is asked to give the second approval",
        "screen": "Agent changes &middot; /agent-changes",
        "text": "Because the list does not cover this agent's level, the card has no line about the approved tools list, and the version waits for two people. Hartley has already "
                "approved for the department. The second approval belongs to the AI risk owner. Okafor writes a reason.",
        "note": "The card for <strong>version 5</strong> says <strong>Waiting for the AI risk owner.</strong> and <strong>Approved</strong> "
                "<strong>for the department that owns the agent by Hartley on</strong> a date and time. The box is headed <strong>Your reason, for the approval of the AI risk "
                "owner</strong> and holds <strong>The list approves this tool only up to Moderate, and this agent is held at High. The wording is fixed, so the AI risk owner approves it.</strong> "
                "The buttons are <strong>Approve</strong> and <strong>Refuse</strong>.",
    },
    {
        "file": "13-approved-by-the-department-and-the-risk-owner.png", "actor": "okafor",
        "title": "Both approvals are recorded, each with who gave it and why",
        "screen": "Agent changes &middot; /agent-changes",
        "text": "The card now carries both decisions. Each names the person, the date and time, and the reason in that person's own words.",
        "note": "The card for <strong>version 5</strong> has two decision lines. The second reads <strong>Approved</strong> "
                "<strong>for the AI risk owner by Okafor on</strong> a date and time, followed by <strong>Reason:</strong> <strong>The list approves this tool only up to Moderate, and this "
                "agent is held at High.</strong>",
    },
    {
        "file": "14-devi-uploads-a-version-with-an-unlisted-tool.png", "actor": "devi",
        "act": ("PART FIVE", "A tool that is not on the list is decided one case at a time",
                "Some tools are never approved in advance, because the harm if they go wrong is greater than the cost of looking. They are decided for each version by named people, "
                "who write why."),
        "title": "Devi uploads a version of the planning agent that adds a tool which sends a text message the agent writes itself",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the upload form",
        "text": "The new tool lets the agent write a text message and send it to a patient. That tool is not on Okafor's list, and the list deliberately holds only the fixed-wording "
                "version of it. The agent is held at High, so the version waits for the department and for the AI risk owner.",
        "note": "<strong>Tools it may call</strong> ends with <strong>queue_reminder_from_template, send_free_text_message</strong>, after the tools of the version before it.",
    },
    {
        "file": "15-the-risk-owner-refuses.png", "actor": "okafor",
        "title": "Hartley approved for the department, and Okafor reads the version and refuses",
        "screen": "Agent changes &middot; /agent-changes",
        "text": "Hartley approved for Cardiology, saying that patients should be reminded and that whether the agent may write the text itself is for the AI risk owner. Okafor "
                "refuses, and writes why: a text can show health information on a lock screen, and the fixed-wording tool on the list is enough.",
        "note": "The card for <strong>followup-appointment-planner</strong>, <strong>version 6</strong>, has the label <strong>Waiting for approval</strong>. The line for the tool reads "
                "<strong>Adds the tool send_free_text_message.</strong> Below it the page says <strong>Waiting for the AI risk owner.</strong> and records Hartley's approval. The box "
                "holds <strong>The agent would write the text itself, and a text can show health information on a lock screen. The fixed-wording tool is enough.</strong> The buttons are "
                "<strong>Approve</strong> and <strong>Refuse</strong>.",
    },
    {
        "file": "16-the-refused-version-cannot-be-deployed.png", "actor": "devi",
        "title": "The refused version cannot be deployed",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the list of versions",
        "text": "Devi sees the result on the agent's page. The Deploy button is grey and says why, in the platform's own words. The version stays sealed in the list, so there is a "
                "permanent record that it was submitted and refused. The agent keeps running as the version that was already active.",
        "note": "The row for <strong>v6</strong> shows <strong>Tools:</strong> ending with <strong>send_confirmation, send_free_text_message</strong>. Its <strong>Deploy</strong> "
                "button is grey, and under it the page says <strong>This version widens what the agent can do, and the widening was refused.</strong> The row for <strong>v3</strong> "
                "carries the label <strong>active</strong>.",
    },
    {
        "file": "17-devi-uploads-a-version-that-pages-a-person.png", "actor": "devi",
        "title": "Devi uploads a version of the radiology agent that pages a person",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the upload form",
        "text": "The radiology-intake-triage agent ranks incoming radiology requests for review. It belongs to the Radiology department. The new version adds a tool that pages the "
                "on-call radiologist. It keeps only that tool, so it also drops the tool the earlier version had.",
        "note": "<strong>Tools it may call</strong> holds <strong>page_oncall_radiologist</strong>. The button is <strong>Upload version</strong>.",
    },
    {
        "file": "18-radiology-refuses-the-paging-tool.png", "actor": "okonjo",
        "title": "Okonjo, an approver for Radiology, reads what the version adds and writes why it is refused",
        "screen": "Agent changes &middot; /agent-changes",
        "text": "Okonjo is a data custodian for the Radiology department. At the level this agent is held at, the department's approval is the only one asked. The card lists one "
                "thing that widens the agent, the tool, and others that narrow it, which need no approval. A paging tool is not on this organisation's list: a page that fires "
                "on studies that need no action can teach people to ignore pages. Okonjo writes that Radiology will decide again once the rate of false alerts has been measured. "
                "How a paging service decides whom to page, and how often, belongs to that service and is outside Munitas. The box shows the last lines of what was typed, and the whole reason "
                "is recorded and shown in the next picture.",
        "note": "The card for <strong>radiology-intake-triage</strong>, <strong>version 4</strong>, has the tag <strong>Widens</strong> on <strong>Adds the tool "
                "page_oncall_radiologist.</strong> and the tag <strong>Narrows</strong> on <strong>Narrower than before. Removes the tool read_dataset_version.</strong> Below them "
                "the page says <strong>Waiting for an approver of the department that owns the agent.</strong>",
    },
    {
        "file": "19-the-paging-version-is-refused.png", "actor": "okonjo",
        "title": "Both refusals are listed, each with who refused it and the reason",
        "screen": "Agent changes &middot; /agent-changes, filtered to Refused",
        "text": "The Refused tab lists every version that was refused, newest first. The radiology version carries Okonjo's reason in full, and the planning agent's version carries Okafor's. "
                "A refusal is as permanent as an approval: it is kept with the person, the date and the reason.",
        "note": "The tab <strong>Refused</strong> is selected. The first card is <strong>radiology-intake-triage</strong>, <strong>version 4</strong>, with the red label "
                "<strong>Refused</strong> and the line <strong>Refused</strong> <strong>for the department that owns the agent by Okonjo on</strong> a date and time. The second "
                "is <strong>followup-appointment-planner</strong>, <strong>version 6</strong>, with <strong>Refused</strong> <strong>for the AI risk owner by Okafor on</strong> a date and time.",
    },
]

ACTORS = {
    "okafor": ("Okafor", "AI risk owner in the Health organisation"),
    "devi": ("Devi", "Data engineer in the Health organisation"),
    "hartley": ("Hartley", "Data custodian for the Cardiology department"),
    "okonjo": ("Okonjo", "Data custodian for the Radiology department"),
}

PAGE = Page(
    slug="agent-tools", org="health",
    title=series_entry("agent-tools")["title"],
    eyebrow="Health organisation &middot; AI agents",
    lede="Every new version of an AI agent that lets the agent do more has to be approved before it can be put to work. To keep the people who approve from reading a stream of "
         "routine requests, the AI risk owner writes a list of tools the organisation has approved in advance, each with the highest risk level it is approved for. A version "
         "that adds only listed tools is approved at once, or waits only for the department that owns the agent. A tool approved only up to a lower level, a tool that is not "
         "on the list and a tool that pages a person are decided by named people, who write why.",
    actors=ACTORS, steps=STEPS,
    words=["organisation", "department", "custodian", "agent", "agent_version", "sealed", "deploy", "risk_level", "widening", "approved_tools", "risk_owner"],
    shots_dir=SHOTS_ROOT / "agent-tools",
    capture_note="Captured live against the <code>health</code> organisation by <code>web/walkthroughs/agent-tools.spec.ts</code> and built by "
                 "<code>docs/tools/build_agent_tools_walkthrough.py</code>.",
)

if __name__ == "__main__":
    missing = [s["file"] for s in STEPS if not (PAGE.shots_dir / s["file"]).exists()]
    if missing:
        raise SystemExit("No screenshot for: " + ", ".join(missing) + "\nRun the capture first: cd web && npx "
                         "playwright test --config=walkthroughs/playwright.config.ts agent-tools")
    write(PAGE)
