"""The Munitas client library, offered for download by the platform itself.

A person installs it from the platform they will use, so the version always matches that platform and nothing is published anywhere else. The file is built
from the library's own source when the platform's image is built (`scripts/build-client-wheel.py`) and sits in `client_wheel/` beside the application.

Both routes need no sign-in, on purpose: `pip` is a plain web request and cannot carry a console session. The file holds only the library's code, the same code that
is public in the project's repository, and nothing about any organisation. The download route serves exactly the one file that was built, found by its exact
name, so no part of the request is ever used as a path.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(tags=["client library"])

# /app/app/client_library.py -> /app/client_wheel
FOLDER = Path(__file__).resolve().parent.parent / "client_wheel"


def built() -> dict | None:
    """What was built into this image, or None when the library was not built (a build made before `scripts/build-client-wheel.py` was run)."""
    try:
        info = json.loads((FOLDER / "client.json").read_text(encoding="utf-8"))
        if (FOLDER / info["filename"]).is_file():
            return info
    except (OSError, ValueError, KeyError):
        pass
    return None


@router.get("/client")
def client_library() -> dict:
    """The Munitas client library this platform offers: its version, the file to install, and the checksum of that file.

    Open to anyone who can reach the platform, because `pip` cannot sign in. `path` is where the file is downloaded from, relative to the platform's address; install it
    with `pip install <the platform's address><path>`. `sha256` is the checksum of exactly that file, for anyone who wants to check what they downloaded. When the
    library was not built into this platform, `available` is false and nothing else is returned."""
    info = built()
    if info is None:
        return {"available": False}
    return {
        "available": True,
        "name": info["name"],
        "version": info["version"],
        "filename": info["filename"],
        "path": f"/client/{info['filename']}",
        "sha256": info["sha256"],
        "size": info["size"],
        "requires_python": info["requires_python"],
        "requires": info["requires"],
        "extras": info["extras"],
    }


@router.get("/client/{filename}")
def client_library_file(filename: str) -> FileResponse:
    """Download the client library as the file `pip` installs. Only the file this platform offers (named in `GET /client`) can be downloaded; any other name is not found."""
    info = built()
    if info is None or filename != info["filename"]:
        raise HTTPException(404, "That file is not offered by this platform.")
    return FileResponse(FOLDER / info["filename"], media_type="application/octet-stream", filename=info["filename"])
