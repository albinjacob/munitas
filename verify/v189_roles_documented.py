r"""U204: what each role can and cannot do is documented once, the documentation matches the policy, and the public page is current.

Needs no database and no running stack, so it runs anywhere, including CI:

    .venv\Scripts\python.exe verify\v189_roles_documented.py

Three things are compared that nobody derives from the others. The policy says who holds each power (`platform/policy/access.rego`, the `*_roles` sets) and what each role is
called (`platform/policy/role_words.rego`). `docs/tools/roles_reference.json` is what a person wrote: what each power means, and what each role cannot do. The console's roles
page keeps a list of roles by hand (`web/src/features/roles/Roles.tsx`). They have to agree, and the public page (`docs/public/reference/roles.html`) has to be exactly what the
generator makes from them today. A role or a power added in the policy therefore fails here until it is written in the catalogue, and the page cannot be published out of step.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "docs" / "tools"))

import generate_roles_reference as gen  # noqa: E402

_results: list[tuple[str, bool | None, str]] = []
COUNTS_MARKER = "##MUNITAS-CHECK-COUNTS##"
CONTRACTION = re.compile(r"\b\w+(n't|'re|'ve|'ll|'d|'m)\b|\b(it's|that's|there's|here's|let's)\b", re.I)
PRONOUN = re.compile(r"\b(he|she|his|hers|him)\b", re.I)


def check(label: str, ok: bool, detail: str = "") -> bool:
    _results.append((label, ok, detail))
    shown = detail if (not ok or (len(detail) <= 60 and chr(10) not in detail)) else ""
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({shown})" if shown else ""))
    return ok


def heading(text: str) -> None:
    print(f"\n{text}\n" + "-" * len(text))


def summary(name: str) -> int:
    passed = sum(1 for _, ok, _ in _results if ok is True)
    failed = sum(1 for _, ok, _ in _results if ok is False)
    skipped = sum(1 for _, ok, _ in _results if ok is None)
    print(f"\n{name}: {passed} passed, {failed} failed, {skipped} skipped")
    print(f"{COUNTS_MARKER} " + json.dumps({"passed": passed, "failed": failed, "skipped": skipped}, separators=(",", ":")))
    return 1 if failed else 0


def console_lists() -> set[str]:
    """The roles the console's roles page lists by hand."""
    text = (ROOT / "web" / "src" / "features" / "roles" / "Roles.tsx").read_text(encoding="utf-8")
    found: set[str] = set()
    for name in ("INTERNAL_ROLES", "CUSTOMER_ROLES"):
        block = re.search(name + r" = new Set\(\[(.*?)\]\)", text, re.S)
        if block:
            found |= set(re.findall(r'"([a-z_]+)"', block.group(1)))
    return found


