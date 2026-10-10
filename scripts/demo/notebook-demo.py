"""A narrated session: a researcher signs a Python notebook in as themself, the way the Health walkthroughs show it.

    python scripts/demo/notebook-demo.py --sync <folder> --transcript <file>

The notebook asks Munitas for a code, the person types the code in the console and approves, and the notebook carries on as that person with a short-lived
pass. Every step runs for real against the running stack. The code shown is the exact text that is executed and what follows it is what it printed;
`--transcript` saves all of that as JSON, which the walkthrough page is built from (docs/tools/build_derive_walkthrough.py).

The approval happens in the console, which a program cannot do for itself, so this session is run by scripts/walkthroughs/capture-health-story.py: it
writes the code the notebook was given to `<folder>/signin.json` the moment Munitas hands it out, and after each step it writes `after-<n>.ready` and
waits for `after-<n>.go` (the same arrangement as scripts/demo/derive-demo.py), so the console screens can be taken in between.

Needs the stack up and a person who is signed in to the console. Safe to run again: the notebook's sign-in ends at the last step.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "seed"))
sys.path.insert(0, str(ROOT / "scripts" / "client"))

from munitas_client import Munitas, MunitasError  # noqa: E402
from ports_config import PORTS  # noqa: E402

API = f"http://localhost:{PORTS['munitas_api_http']}"
LABEL = "Sam's readmission notebook"

# The recorder is the one the derivation session uses (its file name has a hyphen, so it is loaded by path).
_spec = importlib.util.spec_from_file_location("derive_demo", ROOT / "scripts" / "demo" / "derive-demo.py")
derive_demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(derive_demo)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sync", required=True, help="a folder shared with the program that works the console")
    ap.add_argument("--transcript", required=True, help="save everything shown, as JSON")
    args = ap.parse_args()
    folder = Path(args.sync)
    folder.mkdir(parents=True, exist_ok=True)

    # Hand the console program the code the moment Munitas gives it to the notebook. The session itself is not changed by this.
    post = httpx.post

    def snoop(url, *a, **k):
        r = post(url, *a, **k)
        if str(url).endswith("/notebook/v1/sign-in") and r.status_code == 201:
            (folder / "signin.json").write_text(json.dumps(r.json()), encoding="utf-8")
        return r

    httpx.post = snoop

    stage = derive_demo.Stage(False, str(folder))
    stage.ns.update(Munitas=Munitas, MunitasError=MunitasError, API=API, time=time)
    stage.TYPED_BY = {}

    s = stage.scene(
        "Sign the notebook in as the person", "Sam, a researcher in the Health organisation", "Notebook (client library)",
        "A notebook has no password of its own and is never given the person's console sign-in. It asks Munitas for a code, shows it, and waits while the "
        "person approves it in the console.")
    stage.run(s, f'''
        me = Munitas.sign_in(API, label="{LABEL}", wait=180)
    ''')
    # sign_in prints its instruction itself; an empty first line would be a step that shows nothing, so the instruction is the output.
    stage.wait()

    s = stage.scene(
        "Use the pass", "Sam, a researcher in the Health organisation", "Notebook (client library)",
        "The notebook now acts as Sam, with a pass that lasts 15 minutes and renews itself for at most 8 hours. It sees what Sam sees.")
    stage.run(s, '''
        who = me.me
        print(f"Signed in as {who['label']} in the {who['tenant_id']} organisation")
        found = me.dataset("diagnosis_codes")
        print(found["name"], "has", found["version_count"], "version, and Sam may read it")
    ''')
    stage.wait()

    s = stage.scene(
        "The pass cannot decide anything", "Sam, a researcher in the Health organisation", "Notebook (client library)",
        "A notebook may find datasets, ask for access, and make a dataset from a query. Deciding a request, or changing a role, is done in the console, "
        "and a notebook is refused before Munitas looks at who it is.")
    stage.run(s, '''
        try:
            me.approve("any-request-at-all")
        except MunitasError as exc:
            print("Refused:", exc)
    ''')
    stage.wait()

    # The console ends the sign-in here (the person presses End now), then the notebook tries again.
    s = stage.scene(
        "After the person ends the sign-in", "Sam, a researcher in the Health organisation", "Notebook (client library)",
        "Sam ended the notebook's sign-in in the console. The pass stops at its very next request, and the renewal that would have kept it going stops too.")
    stage.run(s, '''
        try:
            me.dataset("diagnosis_codes")
        except MunitasError as exc:
            print("Refused:", exc)
    ''')
    stage.wait()

    Path(args.transcript).write_text(json.dumps({
        "tenant": "health", "department": "Cardiology", "target": "",
        "people": {"researcher": "Sam", "custodian": "Hartley", "colleague": "Devi"},
        "scenes": stage.scenes}, indent=2), encoding="utf-8")
    print(f"Transcript saved to {args.transcript}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
