r"""Write docs/internal/dev/services.html and docs/internal/dev/logins.html.

Run automatically by start-dev.ps1, right after the backend containers come
up; run it by hand after that if something changed since (a role granted, a
department renamed) and you want the pages caught up sooner:

    .venv\Scripts\python.exe docs\tools\generate_services_page.py

Two pages, because they answer two questions and go stale for different
reasons. SERVICES is every address this stack publishes on this machine, what
each one is for and who would open it. LOGINS is who to sign in as, and the
one development password. They link to each other.

Generated rather than hand-written. The ports come from `docker-compose.yml`,
the logins from `infra/kratos/identities.json`, the password from
`infra/kratos/seed-identities.py`, and the roles, departments and organisation
notes from the running database. Nothing here is a second copy of any of that,
so a port or a person that moves does not leave a page quietly lying.

The database is optional. With nothing running, LOGINS still lists every email
and says plainly that the roles could not be read, which is more useful than
refusing to write the page.

Both pages are self-contained and open from the filesystem. SERVICES checks
each address from the browser when opened, so it says which services are up.
"""
from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent
ROOT = DOCS.parent
SERVICES_OUT = DOCS / "internal" / "dev" / "services.html"
LOGINS_OUT = DOCS / "internal" / "dev" / "logins.html"

IDENTITIES = ROOT / "infra" / "kratos" / "identities.json"
SEEDER = ROOT / "infra" / "kratos" / "seed-identities.py"
COMPOSE = ROOT / "docker-compose.yml"

DSN = os.environ.get(
    "MUNITAS_PG_DSN", "postgresql://munitas:munitas@localhost:5432/platform"
)

