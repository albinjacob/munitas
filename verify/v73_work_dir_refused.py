"""U73: a worker refuses a work or corpus directory it cannot honestly use.

Both directories sit under MUNITAS_DATA unless MUNITAS_WORK or
MUNITAS_CORPUS names one directly, and there is no built-in root. A worker
must never invent a location: a Windows path read on Linux is an ordinary
relative folder name, and an empty value (what Compose hands the quickstart
worker when MUNITAS_HOST_WORK_DIR is unset) would silently be the start
directory itself. Either way run files land where nothing will look.

config.require_work_dir() and config.require_corpus_dir() refuse, name the
variable and its value, and say what to set. This proves each case on the
OS it belongs to, and proves the refusal happens before anything is created:
every case runs in an empty temporary directory, and a case passes only if
that directory is still empty afterwards.

The Windows cases run with this interpreter. The Linux cases run inside the
WSL2 distro with its sandbox worker venv, the same interpreter start-dev.ps1
uses for worker.sandbox_worker, and are skipped (not passed) without it.

    .venv\\Scripts\\python.exe verify\\v73_work_dir_refused.py

Needs no running services.
"""
from __future__ import annotations

import os
import shutil
import string
import subprocess
import sys
import tempfile
from pathlib import Path

# common.py reads PG_DSN at import. This check opens no connection, so any
# value will do; an existing one is left alone.
os.environ.setdefault("PG_DSN", "unused-by-v73")
sys.path.insert(0, str(Path(__file__).parent))
from common import check, heading, skip, summary  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
WSL_DISTRO = os.environ.get("MUNITAS_VERIFY_WSL_DISTRO", "Ubuntu-20.04")
WSL_PYTHON = "$HOME/.munitas/sandbox-venv/bin/python"

WORK_CALL = "import worker.config as c; print(c.require_work_dir())"
CORPUS_CALL = "import worker.config as c; print(c.require_corpus_dir())"


def _env(**values: str | None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k not in ("MUNITAS_WORK", "MUNITAS_CORPUS", "MUNITAS_DATA")}
    env["PYTHONPATH"] = str(REPO)
    env.update({k: v for k, v in values.items() if v is not None})
    return env


def _run_here(code: str, cwd: Path, **values: str | None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], cwd=cwd, env=_env(**values),
                          capture_output=True, text=True, timeout=60)


def _expect(label: str, proc: subprocess.CompletedProcess, cwd: Path, *,
            refused: bool, mentions: tuple[str, ...] = ()) -> None:
    out = (proc.stdout + proc.stderr).strip()
    left_behind = sorted(p.name for p in cwd.iterdir())
    if refused:
        said = all(m in out for m in mentions)
        ok = proc.returncode != 0 and "Refusing to start:" in out and said and not left_behind
        detail = (f"exit {proc.returncode}, created {left_behind}" if left_behind
                  else f"exit {proc.returncode}" if said
                  else f"message lacks {[m for m in mentions if m not in out]}: {out[-200:]}")
    else:
        ok = proc.returncode == 0 and not left_behind
        detail = f"exit {proc.returncode}: {out[-200:]}" if not ok else out.splitlines()[-1]
    check(label, ok, detail)


def _free_drive() -> str:
    used = {d[0].upper() for d in os.listdrives()} if hasattr(os, "listdrives") else {
        c for c in string.ascii_uppercase if Path(f"{c}:/").exists()}
    return next(c for c in reversed(string.ascii_uppercase) if c not in used)


