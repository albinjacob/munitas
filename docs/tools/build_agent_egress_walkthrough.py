r"""Build docs/public/walkthroughs/agent-egress-walkthrough.html from captured screenshots.

The screenshots come from web/walkthroughs/agent-egress.spec.ts, which drives the real console against the live Health organisation as Devi (a data
engineer), Okonjo (the custodian of Radiology) and Turner (the network architect). Run that first, then this builder:

    cd web
    npx playwright test --config=walkthroughs/playwright.config.ts agent-egress
    ..\.venv\Scripts\python.exe ..\docs\tools\build_agent_egress_walkthrough.py

This is the second page of "AI agents" in the Health story. It follows one version of an agent from the moment its code names the external hosts it
wants to call, to the decision on that list, to what the decision allows. The capture makes two versions of the same code: the first names a host that
the hospital group's own reporting service runs and is approved, the second names a host nobody can account for and is refused.
"""
from __future__ import annotations

from walkthrough_kit import SHOTS_ROOT, Page, series_entry, write

STEPS = [
    {
        "file": "01-devi-opens-the-agent.png", "actor": "devi",
        "act": ("PART ONE", "A version of an agent names the hosts it wants to call",
                "An AI agent is a program that works on data on somebody's behalf, with a named owner, under the same rules as a person. Before a new "
                "version of an agent can be put to work, Munitas asks one more question: which external computers will this code contact? Those "
                "computers are called hosts, and a host is the name of a computer on the internet, such as reports.radiology.example."),
        "title": "Devi opens the agent that the Radiology department owns",
        "screen": "An agent &middot; /agents/&lt;id&gt;",
        "text": "Devi is a data engineer in the Health organisation. The agent is called radiology-intake-triage, and it ranks radiology intake documents "
                "for a person to review. It belongs to the Radiology department. Each version of an agent is one uploaded "
                "and sealed copy of its code, and a sealed version can never be changed. This agent already has one version, "
                "version @OLD@, uploaded on the page before this one. It calls no external host.",
        "note": "The heading is <strong>radiology-intake-triage</strong>, and under it <strong>Ranks radiology intake documents for human review</strong>. "
                "<strong>Owned by</strong> says <strong>Radiology</strong> and <strong>Registered by</strong> says <strong>Devi</strong>. Under "
                "<strong>Versions</strong> the table has one row, <strong>v@OLD@</strong>, with the green label <strong>active</strong>, the model "
                "<strong>none</strong> and the source path <strong>sandboxed &middot; main.py</strong>.",
    },
    {
        "file": "02-devi-names-the-host.png", "actor": "devi",
        "title": "Devi uploads the code and lists the one host it may call",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the upload form",
        "text": "Devi chooses the zip file that holds the agent's code, says that it calls no model, and opens the part of the form for tools and hosts. "
                "A tool is something the agent may do, here reading a version of a dataset. In this example the code needs to fetch report templates "
                "from the hospital group's own reporting service, so Devi lists that one host. The form says plainly that a list of hosts has to be "
                "approved before the version can be deployed.",
        "note": "The box <strong>Project (.zip)</strong> holds <strong>triage-agent.zip</strong> and <strong>Model</strong> holds <strong>none</strong>. "
                "<strong>Tools it may call</strong> holds <strong>read_dataset_version</strong> and <strong>Hosts it may call</strong> holds "
                "<strong>reports.radiology.example</strong>. Under the hosts box the page says <strong>A non-empty list has to be approved by a "
                "network architect before this version can be deployed.</strong>",
    },
    {
        "file": "03-the-version-waits-for-a-decision.png", "actor": "devi",
        "title": "The version is sealed, and it cannot be deployed yet",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the list of versions",
        "text": "Munitas seals the upload as version 1 and records who registered it and the fingerprints of what it holds. Deploying a version means "
                "making it the one that runs when somebody starts a run. Because this version asked to call a host, it has filed an egress "
                "approval, which is a request for a network architect to decide the list of hosts. Until that is decided, the Deploy button does not work.",
        "note": "The row for <strong>v1</strong> carries a yellow label <strong>egress pending</strong>. Under <strong>What it may call</strong> it lists "
                "the tool and <strong>Hosts:</strong> <strong>reports.radiology.example</strong> with a link <strong>view request</strong>. The "
                "<strong>Deploy</strong> button at the end of the row is greyed out. Below it, <strong>v@OLD@</strong> is still the active version.",
    },
    {
        "act": ("PART TWO", "Somebody else decides",
                "The list of hosts is decided by a network architect, a person who holds a role of that name. The person who uploaded the version "
                "cannot decide it, and neither can a custodian who does not hold the role, even the custodian of the department that owns the agent."),
        "file": "04-devi-cannot-decide-her-own-request.png", "actor": "devi",
        "title": "Devi cannot decide the request that Devi filed",
        "screen": "An egress approval &middot; /egress-approvals/&lt;id&gt;",
        "text": "Devi follows the link to the request. The page shows what the version asked for, and a box for the reason behind a decision. The "
                "platform does not let the person who uploaded a version decide its hosts, so both buttons are greyed out for Devi, and the page says why.",
        "note": "The heading is <strong>radiology-intake-triage v1</strong> with the label <strong>Waiting for a decision</strong>. <strong>Requested "
                "hosts</strong> says <strong>reports.radiology.example</strong>, <strong>Model</strong> says <strong>none</strong>, and "
                "<strong>Submitted by</strong> says <strong>Devi</strong>. Under <strong>Your decision</strong> the page says <strong>You submitted "
                "this version, so somebody else has to decide its requested hosts.</strong> and both <strong>Approve these hosts</strong> and "
                "<strong>Refuse</strong> are greyed out.",
    },
    {
        "file": "05-okonjo-is-not-the-one-who-decides.png", "actor": "okonjo",
        "title": "The custodian who owns the agent cannot decide it either",
        "screen": "An egress approval &middot; /egress-approvals/&lt;id&gt;",
        "text": "Okonjo is the data custodian for the Radiology department, which owns the agent, and decides who may read Radiology's data. Choosing "
                "which external hosts an agent may call is a different decision, and it belongs to a role of its own. Okonjo can open the request but "
                "has no way to decide it.",
        "note": "The same request shows <strong>Waiting for a decision</strong>. Under <strong>Your decision</strong> the page says "
                "<strong>Approving a version's requested hosts is not part of your role.</strong> and both buttons are greyed out.",
    },
    {
        "file": "06-turner-sees-it-waiting.png", "actor": "turner",
        "title": "Turner, the network architect, sees the request waiting",
        "screen": "Home &middot; /",
        "text": "Turner holds the network architect role, so the home page is about one thing: the requests that wait for a decision on hosts. A tile "
                "counts them, and the first of them is listed with the agent, the host it asks to call, who uploaded the version and when.",
        "note": "The heading is <strong>Welcome, Turner</strong>, and the line under it says <strong>You decide which external hosts each agent version "
                "may call.</strong> The first tile says <strong>1</strong> over <strong>Waiting for your decision</strong>. The table lists "
                "<strong>radiology-intake-triage</strong>, <strong>Version 1</strong>, <strong>reports.radiology.example</strong>, <strong>Devi</strong> "
                "and a button <strong>Review</strong>. <strong>Decided lately</strong> says <strong>No decided approvals.</strong>",
    },
    {
        "file": "07-the-queue-of-requests.png", "actor": "turner",
        "title": "The Egress approvals page lists every request, waiting or decided",
        "screen": "Egress approvals &middot; /egress-approvals",
        "text": "The same request is on the Egress approvals page, which keeps every request, those waiting and those already decided. Each line names "
                "the agent version, the hosts it asks for, its state and who submitted it.",
        "note": "The page says <strong>1 waiting on a decision.</strong> The table has the columns <strong>Agent</strong>, <strong>Requested "
                "hosts</strong>, <strong>State</strong>, <strong>Submitted by</strong> and <strong>Decided</strong>, and one row: "
                "<strong>radiology-intake-triage v1</strong>, <strong>reports.radiology.example</strong>, <strong>Waiting for a decision</strong>, "
                "<strong>Devi</strong>.",
    },
    {
        "file": "08-turner-writes-why-and-both-buttons-work.png", "actor": "turner",
        "title": "Turner writes why, and both buttons become available",
        "screen": "An egress approval &middot; /egress-approvals/&lt;id&gt;",
        "text": "Turner opens the request and reads what the version asked for. A decision needs a reason, written before the buttons work, because "
                "whoever reads the decision later will not have this screen in front of them. Turner holds the right role and did not upload the "
                "version, so nothing blocks the decision.",
        "note": "The box under <strong>Why you decided this</strong> holds Turner's reason, <strong>One host, run by the hospital group's own "
                "reporting service. The agent needs it to fetch report templates.</strong> Both <strong>Approve these hosts</strong> and "
                "<strong>Refuse</strong> are now ready to press.",
    },
    {
        "file": "09-turner-approves-the-host.png", "actor": "turner",
        "title": "Turner approves the host",
        "screen": "An egress approval &middot; /egress-approvals/&lt;id&gt;",
        "text": "The decision is recorded with who made it, when, and the reason, which is kept with the decision. The label changes from waiting to "
                "approved. The page also repeats what the approval is: it lets the version be deployed, and it does not claim that any call to the host "
                "has been made.",
        "note": "A notice at the bottom right says <strong>Hosts approved.</strong> The label is now <strong>Approved</strong>, and under <strong>Your "
                "decision</strong> the page says <strong>Approved by Turner on</strong> the date and time, followed by Turner's reason. The line "
                "under <strong>What this version asked for</strong> says <strong>Approving this is what lets the version be deployed; it is not "
                "itself a claim that any request has actually been made yet.</strong>",
    },
    {
        "act": ("PART THREE", "What the decision allows",
                "The decision is the only thing that changes whether a version can be deployed. The same code, with a different list of hosts, can end "
                "up on either side of it."),
        "file": "10-the-approved-version-can-be-deployed.png", "actor": "devi",
        "title": "Back on the agent's page, the approved version can be deployed",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the list of versions",
        "text": "Devi opens the agent again. The label on version 1 has changed from waiting to approved, and the Deploy button, which was greyed out, "
                "now works.",
        "note": "The row for <strong>v1</strong> now carries a green label <strong>egress approved</strong>, and the <strong>Deploy</strong> button at "
                "the end of the row is blue.",
    },
    {
        "file": "11-the-version-is-deployed.png", "actor": "devi",
        "title": "Devi deploys it, and it becomes the version that runs",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the list of versions",
        "text": "Devi presses Deploy. Version 1 is now the active version of the agent, which means it is the one that runs when somebody starts a "
                "run. Version @OLD@, which was active until now, stops being the active version and gets a Deploy button of its own.",
        "note": "A notice at the bottom right says <strong>v1 deployed.</strong> The row for <strong>v1</strong> shows a green label "
                "<strong>active</strong> beside the version, and the Deploy button has gone. The row for <strong>v@OLD@</strong> no longer has the label "
                "<strong>active</strong>, and its own <strong>Deploy</strong> button is now ready.",
    },
    {
        "file": "12-turner-refuses-the-second-host.png", "actor": "turner",
        "title": "The same code, uploaded again with another host, is refused",
        "screen": "An egress approval &middot; /egress-approvals/&lt;id&gt;",
        "text": "Devi uploads the same code again as version 2, this time listing files.unknown-host.example, and the version waits for a decision in "
                "the same way. A script checked that, and there is no picture of it. Turner reads the request and refuses it, with a reason "
                "that says what is missing. The decision is about the list of hosts, not about the code, which is why the same code can be approved "
                "once and refused once.",
        "note": "A notice at the bottom right says <strong>Refused.</strong> The heading is <strong>radiology-intake-triage v2</strong> with the "
                "label <strong>Refused</strong>. <strong>Requested hosts</strong> says <strong>files.unknown-host.example</strong>, and under "
                "<strong>Your decision</strong> the page says <strong>Refused by Turner on</strong> the date and time, followed by the reason: "
                "<strong>Nobody on the platform team can say who runs this host, so it cannot be approved until its owner is named.</strong>",
    },
    {
        "file": "13-the-refused-version-cannot-be-deployed.png", "actor": "devi",
        "title": "The refused version cannot be deployed, and version 1 carries on",
        "screen": "An agent &middot; /agents/&lt;id&gt;, the list of versions",
        "text": "On the agent's page, version 2 is marked as refused and its Deploy button is greyed out. The grey button is not the only barrier: a "
                "script asked Munitas itself to deploy version 2, signed in as Devi, and Munitas refused with the reason that this version asks to "
                "call files.unknown-host.example, that the request is refused, and that a network architect has to approve it before the version "
                "can run. Nothing was deployed, and there is no picture of that check. Version 1 is untouched: it is still the active version, and "
                "it still carries its approval.",
        "note": "The row for <strong>v2</strong> carries a red label <strong>egress refused</strong>, lists <strong>files.unknown-host.example</strong> "
                "and its <strong>Deploy</strong> button is greyed out. The row for <strong>v1</strong> still shows <strong>active</strong> and "
                "<strong>egress approved</strong>, and the row for <strong>v@OLD@</strong> is below them.",
    },
]