# What each service is and who would open it, in the words somebody testing
# would use. The port is not written here: it is read from docker-compose.yml
# by `service` name, so a port that moves there moves on the page too.
SERVICES = [
    {
        "name": "Console",
        "service": None,
        "url": "http://localhost:5173",
        "runs": "Host, started by start-dev.ps1",
        "group": "The platform",
        "what": "The web console. Everything a person does goes through it: registering data, asking for access, deciding a request, running a pipeline.",
        "who": "Everyone. This is the front door, and the only address most testing needs.",
        "signin": "A Munitas login. See the logins page.",
    },
    {
        "name": "API",
        "service": "munitas-api",
        "port": 8000,
        "runs": "Compose",
        "group": "The platform",
        "what": "The control plane the console talks to. The only thing that may touch the database, the storage and the policy engine.",
        "who": "Anybody testing an endpoint directly, or reading a refusal the console summarised.",
        "signin": "A session cookie from the console, or a Kratos bearer token.",
    },
    {
        "name": "API reference, live",
        "service": "munitas-api",
        "port": 8000,
        "path": "/docs",
        "runs": "Compose",
        "group": "The platform",
        "what": "Every endpoint with its request and response shape, generated from the running service. docs/public/reference/api-reference.html is the committed copy of the same thing.",
        "who": "Anybody calling the API by hand, or checking what a route actually returns.",
        "signin": "None to read it. Calling an endpoint from it still needs a session.",
    },
    {
        "name": "Worker",
        "service": None,
        "url": None,
        "runs": "Host, python -m worker.main",
        "group": "The platform",
        "what": "Runs everything: the de-identification pipeline (transcription, detection, redaction), Hugging Face fetches, agent runs, and pipeline DAGs. Needs a GPU and the WSL2 host/sandbox split, so it only runs this way on Windows + WSL2. It has no page of its own: watch it through the Temporal console.",
        "who": "Anybody actually exercising the audio pipeline or agent runs, on the machine that setup is built for.",
        "signin": "None.",
    },
    {
        "name": "Worker, lite",
        "service": None,
        "url": None,
        "runs": "Compose, --profile quickstart",
        "group": "The platform",
        "what": "The GPU-free alternative to the worker above: runs pipeline DAGs and the built-in count_records starter pipeline, ordinary containerized Docker on any OS. Does not run the de-identification pipeline, Hugging Face fetches, or agent runs -- none of those are needed to register a dataset, request access, have it granted, run a pipeline, and see a real gate decision. Started instead of the host worker, never alongside it.",
        "who": "Anybody on a machine without the Windows + WSL2 GPU setup who wants to see the governance mechanism work.",
        "signin": "None.",
    },
    {
        "name": "Sign-in service",
        "service": "kratos",
        "port": 4433,
        "runs": "Compose",
        "group": "The platform",
        "what": "Ory Kratos. Handles the login itself; the console drives it and you would not normally open it directly.",
        "who": "Whoever is debugging a login that will not complete.",
        "signin": "None.",
    },
    {
        "name": "Sign-in administration",
        "service": "kratos",
        "port": 4434,
        "runs": "Compose",
        "group": "The platform",
        "what": "Creates and edits identities. Published so the seeding script can run from this machine.",
        "who": "The seeding script. Local only, and it would never be exposed anywhere real.",
        "signin": "None, which is exactly why it stays local.",
    },
    {
        "name": "Policy engine",
        "service": "opa",
        "port": 8181,
        "runs": "Compose",
        "group": "The platform",
        "what": "Open Policy Agent. Decides every access question. A rule can be tested directly by posting to /v1/data/munitas/access.",
        "who": "Whoever is working on a rule, or asking why somebody was refused.",
        "signin": "None.",
    },
    {
        "name": "Temporal console",
        "service": "temporal-ui",
        "port": 8080,
        "runs": "Compose",
        "group": "Running work",
        "what": "Every pipeline run and background job, step by step, with the input and output of each one.",
        "who": "The first place to look when a run seems stuck, and the only place that distinguishes stuck from slow.",
        "signin": "None.",
    },
    {
        "name": "Temporal service",
        "service": "temporal",
        "port": 7233,
        "scheme": "grpc",
        "runs": "Compose",
        "group": "Running work",
        "what": "The workflow engine the worker connects to. Not a web page.",
        "who": "The worker. People use the console above.",
        "signin": "None.",
    },
    {
        "name": "Object storage, S3",
        "service": "seaweedfs",
        "port": 8333,
        "runs": "Compose",
        "group": "Storage",
        "what": "Where the files actually are. Each organisation has its own bucket, reachable only with that organisation's own key.",
        "who": "Scripts and the platform. There is no page to browse here.",
        "signin": "An S3 key the platform mints per lease, never a shared one.",
    },
    {
        "name": "Object storage, file browser",
        "service": "seaweedfs",
        "port": 8888,
        "path": "/buckets/",
        "runs": "Compose",
        "group": "Storage",
        "what": "Browse the raw stored files. One bucket per organisation, which is the isolation claim made visible.",
        "who": "Whoever wants to confirm with their own eyes that a file landed where it should.",
        "signin": "None, so treat it as a local debugging tool rather than part of the platform.",
    },
    {
        "name": "Object storage, master",
        "service": "seaweedfs",
        "port": 9333,
        "path": "/dir/status",
        "runs": "Compose",
        "group": "Storage",
        "what": "Volume allocation. This is where the volume pool figure on the housekeeping screen comes from.",
        "who": "Whoever is investigating storage running out, though the housekeeping screen says it in plainer words.",
        "signin": "None.",
    },
    {
        "name": "Database",
        "service": "postgres",
        "port": 5432,
        "scheme": "postgresql",
        "runs": "Compose",
        "group": "Storage",
        "what": "Postgres. The platform's own database is `platform`; Temporal, Kratos, MLflow and Label Studio each have their own beside it.",
        "who": "Whoever needs to see a row rather than a screen. Writing to it by hand is how the governance rules get bypassed, so read rather than write.",
        "signin": "User `munitas`, password `munitas`, unless PG_USER and PG_PASSWORD were set.",
    },
    {
        "name": "Experiment tracking",
        "service": "mlflow",
        "port": 5000,
        "runs": "Compose, --profile full. start-dev.ps1 starts it",
        "group": "Running work",
        "what": "MLflow. What the de-identification pipeline measured, run by run, and the score card each gate decision cites, so a de-identification run cannot finish without it. Coordinates only: the text that leaked is never sent here.",
        "who": "Whoever is judging whether the pipeline got better or worse.",
        "signin": "None.",
    },
    {
        "name": "Tracing",
        "service": "jaeger",
        "port": 16686,
        "runs": "Compose, --profile full",
        "group": "Only with --profile full",
        "what": "Jaeger. One trace per request, each span tagged with the access level it touched.",
        "who": "Whoever is chasing where a request spent its time, or which class a step really touched.",
        "signin": "None.",
    },
    {
        "name": "Annotation tool",
        "service": "label-studio",
        "port": 8081,
        "runs": "Compose, --profile full",
        "group": "Only with --profile full",
        "what": "Label Studio. Where a reviewer would label. Its own accounts, not the platform's: nothing is wired between them yet.",
        "who": "Nobody yet. It is here so the hand-off can be built against something real.",
        "signin": "Its own sign-up, created on first use.",
    },
]

