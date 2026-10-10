"""The routes for bringing in a table in parts. Thin on purpose: what is refused, and why, lives in `table_uploads`.

A part is sent as the raw body of the request, not as a form, so the platform never writes the file to its own disk on the way in: each part
(8 MB) is held in memory while it is stored.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import auth, table_uploads
from .person_errors import Refused

router = APIRouter(tags=["table uploads"])


class OpenTableUploadIn(BaseModel):
    file_name: str = Field(description="The file's name. Its ending says how it is laid out: .csv, .tsv, .jsonl, .ndjson or .json, with .gz or .zst after it if it is compressed, or .parquet, .avro, .orc, .arrow, .feather or .xlsx for a binary table file")
    size_bytes: int = Field(description="The size of the file in bytes, stated before any part is sent")
    contract: str = Field(description="The contract for the table, as YAML or JSON text. A table brought in this way is not about people, so its contract says it cannot be erased and why")
    options: dict = Field(default_factory=dict, description=(
        "How a text file was written: delimiter (one character), encoding (utf-8 by default), header (true by default), and null_values (the words that mean "
        "a missing value; an empty field by default). For a workbook, sheet names the sheet to read (the first when it is left out). Compression comes from the "
        "file's name. A binary table file carries its own column types, which must fit the contract."))


def _answering(call):
    try:
        return call()
    except Refused as exc:
        raise HTTPException(400, {"reasons": exc.reasons}) from exc
    except table_uploads.TooBusy as exc:
        return JSONResponse(status_code=429, headers={"Retry-After": str(exc.retry_after)},
                            content={"detail": {"reasons": exc.reasons, "retry_after_seconds": exc.retry_after}})
    except table_uploads.NoSuchUpload as exc:
        raise HTTPException(404, {"reasons": ["no such upload"]}) from exc


async def _body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, {"reasons": [f"a part is at most {limit} bytes"]})
    chunks, total = [], 0
    async for piece in request.stream():
        total += len(piece)
        if total > limit:
            raise HTTPException(413, {"reasons": [f"a part is at most {limit} bytes"]})
        chunks.append(piece)
    return b"".join(chunks)


@router.post("/datasets/{dataset_id}/table-uploads", status_code=201)
def open_upload(dataset_id: str, body: OpenTableUploadIn, identity: dict = Depends(auth.current_session)):
    """Start bringing in a table that is not about people, from a file too large for the table route, sent in parts of 8 MB.

    The contract is checked and the file's name and size are accepted or refused before anything is sent. The upload reserves the next version
    of the dataset, so a long upload is never refused at its end because somebody else sealed a version meanwhile. It stays open for a day, and
    each part can be sent again. Past a limit (uploads open at once, uploads started in an hour, and the organisation's own limits on the largest
    file, the files kept while they wait and the amount brought in a day) the answer is 429 with the seconds to wait.
    """
    return _answering(lambda: table_uploads.open_upload(dataset_id=dataset_id, identity=identity, file_name=body.file_name, size_bytes=body.size_bytes,
                                                         contract_text=body.contract, options=body.options))


@router.get("/table-uploads/{job_id}")
def upload_status(job_id: str, identity: dict = Depends(auth.current_session)):
    """Where an upload stands: which parts have arrived and which are missing, so one that was cut off carries on from where it stopped, and, once
    it is finished, how far the table has got and how it ended. Only the person who started it can ask."""
    return _answering(lambda: table_uploads.status(identity, job_id))


@router.put("/table-uploads/{job_id}/parts/{part_number}")
async def send_part(job_id: str, part_number: int, request: Request, identity: dict = Depends(auth.current_session)):
    """Send one part as the raw body of the request: 8 MB, except the last. Sending a part again replaces it. At most four parts of one upload are
    taken at the same moment, and the platform takes only so many in all; past that the answer is 429 with the seconds to wait."""
    data = await _body(request, table_uploads.PART_BYTES)
    # Storing a part takes seconds when storage is slow, so it runs in a worker thread: done here, it would hold up every other request.
    return await run_in_threadpool(_answering, lambda: table_uploads.receive_part(identity=identity, job_id=job_id, part_number=part_number, data=data))


@router.post("/table-uploads/{job_id}/finish", status_code=202)
def finish_upload(job_id: str, identity: dict = Depends(auth.current_session)):
    """Finish the upload. Every part must have arrived. The table is then checked and written by a worker, which can take a while: the answer is 202
    at once, and the upload's status says how far it has got and how it ended, with every problem found if the file was refused."""
    return _answering(lambda: table_uploads.finish(identity=identity, job_id=job_id))


@router.post("/table-uploads/{job_id}/cancel")
def cancel_upload(job_id: str, identity: dict = Depends(auth.current_session)):
    """Cancel an upload, or a table that is waiting or being written. What was sent is thrown away and the version it reserved is free again."""
    return _answering(lambda: table_uploads.cancel(identity=identity, job_id=job_id))
