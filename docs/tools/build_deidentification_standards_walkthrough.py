r"""Build docs/public/walkthroughs/deidentification-standards-walkthrough.html from captured screenshots.

The screenshots come from web/walkthroughs/story-b.spec.ts (part bars), which drives the real console against the live Health organisation as one step of the
Health story. Run the capture first (scripts/walkthroughs/capture-health-story.py), then this builder:

    ..\.venv\Scripts\python.exe ..\docs\tools\build_deidentification_standards_walkthrough.py
"""
from __future__ import annotations

from walkthrough_kit import SHOTS_ROOT, Page, series_entry, write

STEPS = [
    {
        "file": "01-nakamura-opens-the-standards-page.png", "actor": "nakamura",
        "act": ("PART ONE", "The data protection officer writes down the standards",
                "A de-identified result is released to a particular audience, and how much identifying detail it may still hold depends on who "
                "that audience is. A <strong>de-identification standard</strong> is a named set of limits an organisation writes for one audience. "
                "An identifier is a detail that points to a person, such as a name or a phone number."),
        "title": "Nakamura opens the De-identification standards page, which begins by explaining how to read a standard",
        "screen": "De-identification standards &middot; /deidentification-standards",
        "text": "Nakamura is the data protection officer of the Health organisation, a person who watches how the organisation handles personal "
                "data. Before any standard is listed, the page explains one. A pipeline run, which is one pass of the pipeline over the data, is "
                "measured against an answer key, a list prepared beforehand of the identifying details that really are in the data. The "
                "measurement gives two figures: how much identifying detail was removed, and how much was left in the clear. The page then works "
                "through an example in which one result is judged by two different standards.",
        "note": "<strong>How to read a standard</strong> explains <strong>Kind of identifier</strong>, <strong>Removed, at least</strong>, "
                "<strong>Left in the clear, at most</strong> and <strong>Over all kinds together</strong>. Under <strong>An example</strong>, "
                "the run removes all 100 names and 94 of the 100 addresses, which is 97% overall. The standard <strong>For an internal team</strong> "
                "gives <strong>Clears it.</strong> and the standard <strong>For a public release</strong> gives <strong>Does not clear it.</strong>",
    },
    {
        "file": "02-no-standard-yet-and-the-platform-default.png", "actor": "nakamura",
        "title": "The organisation has no standard of its own yet, so the one the platform starts with applies",
        "screen": "De-identification standards &middot; /deidentification-standards",
        "text": "Until the organisation writes a standard, every result is judged by the one the platform starts with, called Platform default: at "
                "least 98 percent of identifying detail removed over all kinds together, and none of the five kinds that name a person directly "
                "left in the clear. The page says plainly that this is a starting point and that an organisation is expected to set standards "
                "of its own.",
        "note": "The page says <strong>This organisation has not set a standard yet, so every result is judged by the Platform default standard "
                "below.</strong> Under <strong>Platform default</strong>, <strong>Removed, at least, over all kinds together: 98%</strong>, and "
                "each of the five kinds that name a person directly reads <strong>No minimum</strong> under <strong>Removed, at least</strong> and "
                "<strong>None may remain</strong> under <strong>Left in the clear, at most</strong>. A button reads <strong>Create a standard</strong>.",
    },
    {
        "file": "03-nakamura-fills-in-the-first-standard.png", "actor": "nakamura",
        "title": "Nakamura writes the first standard, for the annotation team",
        "screen": "De-identification standards &middot; Create a standard",
        "text": "A standard has a name, one figure for all kinds of identifier together, and, for each kind, the least that must be removed and the "
                "most that may be left in the clear. The form starts from Platform default, so a new standard never quietly drops a limit the "
                "platform starts with. The result Imani will decide is going to the annotation team, the people who label data inside the "
                "organisation. Nakamura names the standard for them, lowers the overall figure to 95 percent, and asks for at least 90 percent of "
                "addresses and places to be removed. Names, phone numbers and the other kinds that name a person directly keep their limit of none.",
        "note": "The name reads <strong>Annotation team</strong> and the overall figure reads <strong>95</strong>. Under <strong>Kinds that name a "
                "person directly</strong> every row has <strong>0</strong> in the last column, and <strong>An address or a place</strong> has "
                "<strong>90</strong> under <strong>Removed, at least (percent)</strong>. The reason reads <strong>The annotation team works inside "
                "the organisation and may keep a few addresses or places in a result, but never a name, a phone number or a record number.</strong>",
    },
    {
        "file": "04-the-first-standard-is-saved.png", "actor": "nakamura",
        "title": "The standard is saved as version 1, with who made it and when",
        "screen": "De-identification standards &middot; /deidentification-standards",
        "text": "A standard is never edited. A change adds a version, and the earlier versions stay, so that a decision made later can say exactly "
                "which version of which standard it was judged by. The saved standard shows its limits in a table, with <strong>No minimum</strong> "
                "or <strong>No maximum</strong> where Nakamura set none.",
        "note": "The card reads <strong>Annotation team</strong> and <strong>Version 1 by Nakamura</strong>, followed by a date and time. "
                "<strong>Reason:</strong> shows Nakamura's reason. The last row, <strong>An address or a place</strong>, reads <strong>90%</strong> "
                "under <strong>Removed, at least</strong> and <strong>No maximum</strong> under <strong>Left in the clear, at most</strong>, and a "
                "button reads <strong>Change this standard</strong>.",
    },
    {
        "file": "05-both-standards-are-listed.png", "actor": "nakamura",
        "title": "Nakamura adds a stricter standard for results that leave the organisation",
        "screen": "De-identification standards &middot; /deidentification-standards",
        "text": "The Public release standard asks for 99 percent overall and for at least 99 percent of dates of birth and of addresses and places. "
                "An organisation keeps as many standards as it has audiences, and the person who releases a result chooses between them.",
        "note": "The list now holds <strong>Annotation team</strong> and <strong>Public release</strong>, each marked <strong>Version 1 by "
                "Nakamura</strong>. Under <strong>Public release</strong>, <strong>Removed, at least, over all kinds together: 99%</strong>, "
                "and <strong>A date of birth</strong> and <strong>An address or a place</strong> both read <strong>99%</strong>.",
    },
    {
        "file": "06-imani-can-read-the-standards-but-not-set-one.png", "actor": "imani",
        "act": ("PART TWO", "The reviewer reads the standards and cannot change them",
                "A de-identification reviewer is a person who holds the role that decides whether a result with the names removed may be released."),
        "title": "Imani can read the standards, and cannot set one",
        "screen": "De-identification standards &middot; /deidentification-standards",
        "text": "Everybody signed in can read the organisation's standards, so that a reviewer can choose one and anybody can see what a result was "
                "held to. Only the data protection officer sets them, so that the person who decides a release cannot loosen the standard it is "
                "judged by.",
        "note": "Both standards are listed, and there is no button to create or change one. Below the list the page says <strong>Only the data "
                "protection officer may set a de-identification standard.</strong>",
    },
]

