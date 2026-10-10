r"""One look, one structure and one set of wording rules for every Munitas walkthrough page.

Each walkthrough builder (build_*_walkthrough.py) holds only its own content: the
people, the steps and the screenshots. This module turns that content into a page, so
the pages cannot drift apart again. It owns:

  * the design (the same paper-and-serif look as the feature tour, light and dark),
  * the top bar that links every page to the index and the feature tour,
  * the page shape: opening, at-a-glance strip, how to read, the parts of Munitas,
    the words used, the contents list, the steps, then the next and previous pages,
  * the one master list of platform words and parts, so a term means the same thing
    on every page,
  * the wording check (check_wording) that every builder runs before it writes.

A step is a dict with: title, screen, text, note, actor, and either `file` (a console
screenshot) or `evidence` (ready-made HTML, used for the recorded Python sessions). An
`act` entry starts a new part of the page above that step.

Needs Pillow, listed in docs/tools/requirements.txt.
"""
from __future__ import annotations

import base64
import html
import io
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from PIL import Image

DOCS = Path(__file__).resolve().parent.parent
PUBLIC = DOCS / "public" / "walkthroughs"
SHOTS_ROOT = DOCS.parent / "web" / "walkthroughs" / "shots"
QUALITY = 72

# The console label every screenshot step carries, so a reader always knows which tool
# they are looking at before the route or page name.
CONSOLE = "Munitas console"

# ----------------------------------------------------------------------------------
# The series: the order the pages are read in, and what the index says about each.
# ----------------------------------------------------------------------------------
ORG_TITLES = {
    "health": "Health organisation",
    "finance": "Finance organisation",
    "harbour": "Harbour Clinic",
}

# The stages a reader moves through inside one organisation. An entry in SERIES with a "stage" is listed under that heading on the
# index, and the headings appear in the order the stages first occur.
STAGES = {
    "Set-up: Departments, Roles and Limits": "Before any data arrives, an organisation decides which department owns which data, who may approve access to it, which role each person holds, and what people are allowed to ask for.",
    "Regular data": "Data that does not identify anyone, such as a table of hospital admissions counted by ward and quarter. At first nobody can read the table, not even the person who brought it in. A researcher finds it and asks to read it, and the department that owns it decides. The researcher then uses it from a notebook and makes a new table from a query.",
    "Locked patient data": "Data that does identify patients, such as consultation recordings and a table of clinic appointments about the same patients. Unlike the admissions table, it names people, so every identifying detail is locked under a key made for each patient. An approver decides who may open a few rows or files. A reviewer decides whether the names have been removed well enough to release the result. An auditor can see how everything was used without seeing a name.",
    "Roles and approvers": "A person asks for a role and an approver decides. When a department's usual approver is away, another approver covers for them.",
    "AI agents": "A program that works on data is held to the same rules as a person: it asks for access and waits for an approver to decide. A network architect decides which external hosts a version of it may call.",
    "Custom pipelines": "An organisation can write its own pipelines, which are series of steps that Munitas runs on data. A new version first runs only on development data, and a step that reads recordings needs an approver's agreement.",
    "Audit": "Every request to read data, from a person or a program, is recorded with who asked, what for and why it was allowed or refused. The people who watch can read the record and change nothing.",
    "Closing an organisation": "When an organisation stops using Munitas, it is closed in two stages of 15 days each. Its records can be kept for a legal case and handed over to the people who need them.",
}

