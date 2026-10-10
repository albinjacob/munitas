r"""U187: nothing under a copyleft licence is installed or asked for, so "permissive open-source licences" stays true.

Runs on the host and needs no database. It looks in two places:

  * what the repository asks for: every requirements file, the CI workflow, the compose file and the Dockerfiles, and every import in the code, for a driver
    or library that is known to be copyleft (the psycopg family, which is LGPL 3, is the one this project removed);
  * what is installed: every package in this Python, in the API image, in the table worker's image, in the images of the derivation runner and of MLflow, and in
    the virtual environments inside the WSL distro, for a licence in the GPL family. A package under the Mozilla Public Licence 2.0, which applies file by file,
    passes only if it is on the short list below, so a new one has to be looked at and added on purpose.
  * what is inside the packages: the declared licence of a package says nothing about the libraries its wheel carries, so the files themselves are listed too:
    a native library or a vendored module under the LGPL fails, and the few that are known and recorded in docs/internal/LICENCES.md (the GPL video codecs in
    PyAV's FFmpeg, NVIDIA's CUDA libraries) pass only while they stay on the short list below.

    .venv\Scripts\python.exe verify\v176_licences.py

An environment that cannot be reached is reported as skipped, and a skip is not a pass.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT))

import os  # noqa: E402

os.environ.setdefault("MUNITAS_PG_DSN", "postgresql://munitas:munitas@localhost:5432/platform")
os.environ.setdefault("MUNITAS_VERIFY_KRATOS", "http://localhost:4433")
os.environ.setdefault("MUNITAS_S3_ENDPOINT", "http://localhost:8333")

from common import check, heading, skip, summary  # noqa: E402

# Packages and modules whose licence is a copyleft one and which this project does not use.
DENIED = ("psycopg", "psycopg2", "psycopg2-binary", "psycopg-binary", "psycopg-pool", "psycopg_pool", "langgraph-checkpoint-postgres", "mysqlclient", "pymysql-gpl")
# Mozilla Public Licence 2.0 applies file by file, to the package's own files, and nothing here changes one. Each is a package other packages pull in, and each
# is on this list because somebody looked: certifi (a bundle of root certificates), orjson (JSON, also Apache-2.0 or MIT), tqdm (progress bars, also MIT).
MPL_LOOKED_AT = {"certifi", "orjson", "tqdm"}
DISTRO = "Munitas"
IMAGES = (("munitas-derive-runner:1", "the derivation runner's image"), ("ghcr.io/mlflow/mlflow:v2.16.2", "MLflow's image"))

# Run in each environment: prints one path for every file inside site-packages that is a library or a vendored module under a licence this project does not accept.
# It is sent to each Python on its standard input, because a script with quotes and backslashes does not survive being an argument to wsl.exe.
SCAN_FILES = r"""
import os, re, site
bad = re.compile(r"libsndfile|_soundfile_data|libpq[-.]|libmysql|libfdk|[/\\]autocommand[/\\]")
decided = re.compile(r"libx26[45]|cudart|cublas|cudnn|cufft|cusparse|cusolver|curand|nvrtc|nvJitLink|nvToolsExt|nvperf|cupti")
for sp in [p for p in site.getsitepackages() if os.path.isdir(p)]:
    for top, dirs, files in os.walk(sp):
        for name in files + dirs:
            path = os.path.join(top, name)
            rel = os.path.relpath(path, sp)
            if bad.search(rel) and not rel.endswith(".dist-info"):
                print("DENIED|" + rel)
            elif decided.search(name) and not name.endswith((".lib", ".h", ".txt")):
                print("DECIDED|" + rel)
