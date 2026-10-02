r"""Build docs/public/walkthroughs/legal-export-walkthrough.html from captured screenshots and a recorded terminal.

The screens and the terminal come from web/walkthroughs/legal-export.spec.ts, which drives the real console against the
live stack. Rebuild Harbour Clinic, capture, then build:

    .\scripts\seed\reseed-tenant.ps1 -Tenant harbour -Force
    cd web
    npx playwright test --config=walkthroughs/playwright.config.ts legal-export
    ..\.venv\Scripts\python.exe ..\docs\tools\build_legal_export_walkthrough.py

The page, its look and the wording check come from walkthrough_kit.py.
"""
from __future__ import annotations

import html
import json

from walkthrough_kit import SHOTS_ROOT, Page, series_entry, write

SHOTS = SHOTS_ROOT / "legal-export"
TERMINAL = json.loads((SHOTS / "terminal.json").read_text(encoding="utf-8")) if (SHOTS / "terminal.json").exists() else {"blocks": []}

CLOCK = (
    "To start this story the clinic was closed down through the platform and the first 15 days were skipped with "
    "<code>scripts/admin/advance-closing-clock.py</code>, which moves the clinic&rsquo;s two closing dates back and "
    "changes nothing else. The dates on the screens are therefore earlier than they would be on a real calendar."
)

