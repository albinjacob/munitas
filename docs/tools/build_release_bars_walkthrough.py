r"""Build docs/public/walkthroughs/release-bars-walkthrough.html from captured screenshots.

The screenshots come from web/walkthroughs/story-b.spec.ts (part bars), which drives the real console against the live Health organisation as one step of the
Health story. Run the capture first (scripts/walkthroughs/capture-health-story.py), then this builder:

    ..\.venv\Scripts\python.exe ..\docs\tools\build_release_bars_walkthrough.py
"""
from __future__ import annotations

from walkthrough_kit import SHOTS_ROOT, Page, series_entry, write

STEPS = [
    {
        "file": "01-nakamura-opens-release-bars.png", "actor": "nakamura",
        "act": ("PART ONE", "The data protection officer writes down the bars",
                "A de-identified result is released to a particular audience, and how much identifying detail it may still hold depends on who "
                "that audience is. A <strong>release bar</strong> is a named set of limits an organisation writes for one audience. "
                "An identifier is a detail that points to a person, such as a name or a phone number."),
        "title": "Nakamura opens the Release bars page, and the organisation has no bar of its own yet",
        "screen": "Release bars &middot; /release-bars",
        "text": "Nakamura is the data protection officer of the Health organisation, a person who watches how the organisation handles personal "
                "data. Until the organisation writes a bar, every result is judged by the one the platform starts with, called Platform default: "
                "at least 98 percent of identifying detail removed overall, and none of the kinds that name a person directly left in the clear. "
                "The platform says plainly that this is a starting point to argue with, not a standard.",
        "note": "The page says <strong>This organisation has not set a release bar yet, so every result is judged by the Platform default bar "
                "below.</strong> Under <strong>Platform default</strong>, <strong>Identifying detail removed, over all kinds together: 98%</strong>, "
                "and each of the five kinds that name a person directly reads <strong>None allowed</strong> under <strong>Left in the clear, at "
                "most</strong>.",
    },
    {
        "file": "02-nakamura-fills-in-the-first-bar.png", "actor": "nakamura",
        "title": "Nakamura writes the first bar, for the annotation team",
        "screen": "Release bars &middot; Create a release bar",
        "text": "A bar has a name, one figure for all kinds of identifier together, and, for each kind, the least that must be removed and the most "
                "that may be left in the clear. The form starts from the Platform default, so a new bar never quietly drops a limit the platform "
                "starts with. The result Imani will decide is going to the annotation team, the people who label data inside the organisation. "
                "Nakamura names the bar for them, lowers the overall figure to 95 percent, and asks for at least 90 percent of addresses and "
                "places to be removed. Names, phone numbers and the other kinds that name a person directly keep their limit of none.",
        "note": "The name reads <strong>Annotation team</strong> and the overall figure reads <strong>95</strong>. Under <strong>Kinds that name a "
                "person directly</strong> every row has <strong>0</strong> in the last column, and <strong>An address or a place</strong> has "
                "<strong>90</strong> under <strong>Removed, at least (percent)</strong>. The reason reads <strong>The annotation team works inside "
                "the organisation and may keep a few addresses or places in a result, but never a name, a phone number or a record number.</strong>",
    },
    {
        "file": "03-the-first-bar-is-saved.png", "actor": "nakamura",
        "title": "The bar is saved as version 1, with who made it and when",
        "screen": "Release bars &middot; /release-bars",
        "text": "A bar is never edited. A change adds a version, and the earlier versions stay, so that a decision made later can say exactly "
                "which version of which bar it was judged by. The saved bar shows its limits in a table, with <strong>No limit</strong> where "
                "Nakamura set none.",
        "note": "The card reads <strong>Annotation team</strong> and <strong>Version 1 by Nakamura</strong>, followed by the date and time. "
                "<strong>Reason:</strong> shows Nakamura's reason. The last row, <strong>An address or a place</strong>, reads <strong>90%</strong> "
                "under <strong>Removed, at least</strong>, and a button reads <strong>Change</strong>.",
    },
    {
        "file": "04-both-bars-are-listed.png", "actor": "nakamura",
        "title": "Nakamura adds a stricter bar for results that leave the organisation",
        "screen": "Release bars &middot; /release-bars",
        "text": "The Public release bar asks for 99 percent overall and for at least 99 percent of dates of birth and of addresses and places. An "
                "organisation keeps as many bars as it has audiences, and the person who releases a result chooses between them.",
        "note": "The list now holds <strong>Annotation team</strong> and <strong>Public release</strong>, each marked <strong>Version 1 by "
                "Nakamura</strong>. Under <strong>Public release</strong>, <strong>Identifying detail removed, over all kinds together: 99%</strong>, "
                "and <strong>A date of birth</strong> and <strong>An address or a place</strong> both read <strong>99%</strong>.",
    },
    {
        "file": "05-imani-can-read-the-bars-but-not-set-one.png", "actor": "imani",
        "act": ("PART TWO", "The reviewer reads the bars and cannot change them",
                "A de-identification reviewer is a person who holds the role that decides whether a result with the names removed may be released."),
        "title": "Imani can read the bars, and cannot set one",
        "screen": "Release bars &middot; /release-bars",
        "text": "Everybody signed in can read the organisation's bars, so that a reviewer can choose one and anybody can see what a result was held "
                "to. Only the data protection officer sets them, so that the person who decides a release cannot loosen the bar it is judged by.",
        "note": "Both bars are listed, and there is no button to create or change one. At the foot of the list the page says <strong>Only the "
                "data protection officer may set a release bar.</strong>",
    },
]

ACTORS = {
    "nakamura": ("Nakamura", "Data protection officer in the Health organisation"),
    "imani": ("Imani", "De-identification reviewer in the Health organisation"),
}

PAGE = Page(
    slug="release-bars", org="health",
    title=series_entry("release-bars")["title"],
    eyebrow="Health organisation &middot; The people",
    lede="The data protection officer writes down how much identifying detail a de-identified result may still hold, once for each audience it "
         "is released to. Each is a named bar with a figure for all kinds of identifier together and a limit for each kind. Everyone in the "
         "organisation can read the bars, and only the data protection officer can set one.",
    actors=ACTORS, steps=STEPS,
    words=["organisation", "dpo", "deid_reviewer", "release_bar"],
    shots_dir=SHOTS_ROOT / "release-bars-story",
    capture_note="Captured live against the <code>health</code> organisation by <code>web/walkthroughs/story-b.spec.ts</code> and built by "
                 "<code>docs/tools/build_release_bars_walkthrough.py</code>.",
)

if __name__ == "__main__":
    missing = [s["file"] for s in PAGE.steps if not (PAGE.shots_dir / s["file"]).exists()]
    if missing:
        raise SystemExit("No screenshot for: " + ", ".join(missing) + "\nRun the capture first: scripts/walkthroughs/capture-health-story.py --only b3")
    write(PAGE)