GROUP_ORDER = ["The platform", "Running work", "Storage", "Only with --profile full"]

ROLE_LABEL = {
    "data_custodian": "Data custodian",
    "dpo": "Data protection officer",
    "notebook_explore": "Researcher",
    "pipeline_operator": "Data engineer",
    "platform_admin": "Platform administrator",
    "hybridops": "Support and reliability",
    "deid_reviewer": "De-identification reviewer",
    "analyst": "Analyst",
    "network_architect": "Network architect",
}

# What somebody testing would actually use each person for. Keyed on role, so
# a new person with a known role gets a sensible line without an edit here.
GOOD_FOR = {
    "data_custodian": "Decide access requests for their own department, and be refused another department's.",
    "dpo": "Read the audit log, confirm a role is still needed, end an appointment.",
    "notebook_explore": "Ask for access to a dataset, and wait to be granted or refused.",
    "pipeline_operator": "Register a dataset, upload or fetch data, seal it, start a pipeline.",
    "platform_admin": "Storage housekeeping and closing an organisation. Grants nothing and reads no data, by design.",
    "hybridops": "Support and reliability work, held alongside the administrator role.",
    "deid_reviewer": "Decide a de-identification gate: see what the pipeline missed, then release or refuse.",
    "analyst": "Read what has been released, and nothing wider.",
    "network_architect": "Approve or refuse the external hosts an agent version declares before it may deploy.",
}

PURPOSE_LABEL = {
    "production": "Real",
    "canary": "Test",
    "scratch": "Disposable",
    "retired": "Closed",
}


def password() -> str:
    """The one development password, read from the seeder rather than copied."""
    match = re.search(
        r'^PASSWORD = "([^"]+)"', SEEDER.read_text(encoding="utf-8"), flags=re.M
    )
    if not match:
        raise SystemExit(f"No PASSWORD line found in {SEEDER}")
    return match.group(1)


def published_ports() -> dict[str, list[int]]:
    """Host ports per Compose service, read from docker-compose.yml.

    A small parse rather than a YAML dependency: this is the only thing read
    out of that file, and its shape here is a service key and a
    `- "${PORT_X:-8000}:8000"` list under it -- host ports are
    config.json-driven (see PORT_* substitution in docker-compose.yml and
    scripts/render_ports_env.py), so what's actually extracted here is each
    mapping's default, which is what this stack runs on unless .env
    overrides it. A plain `- "8000:8000"` literal (nothing left in
    docker-compose.yml today, but tolerated) still matches too.
    """
    ports: dict[str, list[int]] = {}
    service = None
    pattern = re.compile(
        r'^\s+- "(?:\$\{PORT_[A-Z0-9_]+:-(\d+)\}|(\d+)):(\d+)"'
    )
    for line in COMPOSE.read_text(encoding="utf-8").splitlines():
        top = re.match(r"^  ([a-z0-9][a-z0-9_-]*):\s*$", line)
        if top:
            service = top.group(1)
            continue
        mapped = pattern.match(line)
        if mapped and service:
            host_port = int(mapped.group(1) or mapped.group(2))
            ports.setdefault(service, []).append(host_port)
    return ports