SERIES = [
    {"slug": "before-any-data", "org": "health", "stage": "Set-up: Departments, Roles and Limits",
     "title": "Before the programme starts: who owns what, and who may do what",
     "summary": "Before a research programme brings in any new data, the Health organisation already has its structure in "
                "place. The platform administrator walks through it: the departments and who answers for each, the people "
                "and their roles, what each role may read without asking, and the limits on what people may ask for.",
     "you_see": "The departments with their approvers, the people and their roles, what each role may read without "
                "asking, and the limits an organisation has set"},
    {"slug": "a-table-arrives", "org": "health", "stage": "Regular data", "title": "Bring in a table, and see that nobody can read it yet",
     "summary": "A data engineer brings in a table of hospital admissions and a small public list of diagnosis codes. The "
                "admissions table starts closed to everybody, including the person who brought it in. The list is "
                "registered with a claim that it is open to all, and the custodian of the owning department agrees with "
                "the claim.",
     "you_see": "Registering a dataset with an owner, a contract and rows checked and sealed as a table, a version that "
                "nobody can read yet, and a claim that a custodian agrees with"},
    {"slug": "find-and-ask", "org": "health", "stage": "Regular data", "title": "Find a table, ask to read it, and the custodian decides",
     "summary": "A researcher is stopped at the table of admissions that a data engineer brought in, and sees only the public "
                "lookup. The researcher asks for access with a purpose and a reason, the custodian of the owning department "
                "grants it for a limited time, and the table opens.",
     "you_see": "Python and the console side by side: a refusal with its reason, a request, the custodian's decision, and "
                "rows that could not be read a moment before"},
    {"slug": "work-from-a-notebook", "org": "health", "stage": "Regular data", "title": "Sign a notebook in, with a pass that cannot decide anything",
     "summary": "A researcher signs a Python notebook in as themself: the notebook shows a code, the researcher types it in the "
                "console and approves, and the notebook receives a short-lived pass. The pass cannot decide a request, and "
                "the researcher ends it from the list of notebooks.",
     "you_see": "A code typed in the console, a page that says what the notebook will and will not be able to do, a refusal "
                "of the one thing a notebook cannot do, and the sign-in ending"},
    {"slug": "make-a-dataset", "org": "health", "stage": "Regular data", "title": "Make a new dataset from a query, under the same rules",
     "summary": "The researcher makes a new dataset from a query over the admissions and the diagnosis lookup. Munitas "
                "plans it, runs it, and keeps the new dataset as restricted as its inputs. A colleague without access is "
                "refused, a query that reaches too far is stopped, and when the custodian withdraws the access the new "
                "dataset closes too.",
     "you_see": "A plan, a finished dataset that records where it came from, three refusals with reasons, and a dataset "
                "that closes when access is withdrawn"},
    {"slug": "people-arrive", "org": "health", "stage": "Locked patient data", "title": "Bring in patient data, locked under a key for each patient",
     "summary": "A data engineer brings in data that identifies patients. The page first asks whether anybody will ever need "
                "to be erased from it. Three consultation recordings are then locked under a key made for each patient, and a "
                "table of appointments about the same patients is sealed with what identifies them locked too.",
     "you_see": "The question that comes first, every mistake listed before anything is sent, recordings locked and counted "
                "without naming anybody, a table refused and then sealed, and what was done to each column"},
    {"slug": "read-a-few-rows", "org": "health", "stage": "Locked patient data", "title": "Read five locked rows, with an approver's say",
     "summary": "The data engineer who brought in a table of appointments is refused its locked names and reasons, asks to "
                "read five rows for three days, and the custodian of the owning department decides. A researcher has no "
                "way to ask. The platform reports which readable columns could single a person out, and the lease opens "
                "exactly five rows.",
     "you_see": "A refusal with its reason, a request, the custodian's decision with a note, a report on small groups, "
                "and five rows with their locked values opened"},
    {"slug": "open-a-few-files", "org": "health", "stage": "Locked patient data", "title": "Open two of the recordings, with an approver's say",
     "summary": "The data engineer asks to open three of the locked recordings in the page, for an hour. The custodian "
                "allows two, the platform chooses which, and the engineer plays one. When the custodian ends the "
                "permission, the files close again.",
     "you_see": "A request for files, a custodian allowing fewer, a recording playing in the page, and the files closing "
                "when the permission ends"},
    {"slug": "de-identify-and-release", "org": "health", "stage": "Locked patient data", "title": "Remove names from the recordings, and a reviewer decides",
     "summary": "The built-in pipeline removes names, dates and addresses from the three locked recordings and is scored "
                "against their answer keys. The person who started the run cannot decide the result, and neither can the "
                "custodian of the owning department. The de-identification reviewer decides, with a written reason, and a "
                "release widens access by one step.",
     "you_see": "A run on real speech, two people refused for different reasons, a reviewer's measurement and decision, and a "
                "researcher who still cannot read the released result"},
    {"slug": "evidence-without-names", "org": "health", "stage": "Locked patient data", "title": "See how locked data was used, without seeing a name",
     "summary": "The privacy auditor and the data protection officer read the record of everything that happened to the "
                "locked data: who read, who was refused, what was done to each column, and which key was opened and why. "
                "No patient's name appears, and the same person code ties a patient's recording to the same patient's row.",
     "you_see": "An audit page with no names on it, how each column was treated, and every key opening with its reason"},
    {"slug": "people-administration", "org": "health", "stage": "Roles and approvers", "title": "Ask for a role, and see who may grant it",
     "summary": "A researcher asks to hold a role and cannot decide the request alone. The person who runs "
                "Munitas is refused when trying to grant it, and the custodian of a department grants it instead.",
     "you_see": "A request for a role, a refusal with its reason, and a grant that starts as never checked"},
    {"slug": "department-approvers", "org": "health", "stage": "Roles and approvers", "title": "Cover a department while its custodian is away",
     "summary": "The custodian of Cardiology names a colleague from another department as cover. The cover "
                "confirms a claim about Cardiology's data on their own, then the custodian ends the cover. The "
                "platform refuses to remove the last permanent approver and keeps a record of every change.",
     "you_see": "Adding an approver with a reason and an end date, a claim confirmed by the cover, a removal, a "
                "refusal with its reason, and the history of who answered for the department"},
    {"slug": "agent-asks", "org": "health", "stage": "AI agents", "title": "Grant an AI agent permission to read a dataset",
     "summary": "An AI agent that ranks radiology documents is given a new version of its code and run. The run "
                "waits until the custodian of the Radiology department grants it permission to read a dataset, in the same "
                "way a person's request would wait, and a second run has to ask again.",
     "you_see": "An agent version sealed, a run waiting for permission, a request that names who asked and who "
                "will read, and the runs finishing"},
    {"slug": "agent-egress", "org": "health", "stage": "AI agents", "title": "Grant an AI agent permission to call an external host",
     "summary": "A data engineer uploads a new version of a radiology agent and lists the one external host it needs to call. The "
                "version is sealed but cannot be deployed. Neither the engineer nor the custodian who owns the agent can decide the "
                "list: the network architect does, with a written reason. An approved version can then be deployed, and a version "
                "whose host is refused cannot.",
     "you_see": "A version waiting for a decision with its Deploy button greyed out, two people refused for different reasons, "
                "the architect's approval and refusal with their reasons, and what each decision does to the version"},
    {"slug": "custom-pipeline", "org": "health", "stage": "Custom pipelines", "title": "Register a custom pipeline, and run it",
     "summary": "A data engineer in Cardiology registers a pipeline of their own, asks for access to a note, and "
                "runs the pipeline. A reviewer makes the final decision, as for every other pipeline.",
     "you_see": "Registering a pipeline, asking for access, running it, and a reviewer's decision"},
    {"slug": "development-sample", "org": "health", "stage": "Custom pipelines", "title": "Test a new pipeline version on a development sample",
     "summary": "A data engineer in Cardiology has written a pipeline version that is still in development, so it "
                "may run only on development data. The engineer asks for a sample of the locked recordings, a "
                "custodian approves fewer, and the platform draws them. The pipeline version then runs for real "
                "on the sample and is refused on the real recordings. The sample ends, and recordings with no "
                "real person in them are declared synthetic.",
     "you_see": "A request for a sample, a custodian lowering it and the platform drawing it, a sample that says "
                "what it is, a run refused on real recordings and working on the sample, a sample that ends, and "
                "a declaration of synthetic recordings"},
    {"slug": "custom-step", "org": "health", "stage": "Custom pipelines", "title": "Approve a custom step before it runs",
     "summary": "A data engineer in Cardiology uploads a pipeline with a step of their own that reads recordings "
                "and writes files. The platform refuses to run it until an approver of the owning department, who "
                "did not upload it, approves it. The run then makes a new version that waits for a review.",
     "you_see": "A version waiting for an approver, a refused run, an approval, a finished run that links to what "
                "it made, and a new version that records exactly what produced it"},
    {"slug": "audit-decisions", "org": "health", "stage": "Audit", "title": "Read every access decision, including every refusal",
     "summary": "The data protection officer opens a home page of counts, then the list of every request to read data, newest first, and then "
                "only the refusals, each with its date and time, who asked, what for and the reason. Each request is recorded twice, once for whether "
                "it was allowed and once for whether the access was given, and reading the record is itself recorded.",
     "you_see": "A home page of counts and the latest refusals, every request in one list, the refusals alone with the reason for each, and the "
                "auditors' own reading appearing in the list"},
    {"slug": "finance", "org": "finance", "title": "Score card transactions for fraud, with permission",
     "summary": "A fraud operations team brings in card transactions, and a scoring program has to ask the "
                "custodian of the owning department before it may read them. A second custodian in the same "
                "organisation sees none of it.",
     "you_see": "Bringing data in, a program waiting for permission, and two custodians with separate lists"},
    {"slug": "derive-finance", "org": "finance", "title": "Make a new dataset from card transactions",
     "summary": "An analyst is stopped at a restricted table of card transactions, asks the Fraud Operations "
                "custodian for access, and makes a new dataset of large payments made abroad.",
     "you_see": "Python and the console side by side: a refusal, a lease, a plan, and a finished dataset"},
    {"slug": "closing-an-organisation", "org": "harbour", "stage": "Closing an organisation", "title": "Close down an organisation, and keep its records for a legal case",
     "summary": "A small clinic is closing. Its data custodian starts the closing, and an ordinary member cannot "
                "stop it. A law firm asks that the records be kept for a patient claim, two platform "
                "administrators record and approve a legal hold, and everything is deleted only after the hold is "
                "released.",
     "you_see": "A closing with two stages, a refusal, a legal hold that needs two administrators, and a short "
                "record of what was deleted"},
    {"slug": "legal-export", "org": "harbour", "stage": "Closing an organisation", "title": "Hand over a closed clinic's records for a legal case",
     "summary": "A court demands a patient's records from the clinic that is closing. One platform administrator "
                "asks for them, a different one approves, and the custodian named by the legal hold confirms what "
                "is included and names the patient, so that only that patient's rows leave. The platform builds a "
                "signed, encrypted package that the recipient opens with a passphrase.",
     "you_see": "Three people each doing one step, a refusal, a passphrase shown once, a download link, and the "
                "recipient checking the package on their own computer"},
]


