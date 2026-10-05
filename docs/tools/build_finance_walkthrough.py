r"""Build docs/public/walkthroughs/finance-walkthrough.html from captured screenshots.

The screenshots come from web/walkthroughs/finance.spec.ts, which drives the real console
against the live stack, seeded by scripts/seed/seed-finance-example.py. Run both first:

    .venv\Scripts\python.exe scripts/seed/seed-finance-example.py
    cd web
    npx playwright test --config=walkthroughs/playwright.config.ts finance
    ..\.venv\Scripts\python.exe ..\docs\tools\build_finance_walkthrough.py

Covers the worked example: a transaction batch coming in, a released summary, an agent asking
for access the same way a person does, two custodians each seeing only their own department's
requests, a dataset from another organisation that cannot be opened, and a custodian choosing
how far to trust a request. The page, its look and the wording check come from
walkthrough_kit.py.

The last screenshot the capture takes (18-any-purpose-lease-marked-in-history.png) repeats
the screen of step 17 after a reload, so it is not used here.
"""
from __future__ import annotations

from walkthrough_kit import SHOTS_ROOT, Page, series_entry, write

STEPS = [
    {
        "file": "01-lena-signs-in.png", "actor": "lena",
        "act": ("PART ONE", "Lena brings in a batch of card transactions",
                "Lena is a data engineer in the Fraud Operations department of a finance organisation. A "
                "department is a team inside an organisation that owns some of its data. This part follows "
                "Lena from the sign-in page to a new dataset that nobody may read yet, not even Lena."),
        "title": "Lena signs in",
        "screen": "Sign in &middot; /auth/login",
        "text": "Every person in every organisation that uses Munitas signs in on this one page. Who signs in "
                "decides what the console shows afterwards: a person sees only the data that the person's "
                "own organisation owns.",
        "note": "The email field reads <strong>lena@finance.example</strong>, and the yellow bar at the top "
                "reads <strong>Nobody is signed in</strong>, because the picture is taken "
                "before Lena presses <strong>Sign in</strong>.",
    },
    {
        "file": "02-lena-home.png", "actor": "lena",
        "title": "Lena's home page counts what the Finance organisation holds",
        "screen": "Home &middot; /",
        "text": "The home page counts four things: datasets, sealed versions, versions released for wider use, "
                "and versions held back. A dataset is a named collection of records, and Munitas decides who "
                "may read it. A version is one state of a dataset, and sealed means finished and closed for "
                "good, so it can never be edited.",
        "note": "The panel at the top left reads <strong>Lena, Data engineer, Organisation: finance</strong>, "
                "with the note <strong>You see only what this organisation owns</strong>. The four counts "
                "cover only the Finance organisation.",
    },
    {
        "file": "03-register-filled.png", "actor": "lena",
        "title": "Lena registers a new dataset",
        "screen": "Bring a dataset in &middot; /datasets/register",
        "text": "Bringing a dataset in takes three steps: register it, upload its files, then seal it. Here Lena registers it with a name, an owning department, <strong>Fraud Operations</strong>, and an access level. The access level says how restricted the data is, and <strong>Raw</strong>, the default, is the most restricted.",
        "note": "<strong>Owned by</strong> reads <strong>Fraud Operations</strong>, and the access level reads <strong>Raw (the safe default)</strong>.",
    },
    {
        "file": "04-csv-uploaded.png", "actor": "lena",
        "title": "Lena uploads a file of card transactions",
        "screen": "Bring a dataset in &middot; upload step",
        "text": "After registering, the same page offers to upload files. Lena uploads "
                "<code>transactions.csv</code>, a file of three card transactions. Each row names a cardholder "
                "and gives a full card number, taken from the payment networks' published test numbers, so no "
                "row describes a real person.",
        "note": "A green <strong>Uploaded</strong> label sits next to <strong>transactions.csv</strong> and its "
                "size. The button below reads <strong>Seal this as version 1</strong>, and the text beside it "
                "says that nothing can be added to the version afterwards.",
    },
    {
        "file": "05-sealed-v1.png", "actor": "lena",
        "title": "Version 1 is finished, and Lena cannot read it",
        "screen": "Version detail &middot; /versions/&lt;id&gt;",
        "text": "Sealing closes version 1 for good, and Munitas records a fingerprint of it: a value worked out from the exact contents, which changes if even one byte changes. Lena registered this dataset but cannot read it, because it is more sensitive than Lena's role may read. A request goes to the custodian of the owning department, the person who decides who may read its data.",
        "note": "<strong>Access level when sealed</strong> reads <strong>Raw</strong>, <strong>Fingerprint</strong> shows a value, and <strong>Your access</strong> says that Marcus, the custodian of Fraud Operations, decides.",
    },
    {
        "file": "06-promoted-summary.png", "actor": "lena",
        "act": ("PART TWO", "A summary that more people may read",
                "Raw data stays closed, but a summary of it can be opened more widely once a check has passed. "
                "This part opens a second dataset, <code>card-transaction-summary</code>, that was released "
                "in this way. A release is a decision to let a version be read more widely than before."),
        "title": "Lena opens a summary that more people may read",
        "screen": "Version detail &middot; card-transaction-summary v1",
        "text": "<code>card-transaction-summary</code> was sealed at the <strong>Under review</strong> level and later released to <strong>Published</strong>, the most open level. A release changes who may read a version and moves no data. It was recorded as made by <code>svc-fraud-pipeline</code>, a workload, which is Munitas's word for a program acting on its own.",
        "note": "<strong>Access level now</strong> reads <strong>Published</strong>, and <strong>Where it is stored</strong> says <strong>Unchanged by the release. The data was never copied</strong>. <strong>Release history</strong> shows the evidence for the release.",
    },
    {
        "file": "07-agent-before-upload.png", "actor": "lena",
        "act": ("PART THREE", "A scoring program asks for access, the way a person does",
                "An agent is a program registered on Munitas, with a named owner, that Munitas runs on "
                "somebody's behalf. Registering an agent does not give it any right to read data. When an "
                "agent needs a dataset that is Raw, the run waits until the custodian of the department that "
                "owns the dataset decides."),
        "title": "Lena opens the fraud scoring program",
        "screen": "Agents &middot; /agents",
        "text": "The agent list shows one agent, <code>fraud-transaction-scoring</code>. Its purpose is stated "
                "in words, it belongs to the Fraud Operations department, and its code is kept as sealed "
                "versions, the same way a dataset is.",
        "note": "The row reads <strong>fraud-transaction-scoring</strong>, the purpose <strong>Scores card "
                "transactions for likely fraud before a human reviews them</strong>, the department "
                "<strong>Fraud Operations</strong>, and the newest version number followed by "
                "<strong>sealed</strong>.",
    },
    {
        "file": "08-agent-version-sealed.png", "actor": "lena",
        "title": "Lena uploads a new version of the program's code",
        "screen": "Agent detail &middot; /agents/&lt;id&gt;",
        "text": "Lena uploads a new version of the program's code. The only tool it may use is <code>read_dataset_version</code>, and it calls no AI model. The sample program prints one fixed result, so this walkthrough follows the permission flow and not real scoring. Older versions below come from earlier recordings.",
        "note": "The newest version is at the top, with a <strong>Deploy</strong> button. The version below it is labelled <strong>active</strong>, which marks the one that runs when somebody starts the program.",
    },
    {
        "file": "09-run-warned.png", "actor": "lena",
        "title": "Lena starts a run and is told to wait",
        "screen": "Agent detail &middot; Runs",
        "text": "Lena deploys the new version, then names a purpose for a run, which is one execution of the program, and chooses <code>card-transaction-log</code>, a Raw dataset of Fraud Operations. Munitas warns before the click, not after.",
        "note": "The orange line says the program cannot read that dataset on its own and that Marcus, who looks after Fraud Operations, will be asked. The button reads <strong>Request access and start</strong>.",
    },
    {
        "file": "10-marcus-queue.png", "actor": "marcus",
        "act": ("PART FOUR", "Each custodian sees only the requests for their own department",
                "Marcus and Naomi are both custodians in the Finance organisation, but Marcus decides for "
                "Fraud Operations and Naomi decides for Risk and Compliance. Each sees only the requests to "
                "read data that the custodian's own department owns."),
        "title": "Marcus finds two requests waiting",
        "screen": "Home &middot; the custodian's home page",
        "text": "A request is a written ask to read one dataset for one purpose. Marcus's home page lists those for Fraud Operations data. The first was filed automatically by Lena's run, for the program. The second is Omar's own request for the same Raw dataset, for another purpose.",
        "note": "<strong>Waiting for your decision</strong> counts <strong>2</strong>. Each card says how many hours a grant would last, for that purpose only.",
    },
    {
        "file": "11-run-finished.png", "actor": "lena",
        "title": "Once Marcus agrees, the scoring run finishes",
        "screen": "Agent detail &middot; Runs",
        "text": "Marcus grants the program's request, which creates a lease: permission to read one dataset for one purpose and for a limited time. This page has no screen for that click. The paused run then resumes by itself, in a container, an isolated environment with no network access of its own.",
        "note": "The newest run reads <strong>succeeded</strong>, with <strong>0 tool calls</strong>, and prints <code>3 transactions scored, 1 flagged for manual review</code>.",
    },
    {
        "file": "12-naomi-queue.png", "actor": "naomi",
        "title": "Naomi sees a different list, for Risk and Compliance",
        "screen": "Home &middot; the custodian's home page",
        "text": "Naomi is the custodian of the Risk and Compliance department. The list holds one request, from "
                "Lena, to read a dataset named <code>kyc-identity-documents</code>, which Risk and Compliance "
                "owns. Neither the agent's request nor Omar's request appears here, because both are for "
                "Fraud Operations data.",
        "note": "<strong>Waiting for your decision</strong> counts <strong>1</strong>, and the card names "
                "<strong>kyc-identity-documents v1</strong>. The section <strong>What you are answerable "
                "for</strong> reads <strong>2 datasets belong to Risk &amp; Compliance</strong>.",
    },
    {
        "file": "13-deep-link-refused.png", "actor": "naomi",
        "title": "Naomi cannot open another organisation's dataset",
        "screen": "Version detail &middot; an address copied from the Health organisation",
        "text": "Naomi pastes the address of a version that belongs to a different organisation on the same "
                "platform, the Health organisation. The console answers that no such version exists. It gives "
                "the same answer for an address that was never valid, so it does not even confirm that the "
                "version is there.",
        "note": "The red panel reads <strong>Could not load this version</strong> and, under it, "
                "<code>no such dataset version</code>. The panel at the top left still reads <strong>Naomi, "
                "Data custodian for Risk &amp; Compliance, Organisation: finance</strong>.",
    },
    {
        "file": "14-omar-finds-a-request-form.png", "actor": "omar",
        "act": ("PART FIVE", "The custodian chooses what a grant covers",
                "A lease normally covers one purpose, the one written in the request. For data that is not "
                "Raw, the custodian may instead cover any purpose while the lease lasts. This part shows "
                "that choice. It is not offered for Raw data, as the other request in the same list shows."),
        "title": "Omar opens a monthly digest that has been through review",
        "screen": "Version detail &middot; fraud-analytics-digest",
        "text": "Omar is an analyst. The digest was released one step, from Raw to <strong>Under review</strong>, which sits between Raw and Published. It is still more sensitive than an analyst may read, so a lease is needed. The form asks for a purpose, a reason and a length of time.",
        "note": "<strong>Access level now</strong> reads <strong>Under review</strong>. The form has no way to choose what the lease covers, because only the custodian chooses that.",
    },
    {
        "file": "15-marcus-sees-omars-request.png", "actor": "marcus",
        "title": "Marcus is offered a choice on the digest request",
        "screen": "Home &middot; the custodian's home page",
        "text": "Omar wrote a purpose and a reason, and asked for 24 hours. Marcus's list now shows the "
                "request for the digest first. It carries two options that the card for the Raw dataset "
                "below it does not have.",
        "note": "The digest card reads <strong>Omar (Analyst) wants to read fraud-analytics-digest</strong> and "
                "carries the label <strong>Under review</strong>, with two options: <strong>This purpose "
                "only</strong>, selected, and <strong>Any purpose, while this lasts</strong>. The card below "
                "it, for <strong>card-transaction-log v1</strong>, which is <strong>Raw</strong>, has no "
                "options.",
    },
    {
        "file": "16-marcus-chooses-any-purpose.png", "actor": "marcus",
        "title": "Marcus chooses Any purpose",
        "screen": "Home &middot; the custodian's home page",
        "text": "Choosing <strong>Any purpose, while this lasts</strong> changes what the lease will cover. "
                "Marcus is still choosing, and the page already states in words what granting would now do.",
        "note": "The line beside the buttons now reads <strong>Granting gives Omar 24 hours, for any purpose "
                "while it lasts, and it ends by itself</strong>. Before the choice it read <strong>for this "
                "purpose only</strong>.",
    },
    {
        "file": "17-any-purpose-granted.png", "actor": "marcus",
        "title": "Marcus grants it, and the record says what it covers",
        "screen": "Home &middot; the custodian's home page",
        "text": "The request moves to the top of <strong>What you have decided</strong>, with a label that records the choice. Separately, the capture script asked the platform whether Omar could read the digest for a purpose that nobody mentioned when Marcus approved, an unplanned spot-check, and the policy allowed it. That check has no screen of its own.",
        "note": "The top decision reads <strong>Granted</strong>, with <strong>covers any purpose</strong>, a time until which it stays open, and a <strong>Revoke</strong> link.",
    },
]