def windows_cases() -> None:
    heading("On Windows, with this interpreter")
    with tempfile.TemporaryDirectory() as scratch:
        scratch = Path(scratch)
        target = scratch / "target"

        def fresh() -> Path:
            cwd = scratch / f"cwd-{len(list(scratch.iterdir()))}"
            cwd.mkdir()
            return cwd

        cwd = fresh()
        _expect("1. an absolute work directory is created and used",
                _run_here(WORK_CALL, cwd, MUNITAS_WORK=str(target / "work")), cwd, refused=False)
        check("   ...and it exists afterwards", (target / "work").is_dir())

        cwd = fresh()
        _expect("2. unset, the work folder under MUNITAS_DATA is used",
                _run_here(WORK_CALL, cwd, MUNITAS_DATA=str(target / "data")), cwd, refused=False)
        check("   ...and it is MUNITAS_DATA/work", (target / "data" / "work").is_dir())

        cwd = fresh()
        _expect("2b. with nothing set, it is refused and names MUNITAS_DATA",
                _run_here(WORK_CALL, cwd), cwd,
                refused=True, mentions=("MUNITAS_WORK", "MUNITAS_DATA"))

        drive = _free_drive()
        cwd = fresh()
        _expect(f"3. a path on a drive that does not exist ({drive}:) is refused",
                _run_here(WORK_CALL, cwd, MUNITAS_WORK=f"{drive}:/munitas-data/work"), cwd,
                refused=True, mentions=("MUNITAS_WORK", f"{drive}:/munitas-data/work"))

        cwd = fresh()
        _expect("5. an empty value is refused, not read as the current directory",
                _run_here(WORK_CALL, cwd, MUNITAS_WORK=""), cwd,
                refused=True, mentions=("MUNITAS_WORK", "empty"))

        cwd = fresh()
        _expect("6. a relative path is refused",
                _run_here(WORK_CALL, cwd, MUNITAS_WORK="work"), cwd,
                refused=True, mentions=("MUNITAS_WORK", "'work'", "not an absolute path"))

        blocker = scratch / "a-file"
        blocker.write_text("not a directory")
        cwd = fresh()
        _expect("8. an absolute path that cannot be created is refused, naming the error",
                _run_here(WORK_CALL, cwd, MUNITAS_WORK=str(blocker / "work")), cwd,
                refused=True, mentions=("MUNITAS_WORK", "writable directory"))

        cwd = fresh()
        _expect("every refusal says what to set: Compose names MUNITAS_HOST_WORK_DIR",
                _run_here(WORK_CALL, cwd, MUNITAS_WORK=""), cwd,
                refused=True, mentions=("MUNITAS_HOST_WORK_DIR", "start-dev.ps1",
                                        "export MUNITAS_WORK="))

        heading("The corpus, on Windows")
        (target / "corpus").mkdir(parents=True)
        cwd = fresh()
        _expect("an existing absolute corpus directory is accepted",
                _run_here(CORPUS_CALL, cwd, MUNITAS_CORPUS=str(target / "corpus")), cwd,
                refused=False)
        cwd = fresh()
        _expect("a corpus directory that does not exist is refused, never created",
                _run_here(CORPUS_CALL, cwd, MUNITAS_CORPUS=str(target / "no-corpus")), cwd,
                refused=True, mentions=("MUNITAS_CORPUS", "no directory exists"))
        check("   ...and it was not created", not (target / "no-corpus").exists())
        cwd = fresh()
        _expect("an empty corpus value is refused",
                _run_here(CORPUS_CALL, cwd, MUNITAS_CORPUS=""), cwd,
                refused=True, mentions=("MUNITAS_CORPUS", "empty"))

        heading("At the entry points")
        cwd = fresh()
        _entry_point_refuses("run_pipeline refuses a bad corpus before contacting anything",
                             ["-m", "worker.run_pipeline", "--triggered-by", "x"], cwd,
                             MUNITAS_CORPUS="")
        cwd = fresh()
        _entry_point_refuses("schedule_pipeline refuses a bad corpus before contacting anything",
                             ["-m", "worker.schedule_pipeline", "--schedule-id", "x"], cwd,
                             MUNITAS_CORPUS="")
        cwd = fresh()
        _entry_point_refuses("worker.main refuses a bad work directory before connecting",
                             ["-m", "worker.main"], cwd, MUNITAS_WORK="")


def _entry_point_refuses(label: str, argv: list[str], cwd: Path, **values: str) -> None:
    # Entry points exit through SystemExit(message), so there is no traceback
    # and no class name to look for: the message itself is the evidence.
    proc = subprocess.run([sys.executable, *argv], cwd=cwd, env=_env(**values),
                          capture_output=True, text=True, timeout=120)
    out = (proc.stdout + proc.stderr).strip()
    left_behind = sorted(p.name for p in cwd.iterdir())
    # "in", not startswith: library import warnings reach stderr first.
    ok = (proc.returncode == 1 and "Refusing to start:" in out
          and "Traceback" not in out and not left_behind)
    check(label, ok, f"exit {proc.returncode}" + (f", created {left_behind}" if left_behind else "")
          + ("" if ok else f": {out[-200:]}"))