def people_from_database() -> tuple[dict, dict, str | None]:
    """Roles, departments and organisation notes, read from the running stack.

    Returns ({directory_id: row}, {tenant_id: row}, error). The error is
    carried rather than raised: a page listing the logins is still worth
    having when the thing those logins are for is not running.
    """
    try:
        import psycopg
    except ImportError:
        return {}, {}, "psycopg is not installed in the Python running this script"

    try:
        with psycopg.connect(DSN, connect_timeout=5) as conn:
            directory = {
                row[0]: {
                    "tenant": row[1],
                    "label": row[2],
                    "roles": row[3] or [],
                    "departments": row[4],
                    "ended_at": row[5],
                }
                for row in conn.execute(
                    """select d.id, d.tenant_id, d.label, d.roles,
                              coalesce(string_agg(dep.name, ', '), ''), d.ended_at
                         from directory d
                         left join department dep on dep.custodian = d.id
                        where d.kind = 'human'
                        group by d.id, d.tenant_id, d.label, d.roles, d.ended_at"""
                ).fetchall()
            }
            tenants = {
                row[0]: {"purpose": row[1], "note": row[2], "backend": row[3]}
                for row in conn.execute(
                    "select id, purpose, note, default_storage_backend from tenant"
                ).fetchall()
            }
        return directory, tenants, None
    except Exception as exc:  # noqa: BLE001 - the reason goes on the page
        return {}, {}, str(exc).strip().splitlines()[0]


def esc(text: object) -> str:
    return html.escape(str(text if text is not None else ""))


def address_for(spec: dict, ports: dict[str, list[int]]) -> str | None:
    if spec.get("url") is not None or spec.get("service") is None:
        return spec.get("url")
    port = spec["port"]
    published = ports.get(spec["service"], [])
    if published and port not in published:
        # The port moved in docker-compose.yml. Follow it rather than
        # printing a link that goes nowhere.
        port = published[0]
    scheme = spec.get("scheme", "http")
    if scheme != "http":
        return f"{scheme}://localhost:{port}"
    return f"http://localhost:{port}{spec.get('path', '')}"


def service_table(ports: dict[str, list[int]]) -> str:
    blocks = []
    for group in GROUP_ORDER:
        rows = []
        for spec in (s for s in SERVICES if s["group"] == group):
            url = address_for(spec, ports)
            openable = url is not None and url.startswith("http")
            link = (
                f'<a href="{esc(url)}" target="_blank" rel="noreferrer">{esc(url)}</a>'
                if openable
                else f"<span class='mono muted'>{esc(url or 'no address')}</span>"
            )
            dot = (
                f'<span class="dot" data-url="{esc(url)}" title="checking"></span>'
                if openable
                else '<span class="dot off" title="nothing to check"></span>'
            )
            rows.append(
                f"<tr><td>{dot} <strong>{esc(spec['name'])}</strong>"
                f"<div class='muted small'>{esc(spec['runs'])}</div></td>"
                f"<td class='mono small'>{link}</td>"
                f"<td>{esc(spec['what'])}</td>"
                f"<td class='small'>{esc(spec['who'])}</td>"
                f"<td class='small muted'>{esc(spec['signin'])}</td></tr>"
            )
        blocks.append(
            f"""
  <h2>{esc(group)}</h2>
  <table>
    <thead><tr><th>Service</th><th>Address</th><th>What it is for</th><th>Who it is meant for</th><th>Signing in</th></tr></thead>
    <tbody>
{chr(10).join(rows)}
    </tbody>
  </table>"""
        )
    return "\n".join(blocks)