# Pages that newer ones have replaced. They are not on the index, not in the Previous and Next chain and not in the tour's list. Each is
# still built, into docs/archive/walkthroughs/, and its old address in docs/public/walkthroughs/ is a short page that goes to the index.
ARCHIVED = [
    {"slug": "bring-in-a-recording", "org": "health", "title": "Bring a recording in, and seal it",
     "summary": "A data engineer registers a dataset for a recording of a cardiology consultation, names the "
                "department that owns it, and tries a file that is not a real recording, which is refused. A real "
                "recording is then uploaded and sealed as version 1, and nothing can be added to a sealed version "
                "afterwards.",
     "you_see": "Registering a dataset with an owner, a file refused because it is not what its name says, and a "
                "sealed version"},
    {"slug": "ask-to-read", "org": "health", "title": "Ask to read a recording, and the custodian decides",
     "summary": "A researcher opens a recording that is more sensitive than the researcher's role may read, and "
                "asks to read it for a stated purpose. The custodian of the owning department decides, and the "
                "decision is recorded and lasts a limited time.",
     "you_see": "A request form where a file would be, a purpose and a reason, the custodian's waiting list, and "
                "a grant for a limited time"},
    {"slug": "remove-names", "org": "health", "title": "Remove names from a recording, and a reviewer decides",
     "summary": "A recording of a cardiology consultation is brought in with its answer key, and a pipeline "
                "removes the names and dates from it. The person who started the run cannot decide the result, "
                "and neither can the department's custodian: the de-identification reviewer decides, with a "
                "written reason. The page ends with where this built-in pipeline is listed and the parts of "
                "Munitas that ran it.",
     "you_see": "A pipeline that removes names, two people refused for different reasons, and a reviewer's "
                "decision with its reason"},
    {"slug": "locked-columns", "org": "health", "title": "Lock what identifies a person, and read it only with permission",
     "summary": "A data engineer in Cardiology seals a table of appointments from two files, so that each "
                "patient's name and reason for visit are locked. The engineer asks to read a few locked rows and "
                "the department's custodian decides. A privacy auditor then checks how the locked columns were "
                "used, without seeing a single name.",
     "you_see": "Two files turned into a sealed table, a refusal that lists every problem, a request and its "
                "decision, a few rows read under permission, a report on small groups, and an audit page with no "
                "names on it"},
    {"slug": "locked-recordings", "org": "health", "title": "Bring in recordings so that one patient can be erased later",
     "summary": "A data engineer in Cardiology brings in recordings of consultations. The page first asks whether "
                "anyone will ever need to be erased from the data, and each recording is then locked under a key "
                "made for its patient as it arrives. A mistake in the list of patients is caught before anything "
                "is sent.",
     "you_see": "The question that comes first, a refusal that lists every mistake, recordings sent in parts and "
                "locked, and a sealed version that counts recordings and patients without naming anybody"},
]


def org_page(org: str) -> str:
    """The file name of the page that lists one organisation's walkthroughs."""
    return f"{org}_walkthrough.html"


def series_entry(slug: str) -> dict:
    return next(e for e in SERIES + ARCHIVED if e["slug"] == slug)


