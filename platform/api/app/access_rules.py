"""The limits an organisation sets on what people may ask for, and the value in force for each: one place, for every screen and every decision.

Today these are the locked-row request (who may ask, the most rows, the most days) and the request to open locked files (the most files and hours, in the page and in a
notebook). Each used to be a constant in the policy file, the same for every organisation. Each is now a setting with a default (the old constant, so nothing
changes for an organisation that sets nothing), a maximum the platform allows, and room for the organisation to choose a value between.

What is stored: `access_rule_version` holds every value an organisation has set, never edited, with who set it, when and why. The value in force for a setting is the latest
row for that organisation, kept within the platform maximum (and a list of roles within the roles the platform allows), or the default when there is none. The policy engine
is handed the values in force with each decision, so a refusal names the real limit, and the page that shows the rules and the decision cannot disagree.

Not here yet: department values that only tighten, a page that edits, a second person to confirm a loosening, project grants, and platform maximums an administrator can
change. Until the page edits, an operator sets a value with `scripts/admin/set-access-rule.py`, which checks the same bounds. The design is `docs/internal/design/access-rules.html`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone

# Roles that may be asked about, in the order a sentence lists them. A role that holds no data by being held (the platform administrator, support and network roles) is
# never offered for "who may ask", whatever an organisation chooses.
ASKING_ROLES = ("pipeline_operator", "deid_reviewer", "privacy_auditor", "dpo", "notebook_explore", "analyst", "data_custodian")
# How a sentence names each of them ("a data engineer") is not written here: it comes from the policy's role table (platform/policy/role_words.rego), through
# `opa.role_words()`.


@dataclass(frozen=True)
class Rule:
    key: str
    group: str
    label: str
    about: str
    kind: str                       # "number" or "roles"
    default: object                 # a number, or a tuple of roles. The same as the constant the policy file kept.
    maximum: int | None = None      # the most the platform allows, for a number
    minimum: int | None = None
    unit: str | None = None
    allowed: tuple[str, ...] = ()   # for a list of roles: the roles the platform allows to be chosen


RULES: tuple[Rule, ...] = (
    Rule("locked_rows.who_may_ask", "Locked rows", "Who may ask to read rows of locked columns",
         "The roles whose people may ask to read the real values in the locked columns of a table, a few rows at a time.",
         "roles", ("pipeline_operator", "deid_reviewer", "privacy_auditor", "dpo"), allowed=ASKING_ROLES),
    Rule("locked_rows.most_rows", "Locked rows", "Most rows in one request", "How many rows one request may ask to read.",
         "number", 20, maximum=100, minimum=1, unit="rows"),
    Rule("locked_rows.most_days", "Locked rows", "Most days a permission may last", "After this many days the permission to read the rows ends by itself.",
         "number", 7, maximum=30, minimum=1, unit="days"),
    Rule("files_in_page.most_files", "Locked files, in the page", "Most files in one request",
         "How many locked files one request may ask to open here in the console.", "number", 10, maximum=50, minimum=1, unit="files"),
    Rule("files_in_page.most_hours", "Locked files, in the page", "Most hours a permission may last", "After this many hours the permission to open those files here ends by itself.",
         "number", 1, maximum=8, minimum=1, unit="hours"),
    Rule("files_in_notebook.most_files", "Locked files, in a notebook", "Most files in one request",
         "How many locked files one request may ask to open in a notebook. The limit is larger than in the page.",
         "number", 200, maximum=5000, minimum=1, unit="files"),
    Rule("files_in_notebook.most_hours", "Locked files, in a notebook", "Most hours a permission may last", "After this many hours the permission to open those files in a notebook ends by itself.",
         "number", 8, maximum=72, minimum=1, unit="hours"),
    Rule("table_uploads.largest_file_gb", "Bringing in tables", "Largest file in one upload", "The biggest file one person may bring in as a table in one upload.",
         "number", 16, maximum=16, minimum=1, unit="GB"),
    Rule("table_uploads.jobs_at_once", "Bringing in tables", "Tables written at once", "How many of this organisation's tables are written at the same time. The rest wait their turn.",
         "number", 2, maximum=8, minimum=1, unit="tables"),
    Rule("table_uploads.jobs_waiting", "Bringing in tables", "Tables allowed to wait", "How many tables may be waiting to be written before another is refused until one is done.",
         "number", 10, maximum=50, minimum=1, unit="tables"),
    Rule("table_uploads.staged_gb", "Bringing in tables", "Files kept while they wait", "How much, in all, may be held for uploads that are still being sent or are waiting to be written.",
         "number", 64, maximum=512, minimum=1, unit="GB"),
    Rule("table_uploads.per_day_gb", "Bringing in tables", "Most brought in a day", "How much, in all, this organisation may bring in as tables in a day.",
         "number", 256, maximum=2048, minimum=1, unit="GB"),
)
BY_KEY = {r.key: r for r in RULES}


class BadValue(ValueError):
    """The value is not one the platform accepts for this rule, said in words."""


def check(rule: Rule, value: object) -> object:
    """The value in the form it is stored in, or BadValue saying why not."""
    if rule.kind == "number":
        if isinstance(value, bool) or not isinstance(value, int):
            raise BadValue(f"{rule.label} must be a whole number")
        if value < (rule.minimum or 0):
            raise BadValue(f"{rule.label} must be at least {rule.minimum} {rule.unit}")
        if rule.maximum is not None and value > rule.maximum:
            raise BadValue(f"{rule.label} may not be more than {rule.maximum} {rule.unit}: that is the most the platform allows")
        return value
    if not isinstance(value, (list, tuple)):
        raise BadValue(f"{rule.label} must be a list of roles")
    wrong = [r for r in value if r not in rule.allowed]
    if wrong:
        raise BadValue(f"{rule.label} may not include {', '.join(map(str, wrong))}: the platform allows only {', '.join(rule.allowed)}")
    return [r for r in ASKING_ROLES if r in value]


def _stored(tenant_id: str) -> dict[str, dict]:
    """The latest row of each rule that this organisation has set."""
    from . import db
    rows = db.all_rows("select distinct on (rule) rule, value, reason, set_by, source, set_at from access_rule_version where tenant_id = %s order by rule, set_at desc, id desc", (tenant_id,))
    return {r["rule"]: r for r in rows}


def in_force(tenant_id: str) -> dict[str, dict]:
    """Every rule, with its value in force and where that came from. A stored value is held to the platform's bounds again here, so a row written by a bug or by hand cannot exceed them."""
    stored = _stored(tenant_id)
    out = {}
    for rule in RULES:
        row = stored.get(rule.key)
        value, source = rule.default, "default"
        if row is not None:
            try:
                value, source = check(rule, row["value"]), "organisation"
            except BadValue:
                if rule.kind == "number" and isinstance(row["value"], int) and not isinstance(row["value"], bool):
                    value, source = min(max(row["value"], rule.minimum or 0), rule.maximum), "organisation"
                elif rule.kind == "roles" and isinstance(row["value"], list):
                    value, source = [r for r in ASKING_ROLES if r in row["value"] and r in rule.allowed], "organisation"
        out[rule.key] = {
            "key": rule.key, "group": rule.group, "label": rule.label, "about": rule.about, "kind": rule.kind, "unit": rule.unit,
            "value": list(value) if rule.kind == "roles" else value, "default": list(rule.default) if rule.kind == "roles" else rule.default,
            "maximum": rule.maximum, "minimum": rule.minimum, "allowed": list(rule.allowed), "source": source,
            "set_by": row["set_by"] if source == "organisation" else None, "set_at": row["set_at"].astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if source == "organisation" else None,
            "reason": row["reason"] if source == "organisation" else None,
        }
    return out


def limits_for(tenant_id: str) -> dict:
    """The values in force, in the shape the policy reads them (`input.limits`)."""
    rules = in_force(tenant_id)
    return {"locked_rows": {"who_may_ask": rules["locked_rows.who_may_ask"]["value"], "most_rows": rules["locked_rows.most_rows"]["value"], "most_days": rules["locked_rows.most_days"]["value"]},
            "files_in_page": {"most_files": rules["files_in_page.most_files"]["value"], "most_hours": rules["files_in_page.most_hours"]["value"]},
            "files_in_notebook": {"most_files": rules["files_in_notebook.most_files"]["value"], "most_hours": rules["files_in_notebook.most_hours"]["value"]}}