def login_sections(directory: dict, tenants: dict) -> str:
    identities = json.loads(IDENTITIES.read_text(encoding="utf-8"))
    blocks = []

    for group in identities["tenants"]:
        tenant = group["tenant"]
        meta = tenants.get(tenant, {})
        purpose = PURPOSE_LABEL.get(meta.get("purpose", ""), "")
        note = meta.get("note") or ""
        backend = meta.get("backend") or ""

        rows = []
        for person in group["identities"]:
            known = directory.get(person["directory_id"], {})
            roles = known.get("roles", [])
            role_text = ", ".join(ROLE_LABEL.get(r, r) for r in roles) or "unknown"
            department = known.get("departments") or ""
            good_for = next((GOOD_FOR[r] for r in roles if r in GOOD_FOR), "")
            ended = (
                " <span class='chip warn'>appointment ended</span>"
                if known.get("ended_at")
                else ""
            )
            rows.append(
                f"<tr><td><strong>{esc(person['name'])}</strong>{ended}"
                f"<div class='muted small mono'>{esc(person['directory_id'])}</div></td>"
                f"<td class='mono small'><button class='copy' "
                f"data-copy='{esc(person['email'])}'>{esc(person['email'])}</button></td>"
                f"<td>{esc(role_text)}"
                + (f"<div class='muted small'>{esc(department)}</div>" if department else "")
                + f"</td><td class='small'>{esc(good_for)}</td></tr>"
            )

        blocks.append(
            f"""
  <h2>{esc(tenant)} <span class="chip">{esc(purpose or "purpose unknown")}</span>
    {f'<span class="chip quiet">{esc(backend)}</span>' if backend else ""}</h2>
  {f'<p class="muted">{esc(note)}</p>' if note else ""}
  <table>
    <thead><tr><th>Person</th><th>Email, click to copy</th><th>Role</th><th>Good for</th></tr></thead>
    <tbody>
{chr(10).join(rows)}
    </tbody>
  </table>"""
        )

    return "\n".join(blocks)


