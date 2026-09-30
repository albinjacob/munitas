r"""Build docs/public/walkthroughs/finance-walkthrough.html from captured screenshots.

The screenshots come from web/walkthroughs/finance.spec.ts, which drives the
real console against the live stack, seeded by
scripts/seed/seed-finance-example.py. Run both first:

    .venv\Scripts\python.exe scripts/seed/seed-finance-example.py
    cd web
    npx playwright test --config=walkthroughs/playwright.config.ts finance
    ..\.venv\Scripts\python.exe ..\docs\tools\build_finance_walkthrough.py

Needs Pillow, listed in docs/tools/requirements.txt.

Covers the whole worked example: a transaction batch coming in, a promoted
summary, a fraud-scoring agent asking for access the same way a person does,
two custodians each scoped to their own department's queue, and the tenant
boundary holding against a deep-linked dataset from outside it. An earlier
version of this page covered the same ground but was hand-assembled rather
than generated, and its screenshots were stale (old tenant name, old logins).
"""
from __future__ import annotations

import base64
import io
from datetime import date
from pathlib import Path

from PIL import Image

DOCS = Path(__file__).resolve().parent.parent
SHOTS = DOCS.parent / "web" / "walkthroughs" / "shots" / "finance"
OUT = DOCS / "public" / "walkthroughs" / "finance-walkthrough.html"

QUALITY = 72

ACTORS = {
    "lena": ("Lena", "Data engineer, Fraud Operations"),
    "marcus": ("Marcus", "Data custodian for Fraud Operations"),
    "omar": ("Omar", "Analyst, asks for access"),
    "naomi": ("Naomi", "Data custodian for Risk & Compliance"),
}

