r"""Build docs/public/walkthroughs/agent-levels-walkthrough.html from captured screenshots.

The screenshots come from web/walkthroughs/agent-levels.spec.ts, which drives the real console against the live Health organisation as Okafor (the AI risk owner)
and Imani (the de-identification reviewer). Run that first, then this builder:

    cd web
    npx playwright test --config=walkthroughs/playwright.config.ts agent-levels
    ..\.venv\Scripts\python.exe ..\docs\tools\build_agent_levels_walkthrough.py

This is the third page of "AI agents" in the Health story, before the page that approves a new agent. The capture changes one number in the live list, from 180 days to 120 for
agents at Moderate, saves it as a new version, and afterwards writes the original list back as one more version, so that the other Health pages still read as they were
captured. Captured twice on the same Health organisation, so the history holds version 1, written when agents were first held to the levels, the trial of the first capture (version 2) and its write-back (version 3), and the version this capture saves (version 4). Picture 08 is the page of an Oncology agent held at Moderate.
"""
from __future__ import annotations

from walkthrough_kit import SHOTS_ROOT, Page, series_entry, write

STEPS = [
    {
        "file": "01-okafor-opens-the-levels.png", "actor": "okafor",
        "act": ("PART ONE", "The organisation writes down how carefully each kind of agent is held",
                "An AI agent is a program that works on data on somebody's behalf. Some agents only tidy appointment reminders. Others write notes about patients. "
                "An organisation does not want to decide how careful to be one agent at a time, so it writes a short list of <strong>risk levels</strong>, from the "
                "least strict to the most strict, and every agent is placed on it."),
        "title": "Okafor, the AI risk owner, opens the risk levels that the Health organisation holds its agents to",
        "screen": "Risk levels &middot; /risk-levels",
        "text": "Okafor is the AI risk owner of the Health organisation, the one person who answers for how the organisation holds its AI agents. Writing the list of levels belongs "
                "to that role. The person who registers an agent proposes a level for it, and the AI risk owner confirms it. Each level says three things: whether a change that lets "
                "an agent do more needs a second approver, how often somebody has to confirm that the agent is still needed, and whether an agent at that level may be deployed at all. "
                "To deploy an agent version is to make it the one that runs. The list in force is version 1, written when the organisation first held its agents to levels. Every later change adds a version, and every earlier version is kept.",
        "note": "The heading is <strong>Risk levels</strong>. Under <strong>The levels in force</strong> the page says <strong>Version 1 by Okafor on</strong> a date and time, "
                "followed by a reason. The table has four rows: <strong>Low</strong>, <strong>Moderate</strong>, <strong>High</strong> and <strong>Not allowed</strong>. "
                "<strong>Moderate</strong> reads <strong>180 days</strong> under <strong>Re-confirm every</strong>, and <strong>High</strong> has <strong>Yes</strong> under "
                "<strong>Second approver</strong>. The button <strong>Change the levels</strong> is offered.",
    },
    {
        "file": "02-okafor-opens-the-editor.png", "actor": "okafor",
        "title": "Okafor opens the editor, where every level has its own settings",
        "screen": "Risk levels &middot; /risk-levels, the editor",
        "text": "Each level has a name, a label for the law or standard it corresponds to, a sentence on what an agent at that level is like, and the number of days "
                "after which the agent has to be confirmed again. Two choices complete it: a second approver is needed to widen an agent at that level, and an agent at that level may "
                "not be deployed. A widening is a change that lets an agent do more than the version before it. The level called Not allowed has the second choice ticked, "
                "so no version of an agent at that level can be deployed.",
        "note": "The block <strong>Level 2</strong> holds <strong>Moderate</strong>. <strong>Re-confirm every (days), empty for never</strong> holds <strong>180</strong>, and "
                "<strong>A second approver is needed to widen an agent at this level</strong> is not ticked. In <strong>Level 4</strong>, <strong>Not allowed</strong>, "
                "<strong>An agent at this level may not be deployed</strong> is ticked.",
    },
    {
        "file": "03-okafor-changes-moderate-to-120-days.png", "actor": "okafor",
        "act": ("PART TWO", "A change is saved as a new version, and the old one is kept",
                "A list of levels is never edited in place. Every save adds a new version, with the name of the person who made it, the date and time, and a written reason."),
        "title": "Okafor changes Moderate from 180 days to 120",
        "screen": "Risk levels &middot; /risk-levels, the editor",
        "text": "Agents at Moderate were going a long time between checks. Re-confirming means that somebody checks that the agent is still needed and still held at the right "
                "level. So Okafor shortens the time for Moderate from 180 days to 120. Only this one number changes. The other levels stay as they were.",
        "note": "In <strong>Level 2</strong>, <strong>Moderate</strong>, the box <strong>Re-confirm every (days), empty for never</strong> now holds <strong>120</strong>. In "
                "<strong>Level 3</strong>, <strong>High</strong>, it still holds <strong>90</strong>.",
    },
    {
        "file": "04-okafor-says-why-and-saves.png", "actor": "okafor",
        "title": "Okafor writes why, and saves the list as a new version",
        "screen": "Risk levels &middot; /risk-levels, the foot of the editor",
        "text": "Further down the same form, Okafor writes the reason for the change. The platform will not save a list without one. The switch that holds agents to the levels stays "
                "ticked: an agent then needs a confirmed level before it can start a run, and a version that widens an agent has to be approved before it is deployed.",
        "note": "The box <strong>Why are the levels written this way?</strong> holds <strong>Agents at Moderate were going a long time between checks, so they are now re-checked "
                "every 120 days instead of 180.</strong> The button <strong>Save as a new version</strong> is dark.",
    },
    {
        "file": "05-the-new-version-is-saved.png", "actor": "okafor",
        "title": "The list is saved as a new version, with who made it, when and why",
        "screen": "Risk levels &middot; /risk-levels",
        "text": "The page now shows the new version as the list in force. The reason sits next to the name and the date, so that a reader who was not there can see why the "
                "organisation changed its rule. Only the Moderate row differs from before.",
        "note": "<strong>Version 2 by Okafor on</strong> a date and time is followed by <strong>Reason:</strong> and what Okafor wrote. The row for <strong>Moderate</strong> "
                "reads <strong>120 days</strong>, and the link reads <strong>Show the 1 earlier version</strong>.",
    },
    {
        "file": "06-the-history-keeps-both-versions.png", "actor": "okafor",
        "title": "The earlier versions are kept, so anybody can see what the rule was and when it changed",
        "screen": "Risk levels &middot; /risk-levels, the earlier versions",
        "text": "A decision made earlier was judged under the list in force at the time. Because every version stays, an auditor can always say which rule applied on a "
                "given day. The page leaves out the version in force, which is shown above, and lists the one before it, "
                "the first list. The history shows what the rule was before the change.",
        "note": "<strong>Version 1 by Okafor on</strong> heads a table. "
                "<strong>Moderate</strong> reads <strong>180 days</strong>, the number before the change. The link "
                "<strong>Hide the earlier versions</strong> is above it.",
    },
    {
        "file": "07-imani-can-read-the-list-and-not-change-it.png", "actor": "imani",
        "act": ("PART THREE", "Who may change the list, and what a change does",
                "The levels decide how strictly agents are held, so the people who judge agents must not be able to relax the rules that apply to them."),
        "title": "Imani can read the list and has no way to change it",
        "screen": "Risk levels &middot; /risk-levels",
        "text": "Imani is a de-identification reviewer in the Health organisation, a person who reviews data from which identifying details have been removed, and who may also check what an agent produced. Every person signed in can read "
                "the list, so that anybody can see what an agent is held to. Only the AI risk owner can set it, and the page says so in place of a button.",
        "note": "<strong>Version 2 by Okafor on</strong> is shown with the table. There is no button to change the levels. Under the table the page says "
                "<strong>Only the AI risk owner may set the risk levels.</strong>",
    },
    {
        "file": "08-the-change-reaches-an-agent-held-at-moderate.png", "actor": "okafor",
        "title": "The change reaches an agent that is held at Moderate",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the Sponsor and re-confirmation section",
        "text": "Okafor follows the agent care-gap-finder from the Agent registry. It belongs to the Oncology department and is held at Moderate, and its sponsor, the person who "
                "answers for it, is Mensah, an approver for Oncology. An agent keeps the level it was confirmed at, but how often it has to be confirmed again follows the list in "
                "force. So the page now says every 120 days, and the date by which it is due again is 60 days earlier than it would have been under 180. The card lower down is the "
                "record of the agent's first version, approved when the agent was set up: it names who approved for the department and who approved for the AI risk owner.",
        "note": "<strong>Sponsor: Mensah, assigned by Mensah on</strong> a date and time. Below it the page says <strong>Its level asks for re-confirmation every 120 days.</strong> "
                "followed by <strong>Last confirmed</strong> and <strong>Due</strong>, each with a date and time. In the card for <strong>version 1</strong> the second decision reads "
                "<strong>Approved</strong> <strong>for the AI risk owner by Okafor on</strong> a date and time.",
    },
]