"""
# Known, recorded in docs/internal/LICENCES.md, and not yet resolved: the environment that runs the speech models (the host worker) holds the GPL video codecs that
# PyAV's FFmpeg is built with, and NVIDIA's CUDA libraries inside torch. Neither is in the API image, the table worker's image or the WSL environments.
DECIDED_FILES = {
    "libx264 / libx265, GPL video codecs inside the FFmpeg that PyAV carries": re.compile(r"libx26[45]"),
    "NVIDIA CUDA libraries inside torch (proprietary, redistributable)": re.compile(r"cudart|cublas|cudnn|cufft|cusparse|cusolver|curand|nvrtc|nvJitLink|nvToolsExt|nvperf|cupti"),
}

# Run in each environment: prints "name|version|licence text" for every installed distribution, so the same rule applies everywhere.
SCAN = r'''
import importlib.metadata as m
for d in m.distributions():
    meta = d.metadata
    lic = (meta.get("License-Expression") or meta.get("License") or "")
    lic = lic if len(lic) < 120 else lic[:120]
    cls = "; ".join(c.split("::")[-1].strip() for c in (meta.get_all("Classifier") or []) if c.startswith("License ::"))
    print("%s|%s|%s | %s" % (meta["Name"], d.version, lic.replace("\n", " ").replace("|", "/"), cls))
'''
GPL_FAMILY = re.compile(r"\b(L?GPL|AGPL|Affero|EUPL|SSPL|OSL|CPAL|Commons Clause)\b|GNU (Lesser |Affero )?General Public", re.I)
MPL = re.compile(r"\bMPL\b|Mozilla Public", re.I)


def normal(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def judge(label: str, lines: list[str]) -> None:
    gpl, mpl, unknown = [], [], 0
    for line in lines:
        parts = line.split("|", 2)
        if len(parts) < 3:
            continue
        name, version, text = parts
        if not text.replace("|", "").strip():
            unknown += 1
        if GPL_FAMILY.search(text):
            gpl.append(f"{name} {version} ({text.strip()[:60]})")
        elif MPL.search(text) and normal(name) not in MPL_LOOKED_AT:
            mpl.append(f"{name} {version}")
    check(f"{label}: {len(lines)} packages, none under the GPL family", not gpl, "; ".join(gpl))
    check(f"{label}: none under the Mozilla Public Licence that nobody has looked at", not mpl, "; ".join(mpl))


def judge_files(label: str, lines: list[str]) -> None:
    denied = sorted({l.split("|", 1)[1] for l in lines if l.startswith("DENIED|")})
    decided = [l.split("|", 1)[1] for l in lines if l.startswith("DECIDED|")]
    check(f"{label}: no native library or vendored module under the LGPL or GPL inside a package", not denied, "; ".join(denied[:4]))
    unlisted = [d for d in decided if not any(rx.search(d) for rx in DECIDED_FILES.values())]
    check(f"{label}: the libraries that are not permissive are only the ones recorded in docs/internal/LICENCES.md", not unlisted, "; ".join(unlisted[:4]))
    for what, rx in DECIDED_FILES.items():
        hits = [d for d in decided if rx.search(d)]
        if hits:
            print(f"    note: {label} holds {len(hits)} file(s) of: {what}")


def what_is_asked_for() -> None:
    heading("What the repository asks for")
    asked = []
    for path in list(ROOT.glob("**/requirements*.txt")) + [ROOT / "docker-compose.yml"] + list((ROOT / ".github" / "workflows").glob("*.yml")) + list(ROOT.glob("**/Dockerfile*")):
        if any(part in (".venv", "node_modules", "history", ".git") for part in path.parts):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            code = line.split("#", 1)[0]
            for name in re.findall(r"[A-Za-z0-9][A-Za-z0-9_.\-]*", code):
                if normal(name) in {normal(d) for d in DENIED} and re.search(rf"(^|[\s\"'=])({re.escape(name)})(\[|==|>=|<=|\s|$|\"|')", code) and "import" not in code:
                    asked.append(f"{path.relative_to(ROOT)}:{number}: {name}")
    check("no requirements file, workflow, compose file or Dockerfile asks for a package under a copyleft licence", not asked, "; ".join(asked[:5]))
    imported = []
    for path in ROOT.glob("**/*.py"):
        if any(part in (".venv", "node_modules", "history", ".git", "__pycache__") for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else ([node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            for n in names:
                if n.split(".")[0] in ("psycopg", "psycopg2", "psycopg_pool", "MySQLdb"):
                    imported.append(f"{path.relative_to(ROOT)}:{node.lineno}: {n}")
    check("no code imports a driver under a copyleft licence", not imported, "; ".join(imported[:5]))


def run(argv: list[str], text: str | None = None) -> tuple[int, str]:
    done = subprocess.run(argv, input=text, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    return done.returncode, done.stdout


def what_is_installed() -> None:
    heading("What is installed")
    code, out = run([sys.executable, "-c", SCAN])
    judge("this Python", out.splitlines()) if code == 0 else skip("this Python", out[-100:])
    code, out = run([sys.executable, "-c", SCAN_FILES])
    judge_files("this Python", out.splitlines()) if code == 0 else skip("the files of this Python", out[-100:])
    for container, label in (("munitas-munitas-api-1", "the API image"), ("munitas-table-worker-1", "the table worker's image")):
        code, out = run(["wsl.exe", "-d", DISTRO, "--", "docker", "exec", "-i", container, "python", "-c", SCAN])
        if code == 0 and out.strip():
            judge(label, out.splitlines())
            code, out = run(["wsl.exe", "-d", DISTRO, "--", "docker", "exec", "-i", container, "python", "-"], SCAN_FILES)
            judge_files(label, out.splitlines()) if code == 0 else skip(f"the files of {label}", out[-100:])
        else:
            skip(label, "the container is not running or cannot be reached")
    for image, label in IMAGES:
        code, out = run(["wsl.exe", "-d", DISTRO, "--", "docker", "run", "--rm", "-i", "--entrypoint", "python", image, "-c", SCAN])
        if code == 0 and out.strip():
            judge(label, out.splitlines())
            code, out = run(["wsl.exe", "-d", DISTRO, "--", "docker", "run", "--rm", "-i", "--entrypoint", "python", image, "-"], SCAN_FILES)
            judge_files(label, out.splitlines()) if code == 0 else skip(f"the files of {label}", out[-100:])
        else:
            skip(label, "the image is not built or cannot be run")
    code, venvs = run(["wsl.exe", "-d", DISTRO, "--", "bash", "-lc", "ls -d $HOME/.munitas/*venv/bin/python 2>/dev/null"])
    found = [v for v in venvs.split() if v.strip()]
    if not found:
        skip("the virtual environments in the WSL distro", "there are none, or the distro cannot be reached")
    for python in found:
        code, out = run(["wsl.exe", "-d", DISTRO, "--", "bash", "-lc", f"{python} -c '{SCAN}'"])
        if code == 0 and out.strip():
            name = python.split('/.munitas/')[1].split('/')[0]
            judge(f"{name} in the distro", out.splitlines())
            code, out = run(["wsl.exe", "-d", DISTRO, "--", "bash", "-lc", f"{python} -"], SCAN_FILES)
            judge_files(f"{name} in the distro", out.splitlines()) if code == 0 else skip(f"the files of {name}", out[-100:])
        else:
            skip(python, "its Python could not be run")


def main() -> int:
    what_is_asked_for()
    what_is_installed()
    return summary("U187")


if __name__ == "__main__":
    sys.exit(main())
