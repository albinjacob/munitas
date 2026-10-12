r"""Build docs/public/walkthroughs/agent-approval-walkthrough.html from captured screenshots.

The screenshots come from web/walkthroughs/agent-approval.spec.ts, which drives the real console against the live Health organisation as Okafor (the
AI risk owner), Devi (a data engineer) and Hartley (the custodian of Cardiology). Run that first, then this builder:

    cd web
    npx playwright test --config=walkthroughs/playwright.config.ts agent-approval
    ..\.venv\Scripts\python.exe ..\docs\tools\build_agent_approval_walkthrough.py

This is the third page of "AI agents" in the Health story. It follows a new agent, followup-appointment-planner, from its registration with a proposed risk level to
the decision on its first version and its level, and then a later version that adds a tool that is not on the approved tools list, which two people approve. Pictures 09 and
19 of the capture are not used: 09 repeats what 11 shows, and 19 is a form whose typed reason contains a pronoun.
"""
from __future__ import annotations

from walkthrough_kit import SHOTS_ROOT, Page, series_entry, write

STEPS = [
    {
        "file": "01-okafor-reads-the-risk-levels.png", "actor": "okafor",
        "act": ("PART ONE", "The organisation decides how carefully each agent is held",
                "An AI agent is a program that works on data on somebody's behalf, with a named owner, under the same rules as a person. Before an agent "
                "does any work, the organisation decides how much care it needs. The organisation writes a short list of <strong>risk levels</strong>, "
                "from the least strict to the most strict. A level says whether a change to an agent needs a second approver, and how often somebody "
                "has to confirm that the agent is still needed."),
        "title": "Okafor, the AI risk owner, reads the risk levels that the Health organisation has already written",
        "screen": "Risk levels &middot; /risk-levels",
        "text": "Okafor is the AI risk owner of the Health organisation, the one person who answers for how the organisation holds its AI agents. The levels were written "
                "earlier, and every person signed in can read them. Where it says a second approver, it means that a change which makes an agent able to do more "
                "needs the AI risk owner as well as the department that owns the agent. The top of the list, High, is the level for an agent whose output can change "
                "what happens to a person.",
        "note": "The heading is <strong>Risk levels</strong>. Under <strong>The levels in force</strong> the page says <strong>Version 3 by Okafor on</strong> a date and time, "
                "followed by a reason. The table has four rows: <strong>Low</strong>, <strong>Moderate</strong>, <strong>High</strong> and <strong>Not allowed</strong>. "
                "The row for <strong>High</strong> has <strong>Yes</strong> under <strong>Second approver</strong> and <strong>90 days</strong> under "
                "<strong>Re-confirm every</strong>. Below the table the page says <strong>Agents are held to these levels: an agent needs a confirmed level to start a run, "
                "and a version that widens an agent is approved before it is deployed.</strong>",
    },
    {
        "file": "02-devi-registers-the-agent.png", "actor": "devi",
        "title": "Devi registers a new agent for the Cardiology department and proposes a level for it",
        "screen": "Register an agent &middot; /agents/register",
        "text": "Devi is a data engineer in the Health organisation. The new agent plans follow-up appointments from a consultation note, for scheduling staff to confirm. "
                "The Cardiology department owns it. The person who registers an agent proposes its level, and says who is affected by what the agent produces and why the "
                "level fits. Devi cannot confirm the level, because that belongs to the AI risk owner.",
        "note": "<strong>Name</strong> holds <strong>followup-appointment-planner</strong> and <strong>Owned by</strong> holds <strong>Cardiology</strong>. "
                "Under <strong>Risk level you propose</strong> the chosen level is <strong>Moderate</strong>. <strong>Who is affected by what the agent "
                "produces</strong> reads <strong>Patients whose consultations ask for a follow-up appointment</strong>. <strong>Why this "
                "level</strong> reads <strong>Scheduling staff confirm every appointment, and the agent cannot book or send anything itself</strong>.",
    },
    {
        "file": "03-the-level-waits-for-the-officer.png", "actor": "devi",
        "title": "The agent exists, and its level waits for the AI risk owner",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the Risk level section",
        "text": "Munitas has registered the agent, with no version yet. The page says what it means that no level is confirmed: every approval for this agent "
                "treats it as High, the strictest level that still allows it to be put to work, and because the organisation holds its agents to its levels, "
                "the agent cannot start a run yet. Devi's proposal is kept with who made it and when.",
        "note": "Under <strong>Risk level</strong> the page says <strong>No level has been confirmed.</strong> and then <strong>Proposed: Moderate</strong>, "
                "<strong>by Devi on</strong> a date and time, followed by <strong>It waits for the AI risk owner.</strong> At the foot of "
                "the section: <strong>Only the AI risk owner may confirm the risk level of an agent.</strong>",
    },
    {
        "file": "04-devi-uploads-the-first-version.png", "actor": "devi",
        "title": "Devi uploads the agent's first version and lists the one tool it may call",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the upload form",
        "text": "A version of an agent is one uploaded copy of its code. Munitas seals it, which means finished and closed for good, so it can never be edited. "
                "A tool is something the agent may do. This version may only read the details of a dataset version, so Devi lists that one tool. The "
                "model is none, because this code calls no model.",
        "note": "<strong>Project (.zip)</strong> holds <strong>triage-agent.zip</strong> and <strong>Model</strong> holds <strong>none</strong>. "
                "<strong>Tools it may call</strong> holds <strong>read_dataset_version</strong>. The sentence under the box says <strong>Comma-separated.</strong>",
    },
    {
        "file": "05-the-first-version-waits-for-approval.png", "actor": "devi",
        "title": "The version is sealed, and Deploy is switched off until it is approved",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the list of versions",
        "text": "To deploy a version is to make it the one that runs when somebody starts a run. This version is the first one, so it is compared with nothing and "
                "counts as a widening: it gives the agent a tool it did not have. Even though the tool is on the organisation's list of tools approved in advance, a first "
                "version is never covered by that list, because the agent's level is decided together with it. The page says in words what the version waits for, so that "
                "nobody has to guess why the button is grey. It needs an approver of the Cardiology department and the AI risk owner.",
        "note": "The row for <strong>v1</strong> shows <strong>Tools:</strong> <strong>read_dataset_version</strong>. The <strong>Deploy</strong> button is "
                "greyed out, and under it the page says <strong>This version widens what the agent can do. It waits for an approver of the department "
                "that owns the agent and the AI risk owner.</strong>",
    },
    {
        "file": "06-hartley-gives-the-departments-part.png", "actor": "hartley",
        "act": ("PART TWO", "Two different people decide the first version and its level",
                "The department that owns an agent answers for what the agent does, so an approver of that department decides first. The AI risk owner "
                "decides second. The person who uploaded a version is never one of the people who decide it."),
        "title": "Hartley, an approver for Cardiology, sees what the version adds and gives the department's part",
        "screen": "Agent changes &middot; /agent-changes",
        "text": "Hartley is a data custodian for the Cardiology department, a person who may decide who reads its data and approve changes to agents it owns. "
                "The page lists every version waiting for a decision, and says what each one changes compared with the version before it. Hartley "
                "writes a reason, because every decision is kept with its reason, who made it and when. Hartley can give the department's part only: the "
                "page says that the officer's part belongs to the AI risk owner.",
        "note": "The card is headed <strong>followup-appointment-planner</strong>, followed by <strong>version 1</strong>, and carries the label "
                "<strong>Waiting for approval</strong>. The yellow tag <strong>Widens</strong> is followed by <strong>Reads more.</strong> and "
                "<strong>Adds the tool read_dataset_version, which reads the details of a dataset version.</strong> The box is headed "
                "<strong>Your reason, for the approval of the department that owns the agent</strong> and holds <strong>Cardiology owns this agent. It reads one dataset and "
                "cannot book or send anything, which is what the department asked for.</strong> Below the card the page says "
                "<strong>Only the AI risk owner may give the officer's approval.</strong>",
    },
    {
        "file": "07-hartley-cannot-give-the-officers-part.png", "actor": "hartley",
        "title": "Hartley's approval is recorded, and the version still waits for the AI risk owner",
        "screen": "Agent changes &middot; /agent-changes",
        "text": "One approval is not enough. The card now records who approved for the department and when, with the reason Hartley wrote, and it names "
                "who the version waits for next. Hartley has no box for the officer's part, because Hartley does not hold that role.",
        "note": "The card says <strong>Waiting for the AI risk owner.</strong> and then <strong>Approved</strong> <strong>for the department that "
                "owns the agent by Hartley on</strong> a date and time, followed by the reason. The line <strong>Only the AI risk owner may give "
                "the officer's approval.</strong> remains, and there is no box under it. The notice <strong>Approved.</strong> appears at the foot of the screen.",
    },
    {
        "file": "08-okafor-decides-the-level-and-the-version.png", "actor": "okafor",
        "title": "Okafor decides the agent's level and its first version in one step",
        "screen": "Agent changes &middot; /agent-changes",
        "text": "Because this is the agent's first version, its level is decided with it, so that no version ever exists without a level. Okafor sees "
                "what Devi proposed, and chooses a different level, High, because the agent works with named appointment requests and a later version is expected to message "
                "people, even though scheduling staff confirm every booking. One reason covers both the level and the version. Okafor did not upload the version and "
                "so is allowed to decide it. The box shows the last lines of what was typed, and the whole reason is recorded and shown in later pictures.",
        "note": "<strong>This is the agent's first version. Its risk level is decided with it, in one decision.</strong> The page then shows "
                "<strong>Level proposed: Moderate</strong>, by Devi, and what Devi wrote under <strong>Affected:</strong> and <strong>Reason:</strong>. "
                "<strong>Level to confirm</strong> holds <strong>High</strong>. The button reads <strong>Confirm the level and approve this "
                "version</strong>.",
    },
    {
        "file": "10-the-registry-lists-the-agent-at-its-level.png", "actor": "okafor",
        "act": ("PART THREE", "The agent is listed at its level, and somebody answers for it",
                "The registry lists every agent of the organisation in one place, for anybody signed in. A sponsor is the person who answers for an agent. "
                "The sponsor has to be an approver of the department that owns it."),
        "title": "Okafor finds the agent in the Agent registry, at its confirmed level",
        "screen": "Agent registry &middot; /agent-registry",
        "text": "Okafor has no need of the Agents menu item, which is for the people who build and run agents. The Agent registry is for everybody who "
                "oversees them, and each agent in it is a link to its own page. The registry shows the department that owns the agent, who registered "
                "it, its risk level, and what needs attention. It flags that this agent has no sponsor yet.",
        "note": "<strong>Showing 1 of 8</strong> is the count after Okafor typed the agent's name in <strong>Search</strong>. The row shows "
                "<strong>Cardiology</strong>, <strong>Registered by Devi</strong> and <strong>Sponsor: none</strong>. Under <strong>Risk level</strong> "
                "it reads <strong>High</strong>, then <strong>Example label: EU AI Act high-risk use</strong>. The orange tag under <strong>Needs "
                "attention</strong> reads <strong>No sponsor</strong>, and <strong>Active version</strong> reads <strong>None deployed</strong>.",
    },
    {
        "file": "11-the-agent-page-shows-the-confirmed-level.png", "actor": "okafor",
        "title": "The agent's own page shows the confirmed level, who confirmed it and why",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the Risk level section",
        "text": "Okafor follows the link from the registry. The level that was proposed as Moderate is now confirmed as High, with Okafor's name, the date "
                "and time, and the reason. The earlier proposal is kept: the page offers the full history of proposals and decisions. Under it, the same page "
                "offers the sponsor to be chosen from the approvers of Cardiology, and says how often the agent must be re-confirmed: re-confirming means "
                "somebody checks that the agent is still needed and still held at the right level.",
        "note": "<strong>High</strong>, <strong>Example label: EU AI Act high-risk use</strong>, then <strong>confirmed by Okafor on</strong> a date and time. "
                "Below it are <strong>Purpose:</strong>, <strong>Affected:</strong> and <strong>Reason:</strong>, and the link "
                "<strong>Show all 2 proposals and decisions</strong>. Under <strong>Sponsor and re-confirmation</strong> the page says <strong>This agent "
                "has no sponsor yet.</strong> and <strong>Its level asks for re-confirmation every 90 days.</strong>",
    },
    {
        "file": "20-the-sponsor-is-recorded.png", "actor": "okafor",
        "title": "Okafor names Hartley as the sponsor, and the page records who and when",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the Sponsor and re-confirmation section",
        "text": "Okafor chooses Hartley, an approver of Cardiology, and writes why. The sponsor is a person who can be asked about the agent, and who has "
                "to re-confirm it every 90 days. The assignment is a new record and the earlier state stays in the history.",
        "note": "The page now says <strong>Sponsor: Hartley, assigned by Okafor on</strong> a date and time, and the notice <strong>Sponsor "
                "assigned.</strong> appears at the foot of the screen. <strong>Show the history</strong> is now offered under the form.",
    },
    {
        "file": "12-the-first-version-is-deployed.png", "actor": "devi",
        "title": "Devi deploys the first version, and the agent can start a run",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the list of versions",
        "text": "With the level confirmed and the first version approved, the Deploy button works. Devi presses it, and version 1 becomes the version that "
                "runs when somebody starts a run. Only one version of an agent is active at a time. The Runs section now offers a box for a purpose and a "
                "dataset, because a run has to say what it is for.",
        "note": "The row for <strong>v1</strong> now carries the green label <strong>active</strong>, and the notice <strong>v1 deployed.</strong> "
                "appears at the foot of the screen. Under <strong>Runs</strong> the page says <strong>No runs.</strong> and "
                "<strong>Nobody has started one yet.</strong>",
    },
    {
        "file": "13-devi-uploads-a-version-that-adds-a-tool.png", "actor": "devi",
        "act": ("PART FOUR", "A later version adds a tool that is not on the approved list",
                "A version that lets an agent do more than before is a <strong>widening</strong>. Munitas compares every new version with the one before "
                "it and puts the widest change first. A tool whose name the platform does not know is treated as one that may act outside the platform, and a tool that is not "
                "on the organisation's list of tools approved in advance is decided by people, one version at a time."),
        "title": "Devi uploads a second version that adds a tool called send_confirmation",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the upload form",
        "text": "The clinic wants the agent to be able to send a confirmation to the person who asked for the appointment, so Devi lists a second tool next to the first. "
                "Nothing in the platform says what a tool called send_confirmation does, and it is not on the approved list. Because the agent is held at High, this version "
                "cannot simply be deployed.",
        "note": "<strong>Tools it may call</strong> holds <strong>read_dataset_version, send_confirmation</strong>, and <strong>Model</strong> holds "
                "<strong>none</strong>.",
    },
    {
        "file": "14-the-second-version-waits-for-two-people.png", "actor": "devi",
        "title": "Version 2 is sealed, and it waits for two people while version 1 stays active",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the list of versions",
        "text": "Sealed means finished and closed for good, so the version can never be edited. The new version is listed above the old one. Version 1 is still the one that runs, so nothing changes for anybody until the new "
                "version is approved and deployed. The reason it waits is on the page, in the same words as for the first version.",
        "note": "<strong>v2</strong> lists <strong>Tools:</strong> <strong>read_dataset_version, send_confirmation</strong>, and its <strong>Deploy</strong> "
                "button is greyed out with <strong>This version widens what the agent can do. It waits for an approver of the department that "
                "owns the agent and the AI risk owner.</strong> under it. <strong>v1</strong> keeps the green label <strong>active</strong>.",
    },
    {
        "file": "15-hartley-reads-what-the-version-adds.png", "actor": "hartley",
        "title": "Hartley reads what the version adds, in plain words, before deciding",
        "screen": "Agent changes &middot; /agent-changes",
        "text": "The card says exactly which change makes the version a widening, and why the platform cannot judge it for itself. Hartley writes the "
                "department's reason, which says that the clinic's own system, not the agent, is to send confirmations, and approves.",
        "note": "The card for <strong>version 2</strong> says <strong>Acts outside the platform, or may.</strong> and <strong>Adds the tool "
                "send_confirmation. The platform does not know what it does, so it is treated as one that may act outside the platform.</strong> The box holds "
                "<strong>Cardiology wants confirmations sent by the clinic's own system, never by the agent. This version has to be checked before it goes live.</strong>",
    },
    {
        "file": "16-okafor-reads-it-and-gives-her-part.png", "actor": "okafor",
        "title": "Okafor reads the department's approval and gives the officer's part",
        "screen": "Agent changes &middot; /agent-changes",
        "text": "Okafor sees the department's approval first, with its reason, and then writes the officer's reason. Okafor approves it for a trial on "
                "made-up requests only, and says so. Approval does not deploy the version: it only allows Devi to.",
        "note": "<strong>Waiting for the AI risk owner.</strong> is followed by <strong>Approved</strong> <strong>for the department that owns "
                "the agent by Hartley</strong>. The box is headed <strong>Your reason, for the approval of the AI risk owner</strong> "
                "and holds the reason Okafor wrote: <strong>The platform cannot see what send_confirmation does, so it is treated as an action outside the platform. "
                "Approved for a trial on made-up requests only.</strong>",
    },
    {
        "file": "17-both-people-have-approved.png", "actor": "okafor",
        "title": "Both approvals are recorded, each with who made it, when and why",
        "screen": "Agent changes &middot; /agent-changes",
        "text": "The Approved filter lists the version with both decisions under it. Each decision names the person, the date and time, and the reason in "
                "that person's own words. Version 1 of the same agent, below it, shows the same two decisions from the first half of this page.",
        "note": "The card for <strong>version 2</strong> carries the green label <strong>Approved</strong>, and below it two lines: "
                "<strong>Approved</strong> <strong>for the department that owns the agent by Hartley</strong> and <strong>Approved</strong> "
                "<strong>for the AI risk owner by Okafor</strong>.",
    },
    {
        "file": "18-the-second-version-can-now-be-deployed.png", "actor": "devi",
        "title": "Devi's Deploy button for version 2 now works, and version 1 is still the active one",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the list of versions",
        "text": "The explanation that was under the button has gone, and the button is blue. Nothing has been deployed yet, which means that version 2 "
                "has not been made the running version: the decision to put it to work is Devi's, made when the clinic is ready, and until then the agent keeps running as version 1.",
        "note": "<strong>v2</strong> has a blue <strong>Deploy</strong> button and no explanation under it. <strong>v1</strong> still carries the green "
                "label <strong>active</strong>.",
    },
    {
        "file": "21-the-registry-now-shows-the-sponsor.png", "actor": "okafor",
        "title": "The registry now shows the sponsor, and what still needs attention",
        "screen": "Agent registry &middot; /agent-registry",
        "text": "The earlier flag, No sponsor, is gone. The registry now shows the active version and the tool it may call, and raises a different "
                "flag: a newer version, version 2, has been approved and not deployed. A flag is a prompt for a person to look, not a fault.",
        "note": "The row for <strong>followup-appointment-planner</strong> shows <strong>Sponsor: Hartley</strong> and <strong>Version 1</strong> under "
                "<strong>Active version</strong>. The tag under <strong>Needs attention</strong> reads <strong>A newer version has not been "
                "deployed</strong>.",
    },
]