def main() -> int:
    catalogue = gen.load()
    labels, people = gen.role_words()
    sets = gen.role_sets()
    floors = gen.floors()
    powers = {p["id"]: p for p in catalogue["powers"]}
    roles = catalogue["roles"]

    heading("U204: the roles")
    check("the policy names at least the ten roles people hold today", len(people) >= 10, str(people))
    check("every role a person can hold has an entry in the catalogue", not (set(people) - set(roles)), f"missing: {sorted(set(people) - set(roles))}")
    check("the catalogue lists no role that the policy does not know as a person's role", not (set(roles) - set(people)), f"unknown: {sorted(set(roles) - set(people))}")
    check("every role has a summary and an audience", all(r.get("summary") and r.get("audience") in ("people", "platform staff") for r in roles.values()))
    check("every role has at least one line saying what it cannot do", all(r.get("cannot") for r in roles.values()))
    check("the console's roles page lists every role a person can hold", not (set(people) - console_lists()), f"missing from Roles.tsx: {sorted(set(people) - console_lists())}")
    check("every role a person can hold is in the policy's order list, so that sentences naming several roles include it", not (set(people) - set(gen.listed_order())), f"missing from order: {sorted(set(people) - set(gen.listed_order()))}")

    heading("U204: the powers")
    ignored = set(catalogue["ignored_sets"])
    mapped = {s for p in powers.values() for s in p["sets"]}
    # Every role set in the policy counts, on one line or spread over several, so that a new one cannot go unnoticed because of how it is written.
    every_set = set(re.findall(r"^([a-z_]+_roles) := ", gen.ACCESS.read_text(encoding="utf-8"), re.M))
    unmapped = sorted(every_set - mapped - ignored)
    check("every role set in the policy is a power in the catalogue, or is ignored with a reason", not unmapped, f"not documented: {unmapped}")
    check("every set a power names exists in the policy", all(s in sets for s in mapped), str(sorted(mapped - set(sets))))
    check("every ignored set exists and has a reason", all(s in every_set and catalogue["ignored_sets"][s] for s in ignored), str(sorted(ignored - every_set)))
    check("every set a power names is written on one line, so that its members can be read", mapped <= set(sets), str(sorted(mapped - set(sets))))
    check("every power a role lists is defined", all(p in powers for r in roles.values() for p in r["powers"]), str([p for r in roles.values() for p in r["powers"] if p not in powers]))
    check("no role lists a power twice", all(len(r["powers"]) == len(set(r["powers"])) for r in roles.values()))

    holds_wrong: list[str] = []
    for role in people:
        listed = set(roles.get(role, {}).get("powers", []))
        for pid, power in powers.items():
            in_policy = any(role in sets.get(s, []) for s in power["sets"])
            if in_policy != (pid in listed):
                holds_wrong.append(f"{role}: {pid} is {'held' if in_policy else 'not held'} in the policy and {'listed' if pid in listed else 'not listed'} in the catalogue")
    check("each role holds exactly the powers the catalogue lists for it (checked in both directions)", not holds_wrong, "; ".join(holds_wrong[:6]))

    not_in_wrong = [f"{role}: '{c['text']}' but it is in {s}" for role in people for c in roles.get(role, {}).get("cannot", []) for s in c.get("not_in", []) if role in sets.get(s, [])]
    unchecked = [(r, c["text"]) for r, e in roles.items() for c in e["cannot"] if not c.get("not_in")]
    check("every line that says what a role cannot do is true in the policy", not not_in_wrong, "; ".join(not_in_wrong[:4]))
    check("every cannot line names the sets it rests on, or says why it cannot be checked against a role set", all(c.get("not_in") or c.get("unchecked") for r in roles.values() for c in r["cannot"]))
    print(f"        ({len(unchecked)} cannot lines rest on a rule about a person and are not checked against a set: {', '.join(r for r, _ in unchecked)})")
    check("every role reads published data without asking, as the page says", all(floors.get(r) == gen.PUBLISHED_FLOOR for r in people), str({r: floors.get(r) for r in people}))

    heading("U204: the words")
    texts = [p["text"] for p in catalogue["powers"]] + [r["summary"] for r in roles.values()] + [a for r in roles.values() for a in r["also"]] + [c["text"] for r in roles.values() for c in r["cannot"]]
    check("no contraction in anything a reader sees", not any(CONTRACTION.search(t) for t in texts), str([t for t in texts if CONTRACTION.search(t)][:2]))
    check("no gendered pronoun", not any(PRONOUN.search(t) for t in texts), str([t for t in texts if PRONOUN.search(t)][:2]))
    check("no dash used as punctuation", not any("—" in t or "–" in t or " - " in t for t in texts))

    heading("U204: the other places a role is listed")
    principals = (ROOT / "web" / "src" / "identity" / "principals.ts").read_text(encoding="utf-8")
    # The custodian's copy depends on the department held, so a function builds it instead of an entry (the file says so at the entry's place).
    built_elsewhere = {"data_custodian"}
    absent = [r for r in people if r not in built_elsewhere and not re.search(rf"^\s+{r}: \{{", principals, re.M)]
    check("the console describes every role a person can hold, except the one built by a function (principals.ts)", not absent and "custodianCopy" in principals, str(absent))
    held = re.search(r"REQUESTABLE_ROLES = \[(.*?)\]", (ROOT / "web" / "src" / "features" / "operate" / "RolesHeld.tsx").read_text(encoding="utf-8"), re.S)
    requestable = set(re.findall(r'"([a-z_]+)"', held.group(1))) if held else set()
    check("a person can ask for every role a person can hold, except the two that run the platform (RolesHeld.tsx)", requestable == set(people) - {"platform_admin", "hybridops"},
          f"missing: {sorted(set(people) - {'platform_admin', 'hybridops'} - requestable)}; extra: {sorted(requestable - set(people))}")
    services = (ROOT / "docs" / "tools" / "generate_services_page.py").read_text(encoding="utf-8")
    check("the services page says what each role is good for (generate_services_page.py)", all(f'"{role}":' in services for role in people), str([r for r in people if f'"{r}":' not in services]))

    heading("U204: the walkthrough pages")
    import walkthrough_kit as kit  # noqa: E402  (the glossary the walkthrough pages are built from, with role names written by hand)

    # The kit's glossary entry for each role a walkthrough names, and the role it stands for. A new role needs no entry: the kit only defines the words its pages use.
    kit_roles = {"custodian": "data_custodian", "dpo": "dpo", "deid_reviewer": "deid_reviewer", "platform_admin": "platform_admin", "network_architect": "network_architect", "auditor": "privacy_auditor"}
    # A word the pages deliberately write differently from the console's label, with the reason.
    deliberate = {"custodian": "walkthrough prose says custodian, as the platform's own sentences about a department's approver do; the console label is Data custodian"}
    stale = [f"{k}: the kit says '{kit.WORDS[k][0]}' and the policy says '{labels[r]}'" for k, r in kit_roles.items() if k not in deliberate and r in labels and kit.WORDS[k][0] != labels[r]]
    check("the walkthrough kit names each role as the policy does, except where a reason is recorded", not stale, "; ".join(stale))
    check("every kit word that names a role stands for a role the policy still has", all(r in labels for r in kit_roles.values()), str([r for r in kit_roles.values() if r not in labels]))
    published = (ROOT / "docs" / "public" / "walkthroughs")
    # Only the internal names that are not also ordinary words: "analyst" is the role's name and a word the pages use correctly.
    internal_only = [r for r in people if r != labels[r].lower()]
    leaked = sorted({f"{p.name}: {name}" for p in published.glob("*-walkthrough.html") for name in internal_only if re.search(rf"\b{re.escape(name)}\b", re.sub(r"<style.*?</style>|<script.*?</script>", "", p.read_text(encoding="utf-8"), flags=re.S))})
    check("no published walkthrough page shows an internal role name", not leaked, "; ".join(leaked[:4]))

    heading("U204: the public page")
    page = gen.PAGE.read_text(encoding="utf-8") if gen.PAGE.exists() else ""
    check("the page exists", bool(page))
    try:
        expected = gen.render_page()
        why = "regenerate with docs/tools/generate_roles_reference.py"
    except (SystemExit, KeyError) as exc:   # the generator refuses while the catalogue and the policy disagree
        expected, why = None, f"the generator cannot make the page: {exc}"
    check("the page is exactly what the generator makes today", expected is not None and page == expected, why)
    visible = re.sub(r"<style.*?</style>", "", page, flags=re.S)
    check("the page names no file of the project", not re.search(r"\.(rego|py|tsx?|json)\b|verify/|platform/", visible))
    check("the page shows the label of every role and no internal name",
          all(f">{labels[r]}<" in page for r in people) and not any(f">{r}<" in page for r in people))
    check("the page has a dark mode and a body background", "prefers-color-scheme:dark" in page and "background:var(--bg)" in page)

    return summary("U204")


if __name__ == "__main__":
    sys.exit(main())
