"""A narrated demo: make a new dataset from a query, under the platform's rules.

    python scripts/demo/derive-demo.py --tenant health
    python scripts/demo/derive-demo.py --tenant finance --pause
    python scripts/demo/derive-demo.py --tenant health --transcript demo-health.json

An analyst finds a table she is not allowed to read, asks for access, gets it,
and makes a new dataset from a query over it. The platform runs the query, checks
the result, and keeps the new dataset as restricted as what it came from. Then
the access is withdrawn, and everything built on it closes too.

Every step runs for real against the running stack. The code shown on screen is
the exact text that is executed (a token is a variable, so it is never printed),
and what follows it is what it really printed. `--transcript` saves all of that
as JSON, which is what the walkthrough page is built from.

Needs the stack up, the workers running, the query image built, and the demo data
seeded (scripts/seed/seed-derive-demo-data.py). Needs httpx and duckdb.

--pause    wait for Enter between steps, for showing it live
--fresh    end any lease left from an earlier run before starting (the default)
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "seed"))
sys.path.insert(0, str(ROOT / "scripts" / "client"))

from munitas_client import Munitas, MunitasError  # noqa: E402
from ports_config import PORTS  # noqa: E402
from seed_common import bearer_for  # noqa: E402

API = f"http://localhost:{PORTS['munitas_api_http']}"

SCENARIOS = {
    "health": {
        "tenant": "health", "department": "Cardiology",
        "researcher": "sam-researcher", "custodian": "cust-hartley", "colleague": "eng-devi",
        "who": {"sam-researcher": "Sam, a researcher in the Health organisation",
                "cust-hartley": "Dr Hartley, the custodian of the Cardiology department",
                "eng-devi": "Devi, a pipeline engineer in the Health organisation"},
        "raw": "admissions", "lookup": "diagnosis_codes",
        "raw_what": "hospital admissions, with patient names",
        "purpose": "readmission study",
        "justification": "Compare readmission rates for older patients with a chronic heart condition.",
        "target": "older-chronic-cardiac-patients", "pk": "admission_id",
        "inputs": [{"dataset": "admissions", "alias": "a"}, {"dataset": "diagnosis_codes", "alias": "d"}],
        "sql": ("SELECT a.admission_id, a.age, a.diagnosis_code, d.description, d.chronic,\n"
                "       a.length_of_stay_days, a.readmitted_30d\n"
                "FROM a JOIN d ON d.code = a.diagnosis_code\n"
                "WHERE a.age > 65 AND d.chronic"),
        "peek": 'SELECT admission_id, patient_name, age, diagnosis_code FROM lake."admissions".v1 LIMIT 3',
        "lower": ("SELECT admission_id, age FROM a", "age"),
        "result_peek": ('SELECT admission_id, age, description, length_of_stay_days, readmitted_30d '
                        'FROM lake."{target}".v1 ORDER BY age DESC LIMIT 5'),
        "result_stats": ('SELECT count(*) AS patients, round(avg(age), 1) AS mean_age, '
                         'round(avg(length_of_stay_days), 1) AS mean_stay_days FROM lake."{target}".v1'),
    },
    "finance": {
        "tenant": "finance", "department": "Fraud Operations",
        "researcher": "ana-omar", "custodian": "cust-marcus", "colleague": "eng-lena",
        "who": {"ana-omar": "Omar, an analyst in the Finance organisation",
                "cust-marcus": "Marcus, the custodian of the Fraud Operations department",
                "eng-lena": "Lena, a pipeline engineer in the Finance organisation"},
        "raw": "transactions", "lookup": "merchants",
        "raw_what": "card transactions, with account holders",
        "purpose": "cross-border fraud review",
        "justification": "Review large transactions made outside the home country at risky merchants.",
        "target": "high-value-foreign-transactions", "pk": "txn_id",
        "inputs": [{"dataset": "transactions", "alias": "t"}, {"dataset": "merchants", "alias": "m"}],
        "sql": ("SELECT t.txn_id, t.amount, t.country, t.occurred_at, m.category, m.high_risk, t.flagged\n"
                "FROM t JOIN m ON m.merchant_id = t.merchant_id\n"
                "WHERE t.amount > 300 AND t.country <> 'US'"),
        "peek": 'SELECT txn_id, account_holder, card_last4, amount, country FROM lake."transactions".v1 LIMIT 3',
        "lower": ("SELECT txn_id, country FROM t", "country"),
        "result_peek": ('SELECT txn_id, amount, country, category, flagged '
                        'FROM lake."{target}".v1 ORDER BY amount DESC LIMIT 5'),
        "result_stats": ('SELECT count(*) AS transactions, round(sum(amount), 2) AS total_amount, '
                         'count(*) FILTER (WHERE flagged) AS flagged FROM lake."{target}".v1'),
    },
}


class Stage:
    """Runs and records the steps of the demo."""

    def __init__(self, pause: bool):
        self.pause, self.scenes, self.secrets, self.ns = pause, [], [], {}

    def scrub(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, "<token>")
        return text

    def scene(self, title: str, who: str, where: str, say: str) -> dict:
        n = len(self.scenes) + 1
        print(f"\n{'=' * 78}\nStep {n}: {title}\n{who}  |  {where}\n{'-' * 78}")
        print(textwrap.fill(say, 78))
        scene = {"step": n, "title": title, "who": who, "where": where, "say": say, "blocks": []}
        self.scenes.append(scene)
        return scene

    def run(self, scene: dict, code: str, expect_error: bool = False) -> str:
        """Show this code, run exactly this code, and record what it printed."""
        code = textwrap.dedent(code).strip("\n")
        print("\n" + textwrap.indent(code, "    >>> ", lambda line: True))
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                exec(compile(code, "<demo>", "exec"), self.ns)
        except Exception as exc:  # noqa: BLE001 - a step that fails is the demo's own failure
            if not expect_error:
                print(self.scrub(out.getvalue()))
                raise SystemExit(f"\nThis step failed: {type(exc).__name__}: {exc}")
            out.write(f"Refused: {exc}\n")
        text = self.scrub(out.getvalue()).rstrip("\n")
        if not text and "print(" in code:
            # A step written to show something that shows nothing has not worked,
            # even though it raised nothing. Better to stop than to narrate over it.
            raise SystemExit("\nThis step printed nothing, so it did not do what the demo says it does.")
        print(textwrap.indent(text, "    ") if text else "    (no output)")
        scene["blocks"].append({"code": code, "output": text})
        return text

    def wait(self):
        if self.pause:
            input("\n    [press Enter for the next step] ")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", choices=sorted(SCENARIOS), default="health")
    ap.add_argument("--pause", action="store_true")
    ap.add_argument("--transcript", help="save everything shown, as JSON")
    args = ap.parse_args()
    cfg = SCENARIOS[args.tenant]

    def login(person: str) -> Munitas:
        return Munitas(API, bearer_for(person)["Authorization"].split()[1])

    me, custodian, colleague = login(cfg["researcher"]), login(cfg["custodian"]), login(cfg["colleague"])
    stage = Stage(args.pause)
    stage.ns.update(me=me, custodian=custodian, colleague=colleague, api=API, time=time, MunitasError=MunitasError)

    # Housekeeping, not part of the story: a lease left by an earlier run would
    # make the first refusal impossible to show, and a name taken would make the
    # new dataset impossible to create.
    raw_version = custodian.latest_version(cfg["raw"])["id"]
    leases = custodian._call("GET", "/leases", params={"principal": cfg["researcher"], "active_only": "true"})
    ended = 0
    for lease in leases.get("leases", []):
        if lease.get("dataset_version_id") == raw_version:
            custodian.revoke(lease["id"])
            ended += 1
    target = cfg["target"]
    for suffix in [""] + [f"-{n}" for n in range(2, 50)]:
        try:
            me.dataset(target + suffix)
        except MunitasError:
            target += suffix
            break
    if ended:
        print(f"(housekeeping: ended {ended} lease left by an earlier run)")
    if target != cfg["target"]:
        print(f"(housekeeping: {cfg['target']} already exists, so this run makes {target})")
    stage.ns["target"] = target
    purpose, tenant = cfg["purpose"], cfg["tenant"]

    # ------------------------------------------------------------------ 1 --
    s = stage.scene(
        "Connect a notebook tool to the catalog", cfg["who"][cfg["researcher"]], "Notebook (DuckDB)",
        f"A catalog is a list of tables that a tool such as DuckDB can open. The person asks the platform for "
        f"a token that names why they want to read (here: {purpose}), then connects DuckDB with it. The "
        f"platform shows only the tables this person may read right now.")
    stage.run(s, f'''
        token = me.catalog_token(purpose="{purpose}")   # shown once, never printed
        import duckdb
        con = duckdb.connect()
        con.execute("INSTALL iceberg; LOAD iceberg;")
        con.execute(f"ATTACH '{tenant}' AS lake (TYPE ICEBERG, ENDPOINT '{API}/iceberg', TOKEN '{{token}}')")
        print(con.sql("SELECT schema AS dataset, name AS version FROM (SHOW ALL TABLES) ORDER BY 1"))
    ''')
    stage.secrets.append(stage.ns["token"])
    stage.wait()

    # ------------------------------------------------------------------ 2 --
    s = stage.scene(
        "A restricted table is closed, with the reason", cfg["who"][cfg["researcher"]], "Notebook (DuckDB)",
        f"The {cfg['raw']} dataset holds {cfg['raw_what']}. Its class is RAW, the most restricted class, so "
        f"nobody can read it without a lease. A lease is access that the owning department's custodian "
        f"approves for one purpose and a limited time.")
    stage.run(s, f'''
        try:
            print(con.sql('SELECT count(*) FROM lake."{cfg["raw"]}".v1'))
        except Exception as exc:
            print("Refused:", exc)
    ''')
    stage.wait()

    # ------------------------------------------------------------------ 3 --
    s = stage.scene(
        "Ask for access, and the custodian decides", cfg["who"][cfg["researcher"]] + ", then " + cfg["who"][cfg["custodian"]],
        "Notebook (client library)",
        f"The person asks for access and gives a purpose and a reason. A different person, the custodian of the "
        f"{cfg['department']} department, approves it. Nobody can approve their own request.")
    stage.run(s, f'''
        request = me.request_access("{cfg["raw"]}", purpose="{purpose}",
                                    justification="{cfg["justification"]}", hours=2)
        print("Request filed:", request[:8])
    ''')
    stage.run(s, '''
        lease = custodian.approve(request)
        print("Approved by the custodian. Lease:", lease[:8])
    ''')
    stage.run(s, f'''
        for _ in range(20):                      # storage takes a few seconds to learn of new access
            try:
                print(con.sql('SELECT count(*) AS {cfg["raw"]} FROM lake."{cfg["raw"]}".v1'))
                break
            except Exception:
                time.sleep(3)
    ''')
    stage.wait()

    # ------------------------------------------------------------------ 4 --
    s = stage.scene(
        "Look at what the restricted data holds", cfg["who"][cfg["researcher"]], "Notebook (DuckDB)",
        "With a lease the table opens. It holds names and other identifying details, which is the reason it is "
        "restricted. Anything made from it has to stay as careful as it is.")
    stage.run(s, f'''print(con.sql("""{cfg["peek"]}"""))''')
    stage.wait()

    # ------------------------------------------------------------------ 5 --
    s = stage.scene(
        "Make a new dataset from a query: the platform shows its plan first", cfg["who"][cfg["researcher"]],
        "Notebook (client library)",
        f"The person writes a query that joins the restricted table with a small public lookup table. The "
        f"platform does not run it yet. It answers with the columns the query would produce, and the "
        f"sensitivity each column must carry, worked out from the columns it was computed from.")
    stage.run(s, f'''
        sql = """{cfg["sql"].replace(chr(10), chr(10) + "        ")}"""
        draft = me.draft(inputs={json.dumps(cfg["inputs"])}, sql=sql, name="{target}",
                         primary_key=["{cfg["pk"]}"], purpose="{purpose}")
        for f in draft["fields"]:
            print(f"{{f['name']:<22}}{{f['type']:<8}}{{f['sensitivity']}}")
        print("The new dataset would be class", draft["output_class"])
    ''')
    stage.wait()

    # ------------------------------------------------------------------ 6 --
    s = stage.scene(
        "A sensitivity cannot be lowered by the person who wrote the query", cfg["who"][cfg["researcher"]],
        "Notebook (client library)",
        "A column computed from a sensitive column may not be marked as less sensitive. Raising a label is "
        "allowed. Lowering one is a claim that needs somebody else to agree, so it is refused.")
    low_sql, low_col = cfg["lower"]
    stage.run(s, f'''
        probe = me.draft(inputs={json.dumps(cfg["inputs"][:1])}, sql="{low_sql}", name="{target}-probe",
                         primary_key=["{cfg["pk"]}"], purpose="{purpose}")
        try:
            me.confirm(probe, sensitivities={{"{low_col}": "none"}})
        except MunitasError as exc:
            print("Refused:", exc)
    ''')
    stage.wait()

    # ------------------------------------------------------------------ 7 --
    s = stage.scene(
        "Confirm, and the platform runs the query in a sandbox", cfg["who"][cfg["researcher"]],
        "Notebook (client library)",
        "After the person confirms, the platform copies the input files into a container that has no network and "
        "no credentials, runs the query there, and checks every row against the plan. The query never runs on the "
        "person's own machine.")
    stage.run(s, '''
        confirmed = me.confirm(draft)
        result = me.wait(confirmed["id"])
        print("Status:", result["status"])
        print("New dataset:", result["target_name"], "|", result["output_class"])
    ''')
    stage.wait()

    # ------------------------------------------------------------------ 8 --
    s = stage.scene(
        "The new dataset opens like any other table", cfg["who"][cfg["researcher"]], "Notebook (DuckDB)",
        "The result is a sealed, unchangeable dataset and also an Iceberg table. The person made it, so "
        "they can read it at once, without asking anybody.")
    stage.run(s, f'''
        con = me.attach_duckdb(token)
        print(con.sql("""{cfg["result_peek"].format(target=target)}"""))
        print(con.sql("""{cfg["result_stats"].format(target=target)}"""))
    ''')
    stage.wait()

    # ------------------------------------------------------------------ 9 --
    s = stage.scene(
        "The record of where it came from", cfg["who"][cfg["researcher"]], "Notebook (client library)",
        "The platform keeps the query exactly as written, the exact version of each input, who asked, and the "
        "purpose. Anyone auditing the new dataset can follow it back.")
    stage.run(s, '''
        record = me.derivation(result["id"])
        print("Purpose :", record["purpose"])
        print("Inputs  :", ", ".join(f"{i['dataset']} version {i['version']} ({i['class']})" for i in record["inputs"]))
        print("Query   :")
        print(record["sql"])
    ''')
    stage.wait()

    # ------------------------------------------------------------------ 10 --
    s = stage.scene(
        "A colleague without access is refused", cfg["who"][cfg["colleague"]], "Notebook (DuckDB)",
        f"The new dataset is as restricted as the data it came from. A colleague in the same organisation, who has "
        f"no lease on it, cannot open it.")
    stage.run(s, f'''
        colleague_token = colleague.catalog_token(purpose="curiosity")
        other = colleague.attach_duckdb(colleague_token)
        try:
            print(other.sql('SELECT count(*) FROM lake."{target}".v1'))
        except Exception as exc:
            print("Refused:", exc)
    ''', expect_error=True)
    stage.secrets.append(stage.ns["colleague_token"])
    stage.wait()

    # ------------------------------------------------------------------ 11 --
    s = stage.scene(
        "A query that reaches outside its inputs is refused", cfg["who"][cfg["researcher"]], "Notebook (client library)",
        "The query may read the inputs it declared and nothing else. A query that tries to read a file on the "
        "machine, or an address on the internet, is stopped before it runs.")
    stage.run(s, f'''
        try:
            me.draft(inputs={json.dumps(cfg["inputs"][:1])}, sql="SELECT content AS {cfg["pk"]} FROM read_text('/etc/passwd')",
                     name="{target}-escape", primary_key=["{cfg["pk"]}"], purpose="{purpose}")
        except MunitasError as exc:
            print("Refused:", exc)
    ''')
    stage.wait()

    # ------------------------------------------------------------------ 12 --
    s = stage.scene(
        "Withdraw the access, and everything built on it closes", cfg["who"][cfg["custodian"]] + ", then " + cfg["who"][cfg["researcher"]],
        "Notebook (DuckDB)",
        "The custodian withdraws the lease. The person loses the restricted table at once, and also the new "
        "dataset that was made from it, because that access was granted on the strength of the lease.")
    stage.run(s, f'''
        custodian.revoke(lease)
        time.sleep(3)
        con = me.attach_duckdb(token)
        for name in ["{cfg["raw"]}", "{target}"]:
            try:
                print(con.sql(f'SELECT count(*) FROM lake."{{name}}".v1'))
            except Exception as exc:
                print(f"{{name}}: refused, {{str(exc).split('message ')[-1]}}")
    ''')

    print(f"\n{'=' * 78}\nDone. {len(stage.scenes)} steps.")
    if args.transcript:
        Path(args.transcript).write_text(json.dumps({
            "tenant": tenant, "department": cfg["department"], "target": target, "scenes": stage.scenes},
            indent=2), encoding="utf-8")
        print(f"Transcript saved to {args.transcript}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