STYLE = """<style>
  :root {
    color-scheme: light dark;
    --bg: #f4f4f2; --card: #ffffff; --ink: #1b1d23; --soft: #5b6070;
    --line: #dedfe4; --accent: #2f5d50; --warn: #8a5a12; --warn-bg: #fdf3e0;
    --chip: #eceef2; --mono-bg: #f0f1f4; --ok: #2e7d4f; --bad: #b3352f;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --bg: #14161b; --card: #1c1f26; --ink: #e8e9ee; --soft: #a2a7b5;
      --line: #2b2f39; --accent: #7fc0ab; --warn: #e0b167; --warn-bg: #2e2415;
      --chip: #272b35; --mono-bg: #23262e; --ok: #6cc38d; --bad: #e2837c;
    }
  }
  :root[data-theme="dark"] {
    --bg: #14161b; --card: #1c1f26; --ink: #e8e9ee; --soft: #a2a7b5;
    --line: #2b2f39; --accent: #7fc0ab; --warn: #e0b167; --warn-bg: #2e2415;
    --chip: #272b35; --mono-bg: #23262e; --ok: #6cc38d; --bad: #e2837c;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--ink); line-height: 1.55;
    font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
  }
  .wrap { max-width: 1180px; margin: 0 auto; padding: 40px 16px 80px; }
  h1 { font-size: 28px; margin: 0 0 6px; letter-spacing: -0.01em; }
  h2 { font-size: 18px; margin: 40px 0 8px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  p { margin: 6px 0 14px; }
  .lede { color: var(--soft); max-width: 72ch; }
  .muted { color: var(--soft); }
  .small { font-size: 13px; }
  .mono, .mono a, code { font-family: ui-monospace, Consolas, monospace; font-size: 13px; }
  table {
    width: 100%; border-collapse: collapse; background: var(--card);
    border: 1px solid var(--line); border-radius: 10px; overflow: hidden;
  }
  th {
    text-align: left; font-size: 11px; text-transform: uppercase;
    letter-spacing: 0.08em; color: var(--soft); padding: 10px 14px;
    border-bottom: 1px solid var(--line); font-weight: 600;
  }
  td { padding: 11px 14px; border-bottom: 1px solid var(--line); vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  a { color: var(--accent); }
  .dot {
    display: inline-block; width: 9px; height: 9px; border-radius: 50%;
    background: var(--soft); margin-right: 6px; vertical-align: middle;
  }
  .dot.up { background: var(--ok); }
  .dot.off { background: var(--line); }
  .chip {
    display: inline-block; font-size: 11px; font-weight: 600; padding: 2px 8px;
    border-radius: 999px; background: var(--chip); color: var(--soft);
    text-transform: uppercase; letter-spacing: 0.06em;
  }
  .chip.quiet { text-transform: none; letter-spacing: 0; font-weight: 500; }
  .chip.warn { background: var(--warn-bg); color: var(--warn); }
  .pw {
    background: var(--card); border: 1px solid var(--line); border-radius: 10px;
    padding: 16px 18px; display: flex; gap: 14px; align-items: center; flex-wrap: wrap;
  }
  .pw code { background: var(--mono-bg); padding: 4px 10px; border-radius: 6px; font-size: 15px; }
  button.copy {
    font-family: ui-monospace, Consolas, monospace; font-size: 13px;
    background: none; border: none; padding: 0; color: var(--accent);
    cursor: pointer; text-align: left;
  }
  button.copy:hover { text-decoration: underline; }
  button.linkish {
    font: inherit; background: none; border: none; padding: 0;
    color: var(--accent); cursor: pointer; text-decoration: underline;
  }
  button.copy.copied::after { content: " copied"; color: var(--soft); font-size: 11px; }
  .warn-box {
    background: var(--warn-bg); border: 1px solid var(--warn); color: var(--warn);
    border-radius: 10px; padding: 12px 16px; max-width: 80ch;
  }
  .crosslink { font-size: 14px; margin-bottom: 26px; }
  footer { margin-top: 56px; padding-top: 18px; border-top: 1px solid var(--line); color: var(--soft); font-size: 13px; }
  @media (max-width: 760px) {
    td, th { padding: 9px 10px; }
    table { font-size: 14px; }
  }
</style>"""

COPY_SCRIPT = """<script>
  document.querySelectorAll("button.copy").forEach(function (button) {
    button.addEventListener("click", function () {
      navigator.clipboard.writeText(button.dataset.copy).then(function () {
        button.classList.add("copied");
        setTimeout(function () { button.classList.remove("copied"); }, 1200);
      });
    });
  });
</script>"""

REACHABILITY_SCRIPT = """<script>
  // Reachability, not health. An opaque response means something answered on
  // that port; it says nothing about what. A service that is up but broken
  // still shows green, which is why this is a dot and not a verdict.
  //
  // A failed check is left grey rather than turned red, because there are two
  // reasons it can fail and this page cannot tell them apart: the service is
  // down, or the browser refused the check because the page was opened from a
  // file rather than served. Claiming "down" for the second one would be the
  // page lying about the platform.
  function checkAll() {
    document.querySelectorAll(".dot[data-url]").forEach(function (dot) {
      var done = false;
      dot.classList.remove("up");
      dot.title = "checking";
      var settle = function (ok) {
        if (done) return;
        done = true;
        if (ok) {
          dot.classList.add("up");
          dot.title = "answered";
        } else {
          dot.title = "no answer: not running, or the check was blocked";
        }
      };
      setTimeout(function () { settle(false); }, 4000);
      fetch(dot.dataset.url, { mode: "no-cors", cache: "no-store" })
        .then(function () { settle(true); })
        .catch(function () { settle(false); });
    });
  }

  checkAll();
  document.getElementById("recheck").addEventListener("click", checkAll);
</script>"""


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def build_services(ports: dict[str, list[int]]) -> str:
    return f"""<title>Munitas: Local Services</title>
{STYLE}

<div class="wrap">
  <h1>Local services</h1>
  <p class="lede">
    Every address this stack publishes on this machine, what each one is for and
    who would open it. A green dot means something answered on that address just
    now. Grey means nothing did, which is usually a service that is not running,
    and sometimes a browser refusing the check because this page was opened from a
    file rather than served.
  </p>
  <p class="crosslink">
    Who to sign in as: <a href="logins.html">logins and passwords &rarr;</a>
    &nbsp;&middot;&nbsp; <button type="button" id="recheck" class="linkish">check again</button>
  </p>

{service_table(ports)}

  <h2>Starting them</h2>
  <p class="lede">
    <code>start-dev.ps1</code> brings up Compose, waits for the API and opens the
    worker and the console in their own windows. Docker runs inside WSL2 on this
    machine, so a bare <code>docker compose</code> from Windows reaches nothing:
    <code>wsl-docker.ps1</code> builds the working form for every other Compose command.
  </p>

  <footer>
    Generated by <code>docs/tools/generate_services_page.py</code> on {stamp()}. Addresses
    come from <code>docker-compose.yml</code> rather than being written here, so a
    port that moves there moves on this page. Re-run it after adding a service.
  </footer>
</div>

{REACHABILITY_SCRIPT}
"""