STEPS = [
    {
        "file": "01-priya-finds-the-hold-in-force.png", "actor": "priya",
        "act": ("PART ONE", "A court demands the records",
                "This story starts where the walkthrough about closing down an organisation ends. Harbour Clinic "
                "is closing down, and a legal hold is in force on it. A legal hold is an order to keep an "
                "organisation&rsquo;s records and not delete them. It only keeps them. Now the High Court has "
                "demanded the records of the patient who is making the claim, together with the log of who read "
                "them. Producing records is a different act from keeping them, so the platform gives it its own "
                "steps. Three different people each do one step, and none of them reads the records. Priya is a "
                "platform administrator, a person who runs Munitas itself and who has no say over who may read "
                "any department&rsquo;s data. " + CLOCK),
        "title": "Priya finds the legal hold in force, and a way to ask for records",
        "screen": "Legal holds &middot; /legal-holds, the hold in force on harbour",
        "text": "Under every legal hold that is in force there is a section called Producing records for this "
                "matter. A hold that is only waiting for approval, or that has been released, has no such "
                "section, because the platform produces records only while a hold stands. The section repeats "
                "the three steps in one sentence before it offers anything.",
        "note": "The card for <strong>HC-2026-0417: Alder v Harbour Clinic</strong> carries the red label "
                "<strong>In force</strong>. Below the notice, the section <strong>Producing records for this "
                "matter</strong> holds one button, <strong>Ask for records to be produced</strong>. The sentence "
                "under the heading reads <strong>Nobody reads the records: the platform builds an encrypted, "
                "signed package</strong>.",
    },
    {
        "file": "02-priya-fills-in-the-demand.png", "actor": "priya",
        "title": "Priya copies the demand into the form",
        "screen": "Legal holds &middot; the request form under the hold",
        "text": "The form asks for what the court sent, in the court&rsquo;s words: who is demanding the records, "
                "the reference of the demand, its date, and what it asks for. It also asks who receives the "
                "package. Then Priya chooses which datasets are included. A dataset is the platform&rsquo;s name "
                "for a registered, named collection of records. Choosing a dataset includes every sealed version "
                "of it, and a sealed version is a finished state of a dataset that can never be edited. The "
                "platform offers nothing narrower than a whole dataset, because deciding what is relevant or "
                "private is the lawyers&rsquo; work and not the platform&rsquo;s.",
        "note": "The fields read: demanded by <strong>High Court, King&rsquo;s Bench Division</strong>, "
                "reference <strong>KB-2026-004411</strong>, recipient <strong>Ruth Aldous</strong> of "
                "<strong>Aldous and Brennan LLP</strong>. Both datasets are ticked, "
                "<strong>appointment-reminders</strong> and <strong>discharge-letters</strong>, each with its "
                "number of versions and size, and so is the audit trail, the list of who was allowed or refused "
                "what for those datasets.",
    },
    {
        "file": "03-the-request-waits.png", "actor": "priya",
        "title": "The request is recorded, and it waits for a second administrator",
        "screen": "Legal holds &middot; Producing records for this matter",
        "text": "Nothing has been built yet. The request is stored with who asked and when, and it waits for a "
                "different platform administrator. The first thing it records is the audit trail, the permanent "
                "list of every decision the platform makes, so the request itself is evidence that somebody "
                "asked.",
        "note": "A card with the demand&rsquo;s reference <strong>KB-2026-004411</strong> carries the amber "
                "label <strong>Waiting for a second administrator</strong>. It lists <strong>Demanded by</strong>, "
                "<strong>Asks for</strong>, <strong>Goes to</strong> and <strong>Asked for by Priya</strong>. "
                "Under the buttons: <strong>You asked for this one, so a different administrator approves "
                "it</strong>.",
    },
    {
        "file": "04-priya-cannot-approve-her-own.png", "actor": "priya",
        "title": "Priya presses Approve on the request that Priya made, and is refused",
        "screen": "Legal holds &middot; Producing records for this matter",
        "text": "The page draws the Approve button for every platform administrator, and the platform decides "
                "when it is pressed. It refuses the person who asked. The same rule is written into the "
                "database as a check, so a fault in the program could not bypass it. The refusal is also "
                "written to the audit trail.",
        "note": "A red panel reads <strong>Could not decide this export</strong> and gives the reason "
                "<strong>an export is approved by a different platform administrator from the one who asked for "
                "it</strong>. The status is unchanged, still <strong>Waiting for a second administrator</strong>.",
    },
    {
        "file": "05-ravi-reads-the-demand.png", "actor": "ravi",
        "act": ("PART TWO", "A second platform administrator approves it",
                "Ravi is the second platform administrator. Ravi and Priya hold the same role, and the rule is "
                "only that the two are different people."),
        "title": "Ravi reads the demand and writes a note",
        "screen": "Legal holds &middot; Producing records for this matter",
        "text": "Ravi sees the same waiting request with the whole demand in front of the decision. The note is "
                "stored with the decision. It is required only when a request is declined.",
        "note": "The name at the top left is <strong>Ravi</strong>. The note field reads <strong>Demand checked "
                "against the court&rsquo;s reference</strong>, and both <strong>Approve it</strong> and "
                "<strong>Decline</strong> are offered. There is no line saying that Ravi asked for this one.",
    },
    {
        "file": "06-ravi-approves.png", "actor": "ravi",
        "title": "Ravi approves, and the request moves to the custodian",
        "screen": "Legal holds &middot; Producing records for this matter",
        "text": "Approval is the second of three steps. The platform administrators have done their part, and "
                "nothing is built yet, because the person who answers for the records has not confirmed what "
                "is included.",
        "note": "The label now reads <strong>Waiting for the custodian to confirm the scope</strong>, and the "
                "card adds <strong>Approved by Ravi</strong> with the note.",
    },
    {
        "file": "07-adeyemi-is-asked-to-confirm-the-scope.png", "actor": "adeyemi",
        "act": ("PART THREE", "The custodian confirms the scope, and is given the passphrase",
                "Adeyemi is the clinic&rsquo;s data protection officer, and the legal hold names Adeyemi as its "
                "temporary custodian, the person who answers for the kept records while the hold stands. The "
                "clinic is in its second stage, so the usual screens are closed to Adeyemi. The one thing still "
                "open is what concerns the hold."),
        "title": "Adeyemi is asked whether the scope is right",
        "screen": "Closing notice &middot; shown in place of every other screen",
        "text": "Besides the hold, the notice now shows the export that waits for the custodian. The custodian "
                "sees the whole demand, who asked and who approved, and the names of the datasets. Only the "
                "custodian can confirm that what is asked for matches the demand and goes no further, because "
                "the custodian answers for the records. Neither platform administrator can.",
        "note": "A box titled <strong>Records being produced for a legal matter</strong> holds the card "
                "<strong>KB-2026-004411: matter HC-2026-0417</strong>. It says <strong>The datasets named: "
                "appointment-reminders, discharge-letters</strong>. Two buttons are offered: <strong>The scope "
                "is right</strong> and <strong>It goes further than the demand</strong>. The note field reads "
                "<strong>Both of the datasets named in the demand, and nothing more</strong>.",
    },
    {
        "file": "08-the-package-is-ready.png", "actor": "adeyemi",
        "title": "The package is built, and the passphrase is waiting",
        "screen": "Closing notice &middot; Records being produced for a legal matter",
        "text": "Confirming starts a background job. It copies the files of the named datasets, checks each one "
                "against the fingerprint recorded when it was sealed, and writes them into one package that it "
                "signs and encrypts. No person opens the package, and no platform administrator can, because "
                "the secret that opens it, called a passphrase, is held back for the custodian.",
        "note": "The label reads <strong>Ready</strong>. The card adds <strong>Scope confirmed by Adeyemi</strong>, "
                "<strong>Package: 5 files, 3 KB, encrypted</strong> and <strong>Kept until October 17, "
                "2026</strong>, after which the platform deletes the package. A button reads <strong>Show me "
                "the passphrase, once</strong>.",
    },
    {
        "file": "09-adeyemi-reads-the-passphrase.png", "actor": "adeyemi",
        "title": "Adeyemi reads the passphrase, once",
        "screen": "Closing notice &middot; Records being produced for a legal matter",
        "text": "The passphrase is shown on this screen and nowhere else. The platform does not keep it after "
                "this, so it cannot be shown again and cannot be recovered. The custodian passes it to the "
                "recipient separately from the download link, so that holding the file alone is not enough to "
                "open it.",
        "note": "A yellow box shows the passphrase, five groups of letters and digits, and the line "
                "<strong>Shown once and not kept. Give it to the recipient separately from the download "
                "link</strong>. The button has gone.",
    },
    {
        "file": "10-priya-sees-what-the-package-holds.png", "actor": "priya",
        "act": ("PART FOUR", "A platform administrator makes the download link",
                "Delivery is a person&rsquo;s act, because how a court or a law firm wants to receive a package "
                "is not the platform&rsquo;s decision. The platform makes a package available and records "
                "every download."),
        "title": "Priya sees what the package holds, without opening it",
        "screen": "Legal holds &middot; Producing records for this matter, with the list of files",
        "text": "A platform administrator never sees the records, but sees what is in the package. The list is "
                "called the manifest. It names every file and gives its size and a fingerprint of its contents, "
                "which is a value that changes if even one byte of the file changes. The platform signs the "
                "manifest, so any later change to a file or to the list can be noticed.",
        "note": "The status is <strong>Ready</strong>. The table lists five files, three under "
                "<strong>appointment-reminders/v1</strong> and two under <strong>discharge-letters/v1</strong>, "
                "each with a size and the start of its <strong>SHA-256 fingerprint</strong>. The button "
                "<strong>Make a download link</strong> is below the table.",
    },
    {
        "file": "11-priya-makes-the-link.png", "actor": "priya",
        "title": "Priya makes a download link",
        "screen": "Legal holds &middot; Producing records for this matter",
        "text": "The link is shown once. It works three times and for seven days at most, and each use is "
                "written to the audit trail. The package is encrypted, so the link alone opens nothing. The "
                "recipient also needs the passphrase that only Adeyemi was given.",
        "note": "A box under the button shows the link, beginning with the platform&rsquo;s address and "
                "<code>/legal-exports/download/mlx_</code>, and the line <strong>Works 3 times until "
                "October 10, 2026. It is not shown again</strong>.",
    },
    {
        "terminal": [0], "actor": "ruth",
        "act": ("PART FIVE", "The recipient opens the package",
                "Ruth Aldous is a lawyer at the firm that is to receive the package. The platform has finished "
                "its part. Ruth has been sent the link and, separately, the passphrase, and works on Ruth&rsquo;s "
                "own computer with a small program that comes with Munitas, "
                "<code>open_legal_package.py</code>. The program shares no code with the platform, so that the "
                "format of the package is the only thing the two have in common. What follows was recorded by "
                "running the real commands. The link and the passphrase are shown as placeholders here, because "
                "they are secrets."),
        "title": "Ruth asks the platform for the key that signs its packages",
        "screen": "Terminal &middot; on the recipient&rsquo;s computer",
        "text": "The platform publishes the public half of its signing key. A public key lets anybody check a "
                "signature and lets nobody make one. Ruth asks for it directly from the platform, so that the "
                "check below does not rest on anything that came inside the package.",
        "note": "The answer names the algorithm, <strong>ed25519</strong>, and gives the key as a long string "
                "of letters and digits.",
    },
    {
        "terminal": [1, 2], "actor": "ruth",
        "title": "Ruth downloads the package and opens it",
        "screen": "Terminal &middot; on the recipient&rsquo;s computer",
        "text": "The first command saves the file the link hands over. The second decrypts it with the "
                "passphrase, unpacks it, and checks three things: the manifest&rsquo;s signature against the "
                "platform&rsquo;s key, every file against the size and fingerprint in the manifest, and that no "
                "file is present that the manifest does not list. Any failure makes the program say so and "
                "stop.",
        "note": "The program prints one line, beginning <strong>OK: 5 files for matter HC-2026-0417</strong> "
                "and ending <strong>Signature verified</strong>. A wrong passphrase, a changed byte, a file cut "
                "short or a key that is not the platform&rsquo;s each make it print a problem instead.",
    },
    {
        "terminal": [3], "actor": "ruth",
        "title": "Ruth looks at what the package holds",
        "screen": "Terminal &middot; on the recipient&rsquo;s computer",
        "text": "Besides the five files, the package holds the manifest and its signature, the chain of custody, "
                "the audit trail, and a list of records that no longer exist. The chain of custody is the "
                "record of who asked, who approved and who confirmed, and when. The audit trail is the list "
                "of who was allowed or refused what for the named datasets, as the demand asked.",
        "note": "The listing shows <strong>audit-trail.csv</strong>, <strong>chain-of-custody.json</strong>, "
                "<strong>erased.json</strong>, <strong>manifest.json</strong>, <strong>manifest.sig</strong> and "
                "the five files under <strong>data</strong>.",
    },
    {
        "terminal": [4], "actor": "ruth",
        "title": "A file is exactly what was stored",
        "screen": "Terminal &middot; on the recipient&rsquo;s computer",
        "text": "The platform does not convert, redact or reformat anything. It copies each file byte for "
                "byte, and it stops with an error rather than hand over a file whose bytes no longer match the "
                "fingerprint recorded when the version was sealed. Deciding what is private or privileged is "
                "left to the lawyers.",
        "note": "The letter reads <strong>Dear Ms Alder, this is a reminder of your appointment on 14 November "
                "at 10:30</strong>, the same words that were uploaded to the clinic&rsquo;s department Patient "
                "Records.",
    },
]