STEPS = [
    {
        "file": "01-lena-signs-in.png",
        "actor": "lena",
        "act": (
            "ACT ONE",
            "Signing in",
            "Munitas is a governance platform: it controls who is allowed "
            "to read which piece of data, and it keeps a permanent record "
            "of every time that access was granted or refused. Every "
            "person who works at an organisation using Munitas shares one "
            "sign-in screen. Which person signs in decides which home page "
            "they land on, and which pieces of data they are allowed to "
            "see from that point on.",
        ),
        "title": "Signs in as a data engineer in the Fraud Operations department",
        "screen": "Sign in &middot; /auth/login",
        "text": "Lena works at a card payments company that uses Munitas. "
                "Inside that company, she belongs to a department called "
                "Fraud Operations, and her job title there is data "
                "engineer. The login form on this screen is the same form "
                "every organisation on the platform uses, filled in here "
                "for Lena's organisation, finance.",
        "note": "Lena's job title, data engineer, and her department, "
                "Fraud Operations, are both fixed the moment she signs in. "
                "She does not choose them afterwards. Together, they decide "
                "everything the platform shows her from this point on.",
    },
    {
        "file": "02-lena-home.png",
        "actor": "lena",
        "title": "Lands on her own home page",
        "screen": "Home &middot; /",
        "text": "This home page counts four things: how many datasets "
                "exist, how many dataset versions have been sealed, how "
                "many were released for wider use, and how many are still "
                "held back. A dataset is the platform's own record for a "
                "file it controls access to. Every number shown here "
                "counts only the finance organisation's own datasets.",
        "note": "If a different person, at a different organisation, "
                "signed in right now, every number on this exact page would "
                "change. These figures are never shared across "
                "organisations.",
    },
    {
        "file": "03-register-filled.png",
        "actor": "lena",
        "act": (
            "ACT TWO",
            "Lena brings a transaction batch in",
            "Lena works in Fraud Operations, one of two departments inside "
            "the finance organisation. She is about to bring in a fresh "
            "batch of card transactions, and doing that means creating a "
            "dataset: the platform's own record for a file, which decides "
            "who is allowed to read it from then on. Creating a dataset "
            "takes three steps, always in the same order: register a name "
            "and an owning department for it, upload the actual file into "
            "it, and seal the finished version so nobody can change it "
            "afterward. This is the same three-step process used anywhere "
            "else on the platform, whatever kind of file the dataset holds.",
        ),
        "title": "Registers a fresh transaction batch as a new dataset",
        "screen": "Bring a dataset in &middot; /datasets/register",
        "text": "Lena fills in two fields on this form: a name for the new "
                "dataset, and the department that will own it. The "
                "department she picks here decides who is already "
                "positioned to work with whatever gets uploaded into this "
                "dataset next.",
        "note": "The department field is set to Fraud Operations, the same "
                "department Lena herself belongs to. This choice is what "
                "makes Marcus, the data custodian for Fraud Operations, the "
                "person who will later decide who else may read this data. "
                "Naomi, the data custodian for a different department, "
                "plays no part in that decision.",
    },
    {
        "file": "04-csv-uploaded.png",
        "actor": "lena",
        "title": "Uploads a real file of card transactions into the new dataset",
        "screen": "Bring a dataset in &middot; upload step",
        "text": "The uploaded file has three rows, and each row names a "
                "real cardholder and a full card number. This is exactly "
                "the kind of personal information a de-identification "
                "process exists to remove before anybody outside Fraud "
                "Operations can see it. The card numbers themselves are "
                "the payment networks' own published test numbers, not "
                "numbers belonging to any real person.",
        "note": "Nothing about this file was simplified for the "
                "screenshot: it is a real file with real rows, each "
                "naming a cardholder and a full card number. This raw, "
                "sensitive file is the reason every access rule described "
                "later in this walkthrough exists in the first place.",
    },
    {
        "file": "05-sealed-v1.png",
        "actor": "lena",
        "title": "Seals the dataset as version 1",
        "screen": "Version detail &middot; /versions/&lt;id&gt;",
        "text": "Sealing a dataset version fixes its contents permanently: "
                "the platform records a fingerprint of the exact bytes "
                "uploaded, and that version can never be changed again. "
                "If something later needs fixing, the fix becomes a new "
                "version, never an edit to this one.",
        "note": "Find the field labelled Access level when sealed. It "
                "reads RAW, meaning the file still contains cardholder "
                "names and full card numbers. That value stays fixed for "
                "this version forever, which is exactly why the next "
                "section introduces a second, separate dataset instead of "
                "trying to change this dataset's access level after the "
                "fact.",
    },
    {
        "file": "06-promoted-summary.png",
        "actor": "lena",
        "act": (
            "ACT THREE",
            "A mask, not a move",
            "card-transaction-summary is a second, separate dataset from "
            "the raw batch Lena just sealed. It was sealed under review, "
            "meaning nobody outside Fraud Operations could read it yet, "
            "and it was only released for wider use once the cardholder "
            "names and card numbers that made the raw data sensitive had "
            "been combined into totals that no longer identify anybody. "
            "Releasing a dataset changes who is allowed to read the file. "
            "It does not move or rewrite a single byte of the file itself.",
        ),
        "title": "Opens the already-released summary dataset",
        "screen": "Version detail &middot; card-transaction-summary v1",
        "text": '<code>This has been released for wider use. Nothing '
                "about it was rewritten to do that.</code> This dataset "
                "was sealed under review, and it has since been released. "
                "Further down the page, the platform shows the evidence "
                "an automated process relied on before releasing it: which "
                "cardholder fields were removed, and how often the "
                "resulting totals wrongly looked like fraud when tested "
                "against transactions whose real answer was already "
                "known. The page also shows this dataset's fingerprint, a "
                "value calculated from the exact bytes of the file, which "
                "has not changed since before the dataset was released. "
                "An unchanged fingerprint is how the platform proves "
                "nothing in the file itself was altered.",
        "note": "Two fields near the top of the page tell the real story. "
                "Access level when sealed still reads Under review: that "
                "value was fixed the moment this dataset was sealed and "
                "can never change afterward. Access level now reads "
                "Published: that value can change again with each later "
                "release decision. Further down the page, Where it is "
                "stored and Fingerprint are identical to what they were "
                "before release. That is the proof that releasing this "
                "dataset moved no bytes and only changed who is allowed "
                "to read them.",
    },
    {
        "file": "07-agent-before-upload.png",
        "actor": "lena",
        "act": (
            "ACT FOUR",
            "A fraud-scoring agent asks for access, the same way a person does",
            "fraud-transaction-scoring is an agent: a piece of code, "
            "registered and versioned on the platform, that Munitas runs "
            "on somebody's behalf rather than a person reading data "
            "directly. This particular agent scores card transactions for "
            "likely fraud before a human reviewer looks at them. "
            "Registering an agent and deploying its code is not the same "
            "thing as being allowed to read any particular dataset. By "
            "default, an agent may only read datasets that have already "
            "been published, so asking this agent to read the raw batch "
            "from Act Two pauses the run until Marcus, the data custodian "
            "for Fraud Operations, decides whether to grant it access.",
        ),
        "title": "Opens the fraud-scoring agent, already registered by somebody else",
        "screen": "Agents &middot; /agents",
        "text": "This agent already exists on the platform before Lena "
                "does anything with it: it has a name, an owning "
                "department, and a stated purpose, the same three facts "
                "recorded when Lena registered the transaction dataset "
                "earlier, just for a different kind of resource.",
        "note": "The row on this screen names the department that owns "
                "this agent: Fraud Operations, the same department that "
                "owns the transaction batch from Act Two. Because both "
                "belong to the same department, Marcus is the person who "
                "will be asked to grant this agent access in Act Five. If "
                "this agent instead belonged to Risk & Compliance, Naomi "
                "would be asked, not Marcus.",
    },
    {
        "file": "08-agent-version-sealed.png",
        "actor": "lena",
        "title": "Uploads a small real code project as a new version of the agent",
        "screen": "Agent detail &middot; /agents/&lt;id&gt;",
        "text": "Lena uploads a small, real project as a new version of "
                "this agent's code. Once uploaded, the version runs "
                "inside an isolated container, is identified by a hash of "
                "its own code, and becomes permanent the moment it is "
                "sealed.",
        "note": "The newest version, listed at the top of this page, is "
                "the one just uploaded. Clicking its Deploy button, an "
                "action this walkthrough does not capture as its own "
                "screenshot, makes that version the active one: the "
                "version that will actually run the next time somebody "
                "starts this agent.",
    },
    {
        "file": "09-run-warned.png",
        "actor": "lena",
        "title": "Starts a run against the raw batch, and is told plainly why it will have to wait",
        "screen": "Agent detail &middot; Runs section",
        "text": '<code>This agent cannot read that dataset on its own. '
                "Starting will ask Marcus, who looks after Fraud "
                "Operations, to grant it access, in your name.</code> "
                "Munitas shows this warning before the click that would "
                "start the run, not after.",
        "note": "The dataset chosen for this run is card-transaction-log, "
                "the batch sealed in Act Two that still contains full "
                "cardholder names and card numbers, not the published "
                "summary dataset from Act Three, which no longer does. "
                "Choosing the unreleased, still-sensitive dataset is "
                "exactly why this warning appears now. Choosing the "
                "published summary instead would not have triggered it.",
    },
    {
        "file": "10-marcus-queue.png",
        "actor": "marcus",
        "act": (
            "ACT FIVE",
            "Two departments, two queues, and a dataset that was never here",
            "Marcus and Naomi are both data custodians inside the same "
            "finance organisation, but each one is the custodian for a "
            "different department: Marcus for Fraud Operations, Naomi for "
            "Risk & Compliance. Each custodian sees only the requests to "
            "read data that their own department owns. Marcus never sees "
            "a request for Risk & Compliance's data, Naomi never sees a "
            "request for Fraud Operations's data, and neither one may "
            "decide a request that belongs to the other. The same "
            "boundary holds at the level of the whole organisation: a "
            "dataset belonging to a different organisation entirely, "
            "opened by pasting its address directly into the browser, "
            "comes back as though it does not exist at all.",
        ),
        "title": "Sees two requests waiting: the agent's, and a human analyst's",
        "screen": "Home &middot; Marcus, data custodian for Fraud Operations",
        "text": "Two separate requests are waiting for a decision. One is "
                "the agent from Act Four, asking to read the raw batch on "
                "Lena's behalf. The other is Omar, an analyst, asking in "
                "his own name to read the same raw dataset for a "
                "different reason. Underneath both, a request Marcus "
                "already granted earlier stays visible on the page, "
                "instead of disappearing the moment it was decided.",
        "note": "Marcus is signed in as the data custodian for Fraud "
                "Operations, the department that owns both the "
                "transaction batch and the agent. That department match "
                "is the only reason these two requests reach him at all. "
                "A request tied to Risk & Compliance's own data, like the "
                "one shown in step 12, would never appear on this screen, "
                "no matter who is signed in.",
    },
    {
        "file": "11-run-finished.png",
        "actor": "lena",
        "title": "The run finishes, for real",
        "screen": "Agent detail &middot; Runs section",
        "text": "After Marcus grants access, the paused run resumes on "
                "its own and finishes without anybody clicking anything "
                "further. The block of text shown underneath the "
                "succeeded badge is the agent's real output: the literal "
                "result its code produced while running, not text written "
                "for this walkthrough.",
        "note": "The status badge now reads succeeded, and the block "
                "beneath it is the exact result the agent's code printed "
                "while it ran inside its isolated container. Compare this "
                "with the warning shown in step 9: this is the same run "
                "that was told it would have to wait, now finished.",
    },
    {
        "file": "12-naomi-queue.png",
        "actor": "naomi",
        "title": "Signs in as the data custodian for Risk & Compliance, and sees a different queue entirely",
        "screen": "Home &middot; Naomi, data custodian for Risk & Compliance",
        "text": "Naomi's queue does not contain Marcus's two requests from "
                "step 10, and it does not contain the fraud-scoring "
                "agent's request either. It contains exactly one request, "
                "asking to read a dataset called kyc-identity-documents, "
                "which is the only dataset owned by the Risk & Compliance "
                "department.",
        "note": "Naomi is the data custodian for Risk & Compliance, a "
                "different department from Marcus's Fraud Operations. "
                "Neither the agent's request nor Omar's request from step "
                "10 appears on this page. This queue only ever shows "
                "requests for data that Risk & Compliance owns, the same "
                "rule that limited Marcus's queue to Fraud Operations's "
                "own data in step 10.",
    },
    {
        "file": "13-deep-link-refused.png",
        "actor": "naomi",
        "title": "A dataset from a different organisation, opened by its direct address, comes back as though it never existed",
        "screen": "Version detail &middot; a dataset version address from a different organisation",
        "text": "This address is a real, valid address for a dataset "
                "version. It does not belong to the finance organisation, "
                "though: it belongs to a dataset owned by a separate "
                "organisation on the same platform, called health. Typed "
                "directly into the browser while signed in as Naomi, the "
                'address returns <code>no such dataset version</code>, '
                "rather than a message saying access was refused.",
        "note": "The difference matters. A message saying access was "
                "refused would confirm that this dataset version exists "
                "somewhere, even if Naomi could not read it. Instead, "
                "Munitas returns exactly the message it would return for "
                "an address that was never valid at all, which is what "
                "keeps data belonging to other organisations invisible to "
                "people outside them.",
    },
    {
        "file": "14-omar-finds-a-request-form.png",
        "actor": "omar",
        "act": (
            "ACT SIX",
            "The same decision, made the other way",
            "Every grant so far covered exactly one purpose: the agent's "
            "batch run, and each of Omar's raw-data requests from Act Five. "
            "That is not a platform-wide rule, it is what raw data always "
            "gets, because the database itself refuses anything looser "
            "against it, no matter how much a custodian trusts who is "
            "asking. Below that floor, the shape of a grant is the "
            "custodian's own call. Omar runs the same merchant digest every "
            "month, from data that has already been reviewed and is no "
            "longer raw. Asking Marcus to approve it again each time, "
            "solely because Omar reworded his own reason, buys nothing: "
            "nobody is deciding anything new, they are just clicking "
            "approve on a request they already trust.",
        ),
        "title": "Asks to read a monthly digest that is no longer raw",
        "screen": "Version detail &middot; fraud-analytics-digest, under review",
        "text": "The same request form Sam used to ask for Devi's raw "
                "recording in the healthcare organisation, on a dataset "
                "that has already been through review. Under review is not "
                "raw: it sits below the published floor, so reading it "
                "still needs a custodian's grant, but it is the one class "
                "where that custodian gets to decide how far to extend "
                "their trust.",
        "note": "Omar's own request looks identical to any other: a "
                "purpose, a justification, nothing about which shape of "
                "grant he wants. That choice belongs to whoever approves "
                "it, not to whoever asks.",
    },
    {
        "file": "15-marcus-sees-omars-request.png",
        "actor": "marcus",
        "title": "Sees the request, and a choice the raw requests never offered",
        "screen": "Home &middot; Marcus, data custodian for Fraud Operations",
        "text": "Underneath Omar's stated purpose sits a control that did "
                "not appear anywhere in Act Five: <em>This purpose only</em> "
                "or <em>Any purpose, while this lasts</em>. It is absent "
                "entirely on a request against raw data -- the database "
                "refuses to create that combination -- so the choice only "
                "ever appears where it is actually safe to make.",
        "note": "Nothing forces Marcus to notice this control or to change "
                "it. Left alone, granting behaves exactly as every grant in "
                "this walkthrough already has: scoped to the one purpose "
                "named today.",
    },
    {
        "file": "16-marcus-chooses-any-purpose.png",
        "actor": "marcus",
        "title": "Chooses to trust Omar with this dataset, not just with today's reason",
        "screen": "Home &middot; Marcus, data custodian for Fraud Operations",
        "text": "Marcus already knows this request: Omar runs the same "
                "digest monthly, and Marcus has granted it before. He picks "
                "<em>Any purpose, while this lasts</em>, and the line "
                "beneath the buttons updates to say so before he commits to "
                "anything.",
        "note": "This is a judgment about Omar and this particular "
                "dataset, made once, by the person accountable for the "
                "data. It is not a setting anyone can turn on for "
                "everyone, and it never becomes available for raw data no "
                "matter how much Marcus trusts Omar.",
    },
    {
        "file": "17-any-purpose-granted.png",
        "actor": "marcus",
        "title": "Grants it, and the request leaves the queue exactly as any other would",
        "screen": "Home &middot; Marcus, data custodian for Fraud Operations",
        "text": "The grant itself looks identical to every other approval "
                "in this walkthrough: the request disappears from the "
                "queue, the same as Act Five's. What differs is invisible "
                "here, and shows up only in what Omar can do next.",
        "note": "A screenshot of a queue emptying cannot show a difference "
                "that has not happened yet. The next step is the proof.",
    },
    {
        "file": "18-any-purpose-lease-marked-in-history.png",
        "actor": "marcus",
        "title": "Reads for a reason nobody approved, and it works anyway",
        "screen": "Home &middot; Marcus, data custodian for Fraud Operations",
        "text": "Omar reads the digest again, this time for an unplanned "
                "spot-check he never mentioned when he first asked. Under a "
                "purpose-locked grant this would be refused outright, the "
                "same as every raw-data request in Act Five. Here it is "
                "allowed, because Marcus's grant was never scoped to the "
                "one reason Omar happened to type. Marcus's own record of "
                "what he decided now carries a small label, "
                "<code>covers any purpose</code>, so the choice he made "
                "stays visible every time he looks back at it, not only at "
                "the moment he made it.",
        "note": "Compare this directly with Act Five: the same platform, "
                "the same kind of request, and two different outcomes for "
                "a purpose stated after the fact -- refused there, allowed "
                "here -- because the data and the custodian's own choice "
                "differed, not because the rule changed.",
    },
]