ARCHIVE_DIR = DOCS / "archive" / "walkthroughs"
ARCHIVE_STUB = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="0; url=index.html">
<title>Munitas walkthroughs</title>
<link rel="canonical" href="index.html">
</head>
<body>
<p>This walkthrough has been replaced by newer ones. <a href="index.html">Continue to all walkthroughs</a>.</p>
</body>
</html>
"""


# ----------------------------------------------------------------------------------
# The words and the parts, defined once.
# ----------------------------------------------------------------------------------
PARTS = {
    "console": ("Munitas console", "The Munitas web app that people open in a browser. It asks the control plane "
                                    "for everything it shows."),
    "control_plane": ("Control plane", "Munitas&rsquo;s API service. It decides who may do what, keeps the "
                                       "permanent record of every decision, and starts the work for new datasets."),
    "catalog": ("Catalog", "The part of the control plane that lists tables for tools such as DuckDB and hands them "
                           "a short-lived key to read the files."),
    "storage": ("Storage", "SeaweedFS, the object storage where the files of every dataset are kept."),
    "sandbox_worker": ("Sandbox worker", "A background worker that runs the query for a new dataset in an isolated "
                                         "container named <code>munitas-derive-runner</code>, which has no network "
                                         "connection and no passwords."),
    "client_library": ("Munitas client library", "A small Python library, <code>munitas_client.py</code>, that a "
                                                 "person uses in Python to send requests to the control plane."),
    "duckdb": ("DuckDB", "A free tool for analysing tables. It runs on the person&rsquo;s computer and reads tables "
                         "from Munitas through the catalog."),
}

WORDS = {
    "organisation": ("Organisation", "A company or hospital group that uses Munitas. Each organisation sees only "
                                     "its own data."),
    "department": ("Department", "A team inside an organisation that owns some of its data, such as Cardiology."),
    "dataset": ("Dataset", "A named collection of records, such as a table of patient admissions."),
    "custodian": ("Custodian", "A person who holds the data custodian role, which allows deciding who may read data. A "
                               "custodian decides for the departments they are an approver for."),
    "claim": ("Claim", "A statement, made by the person who brings a dataset in, about how sensitive it is. A "
                       "different approver of the owning department has to confirm it."),
    "approver": ("Department approver", "A custodian who is listed for one department and may approve access to its "
                                        "data and confirm claims about it. A department can have several, and any one "
                                        "of them can act on their own."),
    "access_level": ("Access level", "How restricted a dataset is. Raw is the most restricted, and nobody may read "
                                     "it without permission. Published is the most open."),
    "lease": ("Lease", "Permission, given by the custodian, to read one dataset for one stated purpose and for a "
                       "limited time."),
    "query": ("Query", "A question about a dataset, written in a language called SQL."),
    "sensitivity": ("Sensitivity label", "How identifying a column is. <em>none</em> does not identify anybody, "
                                         "<em>quasi</em> is a detail that could help identify a person when "
                                         "combined with others, and <em>phi</em> is protected health information."),
    "token": ("Token", "A short-lived password that also states why the person wants to read."),
    "sealed": ("Sealed", "Finished and closed for good, so that it can never be edited."),
    "role": ("Role", "A named set of permissions that a person can hold, such as the permission to decide who may read "
                     "a department&rsquo;s data."),
    "deid_reviewer": ("De-identification reviewer", "A role that allows a person to review data from which identifying "
                                                    "details have been removed, and to decide whether it may be released."),
    "platform_admin": ("Platform administrator", "The person who runs Munitas itself. The role gives no say over who "
                                                  "may read any department&rsquo;s data."),
    "principal": ("Principal", "Munitas&rsquo;s word for whichever person or program is being checked."),
    "version": ("Version", "One sealed state of a dataset. Changing the contents means making a new version, never "
                           "editing an old one."),
    "fingerprint": ("Fingerprint", "A value worked out from the exact contents of a file. It changes if even one "
                                   "byte changes, so it shows that a file was not altered."),
    "release": ("Release", "A decision to let a dataset version be read more widely than before. A release changes who "
                           "may read it and moves no data."),
    "request": ("Request", "A written ask to read one dataset for one stated purpose. The custodian of the department "
                           "that owns the dataset decides it."),
    "purpose": ("Purpose", "The reason a person states for reading data. A lease covers that purpose only, unless the "
                           "custodian chooses otherwise."),
    "agent": ("Agent", "A program registered on Munitas, with a named owner, that Munitas runs on somebody&rsquo;s "
                       "behalf instead of a person reading data directly."),
    "agent_version": ("Version of an agent", "One uploaded and sealed copy of an agent&rsquo;s code. Uploading again makes a new version, and the older ones stay."),
    "host": ("Host", "The name of a computer on the internet that a program contacts, such as reports.radiology.example. For an agent, a host is an external address that it asks to be allowed to call."),
    "egress": ("Egress approval", "A network architect&rsquo;s decision on the list of external hosts that one version of an agent asks to call. A version that lists hosts cannot be deployed until the list is approved, and a version that lists none needs no approval."),
    "network_architect": ("Network architect", "A role that decides which external hosts an agent version may call. The person who uploaded the version cannot decide it, and neither can a custodian who does not hold this role."),
    "deploy": ("Deploy", "To make one version of an agent the one that runs when somebody starts a run. Only one version of an agent is active at a time."),
    "run": ("Run", "One execution of an agent or a pipeline, started for a stated purpose."),
    "container": ("Container", "An isolated environment, with no network access of its own, where Munitas runs an "
                               "agent."),
    "pipeline": ("Pipeline", "A series of steps that Munitas runs on a dataset version, each step after the ones it "
                             "depends on. The last step is a gate decision."),
    "gate": ("Gate decision", "The final step of a pipeline, where a person decides whether a dataset version may "
                              "be released to a more open access level. The person who started the run may not "
                              "decide it."),
    "open_for_annotation": ("Open for annotation", "An access level above Under review, where people who label data "
                                                   "may work with it."),
    "closing": ("Closing down an organisation", "Ending an organisation&rsquo;s use of Munitas in two stages of 15 days. In the "
                                           "first its people can read and may cancel. In the second they can do "
                                           "nothing. When both end, everything inside the organisation is deleted."),
    "legal_hold": ("Legal hold", "An instruction from a court, a regulator or a law firm to keep an organisation&rsquo;s "
                                 "records and not delete them while a legal matter is open."),
    "temp_custodian": ("Temporary custodian", "The person a legal hold names to answer for the kept records while the "
                                               "hold stands. The person confirms having read the notice."),
    "dpo": ("Data protection officer", "A person who watches how an organisation handles personal data. The role "
                                       "decides nothing about who may read it."),
    "sweep": ("Sweep", "The check that Munitas makes every few minutes for organisations whose time is up. It deletes "
                       "an organisation only when both periods have ended and no legal hold stands."),
    "legal_export": ("Export for a legal matter", "Producing some of an organisation&rsquo;s kept records for a court, a "
                                                  "regulator or a law firm that has demanded them. It needs a legal hold "
                                                  "in force and three different people."),
    "filter": ("Filter", "Keeping only the rows of a table that match values the custodian names, such as one "
                         "patient&rsquo;s id, so that other people&rsquo;s rows do not leave with them."),
    "manifest": ("Manifest", "The list of every file in a package, each with its size and a fingerprint of its "
                             "contents. The platform signs it, so that changing a file or the list can be noticed."),
    "passphrase": ("Passphrase", "A long secret that opens an encrypted file. It travels separately from the file, "
                                 "so that having the file alone is not enough."),
    "iceberg": ("Iceberg table", "An open way of storing a table that tools such as DuckDB can read directly."),
    "contract": ("Privacy contract", "A file that says, column by column, how identifying each column of a table is and what is "
                                     "done to it when the table is sealed, and how to find the person each row is about."),
    "locked": ("Locked column", "A column whose values are scrambled under a key made for each person. Reading them back needs "
                                "permission. Destroying a person&rsquo;s key later makes their values unreadable for good, "
                                "while the sealed table itself never changes."),
    "person_code": ("Person code", "A label that stands for one person and means nothing on its own. It replaces the number "
                                   "that identified them."),
    "locked_lease": ("Locked-column lease", "Permission, decided by an approver of the owning department, to read a few rows of "
                                             "a table&rsquo;s locked columns for a few days, for a stated purpose. It opens "
                                             "only those rows, never the whole table."),
    "locked_recording": ("Locked recording", "A recording stored scrambled under a key made for the one patient it is about. Reading it "
                                              "back needs the platform to open it, and destroying the patient&rsquo;s key later makes it "
                                              "unreadable for good."),
    "recordings_manifest": ("Manifest of recordings", "A small table, saved as a CSV file, with two columns: the recording and the patient "
                                                      "it is about. It is how the platform knows whose key to lock each recording under."),
    "answer_key": ("Answer key", "A file that holds the correct written text of one recording, used to check how well names were removed "
                                 "from it. It is named after its recording and belongs to the same patient."),
    "custom_step": ("Custom step", "A step of a pipeline whose script was written by the person who registered the pipeline, instead of one of "
                                   "the steps Munitas provides. It runs in a closed container with no internet, no key and no storage address, "
                                   "and can only ask the platform for the recordings it is allowed to read and send back the files it makes."),
    "stage": ("Stage", "Where a version of a pipeline is in its life. In development means the version runs only on development data, "
                       "which is a small sample or recordings with no real person in them. In production means it runs on any data the "
                       "department allows. Retired means it has ended for good and cannot be run again."),
    "development_sample": ("Development sample", "A few recordings drawn by the platform from a sealed version of locked recordings, for "
                                                 "building and testing a pipeline version that is still in development. It names the same "
                                                 "locked recordings as the original and copies none, and it ends on its own."),
    "synthetic": ("Synthetic", "Made up, with no real person in it, such as sentences read aloud by volunteers from invented scripts."),
    "under_review": ("Under review", "The access level of something that no person has reviewed. Nobody may use it until a "
                                     "reviewer decides that it may be released."),
    "auditor": ("Privacy auditor", "A person who checks how locked data was used. The role reads records of use and no "
                                   "personal data."),
    "small_group": ("Small group", "A combination of readable columns, such as postcode and age band, shared by only a few "
                                   "people, so that one of them could be singled out even though no column names anybody."),
}

# ----------------------------------------------------------------------------------
# Actor colours. A page gives its people in order; each takes the next slot.
# ----------------------------------------------------------------------------------
SLOTS = [  # light (fg, bg, line), dark (fg, bg, line)
    (("#6a4c9c", "#efe9f7", "#d8c9ee"), ("#c3a8ec", "#2c2340", "#473465")),
    (("#2a7d6a", "#e3f3ee", "#bfe3d8"), ("#7fd1ba", "#14302a", "#24574a")),
    (("#a4562a", "#fbeee4", "#edcdb4"), ("#f0a771", "#3a2415", "#5e3c22")),
    (("#3949e0", "#edeefc", "#c7caf5"), ("#8b93f7", "#23264a", "#3a3e78")),
    (("#6b7a1f", "#f1f4de", "#d9e0a8"), ("#c2d16a", "#2b3010", "#464f1c")),
    (("#b03a5b", "#fbe7ed", "#efc3cf"), ("#f09ab4", "#3d1522", "#62283a")),
]



def _slot_css() -> str:
    def block(idx: int, tone: int) -> str:
        fg, bg, line = SLOTS[idx][tone]
        return f"--a{idx}: {fg}; --a{idx}-bg: {bg}; --a{idx}-line: {line};"
    light = " ".join(block(i, 0) for i in range(len(SLOTS)))
    dark = " ".join(block(i, 1) for i in range(len(SLOTS)))
    rules = "\n".join(
        f"  .actor-chip.a{i} {{ background: var(--a{i}-bg); border-color: var(--a{i}-line); color: var(--a{i}); }}\n"
        f"  .actor-chip.a{i} .dot, .cast .dot.a{i} {{ background: var(--a{i}); }}"
        for i in range(len(SLOTS)))
    return light, dark, rules


CSS_LIGHT_SLOTS, CSS_DARK_SLOTS, CSS_SLOT_RULES = _slot_css()

CSS = """
<style>
  :root {
    --ink: #1c2333; --ink-soft: #4b5266; --ink-faint: #6b7182;
    --paper: #eae7de; --paper-raised: #f6f4ec; --paper-line: #d8d4c6; --paper-line-strong: #c4bfae;
    --accent: #7a2e2e; --brass: #8a6d3b; --code-bg: #e3e0d3;
    --note: #8a5a00; --note-bg: #fbf1d4; --note-line: #ecd9a0;
    --term-bg: #151a26; --term-ink: #e6e8ee; --term-out: #9aa3b8; --term-line: #232a3b;
    --shadow: 0 1px 2px rgba(28,35,51,0.06), 0 4px 14px rgba(28,35,51,0.05);
    __LIGHT__
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --ink: #eae6da; --ink-soft: #c3bfae; --ink-faint: #9a9787;
      --paper: #14161c; --paper-raised: #1c1f28; --paper-line: #2f3340; --paper-line-strong: #434859;
      --accent: #d47d78; --brass: #d1ac5f; --code-bg: #22252e;
      --note: #e0b64c; --note-bg: #332a0c; --note-line: #54451a;
      --term-bg: #0d1018; --term-ink: #e6e8ee; --term-out: #9aa3b8; --term-line: #232a3b;
      --shadow: 0 1px 2px rgba(0,0,0,0.3), 0 6px 20px rgba(0,0,0,0.35);
      __DARK__
    }
  }
  :root[data-theme="dark"] {
    --ink: #eae6da; --ink-soft: #c3bfae; --ink-faint: #9a9787;
    --paper: #14161c; --paper-raised: #1c1f28; --paper-line: #2f3340; --paper-line-strong: #434859;
    --accent: #d47d78; --brass: #d1ac5f; --code-bg: #22252e;
    --note: #e0b64c; --note-bg: #332a0c; --note-line: #54451a;
    --term-bg: #0d1018; --term-ink: #e6e8ee; --term-out: #9aa3b8; --term-line: #232a3b;
    --shadow: 0 1px 2px rgba(0,0,0,0.3), 0 6px 20px rgba(0,0,0,0.35);
    __DARK__
  }

  :root { --gutter: clamp(16px, 2vw, 32px); }
  * { box-sizing: border-box; }
  html { scroll-behavior: smooth; }
  body {
    margin: 0; background: var(--paper); color: var(--ink);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    font-size: 16px; line-height: 1.62;
  }
  h1, h2, h3, h4 {
    font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Palatino, Georgia, serif;
    font-weight: 600; color: var(--ink); text-wrap: balance;
  }
  a { color: var(--accent); }
  code {
    font-family: ui-monospace, "Cascadia Code", "Segoe UI Mono", "SFMono-Regular", Consolas, monospace;
    background: var(--code-bg); padding: 0.1em 0.4em; border-radius: 3px; font-size: 0.88em;
  }

  /* ---------- top bar, the same on every page ---------- */
  .topbar {
    position: sticky; top: 0; z-index: 50; background: var(--paper-raised);
    border-bottom: 1px solid var(--paper-line);
  }
  .topbar-inner {
    margin: 0; padding: 0 var(--gutter); min-height: 48px;
    display: flex; align-items: center; gap: 22px; flex-wrap: wrap;
  }
  .topbar .mark {
    font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif; font-weight: 700;
    letter-spacing: 0.02em; color: var(--ink); text-decoration: none; margin-right: 6px;
  }
  .topbar a.link { color: var(--ink-soft); text-decoration: none; font-size: 14px; padding: 13px 0 11px;
                   border-bottom: 2px solid transparent; }
  .topbar a.link:hover { color: var(--ink); }
  .topbar a.link[aria-current="page"] { color: var(--ink); border-bottom-color: var(--brass); font-weight: 600; }

  /* ---------- opening ---------- */
  .wrap { margin: 0; padding: 0 var(--gutter) 72px; }
  header.hero { padding: 56px 24px 36px; text-align: center; border-bottom: 1px solid var(--paper-line); margin-bottom: 36px; }
  .crumbs { font-size: 13px; color: var(--ink-faint); margin: 0 0 18px; }
  .crumbs a { color: var(--ink-faint); }
  header.hero .eyebrow {
    text-transform: uppercase; letter-spacing: 0.14em; font-size: 12px; font-weight: 600; color: var(--brass);
    margin-bottom: 14px;
  }
  header.hero h1 { font-size: clamp(28px, 4.2vw, 42px); margin: 0 0 16px; letter-spacing: -0.01em; line-height: 1.18; }
  header.hero p.lede { max-width: 660px; margin: 0 auto; color: var(--ink-soft); font-size: 17px; }
  .glance {
    display: flex; flex-wrap: wrap; justify-content: center; gap: 10px 28px; margin: 26px auto 0;
    max-width: 720px; font-size: 14px; color: var(--ink-soft);
  }
  .glance b { display: block; font-size: 20px; color: var(--ink); font-family: "Iowan Old Style", Georgia, serif; }
  .glance div { min-width: 90px; }
  .cast-title { margin-top: 28px; font-weight: 600; color: var(--ink-soft); font-size: 14px; }
  .cast { display: flex; gap: 12px; justify-content: center; margin-top: 12px; flex-wrap: wrap; }
  .cast .card {
    display: flex; align-items: center; gap: 10px; background: var(--paper-raised);
    border: 1px solid var(--paper-line); border-radius: 10px; padding: 10px 16px; font-size: 14px; text-align: left;
  }
  .cast .dot { width: 10px; height: 10px; border-radius: 50%; flex: none; }
  .cast .name { font-weight: 600; }
  .cast .role { color: var(--ink-faint); font-size: 12.5px; }

  /* ---------- reference boxes ---------- */
  .before {
    margin: 0 0 22px; padding: 18px 22px; border: 1px solid var(--paper-line); border-radius: 12px;
    background: var(--paper-raised);
  }
  .before h2 { margin: 0 0 8px; font-size: 18px; }
  .before ul { margin: 0; padding-left: 20px; color: var(--ink-soft); font-size: 14.5px; max-width: 110ch; }
  .before li { margin-bottom: 6px; }
  .before dl { margin: 0; display: grid; grid-template-columns: max-content 1fr; gap: 6px 18px; font-size: 14px; }
  .before dt { font-weight: 600; }
  .before dd { margin: 0; color: var(--ink-soft); }
  @media (max-width: 640px) { .before dl { grid-template-columns: 1fr; gap: 0; } .before dd { margin-bottom: 8px; } }

  nav.toc {
    background: var(--paper-raised); border: 1px solid var(--paper-line); border-radius: 12px;
    padding: 20px 24px; margin: 30px 0 52px;
  }
  nav.toc .toc-title { font-size: 12px; text-transform: uppercase; letter-spacing: 0.1em; color: var(--ink-faint);
                       font-weight: 600; margin-bottom: 12px; }
  nav.toc ol { margin: 0; padding-left: 22px; columns: 3; column-gap: 32px; }
  @media (max-width: 1000px) { nav.toc ol { columns: 2; } }
  nav.toc li { margin-bottom: 6px; font-size: 14.5px; break-inside: avoid; }
  nav.toc a { color: var(--ink); text-decoration: none; }
  nav.toc a:hover { color: var(--accent); }

  /* ---------- parts and steps ---------- */
  section.act { margin-bottom: 56px; }
  .act-head { display: flex; align-items: baseline; gap: 14px; margin-bottom: 6px; padding-bottom: 14px;
              border-bottom: 2px solid var(--paper-line-strong); }
  .act-head .act-num { font-size: 12.5px; font-weight: 700; letter-spacing: 0.1em; color: var(--brass); }
  .act-head h2 { font-size: 24px; margin: 0; letter-spacing: -0.01em; }
  .act-sub { color: var(--ink-soft); font-size: 15.5px; margin: 10px 0 28px; max-width: 90ch; }

  .actor-chip {
    display: inline-flex; align-items: center; gap: 7px; font-size: 12.5px; font-weight: 600;
    padding: 4px 11px 4px 9px; border-radius: 999px; border: 1px solid var(--paper-line);
  }
  .actor-chip .dot { width: 8px; height: 8px; border-radius: 50%; }
