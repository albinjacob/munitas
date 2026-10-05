r"""Build docs/public/walkthroughs/closing-an-organisation-walkthrough.html from captured screenshots.

The screenshots come from web/walkthroughs/closing.spec.ts, which drives the real console against the
live stack and ends by deleting Harbour Clinic. Rebuild the clinic, capture, then build:

    .\scripts\seed\reseed-tenant.ps1 -Tenant harbour -Force
    cd web
    npx playwright test --config=walkthroughs/playwright.config.ts closing
    ..\.venv\Scripts\python.exe ..\docs\tools\build_closing_walkthrough.py

Two scripts rather than one because they need different things: the capture needs a browser and a running
platform, and this needs neither. The page, its look and the wording check come from walkthrough_kit.py.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from walkthrough_kit import SHOTS_ROOT, Page, series_entry, write

CLOCK = (
    "Recording cannot wait 15 days. At each jump in time a small script, "
    "<code>scripts/admin/advance-closing-clock.py</code>, moved the clinic&rsquo;s two closing dates back, "
    "and changed nothing else. The platform works out where the clinic stands from those two dates every time "
    "it is asked, so moving them is all that a clock moving on would change. For that reason the dates on the "
    "screens after a jump are earlier than they would be on a real calendar."
)

STEPS = [
    {
        "file": "01-dunmore-opens-closing-down-the-organisation.png", "actor": "dunmore",
        "act": ("PART ONE", "The data custodian closes the clinic down",
                "Harbour Clinic is a small clinic that is closing down. It holds two datasets of letters in its "
                "one department, Patient Records. A dataset is the platform&rsquo;s name for a registered, "
                "named collection of records. The platform calls the clinic <code>harbour</code> on screen. "
                "Dunmore is the data custodian of Patient Records, which means that Dunmore is the person the "
                "department trusts to decide who may read its data."),
        "title": "Dunmore opens the page about closing down the organisation",
        "screen": "Closing down the organisation &middot; /closing",
        "text": "Closing down an organisation means ending its use of Munitas in stages, and the page "
                "explains the three stages before it offers anything. In the first stage, called Closing down "
                "and 15 days long, the clinic takes no changes but its people can still read, and a data "
                "custodian can cancel. In the second stage, called Closed to its people and also 15 days long, "
                "nobody in the clinic can do anything. In the third, everything inside the clinic is deleted, "
                "unless a legal hold stands over it. A legal hold is an order to keep records, and it appears "
                "in part four.",
        "note": "Under <strong>Where harbour stands</strong> a green <strong>Open</strong> label says "
                "<strong>The organisation is running normally</strong>. The one button is the red "
                "<strong>Close down this organisation</strong>, with a line below it naming who may press it: a data "
                "custodian of the organisation, or a platform administrator.",
    },
    {
        "file": "02-dunmore-says-why.png", "actor": "dunmore",
        "title": "Dunmore writes down why the clinic is closing down",
        "screen": "Closing down the organisation &middot; the Close down harbour? box",
        "text": "Pressing the button opens a box that asks for a reason before anything happens. The reason is "
                "stored with the closing down and is shown to everybody in the clinic afterwards, so that "
                "nobody has to guess why the organisation was closed. Nothing is deleted by this step.",
        "note": "The box is titled <strong>Close down harbour?</strong> and repeats the three stages in one "
                "paragraph. The reason field reads <strong>Harbour Clinic is winding down. The last patient was "
                "seen on 30 September.</strong> and the red <strong>Close it down</strong> button is now "
                "usable.",
    },
    {
        "file": "03-the-clinic-is-closing-down.png", "actor": "dunmore",
        "title": "The clinic is now closing down, and Dunmore can still cancel",
        "screen": "Closing down the organisation &middot; /closing",
        "text": "The status changes from Open to Closing down. From this moment the clinic takes no changes: "
                "nothing can be added, registered or approved, because a write of any kind is refused by the "
                "database. Reading still works until the first date. Because this is the first stage, the "
                "page offers Dunmore a button to cancel.",
        "note": "A yellow banner across the top reads <strong>This organisation is closing down</strong> and "
                "gives <strong>@D15@ (15 days left)</strong> as the last day to cancel. The status "
                "box says <strong>Closing down started on @D0@, asked for by Dunmore</strong> and "
                "names <strong>@D30@</strong> as the day everything inside is deleted. The "
                "<strong>Cancel the closing down</strong> button is there, and a notice at the bottom right "
                "reads <strong>harbour is closing down</strong>.",
    },
    {
        "file": "04-quinn-sees-the-banner.png", "actor": "quinn",
        "act": ("PART TWO", "An ordinary member can read, and cannot stop it",
                "Quinn is an analyst at Harbour Clinic. An analyst is a person who works with data. The role "
                "carries no say over who may read the clinic&rsquo;s data and none over the clinic itself."),
        "title": "Quinn sees the same yellow banner on the home page",
        "screen": "Home &middot; /",
        "text": "Everybody in the clinic is told that it is being closed, not only the person who started it. "
                "Quinn has no menu entry for closing down, because that entry is shown only to data custodians, "
                "data protection officers and platform administrators. The banner carries a link instead.",
        "note": "The banner reads <strong>This organisation is closing down</strong> with the same last day to "
                "cancel, <strong>@D15@</strong>, and ends with a link <strong>See where it "
                "stands</strong>. In the box at the top left the clinic is marked <strong>closing down</strong> "
                "and the role is <strong>Analyst</strong>.",
    },
    {
        "file": "05-quinn-is-refused.png", "actor": "quinn",
        "title": "Quinn presses Cancel the closing down and is refused",
        "screen": "Closing down the organisation &middot; /closing",
        "text": "Quinn follows the link and reaches the status page, which shows the same Cancel the closing down "
                "button. The page draws the button for everybody, because the platform decides when it is "
                "pressed and not the page. The platform checks the role against its access rules and answers "
                "with the reason.",
        "note": "A red panel under the button reads <strong>Could not cancel the closing down</strong>, and "
                "gives one reason: <strong>only a data custodian of the organisation, or a platform "
                "administrator, may cancel its closing down</strong>. Nothing on the page has changed, and "
                "the status still reads <strong>Closing down</strong>.",
    },
    {
        "file": "06-dunmore-finds-the-clinic-closed.png", "actor": "dunmore",
        "act": ("PART THREE", "Fifteen days pass, and nobody in the clinic can do anything",
                "Nobody cancelled. " + CLOCK),
        "title": "Dunmore signs in and finds a notice instead of the usual screens",
        "screen": "Closing notice &middot; shown in place of every other screen",
        "text": "The first stage has ended, so the clinic is now in the second stage, called Closed to its people. The "
                "platform refuses every request from the clinic&rsquo;s people from this point, including "
                "the request to cancel. The console therefore shows one notice and nothing else, instead of a "
                "menu whose every link would be refused.",
        "note": "The heading reads <strong>harbour has closed down to its people</strong>, with a red "
                "<strong>Closed to its people</strong> "
                "label. The status line reads <strong>The time to cancel ended on @D0@. Everything "
                "inside is deleted on @D15@ (15 days left)</strong>, which are earlier dates than "
                "before because of the clock move described above. The only control is <strong>Sign "
                "out</strong>.",
    },
    {
        "file": "07-priya-sees-it-closing.png", "actor": "priya",
        "act": ("PART FOUR", "A platform administrator records a legal hold",
                "A law firm, Aldous and Brennan LLP, has written to the platform. A patient is making a claim "
                "against Harbour Clinic and the firm asks that the clinic&rsquo;s records be kept. An "
                "instruction like this is called a legal hold. It arrives in writing, outside the platform, "
                "and goes to a platform administrator. A platform administrator is a person who runs Munitas "
                "itself, and who has no say over who may read any department&rsquo;s data. Priya is one."),
        "title": "Priya sees every organisation and where each one stands",
        "screen": "Closing down the organisation &middot; /closing, the table of every organisation",
        "text": "A platform administrator sees the clinic as one row of a table that covers every "
                "organisation. The table shows dates and states only, and never what an organisation holds. "
                "Harbour Clinic is already in the second stage.",
        "note": "The row for <strong>harbour</strong> reads <strong>Closed to its people</strong> in red, with "
                "<strong>Deleted on @D15@</strong> and <strong>None</strong> under Legal hold. The "
                "rows for <strong>finance</strong> and <strong>health</strong> read <strong>Open</strong> and "
                "each carries a <strong>Close down</strong> button.",
    },
    {
        "file": "08-priya-fills-in-the-notice.png", "actor": "priya",
        "title": "Priya copies the notice into the form",
        "screen": "Legal holds &middot; /legal-holds, Record a legal hold",
        "text": "The form asks for every part of the notice: which organisation it applies to, who issued it, "
                "the issuer&rsquo;s own reference, a contact, what triggered it, the date it arrived, what the "
                "matter is about, and what must be kept. It also asks for a temporary custodian, which is the "
                "person who answers for the kept records while the hold stands. Priya names Adeyemi, the "
                "clinic&rsquo;s data protection officer, a person who watches how the clinic handles personal "
                "data. The form will not send until every required part is filled in.",
        "note": "The fields read: organisation <strong>harbour</strong>, temporary custodian "
                "<strong>Adeyemi</strong>, matter <strong>Alder v Harbour Clinic</strong>, matter number "
                "<strong>HC-2026-0417</strong>, issued by <strong>Aldous and Brennan LLP, for the "
                "claimant</strong>, reference <strong>AB/2026/17</strong>, and what must be kept <strong>Every "
                "record of the claimant, and the audit trail of who read them</strong>. The two date fields for "
                "the range of records are optional and left empty.",
    },
    {
        "file": "09-priya-records-the-hold.png", "actor": "priya",
        "title": "The hold is recorded, and it waits for a second administrator",
        "screen": "Legal holds &middot; Waiting for a second administrator",
        "text": "The hold does not take effect yet, because one platform administrator alone cannot place it. "
                "It already stops the clinic being deleted, though. The reason is that a deletion could "
                "otherwise happen between the moment the hold is recorded and the moment it is approved, "
                "and the hold would then have protected nothing. If nobody approves it within 7 days, it "
                "lapses and stops standing in the way.",
        "note": "A card titled <strong>HC-2026-0417: Alder v Harbour Clinic</strong> carries an amber label "
                "<strong>Waiting for a second administrator</strong>. It repeats every part of the notice, "
                "reads <strong>Recorded by Priya on @D0@</strong>, and gives "
                "<strong>@D7@</strong> as the day it lapses if nobody approves it. Under the "
                "buttons: <strong>You recorded this one, so a different administrator approves it</strong>.",
    },
    {
        "file": "10-priya-cannot-approve-her-own.png", "actor": "priya",
        "title": "Priya presses Approve on the hold that Priya recorded, and is refused",
        "screen": "Legal holds &middot; Waiting for a second administrator",
        "text": "The page offers the Approve button to every platform administrator and the platform decides "
                "when it is pressed. It refuses an approval from the person who recorded the hold. The same "
                "rule is also written into the database as a check, so a fault in the program could not "
                "bypass it.",
        "note": "A red panel reads <strong>Could not decide this hold</strong> and gives the reason "
                "<strong>a legal hold is approved by a different platform administrator from the one who "
                "placed it</strong>. The hold card above it is unchanged.",
    },
    {
        "file": "11-ravi-reads-the-notice.png", "actor": "ravi",
        "act": ("PART FIVE", "A second platform administrator approves it",
                "Ravi is the second platform administrator. Ravi and Priya hold the same role, and the rule is "
                "only that the two are different people."),
        "title": "Ravi reads the notice and writes a note",
        "screen": "Legal holds &middot; Waiting for a second administrator",
        "text": "Ravi sees the same waiting hold, with every part of the notice in front of the decision. The "
                "note is stored with the decision, and a note is required only when a hold is declined. Ravi "
                "writes down what was checked.",
        "note": "The signed-in name at the top left is <strong>Ravi</strong>, a <strong>Platform "
                "administrator</strong>. The note field reads <strong>Notice checked against the issuing "
                "firm&rsquo;s reference</strong>. Both <strong>Approve it</strong> and <strong>Decline</strong> "
                "are offered, and there is no line saying that Ravi recorded this one.",
    },
    {
        "file": "12-the-hold-is-in-force.png", "actor": "ravi",
        "title": "The hold is in force",
        "screen": "Legal holds &middot; In force",
        "text": "Approving moves the card from the waiting list to the list of holds in force. While it stands, "
                "nothing inside the clinic is deleted, whatever the dates say. A hold in force has a review "
                "date, so that somebody has to look at it again and it does not stand by default for ever. "
                "Releasing it is possible only with a written reason.",
        "note": "The card now carries a red label <strong>In force</strong> and reads <strong>Approved by Ravi "
                "on @D0@: Notice checked against the issuing firm&rsquo;s reference</strong> and "
                "<strong>Review by @D90@</strong>. The line <strong>Adeyemi, has not acknowledged "
                "it yet</strong> shows that the temporary custodian has not responded. Below the card the "
                "button <strong>Release this hold</strong> says that releasing starts the closing period "
                "again.",
    },
    {
        "file": "13-adeyemi-is-named-custodian.png", "actor": "adeyemi",
        "act": ("PART SIX", "The temporary custodian acknowledges the hold",
                "Adeyemi is the clinic&rsquo;s data protection officer and has been named as the temporary "
                "custodian of the held records. The clinic is in its closing stage, so the usual screens are "
                "closed to Adeyemi too. The one thing still open is the hold that names Adeyemi."),
        "title": "Adeyemi is told that a hold names Adeyemi as custodian",
        "screen": "Closing notice &middot; shown in place of every other screen",
        "text": "Besides the closing status, the notice shows a second box for the legal hold. It names the "
                "issuing firm, the matter and what must be kept. The temporary custodian is accountable "
                "for the records while the hold stands, and confirms having read the notice by pressing "
                "the button.",
        "note": "The status line now ends <strong>A legal hold is in force, so nothing will be deleted while "
                "it stands</strong>. A second box is headed <strong>A legal hold names you as "
                "custodian</strong> and holds the dark button <strong>I have read it and will answer for "
                "these records</strong>.",
    },
    {
        "file": "14-adeyemi-acknowledges.png", "actor": "adeyemi",
        "title": "Adeyemi acknowledges the hold",
        "screen": "Closing notice &middot; shown in place of every other screen",
        "text": "The acknowledgement is stored with the hold and shown to the platform administrators. Only "
                "the named person can give it. Another member of the clinic who pressed the same button "
                "would be refused.",
        "note": "The button has been replaced by a green line, <strong>You acknowledged this hold on "
                "@D0@</strong>.",
    },
    {
        "file": "15-the-time-is-up-and-nothing-is-deleted.png", "actor": "priya",
        "act": ("PART SEVEN", "The time is up, and the hold stands",
                "The second period ends. " + CLOCK),
        "title": "Both periods have ended, and nothing is deleted",
        "screen": "Closing down the organisation &middot; /closing, the table of every organisation",
        "text": "Every few minutes the platform runs a sweep, which is a check for organisations whose time "
                "is up. It deletes an organisation only when both periods have ended and no legal hold "
                "stands. A sweep ran after the dates moved, and it deleted nothing, because the hold is in "
                "force.",
        "note": "The row for <strong>harbour</strong> reads <strong>Held, time is up</strong> in amber, with "
                "<strong>Not deleted while the hold stands</strong> as the next date and <strong>In "
                "force</strong> under Legal hold.",
    },
    {
        "file": "16-ravi-releases-the-hold.png", "actor": "ravi",
        "act": ("PART EIGHT", "The matter ends, and the hold is released",
                "The law firm confirms in writing that the matter is settled."),
        "title": "Ravi releases the hold and writes down why",
        "screen": "Legal holds &middot; the Release box",
        "text": "Releasing a hold means that the records are no longer protected by it. The box says so, "
                "and it asks for a written reason, which is stored with the hold. Releasing does not delete "
                "anything by itself, and the platform starts the second period again from the moment of "
                "release, so a release made by mistake still leaves 15 days to notice.",
        "note": "The box is titled <strong>Release HC-2026-0417?</strong>. The reason reads <strong>Matter "
                "settled. Written confirmation received from Aldous and Brennan LLP</strong>, and the red "
                "<strong>Release it</strong> button is usable.",
    },
    {
        "file": "17-the-closing-period-starts-again.png", "actor": "ravi",
        "title": "The closing period starts again",
        "screen": "Closing down the organisation &middot; /closing, the table of every organisation",
        "text": "The clinic is back in the second stage, with a new deletion date 15 days after the release. "
                "No hold stands, so the sweep will delete the clinic when that date arrives.",
        "note": "The row for <strong>harbour</strong> reads <strong>Closed to its people</strong> in red, with "
                "<strong>Deleted on @D15@</strong> and <strong>None</strong> under Legal hold. The "
                "date matches the 15 days that the release added.",
    },
    {
        "file": "18-harbour-clinic-is-deleted.png", "actor": "priya",
        "act": ("PART NINE", "Nothing stands in the way, and the clinic is deleted",
                "The second period ends again. " + CLOCK),
        "title": "Harbour Clinic is deleted, and a short record is kept",
        "screen": "Legal holds &middot; Deleted organisations",
        "text": "The sweep deletes everything inside the clinic: its datasets, its sealed versions, its "
                "departments, its people and its stored files. A sealed version is a dataset state that can "
                "never be edited, and the platform allows its deletion only inside a purge that the "
                "database itself has confirmed as due, so nothing else can be removed this way. The clinic&rsquo;s "
                "sign-in accounts are removed too, so nobody can sign in as a person of the clinic any more. "
                "Two things stay. One is a short record, which names no contact detail and holds none of the "
                "clinic&rsquo;s records. The other is the audit trail, the list of who was allowed to read "
                "what, which is kept for seven years and then removed. Both are filed under a new name made "
                "from the old one, so that a later organisation can be called harbour again.",
        "note": "Under <strong>Deleted organisations</strong> the entry for <strong>harbour</strong> begins "
                "with the line <strong>Filed under</strong> and the new name, which starts with "
                "<code>harbour~deleted-</code>. It then reads <strong>Closing down was asked for by Dunmore "
                "on @D0@, because: Harbour Clinic is winding down</strong>, <strong>Removed 3 "
                "sign-in accounts</strong>, and <strong>2 audit rows of who read what are kept until "
                "@Y7@, and then removed</strong>. The last line, <strong>Legal holds that applied: HC-2026-0417 (Aldous and Brennan "
                "LLP, for the claimant, released)</strong>, shows the hold by its number and issuer.",
    },
]

ACTORS = {
    "dunmore": ("Dunmore", "Data custodian of the Patient Records department at Harbour Clinic"),
    "quinn": ("Quinn", "Analyst at Harbour Clinic, with no say over the clinic"),
    "priya": ("Priya", "Platform administrator, who runs Munitas itself"),
    "ravi": ("Ravi", "A second platform administrator"),
    "adeyemi": ("Adeyemi", "Data protection officer at Harbour Clinic, named temporary custodian"),
}

# The dates a screen shows depend on the day it was captured, so the notes name them from the date of the capture and are never typed in.
_FIRST = SHOTS_ROOT / "closing" / "03-the-clinic-is-closing-down.png"
CAPTURED = datetime.fromtimestamp(_FIRST.stat().st_mtime).date() if _FIRST.exists() else date.today()


def _on(days: int = 0, years: int = 0) -> str:
    d = CAPTURED + timedelta(days=days)
    d = d.replace(year=d.year + years)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


_DATES = {"@D0@": _on(), "@D7@": _on(7), "@D15@": _on(15), "@D30@": _on(30), "@D90@": _on(90), "@Y7@": _on(0, 7)}
for _step in STEPS:
    for _key in ("text", "note"):
        for _token, _text in _DATES.items():
            _step[_key] = _step[_key].replace(_token, _text)

PAGE = Page(
    slug="closing-an-organisation", org="harbour",
    title=series_entry("closing-an-organisation")["title"],
    eyebrow="Harbour Clinic &middot; Closing down an organisation",
    lede="A small clinic is closing down. Its data custodian starts the closing down, and an ordinary member is refused when "
         "trying to stop it. After 15 days nobody in the clinic can do anything. A law firm asks that the records "
         "be kept, so one platform administrator records a legal hold and a different one approves it. Everything "
         "inside the clinic is deleted only after the hold is released, and a short record says that it was.",
    actors=ACTORS, steps=STEPS,
    words=["organisation", "department", "dataset", "custodian", "role", "platform_admin", "closing",
           "legal_hold", "temp_custodian", "dpo", "sweep", "sealed"],
    shots_dir=SHOTS_ROOT / "closing",
    capture_note="Captured live against the <code>harbour</code> organisation by "
                 "<code>web/walkthroughs/closing.spec.ts</code> and built by "
                 "<code>docs/tools/build_closing_walkthrough.py</code>. The days between the stages were skipped "
                 "by <code>scripts/admin/advance-closing-clock.py</code>, as each part says. What the platform "
                 "refuses is proved separately by <code>verify/v98_organisation_closing.py</code>, "
                 "<code>verify/v99_legal_hold.py</code> and <code>verify/v100_purge.py</code>, which also "
                 "show that nothing outside a due, unheld organisation can be deleted.",
)

if __name__ == "__main__":
    missing = [s["file"] for s in STEPS if not (PAGE.shots_dir / s["file"]).exists()]
    if missing:
        raise SystemExit("No screenshot for: " + ", ".join(missing) + "\nRun the capture first: cd web && npx "
                         "playwright test --config=walkthroughs/playwright.config.ts closing")
    write(PAGE)
