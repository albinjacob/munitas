r"""U201: the sandbox copies in the files of a dataset version however the version lists them.

Runs on the host, no platform needed (the settings of U193 are enough to import the shared helpers):

    .venv\Scripts\python.exe verify\v187_sandbox_staging_keys.py

A sandboxed agent that asks for a copy of its data is given the files of the dataset version it was started on. A version made by the pipeline lists each file by its full storage key. A version made by
uploading files lists each by its name, relative to the version's own prefix, while the file is stored under that prefix. Asking storage for the bare name found nothing, so an agent run on uploaded
files could not get its data. This proves the list the copy step works from is right for both, and that nothing in a manifest can place a file outside the data folder.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "verify"))

from common import check, heading, summary  # noqa: E402
from worker.sandbox_run import _staging_manifest  # noqa: E402

PREFIX = "health/0d1e/v1"


def main() -> int:
    heading("U201: how the copy step asks storage for each file")
    full = _staging_manifest([{"key": f"{PREFIX}/part-0.json", "bytes": 4, "sha256": "a"}], PREFIX)
    check("a key that already starts with the prefix is asked for as it is, and the prefix is stripped from the path the agent sees",
          full == [{"key": f"{PREFIX}/part-0.json", "relative_path": "part-0.json", "bytes": 4, "sha256": "a"}], str(full))
    bare = _staging_manifest([{"key": "visit-1.txt", "bytes": 9, "sha256": "b"}], PREFIX)
    check("a file listed by its name is asked for under the version's prefix, which is where upload stored it", bare[0]["key"] == f"{PREFIX}/visit-1.txt", str(bare))
    check("and the agent sees it at that name", bare[0]["relative_path"] == "visit-1.txt")
    nested = _staging_manifest([{"key": "notes/visit-1.txt"}, {"key": f"{PREFIX}/notes/visit-2.txt"}], PREFIX)
    check("a file in a folder keeps the folder, listed either way", [(e["key"], e["relative_path"]) for e in nested] == [(f"{PREFIX}/notes/visit-1.txt", "notes/visit-1.txt"), (f"{PREFIX}/notes/visit-2.txt", "notes/visit-2.txt")], str(nested))
    check("bytes and checksum are passed along, and may be missing", bare[0]["bytes"] == 9 and bare[0]["sha256"] == "b" and _staging_manifest([{"key": "x.txt"}], PREFIX)[0]["bytes"] is None)

    heading("U201: nothing can place a file outside the data folder")
    out = _staging_manifest([{"key": "../../etc/passwd"}, {"key": "/absolute/path.txt"}, {"key": "a/../../b.txt"}, {"key": f"{PREFIX}/../other/c.txt"}], PREFIX)
    check("a path that climbs out of the folder is cut down to its file name", [e["relative_path"] for e in out] == ["passwd", "path.txt", "b.txt", "c.txt"], str([e["relative_path"] for e in out]))
    check("no path the agent sees starts with a slash or contains ..", all(not e["relative_path"].startswith("/") and ".." not in e["relative_path"].split("/") for e in out))
    check("an empty list gives an empty list", _staging_manifest([], PREFIX) == [])
    return summary("U201")


if __name__ == "__main__":
    sys.exit(main())
