"""Write the Health organisation's approved tools list, as its AI risk owner would through the console.

    .venv/Scripts/python.exe scripts/admin/seed-health-tool-list.py

Signs in as Okafor (the AI risk owner seeded for Health) and writes the list through the platform's own route, so the list carries who wrote it, when and why, the same as any other. It does
nothing when Health already has a list, so it is safe to run again; it never replaces one. The entries are the walkthrough's example: routine tools that need nobody, tools that need the
department only, and the conditions each is approved under. A tool that sends something out on its own, or pages somebody, is deliberately not on it: those are decided one case at a time.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ports_config import PORTS  # noqa: E402

API = f"http://localhost:{PORTS['munitas_api_http']}"
KRATOS = f"http://localhost:{PORTS['kratos_public']}"
EMAIL = "okafor@health.example"
PASSWORD = "dev-password-not-for-production"
REVIEW_IN_DAYS = 330


def entries(level_key: dict[str, str]) -> list[dict]:
    review = (date.today() + timedelta(days=REVIEW_IN_DAYS)).isoformat()
    high, moderate = level_key["high"], level_key["moderate"]
    return [
        {"tool": "read_dataset_version", "description": "Reads one version of a dataset the agent has been given a lease for.", "reach": "reads", "tier": 1, "ceiling": high, "conditions": "",
         "reason": "It changes nothing, and the lease already decides what it may read.", "review_by": review},
        {"tool": "summarise_counts", "description": "Returns counts and totals over a dataset, never individual records.", "reach": "reads", "tier": 1, "ceiling": high, "conditions": "",
         "reason": "It returns aggregates only and changes nothing.", "review_by": review},
        {"tool": "list_review_queue", "description": "Lists the items waiting in a review queue.", "reach": "reads", "tier": 1, "ceiling": high, "conditions": "",
         "reason": "It reads a list and changes nothing.", "review_by": review},
        {"tool": "add_to_review_queue", "description": "Adds an item to a queue that a person works through.", "reach": "adds_for_review", "tier": 1, "ceiling": high,
         "conditions": "A person opens every item in the queue, and an item can be removed.", "reason": "Nothing happens until a person has looked, and an item is easy to undo.", "review_by": review},
        {"tool": "draft_note_for_clinician", "description": "Prepares a draft note that a clinician reads, edits and files.", "reach": "creates_draft", "tier": 2, "ceiling": high,
         "conditions": "A clinician reads and files every draft. The agent never files one itself.", "reason": "A draft is not part of the record until a clinician files it.", "review_by": review},
        {"tool": "draft_appointment_letter", "description": "Prepares a letter that names a follow-up appointment, as a draft for staff to check and send.", "reach": "creates_draft", "tier": 2, "ceiling": high,
         "conditions": "A member of staff reads and sends every letter. The agent never sends one.", "reason": "A letter does not leave the organisation until a person has checked it.", "review_by": review},
        {"tool": "prepare_summary_for_family_doctor", "description": "Prepares a summary for a person's family doctor and leaves it as a draft.", "reach": "creates_draft", "tier": 2, "ceiling": high,
         "conditions": "A clinician reads and sends every summary. The agent never sends one.", "reason": "Nothing leaves the organisation before a clinician has signed it.", "review_by": review},
        {"tool": "request_followup_appointment", "description": "Creates a request for a follow-up appointment that scheduling staff confirm.", "reach": "creates_draft", "tier": 2, "ceiling": moderate,
         "conditions": "Scheduling staff confirm every request before an appointment exists.", "reason": "The agent asks and a person books, so nothing is booked on its own.", "review_by": review},
        {"tool": "queue_reminder_from_template", "description": "Leaves a reminder, built from a fixed neutral wording, for the clinic's own notification service to pick up.", "reach": "hands_off", "tier": 2, "ceiling": moderate,
         "conditions": "Fixed wording that names no test and no condition. The notification service decides whether, when and how a person is told.", "reason": "The wording is fixed, so it cannot show health information on a lock screen.", "review_by": review},
    ]


def main() -> int:
    flow = httpx.get(f"{KRATOS}/self-service/login/api", timeout=10).json()
    login = httpx.post(f"{KRATOS}/self-service/login", params={"flow": flow["id"]}, json={"method": "password", "identifier": EMAIL, "password": PASSWORD}, headers={"Accept": "application/json"}, timeout=10)
    login.raise_for_status()
    headers = {"Authorization": f"Bearer {login.json()['session_token']}"}
    page = httpx.get(f"{API}/agent-tool-list", headers=headers, timeout=20)
    page.raise_for_status()
    body = page.json()
    if body["current"]["version"] is not None:
        print(f"Health already has an approved tools list (version {body['current']['version']}); nothing written.")
        return 0
    if not body["may_manage"]:
        print("Okafor may not write the list: " + " ".join(body["may_manage_reasons"]))
        return 1
    level_key = {lv["name"].lower(): lv["key"] for lv in body["levels"]}
    missing = [n for n in ("high", "moderate") if n not in level_key]
    if missing:
        print(f"Health has no risk level named {missing}; found {sorted(level_key)}. Nothing written.")
        return 1
    written = httpx.post(f"{API}/agent-tool-list", headers=headers, timeout=30,
                         json={"entries": entries(level_key), "reason": "The routine tools that need nobody, and the tools that need only the department, agreed for the first list."})
    if written.status_code != 201:
        print(f"Refused: HTTP {written.status_code} {written.text[:600]}")
        return 1
    print(f"Wrote version {written.json()['current']['version']} with {len(written.json()['current']['entries'])} tools.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
