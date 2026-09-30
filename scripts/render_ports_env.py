r"""Sync config.json's ports into the things that run from them and cannot
read config.json themselves.

Two different problems, solved here because they have the same root cause
and the same fix shape:

1. docker-compose.yml's own ${VAR} substitution. Compose has no way to read
   a JSON file for this -- it only ever reads the shell environment or a
   project-root .env. So this writes every port into a clearly-delimited
   block in .env, leaving the rest of your .env (PG_PASSWORD, MUNITAS_DATA,
   secrets) exactly as it was.
2. infra/kratos/kratos.yml. Ory Kratos reads this file directly and has no
   ${VAR} expansion of its own, so infra/kratos/kratos.yml.template holds
   {{TOKEN}} placeholders instead, and this renders the real kratos.yml
   from it the same way.

ARCHITECTURE.md's "Key URLs" table used to be a third target here, spliced
in the same way. Deliberately reverted: unlike .env and kratos.yml, that
table is not something a running service reads, it is prose a person reads,
and a doc a start script silently rewrites on every run reads as unexpected
even when the mechanism is sound (raised directly, 2026-09-29). It is now
hand-maintained, shows the *default* ports, and points at config.json and
docs/internal/dev/services.html for what is actually running. This module no longer
touches it.

config.json stays the one thing a person actually edits. Run automatically
by start-dev.ps1 before `docker compose up`; run it by hand after editing
config.json if you're starting the stack some other way:

    .venv\Scripts\python.exe scripts\render_ports_env.py

Both outputs are idempotent to regenerate and gitignored -- generated
files, not something to hand-edit or commit.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.json"
ENV = ROOT / ".env"
KRATOS_TEMPLATE = ROOT / "infra" / "kratos" / "kratos.yml.template"
KRATOS_OUT = ROOT / "infra" / "kratos" / "kratos.yml"

BEGIN = "# BEGIN PORTS (generated from config.json by scripts/render_ports_env.py -- do not edit this block by hand, edit config.json instead)"
END = "# END PORTS"


def render_env(ports: dict[str, int]) -> None:
    block_lines = [BEGIN]
    for name, port in ports.items():
        block_lines.append(f"PORT_{name.upper()}={port}")
    block_lines.append(END)
    block = "\n".join(block_lines)

    existing = ENV.read_text(encoding="utf-8") if ENV.exists() else ""

    if BEGIN in existing and END in existing:
        before = existing[: existing.index(BEGIN)]
        after = existing[existing.index(END) + len(END):]
        new_content = before + block + after
    elif existing and not existing.endswith("\n"):
        new_content = existing + "\n\n" + block + "\n"
    elif existing:
        new_content = existing + "\n" + block + "\n"
    else:
        new_content = block + "\n"

    ENV.write_text(new_content, encoding="utf-8")
    print(f"wrote {len(ports)} ports from config.json into {ENV}")


def render_kratos(ports: dict[str, int]) -> None:
    text = KRATOS_TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("{{KRATOS_PUBLIC}}", str(ports["kratos_public"]))
    text = text.replace("{{CONSOLE_DEV}}", str(ports["console_dev"]))
    if "{{" in text:
        remaining = {tok for tok in text.split("{{")[1:]}
        raise SystemExit(
            f"kratos.yml.template still has unresolved placeholders: {remaining}. "
            "Add them to config.json and to render_kratos() above."
        )
    KRATOS_OUT.write_text(text, encoding="utf-8")
    print(f"wrote {KRATOS_OUT}")


def main() -> None:
    ports = json.loads(CONFIG.read_text(encoding="utf-8"))
    ports = {k: v for k, v in ports.items() if not k.startswith("_")}
    render_env(ports)
    render_kratos(ports)


if __name__ == "__main__":
    main()