ACTORS = {
    "devi": ("Devi", "Data engineer in the Health organisation"),
    "okafor": ("Okafor", "AI risk owner in the Health organisation"),
    "hartley": ("Hartley", "Data custodian for the Cardiology department"),
}

PAGE = Page(
    slug="agent-approval", org="health",
    title=series_entry("agent-approval")["title"],
    eyebrow="Health organisation &middot; AI agents",
    lede="Before an AI agent works with patient records, the organisation decides how carefully it is held. The AI risk owner has written risk "
         "levels. A data engineer registers an agent for the Cardiology department and proposes a level, and the AI risk owner confirms the level together "
         "with the agent's first version, after the department's own approver has agreed. Somebody is named to answer for the agent, and it is listed in "
         "the registry. A later version that lets the agent do more needs both people again, and nothing is deployed until they have said yes.",
    actors=ACTORS, steps=STEPS,
    words=["organisation", "department", "custodian", "agent", "agent_version", "sealed", "deploy", "run", "risk_owner", "risk_level", "widening", "approved_tools", "sponsor",
           "agent_registry"],
    shots_dir=SHOTS_ROOT / "agent-approval",
    capture_note="Captured live against the <code>health</code> organisation by <code>web/walkthroughs/agent-approval.spec.ts</code> and built by "
                 "<code>docs/tools/build_agent_approval_walkthrough.py</code>.",
)

if __name__ == "__main__":
    missing = [s["file"] for s in STEPS if not (PAGE.shots_dir / s["file"]).exists()]
    if missing:
        raise SystemExit("No screenshot for: " + ", ".join(missing) + "\nRun the capture first: cd web && npx "
                         "playwright test --config=walkthroughs/playwright.config.ts agent-approval")
    write(PAGE)