FOOTER_TEMPLATE = """
<footer>
  Captured live against the <code>finance</code> organisation by
  <code>web/walkthroughs/finance.spec.ts</code> and rendered by
  <code>docs/tools/build_finance_walkthrough.py</code>. Re-run both when the
  flow changes.
  <p class="updated">Last updated __UPDATED__.</p>
</footer>

<div id="lightbox">
  <span class="close-hint">Click anywhere to close</span>
  <img id="lightbox-img" src="" alt="">
</div>

<script>
  (function () {
    var box = document.getElementById("lightbox");
    var boxImg = document.getElementById("lightbox-img");

    document.querySelectorAll(".shot img").forEach(function (img) {
      img.addEventListener("click", function () {
        boxImg.src = img.src;
        boxImg.alt = img.alt;
        box.classList.add("open");
      });
    });

    box.addEventListener("click", function () {
      box.classList.remove("open");
      boxImg.src = "";
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        box.classList.remove("open");
        boxImg.src = "";
      }
    });
  })();
</script>
"""


def footer() -> str:
    return FOOTER_TEMPLATE.replace("__UPDATED__", date.today().isoformat())


def encoded(path: Path) -> str:
    """The screenshot as an inline JPEG data URI."""
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        buffer = io.BytesIO()
        rgb.save(buffer, format="JPEG", quality=QUALITY, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def strip_tags(text: str) -> str:
    """The plain words of a step title, for the contents list."""
    out, depth = [], 0
    for char in text:
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
        elif depth == 0:
            out.append(char)
    return "".join(out)


def stylesheet() -> str:
    """The walkthrough styling, taken from the page that established it.

    Copied rather than shared as a file because a walkthrough has to open
    standalone from the filesystem. The base page names its two actors devi
    and hartley; this flow renames them to its own first two (lena, marcus)
    and adds the other two from the same ramp people-administration already
    introduced (naomi reuses its "priya" slot; omar is new).
    """
    source = (DOCS / "public" / "walkthroughs" / "custom-pipeline-walkthrough.html").read_text(encoding="utf-8")
    style = source[source.index("<style>"): source.index("</style>") + len("</style>")]
    style = style.replace("devi", "lena").replace("hartley", "marcus")
    extra = """
<style>
  :root {
    --naomi: #a4562a; --naomi-bg: #fbeee4; --naomi-line: #edcdb4;
    --omar: #6a4c9c; --omar-bg: #efe9f7; --omar-line: #d8c9ee;
    --note: #9a6b00; --note-bg: #fdf3d9; --note-line: #f0dea3;
  }
  :root:not([data-theme="light"]) {
    @media (prefers-color-scheme: dark) {
      --naomi: #f0a771; --naomi-bg: #3a2415; --naomi-line: #5e3c22;
      --omar: #c3a8ec; --omar-bg: #2c2340; --omar-line: #473465;
      --note: #e0b64c; --note-bg: #3a2f0c; --note-line: #5c4b16;
    }
  }
  :root[data-theme="dark"] {
    --naomi: #f0a771; --naomi-bg: #3a2415; --naomi-line: #5e3c22;
    --omar: #c3a8ec; --omar-bg: #2c2340; --omar-line: #473465;
    --note: #e0b64c; --note-bg: #3a2f0c; --note-line: #5c4b16;
  }
  .cast .dot.naomi { background: var(--naomi); }
  .cast .dot.omar { background: var(--omar); }
  .actor-chip.naomi {
    background: var(--naomi-bg); border-color: var(--naomi-line); color: var(--naomi);
  }
  .actor-chip.naomi .dot { background: var(--naomi); }
  .actor-chip.omar {
    background: var(--omar-bg); border-color: var(--omar-line); color: var(--omar);
  }
  .actor-chip.omar .dot { background: var(--omar); }

  /*
    Which screen, and what to notice on it: standing requirement in
    CLAUDE.md's "UI flow walkthroughs" section, so a step never reduces to a
    picture with a one-line caption.
  */
  .step-screen {
    display: block; margin: 2px 0 8px; font-family: "SF Mono", ui-monospace,
    Consolas, monospace; font-size: 12px; color: var(--ink-faint);
  }
  .step-note {
    display: flex; gap: 8px; margin: 10px 0 14px; padding: 10px 12px;
    border-radius: 8px; border: 1px solid var(--note-line);
    background: var(--note-bg); font-size: 13.5px; color: var(--ink);
  }
  .step-note::before {
    content: "Look for"; flex: none; font-weight: 700; font-size: 11px;
    text-transform: uppercase; letter-spacing: 0.06em; color: var(--note);
    padding-top: 1.5px;
  }
</style>"""
    return style + extra


def hero() -> str:
    cards = "\n".join(
        f'    <div class="card">\n      <span class="dot {key}"></span>\n'
        f"      <div>\n"
        f'        <div class="name">{name}</div>\n'
        f'        <div class="role">{role}</div>\n'
        f"      </div>\n    </div>"
        for key, (name, role) in ACTORS.items()
    )
    return f"""
<header class="hero">
  <div class="eyebrow">Munitas &middot; Finance organisation &middot; Card payments</div>
  <h1>Scoring card transactions for fraud, without letting the raw data out of Fraud Operations's control</h1>
  <p class="lede">
    Munitas is a governance platform: it decides who may read which piece of
    data, and it keeps a permanent record of every time that access was
    granted or refused. This walkthrough follows one real example inside a
    card payments company that uses Munitas. The company's Fraud Operations
    department brings in a batch of card transactions, and every row names a
    real cardholder and their full card number from the moment the file
    arrives. Later, an automated fraud-scoring agent asks to read that same
    data. Munitas does not let it read anything automatically: a data
    custodian, the person accountable for that department's data, has to
    decide and grant access first. Every screenshot below was captured from
    one real, unedited run of this exact example.
  </p>
  <div class="cast-title">Who is involved</div>
  <div class="cast">
{cards}
  </div>
</header>
"""


def build() -> str:
    parts: list[str] = [
        "<title>Card transactions, scored for fraud: a walkthrough</title>",
        stylesheet(),
        hero(),
        '<div class="wrap">',
    ]

    contents = "\n".join(
        f'    <li><a href="#s{i}">{strip_tags(step["title"])}</a></li>'
        for i, step in enumerate(STEPS, start=1)
    )
    parts.append(
        '<nav class="toc">\n  <div class="toc-title">On this page</div>\n'
        f"  <ol>\n{contents}\n  </ol>\n</nav>"
    )

    open_act = False
    for i, step in enumerate(STEPS, start=1):
        if "act" in step:
            if open_act:
                parts.append("</section>")
            num, heading, sub = step["act"]
            parts.append(
                '<section class="act">\n  <div class="act-head">\n'
                f'    <span class="act-num">{num}</span>\n    <h2>{heading}</h2>\n'
                f'  </div>\n  <p class="act-sub">{sub}</p>'
            )
            open_act = True

        actor = step["actor"]
        name = ACTORS[actor][0]
        image = encoded(SHOTS / step["file"])
        parts.append(
            f'  <div class="step" id="s{i}">\n'
            f'    <div class="step-num">{i}</div>\n'
            f'    <div class="step-body">\n'
            f'      <span class="actor-chip {actor}"><span class="dot"></span>{name}</span>\n'
            f'      <div class="step-title">{step["title"]}</div>\n'
            f'      <span class="step-screen">{step["screen"]}</span>\n'
            f'      <p class="step-text">{step["text"]}</p>\n'
            f'      <div class="step-note">{step["note"]}</div>\n'
            f'      <div class="shot"><img src="{image}" '
            f'alt="Screenshot: {strip_tags(step["title"])}" loading="lazy"></div>\n'
            f"    </div>\n  </div>"
        )
    if open_act:
        parts.append("</section>")

    parts.append("</div>")
    parts.append(footer())
    return "\n".join(parts) + "\n"


if __name__ == "__main__":
    missing = [s["file"] for s in STEPS if not (SHOTS / s["file"]).exists()]
    if missing:
        raise SystemExit(
            "No screenshot for: "
            + ", ".join(missing)
            + "\nRun the capture first: cd web && npx playwright test "
              "--config=walkthroughs/playwright.config.ts finance"
        )
    OUT.write_text(build(), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {len(STEPS)} steps)")
