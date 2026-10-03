"""The table worker: writes a version's rows as a table, in a job, and reports to the control plane. See activity.py."""

import os

# Newer S3 clients, pyarrow's among them, send an upload with its checksum in a trailer by default, and SeaweedFS stores the
# transfer framing as part of the object: a table's metadata file then begins with the length of a chunk and a line break
# and cannot be parsed. These two make the client send a plain upload. They are set here, before any client exists, so that
# no way of starting this worker can leave them out. The control plane's own container sets the same two in docker-compose.yml.
os.environ.setdefault("AWS_REQUEST_CHECKSUM_CALCULATION", "when_required")
os.environ.setdefault("AWS_RESPONSE_CHECKSUM_VALIDATION", "when_required")