__SLOTS__

  .step { display: flex; gap: 22px; padding: 26px 0; border-bottom: 1px solid var(--paper-line); scroll-margin-top: 64px; }
  .step:last-child { border-bottom: none; }
  .step, .step-body { min-width: 0; }
  .step-num {
    flex: none; width: 40px; height: 40px; border-radius: 10px; background: var(--paper-raised);
    border: 1px solid var(--paper-line-strong); display: flex; align-items: center; justify-content: center;
    font-weight: 700; font-size: 15px; color: var(--ink-soft);
  }
  .step-body { flex: 1; }
  .step-title { font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif; font-size: 19px; font-weight: 600;
                margin: 4px 0 4px; line-height: 1.3; }
  .step-screen { display: block; margin: 0 0 10px; font-size: 12.5px; color: var(--ink-faint); }
  .step-screen::before { content: "Where  "; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase;
                         font-size: 10.5px; color: var(--brass); }
  .step-text { color: var(--ink-soft); font-size: 15.5px; margin: 0 0 14px; max-width: 78ch; }
  .step-text code { color: var(--ink); }

  .shot { border: 1px solid var(--paper-line-strong); border-radius: 10px; overflow: hidden; box-shadow: var(--shadow);
          max-width: 100%; background: var(--paper-raised); }
  .shot img { display: block; width: 100%; height: auto; cursor: zoom-in; }

  .step-note {
    display: flex; gap: 10px; margin: 14px 0 4px; max-width: 100ch; padding: 10px 12px; border-radius: 8px;
    border: 1px solid var(--note-line); background: var(--note-bg); font-size: 14px; color: var(--ink);
  }
  .step-note::before { content: "Look for"; flex: none; font-weight: 700; font-size: 11px; text-transform: uppercase;
                       letter-spacing: 0.06em; color: var(--note); padding-top: 2px; }
  .step-note .note-body { flex: 1; min-width: 0; }

  .term { border-radius: 10px; overflow: hidden; background: var(--term-bg); margin: 8px 0 4px; max-width: 100%; }
  .term pre {
    margin: 0; padding: 12px 14px; overflow-x: auto; font-size: 12.5px; line-height: 1.5; white-space: pre;
    max-width: 100%; font-family: ui-monospace, "Cascadia Mono", "Segoe UI Mono", Consolas, "DejaVu Sans Mono", monospace;
  }
  .term .code { color: var(--term-ink); }
  .term .out { color: var(--term-out); border-top: 1px solid var(--term-line); line-height: 1.25; }
  .term .tag { display: block; padding: 8px 14px 0; font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase;
               color: var(--term-out); }
  .term .who { padding-top: 10px; color: var(--term-ink); letter-spacing: 0.04em; text-transform: none; font-size: 12px; }
  .term .does { padding-top: 3px; padding-bottom: 4px; letter-spacing: 0.01em; text-transform: none; font-size: 12px;
                line-height: 1.45; }
  .term .does code { color: var(--term-ink); background: transparent; padding: 0; }

  /* ---------- previous and next, footer ---------- */
  .pager { display: flex; gap: 14px; margin: 8px 0 0; flex-wrap: wrap; }
  .pager a {
    flex: 1 1 280px; border: 1px solid var(--paper-line); background: var(--paper-raised); border-radius: 12px;
    padding: 14px 18px; text-decoration: none; color: var(--ink); box-shadow: var(--shadow);
  }
  .pager a small { display: block; font-size: 11.5px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--brass);
                   font-weight: 700; margin-bottom: 4px; }
  .pager a.next { text-align: right; }
  footer.page-foot { margin: 0; padding: 28px var(--gutter) 56px; color: var(--ink-faint); font-size: 13px;
                     border-top: 1px solid var(--paper-line); }
  footer.page-foot p { margin: 0 0 8px; }

  /* ---------- the index ---------- */
  .group { margin: 0 0 44px; }
  .group h2 { font-size: 26px; margin: 0 0 4px; }
  .group > p { margin: 0 0 18px; color: var(--ink-soft); }
  .group h3.stage { font-size: 24px; font-weight: 700; margin: 40px 0 4px; padding-top: 22px; border-top: 1px solid var(--paper-line); }
  .group h3.stage:first-of-type { margin-top: 24px; }
  .group h3.stage + p { margin: 0 0 14px; color: var(--ink-soft); font-size: 16px; max-width: 110ch; }
  .group h3.stage { scroll-margin-top: 72px; }
  .tile { scroll-margin-top: 72px; }
  .tile:target { outline: 2px solid var(--brass); outline-offset: 3px; }
  .quicknav { margin: 0 0 28px; padding: 18px 22px; border: 1px solid var(--paper-line); border-radius: 12px; }
  .quicknav h2 { margin: 0 0 10px; font-size: 18px; }
  .quicknav .quicknav-stages { margin: 0; padding: 0; list-style: none; column-count: 2; column-gap: 40px; }
  .quicknav .quicknav-stages > li { break-inside: avoid; margin: 0 0 14px; }
  .quicknav .quicknav-stage { font-size: 16px; font-weight: 700; }
  .quicknav ul { margin: 4px 0 0; padding: 0; list-style: none; }
  .quicknav ul li { margin: 3px 0; font-size: 14.5px; }
  .quicknav ul li a { color: var(--ink-soft); }
  .quicknav .quicknav-n { display: inline-block; min-width: 1.7em; color: var(--ink-faint); }
  .quicknav .quicknav-stages.quicknav-few { column-count: 1; }
  @media (max-width: 800px) { .quicknav .quicknav-stages { column-count: 1; } }
  .people { margin: 0 0 28px; padding: 18px 22px; border: 1px solid var(--paper-line); border-radius: 12px; }
  .people h2 { margin: 0 0 6px; font-size: 20px; }
  .people > p { margin: 0 0 12px; color: var(--ink-soft); font-size: 14.5px; max-width: 110ch; }
  .people table { width: 100%; border-collapse: collapse; font-size: 14.5px; }
  .people th { text-align: left; font-size: 12px; text-transform: uppercase; letter-spacing: 0.1em; color: var(--ink-faint); padding: 6px 14px 6px 0; border-bottom: 1px solid var(--paper-line); }
  .people td { padding: 8px 14px 8px 0; border-bottom: 1px solid var(--paper-line); vertical-align: top; }
  .people td.who { font-weight: 700; white-space: nowrap; }
  .people td.for { color: var(--ink-soft); }
  .tiles { display: grid; grid-template-columns: 1fr; gap: 16px; }
  @media (max-width: 720px) { .tiles { grid-template-columns: 1fr; } }
  .tile {
    display: flex; flex-direction: column; gap: 8px; border: 1px solid var(--paper-line); background: var(--paper-raised);
    border-radius: 14px; padding: 20px 22px; box-shadow: var(--shadow); text-decoration: none; color: var(--ink);
  }
  .tile:hover { border-color: var(--brass); }
  .tile .tag { font-size: 11px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--brass); font-weight: 700; }
  .tile h3 { margin: 0; font-size: 20px; line-height: 1.25; }
  .tile p { margin: 0; color: var(--ink-soft); font-size: 14.5px; }
  .tile .meta { margin-top: auto; padding-top: 6px; font-size: 13px; color: var(--ink-faint); }
  .tile .open { font-weight: 600; color: var(--accent); font-size: 14px; }
  .about { max-width: 700px; margin: 0 auto; }
  .about p { color: var(--ink-soft); font-size: 16.5px; }

  #lightbox { position: fixed; inset: 0; background: rgba(10, 11, 16, 0.82); display: none; align-items: center;
              justify-content: center; padding: 32px; z-index: 100; cursor: zoom-out; }
  #lightbox.open { display: flex; }
  #lightbox img { max-width: 100%; max-height: 100%; border-radius: 8px; box-shadow: 0 24px 64px -16px rgba(0,0,0,0.6); }
  #lightbox .close-hint { position: absolute; top: 20px; right: 24px; color: #e9eaf2; font-size: 13px; opacity: 0.7; }

  @media (max-width: 640px) {
    .step { flex-direction: column; gap: 10px; }
    nav.toc ol { columns: 1; }
    header.hero { padding: 40px 18px 28px; }
    .wrap { padding-bottom: 56px; }
    .topbar-inner { gap: 14px; }
  }