def _version_in(picture: str) -> str:
    """The version number the captured screen shows in its heading, read from the text recorded with the picture."""
    import re

    text = (SHOTS_ROOT / "agent-egress" / picture).with_suffix(".txt").read_text(encoding="utf-8")
    found = re.search(r"radiology-intake-triage v(\d+)", text)
    if not found:
        raise SystemExit(f"{picture} does not show a version number in its heading.")
    return found.group(1)


def _renumber(value):
    """This page was written for the two versions it makes, called v1 and v2 in the text below. The agent already has a first version from the page before
    this one, so the capture makes the next two numbers. The text keeps v1 and v2 for those two and @OLD@ for the earlier one, and the numbers come from the screens."""
    import re

    approved = _version_in("04-devi-cannot-decide-her-own-request.png")
    refused = _version_in("12-turner-refuses-the-second-host.png")
    if isinstance(value, str):
        value = re.sub(r"\b([Vv]ersion |v)([12])\b", lambda m: m.group(1) + (approved if m.group(2) == "1" else refused), value)
        return value.replace("@OLD@", "1")
    if isinstance(value, tuple):
        return tuple(_renumber(v) for v in value)
    return value


STEPS = [{key: _renumber(value) for key, value in step.items()} for step in STEPS]

ACTORS = {
    "devi": ("Devi", "Data engineer in the Health organisation"),
    "okonjo": ("Okonjo", "Data custodian for the Radiology department"),
    "turner": ("Turner", "Network architect in the Health organisation"),
}

