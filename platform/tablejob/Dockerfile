# The table worker. Build context is ./platform, so the table-writing library is copied in alongside it.
#
# What is NOT in this image, on purpose: the API's code, a database driver, the master key, and any storage key. The worker
# is given one storage key per job by the control plane, for one folder, and that is all it can reach.
FROM python:3.12-slim

WORKDIR /app

COPY tablejob/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY tablewriter ./tablewriter
COPY tablejob ./tablejob

RUN useradd --create-home --uid 10002 tablejob
USER tablejob

CMD ["python", "-m", "tablejob.main"]
