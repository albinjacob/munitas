"""U97: start-dev.ps1 tells a running sandbox worker from the shell wrapped around a dead one.

The script decides whether to start the sandbox worker by listing processes in the
distro whose command line matches a pattern. When the pattern also matched the
wrapper shell, whose command line merely contains the same words, a worker that had
died under a live wrapper looked like one that was still running and was never
started again. This reads the pattern out of start-dev.ps1 itself and runs it
against two real processes in the distro: a stand-in worker, and a wrapper shell
that outlives it. Host run, needs the distro (WSL) and nothing else.

    .venv\\Scripts\\python.exe verify\\v97_worker_restart_pattern.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ports_config import PORTS  # noqa: E402

# verify/common.py reads these at import; nothing here connects to them.
os.environ.setdefault("PG_DSN", f"postgresql://munitas:munitas@localhost:{PORTS['postgres']}/platform")
os.environ.setdefault("MUNITAS_VERIFY_KRATOS", f"http://localhost:{PORTS['kratos_public']}")
os.environ.setdefault("S3_ENDPOINT", f"http://localhost:{PORTS['seaweedfs_s3']}")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import check, heading, summary  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DISTRO = "Ubuntu-20.04"
STAGE = "/tmp/u97-worker-pattern"


def wsl(script: str) -> str:
    out = subprocess.run(["wsl.exe", "-d", DISTRO, "--", "bash", "-c", script],
                         capture_output=True, text=True, timeout=60)
    return out.stdout


def pgrep(pattern: str) -> list[str]:
    """Matches whose working folder is the stand-in's, so a real worker running here is left out."""
    ids = [line.split()[0] for line in wsl(f"pgrep -af '{pattern}' 2>/dev/null").splitlines() if line.strip()]
    return [i for i in ids if wsl(f"readlink -f /proc/{i}/cwd").strip() == STAGE]


heading("The pattern, read from start-dev.ps1")
source = (ROOT / "start-dev.ps1").read_text(encoding="utf-8")
found = re.search(r"pgrep -af '([^']+worker\\\.sandbox_worker)'", source)
check("start-dev.ps1 holds one pgrep pattern for the sandbox worker", found is not None)
pattern = found.group(1) if found else ""
check("the pattern is anchored on the interpreter", pattern.startswith("^"), pattern)

heading("A live worker, then the wrapper left behind after it dies")
wsl(f"rm -rf {STAGE}; mkdir -p {STAGE}/worker; touch {STAGE}/worker/__init__.py; "
    f"printf 'import time\\ntime.sleep(8)\\n' > {STAGE}/worker/sandbox_worker.py")
# The wrapper runs the worker and then stays alive, as a shell window does after its program stops.
subprocess.Popen(["wsl.exe", "-d", DISTRO, "--", "bash", "-c",
                  f"cd {STAGE}; python3 -m worker.sandbox_worker; sleep 40"],
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3)
alive = pgrep(pattern)
wrapper = [p for p in wsl(f"pgrep -af 'sleep 40|worker.sandbox_worker' | grep bash | cut -d' ' -f1").split() if p]
check("while the worker runs, exactly one process matches", len(alive) == 1, str(alive))
check("the match is the interpreter, not the wrapper shell",
      bool(alive) and alive[0] not in wrapper and "python" in wsl(f"cat /proc/{alive[0]}/cmdline | tr '\\0' ' '"))

time.sleep(8)
check("the wrapper shell is still alive after the worker died",
      "sleep 40" in wsl("ps -eo args | grep -v grep | grep 'sleep 40'"))
check("with only the wrapper left, nothing matches, so the script would start a new worker",
      pgrep(pattern) == [], str(pgrep(pattern)))

wsl(f"pkill -f 'sleep 40'; rm -rf {STAGE}")
sys.exit(summary("U97"))