ACTORS = {
    "priya": ("Priya", "Platform administrator, who asks for the records"),
    "ravi": ("Ravi", "A second platform administrator, who approves"),
    "adeyemi": ("Adeyemi", "Data protection officer at Harbour Clinic, the custodian the hold names"),
    "ruth": ("Ruth Aldous", "Lawyer at Aldous and Brennan LLP, who receives the package"),
}


def evidence(step: dict) -> str:
    boxes = []
    for index in step["terminal"]:
        block = TERMINAL["blocks"][index]
        if block.get("kind") == "shown":
            boxes.append(
                '<div class="term">'
                f'<span class="tag who">{html.escape(block["what"])}</span>'
                f'<pre class="out">{html.escape(block["output"].rstrip())}</pre>'
                "</div>")
            continue
        boxes.append(
            '<div class="term">'
            f'<span class="tag who">Typed by Ruth, in {block["where"]}</span>'
            f'<pre class="code">{html.escape(block["command"])}</pre>'
            '<span class="tag">What came back</span>'
            f'<pre class="out">{html.escape(block["output"].rstrip() or "(nothing is printed)")}</pre>'
            "</div>")
    return "\n".join(boxes)


PAGE = Page(
    slug="legal-export", org="harbour",
    title=series_entry("legal-export")["title"],
    eyebrow="Harbour Clinic &middot; Export for a legal matter",
    lede="A court demands a patient&rsquo;s records from a clinic that is closing down, and a legal hold already "
         "keeps them. One platform administrator asks for them, a different one approves, and the custodian the "
         "hold names confirms what is included. The platform builds a package that is signed and encrypted, "
         "and the custodian alone is given the passphrase. The recipient opens it on their own computer with "
         "a program that checks it.",
    actors=ACTORS, steps=STEPS,
    words=["organisation", "department", "dataset", "sealed", "custodian", "platform_admin", "legal_hold",
           "temp_custodian", "dpo", "legal_export", "manifest", "passphrase", "fingerprint"],
    has_code=True, evidence=evidence,
    shots_dir=SHOTS,
    capture_note="Captured live against the <code>harbour</code> organisation by "
                 "<code>web/walkthroughs/legal-export.spec.ts</code> and built by "
                 "<code>docs/tools/build_legal_export_walkthrough.py</code>. The terminal part is the real output "
                 "of the real commands. What the platform refuses is proved separately by "
                 "<code>verify/v102_legal_export_rules.py</code> and <code>verify/v103_legal_export_package.py</code>, "
                 "which also open a package with the recipient&rsquo;s program, change it in four ways, and show "
                 "that each change is noticed.",
)

if __name__ == "__main__":
    missing = [s["file"] for s in STEPS if "file" in s and not (SHOTS / s["file"]).exists()]
    if missing or not TERMINAL["blocks"]:
        raise SystemExit("No screenshot for: " + ", ".join(missing) + "\nRun the capture first: cd web && npx "
                         "playwright test --config=walkthroughs/playwright.config.ts legal-export")
    write(PAGE)