ACTORS = {
    "lena": ("Lena", "Data engineer, Fraud Operations department"),
    "marcus": ("Marcus", "Data custodian for the Fraud Operations department"),
    "omar": ("Omar", "Analyst in the Finance organisation"),
    "naomi": ("Naomi", "Data custodian for the Risk and Compliance department"),
}

# Short pictures keep the part of the screen that matters. Cropped from the same pixels, nothing redrawn.
CROPS = {
    "01-lena-signs-in.png": (0, 0, 1920, 440),
    "02-lena-home.png": (320, 0, 1600, 380),
    "10-marcus-queue.png": (320, 0, 1600, 720),
    "12-naomi-queue.png": (320, 0, 1600, 830),
    "13-deep-link-refused.png": (320, 0, 1600, 340),
    "14-omar-finds-a-request-form.png": (320, 0, 1600, 940),
    "15-marcus-sees-omars-request.png": (320, 0, 1600, 730),
    "16-marcus-chooses-any-purpose.png": (320, 0, 1600, 730),
    "17-any-purpose-granted.png": (320, 0, 1600, 830),
}

PAGE = Page(
    slug="finance", org="finance",
    title=series_entry("finance")["title"],
    eyebrow="Finance organisation &middot; Card transactions",
    lede="A fraud operations team brings in a batch of card transactions that nobody may read yet. A scoring agent "
         "has to ask the custodian of the owning department before it can read them, and a second custodian in "
         "the same organisation sees none of it. The last part shows a custodian deciding how far to trust a "
         "request.",
    actors=ACTORS, steps=STEPS, crops=CROPS,
    words=["organisation", "department", "dataset", "version", "sealed", "access_level", "fingerprint",
           "custodian", "release", "request", "purpose", "lease", "agent", "run", "container"],
    shots_dir=SHOTS_ROOT / "finance",
    capture_note="Captured live against the <code>finance</code> organisation by "
                 "<code>web/walkthroughs/finance.spec.ts</code> and built by "
                 "<code>docs/tools/build_finance_walkthrough.py</code>.",
)

if __name__ == "__main__":
    missing = [s["file"] for s in STEPS if not (PAGE.shots_dir / s["file"]).exists()]
    if missing:
        raise SystemExit("No screenshot for: " + ", ".join(missing) + "\nRun the capture first: cd web && npx "
                         "playwright test --config=walkthroughs/playwright.config.ts finance")
    write(PAGE)
