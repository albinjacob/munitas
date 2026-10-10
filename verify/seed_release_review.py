"""Make a result that is waiting for a reviewer's decision, for a browser test or a walkthrough to open. Prints one line of JSON.

    docker compose exec -T munitas-api python /verify/seed_release_review.py bad     files that fail checks (a copy of the original, a "sound" file that is not sound)
    docker compose exec -T munitas-api python /verify/seed_release_review.py good    files that pass every check

The run was started by the verification engineer, who is also the uploader of the pipeline version, so the seeded reviewer (`canary-reviewer`) may
decide it. It is built the way the release-review check builds it, in the verification organisation, from files invented here.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/app")
sys.path.insert(0, "/")

from common import CANARY, api, bearer_for, fixture_tenant, require_api  # noqa: E402
from v128_person_keys_erasure import ENGINEER, register_dataset  # noqa: E402
from v137_locked_files import make_wav, upload_whole  # noqa: E402
from v151_release_review import make_result  # noqa: E402


def main() -> int:
    kind = sys.argv[1] if len(sys.argv) > 1 else "bad"
    require_api()
    fixture_tenant(CANARY)
    tag = uuid.uuid4().hex[:6]
    dataset = register_dataset(ENGINEER)
    plain = make_wav(60_000)
    upload_whole(dataset, "r-0001.wav", plain, f"SEED-{tag}-p")
    src = api("POST", f"/datasets/{dataset}/seal-files", headers=bearer_for(ENGINEER)).json()["id"]
    if kind == "bad":
        files = {f"x-{n:02d}.bin": os.urandom(2000 + n) for n in range(6)}
        files["same.wav"] = None
        files["notsound.wav"] = os.urandom(3000)
    else:
        files = {"a.bin": os.urandom(1500), "b.bin": os.urandom(1700), "c.wav": make_wav(5_000)}
    version, decision, run = make_result(src, plain, files, f"seed-{tag}", "canary-researcher" if kind == "bad" else "canary-elsewhere")
    print(json.dumps({"decision": decision, "version": version, "run": run, "files": sorted(files)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
