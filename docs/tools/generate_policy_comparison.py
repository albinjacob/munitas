r"""Write docs/internal/design/policy-comparison.html: how Munitas policy compares with OCI IAM policy, with real storage lists.

    .venv\Scripts\python.exe docs\tools\generate_policy_comparison.py

The plain lists of allowed actions shown on the page are read from the running API (the document the platform compiles for
SeaweedFS), never typed in. Secrets are never read: only each key's name and its list of actions. The Rego rules are copied
from platform/policy/access.rego as written. Needs the stack up.

The page lives under docs/internal, which is never published.
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "docs" / "internal" / "design" / "policy-comparison.html"
CONTAINER = "munitas-munitas-api-1"
DISTRO = "Ubuntu-20.04"

# Runs inside the API container. It reads names and actions only, never a secret.
INTROSPECT = r'''
import sys, json, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, "/app")
from app import grants, db
db.pool.open(wait=True)
doc = grants.desired_document()
identities = [{"name": i["name"], "actions": i["actions"]} for i in doc["identities"]]
# A table job's key, built by the two functions that build it for a real job, for the next folder of a real dataset.
sample = None
for i in sorted(identities, key=lambda i: i["name"] != "pipeline_action~health"):
    if i["name"].startswith("pipeline_action~") and i["actions"]:
        first = next((a for a in i["actions"] if a.startswith("Write:") and a.endswith("/*")), None)
        if first:
            bucket, _, rest = first[len("Write:"):].partition("/")
            prefix = rest[:-2]
            nxt = prefix.rsplit("/v", 1)[0] + "/v" + str(int(prefix.rsplit("/v", 1)[1]) + 1)
            sample = {"bucket": bucket, "prefix": nxt,
                      "actions": sorted(set(grants._prefix_actions(bucket, nxt) + grants._write_prefix_actions(bucket, nxt) + ["List:" + bucket]))}
            # A derivation run's key, by the function that builds it, for the first two folders this organisation's role already reads.
            reads = sorted({a[len("Read:"):].split("/", 1)[1][:-2] for a in i["actions"]
                            if a.startswith("Read:" + bucket + "/") and a.endswith("/*")})[:2]
            sample["run_inputs"] = reads
            sample["run_actions"] = sorted(set(sum((grants._read_prefix_actions(bucket, p) for p in reads), [])))
            break
print("@@LIVE@@" + json.dumps({"identities": identities, "table_job": sample}))
'''


def live() -> dict:
    done = subprocess.run(["wsl.exe", "-d", DISTRO, "--", "docker", "exec", "-i", CONTAINER, "python", "-"],
                          input=INTROSPECT, capture_output=True, text=True)
    for line in done.stdout.splitlines():
        if line.startswith("@@LIVE@@"):
            return json.loads(line[len("@@LIVE@@"):])
    raise SystemExit("could not read the live document from the API container. Is the stack up?\n" + (done.stderr or done.stdout)[-600:])


def rego_rule(text: str, name: str) -> str:
    """The first block of the policy file that defines `name`, copied as written so the page cannot drift from the rules."""
    m = re.search(rf"^{name}\b[^\n]*\{{\s*$", text, re.M)
    if not m:
        raise SystemExit(f"rule {name} not found in access.rego")
    end = text.index("\n}", m.end())
    return text[m.start():end + 2]


def block(lines: list[str], limit: int | None = None, note: str = "") -> str:
    shown = lines if limit is None else lines[:limit]
    body = html.escape("\n".join(shown))
    if limit is not None and len(lines) > limit:
        body += html.escape(f"\n... and {len(lines) - limit} more like these")
    return f"<pre>{body}</pre>" + (f'<p class="note">{html.escape(note)}</p>' if note else "")


STYLE = """
:root { --bg:#f7f8fa; --ink:#1b2430; --faint:#5d6b7a; --line:#d9dee5; --card:#ffffff; --head:#eef1f5; --accent:#0b5cad; --good:#17803d; --warn:#9a6b00; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#12161c; --ink:#e6eaf0; --faint:#9aa7b6; --line:#2a323c; --card:#1a2028;
        --head:#222a34; --accent:#60a5fa; --good:#4ade80; --warn:#fbbf24; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }
header, main { padding:0 28px; } header { padding-top:22px; } h1 { margin:0 0 6px; font-size:24px; }
h2 { font-size:19px; margin:34px 0 8px; padding-top:6px; border-top:1px solid var(--line); } h3 { font-size:15px; margin:20px 0 4px; }
p { margin:8px 0; } .lead { color:var(--faint); } .note { color:var(--faint); font-size:13px; margin-top:4px; }
nav.toc { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:8px 18px; margin:14px 0; }
nav.toc ol { margin:6px 0; padding-left:20px; } a { color:var(--accent); text-decoration:none; } a:hover { text-decoration:underline; }
table { border-collapse:collapse; width:100%; background:var(--card); margin:10px 0; }
th, td { border:1px solid var(--line); padding:7px 10px; text-align:left; vertical-align:top; } th { background:var(--head); font-size:13px; }
code, pre { font:13px/1.5 ui-monospace,Menlo,Consolas,monospace; } pre { background:var(--head); border:1px solid var(--line); border-radius:6px; padding:10px 12px; overflow-x:auto; margin:8px 0; }
.two { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:16px; } @media (max-width:900px) { .two { grid-template-columns:minmax(0,1fr); } }
table { table-layout:auto; } td, th { overflow-wrap:anywhere; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:4px 14px 10px; }
.tag { display:inline-block; font-size:12px; border:1px solid var(--line); border-radius:999px; padding:1px 9px; background:var(--card); color:var(--faint); }
.callout { border-left:4px solid var(--accent); background:var(--card); padding:8px 14px; margin:12px 0; border-radius:0 6px 6px 0; }
.callout.warn { border-left-color:var(--warn); }
"""


def build(data: dict) -> str:
    by = {i["name"]: i["actions"] for i in data["identities"]}
    rego = (ROOT / "platform" / "policy" / "access.rego").read_text(encoding="utf-8")
    actor_line = re.search(r"^lifecycle_actor_roles\b[^\n]*$", rego, re.M).group(0)
    read_rules = rego_rule(rego, "baseline_ok") + "\n\n" + rego_rule(rego, "role_reaches")
    worker_rule = actor_line + "\n\n" + rego_rule(rego, "may_set_table_worker")

    def first(prefix: str, with_actions: bool = True, bucket: str | None = None) -> str:
        """The first live identity with this name prefix, preferring one that is about `bucket`, so the examples share one organisation."""
        fallback = None
        for name, actions in by.items():
            if name.startswith(prefix) and (actions or not with_actions):
                if bucket is None or any(f":{bucket}" in a for a in actions):
                    return name
                fallback = fallback or name
        if fallback:
            return fallback
        raise SystemExit(f"no live identity starting with {prefix}")

    role_name = "pipeline_action~health" if by.get("pipeline_action~health") else first("pipeline_action~")
    role_actions = by[role_name]
    organisation_bucket = role_actions[0].split(":")[1].split("/")[0]
    wide = [a for a in role_actions if "/" not in a]
    narrow = [a for a in role_actions if "/" in a]
    counts: dict[str, int] = {}
    for a in role_actions:
        counts[a.split(":")[0]] = counts.get(a.split(":")[0], 0) + 1
    lease_name = first("lease-", bucket=organisation_bucket)
    ingest_name = f"ingest-{organisation_bucket.removeprefix('munitas-')}" if f"ingest-{organisation_bucket.removeprefix('munitas-')}" in by else first("ingest-")
    cat_name = first("cat-", bucket=organisation_bucket)
    person_name = f"notebook_explore~{organisation_bucket.removeprefix('munitas-')}"
    if person_name not in by:
        person_name = first("notebook_explore~", with_actions=False)
    busiest = max((n for n in by if n.startswith("notebook_explore~")), key=lambda n: len(by[n]))
    compartment = data["table_job"]["bucket"].removeprefix("munitas-").capitalize()

    def other_org(name: str) -> str:
        """A note when a live example had to come from a different organisation than the rest of the page."""
        bucket = by[name][0].split(":")[1].split("/")[0]
        if bucket == organisation_bucket:
            return ""
        return (f"No key of this kind exists in the {organisation_bucket.removeprefix('munitas-')} organisation right now, "
                f"so this one is from the {bucket.removeprefix('munitas-')} organisation (bucket {bucket}).")
    job = data["table_job"]
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    keys_total = len(by)
    keys_empty = sum(1 for a in by.values() if not a)

    role_sample = [a for a in narrow if a.startswith("Write:")][:2] + [a for a in narrow if a.startswith("Read:")][:2] + [a for a in narrow if a.startswith("List:")][:2]

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Policy comparison</title><style>{STYLE}</style></head><body>
<header><h1>Munitas policy compared with OCI IAM policy</h1>
<p class="lead">How an access decision is written, made and enforced on Oracle Cloud Infrastructure (OCI), and how the same job is done in Munitas
with the policy engine (OPA) and SeaweedFS object storage. The storage lists below are read from the running platform on {when}.
Internal: this page lives under docs/internal and is never published. <a href="../dev/api-callers.html">API callers</a></p>
<nav class="toc"><b>Contents</b><ol>
<li><a href="#short">The short answer</a></li>
<li><a href="#side">Side by side</a></li>
<li><a href="#read">Reading an OPA rule like an OCI statement</a></li>
<li><a href="#storage">Object storage: one goal, two ways</a></li>
<li><a href="#lists">The plain lists, from the live platform</a></li>
<li><a href="#dataset">Walkthrough: POST a dataset, then read it</a></li>
<li><a href="#differ">Where the two differ, and what that means</a></li>
<li><a href="#sources">Sources and what was not verified</a></li></ol></nav></header>
<main>

<h2 id="short">The short answer</h2>
<p><b>OCI keeps the rule and checks it on every request.</b> You write one statement, such as "this group may use objects in this compartment where the bucket is health-data",
and OCI itself evaluates the condition each time somebody calls Object Storage.</p>
<p><b>Munitas splits the job in two.</b> The policy engine (OPA) decides whether a caller may be given access, using rules in one file. If the answer is yes, the platform
works out the exact list of actions that caller's storage key needs, and writes that list into SeaweedFS. SeaweedFS then enforces only the list. It has no conditions, no groups and no
deny: a request is allowed if its action and path appear in the list, and refused if not.</p>
<div class="callout">In OCI the condition is checked at request time, inside the cloud. In Munitas the condition is checked ahead of time, by the platform, and only the finished answer is handed to storage.</div>

<h2 id="side">Side by side</h2>
<table><thead><tr><th></th><th>OCI IAM</th><th>Munitas</th></tr></thead><tbody>
<tr><td>Where the rules live</td><td>Policy statements stored in the tenancy</td><td>Rego rules in <code>platform/policy/access.rego</code>, plus the lists compiled for storage</td></tr>
<tr><td>Who decides</td><td>OCI IAM, for every request</td><td>OPA decides whether to grant. SeaweedFS enforces the compiled list.</td></tr>
<tr><td>Statement shape</td><td><code>Allow &lt;subject&gt; to &lt;verb&gt; &lt;resource&gt; in &lt;location&gt; where &lt;conditions&gt;</code></td><td>A rule name, then conditions that must all hold. Several rules with one name are alternatives.</td></tr>
<tr><td>Subject</td><td>A group, dynamic group, or any-user / any-group</td><td>The caller's roles, and for storage an identity (one access key)</td></tr>
<tr><td>Action</td><td>Verbs (inspect, read, use, manage, and finer ones such as create, update, delete) and individual permissions</td><td>A rule name such as "set the table worker"; for storage, <code>Read:</code>, <code>Write:</code>, <code>List:</code>, <code>Tagging:</code></td></tr>
<tr><td>Resource</td><td>A resource type, a family, or one resource by identifier</td><td>A dataset version; for storage, a bucket and folder path</td></tr>
<tr><td>Scope</td><td>A compartment or the tenancy</td><td>An organisation, which owns one bucket</td></tr>
<tr><td>Conditions</td><td>A variable compared with a value or pattern, for example <code>target.bucket.name</code> or <code>target.object.name</code>, with <code>any</code> and <code>all</code></td><td>Anything Rego can express, including comparing two values with each other</td></tr>
<tr><td>Deny</td><td>Explicit <code>deny</code> statements exist since November 2025. They are opt-in and take precedence over allow.</td><td>No deny rules. The default is "not allowed", and a refusal carries reasons.</td></tr>
<tr><td>Default</td><td>Nothing is allowed until a statement allows it</td><td>Nothing is allowed until a rule allows it, and nothing is on a storage key's list until the platform puts it there</td></tr>
<tr><td>Time limit</td><td>Not reviewed for this page</td><td>Leases and table-job keys end, and the next compile of the list drops them</td></tr>
</tbody></table>

<h2 id="read">Reading an OPA rule like an OCI statement</h2>
<p>An OPA rule reads like an OCI statement: "Allow <i>these roles</i> to <i>do this action</i> on <i>this resource</i> where <i>these conditions hold</i>". The rule name is the action, each line inside the braces is a condition that must all be true (like <code>where all {{...}}</code>), and several rules with the same name are alternatives (like several Allow statements).</p>
<div class="two"><div class="card"><h3>Munitas rule: who may give an organisation its own table worker</h3>{block(worker_rule.splitlines())}</div>
<div class="card"><h3>The OCI statement it resembles</h3><pre>Allow group PlatformAdmins to manage table-workers in tenancy</pre>
<p class="note">Written for comparison from the documented syntax. It is not a statement that exists in any tenancy.</p></div></div>
<table><thead><tr><th>OCI part</th><th>Munitas part</th></tr></thead><tbody>
<tr><td>Subject: group</td><td><code>input.viewer.roles</code> (here, only <code>platform_admin</code> qualifies)</td></tr>
<tr><td>Verb and resource type</td><td>The rule name, <code>may_set_table_worker</code></td></tr>
<tr><td>Location: compartment</td><td>The organisation (<code>same_tenant</code> in the dataset-read rule below)</td></tr>
<tr><td><code>where target.resource.id = ...</code></td><td><code>input.dataset.version_id</code>: one specific dataset version, as in the lease rule</td></tr>
<tr><td><code>where all {{...}}</code></td><td>Every line inside one rule body</td></tr>
<tr><td>Several Allow statements</td><td>Several rules with the same name; any one is enough</td></tr>
<tr><td>Implicit deny</td><td><code>default may_set_table_worker := false</code></td></tr>
</tbody></table>
<h3>The dataset-read rule</h3>
<p>Two rules must both hold. The caller is in the same organisation as the dataset and states a purpose (<code>baseline_ok</code>). Then the dataset's class must be open enough for one of the caller's roles (<code>role_reaches</code>).
Classes run from most to least sensitive: RAW, UNDER_REVIEW, OPEN_FOR_ANNOTATION, OPEN_FOR_TRAINING, PUBLISHED. Each role has a floor, the most sensitive class it may read, and every human role has the floor PUBLISHED.
Anything more sensitive needs a lease, which is extra access that a second person approved for a limited time.</p>
{block(read_rules.splitlines())}
<div class="callout warn">Where the comparison stops. An OCI condition compares a variable with a fixed value or pattern. Rego can compare two values from the request with each other, for example the lease rule
requires that the approver is not the person asking (<code>lease.approved_by != input.principal.id</code>), so nobody approves their own access. Munitas also has no ladder of verbs; the nearest thing is each role's floor.</div>

<h2 id="storage">Object storage: one goal, two ways</h2>
<p><b>Goal.</b> One table job may read and write only its own folder, <code>{html.escape(job["prefix"])}</code>, in its organisation's bucket <code>{html.escape(job["bucket"])}</code>, for as long as the job runs.</p>
<div class="two"><div class="card"><h3>OCI Object Storage</h3>
<pre>Allow dynamic-group table-job-1234 to use objects in compartment {html.escape(compartment)}
  where all {{target.bucket.name='{html.escape(job["bucket"])}',
             target.object.name='{html.escape(job["prefix"])}/*'}}</pre>
<p class="note">Written from Oracle's Object Storage policy reference, which lists <code>target.bucket.name</code>, <code>target.object.name</code> (string or pattern) and bucket tags as condition variables.
It has not been run on OCI. Whether a listing can be limited to one folder in OCI was not checked.</p></div>
<div class="card"><h3>Munitas on SeaweedFS</h3>
{block(["identity: tablejob-1234"] + ["actions:  " + a if n == 0 else "          " + a for n, a in enumerate(job["actions"])])}
<p class="note">Produced by the two functions that build a job's list (<code>_prefix_actions</code> and <code>_write_prefix_actions</code>) for the next folder of a real dataset. No table job is running now,
so this is not read from the live document. The real thing is checked by verification U109.</p></div></div>
<table><thead><tr><th>Idea</th><th>OCI</th><th>SeaweedFS</th></tr></thead><tbody>
<tr><td>Who</td><td>A group or dynamic group</td><td>One identity, which is one access key</td></tr>
<tr><td>What</td><td>A verb on a resource type</td><td>One string per action: <code>Read:</code>, <code>Write:</code>, <code>List:</code></td></tr>
<tr><td>Which bucket</td><td><code>target.bucket.name = ...</code></td><td>The bucket name written into every string</td></tr>
<tr><td>Which folder</td><td><code>target.object.name = '.../*'</code></td><td>The folder path written into every string, once with <code>/*</code> and once without</td></tr>
<tr><td>Which organisation</td><td>A compartment</td><td>The organisation's own bucket: the key names only that bucket</td></tr>
<tr><td>Conditions, tags, deny</td><td><code>where</code> clauses, tag conditions, deny statements</td><td>None. A string either lists the path or does not.</td></tr>
<tr><td>Ends with the job</td><td>You remove the statement</td><td>The platform stops writing the key into the document at its next compile</td></tr>
</tbody></table>
<p>Both forms of each path are present because SeaweedFS reads <code>Read:bucket/folder</code> as that one path and not the objects beneath it. The <code>/*</code> form covers the objects, and the bare form covers a listing of the folder itself.</p>

<h2 id="lists">The plain lists, from the live platform</h2>
<p>SeaweedFS holds {keys_total} identities right now. {keys_empty} of them have an empty list, which means the key exists but opens nothing: for example a role that no dataset in that organisation has justified.
Below are real examples of each kind. Only names and lists are read; no secret is.</p>

<h3>1. A role's key for one organisation: <code>{html.escape(role_name)}</code> <span class="tag">standing, one per role and organisation</span></h3>
<p>This is the key the pipeline worker uses for the organisation named after the tilde. It holds {len(role_actions)} actions: {", ".join(f"{n} {k}" for k, n in sorted(counts.items()))}.
The bucket-wide entries come from the policy, and the per-folder entries are added one dataset version at a time, as the platform justifies them.</p>
{block(wide, note="Bucket-wide: the role's standing reach inside its own organisation's bucket. No other organisation's bucket appears in this list.")}
{block(role_sample, note=f"A sample of the {len(narrow)} per-folder entries. A write entry appears when a pipeline task asks for write access (POST /write-credentials) and expires unless renewed.")}

<h3>2. A lease's key: <code>{html.escape(lease_name)}</code> <span class="tag">temporary, one per approved lease</span></h3>
<p>A lease is extra, time-limited access that a second person approved. Its key opens one dataset version, for reading and listing, and nothing else. It is removed from the document when the lease ends or is revoked.</p>
{block(by[lease_name], note=other_org(lease_name))}

<h3>3. The organisation's standing upload key: <code>{html.escape(ingest_name)}</code> <span class="tag">standing, one per organisation</span></h3>
<p>Used for putting uploaded data into the organisation's own bucket. It is wide inside that one bucket and opens nothing outside it.</p>
{block(by[ingest_name])}

<h3>4. A catalog key: <code>{html.escape(cat_name)}</code> <span class="tag">rolling</span></h3>
<p>Handed out through the Iceberg catalog for reading one table. Each catalog identity covers the previous, the current and the next rotation, so a key handed out a moment ago is always already in the document.</p>
{block(by[cat_name], note=other_org(cat_name))}

<h3>5. A table job's key <span class="tag">temporary, one per job</span></h3>
<p>This key is shown in the previous section. It holds read and write access to one folder, plus a bucket-wide listing, and it exists only while the job is pending or running.</p>

<h3>5a. A task's own key: a derivation run, a pipeline run or an agent run <span class="tag">temporary, one per task</span></h3>
<p>A derivation is a query that makes a new dataset from existing ones. Each run of a derivation, each pipeline run that adopts a sealed version, and each agent run reads its inputs with a key of its own. The key lists only the input folders that the platform has allowed that task to read, and it contains
no listing and no write access. It exists only while the task is alive. A derivation run is alive while it is running and for no longer than the six-hour life of its task credential. A pipeline run or an agent run is alive until it ends, but only while it keeps asking: if it has not asked for six hours, its key is removed, and asking again brings the key back. This is how an agent run that has waited a long time for a person can resume.</p>
{block(["identity: run-<first twelve characters of the run's identifier>"] + ["actions:  " + a if n == 0 else "          " + a for n, a in enumerate(job["run_actions"])])}
<p class="note">This list is produced by the function that builds a task's key, for two folders that this organisation's role can already read. No such task is in progress at the moment, so it is not read from the live document. Verifications U114 and U116 read the real key of a real task of each kind and check that it opens its allowed inputs and nothing else.</p>

<h3>6. A researcher's workspace role: <code>{html.escape(person_name)}</code> <span class="tag">held by the workspace, not by a person</span></h3>
<p>A person never holds a storage key. A researcher reaches data through a workspace that holds this key on their behalf. This list is empty because nothing in that organisation has justified a read for the role yet.
For comparison, the same role in another organisation, <code>{html.escape(busiest)}</code>, holds {len(by[busiest])} actions, because reads there have been justified.</p>
{block(by[person_name] or ["(empty list: opens nothing)"])}

<h2 id="dataset">Walkthrough: POST a dataset, then read it</h2>
<p>A dataset's life touches both layers. The first column is what the caller sends or does; the next ones say what checks it and what changes in storage.</p>
<table><thead><tr><th>Step</th><th>Who proves what</th><th>What the platform decides</th><th>What changes in storage</th></tr></thead><tbody>
<tr><td><b>1. Register.</b> <code>POST /datasets/register</code> with the organisation, name, owning department, who registered it, where it came from, and the class being claimed.</td>
<td>A signed-in person. The route refuses a body that names another organisation or another person.</td>
<td>No policy question is asked, because registering exposes nothing. The department must exist and belong to the organisation. A claim below RAW is recorded as "asserted" until the custodian confirms it.</td>
<td>Nothing. The response says "Nothing is readable yet".</td></tr>
<tr><td><b>2. Upload.</b> <code>POST /datasets/{{id}}/files</code></td>
<td>The same signed-in person, in the dataset's organisation. A dataset of another organisation is reported as not found.</td>
<td>The upload size is limited.</td>
<td>The bytes land in the organisation's own bucket, through its standing upload key (list 3). The key's list does not change.</td></tr>
<tr><td><b>3. Seal.</b> <code>POST /datasets/{{id}}/seal</code></td>
<td>The same signed-in person.</td>
<td>The platform creates version 1 and fixes its folder as <code>&lt;organisation&gt;/&lt;dataset id&gt;/v1</code>. If a class below RAW was claimed, the custodian must agree before it is readable.</td>
<td>Still no list changes. The folder exists, but no role has yet been justified to read it.</td></tr>
<tr><td><b>4. Ask to read.</b> <code>POST /credentials</code> with the principal, its roles, the organisation, the dataset version and a purpose.</td>
<td>The worker token, or a task credential for the same principal, or the signed-in person for themselves. The platform then looks up the roles the principal really holds, not the roles it claims.</td>
<td><b>OPA decides.</b> It receives the principal, its roles and any active leases, the dataset's organisation, version and class, and the purpose. It answers allow or deny with reasons, and either answer is written to the audit log. If OPA cannot be reached, the answer is deny.</td>
<td>When the answer is allow, the platform records the decision as the justification and then recompiles the document. Which key the caller receives depends on the caller. A derivation run, a pipeline run or an agent run receives its own key (list 5a), which gains a read of this one folder. A caller whose read was justified by a lease receives that lease's key (list 2). Any other caller receives the role's key (list 1), which gains <code>Read:</code> and <code>List:</code> for this folder. The key is returned only after the new document is in place.</td></tr>
<tr><td><b>5. Ask to write.</b> <code>POST /write-credentials</code> by a pipeline task.</td>
<td>The worker token and the task's signed credential.</td>
<td>The platform reserves the next version's folder and records a write grant.</td>
<td>The role's list gains <code>Write:</code> for that one folder. The entry lapses unless renewed.</td></tr>
<tr><td><b>6. The grant ends.</b> A lease expires or is revoked, a job finishes, or a write grant stops being renewed.</td>
<td>Nobody calls anything.</td>
<td>The platform recomputes the whole document from its records. Whatever is no longer justified is not in it.</td>
<td>The entry disappears from the list. SeaweedFS refuses that key's next request for it.</td></tr>
</tbody></table>
<h3>Example request bodies</h3>
<div class="two"><div class="card"><h3>Step 1: register</h3><pre>POST /datasets/register
{{
  "tenant_id": "health",
  "name": "clinic-notes-2026",
  "department_id": "&lt;owning department&gt;",
  "registered_by": "&lt;a signed-in person&gt;",
  "provenance": "internal_regulated",
  "declared_class": "RAW",
  "source_kind": "upload"
}}</pre></div>
<div class="card"><h3>Step 4: ask to read</h3><pre>POST /credentials
{{
  "principal": "&lt;the principal&gt;",
  "principal_kind": "workload",
  "roles": ["pipeline_action"],
  "tenant_id": "health",
  "dataset_version_id": "&lt;version id&gt;",
  "purpose": "&lt;stated purpose&gt;",
  "task_credential": "&lt;signed token for this task&gt;"
}}</pre></div></div>
<p class="note">Values in angle brackets are placeholders. The field names come from the request models in <code>platform/api/app/ingest.py</code> and <code>models.py</code>.</p>

<h2 id="differ">Where the two differ, and what that means</h2>
<ul>
<li><b>SeaweedFS has no policy language.</b> Its entire policy is a list of strings per key. What an OCI <code>where</code> clause does inside OCI, Munitas does in the platform, ahead of time. To learn why a key can or cannot do something, read the platform's records and decision log, because the list in SeaweedFS shows the result and not the reasons.</li>
<li><b>Two layers here, one layer in OCI.</b> OPA answers "may this caller have access". SeaweedFS answers "does this key's list include this path". A mistake in the platform's list would be trusted by SeaweedFS, which is why verification compares the live document with what it should be, and checks that no key names two organisations' buckets.</li>
<li><b>Timing.</b> OCI reacts to a changed statement at once. In Munitas a key can outlive its job until the next compile of the document, which the platform triggers when the job, lease, run or grant ends. A derivation run that never finishes is marked as failed after six hours, and a pipeline run or an agent run that stops asking loses its key after six hours. In both cases the key is removed at the next compile.</li>
<li><b>Listing is wider than a folder.</b> SeaweedFS authorises a listing on the whole bucket and never on a folder inside it, so a job's key carries a bucket-wide <code>List:</code>. A listing shows names, not contents. Reading and writing stay on the one folder. Whether OCI can restrict a listing to a folder was not checked.</li>
<li><b>Standing reach inside one organisation.</b> The pipeline role's key holds bucket-wide <code>Read:</code> and <code>List:</code> on its own organisation's bucket (list 1). Derivation runs, pipeline runs and agent runs no longer use that key, because each receives a key of its own (list 5a). The worker's own reads of its earlier steps' output, the staging of uploaded files, and the workspace roles still use the role's key, so those can read the whole of their organisation's bucket.</li>
</ul>

<h2 id="sources">Sources and what was not verified</h2>
<p>OCI facts were read from Oracle's documentation on {when[:10]}:
<a href="https://docs.oracle.com/en-us/iaas/Content/Identity/Concepts/policysyntax.htm">policy syntax</a>,
<a href="https://docs.oracle.com/en-us/iaas/Content/Identity/policysyntax/denypolicies.htm">deny policies</a>,
<a href="https://docs.oracle.com/en-us/iaas/Content/Identity/Reference/objectstoragepolicyreference.htm">Object Storage policy reference</a>.
Munitas facts come from <code>platform/policy/access.rego</code>, <code>platform/api/app/grants.py</code>, <code>seaweed.py</code>, <code>main.py</code> and <code>ingest.py</code>, and the lists above from the running platform.</p>
<ul>
<li>The OCI statements on this page are written from the documented syntax and have not been run on OCI.</li>
<li>One Oracle page still says policies cannot deny, while the deny page documents deny statements. The newer deny page is followed here.</li>
<li>The full list of condition variables for each OCI service was not reviewed. Only the Object Storage ones were.</li>
<li>Step 4 and step 5 were read from the code. The lists were read live, but this page did not run a new dataset through every step.</li>
</ul>
</main></body></html>"""


def main() -> int:
    data = live()
    if not data.get("table_job"):
        raise SystemExit("no pipeline write entry found in the live document to build the table job example from")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(data), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(data['identities'])} live identities read")
    return 0


if __name__ == "__main__":
    sys.exit(main())