</style>
""".replace("__LIGHT__", CSS_LIGHT_SLOTS).replace("__DARK__", CSS_DARK_SLOTS).replace("__SLOTS__", CSS_SLOT_RULES)

LIGHTBOX = """
<div id="lightbox">
  <span class="close-hint">Click anywhere to close</span>
  <img id="lightbox-img" src="" alt="">
</div>
<script>
  (function () {
    var box = document.getElementById("lightbox");
    var boxImg = document.getElementById("lightbox-img");
    document.querySelectorAll(".shot img").forEach(function (img) {
      img.addEventListener("click", function () { boxImg.src = img.src; boxImg.alt = img.alt; box.classList.add("open"); });
    });
    box.addEventListener("click", function () { box.classList.remove("open"); boxImg.src = ""; });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { box.classList.remove("open"); boxImg.src = ""; }
    });
  })();
</script>
"""

HOW_TO_READ_CONSOLE = [
    "Every step is one screen, or one moment, in a real run. The label under the title names where it happens, "
    "and the step reads in the same order each time: what is happening, the evidence, and then what to look for in it.",
    "<strong>Screenshots</strong> are real pictures of the Munitas console, the web app that people open in a "
    "browser. Click any picture to enlarge it.",
    "Every name of a person, patient or card holder in the example data is invented, and every screenshot is "
    "unedited apart from being cropped to the part that matters.",
]

HOW_TO_READ_CODE = [
    "Every step names the tool or the part of Munitas where it happens. The label under the title says where, and "
    "the step reads in the same order each time: what is happening, the evidence, and then what to look for in it.",
    "<strong>Console steps</strong> happen in the Munitas console, the web app that people open in a browser. "
    "They show real screenshots, and clicking one enlarges it.",
    "<strong>Python steps</strong> happen in Python, on the person&rsquo;s own computer, in a notebook or in a "
    "terminal. Python is a window where a person types a line and sees the answer straight away. From there the "
    "person uses the Munitas client library, which sends requests to Munitas, and DuckDB, a free tool for "
    "analysing tables. Each dark box on the page is one of these steps.",
    "In a dark box, the top part is exactly what was typed and the bottom part is exactly what came back. "
    "<code>me</code> is the client library signed in as the person named on the label, and "
    "<code>custodian</code> is the client library signed in as the custodian.",
    "Every step was run for real by a script that types each one, so the answers are genuine and nothing has "
    "been edited. Every name of a person, patient or card holder in the example data is invented.",
]


# ----------------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------------
# A console screenshot is the whole 1920 by 1080 screen: the menu with the signed-in person at its foot, the header, and the page. Nothing is cut away,
# because the person's name, role and organisation are in the menu and the notes point at them. (Before the console had a fixed menu, the pictures were cut to
# the middle 1280 pixels and to the top of the page.)
DEFAULT_CROP = None


def encoded(path: Path, crop: tuple[int, int, int, int] | None = None) -> str:
    """A screenshot as an inline JPEG, so the page opens from the filesystem with nothing beside it."""
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        if crop:
            rgb = rgb.crop(crop)
        buffer = io.BytesIO()
        rgb.save(buffer, format="JPEG", quality=QUALITY, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def strip_tags(text: str) -> str:
    out, depth = [], 0
    for char in text:
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
        elif depth == 0:
            out.append(char)
    return "".join(out)


def topbar(current: str) -> str:
    def link(href: str, label: str, key: str) -> str:
        mark = ' aria-current="page"' if key == current else ""
        return f'<a class="link" href="{href}"{mark}>{label}</a>'
    return ('<div class="topbar"><div class="topbar-inner">'
            '<a class="mark" href="feature-walkthrough.html">Munitas</a>'
            + link("feature-walkthrough.html", "Feature tour", "tour")
            + link("index.html", "All walkthroughs", "index")
            + "</div></div>")


@dataclass
class Page:
    slug: str                       # file name without "-walkthrough.html"
    title: str                      # the tab title and the opening heading
    eyebrow: str                    # for example "Health organisation"
    lede: str                       # what this story shows, in two or three sentences
    actors: dict                    # key -> (name, role), in the order they appear
    steps: list
    parts: list = field(default_factory=list)   # keys of PARTS this page uses
    words: list = field(default_factory=list)   # keys of WORDS this page uses
    has_code: bool = False          # true when some steps are recorded Python sessions
    capture_note: str = ""          # how the screens and code were recorded, for the footer
    shots_dir: Path | None = None
    crops: dict = field(default_factory=dict)
    evidence: Callable | None = None  # builds the evidence HTML for steps without `file`
    org: str | None = None
    before_steps: str = ""        # html shown after the contents list and before the first step


def _step_evidence(page: Page, step: dict) -> str:
    if "file" in step:
        crop = DEFAULT_CROP  # the whole screen; `page.crops` is no longer used, see DEFAULT_CROP
        alt = html.escape(strip_tags(step["title"]))
        return (f'<div class="shot"><img src="{encoded(page.shots_dir / step["file"], crop)}" '
                f'alt="Screenshot: {alt}" loading="lazy"></div>')
    return page.evidence(step)


def quote_screen_text(note: str) -> str:
    """Screen text that ends with a full stop or a question mark and is followed by more of the note goes in quotation marks, so that
    the note does not read as a sentence that stops and starts again."""
    return re.sub(r"<strong>([^<]*[.?!])</strong>(?=,? [a-z])", lambda m: f"<strong>&ldquo;{m.group(1)}&rdquo;</strong>", note)


def _screen_label(step: dict) -> str:
    if "file" in step:
        return f'{CONSOLE} &middot; {step["screen"]}'
    return step["screen"]


def _glance(page: Page) -> str:
    n = len(page.steps)
    minutes = max(3, round(n * 0.8))
    people = len(page.actors)
    cells = [("Steps", str(n)), ("Person" if people == 1 else "People", str(people)), ("Minutes to read", f"about {minutes}")]
    return '<div class="glance">' + "".join(f"<div><b>{v}</b>{k}</div>" for k, v in cells) + "</div>"


COMMON_WORDS = {"request", "purpose", "run", "role", "stage"}


def completed_words(page: Page) -> list[str]:
    """The page's own list of words, plus every platform word its prose uses that the list leaves out, so that no word is used without a plain-words
    meaning on the page. Words that are also everyday English are left to the page."""
    prose = [page.lede] + [s["act"][2] for s in page.steps if "act" in s] + [s.get("text", "") for s in page.steps] + [s.get("note", "") for s in page.steps]
    text = html.unescape(strip_tags(" ".join(prose))).lower()
    add = [key for key, (term, _meaning) in WORDS.items()
           if key not in page.words and term.lower() not in COMMON_WORDS
           and re.search(rf"\b{re.escape(html.unescape(term).lower())}s?\b", text)]
    return page.words + add


def _reference(page: Page) -> str:
    how = HOW_TO_READ_CODE if page.has_code else HOW_TO_READ_CONSOLE
    out = ['<section class="before">\n  <h2>How to read this page</h2>\n  <ul>\n'
           + "\n".join(f"    <li>{p}</li>" for p in how) + "\n  </ul>\n</section>"]
    if page.parts:
        dl = "\n".join(f"      <dt>{PARTS[k][0]}</dt><dd>{PARTS[k][1]}</dd>" for k in page.parts)
        out.append(f'<section class="before">\n  <h2>The parts of Munitas, and the tools around it</h2>\n  <dl>\n{dl}\n  </dl>\n</section>')
    words = completed_words(page)
    if words:
        dl = "\n".join(f"      <dt>{WORDS[k][0]}</dt><dd>{WORDS[k][1]}</dd>" for k in words)
        out.append(f'<section class="before">\n  <h2>Words used on this page</h2>\n  <dl>\n{dl}\n  </dl>\n</section>')
    return "\n".join(out)


def _pager(slug: str) -> str:
    mine = next((e for e in SERIES if e["slug"] == slug), None)
    if mine is None:
        return ""
    group = [e for e in SERIES if e["org"] == mine["org"]]
    i = group.index(mine)
    parts = []
    if i > 0:
        e = group[i - 1]
        parts.append(f'<a class="prev" href="{e["slug"]}-walkthrough.html"><small>Previous</small>{e["title"]}</a>')
    if i < len(group) - 1:
        e = group[i + 1]
        parts.append(f'<a class="next" href="{e["slug"]}-walkthrough.html"><small>Next</small>{e["title"]}</a>')
    return f'<div class="pager">{"".join(parts)}</div>'


def render(page: Page) -> str:
    cast = "\n".join(
        f'    <div class="card"><span class="dot a{i}"></span><div><div class="name">{name}</div>'
        f'<div class="role">{role}</div></div></div>'
        for i, (name, role) in enumerate(page.actors.values()))
    chip_class = {key: f"a{i}" for i, key in enumerate(page.actors)}
    toc = "\n".join(f'    <li><a href="#s{i}">{strip_tags(s["title"])}</a></li>' for i, s in enumerate(page.steps, 1))
    body = []
    open_act = False
    for i, step in enumerate(page.steps, 1):
        if "act" in step:
            if open_act:
                body.append("</section>")
            num, heading, sub = step["act"]
            body.append(f'<section class="act">\n  <div class="act-head"><span class="act-num">{num}</span>'
                        f'<h2>{heading}</h2></div>\n  <p class="act-sub">{sub}</p>')
            open_act = True
        name = page.actors[step["actor"]][0]
        body.append(
            f'  <div class="step" id="s{i}">\n    <div class="step-num">{i}</div>\n    <div class="step-body">\n'
            f'      <span class="actor-chip {chip_class[step["actor"]]}"><span class="dot"></span>{name}</span>\n'
            f'      <div class="step-title">{step["title"]}</div>\n'
            f'      <span class="step-screen">{_screen_label(step)}</span>\n'
            f'      <p class="step-text">{step["text"]}</p>\n'
            f'      {_step_evidence(page, step)}\n'
            f'      <div class="step-note"><span class="note-body">{quote_screen_text(step["note"])}</span></div>\n'
            f'    </div>\n  </div>')
    if open_act:
        body.append("</section>")
    org = ORG_TITLES.get(page.org or "", "")
    crumbs = (f'<p class="crumbs"><a href="index.html">All walkthroughs</a>'
              + (f' / <a href="{org_page(page.org)}">{org}</a>' if org else "") + "</p>")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{page.title}: a Munitas walkthrough</title>
{CSS}
</head>
<body>
{topbar("")}
<header class="hero">
  {crumbs}
  <div class="eyebrow">{eyebrow_for(page)}</div>
  <h1>{page.title}</h1>
  <p class="lede">{page.lede}</p>
  {_glance(page)}
  <div class="cast-title">Who is involved</div>
  <div class="cast">
{cast}
  </div>
</header>
<div class="wrap">
{_reference(page)}
<nav class="toc">
  <div class="toc-title">On this page</div>
  <ol>
{toc}
  </ol>
</nav>
{page.before_steps}
{chr(10).join(body)}
{_pager(page.slug)}
</div>
<footer class="page-foot">
  <p>{page.capture_note} Every person, patient and card holder in the example data is invented.</p>
  <p><a href="index.html">All walkthroughs</a> &middot; <a href="feature-walkthrough.html">Feature tour</a>
  &middot; Last updated {date.today().isoformat()}.</p>
</footer>
{LIGHTBOX}
</body>
</html>
"""