ACTORS = {
    "okafor": ("Okafor", "AI risk owner in the Health organisation"),
    "imani": ("Imani", "De-identification reviewer in the Health organisation"),
}

PAGE = Page(
    slug="agent-levels", org="health",
    title=series_entry("agent-levels")["title"],
    eyebrow="Health organisation &middot; AI agents",
    lede="The AI risk owner writes a short list of risk levels that says how strictly each kind of AI agent is held: whether a change needs a second approver, how often the "
         "agent has to be confirmed again, and whether it may be deployed at all. A change to the list is saved as a new version with who made it, when and why, and the earlier "
         "version is kept. Everybody signed in can read the list, nobody else can change it, and a change reaches the agents that are held at the level it touches.",
    actors=ACTORS, steps=STEPS,
    words=["organisation", "agent", "risk_level", "widening", "sponsor", "agent_registry", "risk_owner", "deid_reviewer", "deploy"],
    shots_dir=SHOTS_ROOT / "agent-levels",
    capture_note="Captured live against the <code>health</code> organisation by <code>web/walkthroughs/agent-levels.spec.ts</code> and built by "
                 "<code>docs/tools/build_agent_levels_walkthrough.py</code>.",
)

if __name__ == "__main__":
    missing = [s["file"] for s in STEPS if not (PAGE.shots_dir / s["file"]).exists()]
    if missing:
        raise SystemExit("No screenshot for: " + ", ".join(missing) + "\nRun the capture first: cd web && npx "
                         "playwright test --config=walkthroughs/playwright.config.ts agent-levels")
    write(PAGE)