PAGE = Page(
    slug="agent-egress", org="health",
    title=series_entry("agent-egress")["title"],
    eyebrow="Health organisation &middot; AI agents",
    lede="An AI agent is a program that works on data on somebody's behalf. Before a new version of one can be deployed, the network architect "
         "decides which external hosts it may call. A data engineer uploads a version and lists the one host it needs. Neither the engineer nor the "
         "custodian who owns the agent can decide the list. The network architect approves it with a written reason, and an approved version can "
         "then be deployed. The same code, listing a host that nobody can account for, is refused and cannot be deployed.",
    actors=ACTORS, steps=STEPS,
    words=["organisation", "department", "custodian", "agent", "agent_version", "host", "egress", "network_architect", "deploy", "run"],
    shots_dir=SHOTS_ROOT / "agent-egress",
    capture_note="Captured live against the <code>health</code> organisation by <code>web/walkthroughs/agent-egress.spec.ts</code> and built by "
                 "<code>docs/tools/build_agent_egress_walkthrough.py</code>.",
)

if __name__ == "__main__":
    missing = [s["file"] for s in STEPS if not (PAGE.shots_dir / s["file"]).exists()]
    if missing:
        raise SystemExit("No screenshot for: " + ", ".join(missing) + "\nRun the capture first: cd web && npx "
                         "playwright test --config=walkthroughs/playwright.config.ts agent-egress")
    write(PAGE)