def eyebrow_for(page: Page) -> str:
    """The line above a page's title is the organisation and the heading the index lists the page under, so the two always agree."""
    entry = next((e for e in SERIES if e["slug"] == page.slug), None)
    if entry and entry.get("stage"):
        return f"{ORG_TITLES[entry['org']]} &middot; {entry['stage']}"
    return page.eyebrow


def write(page: Page) -> Path:
    problems = check_wording(page)
    if problems:
        raise SystemExit(f"{page.slug}: wording problems\n  " + "\n  ".join(problems))
    missing = check_notes(page)
    if missing:
        report = f"{page.slug}: notes name things that are not on their screens\n  " + "\n  ".join(missing)
        if not os.environ.get("MUNITAS_NOTES_WARN"):
            raise SystemExit(report)
        print("WARNING " + report)
    archived = page.slug in {e["slug"] for e in ARCHIVED}
    out = (ARCHIVE_DIR if archived else PUBLIC) / f"{page.slug}-walkthrough.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    rendered = render(page)
    gone = check_references(rendered)
    if gone:
        raise SystemExit(f"{page.slug}: the page names files that do not exist\n  " + "\n  ".join(gone))
    out.write_text(rendered, encoding="utf-8")
    if archived:
        (PUBLIC / out.name).write_text(ARCHIVE_STUB, encoding="utf-8")
    print(f"wrote {out.name} ({out.stat().st_size // 1024} KB, {len(page.steps)} steps)")
    return out