def linux_cases() -> None:
    heading(f"On Linux, inside WSL ({WSL_DISTRO})")
    if os.name != "nt" or not shutil.which("wsl"):
        skip("the Linux cases", "this is not a Windows host with WSL")
        return
    # Scripts go to bash on stdin, never as a -c argument: wsl.exe hands its
    # arguments through another shell, which expands every $ one layer early.
    def wsl_bash(script: str, timeout: int) -> subprocess.CompletedProcess:
        return subprocess.run(["wsl", "-d", WSL_DISTRO, "--", "bash", "-ls"],
                              input=script, capture_output=True, text=True,
                              timeout=timeout)

    probe = wsl_bash(f"test -x {WSL_PYTHON} && wslpath -a '{REPO.as_posix()}'", 60)
    if probe.returncode != 0:
        skip("the Linux cases", f"no sandbox worker venv at {WSL_PYTHON} in {WSL_DISTRO}")
        return
    repo_in_wsl = probe.stdout.strip()

    def run(label: str, setup: str, code: str, *, refused: bool,
            mentions: tuple[str, ...] = ()) -> None:
        # Each case gets its own empty directory, and the listing of that
        # directory after the call is printed last, so a stray P: folder
        # shows up as evidence rather than being cleaned up unseen.
        script = (
            "d=$(mktemp -d) && cd \"$d\" && unset MUNITAS_WORK MUNITAS_CORPUS MUNITAS_DATA && "
            f"{setup} PYTHONPATH='{repo_in_wsl}' {WSL_PYTHON} -c \"{code}\" 2>&1; "
            "rc=$?; echo \"##rc=$rc\"; echo \"##left=$(ls -A)\"; rm -rf \"$d\" \"$d-elsewhere\""
        )
        proc = wsl_bash(script, 120)
        lines = proc.stdout.strip().splitlines()
        rc = next((int(x[5:]) for x in lines
                   if x.startswith("##rc=") and x[5:].isdigit()), -1)
        left = next((x[7:] for x in lines if x.startswith("##left=")), "?")
        out = "\n".join(x for x in lines if not x.startswith("##"))
        if refused:
            said = all(m in out for m in mentions)
            ok = rc != 0 and "Refusing to start:" in out and said and left == ""
        else:
            ok = rc == 0 and left == ""
        detail = f"exit {rc}" + (f", created {left!r}" if left else "")
        if not ok:
            detail += f": {out[-200:]}"
        check(label, ok, detail)

    run("4. with nothing set, it is refused and no folder appears",
        "", WORK_CALL, refused=True,
        mentions=("MUNITAS_WORK", "not set", "MUNITAS_DATA"))
    run("5. an empty value is refused (what Compose gives worker-lite today)",
        "MUNITAS_WORK=''", WORK_CALL, refused=True, mentions=("MUNITAS_WORK", "empty"))
    run("6. a relative path is refused",
        "MUNITAS_WORK=work", WORK_CALL, refused=True, mentions=("not an absolute path",))
    run("7. a Windows path exported inside WSL is refused",
        "MUNITAS_WORK='P:/munitas-data/work'", WORK_CALL, refused=True,
        mentions=("'P:/munitas-data/work'", "Windows path"))
    run("1. an absolute Linux path, like the one start-dev.ps1 passes, is accepted",
        "MUNITAS_WORK=\"$d-elsewhere/work\"", WORK_CALL, refused=False)
    run("the corpus: with nothing set, it is refused",
        "", CORPUS_CALL, refused=True, mentions=("MUNITAS_CORPUS", "MUNITAS_DATA"))
    # The process that actually made the stray P: folder: the sandbox worker,
    # started by hand in the distro with nothing set. Exits through
    # SystemExit(message) before it reaches Temporal.
    run("worker.sandbox_worker started with nothing set refuses and creates nothing",
        "", "import runpy; runpy.run_module('worker.sandbox_worker', run_name='__main__')",
        refused=True, mentions=("MUNITAS_WORK", "export MUNITAS_WORK="))


if __name__ == "__main__":
    windows_cases()
    linux_cases()
    sys.exit(summary("U73"))
