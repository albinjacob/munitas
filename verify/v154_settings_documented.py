r"""U160: every setting is named properly, documented once, and the documentation is current.

Needs no database and no running stack, so it runs anywhere, including CI:

    .venv\Scripts\python.exe verify\v154_settings_documented.py

Two things are compared that nobody derives from the other. `docs/tools/settings_scan.py` reads the code: every setting it reads, its default, who reads it.
`docs/tools/settings_reference.json` is what a person wrote: what each setting means. They have to agree, and the two generated files (`.env.example` and
`docs/public/reference/configuration.html`) have to be exactly what the generator makes from them today. Then the wiring that makes a value written in
`.env` take effect is checked, and the loader that gives the host worker its settings is run for real, because a loader that is only read can be wrong.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "docs" / "tools"))

import generate_settings_reference as gen  # noqa: E402
import settings_scan  # noqa: E402

_results: list[tuple[str, bool | None, str]] = []
COUNTS_MARKER = "##MUNITAS-CHECK-COUNTS##"


def check(label: str, ok: bool, detail: str = "") -> bool:
    _results.append((label, ok, detail))
    shown = detail if (not ok or (len(detail) <= 60 and chr(10) not in detail)) else ""   # a passing check says little; a failing one says all
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({shown})" if shown else ""))
    return ok


def skip(label: str, why: str) -> None:
    _results.append((label, None, why))
    print(f"  [SKIP] {label}  ({why})")


def heading(text: str) -> None:
    print(f"\n{text}\n" + "-" * len(text))


def summary(name: str) -> int:
    passed = sum(1 for _, ok, _ in _results if ok is True)
    failed = sum(1 for _, ok, _ in _results if ok is False)
    skipped = sum(1 for _, ok, _ in _results if ok is None)
    print(f"\n{name}: {passed} passed, {failed} failed, {skipped} skipped")
    print(f"{COUNTS_MARKER} " + json.dumps({"passed": passed, "failed": failed, "skipped": skipped}, separators=(",", ":")))
    return 1 if failed else 0


# Variables that belong to software we run (Hydra reads SECRETS_SYSTEM, TTL_* and URLS_*), and so keep the names that software reads. Anything else Compose passes must start with MUNITAS_.
THIRD_PARTY = ("AWS_", "POSTGRES_", "POSTGRE_", "TEMPORAL_", "LABEL_STUDIO_", "MLFLOW_", "DB", "DJANGO_DB", "DSN", "BIND_ON_IP", "COLLECTOR_", "SECRETS_SYSTEM", "TTL_", "URLS_")
# Secrets that the host worker is built not to hold: the platform's own administrator, its master and signing keys, Cloudflare, each role's standing key.
NEVER_IN_THE_WORKER = (
    "MUNITAS_S3_ADMIN_KEY", "MUNITAS_S3_ADMIN_SECRET", "MUNITAS_MASTER_KEY", "MUNITAS_SIGNING_KEY", "MUNITAS_R2_ACCOUNT_ID", "MUNITAS_R2_ACCESS_KEY_ID",
    "MUNITAS_R2_SECRET_ACCESS_KEY", "MUNITAS_R2_API_TOKEN", "MUNITAS_S3_PIPELINE_SECRET", "MUNITAS_S3_TRAINING_JOB_SECRET",
    "MUNITAS_S3_ANNOTATION_TOOL_SECRET", "MUNITAS_S3_NOTEBOOK_EXPLORE_SECRET", "MUNITAS_S3_AGENT_RUNTIME_SECRET", "MUNITAS_PG_PASSWORD",
)
CONTRACTION = re.compile(r"\b\w+(n't|'re|'ve|'ll|'d|'m)\b|\b(it's|that's|there's|here's|let's)\b", re.I)
NUMERIC_UNITS = {"seconds", "minutes", "hours", "days", "years", "bytes", "rows", "items", "uses", "volumes"}


def service_block(text: str, name: str) -> str:
    match = re.search(rf"^  {re.escape(name)}:\s*$", text, re.M)
    if not match:
        return ""
    rest = text[match.end():]
    end = re.search(r"^  [a-z][a-z0-9-]*:\s*$", rest, re.M)
    return rest[:end.start()] if end else rest


def main() -> int:
    catalogue, scan = gen.merged()
    entries = {e["name"]: e for e in catalogue["settings"]}
    groups = {g["id"]: g for g in catalogue["groups"]}
    ports = {k for k in json.loads((ROOT / "config.json").read_text(encoding="utf-8")) if k != "_comment"}
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    heading("Names: every setting is prefixed, in one case")
    bad = [n for n in scan if not re.fullmatch(r"MUNITAS_[A-Z][A-Z0-9_]*", n)]
    check(f"all {len(scan)} settings the code and Compose read are MUNITAS_ names in upper case with underscores", not bad, str(bad[:5]))
    compose_keys = set(re.findall(r"^\s{6}([A-Z][A-Z0-9_]+):", compose, re.M))
    foreign = sorted(k for k in compose_keys if not k.startswith("MUNITAS_") and not k.startswith(THIRD_PARTY))
    check("every other variable Compose sets belongs to software we run (AWS, Postgres, Temporal and so on), and none is a setting of ours", not foreign, str(foreign))
    lower = re.findall(r"os\.environ(?:\.get\(|\[)\s*[\"']([A-Za-z0-9_]*[a-z][A-Za-z0-9_]*)[\"']", " ".join(
        p.read_text(encoding="utf-8", errors="ignore") for base in ("platform", "worker") for p in (ROOT / base).rglob("*.py")))
    check("no setting is read under a lower case or mixed case name", not lower, str(lower[:5]))

    heading("The catalogue and the code say the same things")
    port_names = {n for n in scan if n.startswith("MUNITAS_PORT_")}
    plain = {n for n in scan if n not in port_names}
    check("every setting the code or Compose reads is in the catalogue", not (plain - set(entries)), str(sorted(plain - set(entries))[:6]))
    check("every setting in the catalogue is read by the code or Compose", not (set(entries) - plain), str(sorted(set(entries) - plain)[:6]))
    check("the ports in config.json, in the catalogue and in Compose are the same set",
          ports == set(catalogue["ports"]) == {n[len("MUNITAS_PORT_"):].lower() for n in port_names},
          str(sorted(ports ^ set(catalogue["ports"]))[:6]))
    check("every setting belongs to a group that exists", all(e["group"] in groups for e in entries.values()))

    heading("What is written about each setting")
    weak = [n for n, e in entries.items() if not (e["meaning"][:1].isupper() and e["meaning"].rstrip().endswith("."))]
    check("every meaning is a full sentence: it starts with a capital and ends with a full stop", not weak, str(weak[:5]))
    weak = [g for g, v in groups.items() if not v["intro"].rstrip().endswith(".")]
    check("so is every group introduction", not weak, str(weak))
    texts = {**{n: e["meaning"] + " " + (e.get("shown") or "") for n, e in entries.items()}, **{g: v["intro"] for g, v in groups.items()}}
    dashes = [n for n, t in texts.items() if "—" in t or " -- " in t or "–" in t]
    check("no dash is used as punctuation", not dashes, str(dashes[:5]))
    short = [(n, CONTRACTION.search(t).group(0)) for n, t in texts.items() if CONTRACTION.search(t)]
    check("no contraction and no shorthand such as e.g. or i.e.", not short and not [n for n, t in texts.items() if re.search(r"\b(e\.g\.|i\.e\.)", t)], str(short[:5]))
    wrong = [n for n, e in entries.items() if e.get("unit") and e["unit"] not in NUMERIC_UNITS]
    check("every unit is one the page knows how to say", not wrong, str(wrong))
    bad_unit = [n for n, e in entries.items() if e.get("unit") in NUMERIC_UNITS and gen.defaults_of(scan[n]) and not gen.defaults_of(scan[n])[0].isdigit()]
    check("a setting with a unit has a number as its default in the code", not bad_unit, str(bad_unit))

    heading("Secrets are never printed")
    secrets = [n for n, e in entries.items() if gen.kind_of(e, catalogue) == "secret"]
    page, template = gen.PAGE.read_text(encoding="utf-8"), gen.ENV_EXAMPLE.read_text(encoding="utf-8")
    leaked = [v for n in secrets for v in gen.defaults_of(scan[n]) if len(v) >= 12 and v in page]
    check(f"no development value of the {len(secrets)} secrets appears on the public page", not leaked, str(leaked[:3]))
    check("the page says in words that a development value exists, without giving it", "A development value is used when it is not set." in page)

    heading("The generated files are exactly what the generator makes today")
    check(".env.example is current", template == gen.render_env(), "run docs/tools/generate_settings_reference.py")
    check("docs/public/reference/configuration.html is current", page == gen.render_page(), "run docs/tools/generate_settings_reference.py")
    check("the page states the true counts", f"{len(entries)} settings and {len(catalogue['ports'])} ports" in page)
    check("every setting has its own anchor on the page", all(f'id="{n}"' in page for n in entries))
    check("the template lists every setting that is not set by the platform, as a line a person can use",
          all(re.search(rf"^#? ?{n}=", template, re.M) for n, e in entries.items() if gen.kind_of(e, catalogue) != "internal" and e.get("template", "commented") != "omit"))

    heading("A value written in .env takes effect")
    api = service_block(compose, "munitas-api")
    check("the API receives .env as a whole (env_file in Compose)", bool(re.search(r"^\s+env_file:\s*\n\s+- \.env\s*$", api, re.M)))
    fixed = {"MUNITAS_PG_DSN", "MUNITAS_S3_ENDPOINT", "MUNITAS_OPA_URL", "MUNITAS_FILER_URL", "MUNITAS_TEMPORAL_ADDRESS"}
    check("addresses inside the Compose network stay fixed under environment, which wins over the file", all(re.search(rf"^\s+{n}:", api, re.M) for n in fixed),
          str([n for n in fixed if not re.search(rf"^\s+{n}:", api, re.M)]))
    start = (ROOT / "start-dev.ps1").read_text(encoding="utf-8")
    check("the start script gives the host worker its settings by name from .env", "Import-DotEnv" in start and "--worker-names" in start)

    heading("The host worker is given what it reads, and never what it must not hold")
    names = settings_scan.worker_names()
    check("the list is the settings the worker's own code reads", {"MUNITAS_TEMPORAL_ADDRESS", "MUNITAS_WHISPER_MODEL", "MUNITAS_LOG_DIR", "MUNITAS_API"} <= set(names), f"{len(names)} names")
    check("none of the secrets the worker is built not to hold is in it", not (set(NEVER_IN_THE_WORKER) & set(names)), str(sorted(set(NEVER_IN_THE_WORKER) & set(names))))
    check("nothing the platform passes to the containers it starts is in it", not [n for n in names if gen.kind_of(entries[n], catalogue) == "internal"])

    heading("The loader, run for real")
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        skip("Import-DotEnv sets what it is told to, skips blanks and strips quotes, and refuses everything else", "no PowerShell on this machine")
    else:
        source = (ROOT / "wsl-docker.ps1").read_text(encoding="utf-8")
        function = re.search(r"^function Import-DotEnv \{.*?^\}\n", source, re.M | re.S)
        check("the loader function is in wsl-docker.ps1", bool(function))
        if function:
            with tempfile.TemporaryDirectory() as folder:
                envfile = Path(folder) / "test.env"
                envfile.write_text("\n".join([
                    "# a comment", "MUNITAS_WHISPER_MODEL=tiny", 'MUNITAS_TENANT="quoted-value"', "MUNITAS_SPACY_MODEL='single-quoted'",
                    "MUNITAS_GLINER_MODEL=", "MUNITAS_S3_ADMIN_SECRET=must-not-reach-the-worker", "MUNITAS_MASTER_KEY=must-not-reach-the-worker",
                    "  MUNITAS_WORK = /spaces/around/equals  ", "not a setting line", ""]), encoding="utf-8")
                script = function.group(0) + (
                    "\n$only = @('MUNITAS_WHISPER_MODEL','MUNITAS_TENANT','MUNITAS_SPACY_MODEL','MUNITAS_GLINER_MODEL','MUNITAS_WORK')"
                    f"\n$set = Import-DotEnv -Path '{envfile}' -Only $only"
                    "\nforeach ($n in 'MUNITAS_WHISPER_MODEL','MUNITAS_TENANT','MUNITAS_SPACY_MODEL','MUNITAS_GLINER_MODEL','MUNITAS_WORK','MUNITAS_S3_ADMIN_SECRET','MUNITAS_MASTER_KEY')"
                    " { \"$n=[\" + [Environment]::GetEnvironmentVariable($n) + \"]\" }"
                    "\n'set=' + ($set -join ',')")
                done = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=60)
                out = {line.split("=", 1)[0]: line.split("=", 1)[1] for line in done.stdout.splitlines() if "=" in line}
                check("a named setting is set, with its quotes removed and the spaces around it trimmed",
                      out.get("MUNITAS_WHISPER_MODEL") == "[tiny]" and out.get("MUNITAS_TENANT") == "[quoted-value]"
                      and out.get("MUNITAS_SPACY_MODEL") == "[single-quoted]" and out.get("MUNITAS_WORK") == "[/spaces/around/equals]", done.stdout[-200:] + done.stderr[-120:])
                check("a blank value is not set, so it never overrides the default in the code", out.get("MUNITAS_GLINER_MODEL") == "[]")
                check("the administrator's secret and the master key are not set, though they are in the file",
                      out.get("MUNITAS_S3_ADMIN_SECRET") == "[]" and out.get("MUNITAS_MASTER_KEY") == "[]")
                check("it reports exactly what it set", set(out.get("set", "").split(",")) == {"MUNITAS_WHISPER_MODEL", "MUNITAS_TENANT", "MUNITAS_SPACY_MODEL", "MUNITAS_WORK"}, out.get("set", ""))

    return summary("U160")


if __name__ == "__main__":
    sys.exit(main())