# ----------------------------------------------------------------------------------
# Wording check: the rules from CLAUDE.md that can be checked by machine.
# ----------------------------------------------------------------------------------
CONTRACTION = re.compile(r"\b(?:[A-Za-z]+n&rsquo;t|[A-Za-z]+n't|it&rsquo;s|it's|that&rsquo;s|that's|there&rsquo;s|"
                         r"there's|who&rsquo;s|who's|what&rsquo;s|what's|we&rsquo;re|they&rsquo;re|you&rsquo;re|"
                         r"you&rsquo;ve|I&rsquo;m|let&rsquo;s|let's)\b", re.I)
PRONOUN = re.compile(r"\b(?:he|she|him|his|hers|himself|herself)\b|\bher\b", re.I)
SELF_LABELS = re.compile(r"\b(?:demo|prototype|experiment|internal reference|not yet|previously|legacy)\b", re.I)


def _prose(step: dict) -> str:
    text = " ".join(str(step.get(k, "")) for k in ("title", "text", "note"))
    text = re.sub(r"<code>.*?</code>", " ", text, flags=re.S)
    return html.unescape(strip_tags(text))


def _words(text: str) -> str:
    """Words as they compare: lower case, straight quotes, single spaces."""
    text = text.replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"').replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def check_notes(page: Page) -> list[str]:
    """Every phrase a note sets in bold or as code has to be on the screen at that step.

    The capture saves the words of each screen beside its picture (web/walkthroughs/snap.ts), so this compares what a note says is on screen with what was
    on screen. A phrase that is a date, a count or a name made up at capture time can be left out of the bold on purpose.
    """
    problems = []
    for i, step in enumerate(page.steps, 1):
        if "file" not in step or page.shots_dir is None:
            continue
        saved = page.shots_dir / (step["file"].rsplit(".", 1)[0] + ".txt")
        if not saved.exists():
            problems.append(f"step {i} ({step['file']}): no words were saved with this picture; capture it again")
            continue
        seen = _words(saved.read_text(encoding="utf-8"))
        for phrase in re.findall(r"<(?:strong|code)>(.*?)</(?:strong|code)>", step.get("note", ""), re.S):
            want = _words(html.unescape(strip_tags(phrase)))
            if len(want) >= 2 and want not in seen:
                problems.append(f"step {i} ({step['file']}): not on the screen: {want!r}")
    return problems


REFERENCED_FILE = re.compile(r"(?<![\w/.-])((?:verify|docs/tools|web/walkthroughs|scripts(?:/[a-z]+)?|worker|agent|platform/api(?:/app)?)/[\w./-]+\.(?:py|ts|ps1|sql))")


def check_references(rendered: str) -> list[str]:
    """Every file a page names has to be in the repository, so a renamed check cannot leave a public page pointing at nothing."""
    text = re.sub(r"data:[a-z/+.-]+;base64,[A-Za-z0-9+/=]+", "", rendered)
    root = DOCS.parent
    return sorted({f"{m}" for m in REFERENCED_FILE.findall(text) if "<" not in m and "{" not in m and not (root / m).exists()})


def check_wording(page: Page) -> list[str]:
    problems = []
    texts = [("lede", page.lede)] + [(f"step {i}", _prose(s)) for i, s in enumerate(page.steps, 1)]
    texts += [(f"act {i}", html.unescape(strip_tags(s["act"][2]))) for i, s in enumerate(page.steps, 1) if "act" in s]
    for where, text in texts:
        if "—" in text or "–" in text:
            problems.append(f"{where}: dash used as punctuation")
        for pattern, what in ((CONTRACTION, "contraction"), (PRONOUN, "gendered pronoun"), (SELF_LABELS, "self-describing word")):
            m = pattern.search(text)
            if m:
                s = max(0, m.start() - 30)
                problems.append(f"{where}: {what} {m.group()!r} in ...{text[s:m.end() + 30]}...")
    return problems