def build_logins(directory: dict, tenants: dict, db_error: str | None) -> str:
    warning = (
        f"""<p class="warn-box"><strong>Roles could not be read.</strong>
    Every email below is still correct, but the role, department and organisation
    notes are missing because the database did not answer:
    <span class="mono">{esc(db_error)}</span>. Start the stack and run
    <code>docs/tools/generate_services_page.py</code> again to fill them in.</p>"""
        if db_error
        else ""
    )

    return f"""<title>Munitas: Local Logins</title>
{STYLE}

<div class="wrap">
  <h1>Local logins</h1>
  <p class="lede">
    Who to sign in as, for each kind of testing. Sign in at
    <a href="http://localhost:5173/auth/login" target="_blank" rel="noreferrer">localhost:5173/auth/login</a>.
    Registration is closed: these are the only accounts that exist, and new ones are
    added by editing <code>infra/kratos/identities.json</code> and re-running the
    seeder.
  </p>
  <p class="crosslink">Where everything runs: <a href="services.html">services and addresses &rarr;</a></p>

  {warning}

  <div class="pw">
    <span>Password, the same one for every person here</span>
    <code>{esc(password())}</code>
    <button class="copy" data-copy="{esc(password())}">copy</button>
  </div>
  <p class="lede small">
    One password on purpose. It guards nothing, and nothing real should ever sit
    behind it: the point of this stack is to show the mechanism works, not to keep
    anybody out of it.
  </p>

{login_sections(directory, tenants)}

  <h2>Two people at once</h2>
  <p class="lede">
    The console holds one session at a time, so switching people means signing out
    first. Testing two organisations or a refusal usually needs two real sessions:
    one ordinary window and one private window, or two browser profiles. Two tabs in
    the same window share the session and will quietly show you the same person
    twice.
  </p>

  <footer>
    Generated by <code>docs/tools/generate_services_page.py</code> on {stamp()}. People come
    from <code>infra/kratos/identities.json</code>, the password from
    <code>infra/kratos/seed-identities.py</code>, and roles from the running database.
    Re-run it after adding a person rather than editing this page.
  </footer>
</div>

{COPY_SCRIPT}
"""


if __name__ == "__main__":
    ports = published_ports()
    directory, tenants, db_error = people_from_database()

    SERVICES_OUT.write_text(build_services(ports), encoding="utf-8")
    LOGINS_OUT.write_text(build_logins(directory, tenants, db_error), encoding="utf-8")

    print(f"wrote {SERVICES_OUT.name} ({SERVICES_OUT.stat().st_size // 1024} KB)")
    print(f"wrote {LOGINS_OUT.name} ({LOGINS_OUT.stat().st_size // 1024} KB)")
    if db_error:
        print(f"roles left blank, the database did not answer: {db_error}")
