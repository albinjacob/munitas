"""U96: the sandbox worker starts even when a leftover container has already exited.

At start-up the worker removes any container a previous process left behind.
Docker refuses to kill one that is not running (HTTP 409), and the worker used
to stop on that refusal, so a worker that died after its container finished
could not be started again until somebody removed the container by hand. This
runs the real clean-up code against stand-in containers, with no Docker needed.

    .venv\Scripts\python.exe verify\v96_reap_leftover_containers.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ports_config import PORTS  # noqa: E402

# verify/common.py reads these at import; nothing here connects to them.
os.environ.setdefault("PG_DSN", f"postgresql://munitas:munitas@localhost:{PORTS['postgres']}/platform")
os.environ.setdefault("MUNITAS_VERIFY_KRATOS", f"http://localhost:{PORTS['kratos_public']}")
os.environ.setdefault("S3_ENDPOINT", f"http://localhost:{PORTS['seaweedfs_s3']}")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from docker.errors import APIError  # noqa: E402

from common import check, heading, summary  # noqa: E402
from worker import sandbox_run  # noqa: E402


class Leftover:
    def __init__(self, kill_error=None):
        self.labels = {"munitas.run_id": "u96"}
        self.attrs = {"Created": "now"}
        self.short_id = "abc123"
        self.kill_error, self.removed = kill_error, False

    def kill(self):
        if self.kill_error:
            raise self.kill_error

    def remove(self, force=False):
        self.removed = True


def reap(*containers) -> tuple[int | None, str]:
    client = mock.Mock()
    client.containers.list.return_value = list(containers)
    with mock.patch.object(sandbox_run.docker, "from_env", return_value=client):
        try:
            return sandbox_run.reap_orphaned_containers(), ""
        except Exception as exc:  # noqa: BLE001
            return None, f"{type(exc).__name__}: {exc}"


def api_error(status: int) -> APIError:
    response = mock.Mock(status_code=status)
    return APIError("stand-in", response=response, explanation="stand-in")


def main() -> int:
    heading("U96: start-up clean-up of containers a previous worker left behind")
    running, exited = Leftover(), Leftover(api_error(409))
    count, why = reap(running, exited)
    check("a running leftover and one that already exited are both removed",
          count == 2 and running.removed and exited.removed, why or f"{count} removed")
    broken = Leftover(api_error(500))
    count, why = reap(broken)
    check("any other Docker error is still raised, not swallowed", count is None and "APIError" in why, why[:80])
    check("and the container was not removed behind it", not broken.removed, "left in place")
    return summary("U96")


if __name__ == "__main__":
    sys.exit(main())