ACTORS = {
    "nakamura": ("Nakamura", "Data protection officer in the Health organisation"),
    "imani": ("Imani", "De-identification reviewer in the Health organisation"),
}

PAGE = Page(
    slug="deidentification-standards", org="health",
    title=series_entry("deidentification-standards")["title"],
    eyebrow="Health organisation &middot; The people",
    lede="The data protection officer writes down how much identifying detail a de-identified result may still hold, once for each audience it "
         "is released to. Each is a named standard with a figure for all kinds of identifier together and a limit for each kind. Everyone in the "
         "organisation can read the standards, and only the data protection officer can set one.",
    actors=ACTORS, steps=STEPS,
    words=["organisation", "dpo", "deid_reviewer", "deidentification_standard"],
    shots_dir=SHOTS_ROOT / "deidentification-standards-story",
    capture_note="Captured live against the <code>health</code> organisation by <code>web/walkthroughs/story-b.spec.ts</code> and built by "
                 "<code>docs/tools/build_deidentification_standards_walkthrough.py</code>.",
)

if __name__ == "__main__":
    missing = [s["file"] for s in PAGE.steps if not (PAGE.shots_dir / s["file"]).exists()]
    if missing:
        raise SystemExit("No screenshot for: " + ", ".join(missing) + "\nRun the capture first: scripts/walkthroughs/capture-health-story.py --only b3")
    write(PAGE)
